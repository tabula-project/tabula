"""Offline integration smoke test for ``tabula enclave up --dry-run``.

Runs the real terraform binary against the stub root module shipped in
``terraform/enclave/`` (with sibling modules pointing at ``./stubs/<name>``).
No GCP credentials, no network, no providers — just `terraform init` + `plan`.

Skipped automatically when ``terraform`` is not on ``PATH``.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from typer.testing import CliRunner

from tabula_cli.enclave import up as enclave_mod
from tabula_cli.main import app as root_app


# cli/tests/test_enclave_up_smoke.py -> repo root is parents[2]
REPO_ROOT = Path(__file__).resolve().parents[2]
TF_ROOT = REPO_ROOT / "terraform" / "enclave"

pytestmark = pytest.mark.skipif(
    shutil.which("terraform") is None,
    reason="terraform binary not on PATH",
)


def test_dry_run_against_real_stub_root_module(tmp_path, monkeypatch) -> None:
    if not TF_ROOT.exists():
        pytest.skip(f"terraform root module missing at {TF_ROOT}")

    # Isolate enclave home and point at the real stub root module.
    monkeypatch.setenv("TABULA_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("TABULA_TERRAFORM_ROOT", str(TF_ROOT))

    runner = CliRunner()
    result = runner.invoke(
        root_app,
        [
            "enclave",
            "up",
            "smoke",
            "--project", "tabula-smoke",
            "--region", "us-central1",
            "--dry-run",
        ],
    )

    # Exit 0 and a helpful summary.
    assert result.exit_code == enclave_mod.EXIT_OK, (
        f"stdout=\n{result.stdout}\nstderr=\n{result.stderr}"
    )
    assert "(dry-run) plan complete" in result.stdout

    # Working directory was materialized but state.json was NOT written
    # (dry-run never persists state — that's what `--dry-run` means).
    workdir = tmp_path / "home" / "enclaves" / "smoke"
    assert (workdir / "terraform.tfvars").exists()
    assert (workdir / "terraform" / "main.tf").exists()
    assert not (workdir / "state.json").exists()
