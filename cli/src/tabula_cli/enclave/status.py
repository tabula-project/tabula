"""``tabula enclave status <name>`` -- read-only health report (issue #30).

Answers two operator questions in a single, fast, read-only command:

1. *What is this enclave doing right now?*  -- per-VM state, IPs, "cold for
   N seconds" on the sleepable GPU, classifier Noise-port reachability.
2. *Is it healthy?*  -- a boolean plus a list of ``issues`` strings, with
   the exit code mirroring the boolean (0 = healthy, 1 = unhealthy).

The command is the canonical reader of the per-enclave ``state.json``
schema produced by :mod:`tabula_cli.enclave.up` (#26) and shared via
:mod:`tabula_cli._enclave_state`. It deliberately does **not** run
``terraform`` and **must never** mutate cloud state; the only outbound
calls it makes are read-only ``gcloud compute instances describe`` lookups
and a single TCP connect against the classifier's Noise port.

Exit codes (per #30 acceptance, distinct from ``up``/``down``/``ssh``):

* ``0`` -- all expected VMs present and in expected state. ``healthy=true``.
* ``1`` -- at least one VM is missing or unexpectedly ``TERMINATED``;
  ``healthy=false`` and ``issues`` non-empty. A ``STOPPED`` GPU is **not**
  an error -- the GPU is cold-by-default per Epic #12.
* ``2`` -- ``state.json`` not found, unreadable, or schema-incompatible
  (no such enclave locally; user error).
* ``3`` -- GCP API error talking to ``gcloud`` (auth/quota/network).

JSON shape (stable contract; downstream tools may rely on it)::

    {
      "name": "demo",
      "project": "tabula-demo-123",
      "region": "us-central1",
      "created_at": "2026-05-07T12:34:56Z",
      "vms": [
        {
          "role": "classifier",
          "name": "demo-classifier",
          "state": "RUNNING",
          "zone": "us-central1-a",
          "internal_ip": "10.0.0.2",
          "external_ip": "34.x.x.x",
          "last_start": "2026-05-07T12:35:10Z",
          "last_stop": null
        },
        {
          "role": "gpu",
          "name": "demo-gpu",
          "state": "STOPPED",
          "zone": "us-central1-a",
          "internal_ip": "10.0.0.3",
          "external_ip": null,
          "last_start": "2026-05-07T13:01:02Z",
          "last_stop": "2026-05-07T13:14:30Z",
          "cold_seconds": 720
        },
        ...
      ],
      "reachability": {
        "noise_port": {"host": "...", "port": 7777, "reachable": true,
                        "latency_ms": 41},
        "gitea": {"method": "iap", "reachable": false}
      },
      "healthy": true,
      "issues": []
    }

Concurrency: the per-VM ``gcloud describe`` calls and the Noise-port TCP
probe run concurrently via :class:`concurrent.futures.ThreadPoolExecutor`.
This keeps the wall-clock cost roughly equal to the slowest single probe
(target: <5s for a healthy enclave).
"""

from __future__ import annotations

import json as _json
import socket
import subprocess
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

import typer

from tabula_cli._enclave_state import (
    EnclaveState,
    StateCorruptError,
    StateError,
    StateNotFoundError,
    StateVersionError,
    is_valid_name,
    read_state,
)

# --------------------------------------------------------------------------- #
# Exit codes                                                                  #
# --------------------------------------------------------------------------- #

EXIT_OK = 0
EXIT_UNHEALTHY = 1
EXIT_USER_ERROR = 2
EXIT_GCP_ERROR = 3

# --------------------------------------------------------------------------- #
# Roles + canonical defaults                                                  #
# --------------------------------------------------------------------------- #

#: Workload roles a healthy enclave is expected to expose. Mirrors the set
#: from ``ssh`` (#33). Unknown role labels in ``state.outputs`` are ignored
#: silently here -- ``status`` is a read-only diagnostic, not a validator
#: for the writer schema.
EXPECTED_ROLES: tuple[str, ...] = ("classifier", "gpu", "gitea")

#: Default Noise-port the classifier exposes. Overridable via
#: ``state.outputs["noise_port"]`` (set by ``up``/Terraform).
DEFAULT_NOISE_PORT: int = 7777

#: TCP-connect timeout for the Noise-port probe, in seconds. The issue's
#: acceptance criterion mandates 2 seconds; we keep that at the module
#: boundary so tests can assert on it.
NOISE_PROBE_TIMEOUT_S: float = 2.0


# --------------------------------------------------------------------------- #
# Data classes                                                                #
# --------------------------------------------------------------------------- #


@dataclass
class VmInfo:
    """Per-VM snapshot returned by an :data:`InstanceProbeFn`.

    All fields are JSON-serializable. ``last_start`` / ``last_stop`` are
    ISO-8601 strings (UTC, ``Z`` suffix) or ``None`` if the underlying GCE
    metadata field was unset. ``cold_seconds`` is computed only for a
    ``STOPPED`` GPU; it is ``None`` otherwise.
    """

    role: str
    name: str
    state: str
    zone: str
    internal_ip: Optional[str] = None
    external_ip: Optional[str] = None
    last_start: Optional[str] = None
    last_stop: Optional[str] = None
    cold_seconds: Optional[int] = None


@dataclass
class ReachabilityProbe:
    """Result of the classifier Noise-port TCP probe."""

    host: Optional[str]
    port: int
    reachable: bool
    latency_ms: Optional[int] = None


@dataclass
class GiteaReachability:
    """Result of the Gitea reachability check.

    For an MVP we do not require an active IAP tunnel; we report
    ``method="iap"`` and ``reachable=False`` with no negative health impact
    (per the issue's edge cases section: "no IAP tunnel set up - report
    'N/A - internal only' and is not counted against ``healthy``").
    """

    method: str = "iap"
    reachable: bool = False
    note: Optional[str] = None


@dataclass
class StatusReport:
    """Top-level structure mirroring the documented JSON shape."""

    name: str
    project: str
    region: str
    created_at: str
    vms: list[VmInfo] = field(default_factory=list)
    reachability_noise: Optional[ReachabilityProbe] = None
    reachability_gitea: Optional[GiteaReachability] = None
    healthy: bool = True
    issues: list[str] = field(default_factory=list)

    def to_json_dict(self) -> dict[str, Any]:
        """Return the canonical JSON shape (see module docstring)."""
        out: dict[str, Any] = {
            "name": self.name,
            "project": self.project,
            "region": self.region,
            "created_at": self.created_at,
            "vms": [],
            "reachability": {
                "noise_port": (
                    {
                        "host": self.reachability_noise.host,
                        "port": self.reachability_noise.port,
                        "reachable": self.reachability_noise.reachable,
                        "latency_ms": self.reachability_noise.latency_ms,
                    }
                    if self.reachability_noise is not None
                    else None
                ),
                "gitea": (
                    {
                        "method": self.reachability_gitea.method,
                        "reachable": self.reachability_gitea.reachable,
                        **(
                            {"note": self.reachability_gitea.note}
                            if self.reachability_gitea.note
                            else {}
                        ),
                    }
                    if self.reachability_gitea is not None
                    else None
                ),
            },
            "healthy": self.healthy,
            "issues": list(self.issues),
        }
        for v in self.vms:
            entry: dict[str, Any] = {
                "role": v.role,
                "name": v.name,
                "state": v.state,
                "zone": v.zone,
                "internal_ip": v.internal_ip,
                "external_ip": v.external_ip,
                "last_start": v.last_start,
                "last_stop": v.last_stop,
            }
            if v.cold_seconds is not None:
                entry["cold_seconds"] = v.cold_seconds
            out["vms"].append(entry)
        return out


@dataclass
class RoleTarget:
    """Concrete (instance, zone) for a role, resolved from state.outputs."""

    role: str
    instance: str
    zone: str


# --------------------------------------------------------------------------- #
# Pluggable callables (tests inject fakes; production uses gcloud + socket)   #
# --------------------------------------------------------------------------- #

#: Probe a single VM: returns a :class:`VmInfo`. Implementations may raise
#: :class:`GcpProbeError` for transient/permanent infrastructure errors;
#: those become exit-3 ("GCP error") at the top level.
InstanceProbeFn = Callable[[RoleTarget, str], VmInfo]

#: Probe TCP-connect reachability of a (host, port). Returns a
#: :class:`ReachabilityProbe`. Should never raise; report
#: ``reachable=False`` instead. Test seam: ``socket.create_connection``.
NoiseProbeFn = Callable[[Optional[str], int, float], ReachabilityProbe]


class GcpProbeError(RuntimeError):
    """Raised by an :data:`InstanceProbeFn` for GCP-side failures.

    Surfaced by :func:`enclave_status` as exit code 3. Distinguished from
    user errors (state.json missing) which yield exit code 2.
    """


def _gcloud_describe_instance(target: RoleTarget, project: str) -> VmInfo:
    """Real-world :data:`InstanceProbeFn` -- shells out to ``gcloud``.

    Uses ``--format=json`` to capture fields we care about in a single
    round-trip. Raises :class:`GcpProbeError` if the binary is missing,
    the call fails, or the output is unparseable.

    The tabula CLI already shells out to ``gcloud`` for ``ssh`` (#33)
    and ``down`` recovery (#28), so we keep the dependency surface small
    and avoid pulling in ``google-cloud-compute`` (an optional extra).
    """
    cmd = [
        "gcloud",
        "compute",
        "instances",
        "describe",
        target.instance,
        f"--zone={target.zone}",
        f"--project={project}",
        "--format=json",
    ]
    try:
        proc = subprocess.run(  # noqa: S603 -- controlled argv
            cmd,
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
    except FileNotFoundError as exc:
        raise GcpProbeError(
            "gcloud not found on PATH; install the Google Cloud SDK"
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise GcpProbeError(
            f"gcloud describe timed out for {target.instance}"
        ) from exc

    if proc.returncode != 0:
        raise GcpProbeError(
            f"gcloud describe failed for {target.instance}: "
            f"{(proc.stderr or proc.stdout).strip()}"
        )

    try:
        payload = _json.loads(proc.stdout or "{}")
    except _json.JSONDecodeError as exc:
        raise GcpProbeError(
            f"gcloud describe returned non-JSON for {target.instance}"
        ) from exc

    return _vminfo_from_describe_payload(target, payload)


def _vminfo_from_describe_payload(
    target: RoleTarget, payload: dict[str, Any]
) -> VmInfo:
    """Convert a ``gcloud compute instances describe --format=json`` payload.

    Pulled out as a module-level helper so tests can pass a fixture dict
    directly without spawning subprocesses, and so the probe-mocking surface
    stays narrow (one function to override).
    """
    state = str(payload.get("status", "UNKNOWN"))

    nics = payload.get("networkInterfaces") or []
    internal_ip = None
    external_ip = None
    if nics:
        first = nics[0]
        internal_ip = first.get("networkIP")
        access = first.get("accessConfigs") or []
        if access:
            external_ip = access[0].get("natIP")

    last_start = payload.get("lastStartTimestamp")
    last_stop = payload.get("lastStopTimestamp")

    return VmInfo(
        role=target.role,
        name=target.instance,
        state=state,
        zone=target.zone,
        internal_ip=internal_ip,
        external_ip=external_ip,
        last_start=last_start,
        last_stop=last_stop,
    )


def _socket_noise_probe(
    host: Optional[str], port: int, timeout: float
) -> ReachabilityProbe:
    """Real-world :data:`NoiseProbeFn` -- a TCP connect + immediate close.

    Does **not** attempt a Noise handshake; that is an Epic #13 concern.
    Returns ``reachable=False`` for all error modes so the caller can
    record a clean boolean without try/except plumbing.
    """
    if not host:
        return ReachabilityProbe(host=host, port=port, reachable=False)
    started = datetime.now(timezone.utc)
    try:
        with socket.create_connection((host, port), timeout=timeout):
            elapsed_ms = int(
                (datetime.now(timezone.utc) - started).total_seconds() * 1000
            )
            return ReachabilityProbe(
                host=host,
                port=port,
                reachable=True,
                latency_ms=elapsed_ms,
            )
    except (OSError, TimeoutError):
        return ReachabilityProbe(host=host, port=port, reachable=False)


# --------------------------------------------------------------------------- #
# Helpers: resolve targets, parse timestamps, classify health                 #
# --------------------------------------------------------------------------- #


def _resolve_role_targets(state: EnclaveState) -> list[RoleTarget]:
    """Resolve ``RoleTarget`` for each :data:`EXPECTED_ROLES` entry from state.

    Returns a list in :data:`EXPECTED_ROLES` order. Roles whose
    ``<role>_instance`` / ``<role>_zone`` outputs are missing are still
    emitted, with empty strings for ``instance``/``zone``; the caller
    records those as missing-VM issues. We never raise from here -- the
    diagnostic must always run, even on a partially-provisioned enclave.
    """
    out: list[RoleTarget] = []
    outputs = state.outputs or {}
    for role in EXPECTED_ROLES:
        instance = outputs.get(f"{role}_instance")
        zone = outputs.get(f"{role}_zone")
        out.append(
            RoleTarget(
                role=role,
                instance=str(instance) if instance else "",
                zone=str(zone) if zone else "",
            )
        )
    return out


def _parse_iso8601_z(s: str) -> Optional[datetime]:
    """Parse an ISO-8601 timestamp with optional trailing ``Z``.

    Returns ``None`` on any parse error -- used for "best-effort" cold time
    arithmetic where a bad timestamp must not crash the diagnostic.
    """
    if not s:
        return None
    try:
        if s.endswith("Z"):
            return datetime.fromisoformat(s[:-1]).replace(tzinfo=timezone.utc)
        return datetime.fromisoformat(s)
    except (ValueError, TypeError):
        return None


def _compute_cold_seconds(
    vm: VmInfo, *, now: Optional[datetime] = None
) -> Optional[int]:
    """Return seconds since the GPU last stopped, or ``None`` if not applicable.

    Only meaningful for a STOPPED VM with a usable ``last_stop`` timestamp.
    Returns ``None`` for everything else (running, never-stopped,
    unparseable timestamp).
    """
    if vm.state != "STOPPED" or not vm.last_stop:
        return None
    parsed = _parse_iso8601_z(vm.last_stop)
    if parsed is None:
        return None
    ref = now if now is not None else datetime.now(timezone.utc)
    delta = ref - parsed
    return max(0, int(delta.total_seconds()))


def _classify_issues(vms: list[VmInfo]) -> list[str]:
    """Return a list of issue strings; empty if everything is healthy.

    Health policy (from #30 curator notes + Epic #12):

    * Missing VM (empty ``name``)        -> issue.
    * VM in ``TERMINATED``               -> issue (preemption / crash).
      Applies to **every** role including GPU; a stopped GPU appears as
      ``STOPPED`` rather than ``TERMINATED``.
    * VM in ``STOPPED`` for the GPU role -> healthy (cold-by-default).
    * VM in ``STOPPED`` for non-GPU role -> issue (operator stopped it
      manually; the demo expects classifier+gitea to be running).
    * VM ``RUNNING``                     -> healthy.
    * Anything else (``PROVISIONING`` /
      ``STAGING`` / ``REPAIRING``)       -> issue (not steady-state).
    """
    issues: list[str] = []
    for vm in vms:
        if not vm.name:
            issues.append(f"role '{vm.role}': VM missing from state.json")
            continue
        if vm.state == "TERMINATED":
            issues.append(
                f"role '{vm.role}': VM '{vm.name}' is TERMINATED "
                f"(preempted or crashed)"
            )
        elif vm.state == "STOPPED":
            if vm.role != "gpu":
                issues.append(
                    f"role '{vm.role}': VM '{vm.name}' is STOPPED "
                    f"(should be RUNNING)"
                )
            # else: STOPPED GPU is healthy (cold-by-default per Epic #12)
        elif vm.state not in ("RUNNING",):
            issues.append(
                f"role '{vm.role}': VM '{vm.name}' is in non-steady state "
                f"'{vm.state}'"
            )
    return issues


# --------------------------------------------------------------------------- #
# Orchestration                                                               #
# --------------------------------------------------------------------------- #


@dataclass
class StatusOptions:
    """Parameters for :func:`enclave_status`.

    Pluggable callables default to real-world implementations; tests
    inject fakes (mirrors the pattern used by ``ssh.SshOptions``).
    """

    name: str
    json_output: bool = False
    enclaves_root: Optional[Path] = None
    state_reader: Callable[[str, Optional[Path]], EnclaveState] = read_state
    instance_probe: InstanceProbeFn = _gcloud_describe_instance
    noise_probe: NoiseProbeFn = _socket_noise_probe
    # ``now`` lets tests pin "now" for deterministic cold_seconds.
    now_fn: Callable[[], datetime] = lambda: datetime.now(timezone.utc)


def _emit(msg: str, *, err: bool = False) -> None:
    """Stream-aware print so Typer's stdout buffering is consistent."""
    typer.echo(msg, err=err)


def _format_text(report: StatusReport) -> str:
    """Render the human-readable text report.

    Plain f-strings -- we deliberately do not pull in ``rich`` here; the
    ``up``/``down``/``ssh`` siblings stick to plain Typer ``echo`` and we
    keep the dependency surface uniform.
    """
    lines: list[str] = []
    lines.append(f"Enclave: {report.name}")
    lines.append(f"  project:    {report.project}")
    lines.append(f"  region:     {report.region}")
    lines.append(f"  created_at: {report.created_at}")
    lines.append("")
    lines.append("VMs:")
    for vm in report.vms:
        cold = (
            f", cold for {vm.cold_seconds}s"
            if vm.cold_seconds is not None
            else ""
        )
        ext = f", ext={vm.external_ip}" if vm.external_ip else ""
        intern = f"int={vm.internal_ip}" if vm.internal_ip else "int=<none>"
        nm = vm.name or "<missing>"
        lines.append(
            f"  - {vm.role:<10} {nm:<28} state={vm.state:<10} "
            f"zone={vm.zone or '<unknown>'}, {intern}{ext}{cold}"
        )

    lines.append("")
    lines.append("Reachability:")
    if report.reachability_noise is not None:
        np = report.reachability_noise
        host = np.host or "<unknown>"
        latency = (
            f" ({np.latency_ms} ms)"
            if np.reachable and np.latency_ms is not None
            else ""
        )
        lines.append(
            f"  noise_port {host}:{np.port} -> "
            f"{'reachable' if np.reachable else 'unreachable'}{latency}"
        )
    if report.reachability_gitea is not None:
        gt = report.reachability_gitea
        note = f" -- {gt.note}" if gt.note else ""
        lines.append(
            f"  gitea ({gt.method}) -> "
            f"{'reachable' if gt.reachable else 'N/A - internal only'}"
            f"{note}"
        )

    lines.append("")
    if report.healthy:
        lines.append("Status: HEALTHY")
    else:
        lines.append("Status: UNHEALTHY")
        for issue in report.issues:
            lines.append(f"  - {issue}")

    return "\n".join(lines)


def _build_report(
    state: EnclaveState,
    vms: list[VmInfo],
    noise: Optional[ReachabilityProbe],
    gitea: GiteaReachability,
    issues: list[str],
) -> StatusReport:
    """Assemble the final :class:`StatusReport` from probe results."""
    return StatusReport(
        name=state.name,
        project=state.project_id,
        region=state.region,
        created_at=state.created_at,
        vms=vms,
        reachability_noise=noise,
        reachability_gitea=gitea,
        healthy=not issues,
        issues=list(issues),
    )


def enclave_status(opts: StatusOptions) -> int:  # noqa: C901 -- orchestration
    """Run the status diagnostic per ``opts``. Returns the exit code.

    Behaviour matrix:

    * Bad enclave name shape -> exit 2 (user error).
    * Missing/corrupt/version-mismatched state.json -> exit 2.
    * GCP probe error (any role) -> exit 3.
    * Healthy -> exit 0.
    * Unhealthy (TERMINATED VM, missing role, etc.) -> exit 1.
    """
    # 1. Validate name shape early to give a clean error before disk I/O.
    if not is_valid_name(opts.name):
        _emit(
            f"error: invalid enclave name '{opts.name}'. "
            f"Names must match ^[a-z]([-a-z0-9]{{1,28}}[a-z0-9])?$.",
            err=True,
        )
        return EXIT_USER_ERROR

    # 2. Read state. Any state error -> exit 2 (no such enclave / bad schema).
    try:
        state = opts.state_reader(opts.name, opts.enclaves_root)
    except StateNotFoundError as exc:
        _emit(
            f"error: {exc} Run `tabula enclave up {opts.name}` to provision it.",
            err=True,
        )
        return EXIT_USER_ERROR
    except (StateCorruptError, StateVersionError) as exc:
        _emit(f"error: {exc}", err=True)
        return EXIT_USER_ERROR
    except StateError as exc:  # pragma: no cover -- defensive
        _emit(f"error: {exc}", err=True)
        return EXIT_USER_ERROR

    # 3. Resolve targets and probe concurrently. Missing-output roles still
    #    get a stub ``VmInfo`` so the report is dense.
    targets = _resolve_role_targets(state)
    classifier_external_ip: Optional[str] = state.outputs.get("classifier_ip")
    noise_port = int(state.outputs.get("noise_port") or DEFAULT_NOISE_PORT)

    def _probe_or_stub(t: RoleTarget) -> VmInfo:
        if not t.instance or not t.zone:
            return VmInfo(role=t.role, name="", state="MISSING", zone=t.zone)
        return opts.instance_probe(t, state.project_id)

    vms: list[VmInfo] = []
    try:
        with ThreadPoolExecutor(max_workers=max(1, len(targets) + 1)) as pool:
            vm_futures = [pool.submit(_probe_or_stub, t) for t in targets]
            noise_future = pool.submit(
                opts.noise_probe,
                classifier_external_ip,
                noise_port,
                NOISE_PROBE_TIMEOUT_S,
            )
            for fut in vm_futures:
                vms.append(fut.result())
            noise = noise_future.result()
    except GcpProbeError as exc:
        _emit(f"error: GCP probe failed: {exc}", err=True)
        return EXIT_GCP_ERROR

    # 4. Compute derived fields (cold_seconds for STOPPED GPU).
    now = opts.now_fn()
    for vm in vms:
        if vm.role == "gpu":
            vm.cold_seconds = _compute_cold_seconds(vm, now=now)

    # 5. Classify health and build report. Gitea reachability is currently
    #    a structural placeholder ("N/A - internal only") -- the real IAP-
    #    tunnel probe is a future follow-up tracked under the issue's
    #    "no IAP tunnel set up" edge case (does not count against health).
    issues = _classify_issues(vms)
    gitea = GiteaReachability(
        method="iap",
        reachable=False,
        note="internal only; no IAP probe wired",
    )
    report = _build_report(state, vms, noise, gitea, issues)

    # 6. Emit. ``--json`` mode goes to stdout for pipeability.
    if opts.json_output:
        _emit(_json.dumps(report.to_json_dict(), indent=2, sort_keys=False))
    else:
        _emit(_format_text(report))

    return EXIT_OK if report.healthy else EXIT_UNHEALTHY


# --------------------------------------------------------------------------- #
# Typer command (mounted by ``tabula_cli.enclave.__init__``)                  #
# --------------------------------------------------------------------------- #


def status(
    name: str = typer.Argument(
        ..., help="Enclave name (DNS-safe; must already exist locally)."
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Emit a machine-readable JSON status document on stdout.",
    ),
) -> None:
    """Report enclave health and per-VM state. Read-only; never mutates."""
    rc = enclave_status(StatusOptions(name=name, json_output=bool(json_output)))
    raise typer.Exit(code=rc)


__all__ = [
    "DEFAULT_NOISE_PORT",
    "EXIT_GCP_ERROR",
    "EXIT_OK",
    "EXIT_UNHEALTHY",
    "EXIT_USER_ERROR",
    "EXPECTED_ROLES",
    "GcpProbeError",
    "GiteaReachability",
    "NOISE_PROBE_TIMEOUT_S",
    "ReachabilityProbe",
    "RoleTarget",
    "StatusOptions",
    "StatusReport",
    "VmInfo",
    "_classify_issues",
    "_compute_cold_seconds",
    "_format_text",
    "_resolve_role_targets",
    "_vminfo_from_describe_payload",
    "enclave_status",
    "status",
]
