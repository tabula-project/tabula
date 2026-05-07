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
    server_public_key: bytes,
    claude_cmd: list[str],
    state_dir: Path,
) -> tuple[Any, asyncio.Task[Any]]:
    """Spawn the canonical server entrypoint as an asyncio task.

    Returns ``(server_handle, task)`` where ``server_handle`` is whatever the
    server's ``serve`` / ``listen`` factory returns (typically an object with a
    ``close()`` method or an ``asyncio.Server``). The integration test relies
    on the canonical layout from #57:

    * ``tabula_wire.server.listener.serve(...)`` is the entrypoint
    * It accepts at least: ``host``, ``port``, ``private_key``, ``public_key``,
      ``claude_cmd``, and ``state_dir``
    * It binds before returning so the test can connect immediately

    If the actual signature drifts from this shape we adapt by introspection
    here — better to localize the shim than to refuse the test outright.
    """
    from tabula_wire.server import listener as _listener  # type: ignore[import-not-found]

    # Try the most likely entrypoint names in order of preference.
    factory = None
    for name in ("serve", "start_server", "listen", "run"):
        if hasattr(_listener, name):
            factory = getattr(_listener, name)
            break
    if factory is None:
        pytest.skip(
            "tabula_wire.server.listener has no recognized entrypoint "
            "(serve/start_server/listen/run); update the harness once #20 lands"
        )

    coro = factory(
        host=bind_host,
        port=bind_port,
        private_key=server_private_key,
        public_key=server_public_key,
        claude_cmd=claude_cmd,
        state_dir=str(state_dir),
    )
    if asyncio.iscoroutine(coro):
        # Some signatures return the server directly when awaited; others
        # return a long-running task. We start a task either way and let the
        # test's wait_for() loop confirm readiness via the TCP probe.
        task = asyncio.create_task(coro)
        # Give the listener a tick to bind.
        await asyncio.sleep(0)
        return None, task
    # Already-bound server object.
    return coro, asyncio.create_task(asyncio.sleep(0))


async def _send_chat_round_trip(
    *,
    server_host: str,
    server_port: int,
    server_pubkey: bytes,
    client_private_key: bytes,
    client_public_key: bytes,
    pinning_store_path: Path,
    prompt: str,
) -> list[str]:
    """Drive the canonical client programmatically and collect streamed tokens.

    Returns the list of assistant token texts received before
    ``AssistantTurnEnd``. Skips the test if the canonical client API is not
    importable or doesn't expose the expected entrypoints.
    """
    try:
        from tabula_wire.client import dialer as _dialer  # type: ignore[import-not-found]
        from tabula_wire.client import pinning as _pinning  # type: ignore[import-not-found]
    except Exception as exc:
        pytest.skip(f"client side of wire stack not importable: {exc!r}")

    # Pre-populate the pinning store with the server's public key. The
    # canonical pinning module (#32) is expected to expose either an
    # ``add(host, port, pubkey)`` method on a store class or a free function
    # ``pin_server(...)``. We try both.
    pinned = False
    for klass_name in ("PinningStore", "ServerPinningStore", "PinStore"):
        klass = getattr(_pinning, klass_name, None)
        if klass is None:
            continue
        try:
            store = klass(pinning_store_path)
        except TypeError:
            store = klass(path=pinning_store_path)
        for meth in ("add", "pin", "set"):
            if hasattr(store, meth):
                getattr(store, meth)("demo", server_host, server_port, server_pubkey)
                pinned = True
                break
        if hasattr(store, "save"):
            store.save()
        if pinned:
            break
    if not pinned:
        for fn_name in ("pin_server", "add_pin", "write_pin"):
            fn = getattr(_pinning, fn_name, None)
            if fn is not None:
                fn(pinning_store_path, "demo", server_host, server_port, server_pubkey)
                pinned = True
                break
    if not pinned:
        pytest.skip(
            "tabula_wire.client.pinning has no recognized pin-add API "
            "(PinningStore.add / pin_server); update the harness once #32 lands"
        )

    # Dial and run the round-trip. Same kind of introspective shim — the
    # canonical layout fixes the import path; the exact function name is
    # finalized in #27.
    dial = None
    for name in ("connect", "dial", "open_session"):
        if hasattr(_dialer, name):
            dial = getattr(_dialer, name)
            break
    if dial is None:
        pytest.skip(
            "tabula_wire.client.dialer has no recognized entrypoint; "
            "update the harness once #27 lands"
        )

    session = await dial(
        alias="demo",
        host=server_host,
        port=server_port,
        client_private_key=client_private_key,
        client_public_key=client_public_key,
        pinning_store_path=pinning_store_path,
    )

    tokens: list[str] = []

    # The client API is expected to expose ``send_user_message(text)`` and
    # ``stream_assistant_tokens()`` (async iterator) followed by
    # ``end_session()``. If the names differ we try a few variants.
    send = None
    for name in ("send_user_message", "send_prompt", "send"):
        if hasattr(session, name):
            send = getattr(session, name)
            break
    if send is None:
        pytest.skip("session object has no recognized send_user_message method")

    stream = None
    for name in ("stream_assistant_tokens", "stream_tokens", "stream", "tokens"):
        if hasattr(session, name):
            stream = getattr(session, name)
            break
    if stream is None:
        pytest.skip("session object has no recognized assistant token stream")

    end = None
    for name in ("end_session", "close", "shutdown"):
        if hasattr(session, name):
            end = getattr(session, name)
            break

    await send(prompt)
    async for frame in stream():
        # Frames may be raw protobuf messages or simple dicts; handle both.
        text = None
        kind = None
        if hasattr(frame, "WhichOneof"):
            kind = frame.WhichOneof("payload")
            payload = getattr(frame, kind, None) if kind else None
            text = getattr(payload, "text", None) if payload else None
        elif isinstance(frame, dict):
            kind = frame.get("type") or frame.get("kind")
            text = frame.get("text") or (frame.get("token") or {}).get("text")
        if kind in ("assistant_turn_end", "AssistantTurnEnd", "turn_end"):
            break
        if text:
            tokens.append(text)
    if end is not None:
        result = end()
        if asyncio.iscoroutine(result):
            await result
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
    client_sk, client_pk = generate_x25519_keypair()

    # Allocate ports: one for the server's actual TCP listen, one for the tee.
    server_port = ephemeral_port()
    state_dir = tmp_path / "server-state"
    state_dir.mkdir()
    pinning_store = tmp_path / "pins.json"

    server_handle, server_task = await _spawn_server(
        bind_host="127.0.0.1",
        bind_port=server_port,
        server_private_key=server_sk,
        server_public_key=server_pk,
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
                client_public_key=client_pk,
                pinning_store_path=pinning_store,
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
    client_sk, client_pk = generate_x25519_keypair()

    server_port = ephemeral_port()
    state_dir = tmp_path / "server-state"
    state_dir.mkdir()
    pinning_store = tmp_path / "pins.json"

    claude_bin = shutil.which("claude")
    assert claude_bin is not None  # guarded by skipif

    server_handle, server_task = await _spawn_server(
        bind_host="127.0.0.1",
        bind_port=server_port,
        server_private_key=server_sk,
        server_public_key=server_pk,
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
                client_public_key=client_pk,
                pinning_store_path=pinning_store,
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
