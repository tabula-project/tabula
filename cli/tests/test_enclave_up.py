"""Unit tests for ``tabula enclave up``.

Covers argument parsing, name validation, ADC detection, idempotency
(re-apply confirmation), exit codes, and state-file shape — all with the
actual terraform invocation mocked out.

A small offline integration smoke test exists in
``test_enclave_up_smoke.py``; that one runs the real terraform binary
against the stub root module.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest import mock

import pytest
from typer.testing import CliRunner

from tabula_cli import state as state_mod
from tabula_cli import _terraform as tf_mod
from tabula_cli.enclave import up as enclave_mod
from tabula_cli.main import app as root_app


@pytest.fixture
def runner() -> CliRunner:
    # click >= 8.2 dropped the ``mix_stderr`` kwarg; CliRunner's default
    # already keeps stderr separate. Construct without kwargs for portability.
    return CliRunner()


@pytest.fixture
def tabula_home(tmp_path, monkeypatch) -> Path:
    """Isolate ~/.tabula/enclaves to a tmp_path and provide a stub root module."""
    monkeypatch.setenv("TABULA_HOME", str(tmp_path / "home"))

    # Provide a tiny terraform root so _materialize_workdir() doesn't fail
    # when it tries to copy the real one. We don't run terraform here —
    # the tf_mod helpers are all mocked — but the directory must exist.
    fake_root = tmp_path / "tf_root"
    fake_root.mkdir()
    (fake_root / "main.tf").write_text("# placeholder\n")
    monkeypatch.setenv("TABULA_TERRAFORM_ROOT", str(fake_root))

    # Pretend ADC is present so we don't hit the EXIT_AUTH path in non-dry-run
    # tests. Tests that exercise the missing-ADC path override this.
    monkeypatch.setattr(enclave_mod, "_adc_present", lambda: True)
    return tmp_path


@pytest.fixture
def mock_tf(monkeypatch):
    """Mock terraform init/plan/apply/output_json to no-ops."""
    init = mock.MagicMock(return_value=tf_mod.TerraformResult(0, "", ""))
    plan = mock.MagicMock(
        return_value=tf_mod.TerraformResult(0, "Plan: 0 to add", "")
    )
    apply = mock.MagicMock(return_value=tf_mod.TerraformResult(0, "", ""))
    out = mock.MagicMock(
        return_value={"classifier_ip": "203.0.113.10", "noise_port": 51820}
    )
    monkeypatch.setattr(tf_mod, "init", init)
    monkeypatch.setattr(tf_mod, "plan", plan)
    monkeypatch.setattr(tf_mod, "apply", apply)
    monkeypatch.setattr(tf_mod, "output_json", out)
    return {"init": init, "plan": plan, "apply": apply, "output_json": out}


# --------------------------------------------------------------------------- #
# Help / argument parsing                                                     #
# --------------------------------------------------------------------------- #

class TestArgParsing:
    def test_help_lists_subcommands(self, runner: CliRunner) -> None:
        result = runner.invoke(root_app, ["enclave", "--help"])
        assert result.exit_code == 0
        out = result.stdout
        assert "up" in out
        assert "down" in out
        assert "status" in out
        assert "ssh" in out

    def test_up_help_lists_flags(self, runner: CliRunner) -> None:
        result = runner.invoke(root_app, ["enclave", "up", "--help"])
        assert result.exit_code == 0
        # Typer/Rich injects ANSI colour codes that split flag names like
        # "\x1b[1;36m-\x1b[0m\x1b[1;36m-project\x1b[0m" — strip them before
        # the substring check so the test isn't brittle to colour formatting.
        import re
        out = re.sub(r"\x1b\[[0-9;]*m", "", result.stdout)
        for flag in (
            "--project", "--region", "--zone", "--dry-run", "--yes",
            "--verbose", "--composition", "--gpu-type", "--machine-type",
        ):
            assert flag in out

    def test_up_rejects_invalid_composition(
        self, runner: CliRunner, tabula_home: Path
    ) -> None:
        result = runner.invoke(
            root_app,
            ["enclave", "up", "valid-name", "--project", "p", "--composition", "bogus"],
        )
        assert result.exit_code == enclave_mod.EXIT_VALIDATION
        assert "composition must be one of" in (result.stderr or result.stdout)

    def test_up_requires_name(self, runner: CliRunner) -> None:
        result = runner.invoke(root_app, ["enclave", "up"])
        # Missing required arg: typer/click exits 2.
        assert result.exit_code != 0

    def test_up_rejects_invalid_name(
        self, runner: CliRunner, tabula_home: Path
    ) -> None:
        result = runner.invoke(
            root_app,
            ["enclave", "up", "Bad-Name", "--project", "p", "--dry-run"],
        )
        assert result.exit_code == enclave_mod.EXIT_VALIDATION
        assert "DNS-safe" in (result.stderr or "")

    def test_up_rejects_missing_project(
        self, runner: CliRunner, tabula_home: Path, monkeypatch
    ) -> None:
        # No --project, and gcloud detection returns None.
        monkeypatch.setattr(
            enclave_mod, "_detect_default_project", lambda: None
        )
        result = runner.invoke(root_app, ["enclave", "up", "demo", "--dry-run"])
        assert result.exit_code == enclave_mod.EXIT_VALIDATION
        assert "no GCP project" in (result.stderr or "")


# --------------------------------------------------------------------------- #
# Composition selection                                                       #
# --------------------------------------------------------------------------- #

class TestComposition:
    def test_root_module_defaults_to_stub(self, monkeypatch) -> None:
        # Unset the env override so the function uses its package-relative path.
        monkeypatch.delenv("TABULA_TERRAFORM_ROOT", raising=False)
        result = enclave_mod._terraform_root_module()
        assert result.name == "enclave", f"expected 'enclave', got {result}"

    def test_root_module_picks_enclave_prod_when_composition_is_prod(
        self, monkeypatch
    ) -> None:
        monkeypatch.delenv("TABULA_TERRAFORM_ROOT", raising=False)
        result = enclave_mod._terraform_root_module(composition="prod")
        assert result.name == "enclave-prod", f"expected 'enclave-prod', got {result}"

    def test_root_module_rejects_unknown_composition(self) -> None:
        with pytest.raises(ValueError, match="composition must be one of"):
            enclave_mod._terraform_root_module(composition="bogus")

    def test_env_override_applies_regardless_of_composition(
        self, monkeypatch, tmp_path
    ) -> None:
        monkeypatch.setenv("TABULA_TERRAFORM_ROOT", str(tmp_path))
        assert enclave_mod._terraform_root_module() == tmp_path.resolve()
        assert (
            enclave_mod._terraform_root_module(composition="prod")
            == tmp_path.resolve()
        )

    def test_prod_dry_run_requires_adc(
        self,
        runner: CliRunner,
        tabula_home: Path,
        monkeypatch,
        mock_tf,
    ) -> None:
        # Even with --dry-run, prod composition needs ADC because the plan
        # will hit GCP. Verify the CLI's ADC check fires before terraform.
        monkeypatch.setattr(enclave_mod, "_adc_present", lambda: False)
        result = runner.invoke(
            root_app,
            [
                "enclave", "up", "prod-test",
                "--project", "test-proj",
                "--region", "us-central1",
                "--dry-run",
                "--composition", "prod",
            ],
        )
        assert result.exit_code == enclave_mod.EXIT_AUTH
        assert "application-default credentials" in (result.stderr or result.stdout)

    def test_stub_dry_run_skips_adc_check(
        self,
        runner: CliRunner,
        tabula_home: Path,
        monkeypatch,
        mock_tf,
    ) -> None:
        # The stub composition makes no GCP calls so the CLI should not
        # block on missing ADC during a dry-run.
        monkeypatch.setattr(enclave_mod, "_adc_present", lambda: False)
        result = runner.invoke(
            root_app,
            [
                "enclave", "up", "stub-test",
                "--project", "test-proj",
                "--region", "us-central1",
                "--dry-run",
                # default --composition stub
            ],
        )
        assert result.exit_code == enclave_mod.EXIT_OK, (
            f"expected EXIT_OK; got {result.exit_code}; "
            f"stdout={result.stdout!r} stderr={result.stderr!r}"
        )

    def test_prod_workdir_includes_modules_sibling(
        self, monkeypatch, tmp_path
    ) -> None:
        """Regression for #111: prod composition's main.tf references
        ../modules/<name>; _materialize_workdir must copy terraform/modules/
        next to the copied root or `terraform init` fails with 'Unable to
        evaluate directory symlink: lstat ../modules: no such file...'."""
        # Build a fake terraform layout: terraform/{enclave-prod,modules}.
        fake_root = tmp_path / "tf"
        prod_src = fake_root / "enclave-prod"
        modules_src = fake_root / "modules"
        prod_src.mkdir(parents=True)
        (prod_src / "main.tf").write_text("# fake prod composition\n")
        for mod in ("network", "iam", "firewall", "classifier", "gpu", "gitea", "vertex-psc"):
            d = modules_src / mod
            d.mkdir(parents=True)
            (d / "main.tf").write_text(f"# {mod}\n")
        # Point the CLI at this fake layout's prod root.
        monkeypatch.setenv("TABULA_TERRAFORM_ROOT", str(prod_src))
        # Isolate ~/.tabula.
        monkeypatch.setenv("TABULA_HOME", str(tmp_path / "home"))

        tf_dir = enclave_mod._materialize_workdir(
            "prodtest", project_id="p", region="us-central1", composition="prod",
        )
        workdir = tf_dir.parent
        modules_dst = workdir / "modules"

        # The composition root was copied.
        assert (tf_dir / "main.tf").is_file()
        # The sibling modules tree was copied next to it.
        assert modules_dst.is_dir(), (
            f"expected {modules_dst} to exist (modules sibling for "
            f"prod composition); workdir contains: {list(workdir.iterdir())}"
        )
        for mod in ("network", "iam", "firewall", "classifier", "gpu", "gitea", "vertex-psc"):
            assert (modules_dst / mod / "main.tf").is_file(), (
                f"expected modules/{mod}/main.tf in workdir"
            )

    def test_zone_default_derives_from_region(
        self, monkeypatch, tmp_path
    ) -> None:
        """When --zone is not passed, tfvars should get `<region>-a`."""
        from tabula_cli.enclave import up as up_mod
        # _write_tfvars writes the region-and-name + optional zone; verify
        # that with zone=None NO zone line is written, and with explicit
        # zone the line appears.
        path_no_zone = up_mod._write_tfvars(
            tmp_path / "no-zone", project_id="p", region="us-east1", name="x",
        )
        text_no_zone = path_no_zone.read_text()
        assert "zone" not in text_no_zone
        assert 'region       = "us-east1"' in text_no_zone

        path_with_zone = up_mod._write_tfvars(
            tmp_path / "with-zone",
            project_id="p", region="us-east1", name="x", zone="us-east1-c",
        )
        text_with_zone = path_with_zone.read_text()
        assert 'zone         = "us-east1-c"' in text_with_zone

    def test_zone_flag_threads_through_to_tfvars(
        self,
        runner: CliRunner,
        tabula_home: Path,
        monkeypatch,
        mock_tf,
        tmp_path,
    ) -> None:
        """--zone flag value lands in the materialized tfvars file."""
        result = runner.invoke(
            root_app,
            [
                "enclave", "up", "zonetest",
                "--project", "test-proj",
                "--region", "us-east1",
                "--zone", "us-east1-c",
                "--dry-run",
                # default --composition stub
            ],
        )
        assert result.exit_code == enclave_mod.EXIT_OK, (
            f"exit={result.exit_code} stdout={result.stdout!r} stderr={result.stderr!r}"
        )
        # The tfvars file should exist with the zone line.
        tfvars_path = state_mod.enclave_dir("zonetest") / "terraform.tfvars"
        assert tfvars_path.exists()
        assert 'zone         = "us-east1-c"' in tfvars_path.read_text()

    def test_stub_workdir_does_not_create_modules_sibling(
        self, monkeypatch, tmp_path
    ) -> None:
        """The stub composition's modules live at ./stubs/<name>/ inside
        the copied root — there should NOT be a modules/ sibling created."""
        fake_root = tmp_path / "tf"
        stub_src = fake_root / "enclave"
        stub_src.mkdir(parents=True)
        (stub_src / "main.tf").write_text("# fake stub composition\n")
        # Note: no terraform/modules/ created — proving stub doesn't reach for it.
        monkeypatch.setenv("TABULA_TERRAFORM_ROOT", str(stub_src))
        monkeypatch.setenv("TABULA_HOME", str(tmp_path / "home"))

        tf_dir = enclave_mod._materialize_workdir(
            "stubtest", project_id="p", region="us-central1", composition="stub",
        )
        workdir = tf_dir.parent
        assert not (workdir / "modules").exists()

    def test_gpu_type_default_omits_tfvars_lines(self, tmp_path) -> None:
        """When --gpu-type / --machine-type are not passed, the tfvars file
        must NOT contain `gpu_accelerator_type` or `gpu_machine_type` lines.

        Back-compat invariant: the prod composition's own defaults
        (`nvidia-tesla-t4` + `n1-standard-4`) stay authoritative when the
        operator doesn't opt in. Adding tfvars lines with the defaults would
        be silent, but emitting them only on opt-in keeps the tfvars file a
        readable record of what was overridden vs. what stayed default.
        """
        from tabula_cli.enclave import up as up_mod
        path = up_mod._write_tfvars(
            tmp_path / "no-gpu-flags",
            project_id="p", region="us-east1", name="x", zone="us-east1-c",
        )
        text = path.read_text()
        assert "gpu_accelerator_type" not in text
        assert "gpu_machine_type" not in text
        # Sanity: the standard lines are still there.
        assert 'project_id   = "p"' in text
        assert 'zone         = "us-east1-c"' in text

    def test_gpu_flags_thread_through_to_tfvars(
        self,
        runner: CliRunner,
        tabula_home: Path,
        monkeypatch,
        mock_tf,
        tmp_path,
    ) -> None:
        """--gpu-type / --machine-type values land in the materialized tfvars.

        Mirrors `test_zone_flag_threads_through_to_tfvars`. Uses the stub
        composition because the prod composition would also need the real
        sibling modules + ADC; we're asserting the CLI->tfvars plumbing,
        not the terraform plan itself. The stub composition silently
        ignores extra vars so this is safe.
        """
        result = runner.invoke(
            root_app,
            [
                "enclave", "up", "gputest",
                "--project", "test-proj",
                "--region", "us-east1",
                "--zone", "us-east1-c",
                "--gpu-type", "nvidia-l4",
                "--machine-type", "g2-standard-4",
                "--dry-run",
            ],
        )
        assert result.exit_code == enclave_mod.EXIT_OK, (
            f"exit={result.exit_code} stdout={result.stdout!r} "
            f"stderr={result.stderr!r}"
        )
        tfvars_path = state_mod.enclave_dir("gputest") / "terraform.tfvars"
        assert tfvars_path.exists()
        text = tfvars_path.read_text()
        assert 'gpu_accelerator_type = "nvidia-l4"' in text
        assert 'gpu_machine_type     = "g2-standard-4"' in text


# --------------------------------------------------------------------------- #
# Happy path: dry-run                                                         #
# --------------------------------------------------------------------------- #

class TestDryRun:
    def test_dry_run_calls_init_and_plan_only(
        self,
        runner: CliRunner,
        tabula_home: Path,
        mock_tf,
    ) -> None:
        result = runner.invoke(
            root_app,
            ["enclave", "up", "demo", "--project", "proj-x", "--dry-run"],
        )
        assert result.exit_code == enclave_mod.EXIT_OK, result.stderr
        mock_tf["init"].assert_called_once()
        mock_tf["plan"].assert_called_once()
        mock_tf["apply"].assert_not_called()

    def test_dry_run_does_not_write_state(
        self, runner: CliRunner, tabula_home: Path, mock_tf
    ) -> None:
        result = runner.invoke(
            root_app,
            ["enclave", "up", "demo", "--project", "p", "--dry-run"],
        )
        assert result.exit_code == enclave_mod.EXIT_OK
        assert not state_mod.state_exists("demo")

    def test_dry_run_skips_adc_check(
        self,
        runner: CliRunner,
        tabula_home: Path,
        mock_tf,
        monkeypatch,
    ) -> None:
        # Even with ADC missing, dry-run must succeed.
        monkeypatch.setattr(enclave_mod, "_adc_present", lambda: False)
        result = runner.invoke(
            root_app,
            ["enclave", "up", "demo", "--project", "p", "--dry-run"],
        )
        assert result.exit_code == enclave_mod.EXIT_OK


# --------------------------------------------------------------------------- #
# Happy path: full apply                                                      #
# --------------------------------------------------------------------------- #

class TestFullApply:
    def test_apply_writes_state_with_correct_shape(
        self, runner: CliRunner, tabula_home: Path, mock_tf
    ) -> None:
        result = runner.invoke(
            root_app,
            ["enclave", "up", "demo", "--project", "proj-x", "--region", "us-east1"],
        )
        assert result.exit_code == enclave_mod.EXIT_OK, result.stderr
        mock_tf["apply"].assert_called_once()

        s = state_mod.read_state("demo")
        assert s.name == "demo"
        assert s.project_id == "proj-x"
        assert s.region == "us-east1"
        assert s.outputs["classifier_ip"] == "203.0.113.10"
        assert s.outputs["noise_port"] == 51820
        assert s.version == state_mod.CURRENT_SCHEMA_VERSION
        # created_at is an ISO-8601 UTC string.
        assert s.created_at.endswith("Z")

    def test_success_summary_printed(
        self, runner: CliRunner, tabula_home: Path, mock_tf
    ) -> None:
        result = runner.invoke(
            root_app,
            ["enclave", "up", "demo", "--project", "p"],
        )
        assert result.exit_code == enclave_mod.EXIT_OK
        out = result.stdout
        assert "Enclave: demo" in out
        assert "classifier IP" in out
        assert "Noise port" in out
        assert "tabula enclave status demo" in out


# --------------------------------------------------------------------------- #
# Idempotency: re-apply confirmation                                          #
# --------------------------------------------------------------------------- #

class TestIdempotent:
    def _seed_existing(self, tabula_home: Path) -> None:
        state_mod.write_state(
            state_mod.EnclaveState(
                name="demo",
                project_id="orig-proj",
                region="us-central1",
                created_at="2026-01-01T00:00:00Z",
                terraform_dir=str(tabula_home / "home" / "enclaves" / "demo" / "terraform"),
                outputs={"classifier_ip": "203.0.113.10", "noise_port": 51820},
            )
        )

    def test_reapply_with_yes_skips_prompt(
        self, runner: CliRunner, tabula_home: Path, mock_tf
    ) -> None:
        self._seed_existing(tabula_home)
        result = runner.invoke(
            root_app,
            ["enclave", "up", "demo", "--project", "p", "--yes"],
        )
        assert result.exit_code == enclave_mod.EXIT_OK
        mock_tf["apply"].assert_called_once()

    def test_reapply_without_yes_aborts_when_user_declines(
        self, runner: CliRunner, tabula_home: Path, mock_tf
    ) -> None:
        self._seed_existing(tabula_home)
        # CliRunner sends "n\n" to the confirm prompt.
        result = runner.invoke(
            root_app,
            ["enclave", "up", "demo", "--project", "p"],
            input="n\n",
        )
        assert result.exit_code == enclave_mod.EXIT_OK
        mock_tf["apply"].assert_not_called()
        assert "aborted" in result.stdout.lower()

    def test_reapply_preserves_created_at(
        self, runner: CliRunner, tabula_home: Path, mock_tf
    ) -> None:
        self._seed_existing(tabula_home)
        original = state_mod.read_state("demo").created_at

        result = runner.invoke(
            root_app,
            ["enclave", "up", "demo", "--project", "p", "--yes"],
        )
        assert result.exit_code == enclave_mod.EXIT_OK
        s = state_mod.read_state("demo")
        assert s.created_at == original


# --------------------------------------------------------------------------- #
# Failure paths                                                               #
# --------------------------------------------------------------------------- #

class TestFailureModes:
    def test_missing_adc_exits_4(
        self,
        runner: CliRunner,
        tabula_home: Path,
        mock_tf,
        monkeypatch,
    ) -> None:
        monkeypatch.setattr(enclave_mod, "_adc_present", lambda: False)
        result = runner.invoke(
            root_app,
            ["enclave", "up", "demo", "--project", "p"],
        )
        assert result.exit_code == enclave_mod.EXIT_AUTH
        assert "application-default credentials" in (result.stderr or "")

    def test_terraform_apply_failure_exits_3_and_preserves_state(
        self,
        runner: CliRunner,
        tabula_home: Path,
        monkeypatch,
    ) -> None:
        # init succeeds, apply fails.
        monkeypatch.setattr(
            tf_mod,
            "init",
            mock.MagicMock(return_value=tf_mod.TerraformResult(0, "", "")),
        )
        monkeypatch.setattr(
            tf_mod,
            "apply",
            mock.MagicMock(
                side_effect=tf_mod.TerraformError(1, "", "boom: quota exceeded")
            ),
        )
        result = runner.invoke(
            root_app,
            ["enclave", "up", "demo", "--project", "p"],
        )
        assert result.exit_code == enclave_mod.EXIT_TERRAFORM
        assert "terraform apply failed" in (result.stderr or "")
        # No state.json was written (this is a first-time apply that failed).
        assert not state_mod.state_exists("demo")

    def test_terraform_init_failure_exits_3(
        self,
        runner: CliRunner,
        tabula_home: Path,
        monkeypatch,
    ) -> None:
        monkeypatch.setattr(
            tf_mod,
            "init",
            mock.MagicMock(
                side_effect=tf_mod.TerraformError(1, "", "init borked")
            ),
        )
        result = runner.invoke(
            root_app,
            ["enclave", "up", "demo", "--project", "p", "--dry-run"],
        )
        assert result.exit_code == enclave_mod.EXIT_TERRAFORM

    def test_terraform_missing_exits_2(
        self,
        runner: CliRunner,
        tabula_home: Path,
        monkeypatch,
    ) -> None:
        monkeypatch.setattr(
            tf_mod,
            "init",
            mock.MagicMock(side_effect=tf_mod.TerraformNotFoundError("nope")),
        )
        result = runner.invoke(
            root_app,
            ["enclave", "up", "demo", "--project", "p", "--dry-run"],
        )
        assert result.exit_code == enclave_mod.EXIT_VALIDATION


# --------------------------------------------------------------------------- #
# Status is now implemented (#30); confirm it is mounted and surfaces a       #
# clean user-error exit when state.json is missing rather than the old        #
# "not implemented yet" placeholder.                                          #
# --------------------------------------------------------------------------- #

class TestStatusMounted:
    def test_status_is_a_real_command(self, runner: CliRunner) -> None:
        # No state.json on disk for "missing-enclave-xyz" -> exit 2
        # (user error per #30 exit-code taxonomy).
        result = runner.invoke(
            root_app, ["enclave", "status", "missing-enclave-xyz"]
        )
        assert result.exit_code == 2
        # The error message points the operator at `tabula enclave up`.
        assert "tabula enclave up" in (result.stderr or "")
