"""Tests for ``tabula enclave ssh`` (issue #33).

Covers:

* Argparse wiring (``add_subparser`` + ``run``).
* Role -> instance/zone/project resolution from ``state.json``.
* Hostname resolution via ``state.outputs`` keys.
* Missing-state user error path with the canonical pointer message.
* SSH argv construction (``--tunnel-through-iap``, ``--command``,
  ``--forward-agent``).
* Exit-code propagation from the underlying ``gcloud`` runner.
* TERMINATED VM refusal with a pointer at ``tabula enclave up``.
* STOPPED VM prompt path: ``--no-start``, decline, accept.
* Bad role / bad enclave name shape.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Sequence

import pytest

from tabula_cli.enclave import ssh as ssh_mod
from tabula_cli.enclave.ssh import (
    EXIT_DECLINED,
    EXIT_OK,
    EXIT_USER_ERROR,
    INSTANCE_RUNNING,
    INSTANCE_STOPPED,
    INSTANCE_TERMINATED,
    ResolvedTarget,
    SshOptions,
    VALID_ROLES,
    build_gcloud_ssh_argv,
    enclave_ssh,
    resolve_target,
)
from tabula_cli._enclave_state import EnclaveState, STATE_SCHEMA_VERSION


# --------------------------------------------------------------------------- #
# Helpers                                                                     #
# --------------------------------------------------------------------------- #


def _make_state(
    name: str = "demo",
    *,
    project_id: str = "tabula-demo-123",
    region: str = "us-central1",
    outputs: dict[str, str] | None = None,
) -> EnclaveState:
    """Build a populated :class:`EnclaveState` for the happy path."""
    if outputs is None:
        outputs = {
            "classifier_instance": "demo-classifier",
            "classifier_zone": "us-central1-a",
            "gpu_instance": "demo-gpu",
            "gpu_zone": "us-central1-a",
            "gitea_instance": "demo-gitea",
            "gitea_zone": "us-central1-a",
        }
    return EnclaveState(
        name=name,
        project_id=project_id,
        region=region,
        created_at="2026-05-07T12:00:00Z",
        terraform_dir=f"/tmp/tabula/{name}/terraform",
        outputs=outputs,
    )


def _write_state(root: Path, state: EnclaveState) -> Path:
    """Write a state.json on disk under ``root`` for full read_state coverage."""
    d = root / state.name
    d.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": STATE_SCHEMA_VERSION,
        "name": state.name,
        "project_id": state.project_id,
        "region": state.region,
        "created_at": state.created_at,
        "terraform_dir": state.terraform_dir,
        "outputs": state.outputs,
    }
    p = d / "state.json"
    p.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return p


def _make_opts(
    *,
    name: str = "demo",
    role: str = "classifier",
    command: str | None = None,
    forward_agent: bool = False,
    no_start: bool = False,
    enclaves_root: Path | None = None,
    state: EnclaveState | None = None,
    status: str = INSTANCE_RUNNING,
    ssh_exit: int = 0,
    start_exit: int = 0,
    prompt_answer: bool = True,
    fake_ssh_argv: list[Sequence[str]] | None = None,
    fake_start_calls: list[ResolvedTarget] | None = None,
    fake_status_calls: list[ResolvedTarget] | None = None,
    fake_prompts: list[str] | None = None,
) -> SshOptions:
    """Build an :class:`SshOptions` with injected fakes for every callable."""
    if state is None:
        state = _make_state(name=name)
    if fake_ssh_argv is None:
        fake_ssh_argv = []
    if fake_start_calls is None:
        fake_start_calls = []
    if fake_status_calls is None:
        fake_status_calls = []
    if fake_prompts is None:
        fake_prompts = []

    def _state_reader(req_name: str, root: Path | None) -> EnclaveState:
        assert req_name == name
        return state

    def _status_probe(target: ResolvedTarget) -> str:
        fake_status_calls.append(target)
        return status

    def _start_runner(target: ResolvedTarget) -> int:
        fake_start_calls.append(target)
        return start_exit

    def _ssh_runner(argv: Sequence[str]) -> int:
        fake_ssh_argv.append(list(argv))
        return ssh_exit

    def _prompt(message: str) -> bool:
        fake_prompts.append(message)
        return prompt_answer

    return SshOptions(
        name=name,
        role=role,
        command=command,
        forward_agent=forward_agent,
        no_start=no_start,
        enclaves_root=enclaves_root,
        state_reader=_state_reader,
        status_probe=_status_probe,
        start_runner=_start_runner,
        ssh_runner=_ssh_runner,
        prompt=_prompt,
    )


@pytest.fixture(autouse=True)
def _stub_gcloud_path(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pretend ``gcloud`` is on PATH so the availability check passes.

    The check uses :func:`shutil.which`. We patch it inside the ssh module
    to always return a non-None value so tests do not depend on the host.
    """
    monkeypatch.setattr(ssh_mod.shutil, "which", lambda _name: "/usr/bin/gcloud")


# --------------------------------------------------------------------------- #
# resolve_target                                                              #
# --------------------------------------------------------------------------- #


class TestResolveTarget:
    def test_classifier(self) -> None:
        state = _make_state()
        t = resolve_target(state, "classifier")
        assert t == ResolvedTarget(
            instance="demo-classifier",
            zone="us-central1-a",
            project="tabula-demo-123",
            role="classifier",
        )

    def test_gpu(self) -> None:
        state = _make_state()
        t = resolve_target(state, "gpu")
        assert t.instance == "demo-gpu"
        assert t.zone == "us-central1-a"
        assert t.project == "tabula-demo-123"

    def test_gitea(self) -> None:
        state = _make_state()
        t = resolve_target(state, "gitea")
        assert t.instance == "demo-gitea"

    def test_unknown_role_raises(self) -> None:
        state = _make_state()
        with pytest.raises(ValueError, match=r"unknown role 'database'"):
            resolve_target(state, "database")

    def test_unknown_role_lists_valid_choices(self) -> None:
        state = _make_state()
        with pytest.raises(ValueError) as exc:
            resolve_target(state, "database")
        for r in VALID_ROLES:
            assert r in str(exc.value)

    def test_missing_outputs_raises(self) -> None:
        # Drop the gpu_instance key — emulates a stale state.json from
        # before the GPU module landed.
        state = _make_state(
            outputs={
                "classifier_instance": "demo-classifier",
                "classifier_zone": "us-central1-a",
                # gpu_* deliberately missing
                "gitea_instance": "demo-gitea",
                "gitea_zone": "us-central1-a",
            }
        )
        with pytest.raises(ValueError, match=r"gpu_instance"):
            resolve_target(state, "gpu")


# --------------------------------------------------------------------------- #
# build_gcloud_ssh_argv                                                       #
# --------------------------------------------------------------------------- #


class TestBuildArgv:
    @pytest.fixture
    def target(self) -> ResolvedTarget:
        return ResolvedTarget(
            instance="demo-classifier",
            zone="us-central1-a",
            project="tabula-demo-123",
            role="classifier",
        )

    def test_baseline(self, target: ResolvedTarget) -> None:
        argv = build_gcloud_ssh_argv(target, command=None, forward_agent=False)
        assert argv == [
            "gcloud",
            "compute",
            "ssh",
            "demo-classifier",
            "--zone=us-central1-a",
            "--project=tabula-demo-123",
            "--tunnel-through-iap",
        ]

    def test_command(self, target: ResolvedTarget) -> None:
        argv = build_gcloud_ssh_argv(
            target, command="systemctl status tabula-server", forward_agent=False
        )
        # --command is appended at the end so it is unambiguous.
        assert argv[-1] == "--command=systemctl status tabula-server"
        assert "--tunnel-through-iap" in argv

    def test_forward_agent(self, target: ResolvedTarget) -> None:
        argv = build_gcloud_ssh_argv(target, command=None, forward_agent=True)
        assert "--ssh-flag=-A" in argv

    def test_forward_agent_off_by_default(self, target: ResolvedTarget) -> None:
        argv = build_gcloud_ssh_argv(target, command=None, forward_agent=False)
        assert "--ssh-flag=-A" not in argv

    def test_command_and_forward_agent(self, target: ResolvedTarget) -> None:
        argv = build_gcloud_ssh_argv(target, command="ls -la", forward_agent=True)
        assert "--ssh-flag=-A" in argv
        assert "--command=ls -la" in argv


# --------------------------------------------------------------------------- #
# enclave_ssh — happy path                                                    #
# --------------------------------------------------------------------------- #


class TestEnclaveSshHappyPath:
    def test_running_classifier_invokes_gcloud_with_iap(
        self,
    ) -> None:
        captured: list[Sequence[str]] = []
        opts = _make_opts(
            role="classifier",
            status=INSTANCE_RUNNING,
            fake_ssh_argv=captured,
        )
        rc = enclave_ssh(opts)
        assert rc == EXIT_OK
        assert len(captured) == 1
        argv = captured[0]
        # Hostname resolution: instance name comes from state.outputs.
        assert "demo-classifier" in argv
        assert "--zone=us-central1-a" in argv
        assert "--project=tabula-demo-123" in argv
        assert "--tunnel-through-iap" in argv

    def test_command_flag_appears_in_argv(self) -> None:
        captured: list[Sequence[str]] = []
        opts = _make_opts(
            role="gitea",
            command="uptime",
            fake_ssh_argv=captured,
        )
        rc = enclave_ssh(opts)
        assert rc == EXIT_OK
        assert "--command=uptime" in captured[0]
        assert "demo-gitea" in captured[0]

    def test_exit_code_propagated_from_gcloud(self) -> None:
        # Non-zero gcloud exit (e.g. remote command failed) must propagate
        # so scripts using --command can branch on it.
        captured: list[Sequence[str]] = []
        opts = _make_opts(
            role="gpu",
            command="false",  # remote `false` -> exit 1
            ssh_exit=1,
            fake_ssh_argv=captured,
        )
        rc = enclave_ssh(opts)
        assert rc == 1

    def test_arbitrary_exit_code_propagated(self) -> None:
        # Non-trivial exit code (e.g. SSH protocol error 255) must pass
        # through unchanged.
        opts = _make_opts(role="classifier", ssh_exit=255)
        assert enclave_ssh(opts) == 255

    def test_forward_agent_passes_through(self) -> None:
        captured: list[Sequence[str]] = []
        opts = _make_opts(
            role="gpu",
            forward_agent=True,
            fake_ssh_argv=captured,
        )
        assert enclave_ssh(opts) == EXIT_OK
        assert "--ssh-flag=-A" in captured[0]


# --------------------------------------------------------------------------- #
# enclave_ssh — error paths                                                   #
# --------------------------------------------------------------------------- #


class TestBadInput:
    def test_invalid_enclave_name(self, capsys: pytest.CaptureFixture[str]) -> None:
        opts = _make_opts(name="BadName!")
        rc = enclave_ssh(opts)
        assert rc == EXIT_USER_ERROR
        err = capsys.readouterr().err
        assert "invalid enclave name" in err

    def test_unknown_role_via_options(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        # argparse normally blocks this via choices=, but the dataclass
        # accepts any string; verify the function defends in depth.
        opts = _make_opts(role="database")
        rc = enclave_ssh(opts)
        assert rc == EXIT_USER_ERROR
        err = capsys.readouterr().err
        assert "unknown role 'database'" in err
        for r in VALID_ROLES:
            assert r in err


class TestMissingState:
    def test_missing_state_points_at_up(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        # Use the real read_state so we exercise the StateNotFoundError
        # path end-to-end.
        opts = SshOptions(name="demo", role="classifier", enclaves_root=tmp_path)
        rc = enclave_ssh(opts)
        assert rc == EXIT_USER_ERROR
        err = capsys.readouterr().err
        assert "tabula enclave up demo" in err

    def test_corrupt_state(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        d = tmp_path / "demo"
        d.mkdir()
        (d / "state.json").write_text("not json", encoding="utf-8")
        opts = SshOptions(name="demo", role="classifier", enclaves_root=tmp_path)
        rc = enclave_ssh(opts)
        assert rc == EXIT_USER_ERROR
        err = capsys.readouterr().err
        assert "unreadable" in err

    def test_wrong_version(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        d = tmp_path / "demo"
        d.mkdir()
        (d / "state.json").write_text(
            json.dumps(
                {
                    "version": 99,
                    "name": "demo",
                    "project_id": "p",
                    "region": "r",
                    "created_at": "now",
                    "terraform_dir": "/tmp",
                    "outputs": {},
                }
            ),
            encoding="utf-8",
        )
        opts = SshOptions(name="demo", role="classifier", enclaves_root=tmp_path)
        rc = enclave_ssh(opts)
        assert rc == EXIT_USER_ERROR
        err = capsys.readouterr().err
        assert "version" in err

    def test_full_disk_read_path_resolves_target(self, tmp_path: Path) -> None:
        # End-to-end: write a real state.json and exercise the full
        # read+resolve+ssh chain (with the ssh runner stubbed).
        state = _make_state()
        _write_state(tmp_path, state)
        captured: list[Sequence[str]] = []

        def _ssh_runner(argv: Sequence[str]) -> int:
            captured.append(list(argv))
            return 0

        def _status_probe(target: ResolvedTarget) -> str:
            return INSTANCE_RUNNING

        opts = SshOptions(
            name="demo",
            role="gpu",
            enclaves_root=tmp_path,
            status_probe=_status_probe,
            ssh_runner=_ssh_runner,
        )
        rc = enclave_ssh(opts)
        assert rc == EXIT_OK
        assert "demo-gpu" in captured[0]


# --------------------------------------------------------------------------- #
# Instance state handling                                                     #
# --------------------------------------------------------------------------- #


class TestTerminated:
    def test_refuses_terminated(self, capsys: pytest.CaptureFixture[str]) -> None:
        captured: list[Sequence[str]] = []
        opts = _make_opts(
            role="classifier",
            status=INSTANCE_TERMINATED,
            fake_ssh_argv=captured,
        )
        rc = enclave_ssh(opts)
        assert rc == EXIT_USER_ERROR
        err = capsys.readouterr().err
        assert "TERMINATED" in err
        assert "tabula enclave up demo" in err
        # Must not have shelled out to ssh.
        assert captured == []


class TestStopped:
    def test_no_start_flag_fails(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        captured: list[Sequence[str]] = []
        starts: list[ResolvedTarget] = []
        opts = _make_opts(
            role="gpu",
            status=INSTANCE_STOPPED,
            no_start=True,
            fake_ssh_argv=captured,
            fake_start_calls=starts,
        )
        rc = enclave_ssh(opts)
        assert rc == EXIT_USER_ERROR
        err = capsys.readouterr().err
        assert "STOPPED" in err
        assert "--no-start" in err
        assert starts == []  # no implicit start
        assert captured == []  # no ssh

    def test_decline_prompt(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        captured: list[Sequence[str]] = []
        starts: list[ResolvedTarget] = []
        prompts: list[str] = []
        opts = _make_opts(
            role="gpu",
            status=INSTANCE_STOPPED,
            prompt_answer=False,
            fake_ssh_argv=captured,
            fake_start_calls=starts,
            fake_prompts=prompts,
        )
        rc = enclave_ssh(opts)
        assert rc == EXIT_DECLINED
        assert prompts, "prompt should have been shown"
        assert starts == []
        assert captured == []

    def test_accept_prompt_starts_then_sshes(self) -> None:
        captured: list[Sequence[str]] = []
        starts: list[ResolvedTarget] = []
        prompts: list[str] = []
        opts = _make_opts(
            role="gpu",
            status=INSTANCE_STOPPED,
            prompt_answer=True,
            fake_ssh_argv=captured,
            fake_start_calls=starts,
            fake_prompts=prompts,
        )
        rc = enclave_ssh(opts)
        assert rc == EXIT_OK
        assert len(starts) == 1
        assert starts[0].instance == "demo-gpu"
        assert len(captured) == 1
        assert "demo-gpu" in captured[0]

    def test_start_failure_propagates(self) -> None:
        captured: list[Sequence[str]] = []
        starts: list[ResolvedTarget] = []
        opts = _make_opts(
            role="gpu",
            status=INSTANCE_STOPPED,
            prompt_answer=True,
            start_exit=42,
            fake_ssh_argv=captured,
            fake_start_calls=starts,
        )
        rc = enclave_ssh(opts)
        assert rc == 42
        assert starts == [
            ResolvedTarget(
                instance="demo-gpu",
                zone="us-central1-a",
                project="tabula-demo-123",
                role="gpu",
            )
        ]
        # No ssh attempt after a failed start.
        assert captured == []


class TestStatusProbeError:
    def test_runtime_error_surfaces_as_user_error(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        def _fail(_t: ResolvedTarget) -> str:
            raise RuntimeError("gcloud auth boom")

        opts = SshOptions(
            name="demo",
            role="classifier",
            state_reader=lambda n, r: _make_state(),
            status_probe=_fail,
        )
        rc = enclave_ssh(opts)
        assert rc == EXIT_USER_ERROR
        assert "gcloud auth boom" in capsys.readouterr().err


# --------------------------------------------------------------------------- #
# gcloud-not-on-PATH                                                          #
# --------------------------------------------------------------------------- #


class TestGcloudNotInstalled:
    def test_missing_gcloud_is_user_error(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # Override the autouse fixture: pretend gcloud is missing.
        monkeypatch.setattr(ssh_mod.shutil, "which", lambda _name: None)

        state = _make_state()
        _write_state(tmp_path, state)

        opts = SshOptions(
            name="demo",
            role="classifier",
            enclaves_root=tmp_path,
        )
        rc = enclave_ssh(opts)
        assert rc == EXIT_USER_ERROR
        err = capsys.readouterr().err
        assert "gcloud" in err


# --------------------------------------------------------------------------- #
# Argparse wiring                                                             #
# --------------------------------------------------------------------------- #


class TestArgparseWiring:
    def _build_parser(self) -> argparse.ArgumentParser:
        parser = argparse.ArgumentParser(prog="test")
        sub = parser.add_subparsers(dest="cmd", required=True)
        ssh_mod.add_subparser(sub)
        return parser

    def test_minimal_args(self) -> None:
        parser = self._build_parser()
        args = parser.parse_args(["ssh", "demo", "classifier"])
        assert args.name == "demo"
        assert args.role == "classifier"
        assert args.command is None
        assert args.forward_agent is False
        assert args.no_start is False

    def test_command_arg(self) -> None:
        parser = self._build_parser()
        args = parser.parse_args(
            ["ssh", "demo", "gpu", "--command", "nvidia-smi"]
        )
        assert args.command == "nvidia-smi"

    def test_forward_agent_arg(self) -> None:
        parser = self._build_parser()
        args = parser.parse_args(["ssh", "demo", "gitea", "--forward-agent"])
        assert args.forward_agent is True

    def test_no_start_arg(self) -> None:
        parser = self._build_parser()
        args = parser.parse_args(["ssh", "demo", "gpu", "--no-start"])
        assert args.no_start is True

    def test_role_choices_enforced(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        parser = self._build_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(["ssh", "demo", "database"])
        err = capsys.readouterr().err
        # argparse lists valid choices on a choices= violation.
        assert "classifier" in err
        assert "gpu" in err
        assert "gitea" in err

    def test_run_adapter(self, tmp_path: Path) -> None:
        # Verify the argparse->run adapter wires SshOptions correctly.
        # We monkeypatch enclave_ssh to capture what it receives.
        captured: list[SshOptions] = []

        def _fake(opts: SshOptions) -> int:
            captured.append(opts)
            return 0

        parser = self._build_parser()
        args = parser.parse_args(
            ["ssh", "demo", "gpu", "--command", "uptime", "--forward-agent"]
        )

        from tabula_cli.enclave import ssh as ssh_module
        original = ssh_module.enclave_ssh
        try:
            ssh_module.enclave_ssh = _fake  # type: ignore[assignment]
            rc = ssh_mod.run(args)
        finally:
            ssh_module.enclave_ssh = original  # type: ignore[assignment]

        assert rc == 0
        assert len(captured) == 1
        opts = captured[0]
        assert opts.name == "demo"
        assert opts.role == "gpu"
        assert opts.command == "uptime"
        assert opts.forward_agent is True
        assert opts.no_start is False
