"""``tabula enclave up`` subcommand (issue #26).

Provisions (or re-applies) a Tabula enclave by driving the
``terraform/enclave/`` root module. Idempotent: re-running on an existing
enclave prints current status and asks before re-applying. ``--yes`` skips
the prompt for scripting.

The IaC binary is resolved at run time by
:func:`tabula_cli._terraform.find_binary`: ``tofu`` (OpenTofu, MPL 2.0)
when available, otherwise ``terraform`` (BUSL since v1.6) as a fallback
(#96). The ``.tf`` files themselves are unchanged — OpenTofu reads the
same Terraform Registry providers and the same state-file format.

Exit codes (per #26 acceptance criteria):

- 0: success
- 2: validation error (bad name, bad flag, wrong schema version on disk)
- 3: IaC invocation (tofu/terraform) failed
- 4: GCP application-default credentials missing
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
from pathlib import Path
from typing import Optional

import typer

from tabula_cli import state as state_mod
from tabula_cli import _terraform as tf

# Exit codes are part of the public contract; keep stable.
EXIT_OK = 0
EXIT_VALIDATION = 2
EXIT_TERRAFORM = 3
EXIT_AUTH = 4

logger = logging.getLogger("tabula_cli.enclave.up")


# --------------------------------------------------------------------------- #
# Helpers                                                                     #
# --------------------------------------------------------------------------- #


#: Valid values for the ``--composition`` flag. ``stub`` is the offline-plan
#: composition under ``terraform/enclave/`` (synthetic outputs, no GCP calls).
#: ``prod`` is the real-GCP composition under ``terraform/enclave-prod/``
#: which requires ADC. See issue #107 for the design rationale.
VALID_COMPOSITIONS = ("stub", "prod")


def _terraform_root_module(composition: str = "stub") -> Path:
    """Return the path to the Terraform root module for ``composition``.

    The CLI ships alongside the substrate repo; root modules live at the
    repo root in ``terraform/enclave/`` (stub) and ``terraform/enclave-prod/``
    (prod). We resolve relative to this file so ``pip install -e cli`` from
    the repo root still finds them. The ``TABULA_TERRAFORM_ROOT`` env var
    overrides for tests and out-of-tree installs (overrides apply to both
    compositions; tests pick the desired dir layout).
    """
    if composition not in VALID_COMPOSITIONS:
        raise ValueError(
            f"composition must be one of {VALID_COMPOSITIONS}, got {composition!r}"
        )

    override = os.environ.get("TABULA_TERRAFORM_ROOT")
    if override:
        return Path(override).expanduser().resolve()

    # cli/src/tabula_cli/enclave/up.py -> ../../../../terraform/<composition-dir>
    here = Path(__file__).resolve()
    subdir = "enclave" if composition == "stub" else "enclave-prod"
    return here.parents[4] / "terraform" / subdir


def _detect_default_project() -> Optional[str]:
    """Best-effort: ``gcloud config get-value project``.

    Returns ``None`` if ``gcloud`` is missing or the value is unset/blank.
    """
    if shutil.which("gcloud") is None:
        return None
    try:
        out = subprocess.run(
            ["gcloud", "config", "get-value", "project"],
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
    except (subprocess.TimeoutExpired, OSError):
        return None
    if out.returncode != 0:
        return None
    candidate = (out.stdout or "").strip()
    # gcloud may print "(unset)" when no project is configured.
    if not candidate or candidate.lower() == "(unset)":
        return None
    return candidate


def _adc_present() -> bool:
    """Return True iff GCP application-default credentials look available.

    Mirrors the lookup order used by Google Cloud client libraries: explicit
    ``GOOGLE_APPLICATION_CREDENTIALS`` env var first, then the well-known
    ADC path under ``~/.config/gcloud``.
    """
    explicit = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
    if explicit and Path(explicit).expanduser().exists():
        return True
    well_known = (
        Path.home() / ".config" / "gcloud" / "application_default_credentials.json"
    )
    return well_known.exists()


def _write_tfvars(
    target_dir: Path,
    *,
    project_id: str,
    region: str,
    name: str,
    zone: Optional[str] = None,
    gpu_accelerator_type: Optional[str] = None,
    gpu_machine_type: Optional[str] = None,
) -> Path:
    """Render a minimal ``terraform.tfvars`` for the enclave root module.

    Optional kwargs are emitted only when non-None so the prod composition's
    own defaults (T4 + n1-standard-4) remain authoritative when the operator
    doesn't pass `--gpu-type` / `--machine-type`. The stub composition
    silently ignores extra vars.
    """
    target_dir.mkdir(parents=True, exist_ok=True)
    tfvars = target_dir / "terraform.tfvars"
    lines = [
        f'project_id   = "{project_id}"',
        f'region       = "{region}"',
        f'enclave_name = "{name}"',
    ]
    if zone:
        lines.append(f'zone         = "{zone}"')
    if gpu_accelerator_type:
        lines.append(f'gpu_accelerator_type = "{gpu_accelerator_type}"')
    if gpu_machine_type:
        lines.append(f'gpu_machine_type     = "{gpu_machine_type}"')
    tfvars.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return tfvars


def _materialize_workdir(
    name: str,
    *,
    project_id: str,
    region: str,
    composition: str = "stub",
    zone: Optional[str] = None,
    gpu_accelerator_type: Optional[str] = None,
    gpu_machine_type: Optional[str] = None,
) -> Path:
    """Prepare the per-enclave working directory and return its terraform dir.

    Layout produced for ``stub`` composition::

        ~/.tabula/enclaves/<name>/
            terraform.tfvars     (the canonical, user-visible vars file)
            terraform/           (a copy of the stub root module; sibling
                                  modules live at ./stubs/<name>/ inside)
                main.tf
                ...
                terraform.tfvars (a copy of the parent so terraform auto-loads)

    Layout produced for ``prod`` composition (note the additional ``modules/``
    sibling — the prod root references its sibling modules at
    ``../modules/<name>``)::

        ~/.tabula/enclaves/<name>/
            terraform.tfvars
            modules/             (copy of terraform/modules/ so prod's
                                  ``../modules/<name>`` references resolve)
            terraform/           (a copy of the prod root module)
                main.tf          (references ../modules/<name>)
                ...

    We copy rather than symlink the root module so the per-enclave dir is
    self-contained and survives a CLI upgrade or repo move.

    ``composition`` picks between the stub (offline-plan, synthetic outputs)
    and prod (real GCP, requires ADC) root modules.
    """
    src = _terraform_root_module(composition)
    if not src.is_dir():
        raise FileNotFoundError(
            f"terraform root module not found at {src}; "
            "set TABULA_TERRAFORM_ROOT or run from a Tabula checkout"
        )

    workdir = state_mod.enclave_dir(name)
    workdir.mkdir(parents=True, exist_ok=True)
    tf_dir = workdir / "terraform"
    ignore = shutil.ignore_patterns(
        ".terraform",
        ".terraform.lock.hcl",
        "terraform.tfstate*",
        "*.tfplan",
    )

    if not tf_dir.exists():
        # First run: copy the entire root module tree (excluding any local
        # .terraform/ from the source -- that's a per-init artifact).
        shutil.copytree(src, tf_dir, ignore=ignore)

    # The prod composition's main.tf references sibling modules at
    # ``../modules/<name>``. Materialize them next to the copied root so the
    # relative path resolves inside the workdir. The stub composition's
    # sibling modules live INSIDE ``./stubs/<name>/`` and were copied above.
    if composition == "prod":
        modules_src = src.parent / "modules"
        modules_dst = workdir / "modules"
        if modules_src.is_dir() and not modules_dst.exists():
            shutil.copytree(modules_src, modules_dst, ignore=ignore)

    # Generate the canonical tfvars next to state.json (operator-visible)...
    canonical_tfvars = _write_tfvars(
        workdir,
        project_id=project_id,
        region=region,
        name=name,
        zone=zone,
        gpu_accelerator_type=gpu_accelerator_type,
        gpu_machine_type=gpu_machine_type,
    )
    # ...and mirror them into the terraform working dir so terraform auto-loads.
    target = tf_dir / "terraform.tfvars"
    if target.exists() or target.is_symlink():
        target.unlink()
    shutil.copy2(canonical_tfvars, target)

    return tf_dir


def _capture_outputs_safely(tf_dir: Path) -> dict:
    """Best-effort IaC outputs (``tofu output -json`` / ``terraform output -json``).

    The stub root module emits ``classifier_ip`` and ``noise_port`` as
    placeholders. Real outputs land as sibling modules merge. We never let
    an output read failure mask a successful apply -- log and return ``{}``.
    """
    try:
        return tf.output_json(tf_dir)
    except (tf.TerraformError, tf.TerraformNotFoundError, ValueError) as e:
        logger.warning("could not read terraform outputs: %s", e)
        return {}


def _print_success(s: state_mod.EnclaveState) -> None:
    """Print the human-readable success summary required by #26."""
    classifier_ip = s.outputs.get("classifier_ip", "<pending>")
    noise_port = s.outputs.get("noise_port", "<pending>")
    typer.echo(f"Enclave: {s.name}")
    typer.echo(f"  classifier IP: {classifier_ip}")
    typer.echo(f"  Noise port:    {noise_port}")
    typer.echo(f"  state file:    {state_mod.state_path(s.name)}")
    typer.echo(f"  next: tabula enclave status {s.name}")


# --------------------------------------------------------------------------- #
# Subcommand: up                                                              #
# --------------------------------------------------------------------------- #


def up(
    name: str = typer.Argument(..., help="Enclave name (DNS-safe, 3-30 chars)."),
    project: Optional[str] = typer.Option(
        None,
        "--project",
        help="GCP project ID. Defaults to `gcloud config get-value project`.",
    ),
    region: str = typer.Option(
        "us-central1",
        "--region",
        help="GCP region for the enclave.",
    ),
    zone: Optional[str] = typer.Option(
        None,
        "--zone",
        help=(
            "GCP zone for VM-bearing modules (classifier, gpu, gitea). Defaults "
            "to `<region>-a`. Pin a different zone (e.g. `us-east1-c`) when the "
            "default zone is at T4 capacity."
        ),
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Run `tofu plan` (or `terraform plan`) only; print the plan and exit 0.",
    ),
    yes: bool = typer.Option(
        False,
        "--yes",
        "-y",
        help="Skip the re-apply confirmation prompt (CI / scripting).",
    ),
    verbose: bool = typer.Option(
        False,
        "--verbose",
        "-v",
        help="Stream tofu/terraform stdout/stderr live instead of summarizing.",
    ),
    composition: str = typer.Option(
        "stub",
        "--composition",
        help=(
            "Terraform composition to run. 'stub' (default) uses synthetic "
            "modules and works offline; 'prod' wires the real GCP modules and "
            "requires `gcloud auth application-default login` first. See "
            "terraform/enclave-prod/README.md for the trade-offs."
        ),
    ),
    gpu_type: Optional[str] = typer.Option(
        None,
        "--gpu-type",
        help=(
            "GPU accelerator type for the inference host (prod composition only). "
            "Default keeps the composition's `nvidia-tesla-t4` (cheapest viable "
            "shape). Use `nvidia-l4` when T4 capacity is exhausted in your zone "
            "— L4 is often available when T4 is not, but requires a `g2-*` "
            "machine type (pair with `--machine-type g2-standard-4`)."
        ),
    ),
    machine_type: Optional[str] = typer.Option(
        None,
        "--machine-type",
        help=(
            "GCE machine type for the GPU host (prod composition only). Default "
            "keeps the composition's `n1-standard-4` (pairs with T4). Use "
            "`g2-standard-4` (or larger) when running L4 — the `g2-*` family is "
            "the only one that attaches L4."
        ),
    ),
) -> None:
    """Provision (or re-apply) the enclave named ``name``.

    Idempotent: re-running on an existing enclave prints current status and
    asks before re-applying. ``--yes`` skips the prompt for scripting.
    """
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(message)s",
    )

    # 1) Validate name first -- cheapest failure mode.
    try:
        state_mod.validate_enclave_name(name)
    except state_mod.EnclaveNameError as e:
        typer.echo(f"error: {e}", err=True)
        raise typer.Exit(code=EXIT_VALIDATION) from None

    # 1b) Validate composition.
    if composition not in VALID_COMPOSITIONS:
        typer.echo(
            f"error: --composition must be one of {VALID_COMPOSITIONS}, "
            f"got {composition!r}",
            err=True,
        )
        raise typer.Exit(code=EXIT_VALIDATION)

    # 2) Resolve project.
    project_id = project or _detect_default_project()
    if not project_id:
        typer.echo(
            "error: no GCP project specified. Pass --project or run "
            "`gcloud config set project <id>`.",
            err=True,
        )
        raise typer.Exit(code=EXIT_VALIDATION)

    # 3) ADC check before we even touch the IaC tool -- fail fast with a
    #    clean message rather than letting `tofu apply` (or `terraform
    #    apply`) blow up opaquely. The stub composition makes no GCP calls
    #    so the check is skipped for it; the prod composition requires ADC
    #    for both plan AND apply.
    needs_adc = composition == "prod" or not dry_run
    if needs_adc and not _adc_present():
        typer.echo(
            "error: GCP application-default credentials not found. Run:\n"
            "       gcloud auth application-default login",
            err=True,
        )
        raise typer.Exit(code=EXIT_AUTH)

    # 4) Existing enclave: confirm before re-applying (acceptance: idempotent
    #    no-op-with-confirmation).
    already_exists = state_mod.state_exists(name)
    if already_exists and not dry_run:
        try:
            existing = state_mod.read_state(name)
        except state_mod.StateSchemaError as e:
            typer.echo(f"error: {e}", err=True)
            raise typer.Exit(code=EXIT_VALIDATION) from None

        typer.echo(
            f"Enclave {name!r} already exists "
            f"(project={existing.project_id}, region={existing.region}, "
            f"created_at={existing.created_at})."
        )
        if not yes:
            confirm = typer.confirm("Re-apply terraform?", default=False)
            if not confirm:
                typer.echo("aborted; nothing changed.")
                raise typer.Exit(code=EXIT_OK)

    # 5) Materialize the working directory and tfvars.
    # Default zone to `<region>-a` if not pinned. The prod composition's
    # variables.tf has `default = "us-central1-a"` baked in, but for any
    # non-us-central1 region that default is wrong, so the CLI computes the
    # right zone-a based on the current region.
    effective_zone = zone or f"{region}-a"
    try:
        tf_dir = _materialize_workdir(
            name,
            project_id=project_id,
            region=region,
            composition=composition,
            zone=effective_zone,
            gpu_accelerator_type=gpu_type,
            gpu_machine_type=machine_type,
        )
    except FileNotFoundError as e:
        typer.echo(f"error: {e}", err=True)
        raise typer.Exit(code=EXIT_VALIDATION) from None

    # 6) IaC init (idempotent). Binary is `tofu` (preferred) or `terraform`
    #    (fallback); error messages reflect what actually ran.
    try:
        tf.init(tf_dir, stream=verbose)
    except tf.TerraformNotFoundError as e:
        typer.echo(f"error: {e}", err=True)
        raise typer.Exit(code=EXIT_VALIDATION) from None
    except tf.TerraformError as e:
        typer.echo(f"error: {e.binary} init failed", err=True)
        if e.stderr:
            typer.echo(e.stderr, err=True)
        raise typer.Exit(code=EXIT_TERRAFORM) from None

    # 7) Plan or apply.
    if dry_run:
        try:
            result = tf.plan(tf_dir, stream=verbose)
        except tf.TerraformError as e:
            typer.echo(f"error: {e.binary} plan failed", err=True)
            if e.stderr:
                typer.echo(e.stderr, err=True)
            raise typer.Exit(code=EXIT_TERRAFORM) from None
        if not verbose and result.stdout:
            typer.echo(result.stdout)
        typer.echo(f"\n(dry-run) plan complete for enclave {name!r}.")
        raise typer.Exit(code=EXIT_OK)

    try:
        tf.apply(tf_dir, stream=verbose)
    except tf.TerraformError as e:
        typer.echo(f"error: {e.binary} apply failed", err=True)
        if e.stderr:
            typer.echo(e.stderr, err=True)
        # Acceptance: leave state intact on failure.
        raise typer.Exit(code=EXIT_TERRAFORM) from None

    # 8) Capture outputs and persist state.json.
    outputs = _capture_outputs_safely(tf_dir)

    if already_exists:
        # Preserve original created_at on re-apply; refresh outputs.
        try:
            existing = state_mod.read_state(name)
            new_state = state_mod.EnclaveState(
                name=name,
                project_id=project_id,
                region=region,
                created_at=existing.created_at,
                terraform_dir=str(tf_dir),
                outputs=outputs,
            )
        except state_mod.StateSchemaError:
            new_state = state_mod.EnclaveState(
                name=name,
                project_id=project_id,
                region=region,
                created_at=state_mod.now_utc_iso(),
                terraform_dir=str(tf_dir),
                outputs=outputs,
            )
    else:
        new_state = state_mod.EnclaveState(
            name=name,
            project_id=project_id,
            region=region,
            created_at=state_mod.now_utc_iso(),
            terraform_dir=str(tf_dir),
            outputs=outputs,
        )

    state_mod.write_state(new_state)
    _print_success(new_state)
    raise typer.Exit(code=EXIT_OK)


__all__ = [
    "EXIT_AUTH",
    "EXIT_OK",
    "EXIT_TERRAFORM",
    "EXIT_VALIDATION",
    "up",
]
