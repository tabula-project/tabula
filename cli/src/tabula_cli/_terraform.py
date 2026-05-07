"""Thin wrapper around the ``terraform`` binary.

Decision (from #26 implementation notes): shell out via :mod:`subprocess`
rather than depend on ``python-terraform``. We get the real error messages,
the real exit codes, and we don't have to vendor a wrapper that lags
upstream Terraform versions.

This module is pure plumbing: it does no policy. The ``up`` command in
``enclave.py`` decides what to do with non-zero exit codes.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence


class TerraformNotFoundError(RuntimeError):
    """Raised when no ``terraform`` binary is on ``PATH``."""


class TerraformError(RuntimeError):
    """Raised when a terraform invocation exits non-zero.

    Attributes:
        returncode: The terraform process's exit code.
        stdout:     Combined captured stdout (may be empty in streaming mode).
        stderr:     Combined captured stderr (may be empty in streaming mode).
    """

    def __init__(self, returncode: int, stdout: str = "", stderr: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr
        super().__init__(
            f"terraform exited with code {returncode}"
            + (f": {stderr.strip()}" if stderr.strip() else "")
        )


@dataclass(frozen=True)
class TerraformResult:
    """Captured output of a ``run`` invocation."""

    returncode: int
    stdout: str
    stderr: str


def find_terraform() -> str:
    """Locate the terraform binary, or raise.

    Returns:
        Absolute path to the ``terraform`` executable.

    Raises:
        TerraformNotFoundError: If no binary is found on ``PATH``.
    """
    path = shutil.which("terraform")
    if path is None:
        raise TerraformNotFoundError(
            "terraform binary not found on PATH; "
            "install Terraform >= 1.5 (https://developer.hashicorp.com/terraform/downloads)"
        )
    return path


def run(
    args: Sequence[str],
    *,
    cwd: Path,
    env: Mapping[str, str] | None = None,
    stream: bool = False,
    check: bool = True,
) -> TerraformResult:
    """Run ``terraform <args>`` in ``cwd``.

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
        TerraformNotFoundError: If terraform isn't installed.
        TerraformError: If ``check`` is True and the process exits non-zero.
    """
    binary = find_terraform()
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
            result.returncode, result.stdout, result.stderr
        )
    return result


def init(cwd: Path, *, stream: bool = False) -> TerraformResult:
    """``terraform init -input=false``. Idempotent."""
    return run(["init", "-input=false"], cwd=cwd, stream=stream)


def plan(cwd: Path, *, stream: bool = False) -> TerraformResult:
    """``terraform plan -input=false``."""
    return run(["plan", "-input=false"], cwd=cwd, stream=stream)


def apply(cwd: Path, *, stream: bool = False) -> TerraformResult:
    """``terraform apply -auto-approve -input=false``."""
    return run(
        ["apply", "-auto-approve", "-input=false"],
        cwd=cwd,
        stream=stream,
    )


def output_json(cwd: Path) -> dict:
    """Return ``terraform output -json`` as a flat ``{key: value}`` dict.

    Terraform's ``-json`` form wraps each value as
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
    "find_terraform",
    "init",
    "output_json",
    "plan",
    "run",
]
