"""``tabula enclave`` subcommands.

This module currently implements the ``down`` teardown subcommand (issue
#28). The ``up`` provisioning subcommand (issue #26) is referenced by the
shared :mod:`tabula_cli.state` schema and will be wired into the
same argparse group when its PR lands.

Key invariants for ``down``:

* Trusting the IaC tool's ``destroy`` exit code alone is **not** sufficient.
  Post-destroy verification independently asks GCP for any resource still
  carrying ``labels.enclave=<name>`` and refuses to delete the local state
  directory if leftovers exist.
* If ``destroy`` fails partway through, the local state directory
  is left **intact** so the user can retry. ``--force`` is the documented
  recovery path for the unhappy case where ``state.json`` is missing or
  corrupt.
* All paths are idempotent: running ``down`` against an already-destroyed
  enclave is a clean no-op (destroy reports nothing to do, verification
  returns no leftovers, state dir is removed).

OpenTofu migration (#96): the binary is resolved at run time via
:func:`tabula_cli._terraform.find_binary` — ``tofu`` (OpenTofu, MPL 2.0)
is preferred and ``terraform`` (BUSL since v1.6) is the fallback. The user
sees the binary name that actually ran in error messages.

Exit codes:

* ``0`` — clean teardown (or clean idempotent no-op)
* ``1`` — user error (bad name, missing state without ``--force``)
* ``2`` — terraform failure (state preserved, retry possible)
* ``3`` — post-destroy verification found leftover resources
"""

from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import typer

from tabula_cli import _terraform as _tf
from tabula_cli.state import (
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
    """Result of a ``destroy`` invocation against ``tofu``/``terraform``."""

    returncode: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.returncode == 0


def _run_terraform_destroy(terraform_dir: Path) -> TerraformResult:
    """Run ``<tofu|terraform> destroy -auto-approve`` in ``terraform_dir``.

    Resolves the IaC binary at call time via
    :func:`tabula_cli._terraform.find_binary` so OpenTofu (``tofu``) is
    preferred and Terraform (``terraform``) is the fallback. The caller is
    responsible for surfacing stderr on failure.
    """
    try:
        binary = _tf.find_binary()
    except _tf.TerraformNotFoundError as exc:
        # Encode the missing-binary case as a non-zero result so the
        # caller's normal failure path renders the message and exits the
        # right code, rather than crashing.
        return TerraformResult(returncode=127, stdout="", stderr=str(exc))

    proc = subprocess.run(  # noqa: S603 — controlled, not user-driven argv
        [binary, "destroy", "-auto-approve"],
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
    | destroy fails (tofu/tf)     | exit 2, state dir preserved            |
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
                f"Use --force to skip the destroy step and verify cloud-side only.",
                err=True,
            )
            return EXIT_USER_ERROR

        # Resolve the binary name once so logs reflect what actually ran.
        try:
            binary_name = Path(_tf.find_binary()).name
        except _tf.TerraformNotFoundError:
            binary_name = "tofu"
        _emit(f"running: {binary_name} destroy in {terraform_dir}")
        tf = opts.terraform_runner(terraform_dir)
        if not tf.ok:
            _emit(
                f"{binary_name} destroy failed; local state preserved for retry.\n"
                f"  retry: tabula enclave down {name}\n"
                f"  recover: tabula enclave down {name} --force --project=<gcp project id>",
                err=True,
            )
            if tf.stderr:
                _emit(tf.stderr.rstrip(), err=True)
            return EXIT_TERRAFORM_FAILURE
    else:
        _emit(
            "--force: skipping destroy (no state); cloud-side check only."
        )

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
# Typer command                                                               #
# --------------------------------------------------------------------------- #


def down(
    name: str = typer.Argument(..., help="Enclave name (DNS-safe)."),
    yes: bool = typer.Option(
        False,
        "--yes",
        help="Skip the destructive-confirmation prompt (CI/scripted teardown).",
    ),
    force: bool = typer.Option(
        False,
        "--force",
        help=(
            "Recovery path: attempt destroy when state.json is missing or "
            f"corrupt by querying GCP for resources labelled "
            f"{ENCLAVE_LABEL_KEY}=<name>. Requires --project."
        ),
    ),
    project: str | None = typer.Option(
        None,
        "--project",
        help="GCP project id (required with --force when state is missing).",
    ),
) -> None:
    """Tear an enclave down to zero residual cost.

    Runs ``tofu destroy`` (falling back to ``terraform destroy`` when
    OpenTofu isn't installed), then independently verifies via the GCP API
    that nothing labelled ``enclave=<name>`` remains. Removes the local
    state directory only on a clean destroy + clean verification.
    """
    opts = DownOptions(
        name=name,
        yes=bool(yes),
        force=bool(force),
        project=project,
    )
    rc = enclave_down(opts)
    raise typer.Exit(code=rc)


# --------------------------------------------------------------------------- #
# Legacy argparse wiring                                                      #
# --------------------------------------------------------------------------- #
#
# Kept for callers that still consume the argparse-style entry points (the
# ``ssh`` test suite and any external scripts wired into the pre-rename
# parser). New code should use the Typer ``down`` callable above and the
# ``app`` re-exposed by :mod:`tabula_cli.enclave`.

import argparse  # noqa: E402  -- intentional: keep argparse import opt-in


def add_subparser(sub: "argparse._SubParsersAction") -> None:
    """Register the ``down`` argparse subcommand on ``sub``."""
    p = sub.add_parser(
        "down",
        help="Tear down an enclave to zero residual cost.",
        description=(
            "Tear down enclave <name>. Runs `tofu destroy` (falls back to "
            "`terraform destroy`), then independently verifies via the GCP "
            f"API that nothing labelled {ENCLAVE_LABEL_KEY}=<name> remains. "
            "Removes the local state directory only on a clean destroy + "
            "clean verification."
        ),
    )
    p.add_argument("name", help="Enclave name (DNS-safe).")
    p.add_argument(
        "--yes",
        action="store_true",
        help="Skip the destructive-confirmation prompt (CI/scripted teardown).",
    )
    p.add_argument(
        "--force",
        action="store_true",
        help=(
            "Recovery path: attempt destroy when state.json is missing or "
            "corrupt by querying GCP for resources labelled "
            f"{ENCLAVE_LABEL_KEY}=<name>. Requires --project."
        ),
    )
    p.add_argument(
        "--project",
        default=None,
        help="GCP project id (required with --force when state is missing).",
    )


def run(args: "argparse.Namespace") -> int:
    """Dispatch the parsed argparse ``down`` invocation."""
    opts = DownOptions(
        name=args.name,
        yes=bool(args.yes),
        force=bool(args.force),
        project=args.project,
    )
    return enclave_down(opts)
