"""Unit tests for ``tabula enclave down``.

Tests cover the matrix called out in the issue acceptance:

* Happy teardown path (clean destroy + clean verify -> state dir removed).
* Mocked-leftover detection (verifier returns a leftover -> exit 3, state preserved).
* Idempotency (running ``down`` twice is fine).
* Confirmation prompt path.
* Missing state path (without ``--force`` is a user error).
* Terraform-failure path (state preserved).
* ``--force`` recovery path.

All tests inject fakes for the terraform runner, the GCP verifier, and the
confirmation prompt — no subprocess spawning, no GCP calls.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tabula_cli.state import EnclaveState, write_state
from tabula_cli.enclave import (
    EXIT_LEFTOVERS,
    EXIT_OK,
    EXIT_TERRAFORM_FAILURE,
    EXIT_USER_ERROR,
    DownOptions,
    Leftover,
    TerraformResult,
    enclave_down,
)


def _state(name: str = "demo", terraform_dir: Path | None = None) -> EnclaveState:
    return EnclaveState(
        name=name,
        project_id="tabula-demo-123",
        region="us-central1",
        created_at="2026-05-07T12:00:00Z",
        terraform_dir=str(terraform_dir or Path("/tmp/tabula/enclaves") / name / "terraform"),
        outputs={},
    )


def _seed_enclave(tmp_path: Path, name: str = "demo") -> Path:
    """Create a fake ``terraform_dir`` and write valid state.json."""
    tf = tmp_path / "enclaves_root" / name / "terraform"
    tf.mkdir(parents=True)
    write_state(_state(name=name, terraform_dir=tf), root=tmp_path / "enclaves_root")
    return tmp_path / "enclaves_root"


def _ok_terraform(_dir: Path) -> TerraformResult:
    return TerraformResult(returncode=0, stdout="Destroy complete!", stderr="")


def _fail_terraform(_dir: Path) -> TerraformResult:
    return TerraformResult(returncode=1, stdout="", stderr="Error: bucket in use")


def _no_leftovers(_project: str, _name: str) -> list[Leftover]:
    return []


def _one_leftover(_project: str, _name: str) -> list[Leftover]:
    return [Leftover(kind="instance", name="classifier-vm", location="us-central1-a")]


# --------------------------------------------------------------------------- #
# Happy path                                                                  #
# --------------------------------------------------------------------------- #


class TestHappyPath:
    def test_clean_destroy_and_verify_removes_state(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        root = _seed_enclave(tmp_path)
        opts = DownOptions(
            name="demo",
            yes=True,
            enclaves_root=root,
            terraform_runner=_ok_terraform,
            verifier=_no_leftovers,
        )
        rc = enclave_down(opts)
        assert rc == EXIT_OK
        # State directory removed.
        assert not (root / "demo").exists()
        out = capsys.readouterr().out
        assert "destroyed cleanly" in out

    def test_idempotent_second_call_is_user_error_without_force(
        self, tmp_path: Path
    ) -> None:
        # First call destroys; second call has no state to read. Without
        # --force this is a user error (refuse to act on unknown enclaves).
        root = _seed_enclave(tmp_path)
        opts = DownOptions(
            name="demo",
            yes=True,
            enclaves_root=root,
            terraform_runner=_ok_terraform,
            verifier=_no_leftovers,
        )
        assert enclave_down(opts) == EXIT_OK
        assert enclave_down(opts) == EXIT_USER_ERROR

    def test_idempotent_with_force_is_clean_noop(self, tmp_path: Path) -> None:
        # After a clean destroy, --force --project should query the (empty)
        # cloud and report success without touching anything.
        root = _seed_enclave(tmp_path)
        first = DownOptions(
            name="demo",
            yes=True,
            enclaves_root=root,
            terraform_runner=_ok_terraform,
            verifier=_no_leftovers,
        )
        assert enclave_down(first) == EXIT_OK
        recovery = DownOptions(
            name="demo",
            yes=True,
            force=True,
            project="tabula-demo-123",
            enclaves_root=root,
            terraform_runner=_ok_terraform,
            verifier=_no_leftovers,
        )
        assert enclave_down(recovery) == EXIT_OK


# --------------------------------------------------------------------------- #
# Leftover detection                                                          #
# --------------------------------------------------------------------------- #


class TestLeftoverDetection:
    def test_leftover_after_destroy_exits_3_and_preserves_state(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        root = _seed_enclave(tmp_path)
        opts = DownOptions(
            name="demo",
            yes=True,
            enclaves_root=root,
            terraform_runner=_ok_terraform,
            verifier=_one_leftover,
        )
        rc = enclave_down(opts)
        assert rc == EXIT_LEFTOVERS
        # State preserved so the user can investigate / retry.
        assert (root / "demo" / "state.json").exists()
        err = capsys.readouterr().err
        assert "leftover" in err.lower()
        assert "classifier-vm" in err

    def test_verifier_failure_exits_3_and_preserves_state(
        self, tmp_path: Path
    ) -> None:
        root = _seed_enclave(tmp_path)

        def _verifier_explodes(_p: str, _n: str) -> list[Leftover]:
            raise RuntimeError("ADC missing")

        opts = DownOptions(
            name="demo",
            yes=True,
            enclaves_root=root,
            terraform_runner=_ok_terraform,
            verifier=_verifier_explodes,
        )
        rc = enclave_down(opts)
        assert rc == EXIT_LEFTOVERS
        assert (root / "demo" / "state.json").exists()


# --------------------------------------------------------------------------- #
# Confirmation prompt                                                         #
# --------------------------------------------------------------------------- #


class TestConfirmation:
    def test_user_says_no_aborts_cleanly(self, tmp_path: Path) -> None:
        root = _seed_enclave(tmp_path)
        prompt_calls: list[str] = []

        def _no_prompt(msg: str) -> bool:
            prompt_calls.append(msg)
            return False

        # Aggressive runners + verifiers; if either is invoked the prompt
        # was bypassed, which would be a bug.
        def _explode_terraform(_d: Path) -> TerraformResult:
            raise AssertionError("terraform runner must not be called when user aborts")

        def _explode_verifier(_p: str, _n: str) -> list[Leftover]:
            raise AssertionError("verifier must not be called when user aborts")

        opts = DownOptions(
            name="demo",
            enclaves_root=root,
            terraform_runner=_explode_terraform,
            verifier=_explode_verifier,
            prompt=_no_prompt,
        )
        rc = enclave_down(opts)
        assert rc == EXIT_OK  # aborts are clean exits
        assert prompt_calls and "WARNING" in prompt_calls[0]
        # State preserved on abort.
        assert (root / "demo" / "state.json").exists()

    def test_yes_skips_prompt(self, tmp_path: Path) -> None:
        root = _seed_enclave(tmp_path)

        def _explode_prompt(_msg: str) -> bool:
            raise AssertionError("prompt must not be called when --yes is set")

        opts = DownOptions(
            name="demo",
            yes=True,
            enclaves_root=root,
            terraform_runner=_ok_terraform,
            verifier=_no_leftovers,
            prompt=_explode_prompt,
        )
        assert enclave_down(opts) == EXIT_OK


# --------------------------------------------------------------------------- #
# Missing / corrupt state                                                     #
# --------------------------------------------------------------------------- #


class TestMissingState:
    def test_missing_state_without_force_is_user_error(self, tmp_path: Path) -> None:
        root = tmp_path / "empty_root"
        root.mkdir()
        opts = DownOptions(
            name="demo",
            yes=True,
            enclaves_root=root,
        )
        assert enclave_down(opts) == EXIT_USER_ERROR

    def test_force_without_project_is_user_error(self, tmp_path: Path) -> None:
        root = tmp_path / "empty_root"
        root.mkdir()
        opts = DownOptions(
            name="demo",
            yes=True,
            force=True,
            enclaves_root=root,
        )
        assert enclave_down(opts) == EXIT_USER_ERROR

    def test_force_with_project_skips_terraform_and_verifies(
        self, tmp_path: Path
    ) -> None:
        root = tmp_path / "empty_root"
        root.mkdir()

        def _explode_terraform(_d: Path) -> TerraformResult:
            raise AssertionError("terraform must not run on --force without state")

        opts = DownOptions(
            name="demo",
            yes=True,
            force=True,
            project="tabula-demo-123",
            enclaves_root=root,
            terraform_runner=_explode_terraform,
            verifier=_no_leftovers,
        )
        assert enclave_down(opts) == EXIT_OK

    def test_force_finds_leftovers_and_fails(self, tmp_path: Path) -> None:
        root = tmp_path / "empty_root"
        root.mkdir()
        opts = DownOptions(
            name="demo",
            yes=True,
            force=True,
            project="tabula-demo-123",
            enclaves_root=root,
            verifier=_one_leftover,
        )
        assert enclave_down(opts) == EXIT_LEFTOVERS

    def test_corrupt_state_without_force_is_user_error(self, tmp_path: Path) -> None:
        root = tmp_path / "enclaves_root"
        d = root / "demo"
        d.mkdir(parents=True)
        (d / "state.json").write_text("not json")
        opts = DownOptions(name="demo", yes=True, enclaves_root=root)
        assert enclave_down(opts) == EXIT_USER_ERROR


# --------------------------------------------------------------------------- #
# Terraform failure                                                           #
# --------------------------------------------------------------------------- #


class TestTerraformFailure:
    def test_failure_exits_2_and_preserves_state(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        root = _seed_enclave(tmp_path)

        def _explode_verifier(_p: str, _n: str) -> list[Leftover]:
            raise AssertionError("verifier must not be called after terraform fails")

        opts = DownOptions(
            name="demo",
            yes=True,
            enclaves_root=root,
            terraform_runner=_fail_terraform,
            verifier=_explode_verifier,
        )
        rc = enclave_down(opts)
        assert rc == EXIT_TERRAFORM_FAILURE
        # State preserved so retry is possible.
        assert (root / "demo" / "state.json").exists()
        err = capsys.readouterr().err
        # Message reflects whichever binary actually ran (tofu preferred,
        # terraform fallback — see _terraform.find_binary).
        assert "destroy failed" in err
        assert ("tofu" in err) or ("terraform" in err)

    def test_missing_terraform_dir_is_user_error(self, tmp_path: Path) -> None:
        # state.json points at a terraform_dir that does not exist.
        root = tmp_path / "enclaves_root"
        write_state(
            _state(name="demo", terraform_dir=tmp_path / "nonexistent"),
            root=root,
        )
        opts = DownOptions(name="demo", yes=True, enclaves_root=root)
        assert enclave_down(opts) == EXIT_USER_ERROR


# --------------------------------------------------------------------------- #
# Name validation                                                             #
# --------------------------------------------------------------------------- #


class TestNameValidation:
    def test_bad_name_is_user_error(self, tmp_path: Path) -> None:
        opts = DownOptions(name="BAD_NAME", yes=True, enclaves_root=tmp_path)
        assert enclave_down(opts) == EXIT_USER_ERROR
