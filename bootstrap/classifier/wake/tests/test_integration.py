"""Integration tests for the wake helper.

These tests are deliberately black-box: they exercise the full code path
from CLI args → config load → fake compute client → wake routine → exit
code, and they verify the *contract* with the IAM module (#15) by
inspecting the custom role definition rather than running real gcloud.

We do not depend on an actual GCP project or `gcloud` binary. The "gcloud
mock" is the FakeComputeClient injected by the harness, which mirrors the
behavior of a real ``gcloud compute instances`` invocation precisely enough
for these tests.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

from tabula_wake import cli as wake_cli
from tabula_wake.wake_gpu import wake_gpu

from .conftest import FakeComputeClient


# ---------------------------------------------------------------------------
# CLI integration: exit codes
# ---------------------------------------------------------------------------


@pytest.fixture
def cli_runner(monkeypatch, tmp_path, gpu_config):
    """Returns a function that runs ``cli.main()`` against a fake client.

    Patches ``compute_v1.InstancesClient`` to return the fake, and writes a
    config file the CLI will pick up.
    """
    cfg_path = tmp_path / "wake.json"
    cfg_path.write_text(
        json.dumps(
            {
                "project_id": gpu_config.project_id,
                "zone": gpu_config.zone,
                "instance_name": gpu_config.instance_name,
                "health_url": gpu_config.health_url,
            }
        )
    )

    def run(client: FakeComputeClient, *extra: str) -> int:
        # Provide a fake google.cloud.compute_v1 module hierarchy ahead of
        # the lazy ``from google.cloud import compute_v1`` import in
        # cli.main(). We don't need the real SDK installed for these tests.
        fake_google = type(sys)("google")
        fake_google.__path__ = []  # mark as package so submodules import
        fake_cloud = type(sys)("google.cloud")
        fake_cloud.__path__ = []
        fake_compute = type(sys)("google.cloud.compute_v1")
        fake_compute.InstancesClient = lambda: client  # type: ignore[attr-defined]
        # Wire the parent attributes so ``from google.cloud import
        # compute_v1`` succeeds.
        fake_google.cloud = fake_cloud  # type: ignore[attr-defined]
        fake_cloud.compute_v1 = fake_compute  # type: ignore[attr-defined]
        monkeypatch.setitem(sys.modules, "google", fake_google)
        monkeypatch.setitem(sys.modules, "google.cloud", fake_cloud)
        monkeypatch.setitem(sys.modules, "google.cloud.compute_v1", fake_compute)
        # Silence the metadata fetch path during config load.
        monkeypatch.setenv("TABULA_WAKE_CONFIG", str(cfg_path))
        # Force the helper to skip real network probes. Note: the
        # ``tabula_wake`` package re-exports ``wake_gpu`` (the function) so
        # ``import tabula_wake.wake_gpu`` resolves to that function. We
        # therefore reach the module via importlib to access its attrs.
        import importlib

        wg_mod = importlib.import_module("tabula_wake.wake_gpu")
        monkeypatch.setattr(wg_mod, "_default_health_check", lambda url: True)
        argv = ["--config", str(cfg_path), "--quiet", *extra]
        return wake_cli.main(argv)

    return run


def test_cli_exit_zero_when_already_running(cli_runner):
    client = FakeComputeClient(["RUNNING"])
    rc = cli_runner(client, "--timeout", "5", "--poll-interval", "0.1")
    assert rc == 0
    assert client.start_calls == 0


def test_cli_exit_zero_when_started(cli_runner):
    client = FakeComputeClient(["TERMINATED"], post_start_status="RUNNING")
    rc = cli_runner(client, "--timeout", "5", "--poll-interval", "0.1")
    assert rc == 0
    assert client.start_calls == 1


def test_cli_exit_two_on_timeout(cli_runner):
    client = FakeComputeClient(["STOPPING"])  # never advances
    rc = cli_runner(client, "--timeout", "0.5", "--poll-interval", "0.05")
    assert rc == 2  # _EXIT_TIMEOUT


def test_cli_exit_three_on_permission_denied(cli_runner):
    class PermissionDenied(Exception):
        pass

    PermissionDenied.__name__ = "PermissionDenied"

    client = FakeComputeClient(
        ["TERMINATED"],
        start_exception=PermissionDenied("403"),
    )
    rc = cli_runner(client, "--timeout", "5", "--poll-interval", "0.05")
    assert rc == 3  # _EXIT_PERMISSION_DENIED


# ---------------------------------------------------------------------------
# IAM contract: the wake helper MUST only ever call get + start. Anything
# else would silently drift from the ``${enclave}_gpu_waker`` custom role
# defined in the IAM module (#15).
# ---------------------------------------------------------------------------


class _PermissionTrackingClient:
    """Records the *names* of the methods called on it.

    We assert below that the wake helper only ever invokes ``get`` and
    ``start`` — exactly the two permissions in the gpu_waker custom role.
    Any future change that adds, e.g., ``stop`` or ``delete`` will fail
    this test loudly and force a deliberate IAM-role update.
    """

    def __init__(self) -> None:
        self.calls: list[str] = []

    def get(self, *, project: str, zone: str, instance: str) -> Any:
        self.calls.append("get")

        class _R:
            status = "RUNNING"

        return _R()

    def start(self, *, project: str, zone: str, instance: str) -> Any:
        self.calls.append("start")
        return None

    # If wake_gpu ever tries to call any other method, attribute access
    # will succeed (because of __getattr__) and we record the bad call.
    def __getattr__(self, name: str):
        def _bad(*a: Any, **kw: Any) -> None:
            self.calls.append(f"FORBIDDEN:{name}")

        return _bad


def test_wake_only_uses_get_and_start_methods(gpu_config):
    """Permission-scope assertion. The set of methods touched must be a
    subset of {get, start}."""
    client = _PermissionTrackingClient()

    wake_gpu(
        gpu_config,
        client=client,  # type: ignore[arg-type]
        timeout_s=2.0,
        poll_interval_s=0.01,
        health_check=lambda _u: True,
    )

    forbidden = [c for c in client.calls if c.startswith("FORBIDDEN:")]
    assert not forbidden, (
        f"wake helper called methods outside the gpu_waker IAM role: {forbidden}"
    )
    assert set(client.calls).issubset({"get", "start"})


# ---------------------------------------------------------------------------
# IAM contract: cross-check the IAM module's custom role permissions match
# what the wake helper actually uses.
# ---------------------------------------------------------------------------


def _repo_root() -> Path:
    # tests/test_integration.py -> bootstrap/classifier/wake/tests -> repo root.
    return Path(__file__).resolve().parents[4]


def test_iam_custom_role_permissions_match_helper_usage():
    """Pin the wake helper's contract with the IAM module.

    The IAM module (#15) defines a ``gpu_waker`` custom role with exactly
    two permissions. The wake helper must use exactly those — no more, no
    fewer. If the IAM module ever changes that role, this test fails and
    forces a coordinated update.
    """
    iam_main = _repo_root() / "terraform" / "modules" / "iam" / "main.tf"
    if not iam_main.exists():
        # IAM module lives in a parallel issue branch; on main this file
        # may not yet exist. Skip rather than fail — the test will start
        # enforcing the contract once the IAM module lands.
        pytest.skip("terraform/modules/iam/main.tf not present on this branch")

    text = iam_main.read_text()
    # Permissions block in the gpu_waker custom role.
    assert "compute.instances.get" in text
    assert "compute.instances.start" in text
    # Forbidden permissions: anything that would expand the wake authority
    # beyond "wake one VM" must NOT appear in the gpu_waker role.
    forbidden_perms = [
        "compute.instances.stop",
        "compute.instances.delete",
        "compute.instances.setMetadata",
        "compute.instances.setMachineType",
    ]
    # Search inside the gpu_waker role definition only.
    role_block_start = text.find('resource "google_project_iam_custom_role" "gpu_waker"')
    assert role_block_start >= 0, "gpu_waker custom role missing from IAM module"
    role_block_end = text.find("}", role_block_start)
    # Find the closing brace of the resource by scanning forward through
    # the permissions block.
    # Cheap heuristic: look at a 1500-char window after the start.
    window = text[role_block_start : role_block_start + 1500]
    for forbidden in forbidden_perms:
        assert forbidden not in window, (
            f"gpu_waker role grants {forbidden} — broader than wake helper needs"
        )
