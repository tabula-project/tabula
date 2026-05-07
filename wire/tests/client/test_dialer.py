"""Tests for ``tabula_wire.client.dialer``.

Acceptance-criteria coverage map (issue #27):

- Loopback test: real handshake + frames both directions
    -> ``test_loopback_handshake_and_round_trip``
- Wrong pinned key -> ``ServerKeyMismatch``
    -> ``test_wrong_pinned_key_raises_server_key_mismatch``
- Connection refused -> typed exception
    -> ``test_connection_refused``
- Connect timeout (server accepts TCP, never completes handshake)
    -> ``test_connect_timeout_when_server_stalls_post_accept``
    -> ``test_connect_timeout_when_server_drops_after_msg1``
- Server-side malformed frame mid-stream -> ``ProtocolError``
    -> ``test_garbage_after_handshake_raises_protocol_error``
- ``close()`` during pending ``recv()`` cancels cleanly
    -> ``test_close_during_pending_recv``
- ``close()`` idempotent
    -> ``test_close_is_idempotent``

Plus extras for confidence:

- DNS error path
    -> ``test_dns_error``
- Async iterator over ``recv``
    -> ``test_async_iterator``
"""

from __future__ import annotations

import asyncio
import socket

import pytest

from tabula_wire.client import (
    ChatChannel,
    ConnectRefused,
    ConnectTimeout,
    DnsError,
    ProtocolError,
    ServerKeyMismatch,
    connect,
)
from tabula_wire.crypto import generate_keypair
from tabula_wire.proto.v1 import (
    AssistantToken,
    AssistantTurnEnd,
    ClientFrame,
    ServerFrame,
    UserMessage,
)

from ._test_server import FakeServer, echo_session, send_then_close


# pytest-asyncio: mode=auto via pyproject; mark tests explicitly anyway.
pytestmark = pytest.mark.asyncio


@pytest.fixture
async def server_keypair():
    return generate_keypair()


@pytest.fixture
async def client_keypair():
    return generate_keypair()


@pytest.fixture
async def server(server_keypair):
    s = FakeServer(static_key=server_keypair)
    await s.start()
    try:
        yield s
    finally:
        await s.stop()


def _server_pubkey_bytes(secret) -> bytes:
    return secret.public_key().raw


async def test_loopback_handshake_and_round_trip(
    server, server_keypair, client_keypair
):
    """End-to-end: real Noise XX handshake on loopback, frames each way."""
    server.on_session = echo_session

    server_pub = _server_pubkey_bytes(server_keypair)

    ch = await connect(
        host="127.0.0.1",
        port=server.port,
        local_static_key=client_keypair,
        expected_remote_static_pubkey=server_pub,
    )

    try:
        assert isinstance(ch, ChatChannel)
        assert ch.remote_static_pubkey == server_pub
        assert not ch.closed

        # Send three frames, expect three echoes (text-in, text-out via
        # AssistantToken).
        for i in range(3):
            await ch.send(
                ClientFrame(
                    user_message=UserMessage(text=f"hi-{i}", turn_id=f"t{i}")
                )
            )
            resp = await ch.recv()
            assert isinstance(resp, ServerFrame)
            assert resp.WhichOneof("payload") == "token"
            assert resp.token.text == f"hi-{i}"
            assert resp.token.turn_id == f"t{i}"

        # Larger payload to exercise framing on a non-trivial size.
        big = "x" * 4096
        await ch.send(
            ClientFrame(user_message=UserMessage(text=big, turn_id="big"))
        )
        resp = await ch.recv()
        assert resp.token.text == big
    finally:
        await ch.close()
        assert ch.closed


async def test_wrong_pinned_key_raises_server_key_mismatch(
    server, server_keypair, client_keypair
):
    """Handshake completes but the pinned pubkey is wrong -> typed error.

    Acceptance criterion: socket is closed before any application frames
    are read or written.
    """
    server.on_session = echo_session

    bogus_pubkey = _server_pubkey_bytes(generate_keypair())
    assert bogus_pubkey != _server_pubkey_bytes(server_keypair)

    with pytest.raises(ServerKeyMismatch):
        await connect(
            host="127.0.0.1",
            port=server.port,
            local_static_key=client_keypair,
            expected_remote_static_pubkey=bogus_pubkey,
        )


async def test_connection_refused(client_keypair, server_keypair):
    """No listener on the chosen port -> ``ConnectRefused``."""
    # Allocate then close a socket to grab a port nothing is listening on.
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()

    with pytest.raises(ConnectRefused):
        await connect(
            host="127.0.0.1",
            port=port,
            local_static_key=client_keypair,
            expected_remote_static_pubkey=_server_pubkey_bytes(server_keypair),
            connect_timeout_s=2.0,
        )


async def test_connect_timeout_when_server_stalls_post_accept(
    server, server_keypair, client_keypair
):
    """Server accepts the TCP connection but never reads/writes -> timeout.

    Acceptance criterion: typed timeout exception, socket closed.
    """
    server.refuse_after_accept = True

    with pytest.raises(ConnectTimeout):
        await connect(
            host="127.0.0.1",
            port=server.port,
            local_static_key=client_keypair,
            expected_remote_static_pubkey=_server_pubkey_bytes(server_keypair),
            connect_timeout_s=0.3,
        )

    # Server saw the accept (its handler started) so the connection was
    # established at the TCP layer before we timed out.
    assert server.sessions_started == 1


async def test_connect_timeout_when_server_drops_after_msg1(
    server, server_keypair, client_keypair
):
    """Server accepts msg1 then drops -> client times out."""
    server.skip_handshake_msg2 = True

    with pytest.raises((ConnectTimeout, ProtocolError)):
        # Either timeout (client still awaiting msg2 when wait_for fires) or
        # ProtocolError (server already closed -> EOF). Both are acceptable;
        # the AC requires *a* typed exception and the socket closed cleanly.
        await connect(
            host="127.0.0.1",
            port=server.port,
            local_static_key=client_keypair,
            expected_remote_static_pubkey=_server_pubkey_bytes(server_keypair),
            connect_timeout_s=0.3,
        )


async def test_garbage_after_handshake_raises_protocol_error(
    server, server_keypair, client_keypair
):
    """Server sends a correctly-framed but undecryptable blob -> ProtocolError.

    Also asserts the channel is closed afterwards (no half-open state).
    """
    server.inject_garbage_after_handshake = True

    ch = await connect(
        host="127.0.0.1",
        port=server.port,
        local_static_key=client_keypair,
        expected_remote_static_pubkey=_server_pubkey_bytes(server_keypair),
    )

    with pytest.raises(ProtocolError):
        await ch.recv()
    assert ch.closed


async def test_close_during_pending_recv(
    server, server_keypair, client_keypair
):
    """``close()`` while a ``recv()`` is awaiting bytes returns cleanly."""

    async def _idle_session(noise, reader, writer):
        # Don't send anything; just wait for the client to give up.
        try:
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            pass

    server.on_session = _idle_session

    ch = await connect(
        host="127.0.0.1",
        port=server.port,
        local_static_key=client_keypair,
        expected_remote_static_pubkey=_server_pubkey_bytes(server_keypair),
    )

    recv_task = asyncio.create_task(ch.recv())
    # Let the recv actually start awaiting the socket.
    await asyncio.sleep(0.05)
    assert not recv_task.done()

    await ch.close()
    assert ch.closed

    # The pending recv should resolve: either with ProtocolError (because
    # the underlying read raised on the closed socket) or as a clean EOF.
    # Either way it must not hang and must not leak a task.
    with pytest.raises(ProtocolError):
        await asyncio.wait_for(recv_task, timeout=1.0)


async def test_close_is_idempotent(server, server_keypair, client_keypair):
    """Calling ``close()`` repeatedly is safe in any state."""
    server.on_session = echo_session

    ch = await connect(
        host="127.0.0.1",
        port=server.port,
        local_static_key=client_keypair,
        expected_remote_static_pubkey=_server_pubkey_bytes(server_keypair),
    )

    await ch.close()
    await ch.close()  # second time
    await ch.close()  # third time
    assert ch.closed


async def test_dns_error(client_keypair, server_keypair):
    """Unresolvable host -> ``DnsError``."""
    # ``.invalid`` is reserved by RFC 6761 -- guaranteed not to resolve.
    with pytest.raises(DnsError):
        await connect(
            host="this-host-must-not-exist.invalid",
            port=1,
            local_static_key=client_keypair,
            expected_remote_static_pubkey=_server_pubkey_bytes(server_keypair),
            connect_timeout_s=2.0,
        )


async def test_async_iterator(server, server_keypair, client_keypair):
    """``async for frame in channel`` yields decrypted ServerFrames."""
    frames = [
        ServerFrame(token=AssistantToken(text="alpha", sequence=0, turn_id="t1")),
        ServerFrame(token=AssistantToken(text="beta", sequence=1, turn_id="t1")),
        ServerFrame(turn_end=AssistantTurnEnd(turn_id="t1")),
    ]
    server.on_session = send_then_close(frames)

    ch = await connect(
        host="127.0.0.1",
        port=server.port,
        local_static_key=client_keypair,
        expected_remote_static_pubkey=_server_pubkey_bytes(server_keypair),
    )

    received: list[ServerFrame] = []
    try:
        async for f in ch:
            received.append(f)
            if len(received) == len(frames):
                break
    finally:
        await ch.close()

    assert len(received) == len(frames)
    assert received[0].token.text == "alpha"
    assert received[1].token.text == "beta"
    assert received[2].WhichOneof("payload") == "turn_end"
