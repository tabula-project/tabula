"""Unit tests for the wake-signal core routine.

Coverage targets (from the issue):

* VM-already-running   → ``test_already_running_returns_immediately``
* VM-just-stopped      → ``test_terminated_vm_is_started_and_polled_to_running``
* VM in transient state → ``test_stopping_then_terminated_then_running``
* timeout              → ``test_timeout_when_vm_never_reaches_running``
                         + ``test_timeout_when_health_endpoint_never_ready``
* GCE API error        → ``test_unrecoverable_api_error_returns_api_error_outcome``
* Permission denied    → ``test_permission_denied_translates_to_outcome``
* Idempotency          → ``test_concurrent_wakes_issue_only_one_start``
                         + ``test_already_running_does_not_call_start``
"""

from __future__ import annotations

import threading

from tabula_wake.wake_gpu import (
    WakeOutcome,
    WakeResult,
    _is_permission_denied,
    wake_gpu,
)

from .conftest import FakeComputeClient


def _always_healthy(_url: str) -> bool:
    return True


def _never_healthy(_url: str) -> bool:
    return False


# ---------------------------------------------------------------------------
# Already-running path (idempotency: no instances.start call)
# ---------------------------------------------------------------------------


def test_already_running_returns_immediately(gpu_config, clock):
    client = FakeComputeClient(["RUNNING"])

    result = wake_gpu(
        gpu_config,
        client=client,
        timeout_s=10.0,
        poll_interval_s=0.01,
        health_check=_always_healthy,
        now=clock.now,
        sleep=clock.sleep,
    )

    assert result.outcome is WakeOutcome.ALREADY_RUNNING
    assert client.start_calls == 0
    assert client.get_calls == 1
    assert result.ready_at is not None
    assert result.duration_ms >= 0


def test_already_running_does_not_call_start(gpu_config, clock):
    """Idempotency property: if VM is already up, never issue a start."""
    client = FakeComputeClient(["RUNNING"])
    wake_gpu(
        gpu_config,
        client=client,
        timeout_s=5.0,
        poll_interval_s=0.01,
        health_check=_always_healthy,
        now=clock.now,
        sleep=clock.sleep,
    )
    assert client.start_calls == 0


# ---------------------------------------------------------------------------
# Cold-start path (TERMINATED -> RUNNING)
# ---------------------------------------------------------------------------


def test_terminated_vm_is_started_and_polled_to_running(gpu_config, clock):
    client = FakeComputeClient(["TERMINATED"], post_start_status="RUNNING")

    result = wake_gpu(
        gpu_config,
        client=client,
        timeout_s=30.0,
        poll_interval_s=0.5,
        health_check=_always_healthy,
        now=clock.now,
        sleep=clock.sleep,
    )

    assert result.outcome is WakeOutcome.STARTED
    assert client.start_calls == 1
    assert result.started_at is not None
    assert result.ready_at is not None
    assert result.ready_at >= result.started_at


# ---------------------------------------------------------------------------
# Transient-state handling
# ---------------------------------------------------------------------------


def test_stopping_then_terminated_then_running(gpu_config, clock):
    """If the VM is mid-shutdown, we wait it out, *then* start."""
    client = FakeComputeClient(
        ["STOPPING", "TERMINATED", "RUNNING"],
        post_start_status="RUNNING",
    )

    result = wake_gpu(
        gpu_config,
        client=client,
        timeout_s=30.0,
        poll_interval_s=0.1,
        health_check=_always_healthy,
        now=clock.now,
        sleep=clock.sleep,
    )

    assert result.outcome is WakeOutcome.STARTED
    assert client.start_calls == 1


def test_unknown_status_treated_as_transient(gpu_config, clock):
    """Forward-compatibility: a status the helper has never heard of must
    not crash — wait and re-poll, and time out only via the global deadline."""
    client = FakeComputeClient(["NEW_GCE_STATE", "RUNNING"])

    result = wake_gpu(
        gpu_config,
        client=client,
        timeout_s=10.0,
        poll_interval_s=0.05,
        health_check=_always_healthy,
        now=clock.now,
        sleep=clock.sleep,
    )

    assert result.outcome is WakeOutcome.ALREADY_RUNNING
    # No start issued — VM transitioned to RUNNING on its own from the
    # unknown state. The wake helper must not assume the VM is off.
    assert client.start_calls == 0


# ---------------------------------------------------------------------------
# Timeout paths
# ---------------------------------------------------------------------------


def test_timeout_when_vm_never_reaches_running(gpu_config, clock):
    # The fake will keep returning STOPPING forever.
    client = FakeComputeClient(["STOPPING"])

    result = wake_gpu(
        gpu_config,
        client=client,
        timeout_s=2.0,
        poll_interval_s=0.5,
        health_check=_always_healthy,
        now=clock.now,
        sleep=clock.sleep,
    )

    assert result.outcome is WakeOutcome.TIMEOUT
    # Never made it to start (still in STOPPING — wake helper does not call
    # start on a stopping VM; it waits for terminal state first).
    assert client.start_calls == 0


def test_timeout_when_health_endpoint_never_ready(gpu_config, clock):
    client = FakeComputeClient(["RUNNING"])

    result = wake_gpu(
        gpu_config,
        client=client,
        timeout_s=2.0,
        poll_interval_s=0.5,
        health_check=_never_healthy,
        now=clock.now,
        sleep=clock.sleep,
    )

    assert result.outcome is WakeOutcome.TIMEOUT
    # The detail field must mention the health endpoint so operators
    # debugging a stuck wake know where to look.
    assert "health" in result.detail.lower()


# ---------------------------------------------------------------------------
# Error paths
# ---------------------------------------------------------------------------


class _PermissionDeniedFake(Exception):
    """Exception class whose name maps onto google.api_core's PermissionDenied
    via name-based detection. Lets us avoid the import in tests."""


# Make our fake match the duck-type contract of _is_permission_denied.
_PermissionDeniedFake.__name__ = "PermissionDenied"


def test_permission_denied_translates_to_outcome(gpu_config, clock):
    """If the IAM contract has drifted (no compute.instances.start), the
    wake routine must fail clearly and never time out."""
    client = FakeComputeClient(
        ["TERMINATED"],
        start_exception=_PermissionDeniedFake("403: permission denied"),
    )

    result = wake_gpu(
        gpu_config,
        client=client,
        timeout_s=10.0,
        poll_interval_s=0.1,
        health_check=_always_healthy,
        now=clock.now,
        sleep=clock.sleep,
    )

    assert result.outcome is WakeOutcome.PERMISSION_DENIED
    # Should fail fast — never burn the full timeout budget.
    assert result.duration_ms < 5_000
    assert "compute.instances.start" in result.detail


def test_unrecoverable_api_error_returns_api_error_outcome(gpu_config, clock):
    client = FakeComputeClient(
        ["TERMINATED"],
        start_exception=RuntimeError("backend exploded"),
    )

    result = wake_gpu(
        gpu_config,
        client=client,
        timeout_s=10.0,
        poll_interval_s=0.1,
        health_check=_always_healthy,
        now=clock.now,
        sleep=clock.sleep,
    )

    assert result.outcome is WakeOutcome.API_ERROR
    assert "RuntimeError" in result.detail


def test_get_permission_denied_propagates(gpu_config, clock):
    """Lacking compute.instances.get is just as fatal as lacking start."""
    client = FakeComputeClient(
        ["RUNNING"],
        get_exception=_PermissionDeniedFake("403: get denied"),
    )

    result = wake_gpu(
        gpu_config,
        client=client,
        timeout_s=5.0,
        poll_interval_s=0.1,
        health_check=_always_healthy,
        now=clock.now,
        sleep=clock.sleep,
    )

    assert result.outcome is WakeOutcome.PERMISSION_DENIED


# ---------------------------------------------------------------------------
# IAM permission detection helper
# ---------------------------------------------------------------------------


def test_is_permission_denied_recognizes_named_exceptions():
    assert _is_permission_denied(_PermissionDeniedFake("x"))


def test_is_permission_denied_recognizes_403_code():
    class _Err(Exception):
        code = 403

    assert _is_permission_denied(_Err("nope"))


def test_is_permission_denied_ignores_generic_errors():
    assert not _is_permission_denied(RuntimeError("x"))
    assert not _is_permission_denied(ValueError("x"))


# ---------------------------------------------------------------------------
# Idempotency under concurrency
# ---------------------------------------------------------------------------


def test_concurrent_wakes_issue_only_one_start(gpu_config):
    """Five concurrent /wake calls must result in exactly one
    ``instances.start`` API call."""
    # Real time here so Python's threading scheduler can interleave.
    client = FakeComputeClient(["TERMINATED"], post_start_status="RUNNING")

    results: list[WakeResult] = []
    barrier = threading.Barrier(5)

    def fire() -> None:
        barrier.wait()
        results.append(
            wake_gpu(
                gpu_config,
                client=client,
                timeout_s=10.0,
                poll_interval_s=0.01,
                health_check=_always_healthy,
            )
        )

    threads = [threading.Thread(target=fire) for _ in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert client.start_calls == 1
    # Exactly one thread observes ``STARTED``; the other four see
    # ``ALREADY_RUNNING`` because the VM is up by the time they take the
    # lock. Both are success outcomes.
    outcomes = [r.outcome for r in results]
    assert sum(o is WakeOutcome.STARTED for o in outcomes) == 1
    assert sum(o is WakeOutcome.ALREADY_RUNNING for o in outcomes) == 4
