"""Thin wrapper around the ``tofu`` / ``terraform`` binary.

Decision (from #26 implementation notes): shell out via :mod:`subprocess`
rather than depend on ``python-terraform``. We get the real error messages,
the real exit codes, and we don't have to vendor a wrapper that lags
upstream releases.

OpenTofu migration (#96): prefer the ``tofu`` binary (OpenTofu, MPL 2.0)
and fall back to ``terraform`` (BUSL since v1.6) if ``tofu`` isn't on
``PATH``. Both expose the same CLI surface for ``init``/``plan``/
``apply``/``output``/``destroy``/``validate``/``fmt``, and OpenTofu reads
the same Terraform Registry providers (``hashicorp/google`` etc.) and the
same on-disk state file format. The fallback keeps the CLI usable for
contributors who haven't installed OpenTofu yet.

This module is pure plumbing: it does no policy. The ``up`` command in
``enclave/up.py`` decides what to do with non-zero exit codes.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence


class TerraformNotFoundError(RuntimeError):
    """Raised when neither ``tofu`` nor ``terraform`` is on ``PATH``.

    The historical name is preserved for API stability; the error message
    mentions both binaries since either satisfies the dependency.
    """


class TerraformError(RuntimeError):
    """Raised when a ``tofu``/``terraform`` invocation exits non-zero.

    Attributes:
        returncode: The process's exit code.
        stdout:     Combined captured stdout (may be empty in streaming mode).
        stderr:     Combined captured stderr (may be empty in streaming mode).
        binary:     The binary name that was actually invoked
                    (``"tofu"`` or ``"terraform"``); useful for error
                    messages that want to reflect what the user actually ran.
    """

    def __init__(
        self,
        returncode: int,
        stdout: str = "",
        stderr: str = "",
        binary: str = "terraform",
    ) -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr
        self.binary = binary
        super().__init__(
            f"{binary} exited with code {returncode}"
            + (f": {stderr.strip()}" if stderr.strip() else "")
        )


@dataclass(frozen=True)
class TerraformResult:
    """Captured output of a ``run`` invocation."""

    returncode: int
    stdout: str
    stderr: str


# --------------------------------------------------------------------------- #
# Binary discovery                                                            #
# --------------------------------------------------------------------------- #


def find_binary() -> str:
    """Return the IaC binary path, preferring OpenTofu over Terraform.

    Resolution order:

    1. ``tofu``       — OpenTofu (MPL 2.0).
    2. ``terraform``  — HashiCorp Terraform (BUSL since v1.6); fallback for
                        contributors who haven't installed OpenTofu yet.

    Returns:
        Absolute path to the executable.

    Raises:
        TerraformNotFoundError: If neither binary is found on ``PATH``.
    """
    for name in ("tofu", "terraform"):
        path = shutil.which(name)
        if path is not None:
            return path
    raise TerraformNotFoundError(
        "neither 'tofu' (OpenTofu) nor 'terraform' (Terraform) found on PATH; "
        "install OpenTofu (https://opentofu.org/docs/intro/install/) "
        "or Terraform (https://developer.hashicorp.com/terraform/downloads)"
    )


def find_terraform() -> str:
    """Backwards-compatible alias for :func:`find_binary`.

    Older code/tests imported ``find_terraform``; keep the name so we don't
    churn unrelated callers. New code should call :func:`find_binary`.
    """
    return find_binary()


def _binary_name(path: str) -> str:
    """Return the trailing component of a binary path (``tofu`` or ``terraform``).

    Used to make error messages and warnings reflect what was actually run.
    """
    return Path(path).name


def run(
    args: Sequence[str],
    *,
    cwd: Path,
    env: Mapping[str, str] | None = None,
    stream: bool = False,
    check: bool = True,
) -> TerraformResult:
    """Run ``<tofu|terraform> <args>`` in ``cwd``.

    Args:
        args: Subcommand arguments (e.g. ``["init", "-input=false"]``).
        cwd:  Working directory for the invocation; must contain the root
              module's ``.tf`` files.
        env:  Optional environment overlay; merged with the parent env when
              :mod:`subprocess` is invoked.
        stream: If True, child stdout/stderr inherit the parent's tty so
              the user sees live output. The returned :class:`TerraformResult`
              will have empty ``stdout`` / ``stderr`` strings in that case.
        check: If True (default), raise :class:`TerraformError` on non-zero
              exit. If False, return a :class:`TerraformResult` regardless.

    Raises:
        TerraformNotFoundError: If neither ``tofu`` nor ``terraform`` is installed.
        TerraformError: If ``check`` is True and the process exits non-zero.
    """
    binary = find_binary()
    cwd = Path(cwd)
    cwd.mkdir(parents=True, exist_ok=True)

    cmd = [binary, *args]

    # subprocess uses the parent env when env=None; only build a new dict
    # when an overlay is requested. This preserves PATH / GOOGLE_* /
    # TF_LOG / etc. without surprising shadowing.
    proc_env: Mapping[str, str] | None = None
    if env is not None:
        import os as _os

        merged = dict(_os.environ)
        merged.update(env)
        proc_env = merged

    if stream:
        # Live output; do not capture.
        completed = subprocess.run(
            cmd,
            cwd=str(cwd),
            env=proc_env,
            check=False,
        )
        result = TerraformResult(
            returncode=completed.returncode, stdout="", stderr=""
        )
    else:
        completed = subprocess.run(
            cmd,
            cwd=str(cwd),
            env=proc_env,
            check=False,
            capture_output=True,
            text=True,
        )
        result = TerraformResult(
            returncode=completed.returncode,
            stdout=completed.stdout or "",
            stderr=completed.stderr or "",
        )

    if check and result.returncode != 0:
        raise TerraformError(
            result.returncode,
            result.stdout,
            result.stderr,
            binary=_binary_name(binary),
        )
    return result


def init(cwd: Path, *, stream: bool = False) -> TerraformResult:
    """``<tofu|terraform> init -input=false``. Idempotent."""
    return run(["init", "-input=false"], cwd=cwd, stream=stream)


def plan(cwd: Path, *, stream: bool = False) -> TerraformResult:
    """``<tofu|terraform> plan -input=false``."""
    return run(["plan", "-input=false"], cwd=cwd, stream=stream)


def apply(cwd: Path, *, stream: bool = False) -> TerraformResult:
    """``<tofu|terraform> apply -auto-approve -input=false``."""
    return run(
        ["apply", "-auto-approve", "-input=false"],
        cwd=cwd,
        stream=stream,
    )


def output_json(cwd: Path) -> dict:
    """Return ``<tofu|terraform> output -json`` as a flat ``{key: value}`` dict.

    Terraform/OpenTofu's ``-json`` form wraps each value as
    ``{"value": ..., "type": ...}``; this strips that envelope so callers
    can write the values straight into ``state.json`` outputs.
    """
    result = run(["output", "-json"], cwd=cwd, stream=False)
    raw = json.loads(result.stdout or "{}")
    flat: dict = {}
    for k, wrapped in raw.items():
        if isinstance(wrapped, dict) and "value" in wrapped:
            flat[k] = wrapped["value"]
        else:
            flat[k] = wrapped
    return flat


__all__ = [
    "TerraformError",
    "TerraformNotFoundError",
    "TerraformResult",
    "apply",
    "find_binary",
    "find_terraform",
    "init",
    "output_json",
    "plan",
    "run",
]
