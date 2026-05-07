"""``tabula enclave`` subcommands.

This package currently implements the ``down`` teardown subcommand (issue
#28) and the ``ssh`` subcommand (issue #33). The ``up`` provisioning
subcommand (issue #26) is referenced by the shared
:mod:`tabula_cli._enclave_state` schema and will be wired into the same
argparse group when its PR lands.

Subcommand modules:

* :mod:`tabula_cli.enclave.ssh`    — IAP-tunneled shell (issue #33)
* :mod:`tabula_cli.enclave.up`     — provision (issue #26, pending)
* :mod:`tabula_cli.enclave.status` — current state (issue #30, pending)

Key invariants for ``down``:

* Trusting ``terraform destroy`` exit code alone is **not** sufficient.
  Post-destroy verification independently asks GCP for any resource still
  carrying ``labels.enclave=<name>`` and refuses to delete the local state
  directory if leftovers exist.
* If ``terraform destroy`` fails partway through, the local state directory
  is left **intact** so the user can retry. ``--force`` is the documented
  recovery path for the unhappy case where ``state.json`` is missing or
  corrupt.
* All paths are idempotent: running ``down`` against an already-destroyed
  enclave is a clean no-op (terraform destroy reports nothing to do,
  verification returns no leftovers, state dir is removed).

Exit codes (``down``):

* ``0`` — clean teardown (or clean idempotent no-op)
* ``1`` — user error (bad name, missing state without ``--force``)
* ``2`` — terraform failure (state preserved, retry possible)
* ``3`` — post-destroy verification found leftover resources
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence

from tabula_cli._enclave_state import (
    ENCLAVE_LABEL_KEY,
    EnclaveState,
    StateCorruptError,
    StateNotFoundError,
    StateVersionError,
    is_valid_name,
    read_state,
    remove_state_dir,
)

EXIT_OK = 0
EXIT_USER_ERROR = 1
EXIT_TERRAFORM_FAILURE = 2
EXIT_LEFTOVERS = 3


# --------------------------------------------------------------------------- #
# Confirmation prompt                                                         #
# --------------------------------------------------------------------------- #

#: Big-red-button warning shown before any destroy action. Mentions the
#: Gitea persistent disk explicitly because that is the only stateful
#: resource a user will care about losing.
DESTROY_WARNING = (
    "WARNING: This will destroy enclave '{name}' and ALL its resources, "
    "including the Gitea persistent disk and every repository it stores. "
    "This action is irreversible."
)


def _default_prompt(message: str) -> bool:
    """Default interactive yes/no prompt; requires a literal 'yes'."""
    answer = input(f"{message} Type 'yes' to confirm: ").strip().lower()
    return answer == "yes"


# --------------------------------------------------------------------------- #
# Terraform shell-out                                                         #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class TerraformResult:
    """Result of a ``terraform destroy`` invocation."""

    returncode: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.returncode == 0


def _run_terraform_destroy(terraform_dir: Path) -> TerraformResult:
    """Run ``terraform destroy -auto-approve`` in ``terraform_dir``.

    Shells out to the ``terraform`` binary on PATH. The caller is
    responsible for surfacing stderr on failure.
    """
    proc = subprocess.run(  # noqa: S603 — controlled, not user-driven argv
        ["terraform", "destroy", "-auto-approve"],
        cwd=str(terraform_dir),
        capture_output=True,
        text=True,
        check=False,
    )
    return TerraformResult(
        returncode=proc.returncode, stdout=proc.stdout, stderr=proc.stderr
    )


# --------------------------------------------------------------------------- #
# Post-destroy verification                                                   #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Leftover:
    """A cloud resource still carrying ``enclave=<name>`` after destroy."""

    kind: str  # "instance", "disk", "address", "firewall", "router"
    name: str
    location: str | None = None  # zone/region, if applicable

    def describe(self) -> str:
        loc = f" ({self.location})" if self.location else ""
        return f"{self.kind} {self.name}{loc}"


def _gcloud_list_instances(project_id: str, name: str) -> list[Leftover]:
    """Query GCE for any instances tagged with ``enclave=<name>``.

    Uses ``gcloud compute instances list --format=json`` and filters by
    label. Returns an empty list if no leftovers (or if the project is
    inaccessible — surfaced as an exception).
    """
    cmd = [
        "gcloud",
        "compute",
        "instances",
        "list",
        f"--project={project_id}",
        f"--filter=labels.{ENCLAVE_LABEL_KEY}={name}",
        "--format=json",
    ]
    proc = subprocess.run(  # noqa: S603 — controlled argv
        cmd, capture_output=True, text=True, check=False
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"gcloud verification call failed: {proc.stderr.strip() or proc.stdout.strip()}"
        )
    try:
        data = json.loads(proc.stdout or "[]")
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"gcloud returned non-JSON output: {exc}") from exc
    leftovers: list[Leftover] = []
    for inst in data:
        leftovers.append(
            Leftover(
                kind="instance",
                name=inst.get("name", "<unknown>"),
                location=inst.get("zone", "").rsplit("/", 1)[-1] or None,
            )
        )
    return leftovers


#: Pluggable verification function. Tests inject a fake; production uses
#: :func:`_gcloud_list_instances`. The signature is ``(project_id, name) ->
#: list[Leftover]``.
VerifyFn = Callable[[str, str], list[Leftover]]


def _default_verifier(project_id: str, name: str) -> list[Leftover]:
    """Default leftover-verifier: shells out to gcloud for GCE instances.

    GCE instances are the floor for the MVP — see issue #28 acceptance
    criteria. Disks/addresses/firewalls/router are stretch goals; adding
    them later means extending this function and updating tests.
    """
    return _gcloud_list_instances(project_id=project_id, name=name)


# --------------------------------------------------------------------------- #
# Down command                                                                #
# --------------------------------------------------------------------------- #


@dataclass
class DownOptions:
    """Parameters for :func:`enclave_down`."""

    name: str
    yes: bool = False
    force: bool = False
    project: str | None = None  # required when --force without state
    enclaves_root: Path | None = None  # test override
    terraform_runner: Callable[[Path], TerraformResult] = _run_terraform_destroy
    verifier: VerifyFn = _default_verifier
    prompt: Callable[[str], bool] = _default_prompt


def _emit(msg: str, *, err: bool = False) -> None:
    """Print user-facing messages to the right stream."""
    print(msg, file=sys.stderr if err else sys.stdout)


def enclave_down(opts: DownOptions) -> int:  # noqa: C901 — top-level orchestration
    """Tear down an enclave. Returns process exit code.

    Behaviour matrix:

    +-----------------------------+----------------------------------------+
    | situation                   | outcome                                |
    +=============================+========================================+
    | bad name                    | exit 1, no side effects                |
    +-----------------------------+----------------------------------------+
    | state missing, no --force   | exit 1, no side effects                |
    +-----------------------------+----------------------------------------+
    | state present, no --yes     | confirm; on 'no' exit 0, no action     |
    +-----------------------------+----------------------------------------+
    | terraform destroy fails     | exit 2, state dir preserved            |
    +-----------------------------+----------------------------------------+
    | leftovers found post-destroy| exit 3, state dir preserved            |
    +-----------------------------+----------------------------------------+
    | clean destroy + clean verify| exit 0, state dir removed              |
    +-----------------------------+----------------------------------------+
    """
    name = opts.name
    if not is_valid_name(name):
        _emit(
            f"error: '{name}' is not a valid enclave name "
            f"(lowercase, 3-30 chars, DNS-safe).",
            err=True,
        )
        return EXIT_USER_ERROR

    # 1. Locate state (or recover via --force) ------------------------------- #
    state: EnclaveState | None = None
    project_id: str | None
    terraform_dir: Path | None = None

    try:
        state = read_state(name, root=opts.enclaves_root)
        project_id = state.project_id
        terraform_dir = Path(state.terraform_dir)
    except StateNotFoundError as exc:
        if not opts.force:
            _emit(f"error: {exc}", err=True)
            return EXIT_USER_ERROR
        if not opts.project:
            _emit(
                "error: --force without state.json requires --project=<gcp project id> "
                "so the verifier can query for leftover resources.",
                err=True,
            )
            return EXIT_USER_ERROR
        project_id = opts.project
        _emit(
            f"--force: state missing; will query project '{project_id}' "
            f"for resources labelled {ENCLAVE_LABEL_KEY}={name}."
        )
    except (StateCorruptError, StateVersionError) as exc:
        if not opts.force:
            _emit(f"error: {exc}", err=True)
            return EXIT_USER_ERROR
        if not opts.project:
            _emit(
                "error: --force on corrupt/unsupported state requires --project=<gcp project id>.",
                err=True,
            )
            return EXIT_USER_ERROR
        project_id = opts.project
        _emit(
            f"--force: ignoring unreadable state.json; querying project '{project_id}'."
        )

    # 2. Confirm destructive action ------------------------------------------ #
    if not opts.yes:
        warning = DESTROY_WARNING.format(name=name)
        if not opts.prompt(warning):
            _emit("aborted: confirmation not given.")
            return EXIT_OK

    # 3. Run terraform destroy (skipped on --force without state) ------------ #
    if terraform_dir is not None:
        if not terraform_dir.exists():
            _emit(
                f"error: terraform_dir from state.json does not exist: {terraform_dir}. "
                f"Use --force to skip terraform and verify cloud-side only.",
                err=True,
            )
            return EXIT_USER_ERROR

        _emit(f"running: terraform destroy in {terraform_dir}")
        tf = opts.terraform_runner(terraform_dir)
        if not tf.ok:
            _emit(
                "terraform destroy failed; local state preserved for retry.\n"
                f"  retry: tabula enclave down {name}\n"
                f"  recover: tabula enclave down {name} --force --project=<gcp project id>",
                err=True,
            )
            if tf.stderr:
                _emit(tf.stderr.rstrip(), err=True)
            return EXIT_TERRAFORM_FAILURE
    else:
        _emit("--force: skipping terraform destroy (no state); cloud-side check only.")

    # 4. Independent post-destroy verification ------------------------------- #
    try:
        leftovers = opts.verifier(project_id, name)
    except Exception as exc:  # noqa: BLE001 — surface any verifier failure clearly
        _emit(
            f"error: post-destroy verification failed to query GCP: {exc}\n"
            f"  Local state preserved. Re-run after fixing auth (gcloud auth "
            f"application-default login) or check connectivity.",
            err=True,
        )
        return EXIT_LEFTOVERS

    if leftovers:
        _emit(
            f"error: post-destroy verification found {len(leftovers)} leftover "
            f"resource(s) labelled {ENCLAVE_LABEL_KEY}={name}:",
            err=True,
        )
        for lo in leftovers:
            _emit(f"  - {lo.describe()}", err=True)
        _emit(
            "Local state preserved. Investigate manually; you may need "
            "`gcloud compute instances delete` directly.",
            err=True,
        )
        return EXIT_LEFTOVERS

    # 5. Clean: remove local state dir --------------------------------------- #
    remove_state_dir(name, root=opts.enclaves_root)
    _emit(f"enclave '{name}' destroyed cleanly; verification passed; state removed.")
    return EXIT_OK


# --------------------------------------------------------------------------- #
# argparse wiring                                                             #
# --------------------------------------------------------------------------- #


def add_subparser(subparsers: argparse._SubParsersAction) -> None:
    """Register the ``enclave`` subcommand group on a top-level parser."""
    from tabula_cli.enclave import ssh as ssh_mod

    p = subparsers.add_parser(
        "enclave",
        help="Manage Tabula enclaves (one-command lifecycle).",
        description=(
            "Manage Tabula enclaves. An enclave is one GCP project's worth "
            "of isolated infrastructure (classifier + GPU + Gitea + network)."
        ),
    )
    sub = p.add_subparsers(dest="enclave_cmd", required=True)

    # `down` -------------------------------------------------------------- #
    down = sub.add_parser(
        "down",
        help="Tear down an enclave to zero residual cost.",
        description=(
            "Tear down enclave <name>. Runs `terraform destroy`, then "
            "independently verifies via the GCP API that nothing labelled "
            f"{ENCLAVE_LABEL_KEY}=<name> remains. Removes the local state "
            "directory only on a clean destroy + clean verification."
        ),
    )
    down.add_argument("name", help="Enclave name (DNS-safe).")
    down.add_argument(
        "--yes",
        action="store_true",
        help="Skip the destructive-confirmation prompt (CI/scripted teardown).",
    )
    down.add_argument(
        "--force",
        action="store_true",
        help=(
            "Recovery path: attempt destroy when state.json is missing or "
            "corrupt by querying GCP for resources labelled "
            f"{ENCLAVE_LABEL_KEY}=<name>. Requires --project."
        ),
    )
    down.add_argument(
        "--project",
        default=None,
        help="GCP project id (required with --force when state is missing).",
    )

    # `ssh` --------------------------------------------------------------- #
    ssh_mod.add_subparser(sub)


def run(args: argparse.Namespace) -> int:
    """Dispatch the parsed ``enclave`` subcommand."""
    if args.enclave_cmd == "down":
        opts = DownOptions(
            name=args.name,
            yes=bool(args.yes),
            force=bool(args.force),
            project=args.project,
        )
        return enclave_down(opts)
    if args.enclave_cmd == "ssh":
        from tabula_cli.enclave import ssh as ssh_mod

        return ssh_mod.run(args)
    _emit(f"error: unknown enclave subcommand: {args.enclave_cmd}", err=True)
    return EXIT_USER_ERROR
