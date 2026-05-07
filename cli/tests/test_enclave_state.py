"""Unit tests for the shared state.json schema (version 1).

These tests pin the contract that ``up`` (#26) and ``down`` (#28) share.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tabula_cli.state import (
    STATE_SCHEMA_VERSION,
    EnclaveState,
    StateCorruptError,
    StateNotFoundError,
    StateVersionError,
    is_valid_name,
    read_state,
    remove_state_dir,
    state_path,
    write_state,
)


def _sample_state(name: str = "demo") -> EnclaveState:
    return EnclaveState(
        name=name,
        project_id="tabula-demo-123",
        region="us-central1",
        created_at="2026-05-07T12:00:00Z",
        terraform_dir=f"/tmp/tabula/enclaves/{name}/terraform",
        outputs={"classifier_ip": "1.2.3.4", "noise_port": 51820},
    )


class TestNameValidation:
    @pytest.mark.parametrize("name", ["demo", "ab1", "demo-1", "a1b2c3", "x" * 30])
    def test_valid_names(self, name: str) -> None:
        # 'x' * 30 is right at the upper bound; we expect that to pass.
        assert is_valid_name(name) is (3 <= len(name) <= 30)

    @pytest.mark.parametrize(
        "name",
        [
            "",
            "ab",  # too short
            "Demo",  # uppercase
            "1demo",  # leading digit
            "-demo",  # leading dash
            "demo-",  # trailing dash
            "demo_x",  # underscore
            "x" * 31,  # too long
        ],
    )
    def test_invalid_names(self, name: str) -> None:
        assert is_valid_name(name) is False


class TestRoundTrip:
    def test_write_then_read(self, tmp_path: Path) -> None:
        st = _sample_state()
        path = write_state(st, root=tmp_path)
        assert path == state_path("demo", root=tmp_path)
        assert path.exists()
        loaded = read_state("demo", root=tmp_path)
        assert loaded == st

    def test_writes_versioned_payload(self, tmp_path: Path) -> None:
        write_state(_sample_state(), root=tmp_path)
        raw = json.loads((tmp_path / "demo" / "state.json").read_text())
        assert raw["version"] == STATE_SCHEMA_VERSION
        # Ensure we wrote in stable order (version first for human readers).
        assert list(raw.keys())[0] == "version"


class TestReadFailures:
    def test_missing_file_raises_not_found(self, tmp_path: Path) -> None:
        with pytest.raises(StateNotFoundError):
            read_state("ghost", root=tmp_path)

    def test_unreadable_json_raises_corrupt(self, tmp_path: Path) -> None:
        d = tmp_path / "demo"
        d.mkdir()
        (d / "state.json").write_text("not json")
        with pytest.raises(StateCorruptError):
            read_state("demo", root=tmp_path)

    def test_wrong_version_raises_version_error(self, tmp_path: Path) -> None:
        d = tmp_path / "demo"
        d.mkdir()
        payload = {
            "version": 999,
            "name": "demo",
            "project_id": "p",
            "region": "r",
            "created_at": "2026-05-07T12:00:00Z",
            "terraform_dir": "/tmp/x",
        }
        (d / "state.json").write_text(json.dumps(payload))
        with pytest.raises(StateVersionError):
            read_state("demo", root=tmp_path)

    def test_missing_required_field_is_corrupt(self, tmp_path: Path) -> None:
        d = tmp_path / "demo"
        d.mkdir()
        payload = {"version": 1, "name": "demo"}  # missing project_id etc.
        (d / "state.json").write_text(json.dumps(payload))
        with pytest.raises(StateCorruptError):
            read_state("demo", root=tmp_path)

    def test_name_mismatch_is_corrupt(self, tmp_path: Path) -> None:
        d = tmp_path / "demo"
        d.mkdir()
        payload = {
            "version": 1,
            "name": "other",  # disagrees with directory name
            "project_id": "p",
            "region": "r",
            "created_at": "2026-05-07T12:00:00Z",
            "terraform_dir": "/tmp/x",
        }
        (d / "state.json").write_text(json.dumps(payload))
        with pytest.raises(StateCorruptError):
            read_state("demo", root=tmp_path)


class TestRemoveStateDir:
    def test_removes_recursively(self, tmp_path: Path) -> None:
        write_state(_sample_state(), root=tmp_path)
        # Drop a nested artefact to ensure recursive removal works.
        nested = tmp_path / "demo" / "terraform" / "state.tfstate"
        nested.parent.mkdir(parents=True, exist_ok=True)
        nested.write_text("{}")
        remove_state_dir("demo", root=tmp_path)
        assert not (tmp_path / "demo").exists()

    def test_absent_dir_is_noop(self, tmp_path: Path) -> None:
        # Must not raise; idempotent teardown depends on this.
        remove_state_dir("ghost", root=tmp_path)
