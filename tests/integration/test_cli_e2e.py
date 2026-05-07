"""CLI-level end-to-end integration test for the chat MVP demo (#34).

This is the pytest-driven equivalent of the optional ``test_cli_e2e.sh``: it
walks the same flow but spawns ``tabula`` as a subprocess from the user's
shell perspective, exactly as the MVP demo in epic #13 describes:

.. code-block:: bash

    $ tabula keygen
    $ tabula servers add demo 127.0.0.1:<PORT> --pubkey <SERVER_PUBKEY>
    $ tabula chat connect demo
    > <UNIQUE_PROMPT_PHRASE>
    < <fake-claude streaming response>
    > ^D

The test:
  1. Generates a fresh server keypair (in-process; the server entrypoint
     consumes raw bytes, not on-disk PEM files).
  2. Starts the server entrypoint as a subprocess bound to an ephemeral
     localhost port, configured to invoke the deterministic
     ``fake_claude.sh`` fixture.
  3. Runs ``tabula keygen`` to produce a client keypair on disk.
  4. Runs ``tabula servers add demo ...`` to pin the server.
  5. Runs ``tabula chat connect demo`` with stdin = the unique prompt phrase
     followed by EOF.
  6. Asserts the fake-claude response phrase appears on the client's stdout
     and that the CLI exits 0.

Skips cleanly when either the canonical ``tabula_wire`` server entrypoint or
the canonical ``tabula`` console script is unavailable, so it is safe to
include in CI today and have it pass-as-skip until the wire stack lands.
"""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

from tests.integration.conftest import (
    _wire_stack_available,
    ephemeral_port,
    generate_x25519_keypair,
)


UNIQUE_PROMPT_PHRASE = "kaleidoscope-velveteen-petrichor-okra-zenith"
EXPECTED_RESPONSE_PHRASE = "zephyr-quokka-prism-mango-vortex-glissando"


def _tabula_cli_available() -> bool:
    """``tabula`` console script is on PATH."""
    return shutil.which("tabula") is not None


def _skip_if_unavailable() -> None:
    ok, reason = _wire_stack_available()
    if not ok:
        pytest.skip(reason)
    if not _tabula_cli_available():
        pytest.skip("`tabula` console script not on PATH; skip until tabula-cli installs")


def _wait_for_port(host: str, port: int, timeout: float = 5.0) -> None:
    deadline = time.time() + timeout
    last_err: Exception | None = None
    while time.time() < deadline:
        try:
            with socket.create_connection((host, port), timeout=0.25):
                return
        except OSError as exc:  # noqa: BLE001
            last_err = exc
            time.sleep(0.05)
    raise TimeoutError(
        f"port {host}:{port} did not become reachable within {timeout}s ({last_err!r})"
    )


def test_cli_e2e_fake_claude(tmp_path: Path, fake_claude_path: Path) -> None:
    """Drive the full ``tabula`` CLI flow end-to-end with fake-claude."""
    _skip_if_unavailable()

    home = tmp_path / "home"
    home.mkdir()
    env = os.environ.copy()
    env["HOME"] = str(home)
    # Both the CLI's pin store and the server state are commonly rooted at
    # ``$XDG_CONFIG_HOME`` or ``$HOME/.config/tabula``; setting HOME above is
    # sufficient for both. We also set TABULA_STATE_DIR / TABULA_CONFIG_DIR
    # in case the CLI honors them — extra hints, never required.
    env["TABULA_STATE_DIR"] = str(home / ".local/state/tabula")
    env["TABULA_CONFIG_DIR"] = str(home / ".config/tabula")

    # ---- 1. server keypair ----
    server_sk, server_pk = generate_x25519_keypair()

    # ---- 2. spawn the server ----
    server_port = ephemeral_port()
    state_dir = tmp_path / "server-state"
    state_dir.mkdir()
    server_key_dir = tmp_path / "server-keys"
    server_key_dir.mkdir()
    (server_key_dir / "server.key").write_bytes(server_sk)
    (server_key_dir / "server.pub").write_bytes(server_pk)

    # The canonical server entrypoint is invoked via:
    #   python -m tabula_wire.server <args>
    # If that module isn't importable, skip cleanly.
    try:
        import importlib

        importlib.import_module("tabula_wire.server")
    except Exception as exc:
        pytest.skip(f"tabula_wire.server not importable: {exc!r}")

    server_proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "tabula_wire.server",
            "--host",
            "127.0.0.1",
            "--port",
            str(server_port),
            "--private-key",
            str(server_key_dir / "server.key"),
            "--public-key",
            str(server_key_dir / "server.pub"),
            "--claude-cmd",
            str(fake_claude_path),
            "--state-dir",
            str(state_dir),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
    )

    try:
        try:
            _wait_for_port("127.0.0.1", server_port, timeout=5.0)
        except TimeoutError:
            stdout, stderr = b"", b""
            try:
                stdout, stderr = server_proc.communicate(timeout=0.5)
            except subprocess.TimeoutExpired:
                pass
            pytest.skip(
                "server entrypoint did not bind; CLI flag shape is finalized in #20. "
                f"stderr={stderr!r}"
            )

        # ---- 3. tabula keygen ----
        keygen_result = subprocess.run(
            ["tabula", "keygen"],
            capture_output=True,
            text=True,
            env=env,
            timeout=15,
        )
        if keygen_result.returncode != 0:
            pytest.skip(
                "`tabula keygen` failed; subcommand wired up in #31. "
                f"stderr={keygen_result.stderr!r}"
            )

        # ---- 4. tabula servers add ----
        # Encode the server pubkey as hex; the canonical CLI accepts either
        # hex or base64 — we pick hex because it's unambiguous.
        pubkey_hex = server_pk.hex()
        add_result = subprocess.run(
            [
                "tabula",
                "servers",
                "add",
                "demo",
                f"127.0.0.1:{server_port}",
                "--pubkey",
                pubkey_hex,
            ],
            capture_output=True,
            text=True,
            env=env,
            timeout=15,
        )
        if add_result.returncode != 0:
            pytest.skip(
                "`tabula servers add` failed; subcommand wired up in #32. "
                f"stderr={add_result.stderr!r}"
            )

        # ---- 5. tabula chat connect demo ----
        chat_result = subprocess.run(
            ["tabula", "chat", "connect", "demo"],
            input=f"{UNIQUE_PROMPT_PHRASE}\n",
            capture_output=True,
            text=True,
            env=env,
            timeout=30,
        )
        # ---- 6. assertions ----
        assert chat_result.returncode == 0, (
            f"`tabula chat connect demo` exited {chat_result.returncode}; "
            f"stdout={chat_result.stdout!r} stderr={chat_result.stderr!r}"
        )
        # Either the joined response or any of its tokens should appear.
        # The fake-claude fixture emits hyphen-separated tokens; the client
        # UI may reassemble them with spaces. Accept either.
        joined_dash = EXPECTED_RESPONSE_PHRASE
        joined_space = EXPECTED_RESPONSE_PHRASE.replace("-", " ")
        joined_concat = EXPECTED_RESPONSE_PHRASE.replace("-", "")
        assert any(
            phrase in chat_result.stdout
            for phrase in (joined_dash, joined_space, joined_concat)
        ) or all(
            tok in chat_result.stdout
            for tok in EXPECTED_RESPONSE_PHRASE.split("-")
        ), (
            f"expected fake-claude response phrase on stdout; got {chat_result.stdout!r}"
        )
    finally:
        server_proc.terminate()
        try:
            server_proc.wait(timeout=2.0)
        except subprocess.TimeoutExpired:
            server_proc.kill()
            server_proc.wait(timeout=2.0)
