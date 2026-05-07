"""Unit tests for the cost auto-kill Cloud Function.

These tests exercise the core stop logic with mocked Compute SDK and Pub/Sub
messages. They run without GCP credentials and without google-cloud-compute /
google-cloud-storage installed — `main.py` lazy-imports those.

Run with:
    pip install pytest functions-framework
    pytest -q
"""

from __future__ import annotations

import base64
import importlib
import json
import sys
import types
from typing import Any
from unittest.mock import MagicMock

import pytest


# ---------------------------------------------------------------------------
# Test fixtures: stub functions_framework if it isn't installed, so importing
# main.py doesn't blow up in environments that only have pytest.
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _stub_functions_framework(monkeypatch: pytest.MonkeyPatch) -> None:
    if "functions_framework" in sys.modules:
        return
    fake = types.ModuleType("functions_framework")

    def cloud_event(fn):  # noqa: ANN001 — passthrough decorator
        return fn

    fake.cloud_event = cloud_event  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "functions_framework", fake)


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENCLAVE_NAME", "demo")
    monkeypatch.setenv("PROJECT_ID", "tabula-demo")
    monkeypatch.setenv("WORKLOAD_LABEL_KEY", "enclave-workload")
    monkeypatch.setenv("WORKLOAD_LABEL_VALUE", "true")
    monkeypatch.setenv("AUTO_KILL_ENABLED", "true")
    monkeypatch.setenv("STATE_BUCKET", "tabula-demo-cost-state")
    monkeypatch.setenv("KILL_THRESHOLD_PERCENT", "100")


@pytest.fixture
def main_module(env: None) -> Any:  # noqa: ARG001 — env triggers env setup
    # Force a fresh import so env vars are picked up cleanly per test if they
    # were tweaked.
    if "main" in sys.modules:
        del sys.modules["main"]
    return importlib.import_module("main")


# ---------------------------------------------------------------------------
# Helpers to build CloudEvent-shaped payloads
# ---------------------------------------------------------------------------


def _make_event(notification: dict[str, Any]) -> Any:
    encoded = base64.b64encode(json.dumps(notification).encode("utf-8")).decode("ascii")
    payload = {"message": {"data": encoded, "attributes": {}}}
    ev = MagicMock()
    ev.data = payload
    return ev


def _make_instance(name: str, zone_scope: str, status: str, labels: dict[str, str]):
    inst = MagicMock()
    inst.name = name
    inst.status = status
    inst.labels = labels
    return inst


class _ScopedList:
    """Stand-in for the InstancesScopedList returned in aggregated_list pairs."""

    def __init__(self, instances: list[Any]) -> None:
        self.instances = instances


class _FakeComputeClient:
    """Mocks google.cloud.compute_v1.InstancesClient."""

    def __init__(self, scopes: list[tuple[str, list[Any]]]):
        self._scopes = scopes
        self.stop_calls: list[dict[str, str]] = []
        self.stop_should_raise: dict[str, Exception] = {}

    def aggregated_list(self, *, project: str):  # noqa: ARG002 — mirrors SDK signature
        return iter([(scope, _ScopedList(insts)) for scope, insts in self._scopes])

    def stop(self, *, project: str, zone: str, instance: str):
        self.stop_calls.append({"project": project, "zone": zone, "instance": instance})
        if instance in self.stop_should_raise:
            raise self.stop_should_raise[instance]


class _FakeBlob:
    def __init__(self) -> None:
        self.content: str | None = None
        self.content_type: str | None = None

    def upload_from_string(self, data: str, *, content_type: str) -> None:
        self.content = data
        self.content_type = content_type


class _FakeBucket:
    def __init__(self) -> None:
        self.blobs: dict[str, _FakeBlob] = {}

    def blob(self, name: str) -> _FakeBlob:
        b = self.blobs.setdefault(name, _FakeBlob())
        return b


class _FakeStorageClient:
    def __init__(self) -> None:
        self.buckets: dict[str, _FakeBucket] = {}

    def bucket(self, name: str) -> _FakeBucket:
        return self.buckets.setdefault(name, _FakeBucket())


# ---------------------------------------------------------------------------
# Tests: threshold / decode helpers
# ---------------------------------------------------------------------------


def test_threshold_fraction_uses_alert_field(main_module: Any) -> None:
    assert main_module._threshold_fraction({"alertThresholdExceeded": 0.9}) == 0.9


def test_threshold_fraction_falls_back_to_cost_over_budget(main_module: Any) -> None:
    f = main_module._threshold_fraction({"costAmount": 75.0, "budgetAmount": 50.0})
    assert f == pytest.approx(1.5)


def test_threshold_fraction_missing_returns_zero(main_module: Any) -> None:
    assert main_module._threshold_fraction({}) == 0.0


def test_decode_pubsub_event_extracts_inner_json(main_module: Any) -> None:
    ev = _make_event({"alertThresholdExceeded": 1.0, "costAmount": 60.0})
    decoded = main_module._decode_pubsub_event(ev)
    assert decoded["alertThresholdExceeded"] == 1.0


def test_decode_pubsub_event_accepts_raw_dict(main_module: Any) -> None:
    """If a test harness passes the inner JSON directly, we still cope."""

    class _Direct:
        data = {"alertThresholdExceeded": 0.5, "costAmount": 25}

    decoded = main_module._decode_pubsub_event(_Direct())
    assert decoded["alertThresholdExceeded"] == 0.5


# ---------------------------------------------------------------------------
# Tests: end-to-end handle_budget_event behavior
# ---------------------------------------------------------------------------


def test_below_threshold_is_noop(main_module: Any) -> None:
    """50% notification — must not stop anything."""
    compute = _FakeComputeClient(
        [
            (
                "zones/us-central1-a",
                [_make_instance("vm1", "zones/us-central1-a", "RUNNING", {"enclave-workload": "true"})],
            ),
        ]
    )
    storage = _FakeStorageClient()

    result = main_module.handle_budget_event(
        _make_event({"alertThresholdExceeded": 0.5, "costAmount": 25, "budgetAmount": 50}),
        compute_client_factory=lambda: compute,
        storage_client_factory=lambda: storage,
    )

    assert result["action"] == "noop"
    assert result["reason"] == "below_kill_threshold"
    assert compute.stop_calls == []
    assert storage.buckets == {}


def test_at_threshold_stops_running_workload_vms(main_module: Any) -> None:
    """100% notification — must stop running, labeled VMs, and write marker."""
    compute = _FakeComputeClient(
        [
            (
                "zones/us-central1-a",
                [
                    _make_instance("gpu-1", "zones/us-central1-a", "RUNNING", {"enclave-workload": "true"}),
                    _make_instance("classifier-1", "zones/us-central1-a", "RUNNING", {"enclave-workload": "true"}),
                ],
            ),
            (
                "zones/us-central1-b",
                [_make_instance("gitea", "zones/us-central1-b", "RUNNING", {"enclave-workload": "true"})],
            ),
        ]
    )
    storage = _FakeStorageClient()

    result = main_module.handle_budget_event(
        _make_event(
            {
                "alertThresholdExceeded": 1.0,
                "costAmount": 60.0,
                "budgetAmount": 50.0,
                "currencyCode": "USD",
            }
        ),
        compute_client_factory=lambda: compute,
        storage_client_factory=lambda: storage,
    )

    assert result["action"] == "killed"
    assert {c["instance"] for c in compute.stop_calls} == {"gpu-1", "classifier-1", "gitea"}
    # Marker should have been written.
    bucket = storage.buckets["tabula-demo-cost-state"]
    blob = bucket.blobs["enclaves/demo/state.json"]
    payload = json.loads(blob.content)
    assert payload["cost_killed"] is True
    assert payload["enclave"] == "demo"
    assert payload["threshold_fraction"] == pytest.approx(1.0)
    # All three should appear in stopped_instances.
    assert len(payload["stopped_instances"]) == 3


def test_skips_unlabeled_instances(main_module: Any) -> None:
    """Critical scoping check — never touch VMs without the workload label."""
    compute = _FakeComputeClient(
        [
            (
                "zones/us-central1-a",
                [
                    _make_instance("workload", "zones/us-central1-a", "RUNNING", {"enclave-workload": "true"}),
                    _make_instance("ops-bastion", "zones/us-central1-a", "RUNNING", {"role": "ops"}),  # unlabeled
                    _make_instance("other", "zones/us-central1-a", "RUNNING", {"enclave-workload": "false"}),  # wrong value
                ],
            ),
        ]
    )
    storage = _FakeStorageClient()

    main_module.handle_budget_event(
        _make_event({"alertThresholdExceeded": 1.0}),
        compute_client_factory=lambda: compute,
        storage_client_factory=lambda: storage,
    )

    assert [c["instance"] for c in compute.stop_calls] == ["workload"]


def test_idempotent_skips_already_stopped(main_module: Any) -> None:
    """Pub/Sub at-least-once: a second invocation finds STOPPED VMs and no-ops."""
    compute = _FakeComputeClient(
        [
            (
                "zones/us-central1-a",
                [
                    _make_instance("gpu-1", "zones/us-central1-a", "TERMINATED", {"enclave-workload": "true"}),
                    _make_instance("gpu-2", "zones/us-central1-a", "STOPPING", {"enclave-workload": "true"}),
                    _make_instance("gpu-3", "zones/us-central1-a", "STOPPED", {"enclave-workload": "true"}),
                ],
            ),
        ]
    )
    storage = _FakeStorageClient()

    result = main_module.handle_budget_event(
        _make_event({"alertThresholdExceeded": 1.0}),
        compute_client_factory=lambda: compute,
        storage_client_factory=lambda: storage,
    )

    # No actual stop calls were issued.
    assert compute.stop_calls == []
    # But the marker was still upserted (last writer wins).
    blob = storage.buckets["tabula-demo-cost-state"].blobs["enclaves/demo/state.json"]
    payload = json.loads(blob.content)
    assert payload["cost_killed"] is True
    # All three should be marked as skipped.
    assert all(s["result"] == "skipped" for s in payload["stopped_instances"])
    assert result["action"] == "killed"


def test_auto_kill_disabled_master_switch(main_module: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUTO_KILL_ENABLED", "false")
    # Re-import so the new env is read on next call (handler reads env each call,
    # so re-import isn't strictly necessary, but be safe).
    importlib.reload(main_module)

    compute = _FakeComputeClient(
        [
            (
                "zones/us-central1-a",
                [_make_instance("gpu", "zones/us-central1-a", "RUNNING", {"enclave-workload": "true"})],
            ),
        ]
    )
    storage = _FakeStorageClient()

    result = main_module.handle_budget_event(
        _make_event({"alertThresholdExceeded": 1.0}),
        compute_client_factory=lambda: compute,
        storage_client_factory=lambda: storage,
    )

    assert result["action"] == "noop"
    assert result["reason"] == "auto_kill_disabled"
    assert compute.stop_calls == []
    assert storage.buckets == {}


def test_partial_failure_continues_and_records(main_module: Any) -> None:
    """If one stop call raises, we keep stopping the rest and log the error in
    the marker."""
    compute = _FakeComputeClient(
        [
            (
                "zones/us-central1-a",
                [
                    _make_instance("good", "zones/us-central1-a", "RUNNING", {"enclave-workload": "true"}),
                    _make_instance("flaky", "zones/us-central1-a", "RUNNING", {"enclave-workload": "true"}),
                    _make_instance("good2", "zones/us-central1-a", "RUNNING", {"enclave-workload": "true"}),
                ],
            ),
        ]
    )
    compute.stop_should_raise["flaky"] = RuntimeError("API quota exceeded")
    storage = _FakeStorageClient()

    result = main_module.handle_budget_event(
        _make_event({"alertThresholdExceeded": 1.0}),
        compute_client_factory=lambda: compute,
        storage_client_factory=lambda: storage,
    )

    # All three were attempted.
    assert {c["instance"] for c in compute.stop_calls} == {"good", "flaky", "good2"}
    by_name = {s["name"]: s for s in result["stopped_instances"]}
    assert by_name["good"]["result"] == "stopped"
    assert by_name["good2"]["result"] == "stopped"
    assert by_name["flaky"]["result"].startswith("error:")


def test_kill_threshold_percent_overrideable(
    main_module: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Operator can set kill threshold to 90 to stop earlier than 100%."""
    monkeypatch.setenv("KILL_THRESHOLD_PERCENT", "90")
    importlib.reload(main_module)

    compute = _FakeComputeClient(
        [
            (
                "zones/us-central1-a",
                [_make_instance("gpu", "zones/us-central1-a", "RUNNING", {"enclave-workload": "true"})],
            ),
        ]
    )
    storage = _FakeStorageClient()

    # 90% notification should now fire the kill, not just be a warning.
    result = main_module.handle_budget_event(
        _make_event({"alertThresholdExceeded": 0.9}),
        compute_client_factory=lambda: compute,
        storage_client_factory=lambda: storage,
    )
    assert result["action"] == "killed"
    assert len(compute.stop_calls) == 1


def test_marker_payload_shape(main_module: Any) -> None:
    """state.json schema is the contract that `tabula enclave status` reads —
    pin the field set so future refactors don't silently break #30."""
    compute = _FakeComputeClient(
        [
            (
                "zones/us-central1-a",
                [_make_instance("gpu", "zones/us-central1-a", "RUNNING", {"enclave-workload": "true"})],
            ),
        ]
    )
    storage = _FakeStorageClient()

    main_module.handle_budget_event(
        _make_event(
            {
                "alertThresholdExceeded": 1.0,
                "costAmount": 50.5,
                "budgetAmount": 50.0,
                "currencyCode": "USD",
            }
        ),
        compute_client_factory=lambda: compute,
        storage_client_factory=lambda: storage,
    )
    blob = storage.buckets["tabula-demo-cost-state"].blobs["enclaves/demo/state.json"]
    payload = json.loads(blob.content)

    expected_keys = {
        "enclave",
        "cost_killed",
        "killed_at",
        "threshold_fraction",
        "budget_amount",
        "cost_amount",
        "currency_code",
        "stopped_instances",
        "source",
    }
    assert expected_keys.issubset(payload.keys())
    assert payload["source"] == "tabula-cost-auto-kill"
