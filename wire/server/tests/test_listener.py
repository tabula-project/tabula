"""Tests for ``wire.server.listener`` over real loopback sockets.

Covers the four scenarios required by issue #20's acceptance criteria:

1. Full handshake + N frames each direction (round-trip).
2. Malformed length prefix / oversize frame rejection.
3. Handshake failure (wrong client key shape).
4. Malformed proto after decrypt.

Tests use the stub Noise responder and stub proto codec so they do not
depend on #16 (proto schema) or #18 (real Noise XX wrapper). When
those land, the same tests can be re-run against the real
implementations by changing only the responder factory and codec
fixture.
"""

from __future__ import annotations

import asyncio
import struct

import pytest

from wire.crypto.protocol import HandshakeError, NoiseResponderProtocol
from wire.crypto.stub import (
    XXInitiatorStub,
    XXResponderStub,
    _AUTH_TRAILER,
)
from wire.framing import (
    LENGTH_PREFIX_BYTES,
    MAX_FRAME_SIZE,
    encode_length_prefix,
    read_frame,
    write_frame,
    FrameTooLargeError,
)
from wire.proto import ClientFrame, ServerFrame, StubProtoCodec
from wire.server import HandshakeInfo, Session, serve


# 32-byte stand-ins for X25519 static keys.
SERVER_KEY = b"S" * 32
CLIENT_KEY = b"C" * 32


def _responder_factory(static_key: bytes) -> NoiseResponderProtocol:
    return XXResponderStub(static_key)


@pytest.fixture
def codec() -> StubProtoCodec:
    return StubProtoCodec()


async def _start_server(
    on_session,
    *,
    codec: StubProtoCodec,
    max_frame_size: int = MAX_FRAME_SIZE,
):
    server = await serve(
        host="127.0.0.1",
        port=0,
        static_key=SERVER_KEY,
        on_session=on_session,
        responder_factory=_responder_factory,
        codec=codec,
        max_frame_size=max_frame_size,
    )
    sock = server.sockets[0]
    host, port = sock.getsockname()[:2]
    return server, host, port


async def _open_client(host: str, port: int):
    return await asyncio.open_connection(host, port)


async def _drive_client_handshake(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    expected_server_key: bytes | None = SERVER_KEY,
    *,
    client_key: bytes = CLIENT_KEY,
):
    initiator = XXInitiatorStub(client_key, expected_remote_pubkey=expected_server_key)
    # msg1: -> e
    msg1 = initiator.write_handshake_message(b"")
    await write_frame(writer, msg1)
    # msg2: <- e, ee, s, es
    msg2 = await read_frame(reader)
    initiator.read_handshake_message(msg2)
    # msg3: -> s, se
    msg3 = initiator.write_handshake_message(b"")
    await write_frame(writer, msg3)
    return initiator.into_transport()


# ---------------------------------------------------------------------------
# 1. Full handshake + N frames each direction.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_handshake_and_roundtrip_frames(codec: StubProtoCodec):
    received_on_server: list[ClientFrame] = []
    handshake_seen: list[HandshakeInfo] = []

    async def on_session(info: HandshakeInfo, session: Session):
        handshake_seen.append(info)
        # Echo each ClientFrame back as a ServerFrame.
        async for frame in session.recv_frames:
            received_on_server.append(frame)
            await session.send_frame(
                ServerFrame(kind="echo", text=frame.text)
            )

    server, host, port = await _start_server(on_session, codec=codec)
    try:
        reader, writer = await _open_client(host, port)
        transport = await _drive_client_handshake(reader, writer)

        # Send 5 frames, expect 5 echoes.
        for i in range(5):
            cf = ClientFrame(kind="user", text=f"hello-{i}")
            await write_frame(writer, transport.encrypt(cf.SerializeToString()))

        echoes: list[ServerFrame] = []
        for _ in range(5):
            ct = await read_frame(reader)
            pt = transport.decrypt(ct)
            echoes.append(ServerFrame.ParseFromString(pt))

        # Close client; server should drain and exit.
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass
    finally:
        server.close()
        await server.wait_closed()

    # Server saw the same five frames.
    assert [f.text for f in received_on_server] == [f"hello-{i}" for i in range(5)]
    # Client saw five matching echoes.
    assert [f.text for f in echoes] == [f"hello-{i}" for i in range(5)]
    # Handshake info captured the client static key.
    assert len(handshake_seen) == 1
    assert handshake_seen[0].remote_static_pubkey == CLIENT_KEY
    assert handshake_seen[0].peer_addr is not None
    assert handshake_seen[0].peer_addr[0] in {"127.0.0.1", "::1"}


# ---------------------------------------------------------------------------
# 2a. Oversize length prefix rejected pre-decrypt.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_oversize_frame_rejected(codec: StubProtoCodec):
    # Use a small max so we can send an "oversize" frame quickly.
    small_max = 1024
    on_session_called = asyncio.Event()
    on_session_finished = asyncio.Event()

    async def on_session(info: HandshakeInfo, session: Session):
        on_session_called.set()
        # Iterate to exhaustion so we observe the reader pump shutting down.
        async for _ in session.recv_frames:
            pass
        on_session_finished.set()

    server, host, port = await _start_server(
        on_session, codec=codec, max_frame_size=small_max
    )
    try:
        reader, writer = await _open_client(host, port)
        await _drive_client_handshake(reader, writer)
        await asyncio.wait_for(on_session_called.wait(), timeout=2.0)

        # Announce a frame larger than the cap. Listener must close
        # without reading the body and without crashing.
        writer.write(struct.pack(">I", small_max + 1))
        # Send a few bytes, but not the announced body — the listener
        # should never wait for the full body because it bails on the
        # length prefix.
        writer.write(b"x" * 16)
        try:
            await writer.drain()
        except Exception:
            pass

        # The on_session iterator should terminate cleanly.
        await asyncio.wait_for(on_session_finished.wait(), timeout=2.0)

        # Client side: peer should have closed.
        # readexactly returns IncompleteReadError on EOF.
        with pytest.raises(asyncio.IncompleteReadError):
            await reader.readexactly(1)
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass
    finally:
        server.close()
        await server.wait_closed()


# ---------------------------------------------------------------------------
# 2b. Truncated/malformed handshake length prefix is fatal but listener survives.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_truncated_handshake_does_not_crash_listener(codec: StubProtoCodec):
    sessions_started = 0

    async def on_session(info: HandshakeInfo, session: Session):
        nonlocal sessions_started
        sessions_started += 1
        async for _ in session.recv_frames:
            pass

    server, host, port = await _start_server(on_session, codec=codec)
    try:
        # Connection 1: send only 2 bytes of the length prefix, then close.
        reader1, writer1 = await _open_client(host, port)
        writer1.write(b"\x00\x00")
        await writer1.drain()
        writer1.close()
        try:
            await writer1.wait_closed()
        except Exception:
            pass

        # Connection 2: send full header but no body, then close.
        reader2, writer2 = await _open_client(host, port)
        writer2.write(struct.pack(">I", 1024))
        await writer2.drain()
        writer2.close()
        try:
            await writer2.wait_closed()
        except Exception:
            pass

        # Connection 3: a successful handshake proves the listener is alive.
        reader3, writer3 = await _open_client(host, port)
        await _drive_client_handshake(reader3, writer3)
        # Close cleanly.
        writer3.close()
        try:
            await writer3.wait_closed()
        except Exception:
            pass
        # Give the loop a moment to run on_session.
        await asyncio.sleep(0.05)
    finally:
        server.close()
        await server.wait_closed()

    assert sessions_started == 1


# ---------------------------------------------------------------------------
# 3. Handshake failure (malformed XX msg1) → connection closed, listener survives.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_handshake_failure_closes_cleanly(codec: StubProtoCodec):
    sessions_started = 0

    async def on_session(info: HandshakeInfo, session: Session):
        nonlocal sessions_started
        sessions_started += 1

    server, host, port = await _start_server(on_session, codec=codec)
    try:
        # Send a bogus first handshake message (wrong tag prefix).
        reader, writer = await _open_client(host, port)
        await write_frame(writer, b"NOT-A-VALID-XX-MSG1")
        # Server should close without invoking on_session.
        with pytest.raises(asyncio.IncompleteReadError):
            await reader.readexactly(1)
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass

        # Listener must still accept new connections after handshake failure.
        reader2, writer2 = await _open_client(host, port)
        await _drive_client_handshake(reader2, writer2)
        writer2.close()
        try:
            await writer2.wait_closed()
        except Exception:
            pass
        await asyncio.sleep(0.05)
    finally:
        server.close()
        await server.wait_closed()

    assert sessions_started == 1


# ---------------------------------------------------------------------------
# 3b. Wrong-key handshake (initiator pins the wrong server key) — initiator
# detects mismatch on its side; from the server's perspective the connection
# is still cleanly torn down once the initiator hangs up.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_initiator_pin_mismatch_does_not_crash_listener(codec: StubProtoCodec):
    sessions_started = 0

    async def on_session(info: HandshakeInfo, session: Session):
        nonlocal sessions_started
        sessions_started += 1
        async for _ in session.recv_frames:
            pass

    server, host, port = await _start_server(on_session, codec=codec)
    try:
        reader, writer = await _open_client(host, port)
        # Tell the initiator to expect a different server key — it
        # raises HandshakeError on msg2.
        with pytest.raises(HandshakeError):
            await _drive_client_handshake(
                reader, writer, expected_server_key=b"X" * 32
            )
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass
        # Server treats the dropped third message as connection EOF;
        # the listener stays up.
        reader2, writer2 = await _open_client(host, port)
        await _drive_client_handshake(reader2, writer2)
        writer2.close()
        try:
            await writer2.wait_closed()
        except Exception:
            pass
        await asyncio.sleep(0.05)
    finally:
        server.close()
        await server.wait_closed()

    assert sessions_started == 1


# ---------------------------------------------------------------------------
# 4. Malformed plaintext proto after decrypt → connection closed cleanly.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_malformed_proto_closes_connection(codec: StubProtoCodec):
    closed_cleanly = asyncio.Event()

    async def on_session(info: HandshakeInfo, session: Session):
        # Iterator should terminate after the bogus frame.
        async for _ in session.recv_frames:
            pass
        closed_cleanly.set()

    server, host, port = await _start_server(on_session, codec=codec)
    try:
        reader, writer = await _open_client(host, port)
        transport = await _drive_client_handshake(reader, writer)

        # Send a frame whose plaintext does NOT parse as a ClientFrame.
        await write_frame(writer, transport.encrypt(b"this is not a stub-proto frame\n"))

        await asyncio.wait_for(closed_cleanly.wait(), timeout=2.0)
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass
    finally:
        server.close()
        await server.wait_closed()


# ---------------------------------------------------------------------------
# 4b. AEAD auth failure (tampered ciphertext) is treated as fatal.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_aead_failure_closes_connection(codec: StubProtoCodec):
    closed_cleanly = asyncio.Event()

    async def on_session(info: HandshakeInfo, session: Session):
        async for _ in session.recv_frames:
            pass
        closed_cleanly.set()

    server, host, port = await _start_server(on_session, codec=codec)
    try:
        reader, writer = await _open_client(host, port)
        transport = await _drive_client_handshake(reader, writer)
        # Build a valid ciphertext, then strip the auth trailer to
        # trigger the stub's "AEAD" failure.
        cf = ClientFrame(kind="user", text="x")
        good = transport.encrypt(cf.SerializeToString())
        tampered = good[:-len(_AUTH_TRAILER)] + b"X" * len(_AUTH_TRAILER)
        await write_frame(writer, tampered)
        await asyncio.wait_for(closed_cleanly.wait(), timeout=2.0)
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass
    finally:
        server.close()
        await server.wait_closed()


# ---------------------------------------------------------------------------
# Framing-level smoke tests (pure-bytes; no socket).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_framing_oversize_helper():
    # The encode helper rejects oversize at the public API too.
    with pytest.raises(FrameTooLargeError):
        encode_length_prefix(MAX_FRAME_SIZE + 1)


def test_length_prefix_constant():
    # Sanity: 4-byte u32 BE prefix as documented.
    assert LENGTH_PREFIX_BYTES == 4
    val = 0xCAFEBABE & 0x0FFFFF  # within MAX_FRAME_SIZE
    assert encode_length_prefix(val) == struct.pack(">I", val)
