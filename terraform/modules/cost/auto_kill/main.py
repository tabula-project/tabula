"""Tabula cost auto-kill Cloud Function.

Triggered by Pub/Sub messages from a google_billing_budget. On budget threshold
breach (>= KILL_THRESHOLD_PERCENT of monthly budget) it:

  1. Lists all GCE instances in PROJECT_ID labeled
     {WORKLOAD_LABEL_KEY: WORKLOAD_LABEL_VALUE}.
  2. Stops each running instance (idempotent — skips ones already stopped).
  3. Writes/updates the cost_killed marker at
     gs://STATE_BUCKET/enclaves/<ENCLAVE_NAME>/state.json.

This function is a SAFETY NET. It MUST NOT delete instances, disks, or any
infrastructure. Destroy is gated behind `tabula enclave down`, which is
responsible for backing up state and disks before teardown.

Idempotency:
  Pub/Sub delivers at-least-once. The function is safe to invoke repeatedly:
    - Stopping an already-stopped (or stopping/terminated) VM is a no-op.
    - State marker writes are upserts — last writer wins, but the message is
      strictly additive (kill timestamp + reason).

Environment variables (set by Terraform):
  ENCLAVE_NAME           — logical enclave name
  PROJECT_ID             — GCP project ID owning the workload VMs
  WORKLOAD_LABEL_KEY     — GCE label key identifying enclave workload VMs
  WORKLOAD_LABEL_VALUE   — GCE label value identifying enclave workload VMs
  AUTO_KILL_ENABLED      — "true"/"false" master switch (default true)
  STATE_BUCKET           — GCS bucket for the state.json marker
  KILL_THRESHOLD_PERCENT — minimum cost % at which to actually stop VMs (default 100)
"""

from __future__ import annotations

import base64
import json
import logging
import os
from datetime import datetime, timezone
from typing import Any

import functions_framework

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


# States in which a VM is already considered "not running" — stopping is a no-op.
_NON_RUNNING_STATUSES = {
    "STOPPING",
    "STOPPED",
    "SUSPENDING",
    "SUSPENDED",
    "TERMINATED",
}


def _env(name: str, default: str | None = None) -> str:
    value = os.environ.get(name, default)
    if value is None:
        raise RuntimeError(f"required env var {name} is not set")
    return value


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _decode_pubsub_event(cloud_event: Any) -> dict[str, Any]:
    """Decode the Pub/Sub envelope.

    cloud_event.data is the CloudEvent payload:
        {"message": {"data": "<base64 JSON>", "attributes": {...}, ...}, ...}

    The 'data' field is the base64-encoded JSON budget notification:
        {
          "budgetDisplayName": "...",
          "alertThresholdExceeded": 1.0,        # fraction, may be missing for 0%
          "costAmount": 50.5,
          "costIntervalStart": "2026-05-01T00:00:00Z",
          "budgetAmount": 50.0,
          "budgetAmountType": "SPECIFIED_AMOUNT",
          "currencyCode": "USD"
        }

    We tolerate either CloudEvent attribute (`.data`) or a raw dict.
    """
    payload = cloud_event.data if hasattr(cloud_event, "data") else cloud_event
    if not isinstance(payload, dict):
        raise ValueError(f"unexpected cloud_event payload type: {type(payload).__name__}")

    message = payload.get("message", {})
    encoded = message.get("data")
    if not encoded:
        # Some test harnesses pass the inner JSON directly; fall back gracefully.
        if "alertThresholdExceeded" in payload or "costAmount" in payload:
            return payload
        raise ValueError("Pub/Sub message has no 'data' field")

    decoded = base64.b64decode(encoded).decode("utf-8")
    return json.loads(decoded)


def _threshold_fraction(notification: dict[str, Any]) -> float:
    """Return the spend ratio (cost / budget) implied by the notification.

    Prefer the explicit alertThresholdExceeded field (this is what Cloud
    Billing sends — a fraction like 0.5, 0.9, 1.0). If absent (e.g. an early
    "current spend" ping), compute it from costAmount / budgetAmount.
    Returns 0.0 when no determination is possible (defensive — we will not
    kill on a malformed message).
    """
    threshold = notification.get("alertThresholdExceeded")
    if isinstance(threshold, (int, float)) and threshold > 0:
        return float(threshold)

    cost = notification.get("costAmount")
    budget = notification.get("budgetAmount")
    if (
        isinstance(cost, (int, float))
        and isinstance(budget, (int, float))
        and budget > 0
    ):
        return float(cost) / float(budget)

    return 0.0


def _list_workload_instances(
    compute_client: Any,
    project_id: str,
    label_key: str,
    label_value: str,
) -> list[tuple[str, str, str]]:
    """Return [(zone, name, status), ...] for instances matching the workload label.

    Uses aggregatedList to enumerate across all zones in one call.
    """
    request = compute_client.aggregated_list(project=project_id)
    matched: list[tuple[str, str, str]] = []

    # aggregated_list returns an iterator of (scope, instances_scoped_list) pairs.
    for zone_scope, scoped in request:
        instances = getattr(scoped, "instances", None) or []
        for inst in instances:
            labels = dict(getattr(inst, "labels", {}) or {})
            if labels.get(label_key) != label_value:
                continue
            # zone_scope looks like "zones/us-central1-a"; strip the prefix.
            zone = zone_scope.split("/")[-1] if "/" in zone_scope else zone_scope
            matched.append((zone, inst.name, inst.status))
    return matched


def _stop_instance_idempotent(
    compute_client: Any,
    project_id: str,
    zone: str,
    name: str,
    status: str,
) -> str:
    """Stop a single instance unless it is already stopped/stopping.

    Returns one of: "stopped", "skipped", "error:<msg>".
    """
    if status in _NON_RUNNING_STATUSES:
        logger.info(
            "skip stop name=%s zone=%s status=%s reason=already_not_running",
            name,
            zone,
            status,
        )
        return "skipped"

    try:
        compute_client.stop(project=project_id, zone=zone, instance=name)
        logger.info("stop issued name=%s zone=%s prev_status=%s", name, zone, status)
        return "stopped"
    except Exception as exc:  # noqa: BLE001 — we want to log+continue, not crash the function
        logger.exception("stop failed name=%s zone=%s: %s", name, zone, exc)
        return f"error:{exc}"


def _write_kill_marker(
    storage_client: Any,
    bucket_name: str,
    enclave_name: str,
    notification: dict[str, Any],
    stopped: list[dict[str, str]],
    threshold_fraction_value: float,
) -> str:
    """Write enclaves/<enclave_name>/state.json with cost_killed=true."""
    bucket = storage_client.bucket(bucket_name)
    object_path = f"enclaves/{enclave_name}/state.json"
    blob = bucket.blob(object_path)

    marker = {
        "enclave": enclave_name,
        "cost_killed": True,
        "killed_at": datetime.now(timezone.utc).isoformat(),
        "threshold_fraction": threshold_fraction_value,
        "budget_amount": notification.get("budgetAmount"),
        "cost_amount": notification.get("costAmount"),
        "currency_code": notification.get("currencyCode"),
        "stopped_instances": stopped,
        "source": "tabula-cost-auto-kill",
    }
    blob.upload_from_string(
        json.dumps(marker, indent=2, sort_keys=True),
        content_type="application/json",
    )
    return object_path


def _build_compute_client() -> Any:
    """Return a google.cloud.compute_v1.InstancesClient. Imported lazily so unit
    tests can run without the SDK installed."""
    from google.cloud import compute_v1

    return compute_v1.InstancesClient()


def _build_storage_client() -> Any:
    """Return a google.cloud.storage.Client. Lazy-imported for the same reason."""
    from google.cloud import storage

    return storage.Client()


def handle_budget_event(
    cloud_event: Any,
    *,
    compute_client_factory: Any = _build_compute_client,
    storage_client_factory: Any = _build_storage_client,
) -> dict[str, Any]:
    """Cloud Functions v2 entry point.

    The trailing keyword args are seams for unit tests — production callers
    invoke with just the cloud_event positional argument.
    """
    enclave_name = _env("ENCLAVE_NAME")
    project_id = _env("PROJECT_ID")
    label_key = _env("WORKLOAD_LABEL_KEY", "enclave-workload")
    label_value = _env("WORKLOAD_LABEL_VALUE", "true")
    auto_kill_enabled = _env_bool("AUTO_KILL_ENABLED", True)
    state_bucket = _env("STATE_BUCKET")
    kill_threshold_percent = float(_env("KILL_THRESHOLD_PERCENT", "100"))
    kill_threshold = kill_threshold_percent / 100.0

    notification = _decode_pubsub_event(cloud_event)
    fraction = _threshold_fraction(notification)

    logger.info(
        "budget_event enclave=%s project=%s fraction=%.4f threshold=%.4f auto_kill=%s",
        enclave_name,
        project_id,
        fraction,
        kill_threshold,
        auto_kill_enabled,
    )

    if fraction < kill_threshold:
        # Below kill threshold — this is just an early-warning notification
        # (e.g. 50% or 90%). No action required.
        return {
            "action": "noop",
            "reason": "below_kill_threshold",
            "fraction": fraction,
            "threshold": kill_threshold,
        }

    if not auto_kill_enabled:
        logger.warning(
            "auto-kill disabled by config; budget at %.2f%% but VMs left running",
            fraction * 100,
        )
        return {
            "action": "noop",
            "reason": "auto_kill_disabled",
            "fraction": fraction,
        }

    compute_client = compute_client_factory()
    instances = _list_workload_instances(
        compute_client, project_id, label_key, label_value
    )
    logger.info("matched %d workload instances", len(instances))

    stopped: list[dict[str, str]] = []
    for zone, name, status in instances:
        result = _stop_instance_idempotent(
            compute_client, project_id, zone, name, status
        )
        stopped.append(
            {"name": name, "zone": zone, "prev_status": status, "result": result}
        )

    storage_client = storage_client_factory()
    object_path = _write_kill_marker(
        storage_client,
        state_bucket,
        enclave_name,
        notification,
        stopped,
        fraction,
    )

    return {
        "action": "killed",
        "fraction": fraction,
        "threshold": kill_threshold,
        "stopped_instances": stopped,
        "marker": f"gs://{state_bucket}/{object_path}",
    }


# Cloud Functions v2 expects the entry point to be decorated for CloudEvents.
# We wrap the inner function so unit tests can inject fakes via kwargs.
@functions_framework.cloud_event
def main(cloud_event: Any) -> dict[str, Any]:
    return handle_budget_event(cloud_event)
