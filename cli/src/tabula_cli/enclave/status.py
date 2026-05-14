"""``tabula enclave status <name>`` -- read-only health report (issue #30).

Reads the per-enclave ``state.json`` and reports per-VM GCE state,
``cold_seconds`` for the sleepable GPU, and classifier Noise-port
reachability. Never runs ``terraform`` and never mutates cloud state;
outbound calls are limited to ``gcloud compute instances describe`` and
a single TCP connect to the classifier's Noise port.

Exit codes:

* ``0`` -- all expected VMs in expected state. ``healthy=true``.
* ``1`` -- a VM is missing or ``TERMINATED``; ``healthy=false``. A
  ``STOPPED`` GPU is healthy (cold-by-default per Epic #12).
* ``2`` -- ``state.json`` missing / unreadable / version-incompatible.
* ``3`` -- GCP API error talking to ``gcloud``.

JSON output keys: ``name``, ``project``, ``region``, ``created_at``,
``vms``, ``reachability``, ``healthy``, ``issues``. ``reachability``
nests ``noise_port`` and a placeholder ``gitea: null`` (the IAP probe
is a future follow-up; does not count against health). Per-VM keys:
``role``, ``name``, ``state``, ``zone``, ``internal_ip``,
``external_ip``, ``last_start``, ``last_stop``, plus ``cold_seconds``
when set (STOPPED GPU only).

Concurrency: per-VM ``gcloud describe`` and the Noise-port probe run
in a single :class:`ThreadPoolExecutor`.
"""

from __future__ import annotations

import dataclasses
import json as _json
import socket
import subprocess
import textwrap
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, NamedTuple, Optional

import typer

from tabula_cli.state import (
    EnclaveState,
    StateCorruptError,
    StateError,
    StateNotFoundError,
    StateVersionError,
    is_valid_name,
    read_state,
)

# Exit codes
EXIT_OK = 0
EXIT_UNHEALTHY = 1
EXIT_USER_ERROR = 2
EXIT_GCP_ERROR = 3

#: Workload roles a healthy enclave exposes (mirrors ``ssh`` #33).
EXPECTED_ROLES: tuple[str, ...] = ("classifier", "gpu", "gitea")

#: Default Noise-port; overridable via ``state.outputs["noise_port"]``.
DEFAULT_NOISE_PORT: int = 7777

#: Noise-port TCP-connect timeout (issue #30 mandates 2s).
NOISE_PROBE_TIMEOUT_S: float = 2.0


@dataclass
class VmInfo:
    """Per-VM snapshot. ``cold_seconds`` is set only for a STOPPED GPU."""

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
class StatusReport:
    """Top-level structure mirroring the documented JSON shape."""

    name: str
    project: str
    region: str
    created_at: str
    vms: list[VmInfo] = field(default_factory=list)
    reachability_noise: Optional[ReachabilityProbe] = None
    healthy: bool = True
    issues: list[str] = field(default_factory=list)

    def to_json_dict(self) -> dict[str, Any]:
        """Render the canonical JSON shape (see module docstring).

        ``dataclasses.asdict`` + post-processor: lift ``reachability_noise``
        into ``reachability.noise_port`` (with ``gitea: None`` placeholder)
        and drop ``cold_seconds`` keys whose value is ``None``.
        """
        raw = dataclasses.asdict(self)
        noise = raw.pop("reachability_noise")
        raw["reachability"] = {"noise_port": noise, "gitea": None}
        for vm in raw["vms"]:
            if vm.get("cold_seconds") is None:
                vm.pop("cold_seconds", None)
        ordered = (
            "name", "project", "region", "created_at",
            "vms", "reachability", "healthy", "issues",
        )
        return {k: raw[k] for k in ordered}


class RoleTarget(NamedTuple):
    """Concrete (instance, zone) for a role, resolved from state.outputs."""

    role: str
    instance: str
    zone: str


#: Probe a single VM. May raise :class:`GcpProbeError` (-> exit 3).
InstanceProbeFn = Callable[[RoleTarget, str], VmInfo]

#: TCP-connect reachability probe. Never raises; reports ``reachable=False``.
NoiseProbeFn = Callable[[Optional[str], int, float], ReachabilityProbe]


class GcpProbeError(RuntimeError):
    """GCP-side probe failure. Surfaced by :func:`enclave_status` as exit 3."""


def _gcloud_describe_instance(target: RoleTarget, project: str) -> VmInfo:
    """Real :data:`InstanceProbeFn`: ``gcloud compute instances describe``.

    Raises :class:`GcpProbeError` if the binary is missing, the call
    fails, or the output is unparseable. We shell out (rather than depend
    on ``google-cloud-compute``) to match the ``ssh``/``down`` siblings.
    """
    cmd = [
        "gcloud", "compute", "instances", "describe", target.instance,
        f"--zone={target.zone}", f"--project={project}", "--format=json",
    ]
    try:
        proc = subprocess.run(  # noqa: S603 -- controlled argv
            cmd, capture_output=True, text=True, check=False, timeout=10,
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
    """Convert a ``gcloud ... describe --format=json`` payload to ``VmInfo``."""
    nics = payload.get("networkInterfaces") or []
    internal_ip = external_ip = None
    if nics:
        internal_ip = nics[0].get("networkIP")
        access = nics[0].get("accessConfigs") or []
        if access:
            external_ip = access[0].get("natIP")
    return VmInfo(
        role=target.role,
        name=target.instance,
        state=str(payload.get("status", "UNKNOWN")),
        zone=target.zone,
        internal_ip=internal_ip,
        external_ip=external_ip,
        last_start=payload.get("lastStartTimestamp"),
        last_stop=payload.get("lastStopTimestamp"),
    )


def _socket_noise_probe(
    host: Optional[str], port: int, timeout: float
) -> ReachabilityProbe:
    """Real :data:`NoiseProbeFn`: TCP connect + close (no Noise handshake)."""
    if not host:
        return ReachabilityProbe(host=host, port=port, reachable=False)
    started = datetime.now(timezone.utc)
    try:
        with socket.create_connection((host, port), timeout=timeout):
            elapsed_ms = int(
                (datetime.now(timezone.utc) - started).total_seconds() * 1000
            )
            return ReachabilityProbe(
                host=host, port=port, reachable=True, latency_ms=elapsed_ms,
            )
    except (OSError, TimeoutError):
        return ReachabilityProbe(host=host, port=port, reachable=False)


# Helpers: resolve targets, parse timestamps, classify health


def _resolve_role_targets(state: EnclaveState) -> list[RoleTarget]:
    """Resolve one ``RoleTarget`` per :data:`EXPECTED_ROLES` from ``state.outputs``.

    Missing roles emit empty strings (caller surfaces "missing" issues);
    never raises -- the diagnostic must run on partially-provisioned enclaves.
    """
    outputs = state.outputs or {}
    return [
        RoleTarget(
            role=role,
            instance=str(outputs.get(f"{role}_instance") or ""),
            zone=str(outputs.get(f"{role}_zone") or ""),
        )
        for role in EXPECTED_ROLES
    ]


def _parse_iso8601_z(s: str) -> Optional[datetime]:
    """Parse ISO-8601 with optional ``Z``. Returns ``None`` on any parse error."""
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
    """Seconds since ``last_stop`` for a STOPPED VM, else ``None``."""
    if vm.state != "STOPPED" or not vm.last_stop:
        return None
    parsed = _parse_iso8601_z(vm.last_stop)
    if parsed is None:
        return None
    ref = now if now is not None else datetime.now(timezone.utc)
    delta = ref - parsed
    return max(0, int(delta.total_seconds()))


def _classify_issues(vms: list[VmInfo]) -> list[str]:
    """Issue strings (empty == healthy) per Epic #12 health policy.

    Healthy: RUNNING (any role); STOPPED gpu (cold-by-default).
    Unhealthy: missing name; TERMINATED; STOPPED non-gpu; any non-steady state.
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


# Orchestration


@dataclass
class StatusOptions:
    """Parameters for :func:`enclave_status`. Callables are test seams."""

    name: str
    json_output: bool = False
    enclaves_root: Optional[Path] = None
    state_reader: Callable[[str, Optional[Path]], EnclaveState] = read_state
    instance_probe: InstanceProbeFn = _gcloud_describe_instance
    noise_probe: NoiseProbeFn = _socket_noise_probe
    now_fn: Callable[[], datetime] = lambda: datetime.now(timezone.utc)


def _emit(msg: str, *, err: bool = False) -> None:
    """Stream-aware print so Typer's stdout buffering is consistent."""
    typer.echo(msg, err=err)


def _format_vm_row(vm: VmInfo) -> str:
    """Render a single VM line for the text report."""
    cold = f", cold for {vm.cold_seconds}s" if vm.cold_seconds is not None else ""
    ext = f", ext={vm.external_ip}" if vm.external_ip else ""
    intern = f"int={vm.internal_ip}" if vm.internal_ip else "int=<none>"
    return (
        f"  - {vm.role:<10} {vm.name or '<missing>':<28} "
        f"state={vm.state:<10} zone={vm.zone or '<unknown>'}, "
        f"{intern}{ext}{cold}"
    )


def _format_text(report: StatusReport) -> str:
    """Render the human-readable text report (plain f-strings; no ``rich``)."""
    vm_rows = "\n".join(_format_vm_row(v) for v in report.vms)
    np = report.reachability_noise
    if np is not None:
        latency = (
            f" ({np.latency_ms} ms)"
            if np.reachable and np.latency_ms is not None else ""
        )
        noise_line = (
            f"  noise_port {np.host or '<unknown>'}:{np.port} -> "
            f"{'reachable' if np.reachable else 'unreachable'}{latency}"
        )
    else:
        noise_line = ""
    if report.healthy:
        status_block = "Status: HEALTHY"
    else:
        issue_lines = "\n".join(f"  - {issue}" for issue in report.issues)
        status_block = (
            f"Status: UNHEALTHY\n{issue_lines}" if issue_lines else "Status: UNHEALTHY"
        )
    return textwrap.dedent(
        """\
        Enclave: {name}
          project:    {project}
          region:     {region}
          created_at: {created_at}

        VMs:
        {vm_rows}

        Reachability:
        {noise_line}

        {status_block}"""
    ).format(
        name=report.name, project=report.project, region=report.region,
        created_at=report.created_at, vm_rows=vm_rows,
        noise_line=noise_line, status_block=status_block,
    )


def _build_report(
    state: EnclaveState, vms: list[VmInfo],
    noise: Optional[ReachabilityProbe], issues: list[str],
) -> StatusReport:
    """Assemble the final :class:`StatusReport` from probe results."""
    return StatusReport(
        name=state.name, project=state.project_id, region=state.region,
        created_at=state.created_at, vms=vms, reachability_noise=noise,
        healthy=not issues, issues=list(issues),
    )


def enclave_status(opts: StatusOptions) -> int:  # noqa: C901 -- 4 state-error branches + GCP probe try/except
    """Run the status diagnostic per ``opts``. Returns the exit code."""
    # 1. Validate name shape (cheap pre-IO check).
    if not is_valid_name(opts.name):
        _emit(
            f"error: invalid enclave name '{opts.name}'. "
            f"Names must match ^[a-z]([-a-z0-9]{{1,28}}[a-z0-9])?$.",
            err=True,
        )
        return EXIT_USER_ERROR

    # 2. Read state.json -> exit 2 on any state error.
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

    # 3. Probe VMs + Noise port concurrently. Missing-output roles are stubbed.
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
                opts.noise_probe, classifier_external_ip,
                noise_port, NOISE_PROBE_TIMEOUT_S,
            )
            for fut in vm_futures:
                vms.append(fut.result())
            noise = noise_future.result()
    except GcpProbeError as exc:
        _emit(f"error: GCP probe failed: {exc}", err=True)
        return EXIT_GCP_ERROR

    # 4. Derived fields: cold_seconds for STOPPED GPU.
    now = opts.now_fn()
    for vm in vms:
        if vm.role == "gpu":
            vm.cold_seconds = _compute_cold_seconds(vm, now=now)

    # 5. Classify, build, emit.
    issues = _classify_issues(vms)
    report = _build_report(state, vms, noise, issues)
    if opts.json_output:
        _emit(_json.dumps(report.to_json_dict(), indent=2, sort_keys=False))
    else:
        _emit(_format_text(report))
    return EXIT_OK if report.healthy else EXIT_UNHEALTHY


# Typer command (mounted by ``tabula_cli.enclave.__init__``)


def status(
    name: str = typer.Argument(
        ..., help="Enclave name (DNS-safe; must already exist locally)."
    ),
    json_output: bool = typer.Option(
        False, "--json",
        help="Emit a machine-readable JSON status document on stdout.",
    ),
) -> None:
    """Report enclave health and per-VM state. Read-only; never mutates."""
    rc = enclave_status(StatusOptions(name=name, json_output=bool(json_output)))
    raise typer.Exit(code=rc)


__all__ = [
    "DEFAULT_NOISE_PORT", "EXIT_GCP_ERROR", "EXIT_OK", "EXIT_UNHEALTHY",
    "EXIT_USER_ERROR", "EXPECTED_ROLES", "GcpProbeError",
    "NOISE_PROBE_TIMEOUT_S", "ReachabilityProbe", "RoleTarget",
    "StatusOptions", "StatusReport", "VmInfo",
    "_classify_issues", "_compute_cold_seconds", "_format_text",
    "_resolve_role_targets", "_vminfo_from_describe_payload",
    "enclave_status", "status",
]
