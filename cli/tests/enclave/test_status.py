"""Tests for ``tabula enclave status`` (issue #30).

Coverage matrix:

* ADC-auth path: the orchestrator does **not** require ADC at runtime --
  it shells out to ``gcloud`` (which itself uses ADC). The user-facing
  guarantee here is that no SDK import / no service-account key is
  required, and that the orchestrator never raises if ``gcloud`` is
  pluggable. We assert the no-import behaviour and the pluggability.
* state.json read: missing / corrupt / version-mismatch -> exit 2.
* GPU ``STOPPED`` is healthy (cold-by-default per Epic #12);
  ``cold_seconds`` is populated.
* GPU ``TERMINATED`` is unhealthy and yields exit 1.
* Exit-code taxonomy 0/1/2/3 each have a dedicated test.
* ``--json`` schema validation against the documented shape, including
  ``cold_seconds`` and ``issues`` fields.
* No-mutation guard: assert only read-only ``gcloud`` verbs are invoked
  by the real shell-out path.
"""

from __future__ import annotations

import json
import socket
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from unittest import mock

import pytest

from tabula_cli._enclave_state import (
    EnclaveState,
    StateNotFoundError,
    StateVersionError,
    write_state,
)
from tabula_cli.enclave.status import (
    DEFAULT_NOISE_PORT,
    EXIT_GCP_ERROR,
    EXIT_OK,
    EXIT_UNHEALTHY,
    EXIT_USER_ERROR,
    EXPECTED_ROLES,
    GcpProbeError,
    NOISE_PROBE_TIMEOUT_S,
    ReachabilityProbe,
    RoleTarget,
    StatusOptions,
    VmInfo,
    _classify_issues,
    _compute_cold_seconds,
    _vminfo_from_describe_payload,
    enclave_status,
)


# --------------------------------------------------------------------------- #
# Helpers                                                                     #
# --------------------------------------------------------------------------- #


def _outputs(
    *,
    classifier_ip: str = "34.10.20.30",
    noise_port: int = DEFAULT_NOISE_PORT,
) -> dict[str, Any]:
    """Canonical ``state.outputs`` populated by ``up`` / Terraform."""
    return {
        "classifier_ip": classifier_ip,
        "noise_port": noise_port,
        "classifier_instance": "demo-classifier",
        "classifier_zone": "us-central1-a",
        "gpu_instance": "demo-gpu",
        "gpu_zone": "us-central1-a",
        "gitea_instance": "demo-gitea",
        "gitea_zone": "us-central1-a",
    }


def _state(
    name: str = "demo",
    *,
    outputs: dict[str, Any] | None = None,
    project_id: str = "tabula-demo-123",
    region: str = "us-central1",
    created_at: str = "2026-05-07T12:00:00Z",
) -> EnclaveState:
    return EnclaveState(
        name=name,
        project_id=project_id,
        region=region,
        created_at=created_at,
        terraform_dir=f"/tmp/tabula/{name}/terraform",
        outputs=outputs if outputs is not None else _outputs(),
    )


def _seed_state_on_disk(tmp_path: Path, state: EnclaveState) -> Path:
    """Write a real state.json under ``tmp_path/<name>/`` and return root."""
    write_state(state, root=tmp_path)
    return tmp_path


def _running(role: str, name: str = "x", zone: str = "us-central1-a") -> VmInfo:
    return VmInfo(
        role=role,
        name=name,
        state="RUNNING",
        zone=zone,
        internal_ip="10.0.0.2",
        external_ip="34.1.2.3" if role == "classifier" else None,
        last_start="2026-05-07T12:35:10Z",
        last_stop=None,
    )


def _stopped_gpu(
    name: str = "demo-gpu", last_stop: str = "2026-05-07T13:14:30Z"
) -> VmInfo:
    return VmInfo(
        role="gpu",
        name=name,
        state="STOPPED",
        zone="us-central1-a",
        internal_ip="10.0.0.3",
        external_ip=None,
        last_start="2026-05-07T13:01:02Z",
        last_stop=last_stop,
    )


def _terminated_gpu(name: str = "demo-gpu") -> VmInfo:
    return VmInfo(
        role="gpu",
        name=name,
        state="TERMINATED",
        zone="us-central1-a",
        internal_ip=None,
        external_ip=None,
        last_start="2026-05-07T13:01:02Z",
        last_stop="2026-05-07T13:14:30Z",
    )


def _make_probe(role_to_vm: dict[str, VmInfo]):
    """Return an instance_probe callable that maps role -> VmInfo."""

    def _probe(target: RoleTarget, project: str) -> VmInfo:
        return role_to_vm[target.role]

    return _probe


def _stub_noise(reachable: bool = True, latency_ms: int = 41):
    """Return a NoiseProbeFn that returns a deterministic probe result."""

    def _probe(host, port, timeout):
        return ReachabilityProbe(
            host=host,
            port=port,
            reachable=reachable,
            latency_ms=latency_ms if reachable else None,
        )

    return _probe


def _fixed_now(iso: str = "2026-05-07T13:26:30Z"):
    parsed = datetime.fromisoformat(iso[:-1]).replace(tzinfo=timezone.utc)

    def _now() -> datetime:
        return parsed

    return _now


# --------------------------------------------------------------------------- #
# Pure unit tests (helpers)                                                   #
# --------------------------------------------------------------------------- #


class TestParseDescribePayload:
    """``_vminfo_from_describe_payload`` extracts the right fields."""

    def test_extracts_state_ips_and_timestamps(self):
        target = RoleTarget(role="classifier", instance="demo-c", zone="us-central1-a")
        payload = {
            "status": "RUNNING",
            "networkInterfaces": [
                {
                    "networkIP": "10.0.0.2",
                    "accessConfigs": [{"natIP": "34.1.2.3"}],
                }
            ],
            "lastStartTimestamp": "2026-05-07T12:35:10Z",
            "lastStopTimestamp": None,
        }
        vm = _vminfo_from_describe_payload(target, payload)
        assert vm.role == "classifier"
        assert vm.name == "demo-c"
        assert vm.state == "RUNNING"
        assert vm.internal_ip == "10.0.0.2"
        assert vm.external_ip == "34.1.2.3"
        assert vm.last_start == "2026-05-07T12:35:10Z"
        assert vm.last_stop is None

    def test_handles_missing_nics(self):
        target = RoleTarget(role="gpu", instance="demo-gpu", zone="us-central1-a")
        vm = _vminfo_from_describe_payload(target, {"status": "STOPPED"})
        assert vm.state == "STOPPED"
        assert vm.internal_ip is None
        assert vm.external_ip is None

    def test_handles_no_external_access(self):
        target = RoleTarget(role="gitea", instance="demo-gitea", zone="us-central1-a")
        payload = {
            "status": "RUNNING",
            "networkInterfaces": [{"networkIP": "10.0.0.4"}],
        }
        vm = _vminfo_from_describe_payload(target, payload)
        assert vm.external_ip is None
        assert vm.internal_ip == "10.0.0.4"


class TestComputeColdSeconds:
    def test_stopped_with_last_stop(self):
        vm = _stopped_gpu(last_stop="2026-05-07T13:14:30Z")
        cold = _compute_cold_seconds(vm, now=_fixed_now()())
        # 13:26:30 - 13:14:30 = 720s
        assert cold == 720

    def test_running_returns_none(self):
        vm = _running("gpu")
        assert _compute_cold_seconds(vm, now=_fixed_now()()) is None

    def test_unparseable_timestamp_returns_none(self):
        vm = _stopped_gpu(last_stop="not-a-date")
        assert _compute_cold_seconds(vm, now=_fixed_now()()) is None

    def test_clamps_negative_to_zero(self):
        # If "now" precedes last_stop (clock skew), surface 0 not -N.
        vm = _stopped_gpu(last_stop="2026-05-07T13:14:30Z")
        earlier = datetime.fromisoformat("2026-05-07T13:00:00").replace(
            tzinfo=timezone.utc
        )
        assert _compute_cold_seconds(vm, now=earlier) == 0


class TestClassifyIssues:
    def test_all_running_is_healthy(self):
        vms = [
            _running("classifier", "demo-classifier"),
            _running("gpu", "demo-gpu"),
            _running("gitea", "demo-gitea"),
        ]
        assert _classify_issues(vms) == []

    def test_stopped_gpu_is_healthy(self):
        vms = [
            _running("classifier", "demo-classifier"),
            _stopped_gpu("demo-gpu"),
            _running("gitea", "demo-gitea"),
        ]
        assert _classify_issues(vms) == []

    def test_terminated_gpu_is_unhealthy(self):
        vms = [_terminated_gpu("demo-gpu")]
        issues = _classify_issues(vms)
        assert len(issues) == 1
        assert "TERMINATED" in issues[0]
        assert "gpu" in issues[0]

    def test_stopped_classifier_is_unhealthy(self):
        vms = [
            VmInfo(role="classifier", name="demo-c", state="STOPPED", zone="z"),
        ]
        issues = _classify_issues(vms)
        assert len(issues) == 1
        assert "STOPPED" in issues[0]
        assert "classifier" in issues[0]

    def test_missing_vm_is_unhealthy(self):
        vms = [VmInfo(role="gpu", name="", state="MISSING", zone="")]
        issues = _classify_issues(vms)
        assert len(issues) == 1
        assert "missing from state.json" in issues[0]

    def test_provisioning_state_is_unhealthy(self):
        vms = [VmInfo(role="classifier", name="demo-c", state="PROVISIONING", zone="z")]
        issues = _classify_issues(vms)
        assert len(issues) == 1
        assert "non-steady" in issues[0]


# --------------------------------------------------------------------------- #
# enclave_status -- exit-code taxonomy + behaviour                            #
# --------------------------------------------------------------------------- #


class TestExitCode2_UserError:
    """state-file read failures map to exit 2."""

    def test_missing_state_file_yields_exit_2(self, tmp_path, capsys):
        # No state seeded -> default reader raises StateNotFoundError.
        opts = StatusOptions(
            name="demo",
            enclaves_root=tmp_path,
        )
        rc = enclave_status(opts)
        assert rc == EXIT_USER_ERROR
        err = capsys.readouterr().err
        assert "tabula enclave up demo" in err

    def test_invalid_name_yields_exit_2(self, tmp_path, capsys):
        opts = StatusOptions(name="Bad Name!", enclaves_root=tmp_path)
        rc = enclave_status(opts)
        assert rc == EXIT_USER_ERROR
        assert "invalid enclave name" in capsys.readouterr().err

    def test_version_mismatch_yields_exit_2(self, tmp_path, capsys):
        # Seed a state.json by hand with a future version.
        d = tmp_path / "demo"
        d.mkdir()
        (d / "state.json").write_text(
            json.dumps(
                {
                    "version": 999,
                    "name": "demo",
                    "project_id": "p",
                    "region": "us-central1",
                    "created_at": "2026-05-07T12:00:00Z",
                    "terraform_dir": "/tmp/x",
                    "outputs": {},
                }
            ),
            encoding="utf-8",
        )
        opts = StatusOptions(name="demo", enclaves_root=tmp_path)
        rc = enclave_status(opts)
        assert rc == EXIT_USER_ERROR
        assert "version" in capsys.readouterr().err.lower()


class TestExitCode3_GcpError:
    """Probe-side failures map to exit 3 (distinct from user error)."""

    def test_probe_raises_gcp_error_yields_exit_3(self, tmp_path, capsys):
        _seed_state_on_disk(tmp_path, _state())

        def _failing_probe(target, project):
            raise GcpProbeError("permission denied")

        opts = StatusOptions(
            name="demo",
            enclaves_root=tmp_path,
            instance_probe=_failing_probe,
            noise_probe=_stub_noise(),
        )
        rc = enclave_status(opts)
        assert rc == EXIT_GCP_ERROR
        assert "GCP probe failed" in capsys.readouterr().err


class TestExitCode0_Healthy:
    """All running + stopped GPU -> healthy -> exit 0."""

    def test_running_classifier_stopped_gpu_running_gitea(self, tmp_path, capsys):
        _seed_state_on_disk(tmp_path, _state())
        probes = {
            "classifier": _running("classifier", "demo-classifier"),
            "gpu": _stopped_gpu("demo-gpu"),
            "gitea": _running("gitea", "demo-gitea"),
        }
        opts = StatusOptions(
            name="demo",
            enclaves_root=tmp_path,
            instance_probe=_make_probe(probes),
            noise_probe=_stub_noise(reachable=True, latency_ms=12),
            now_fn=_fixed_now(),
        )
        rc = enclave_status(opts)
        out = capsys.readouterr().out
        assert rc == EXIT_OK
        assert "HEALTHY" in out
        assert "cold for 720s" in out  # GPU cold time rendered


class TestExitCode1_Unhealthy:
    """TERMINATED GPU (or missing role) -> unhealthy -> exit 1."""

    def test_terminated_gpu_yields_exit_1(self, tmp_path, capsys):
        _seed_state_on_disk(tmp_path, _state())
        probes = {
            "classifier": _running("classifier", "demo-classifier"),
            "gpu": _terminated_gpu("demo-gpu"),
            "gitea": _running("gitea", "demo-gitea"),
        }
        opts = StatusOptions(
            name="demo",
            enclaves_root=tmp_path,
            instance_probe=_make_probe(probes),
            noise_probe=_stub_noise(),
            now_fn=_fixed_now(),
        )
        rc = enclave_status(opts)
        out = capsys.readouterr().out
        assert rc == EXIT_UNHEALTHY
        assert "UNHEALTHY" in out
        assert "TERMINATED" in out

    def test_missing_role_outputs_yields_exit_1(self, tmp_path, capsys):
        # State has no per-role outputs -> all roles flagged "missing".
        outputs = {"classifier_ip": "34.1.1.1", "noise_port": 7777}
        _seed_state_on_disk(tmp_path, _state(outputs=outputs))

        # Probe should not be called for missing roles; raise if it is.
        def _explode(*a, **kw):
            raise AssertionError("probe should not be called for missing roles")

        opts = StatusOptions(
            name="demo",
            enclaves_root=tmp_path,
            instance_probe=_explode,
            noise_probe=_stub_noise(),
            now_fn=_fixed_now(),
        )
        rc = enclave_status(opts)
        assert rc == EXIT_UNHEALTHY
        out = capsys.readouterr().out
        # Each expected role surfaces a "missing" issue.
        for role in EXPECTED_ROLES:
            assert role in out


# --------------------------------------------------------------------------- #
# --json schema                                                               #
# --------------------------------------------------------------------------- #


class TestJsonSchema:
    """``--json`` produces a stable, documented shape."""

    def _emit_json(self, tmp_path, *, gpu_state: str, capsys) -> dict:
        _seed_state_on_disk(tmp_path, _state())
        probes = {
            "classifier": _running("classifier", "demo-classifier"),
            "gpu": (
                _stopped_gpu("demo-gpu")
                if gpu_state == "STOPPED"
                else _terminated_gpu("demo-gpu")
            ),
            "gitea": _running("gitea", "demo-gitea"),
        }
        opts = StatusOptions(
            name="demo",
            enclaves_root=tmp_path,
            json_output=True,
            instance_probe=_make_probe(probes),
            noise_probe=_stub_noise(reachable=True, latency_ms=41),
            now_fn=_fixed_now(),
        )
        enclave_status(opts)
        return json.loads(capsys.readouterr().out)

    def test_top_level_keys_present(self, tmp_path, capsys):
        doc = self._emit_json(tmp_path, gpu_state="STOPPED", capsys=capsys)
        for key in (
            "name",
            "project",
            "region",
            "created_at",
            "vms",
            "reachability",
            "healthy",
            "issues",
        ):
            assert key in doc, f"missing top-level key: {key}"

    def test_healthy_true_when_stopped_gpu(self, tmp_path, capsys):
        doc = self._emit_json(tmp_path, gpu_state="STOPPED", capsys=capsys)
        assert doc["healthy"] is True
        assert doc["issues"] == []

    def test_healthy_false_when_terminated_gpu(self, tmp_path, capsys):
        doc = self._emit_json(tmp_path, gpu_state="TERMINATED", capsys=capsys)
        assert doc["healthy"] is False
        assert isinstance(doc["issues"], list)
        assert any("TERMINATED" in s for s in doc["issues"])

    def test_cold_seconds_present_for_stopped_gpu(self, tmp_path, capsys):
        doc = self._emit_json(tmp_path, gpu_state="STOPPED", capsys=capsys)
        gpu_entry = next(v for v in doc["vms"] if v["role"] == "gpu")
        # Pinned now=13:26:30Z, last_stop=13:14:30Z -> 720s
        assert gpu_entry["cold_seconds"] == 720

    def test_cold_seconds_absent_for_running_vm(self, tmp_path, capsys):
        doc = self._emit_json(tmp_path, gpu_state="STOPPED", capsys=capsys)
        # Classifier is RUNNING -> no cold_seconds key
        classifier = next(v for v in doc["vms"] if v["role"] == "classifier")
        assert "cold_seconds" not in classifier

    def test_reachability_noise_port_shape(self, tmp_path, capsys):
        doc = self._emit_json(tmp_path, gpu_state="STOPPED", capsys=capsys)
        np = doc["reachability"]["noise_port"]
        for k in ("host", "port", "reachable", "latency_ms"):
            assert k in np
        assert np["reachable"] is True
        assert np["latency_ms"] == 41

    def test_per_vm_required_fields(self, tmp_path, capsys):
        doc = self._emit_json(tmp_path, gpu_state="STOPPED", capsys=capsys)
        required = {
            "role",
            "name",
            "state",
            "zone",
            "internal_ip",
            "external_ip",
            "last_start",
            "last_stop",
        }
        for vm in doc["vms"]:
            assert required.issubset(vm.keys())

    def test_vm_order_matches_expected_roles(self, tmp_path, capsys):
        doc = self._emit_json(tmp_path, gpu_state="STOPPED", capsys=capsys)
        actual_roles = [v["role"] for v in doc["vms"]]
        assert actual_roles == list(EXPECTED_ROLES)


# --------------------------------------------------------------------------- #
# Concurrency                                                                 #
# --------------------------------------------------------------------------- #


class TestConcurrency:
    """All probes run in parallel, not sequentially."""

    def test_probes_run_concurrently(self, tmp_path):
        """Three 0.5s probes + one 0.5s noise probe complete well under 2.0s."""
        import time

        _seed_state_on_disk(tmp_path, _state())

        def _slow_probe(target, project):
            time.sleep(0.5)
            return _running(target.role, target.instance)

        def _slow_noise(host, port, timeout):
            time.sleep(0.5)
            return ReachabilityProbe(
                host=host, port=port, reachable=True, latency_ms=1
            )

        opts = StatusOptions(
            name="demo",
            enclaves_root=tmp_path,
            instance_probe=_slow_probe,
            noise_probe=_slow_noise,
            now_fn=_fixed_now(),
        )
        t0 = time.monotonic()
        rc = enclave_status(opts)
        elapsed = time.monotonic() - t0
        assert rc == EXIT_OK
        # Sequential would be ~2.0s; parallel should be ~0.5s. Allow generous
        # headroom for slow CI.
        assert elapsed < 1.5, f"probes ran sequentially: {elapsed:.2f}s"


# --------------------------------------------------------------------------- #
# ADC / auth                                                                  #
# --------------------------------------------------------------------------- #


class TestAdcAuth:
    """The orchestrator never imports google-cloud SDKs, never demands ADC.

    `gcloud` itself uses ADC; we delegate. The behavioural promise to the
    operator is "if `gcloud compute instances describe` works for you on
    the command line, `tabula enclave status` works for you too" -- no
    extra service-account key, no extra env var.
    """

    def test_orchestrator_does_not_import_google_sdk(self):
        import sys

        import tabula_cli.enclave.status as status_mod  # re-import is fine
        # No google.cloud.* modules should be pulled in by importing status.
        assert all(not k.startswith("google.cloud") for k in sys.modules)
        assert hasattr(status_mod, "enclave_status")

    def test_probe_is_pluggable(self, tmp_path, capsys):
        """The instance_probe callable is fully overridable -- no real auth."""
        _seed_state_on_disk(tmp_path, _state())
        called: list[tuple[str, str]] = []

        def _probe(target, project):
            called.append((target.role, project))
            return _running(target.role, target.instance)

        opts = StatusOptions(
            name="demo",
            enclaves_root=tmp_path,
            instance_probe=_probe,
            noise_probe=_stub_noise(),
            now_fn=_fixed_now(),
        )
        rc = enclave_status(opts)
        assert rc == EXIT_OK
        # All three roles were probed, all with the same project.
        assert {role for role, _ in called} == set(EXPECTED_ROLES)
        assert {proj for _, proj in called} == {"tabula-demo-123"}


# --------------------------------------------------------------------------- #
# No-mutation guard                                                           #
# --------------------------------------------------------------------------- #


class TestNoMutationGuard:
    """The real `gcloud` shell-out path uses only read-only verbs.

    The mutation surface from `gcloud compute instances` includes
    `start`, `stop`, `delete`, `reset`, and `set-*` verbs. The status
    command must call only `describe`. We assert this on the real
    `_gcloud_describe_instance` helper by capturing the argv.
    """

    def test_only_describe_verb_is_invoked(self, monkeypatch):
        from tabula_cli.enclave.status import _gcloud_describe_instance

        captured: dict[str, list[str]] = {}

        def _fake_run(cmd, *a, **kw):
            captured["argv"] = list(cmd)
            # Return a successful gcloud-describe-shaped result.
            return subprocess.CompletedProcess(
                args=cmd,
                returncode=0,
                stdout=json.dumps(
                    {
                        "status": "RUNNING",
                        "networkInterfaces": [
                            {
                                "networkIP": "10.0.0.2",
                                "accessConfigs": [{"natIP": "34.1.2.3"}],
                            }
                        ],
                        "lastStartTimestamp": "2026-05-07T12:35:10Z",
                        "lastStopTimestamp": None,
                    }
                ),
                stderr="",
            )

        monkeypatch.setattr(
            "tabula_cli.enclave.status.subprocess.run", _fake_run
        )

        target = RoleTarget(role="classifier", instance="demo-c", zone="us-central1-a")
        vm = _gcloud_describe_instance(target, "tabula-demo-123")

        assert captured["argv"][0] == "gcloud"
        assert captured["argv"][1] == "compute"
        assert captured["argv"][2] == "instances"
        assert captured["argv"][3] == "describe"
        # Crucially, none of these mutating verbs:
        for forbidden in (
            "start",
            "stop",
            "delete",
            "reset",
            "set-machine-type",
            "set-disk-auto-delete",
            "set-scheduling",
            "set-service-account",
            "add-metadata",
            "remove-metadata",
            "add-tags",
            "remove-tags",
        ):
            assert forbidden not in captured["argv"]

        assert vm.state == "RUNNING"
        assert vm.internal_ip == "10.0.0.2"

    def test_gcloud_describe_failure_raises_gcp_probe_error(self, monkeypatch):
        from tabula_cli.enclave.status import _gcloud_describe_instance

        def _fake_run(cmd, *a, **kw):
            return subprocess.CompletedProcess(
                args=cmd,
                returncode=1,
                stdout="",
                stderr="permission denied",
            )

        monkeypatch.setattr(
            "tabula_cli.enclave.status.subprocess.run", _fake_run
        )
        target = RoleTarget(role="gpu", instance="demo-gpu", zone="us-central1-a")
        with pytest.raises(GcpProbeError):
            _gcloud_describe_instance(target, "tabula-demo-123")


# --------------------------------------------------------------------------- #
# Noise TCP probe timeout                                                     #
# --------------------------------------------------------------------------- #


class TestNoiseProbeTimeout:
    """The TCP-connect probe respects the 2-second timeout."""

    def test_timeout_constant_is_two_seconds(self):
        assert NOISE_PROBE_TIMEOUT_S == 2.0

    def test_probe_returns_unreachable_on_timeout(self, monkeypatch):
        from tabula_cli.enclave.status import _socket_noise_probe

        def _raises_timeout(addr, timeout):
            raise TimeoutError("simulated")

        monkeypatch.setattr(
            "tabula_cli.enclave.status.socket.create_connection", _raises_timeout
        )
        result = _socket_noise_probe("34.1.2.3", 7777, 2.0)
        assert result.reachable is False
        assert result.latency_ms is None

    def test_probe_returns_unreachable_on_connection_refused(self, monkeypatch):
        from tabula_cli.enclave.status import _socket_noise_probe

        def _raises_oserror(addr, timeout):
            raise OSError("connection refused")

        monkeypatch.setattr(
            "tabula_cli.enclave.status.socket.create_connection", _raises_oserror
        )
        result = _socket_noise_probe("34.1.2.3", 7777, 2.0)
        assert result.reachable is False

    def test_probe_returns_unreachable_when_no_host(self):
        from tabula_cli.enclave.status import _socket_noise_probe

        result = _socket_noise_probe(None, 7777, 2.0)
        assert result.reachable is False
        assert result.host is None
