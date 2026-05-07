"""Tests for the internal-only ``POST /wake`` HTTP server."""

from __future__ import annotations

import pytest

flask = pytest.importorskip("flask")

from tabula_wake.server import create_app  # noqa: E402

from .conftest import FakeComputeClient  # noqa: E402


@pytest.fixture
def app(gpu_config, monkeypatch):
    # Force the server's health check path through a deterministic stub.
    import importlib

    wg_mod = importlib.import_module("tabula_wake.wake_gpu")
    monkeypatch.setattr(wg_mod, "_default_health_check", lambda url: True)
    return create_app(
        config=gpu_config,
        client=FakeComputeClient(["RUNNING"]),
        timeout_s=5.0,
    )


def test_post_wake_already_running_returns_200(app):
    resp = app.test_client().post("/wake")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["outcome"] == "already_running"
    assert body["instance_name"] == "tabula-gpu"
    assert body["duration_ms"] >= 0


def test_post_wake_started_returns_200(gpu_config, monkeypatch):
    import importlib

    wg_mod = importlib.import_module("tabula_wake.wake_gpu")
    monkeypatch.setattr(wg_mod, "_default_health_check", lambda url: True)
    client = FakeComputeClient(["TERMINATED"], post_start_status="RUNNING")
    app = create_app(config=gpu_config, client=client, timeout_s=5.0)

    resp = app.test_client().post("/wake")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["outcome"] == "started"
    assert client.start_calls == 1


def test_post_wake_timeout_returns_504(gpu_config, monkeypatch):
    from tabula_wake import wake_gpu as wg

    monkeypatch.setattr(wg, "_default_health_check", lambda url: False)
    client = FakeComputeClient(["RUNNING"])  # health endpoint never green
    app = create_app(config=gpu_config, client=client, timeout_s=0.2)

    resp = app.test_client().post("/wake?timeout=0.2")
    assert resp.status_code == 504
    body = resp.get_json()
    assert body["outcome"] == "timeout"


def test_post_wake_permission_denied_returns_403(gpu_config, monkeypatch):
    import importlib

    wg_mod = importlib.import_module("tabula_wake.wake_gpu")
    monkeypatch.setattr(wg_mod, "_default_health_check", lambda url: True)

    class PermissionDenied(Exception):
        pass

    PermissionDenied.__name__ = "PermissionDenied"

    client = FakeComputeClient(
        ["TERMINATED"],
        start_exception=PermissionDenied("403"),
    )
    app = create_app(config=gpu_config, client=client, timeout_s=5.0)
    resp = app.test_client().post("/wake")
    assert resp.status_code == 403
    body = resp.get_json()
    assert body["outcome"] == "permission_denied"


def test_invalid_timeout_query_param(gpu_config, monkeypatch):
    import importlib

    wg_mod = importlib.import_module("tabula_wake.wake_gpu")
    monkeypatch.setattr(wg_mod, "_default_health_check", lambda url: True)
    app = create_app(
        config=gpu_config,
        client=FakeComputeClient(["RUNNING"]),
        timeout_s=5.0,
    )
    resp = app.test_client().post("/wake?timeout=abc")
    assert resp.status_code == 400


def test_healthz_endpoint(app):
    resp = app.test_client().get("/healthz")
    assert resp.status_code == 200
    assert resp.get_json() == {"ok": True}
