"""Tests for the layered config loader (metadata server + file fallback)."""

from __future__ import annotations

import json

import pytest

from tabula_wake.config import load


def _no_metadata(_key: str) -> None:
    return None


def test_load_from_file_only(tmp_path):
    cfg_path = tmp_path / "wake.json"
    cfg_path.write_text(
        json.dumps(
            {
                "project_id": "tabula-test",
                "zone": "us-central1-a",
                "instance_name": "tabula-gpu",
                "health_url": "http://gpu.test.internal:8443/healthz",
            }
        )
    )

    cfg = load(config_path=cfg_path, metadata_fetch=_no_metadata)
    assert cfg.project_id == "tabula-test"
    assert cfg.instance_name == "tabula-gpu"
    assert cfg.health_url.endswith("/healthz")


def test_metadata_overrides_file(tmp_path):
    cfg_path = tmp_path / "wake.json"
    cfg_path.write_text(
        json.dumps(
            {
                "project_id": "from-file",
                "zone": "from-file",
                "instance_name": "from-file",
                "health_url": "from-file",
            }
        )
    )

    def from_metadata(key: str) -> str:
        return {
            "tabula-gpu-project-id": "from-meta",
            "tabula-gpu-instance-zone": "from-meta",
            "tabula-gpu-instance-name": "from-meta",
            "tabula-gpu-health-url": "http://from-meta",
        }[key]

    cfg = load(config_path=cfg_path, metadata_fetch=from_metadata)
    assert cfg.project_id == "from-meta"
    assert cfg.health_url == "http://from-meta"


def test_partial_metadata_falls_back_to_file_per_field(tmp_path):
    cfg_path = tmp_path / "wake.json"
    cfg_path.write_text(
        json.dumps(
            {
                "project_id": "from-file",
                "zone": "from-file",
                "instance_name": "from-file",
                "health_url": "from-file",
            }
        )
    )

    def partial(key: str):
        if key == "tabula-gpu-instance-name":
            return "from-meta"
        return None

    cfg = load(config_path=cfg_path, metadata_fetch=partial)
    assert cfg.instance_name == "from-meta"
    assert cfg.project_id == "from-file"


def test_missing_required_field_raises(tmp_path):
    cfg_path = tmp_path / "wake.json"
    cfg_path.write_text(
        json.dumps(
            {
                "project_id": "tabula-test",
                # zone missing
                "instance_name": "tabula-gpu",
                "health_url": "http://x",
            }
        )
    )

    with pytest.raises(RuntimeError) as exc:
        load(config_path=cfg_path, metadata_fetch=_no_metadata)
    assert "zone" in str(exc.value)
