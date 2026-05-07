"""``tabula enclave ssh <name> {classifier|gpu|gitea}`` — IAP-tunneled SSH.

Wraps ``gcloud compute ssh --tunnel-through-iap`` in a tabula-flavored UX
that integrates with the per-enclave ``state.json``. Operators do not have
to remember instance names, zones, or project IDs; the role string
(``classifier``/``gpu``/``gitea``) plus the enclave name are enough.

Why IAP and not bastion hosts? IAP requires no extra VM, integrates with
GCP IAM (free audit logging via Cloud Audit Logs), and works with OS Login
for short-lived SSH credentials. The ingress firewall rule for
``35.235.240.0/20`` (the IAP CIDR) is already added in #24 against the
``enclave-workload`` instance tag.

Operator IAM prerequisites (the CLI does **not** auto-grant these):

* ``roles/iap.tunnelResourceAccessor`` — to mint IAP tunnel tokens
* ``roles/compute.osLogin`` (or ``roles/compute.instanceAdmin.v1``) — for
  OS Login key provisioning

If the operator lacks either role ``gcloud`` will surface a clear 403; the
CLI passes that error through unchanged via exit code propagation.

Exit codes:

* ``0`` — clean SSH session (or ``--command`` ran successfully)
* ``1`` — user error (bad role, bad name, missing state, terminated VM)
* ``2`` — operator declined the GPU auto-start prompt
* anything else — passed through from the underlying ``gcloud`` invocation
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence

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
EXIT_USER_ERROR = 1
EXIT_DECLINED = 2

# --------------------------------------------------------------------------- #
# Role registry                                                               #
# --------------------------------------------------------------------------- #

#: The three workload roles addressable via ``tabula enclave ssh``. Anything
#: else is rejected with a clear "valid choices are ..." message.
VALID_ROLES: tuple[str, ...] = ("classifier", "gpu", "gitea")


@dataclass(frozen=True)
class ResolvedTarget:
    """Concrete (instance, zone, project) tuple for ``gcloud compute ssh``."""

    instance: str
    zone: str
    project: str
    role: str  # original role string, for log/error messages


def _state_key(role: str, suffix: str) -> str:
    """Return the ``state.outputs`` key for a (role, suffix) pair.

    For example, ``_state_key("classifier", "instance")`` returns
    ``"classifier_instance"``. Centralised so the convention lives in one
    place and ``up`` (the writer) and ``ssh`` (this reader) cannot drift.
    """
    return f"{role}_{suffix}"


def resolve_target(state: EnclaveState, role: str) -> ResolvedTarget:
    """Resolve (instance, zone, project) for ``role`` from ``state``.

    Raises:
        ValueError: if ``role`` is not in :data:`VALID_ROLES` or required
            ``state.outputs`` keys are missing.
    """
    if role not in VALID_ROLES:
        valid = ", ".join(VALID_ROLES)
        raise ValueError(
            f"unknown role '{role}'. Valid choices: {valid}."
        )

    outputs = state.outputs or {}
    instance_key = _state_key(role, "instance")
    zone_key = _state_key(role, "zone")

    missing = [k for k in (instance_key, zone_key) if k not in outputs]
    if missing:
        raise ValueError(
            f"state.json for enclave '{state.name}' is missing required outputs "
            f"for role '{role}': {missing}. Re-run `tabula enclave up {state.name}` "
            f"to refresh outputs, or check the Terraform root module."
        )

    return ResolvedTarget(
        instance=str(outputs[instance_key]),
        zone=str(outputs[zone_key]),
        project=state.project_id,
        role=role,
    )


# --------------------------------------------------------------------------- #
# Instance state probe                                                        #
# --------------------------------------------------------------------------- #

#: GCE instance status values we care about. The full set is documented at
#: https://cloud.google.com/compute/docs/instances/instance-life-cycle but
#: only ``RUNNING``/``TERMINATED``/``STOPPED``/``STOPPING``/``PROVISIONING``
#: are reachable via the operator path. We treat ``STOPPED`` and
#: ``TERMINATED`` as the same "off" state for UX purposes — the GCE API
#: returns ``TERMINATED`` for an instance that has been stopped.
INSTANCE_RUNNING: str = "RUNNING"
INSTANCE_TERMINATED: str = "TERMINATED"
INSTANCE_STOPPED: str = "STOPPED"


# Pluggable status probe: tests inject a fake; production shells out to
# gcloud. Signature: (target) -> status string (e.g. "RUNNING"). The probe
# may also raise; callers surface raised errors as exit code 1.
StatusFn = Callable[[ResolvedTarget], str]


def _gcloud_describe_status(target: ResolvedTarget) -> str:
    """Return the GCE instance status for ``target`` via ``gcloud``.

    Shells out to ``gcloud compute instances describe ... --format=value(status)``
    which yields a single line such as ``RUNNING`` or ``TERMINATED``.
    """
    cmd = [
        "gcloud",
        "compute",
        "instances",
        "describe",
        target.instance,
        f"--zone={target.zone}",
        f"--project={target.project}",
        "--format=value(status)",
    ]
    proc = subprocess.run(  # noqa: S603 — controlled argv, never user-supplied
        cmd, capture_output=True, text=True, check=False
    )
    if proc.returncode != 0:
        raise RuntimeError(
            "gcloud compute instances describe failed: "
            f"{proc.stderr.strip() or proc.stdout.strip()}"
        )
    return proc.stdout.strip()


# --------------------------------------------------------------------------- #
# gcloud start (for stopped GPU)                                              #
# --------------------------------------------------------------------------- #

# Pluggable start callable. Signature: (target) -> exit code from gcloud.
StartFn = Callable[[ResolvedTarget], int]


def _gcloud_start_instance(target: ResolvedTarget) -> int:
    """Start ``target`` via ``gcloud compute instances start``."""
    cmd = [
        "gcloud",
        "compute",
        "instances",
        "start",
        target.instance,
        f"--zone={target.zone}",
        f"--project={target.project}",
    ]
    proc = subprocess.run(cmd, check=False)  # noqa: S603 — controlled argv
    return proc.returncode


# --------------------------------------------------------------------------- #
# gcloud SSH (the actual session)                                             #
# --------------------------------------------------------------------------- #

# Pluggable SSH runner. Signature: (argv) -> exit code. Used so tests can
# capture the argv that would be exec'd without actually shelling out.
SshFn = Callable[[Sequence[str]], int]


def _gcloud_ssh_runner(argv: Sequence[str]) -> int:
    """Run ``gcloud compute ssh`` with ``argv`` and pass through stdio.

    Critical that we do **not** capture stdout/stderr/stdin here — this is
    an interactive shell when ``--command`` is absent. ``subprocess.run``
    with the default streams inherits the parent's TTY.
    """
    proc = subprocess.run(list(argv), check=False)  # noqa: S603 — controlled argv
    return proc.returncode


def build_gcloud_ssh_argv(
    target: ResolvedTarget,
    *,
    command: str | None,
    forward_agent: bool,
) -> list[str]:
    """Build the ``gcloud compute ssh`` argv for ``target``.

    Kept as a pure function so it is trivially testable: feed it a target
    plus flags and assert on the resulting argv. The runner takes the
    output of this function unchanged.
    """
    argv: list[str] = [
        "gcloud",
        "compute",
        "ssh",
        target.instance,
        f"--zone={target.zone}",
        f"--project={target.project}",
        "--tunnel-through-iap",
    ]
    if forward_agent:
        # gcloud tunnels SSH options through `--ssh-flag=`. Only enable
        # agent forwarding when explicitly requested — security-conscious
        # operators should not get it by default.
        argv.append("--ssh-flag=-A")
    if command is not None:
        argv.append(f"--command={command}")
    return argv


# --------------------------------------------------------------------------- #
# Confirmation prompt                                                         #
# --------------------------------------------------------------------------- #

# Pluggable y/n prompt. Production reads stdin; tests inject a stub.
PromptFn = Callable[[str], bool]


def _default_prompt(message: str) -> bool:
    """Default interactive y/n prompt; defaults to no on EOF."""
    try:
        answer = input(f"{message} [y/N]: ").strip().lower()
    except EOFError:
        return False
    return answer in {"y", "yes"}


# --------------------------------------------------------------------------- #
# Options + main entry                                                        #
# --------------------------------------------------------------------------- #


@dataclass
class SshOptions:
    """Parameters for :func:`enclave_ssh`."""

    name: str
    role: str
    command: str | None = None
    forward_agent: bool = False
    no_start: bool = False
    enclaves_root: Path | None = None
    # Pluggable callables — all default to real-world implementations and
    # are overridden in tests. Keeping them as fields on this dataclass is
    # the simplest way to inject a fake without monkey-patching globals.
    state_reader: Callable[[str, Path | None], EnclaveState] = read_state
    status_probe: StatusFn = _gcloud_describe_status
    start_runner: StartFn = _gcloud_start_instance
    ssh_runner: SshFn = _gcloud_ssh_runner
    prompt: PromptFn = _default_prompt


def _emit(msg: str, *, err: bool = False) -> None:
    """Print user-facing messages to the right stream."""
    print(msg, file=sys.stderr if err else sys.stdout)


def _check_gcloud_available() -> int | None:
    """Return an exit code if ``gcloud`` is missing, or ``None`` if present.

    Surfaces a clear actionable error rather than letting ``FileNotFound``
    bubble out of the subprocess call. Returning a status (not raising) is
    consistent with the rest of the function's exit-code-returning style.
    """
    if shutil.which("gcloud") is None:
        _emit(
            "error: `gcloud` not found on PATH. Install the Google Cloud SDK "
            "and run `gcloud auth login` before using `tabula enclave ssh`.",
            err=True,
        )
        return EXIT_USER_ERROR
    return None


def enclave_ssh(opts: SshOptions) -> int:  # noqa: C901 — top-level orchestration
    """Open an IAP-tunneled SSH session per ``opts``. Returns exit code.

    Behaviour matrix (high level):

    * Bad enclave name -> ``1``, no side effects.
    * Bad role string -> ``1``, lists valid roles.
    * Missing/corrupt state -> ``1``, points at ``tabula enclave up``.
    * VM ``TERMINATED`` -> ``1``, tells operator to run ``up``.
    * VM ``STOPPED`` (GPU): with TTY, prompt; ``--no-start`` -> ``1``;
      decline -> ``2``; accept -> attempt start, then resume.
    * VM ``RUNNING`` (or post-start) -> exec ``gcloud compute ssh ...``;
      propagate that command's exit code unchanged.
    """
    # 1. Validate the enclave name shape before touching anything.
    if not is_valid_name(opts.name):
        _emit(
            f"error: invalid enclave name '{opts.name}'. "
            f"Names must match ^[a-z]([-a-z0-9]{{1,28}}[a-z0-9])?$.",
            err=True,
        )
        return EXIT_USER_ERROR

    # 2. Validate role early so we never read state for a bogus request.
    if opts.role not in VALID_ROLES:
        valid = ", ".join(VALID_ROLES)
        _emit(
            f"error: unknown role '{opts.role}'. Valid choices: {valid}.",
            err=True,
        )
        return EXIT_USER_ERROR

    # 3. Read state. Treat any state error as a user error (exit 1) with a
    #    targeted message; we never want to leak a stack trace from the
    #    happy path here.
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
    except StateError as exc:  # pragma: no cover — defensive
        _emit(f"error: {exc}", err=True)
        return EXIT_USER_ERROR

    # 4. Resolve (instance, zone, project) from state.
    try:
        target = resolve_target(state, opts.role)
    except ValueError as exc:
        _emit(f"error: {exc}", err=True)
        return EXIT_USER_ERROR

    # 5. Make sure gcloud is on PATH before we probe.
    rc = _check_gcloud_available()
    if rc is not None:
        return rc

    # 6. Probe instance status. If the probe itself fails (e.g. the
    #    operator is logged into the wrong project), surface the error.
    try:
        status = opts.status_probe(target)
    except RuntimeError as exc:
        _emit(f"error: {exc}", err=True)
        return EXIT_USER_ERROR

    # 7. Refuse to connect to a TERMINATED VM. ``up`` is the documented
    #    fix; we explicitly do NOT auto-provision.
    if status == INSTANCE_TERMINATED:
        _emit(
            f"error: VM for role '{opts.role}' in enclave '{opts.name}' is "
            f"TERMINATED. Run `tabula enclave up {opts.name}` to bring it "
            f"back, or `tabula enclave status {opts.name}` to inspect.",
            err=True,
        )
        return EXIT_USER_ERROR

    # 8. STOPPED handling. The GPU role is the common case (sleep schedule
    #    from #19); other roles can be STOPPED too if a human stopped them
    #    manually. We treat STOPPED uniformly.
    if status == INSTANCE_STOPPED:
        if opts.no_start:
            _emit(
                f"error: VM for role '{opts.role}' is STOPPED and --no-start "
                f"was given. Start it with "
                f"`gcloud compute instances start {target.instance} "
                f"--zone={target.zone} --project={target.project}` and retry.",
                err=True,
            )
            return EXIT_USER_ERROR

        _emit(
            f"warning: VM '{target.instance}' (role={opts.role}) is currently "
            f"STOPPED."
        )
        if not opts.prompt(
            f"Start it now (this can take a minute or two on the GPU pool)?"
        ):
            _emit("aborted: user declined to start VM.", err=True)
            return EXIT_DECLINED

        start_rc = opts.start_runner(target)
        if start_rc != 0:
            _emit(
                f"error: `gcloud compute instances start` exited {start_rc}.",
                err=True,
            )
            return start_rc
        # Status is now (presumed) RUNNING. We do not re-probe — gcloud's
        # `start` is synchronous and returns 0 only on success.

    # 9. Build argv and exec. Exit code is propagated verbatim so that
    #    `--command` use is scriptable (acceptance criterion).
    argv = build_gcloud_ssh_argv(
        target,
        command=opts.command,
        forward_agent=opts.forward_agent,
    )
    return opts.ssh_runner(argv)


# --------------------------------------------------------------------------- #
# Argparse wiring                                                             #
# --------------------------------------------------------------------------- #


def add_subparser(sub: argparse._SubParsersAction) -> argparse.ArgumentParser:
    """Register ``ssh`` on the parent ``enclave`` subparser."""
    p = sub.add_parser(
        "ssh",
        help="IAP-tunneled SSH into an enclave VM.",
        description=(
            "Open an IAP-tunneled SSH session to the named enclave's "
            "classifier, gpu, or gitea VM. Reads ~/.tabula/enclaves/<name>/"
            "state.json for the instance name + zone + project."
        ),
    )
    p.add_argument("name", help="Enclave name (e.g. 'demo').")
    p.add_argument(
        "role",
        choices=VALID_ROLES,
        help="Workload role to SSH into.",
    )
    p.add_argument(
        "--command",
        default=None,
        help=(
            "Run a single command on the remote and exit (non-interactive, "
            "scripting use). Exit code is propagated from gcloud."
        ),
    )
    p.add_argument(
        "--forward-agent",
        action="store_true",
        help=(
            "Enable SSH agent forwarding (`-A`). Off by default; useful for "
            "git-cloning from the in-enclave Gitea while sshed into the GPU."
        ),
    )
    p.add_argument(
        "--no-start",
        action="store_true",
        help=(
            "If the target VM is STOPPED, fail instead of prompting to "
            "start it. Use in scripts / CI."
        ),
    )
    return p


def run(args: argparse.Namespace) -> int:
    """Adapter from argparse Namespace to :func:`enclave_ssh`."""
    opts = SshOptions(
        name=args.name,
        role=args.role,
        command=args.command,
        forward_agent=args.forward_agent,
        no_start=args.no_start,
    )
    return enclave_ssh(opts)


__all__ = [
    "EXIT_DECLINED",
    "EXIT_OK",
    "EXIT_USER_ERROR",
    "INSTANCE_RUNNING",
    "INSTANCE_STOPPED",
    "INSTANCE_TERMINATED",
    "ResolvedTarget",
    "SshOptions",
    "VALID_ROLES",
    "add_subparser",
    "build_gcloud_ssh_argv",
    "enclave_ssh",
    "resolve_target",
    "run",
]
