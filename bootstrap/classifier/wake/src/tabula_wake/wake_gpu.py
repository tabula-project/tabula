"""Core wake-signal routine for the Tabula classifier.

Design notes
------------

The wake routine is the sole consumer of the classifier service account's
narrowly scoped permission set:

    compute.instances.get   (poll instance state)
    compute.instances.start (wake the GPU)

Both permissions are granted by the ``${enclave}_gpu_waker`` custom IAM role
created in the IAM module (#15) and bound to the classifier SA with an IAM
condition that pins the grant to a single instance ID. The wake routine
*relies* on that scoping — a 403 here means the IAM module has drifted from
its contract, not a bug in this code.

The flow is intentionally linear:

1. Read the GPU instance state.
2. If already ``RUNNING``, jump straight to the agent health check.
3. If ``TERMINATED``, call ``instances.start``.
4. If in a transient state (``STOPPING``, ``PROVISIONING``, ``STAGING``,
   ``REPAIRING``, ``SUSPENDING``, ``SUSPENDED``), poll until it reaches a
   stable state, then loop.
5. Once ``RUNNING``, poll the agent's HTTP health endpoint until 200, with a
   total wall-clock budget governed by ``timeout_s``.

Idempotency is enforced by two mechanisms:

* An in-process :class:`threading.Lock` — prevents the same classifier process
  from issuing two ``instances.start`` calls in quick succession.
* The GCE API itself — calling ``instances.start`` on a VM that is already
  RUNNING is a no-op and returns success. The narrow IAM permission means we
  cannot call ``stop`` even if a bug tried to.

The combination is sufficient for the single-tenant enclave threat model.
Cross-process or cross-host concurrency is not a concern: the classifier is
a single VM with a single wake server.
"""

from __future__ import annotations

import logging
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable, Optional, Protocol

logger = logging.getLogger("tabula.wake")

# GCE instance lifecycle states. See:
#   https://cloud.google.com/compute/docs/instances/instance-life-cycle
_RUNNING = "RUNNING"
_TERMINATED = "TERMINATED"
_TRANSIENT_STATES = frozenset(
    {
        "PROVISIONING",
        "STAGING",
        "STOPPING",
        "SUSPENDING",
        "SUSPENDED",
        "REPAIRING",
    }
)


class WakeOutcome(str, Enum):
    """Categorizes the terminal outcome of a wake call.

    Distinct from "did the call succeed". A wake call that finds the VM
    already running and ready returns :attr:`ALREADY_RUNNING`; a wake call
    that started the VM returns :attr:`STARTED`. Both are success paths but
    the distinction matters for metrics and logging.
    """

    ALREADY_RUNNING = "already_running"
    STARTED = "started"
    TIMEOUT = "timeout"
    PERMISSION_DENIED = "permission_denied"
    API_ERROR = "api_error"


@dataclass(frozen=True)
class GpuInstanceConfig:
    """Static configuration for the wake target.

    Sourced from instance metadata (preferred) or a config file written by
    Terraform. Both paths are supported by :func:`tabula_wake.config.load`.
    """

    project_id: str
    zone: str
    instance_name: str
    health_url: str
    """The full HTTP URL the wake routine polls to confirm the agent is up.

    Defined by GPU bootstrap (#35); typically
    ``http://gpu.<enclave>.internal:<port>/healthz``. Stored as a string here
    so the wake routine doesn't need to know about the agent's port layout."""


@dataclass
class WakeResult:
    """Structured result from a wake call.

    All timestamps are seconds since the UNIX epoch (``time.time()``). The
    server logs these to Cloud Logging with the field names listed in the
    issue (``requested_at``, ``started_at``, ``ready_at``, ``duration_ms``)
    so the schema across the system is stable.
    """

    outcome: WakeOutcome
    requested_at: float
    started_at: Optional[float] = None
    ready_at: Optional[float] = None
    duration_ms: int = 0
    detail: str = ""

    def to_log_fields(self) -> dict[str, Any]:
        return {
            "outcome": self.outcome.value,
            "requested_at": self.requested_at,
            "started_at": self.started_at,
            "ready_at": self.ready_at,
            "duration_ms": self.duration_ms,
            "detail": self.detail,
        }


class WakeTimeoutError(RuntimeError):
    """Raised when the wake polling loop exceeds its configured budget."""


class _ComputeClient(Protocol):
    """The minimal slice of ``google.cloud.compute_v1.InstancesClient`` we
    use, broken out as a Protocol so unit tests can inject a fake without
    pulling in the SDK.

    The real ``InstancesClient`` is wider than this; we deliberately only
    accept the subset of the surface that maps 1:1 to the IAM permissions
    granted to the classifier SA.
    """

    def get(self, *, project: str, zone: str, instance: str) -> Any: ...
    def start(self, *, project: str, zone: str, instance: str) -> Any: ...


class _PermissionDenied(Exception):
    """Internal sentinel translated to ``WakeOutcome.PERMISSION_DENIED``."""


def _is_permission_denied(exc: BaseException) -> bool:
    """Best-effort detection of GCP 403 / permission errors without importing
    google.api_core at module top — keeps this module testable in isolation.

    google-cloud-compute raises ``google.api_core.exceptions.PermissionDenied``
    (which is a subclass of ``GoogleAPIError``). We test for that by name to
    avoid a hard import cycle in tests.
    """
    name = type(exc).__name__
    if name in {"PermissionDenied", "Forbidden"}:
        return True
    code = getattr(exc, "code", None)
    if callable(code):
        try:
            code = code()
        except Exception:  # pragma: no cover — defensive
            code = None
    if code in (403, "PERMISSION_DENIED"):
        return True
    return False


# Shared in-process lock. The wake server holds a single instance of this
# module so the lock is global to the classifier process, which is the
# coarsest-but-correct scope for "do not start the VM twice from this VM".
_WAKE_LOCK = threading.Lock()


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def wake_gpu(
    config: GpuInstanceConfig,
    *,
    client: _ComputeClient,
    timeout_s: float = 90.0,
    poll_interval_s: float = 2.0,
    health_check: Optional[Callable[[str], bool]] = None,
    now: Callable[[], float] = time.time,
    sleep: Callable[[float], None] = time.sleep,
) -> WakeResult:
    """Wake the configured GPU instance and wait until its agent is ready.

    Parameters
    ----------
    config:
        The GPU target. Read once per call; never cached across calls so that
        Terraform-driven config changes take effect on the next request.
    client:
        A GCE ``InstancesClient``-compatible object. In production this is
        ``google.cloud.compute_v1.InstancesClient()``; tests inject a fake.
    timeout_s:
        Total wall-clock budget for the wake → ready transition. The default
        of ``90s`` is the upper end of the documented cold-start budget
        (40-90s, see README). Caller can shorten or lengthen as needed.
    poll_interval_s:
        Sleep between GCE state polls and between health checks.
    health_check:
        Optional callable that receives the configured ``health_url`` and
        returns ``True`` when the agent is ready. Defaults to a small
        stdlib-only HTTP GET that treats any ``2xx`` response as ready.
    now, sleep:
        Injectable clock + sleep so tests can run deterministically.

    Returns
    -------
    WakeResult
        Always returned — even on timeout or permission-denied — so callers
        can log the outcome without having to translate exceptions. The
        terminal :class:`WakeOutcome` distinguishes success modes from
        failure modes.

    Notes
    -----
    The function is *idempotent under concurrent calls*: a process-wide lock
    serializes wake attempts, and the GCE ``instances.start`` API is itself
    a no-op against a RUNNING VM. Two near-simultaneous calls therefore both
    return success, and only one ``instances.start`` API request is made.
    """
    requested_at = now()
    deadline = requested_at + timeout_s
    health_check = health_check or _default_health_check

    # Serialize wake attempts within this process. We do *not* skip the work
    # if another caller already took the lock — both callers want a synchronous
    # "is the agent ready?" answer, and the polling loop below is cheap. The
    # lock just prevents concurrent ``instances.start`` issuance.
    with _WAKE_LOCK:
        try:
            started_at, outcome = _ensure_started(
                config=config,
                client=client,
                deadline=deadline,
                poll_interval_s=poll_interval_s,
                requested_at=requested_at,
                now=now,
                sleep=sleep,
            )
        except _PermissionDenied as exc:
            duration_ms = int((now() - requested_at) * 1000)
            logger.error(
                "wake_gpu permission_denied: %s",
                exc,
                extra={"tabula_wake": {"target": config.instance_name}},
            )
            return WakeResult(
                outcome=WakeOutcome.PERMISSION_DENIED,
                requested_at=requested_at,
                duration_ms=duration_ms,
                detail=str(exc),
            )
        except WakeTimeoutError as exc:
            duration_ms = int((now() - requested_at) * 1000)
            return WakeResult(
                outcome=WakeOutcome.TIMEOUT,
                requested_at=requested_at,
                duration_ms=duration_ms,
                detail=str(exc),
            )
        except Exception as exc:  # noqa: BLE001 — surface as API error
            duration_ms = int((now() - requested_at) * 1000)
            logger.exception("wake_gpu api_error")
            return WakeResult(
                outcome=WakeOutcome.API_ERROR,
                requested_at=requested_at,
                duration_ms=duration_ms,
                detail=f"{type(exc).__name__}: {exc}",
            )

        # Health-check loop: GCE says RUNNING, but the agent process inside
        # the VM may still be booting. The ``health_url`` (defined by GPU
        # bootstrap #35) is the single source of truth for "ready".
        ready_at = _wait_for_health(
            health_url=config.health_url,
            health_check=health_check,
            deadline=deadline,
            poll_interval_s=poll_interval_s,
            now=now,
            sleep=sleep,
        )
        if ready_at is None:
            duration_ms = int((now() - requested_at) * 1000)
            return WakeResult(
                outcome=WakeOutcome.TIMEOUT,
                requested_at=requested_at,
                started_at=started_at,
                duration_ms=duration_ms,
                detail="agent health endpoint did not return 2xx within timeout",
            )

        duration_ms = int((ready_at - requested_at) * 1000)
        return WakeResult(
            outcome=outcome,
            requested_at=requested_at,
            started_at=started_at,
            ready_at=ready_at,
            duration_ms=duration_ms,
        )


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _ensure_started(
    *,
    config: GpuInstanceConfig,
    client: _ComputeClient,
    deadline: float,
    poll_interval_s: float,
    requested_at: float,
    now: Callable[[], float],
    sleep: Callable[[float], None],
) -> tuple[Optional[float], WakeOutcome]:
    """Drive the GCE state machine to RUNNING.

    Returns a ``(started_at, outcome)`` tuple where ``started_at`` is the
    timestamp at which we observed the VM in RUNNING (or ``requested_at``
    if the VM was already RUNNING — in that case ``outcome`` is
    :attr:`WakeOutcome.ALREADY_RUNNING`).
    """
    issued_start = False
    while True:
        if now() > deadline:
            raise WakeTimeoutError(
                f"timed out waiting for {config.instance_name} to reach RUNNING"
            )

        try:
            instance = client.get(
                project=config.project_id,
                zone=config.zone,
                instance=config.instance_name,
            )
        except Exception as exc:  # noqa: BLE001
            if _is_permission_denied(exc):
                raise _PermissionDenied(
                    f"classifier SA lacks compute.instances.get on "
                    f"{config.instance_name}: {exc}"
                ) from exc
            raise

        status = getattr(instance, "status", None) or _dict_status(instance)
        logger.debug("gpu instance status: %s", status)

        if status == _RUNNING:
            outcome = (
                WakeOutcome.STARTED if issued_start else WakeOutcome.ALREADY_RUNNING
            )
            return now(), outcome

        if status == _TERMINATED and not issued_start:
            try:
                client.start(
                    project=config.project_id,
                    zone=config.zone,
                    instance=config.instance_name,
                )
            except Exception as exc:  # noqa: BLE001
                if _is_permission_denied(exc):
                    raise _PermissionDenied(
                        f"classifier SA lacks compute.instances.start on "
                        f"{config.instance_name}: {exc}"
                    ) from exc
                raise
            issued_start = True
            logger.info(
                "issued instances.start for %s (was TERMINATED)",
                config.instance_name,
            )

        if status in _TRANSIENT_STATES or status == _TERMINATED:
            # For TERMINATED-after-start and any transient state, wait and
            # re-poll. Crucially: do *not* issue another ``start`` once we've
            # already issued one — that's the in-process idempotency
            # guarantee. The deadline check at the top of the loop bounds the
            # total time spent here.
            sleep(poll_interval_s)
            continue

        # Unknown status. Treat as transient — the GCE state vocabulary may
        # add states in the future, and a wake helper should be conservative
        # rather than erroring on a status it doesn't recognize.
        logger.warning(
            "unknown gpu instance status %r; treating as transient", status
        )
        sleep(poll_interval_s)


def _dict_status(instance: Any) -> Optional[str]:
    """Some fakes (and some serialized SDK types) return a dict instead of
    an object with a ``.status`` attribute. Accept both shapes — there's no
    upside to being strict here."""
    if isinstance(instance, dict):
        return instance.get("status")
    return None


def _wait_for_health(
    *,
    health_url: str,
    health_check: Callable[[str], bool],
    deadline: float,
    poll_interval_s: float,
    now: Callable[[], float],
    sleep: Callable[[float], None],
) -> Optional[float]:
    """Poll the agent health endpoint until 2xx or deadline. Returns the
    timestamp of first 2xx, or ``None`` on timeout."""
    while True:
        if now() > deadline:
            return None
        try:
            if health_check(health_url):
                return now()
        except Exception as exc:  # noqa: BLE001
            # The health endpoint may be unreachable for the first few
            # seconds while the VM is finishing its boot; that's expected.
            # Log at debug level only to keep the steady-state logs clean.
            logger.debug("health check raised: %s", exc)
        sleep(poll_interval_s)


def _default_health_check(url: str) -> bool:
    """Stdlib-only health probe.

    Treat any 2xx response as healthy. We deliberately use a short connect
    timeout so a hung VM doesn't waste the wake budget on a single probe.
    """
    try:
        with urllib.request.urlopen(url, timeout=2.0) as resp:  # noqa: S310
            return 200 <= resp.status < 300
    except urllib.error.URLError:
        return False
    except Exception:  # noqa: BLE001
        return False
