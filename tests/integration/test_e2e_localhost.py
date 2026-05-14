"""Localhost end-to-end integration test for the chat MVP demo (#34).

This is the gate test for Epic #13: it spins up the server (listener +
session manager + claude driver) on localhost, runs the client (dialer + UI)
against it, sends a real prompt over a real Noise XX session, pipes the
decrypted prompt into a ``claude`` subprocess, and streams the response back
to the caller's stdout.

The test is intentionally tolerant of in-flight dependencies: if the canonical
``tabula_wire`` layout from #57 is not yet importable (because some sub-issue
has not landed on ``main``) the body skips with a clear diagnostic listing
the missing modules. This lets the harness — and the unit tests in
``test_harness.py`` — keep running on every PR while the wire stack lands
piece by piece.

Two test modes are supported for the ``claude`` subprocess:

* **Default (CI):** the deterministic ``fake_claude.sh`` shell fixture under
  ``tests/integration/fixtures/``. No Vertex AI dependency, no network.
* **Opt-in (local dev only):** when ``TABULA_E2E_REAL_CLAUDE=1`` is set in
  the environment, the real ``claude`` CLI is invoked locally as a
  subprocess. This mode is **never** active in CI.

To run the real-claude variant locally:

.. code-block:: bash

    TABULA_E2E_REAL_CLAUDE=1 pytest tests/integration/test_e2e_localhost.py -k real_claude -s

Plaintext is intercepted at the TCP layer via ``SocketTee``; after a
successful round-trip the test asserts that a known unique prompt phrase
and a known unique response phrase **never** appeared in plaintext on the
wire — only ciphertext.
"""

from __future__ import annotations

import asyncio
import os
import shutil
from pathlib import Path
from typing import Any

import pytest

from tests.integration.conftest import (
    SocketTee,
    _wire_stack_available,
    ephemeral_port,
    generate_x25519_keypair,
    wait_for,
)

pytestmark = pytest.mark.asyncio

# A unique, unusual phrase. We send it as the prompt so the wire-plaintext
# assertion has something specific to look for. Picking unusual words makes
# accidental false-negatives (collisions with framing bytes) unlikely.
UNIQUE_PROMPT_PHRASE = "kaleidoscope-velveteen-petrichor-okra-zenith"

# Matches the default phrase emitted by ``fake_claude.sh``.
EXPECTED_RESPONSE_PHRASE_TOKENS = [
    "zephyr",
    "quokka",
    "prism",
    "mango",
    "vortex",
    "glissando",
]


# ---------------------------------------------------------------------------
# Skip conditions
# ---------------------------------------------------------------------------


def _skip_if_wire_stack_unavailable() -> None:
    ok, reason = _wire_stack_available()
    if not ok:
        pytest.skip(reason)


def _real_claude_requested() -> bool:
    return os.environ.get("TABULA_E2E_REAL_CLAUDE") == "1"


def _real_claude_available() -> bool:
    return shutil.which("claude") is not None


# ---------------------------------------------------------------------------
# Helper: spawn the server in-process
# ---------------------------------------------------------------------------


async def _spawn_server(
    *,
    bind_host: str,
    bind_port: int,
    server_private_key: bytes,
    claude_cmd: list[str],
    state_dir: Path,
) -> tuple[Any, asyncio.Task[Any]]:
    """Spawn the canonical server entrypoint in-process.

    Uses ``tabula_wire.server.main.serve_with_sessions`` — the high-level
    composition of listener + session manager + claude driver from issue
    #25 (post-#57 canonical layout). Returns ``(server_handle, task)`` where
    ``server_handle`` is the ``asyncio.Server`` bound to ``bind_port`` and
    ``task`` is a placeholder coroutine (kept for backwards-compat with the
    existing teardown shape — the real server lifecycle is controlled via
    ``server_handle.close()``).

    The per-session ``claude`` subprocess is constructed via
    ``ClaudeProcess(argv_override=tuple(claude_cmd))``; for the fake-claude
    fixture this means ``["fake_claude.sh"]``.
    """
    from tabula_wire.server.claude_driver import ClaudeProcess
    from tabula_wire.server.config import (
        PROTOCOL_VERSION,
        ServerConfig,
    )
    from tabula_wire.server.main import serve_with_sessions

    claude_argv = tuple(claude_cmd)

    async def _claude_factory(config: ServerConfig) -> ClaudeProcess:
        proc = ClaudeProcess(argv_override=claude_argv)
        await proc.start(cwd=config.cwd)
        return proc

    config = ServerConfig(
        cwd=state_dir,
        max_concurrent_sessions=4,
        protocol_version=PROTOCOL_VERSION,
        server_version="tabula-server/e2e-test",
    )
    server = await serve_with_sessions(
        host=bind_host,
        port=bind_port,
        static_key=bytes(server_private_key),
        claude_factory=_claude_factory,
        config=config,
    )
    # No long-running task — the listener manages its own accept loop. We
    # return a no-op task so the caller's teardown remains symmetric.
    placeholder = asyncio.create_task(asyncio.sleep(0))
    return server, placeholder


async def _send_chat_round_trip(
    *,
    server_host: str,
    server_port: int,
    server_pubkey: bytes,
    client_private_key: bytes,
    prompt: str,
) -> list[str]:
    """Drive the canonical client programmatically and collect streamed tokens.

    Uses ``tabula_wire.client.dialer.connect`` to perform the Noise XX
    handshake + pinning check, then drives the protobuf-framed chat
    protocol (Hello → Welcome → UserMessage → AssistantToken* →
    AssistantTurnEnd → EndSession). Returns the list of token texts.

    The pinning store is bypassed here: ``connect`` takes the expected
    server pubkey directly. The store is only needed by the CLI (which
    looks up host/port/pubkey by alias); for the programmatic harness we
    already have all three.
    """
    from tabula_wire.client.dialer import connect
    from tabula_wire.crypto.keys import SecretKey
    from tabula_wire.proto.v1 import (
        ClientFrame,
        EndSession,
        Hello,
        UserMessage,
    )

    PROTOCOL_VERSION = 1

    channel = await connect(
        host=server_host,
        port=server_port,
        local_static_key=SecretKey(raw=bytes(client_private_key)),
        expected_remote_static_pubkey=bytes(server_pubkey),
        connect_timeout_s=5.0,
    )

    tokens: list[str] = []
    try:
        await channel.send(
            ClientFrame(hello=Hello(protocol_version=PROTOCOL_VERSION))
        )
        welcome_frame = await channel.recv()
        which = welcome_frame.WhichOneof("payload")
        if which != "welcome":
            raise AssertionError(
                f"expected Welcome from server, got {which!r}: {welcome_frame!r}"
            )

        await channel.send(ClientFrame(user_message=UserMessage(text=prompt)))

        while True:
            frame = await channel.recv()
            which = frame.WhichOneof("payload")
            if which == "token":
                tokens.append(frame.token.text)
            elif which == "turn_end":
                break
            elif which == "error":
                raise AssertionError(
                    f"server error frame: code={frame.error.code} "
                    f"message={frame.error.message!r}"
                )
            else:
                raise AssertionError(
                    f"unexpected server frame during streaming: {which!r}"
                )

        await channel.send(ClientFrame(end=EndSession(reason="eof")))
    finally:
        await channel.close()
    return tokens


# ---------------------------------------------------------------------------
# The actual e2e test
# ---------------------------------------------------------------------------


async def test_e2e_localhost_fake_claude(
    tmp_path: Path,
    fake_claude_path: Path,
) -> None:
    """One full round-trip with the deterministic fake-claude fixture.

    Wire layout: ``client → SocketTee → server → fake-claude subprocess``.
    The tee records every byte in both directions so we can prove no plaintext
    leaked on the wire.
    """
    _skip_if_wire_stack_unavailable()

    # Generate fresh keypairs for both ends.
    server_sk, server_pk = generate_x25519_keypair()
    client_sk, _client_pk = generate_x25519_keypair()

    # Allocate ports: one for the server's actual TCP listen, one for the tee.
    server_port = ephemeral_port()
    state_dir = tmp_path / "server-state"
    state_dir.mkdir()

    server_handle, server_task = await _spawn_server(
        bind_host="127.0.0.1",
        bind_port=server_port,
        server_private_key=server_sk,
        claude_cmd=[str(fake_claude_path)],
        state_dir=state_dir,
    )

    try:
        # Wait for the server to be reachable.
        async def _ready() -> bool:
            import socket as _s

            try:
                with _s.create_connection(("127.0.0.1", server_port), timeout=0.2):
                    return True
            except OSError:
                return False

        await wait_for(_ready, timeout=5.0, description="server listener")

        with SocketTee(upstream_port=server_port) as tee:
            prompt = f"{UNIQUE_PROMPT_PHRASE} — please respond"
            tokens = await _send_chat_round_trip(
                server_host="127.0.0.1",
                server_port=tee.bound_port,
                server_pubkey=server_pk,
                client_private_key=client_sk,
                prompt=prompt,
            )

            joined = "-".join(tokens)
            for tok in EXPECTED_RESPONSE_PHRASE_TOKENS:
                assert tok in joined, (
                    f"expected token {tok!r} in streamed response, "
                    f"got tokens={tokens!r}"
                )

            # Wire-plaintext assertion: neither the prompt phrase nor the
            # response phrase may appear unencrypted on the wire.
            tee.assert_no_plaintext(
                UNIQUE_PROMPT_PHRASE,
                "-".join(EXPECTED_RESPONSE_PHRASE_TOKENS),
            )
    finally:
        # Best-effort shutdown.
        if server_handle is not None and hasattr(server_handle, "close"):
            try:
                result = server_handle.close()
                if asyncio.iscoroutine(result):
                    await result
            except Exception:  # noqa: BLE001
                pass
        server_task.cancel()
        try:
            await server_task
        except (asyncio.CancelledError, Exception):  # noqa: BLE001
            pass


@pytest.mark.skipif(
    not _real_claude_requested(),
    reason="set TABULA_E2E_REAL_CLAUDE=1 to run against the real claude CLI",
)
@pytest.mark.skipif(
    not _real_claude_available(),
    reason="real `claude` CLI is not on PATH",
)
async def test_e2e_localhost_real_claude(
    tmp_path: Path,
) -> None:
    """One full round-trip against the **real** local ``claude`` CLI.

    Gated behind ``TABULA_E2E_REAL_CLAUDE=1`` so it never runs in CI. Run
    locally with::

        TABULA_E2E_REAL_CLAUDE=1 pytest tests/integration/test_e2e_localhost.py \\
            -k real_claude -s

    The test only asserts that *some* non-empty response stream comes back —
    the real model's exact wording is not deterministic, so we cannot match a
    fixed phrase. We still assert that the prompt phrase is not echoed in
    plaintext on the wire.
    """
    _skip_if_wire_stack_unavailable()

    server_sk, server_pk = generate_x25519_keypair()
    client_sk, _client_pk = generate_x25519_keypair()

    server_port = ephemeral_port()
    state_dir = tmp_path / "server-state"
    state_dir.mkdir()

    claude_bin = shutil.which("claude")
    assert claude_bin is not None  # guarded by skipif

    server_handle, server_task = await _spawn_server(
        bind_host="127.0.0.1",
        bind_port=server_port,
        server_private_key=server_sk,
        # Minimal invocation: the real claude CLI's argument shape is owned
        # by #22's claude driver; here we just pass the executable path and
        # let the driver pick the right flags.
        claude_cmd=[claude_bin],
        state_dir=state_dir,
    )

    try:
        async def _ready() -> bool:
            import socket as _s

            try:
                with _s.create_connection(("127.0.0.1", server_port), timeout=0.2):
                    return True
            except OSError:
                return False

        await wait_for(_ready, timeout=5.0, description="server listener")

        with SocketTee(upstream_port=server_port) as tee:
            prompt = f"{UNIQUE_PROMPT_PHRASE} — please describe yourself in one sentence."
            tokens = await _send_chat_round_trip(
                server_host="127.0.0.1",
                server_port=tee.bound_port,
                server_pubkey=server_pk,
                client_private_key=client_sk,
                prompt=prompt,
            )

            assert tokens, "real claude returned no streamed tokens"
            tee.assert_no_plaintext(UNIQUE_PROMPT_PHRASE)
    finally:
        if server_handle is not None and hasattr(server_handle, "close"):
            try:
                result = server_handle.close()
                if asyncio.iscoroutine(result):
                    await result
            except Exception:  # noqa: BLE001
                pass
        server_task.cancel()
        try:
            await server_task
        except (asyncio.CancelledError, Exception):  # noqa: BLE001
            pass
