"""Shared pytest fixtures for the wake-helper test suite."""

from __future__ import annotations

import sys
import threading
from pathlib import Path
from typing import Any, Optional

import pytest

# The wake package isn't installed in the test environment by default; we
# add its src/ directory to sys.path so `import tabula_wake` works without a
# pip install. This matches how the etl/ and l3/ subpackages run their tests.
_HERE = Path(__file__).resolve().parent
_SRC = _HERE.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))


from tabula_wake.wake_gpu import GpuInstanceConfig  # noqa: E402


class FakeInstance:
    def __init__(self, status: str) -> None:
        self.status = status


class FakeComputeClient:
    """An InstancesClient stand-in.

    State machine:
      * ``status_sequence`` is a list of statuses to return on successive
        ``get`` calls. The last status is sticky.
      * ``start()`` increments ``start_calls`` and, if the current status is
        TERMINATED, advances to ``post_start_status`` (default RUNNING).
    """

    def __init__(
        self,
        status_sequence: list[str],
        *,
        post_start_status: str = "RUNNING",
        start_exception: Optional[BaseException] = None,
        get_exception: Optional[BaseException] = None,
    ) -> None:
        self._status_sequence = list(status_sequence)
        self._post_start_status = post_start_status
        self._start_exception = start_exception
        self._get_exception = get_exception
        self.start_calls = 0
        self.get_calls = 0
        # Lock so concurrent-invocation tests can hammer this safely.
        self._lock = threading.Lock()

    def get(self, *, project: str, zone: str, instance: str) -> Any:
        with self._lock:
            self.get_calls += 1
            if self._get_exception is not None:
                raise self._get_exception
            if self._status_sequence:
                if len(self._status_sequence) > 1:
                    s = self._status_sequence.pop(0)
                else:
                    s = self._status_sequence[0]
            else:
                s = "RUNNING"
            return FakeInstance(s)

    def start(self, *, project: str, zone: str, instance: str) -> Any:
        with self._lock:
            self.start_calls += 1
            if self._start_exception is not None:
                raise self._start_exception
            # Transition the state machine: TERMINATED -> post_start_status.
            if self._status_sequence and self._status_sequence[0] == "TERMINATED":
                self._status_sequence.append(self._post_start_status)
                # Drop the leading TERMINATED so the next get() advances.
                self._status_sequence.pop(0)
            return None


@pytest.fixture
def gpu_config() -> GpuInstanceConfig:
    return GpuInstanceConfig(
        project_id="tabula-test",
        zone="us-central1-a",
        instance_name="tabula-gpu",
        health_url="http://gpu.test.internal:8443/healthz",
    )


class FakeClock:
    """A monotonic clock that advances explicitly via ``sleep``.

    We avoid real ``time.sleep`` in tests so they finish in ms.
    """

    def __init__(self, start: float = 1_700_000_000.0) -> None:
        self._t = start

    def now(self) -> float:
        return self._t

    def sleep(self, seconds: float) -> None:
        self._t += seconds


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()
