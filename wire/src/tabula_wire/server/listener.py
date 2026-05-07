"""Async TCP listener with Noise XX responder + length-prefixed frame loop.

Implements the bottom half of the server stack for issue #20.

Per-connection lifecycle::

    accept TCP
        -> read XX msg1                 (length-prefixed)
        -> write XX msg2                (length-prefixed)
        -> read XX msg3                 (length-prefixed)
        -> derive transport keys
        -> spawn:
             reader task: read frame -> Noise.decrypt -> proto.parse
                          -> queue.put(ClientFrame)
             writer task: queue.get() -> proto.serialize -> Noise.encrypt
                          -> write frame
        -> hand (handshake_info, recv_iter, send_fn) to on_session
        -> on_session returns or raises -> close cleanly

Failure modes (all close the connection cleanly, none crash the listener):

- TCP EOF before / during handshake
- Malformed handshake message (HandshakeError from responder)
- Length prefix > MAX_FRAME_SIZE pre-decrypt
- AEAD auth failure during transport
- Malformed plaintext proto after decrypt
- on_session callback raises

Backpressure:

- The writer awaits ``StreamWriter.drain()`` after every frame, so a
  slow reader on the client end suspends the producer.
- The decode pipeline drops frames into a bounded queue; if the
  consumer (session manager / on_session) stalls, the reader task
  awaits the queue's free slot and stops pulling bytes off the
  socket — TCP backpressure propagates to the kernel buffer.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from tabula_wire.crypto import HandshakeError
from tabula_wire.framing import (
    FrameTooLargeError,
    MAX_FRAME_SIZE,
    encode_length_prefix,
    is_truncation,
    read_frame,
    write_frame,
)


@runtime_checkable
class NoiseResponderProtocol(Protocol):
    """XX responder + post-handshake transport (structural).

    Matches the surface of ``tabula_wire.crypto.XXResponder``: the
    same object drives the 3-message XX handshake and then encrypts /
    decrypts transport frames. Test fakes may also satisfy this.
    """

    @property
    def handshake_finished(self) -> bool: ...
    @property
    def remote_static_pubkey(self) -> bytes | None: ...
    def read_handshake_message(self, ciphertext: bytes) -> bytes: ...
    def write_handshake_message(self, payload: bytes) -> bytes: ...
    def encrypt(self, plaintext: bytes) -> bytes: ...
    def decrypt(self, ciphertext: bytes) -> bytes: ...


# Backwards-compat alias for code/tests that referred to the
# transport-phase as a separate type.
NoiseTransportProtocol = NoiseResponderProtocol


@runtime_checkable
class FrameCodec(Protocol):
    """Codec the listener uses to (de)serialize plaintext frames.

    Decouples the listener from concrete protobuf classes. The default
    implementation (``_default_codec``) wraps the generated bindings
    from ``tabula_wire.proto.v1``; tests may inject alternates.
    """

    def encode_server_frame(self, frame: object) -> bytes:
        """Serialize a ServerFrame to bytes for encryption."""
        ...

    def decode_client_frame(self, data: bytes) -> object:
        """Parse bytes (decrypted plaintext) into a ClientFrame."""
        ...

log = logging.getLogger(__name__)

# Bounded queue of decoded ClientFrames waiting for the session
# callback to consume. 64 frames is more than the assistant streaming
# rate but small enough that a stalled consumer applies backpressure
# rather than buffering forever.
DEFAULT_RECV_QUEUE_SIZE = 64

# Bounded queue of ServerFrames waiting to be encrypted+sent.
DEFAULT_SEND_QUEUE_SIZE = 64


@dataclass(frozen=True)
class HandshakeInfo:
    """Per-connection metadata captured at the end of the XX handshake."""

    peer_addr: tuple[str, int] | None
    """``(host, port)`` of the connected peer, or None if unavailable."""

    remote_static_pubkey: bytes
    """The initiator's static pubkey, as observed during XX. The
    pinning layer (#32 on the client side) consumes the *server*'s
    pubkey; on the server we still surface the *client*'s for logging
    and downstream auth hooks."""


@dataclass
class Session:
    """The pair of half-duplex handles given to ``on_session``.

    ``recv_frames`` is an async iterator that yields decoded
    ``ClientFrame`` protos as they arrive. It terminates when the
    client closes the stream cleanly or when a fatal error closes the
    connection.

    ``send_frame`` is an async function that enqueues a ``ServerFrame``
    for encryption and sending. It awaits drain via the bounded send
    queue, so backpressure from a slow client propagates to the caller.
    """

    recv_frames: AsyncIterator[Any]
    send_frame: Callable[[Any], Awaitable[None]]


# Type alias for the user-supplied per-session callback. The listener
# awaits this; when it returns (or raises) the connection is closed.
SessionCallback = Callable[[HandshakeInfo, Session], Awaitable[None]]

# Factory that produces a fresh NoiseResponder for each accepted
# connection. Injected so callers can substitute alternate
# implementations (e.g. test fakes) without changing the listener.
ResponderFactory = Callable[[bytes], NoiseResponderProtocol]


async def _run_responder_handshake(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    responder: NoiseResponderProtocol,
    *,
    max_frame_size: int,
) -> NoiseTransportProtocol:
    """Drive the three-message XX handshake to completion.

    Each handshake message is itself length-prefixed using the same
    framing as transport frames; this lets us reuse :mod:`wire.framing`
    and gives the client a uniform read loop.

    Raises:
        HandshakeError: any failure on the wire or in the responder.
    """
    # msg1: <- e
    try:
        msg1 = await read_frame(reader, max_size=max_frame_size)
    except FrameTooLargeError as exc:
        raise HandshakeError(f"oversize handshake msg1: {exc}") from exc
    except asyncio.IncompleteReadError as exc:
        raise HandshakeError("EOF during handshake msg1") from exc
    responder.read_handshake_message(msg1)

    # msg2: -> e, ee, s, es
    msg2 = responder.write_handshake_message(b"")
    try:
        await write_frame(writer, msg2, max_size=max_frame_size)
    except (ConnectionError, BrokenPipeError) as exc:
        raise HandshakeError("connection lost writing handshake msg2") from exc

    # msg3: <- s, se
    try:
        msg3 = await read_frame(reader, max_size=max_frame_size)
    except FrameTooLargeError as exc:
        raise HandshakeError(f"oversize handshake msg3: {exc}") from exc
    except asyncio.IncompleteReadError as exc:
        raise HandshakeError("EOF during handshake msg3") from exc
    responder.read_handshake_message(msg3)

    if not responder.handshake_finished:
        raise HandshakeError("responder did not finalize after msg3")
    # The same object is used for transport-phase encrypt/decrypt.
    return responder


# Module-level sentinel pushed onto the recv queue by the reader pump
# when no more frames will arrive. Single shared object so consumers
# can identify it by identity.
_RECV_EOF: object = object()


async def _reader_task(
    reader: asyncio.StreamReader,
    transport: NoiseTransportProtocol,
    codec: FrameCodec,
    queue: asyncio.Queue,
    *,
    max_frame_size: int,
) -> None:
    """Pull frames off the socket, decrypt, decode, enqueue."""
    try:
        while True:
            try:
                ciphertext = await read_frame(reader, max_size=max_frame_size)
            except FrameTooLargeError as exc:
                log.warning("oversize transport frame, closing: %s", exc)
                break
            except asyncio.IncompleteReadError:
                # Clean EOF if no partial frame; otherwise truncation.
                # Either way, end the iterator.
                break
            except ConnectionError as exc:
                log.warning("connection error during read: %s", exc)
                break

            try:
                plaintext = transport.decrypt(ciphertext)
            except HandshakeError as exc:
                # AEAD failure — fatal for this connection.
                log.warning("decrypt failed, closing: %s", exc)
                break
            except Exception as exc:  # pragma: no cover - belt and suspenders
                log.warning("unexpected decrypt error, closing: %s", exc)
                break

            try:
                frame = codec.decode_client_frame(plaintext)
            except Exception as exc:
                log.warning("malformed ClientFrame, closing: %s", exc)
                break

            await queue.put(frame)
    finally:
        # Signal the consumer that no more frames will arrive.
        await queue.put(_RECV_EOF)


async def _writer_task(
    writer: asyncio.StreamWriter,
    transport: NoiseTransportProtocol,
    codec: FrameCodec,
    send_queue: asyncio.Queue,
    *,
    max_frame_size: int,
) -> None:
    """Pull frames off the send queue, encode, encrypt, write."""
    while True:
        item = await send_queue.get()
        if item is _SEND_CLOSE:
            return
        try:
            plaintext = codec.encode_server_frame(item)
        except Exception as exc:
            log.warning("failed to encode ServerFrame, dropping: %s", exc)
            continue
        try:
            ciphertext = transport.encrypt(plaintext)
        except Exception as exc:
            log.warning("encrypt failed, closing: %s", exc)
            return
        if len(ciphertext) > max_frame_size:
            log.warning(
                "outbound frame %d > max %d; dropping (caller should fragment)",
                len(ciphertext),
                max_frame_size,
            )
            continue
        try:
            writer.write(encode_length_prefix(len(ciphertext)))
            writer.write(ciphertext)
            await writer.drain()
        except (ConnectionError, BrokenPipeError) as exc:
            log.info("client closed during write: %s", exc)
            return


# Sentinel used to signal the writer task to exit.
_SEND_CLOSE: object = object()


def _peer_addr(writer: asyncio.StreamWriter) -> tuple[str, int] | None:
    info = writer.get_extra_info("peername")
    if info is None:
        return None
    if isinstance(info, tuple) and len(info) >= 2:
        return (str(info[0]), int(info[1]))
    return None


async def _handle_connection(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    *,
    static_key: bytes,
    on_session: SessionCallback,
    responder_factory: ResponderFactory,
    codec: FrameCodec,
    max_frame_size: int,
    recv_queue_size: int,
    send_queue_size: int,
) -> None:
    """Handle one accepted TCP connection end-to-end.

    Catches all expected exceptions so a misbehaving peer never takes
    down the listener task.
    """
    peer = _peer_addr(writer)
    log.debug("accepted connection from %s", peer)
    try:
        responder = responder_factory(static_key)
        try:
            transport = await _run_responder_handshake(
                reader, writer, responder, max_frame_size=max_frame_size
            )
        except HandshakeError as exc:
            log.info("handshake failed from %s: %s", peer, exc)
            return
        except Exception as exc:
            # Catch-all — never let a buggy responder crash the listener.
            if is_truncation(exc):
                log.info("peer %s disconnected during handshake", peer)
            else:
                log.warning("unexpected handshake error from %s: %s", peer, exc)
            return

        remote_pub = transport.remote_static_pubkey
        if remote_pub is None:
            # ``handshake_finished`` is True, so the real responder
            # guarantees this is non-None. Defensive guard for fakes.
            log.warning(
                "handshake finished but remote_static_pubkey is None; closing"
            )
            return
        info = HandshakeInfo(
            peer_addr=peer,
            remote_static_pubkey=remote_pub,
        )

        recv_queue: asyncio.Queue = asyncio.Queue(maxsize=recv_queue_size)
        send_queue: asyncio.Queue = asyncio.Queue(maxsize=send_queue_size)

        reader_task = asyncio.create_task(
            _reader_task(
                reader, transport, codec, recv_queue,
                max_frame_size=max_frame_size,
            ),
            name="wire.server.reader",
        )
        writer_task = asyncio.create_task(
            _writer_task(
                writer, transport, codec, send_queue,
                max_frame_size=max_frame_size,
            ),
            name="wire.server.writer",
        )

        async def recv_iter() -> AsyncIterator[Any]:
            while True:
                item = await recv_queue.get()
                if item is _RECV_EOF:
                    return
                yield item

        async def send_frame(frame: Any) -> None:
            await send_queue.put(frame)

        session = Session(recv_frames=recv_iter(), send_frame=send_frame)

        try:
            await on_session(info, session)
        except Exception:
            log.exception("on_session callback raised; closing connection")
        finally:
            # Stop both pumps.
            await send_queue.put(_SEND_CLOSE)
            reader_task.cancel()
            for task in (reader_task, writer_task):
                try:
                    await task
                except asyncio.CancelledError:
                    pass
                except Exception:  # pragma: no cover
                    log.exception("background task error during shutdown")
    finally:
        try:
            writer.close()
            await writer.wait_closed()
        except Exception:
            pass
        log.debug("closed connection from %s", peer)


def _default_responder_factory(static_key: bytes) -> NoiseResponderProtocol:
    """Default responder factory — real Noise XX responder from #50."""
    from tabula_wire.crypto import SecretKey, XXResponder
    return XXResponder(SecretKey(raw=bytes(static_key)))


class _RealProtoCodec:
    """``FrameCodec`` over the real ``tabula_wire.proto.v1`` bindings."""

    def __init__(self) -> None:
        from tabula_wire.proto.v1 import ClientFrame, ServerFrame
        self._client_cls = ClientFrame
        self._server_cls = ServerFrame

    def encode_server_frame(self, frame: object) -> bytes:
        if not isinstance(frame, self._server_cls):
            raise TypeError(
                f"expected {self._server_cls.__name__}, got {type(frame).__name__}"
            )
        return frame.SerializeToString()

    def decode_client_frame(self, data: bytes) -> object:
        msg = self._client_cls()
        msg.ParseFromString(data)
        return msg


def _default_codec() -> FrameCodec:
    """Default proto codec — real protobuf bindings from #45."""
    return _RealProtoCodec()


async def serve(
    host: str,
    port: int,
    static_key: bytes,
    on_session: SessionCallback,
    *,
    responder_factory: ResponderFactory | None = None,
    codec: FrameCodec | None = None,
    max_frame_size: int = MAX_FRAME_SIZE,
    recv_queue_size: int = DEFAULT_RECV_QUEUE_SIZE,
    send_queue_size: int = DEFAULT_SEND_QUEUE_SIZE,
) -> asyncio.base_events.Server:
    """Start the TCP listener.

    Args:
        host: bind address (e.g. ``"0.0.0.0"`` or ``"127.0.0.1"``).
        port: bind port.
        static_key: server's 32-byte X25519 static private key. The
            keygen CLI sub-issue (#31) is responsible for producing
            and persisting this; this listener only consumes the bytes.
        on_session: async callback invoked once per accepted+handshaken
            connection. Receives ``(HandshakeInfo, Session)``. When the
            callback returns or raises, the connection is closed.
        responder_factory: override for the Noise responder
            constructor. Defaults to ``tabula_wire.crypto.XXResponder``.
        codec: ``FrameCodec`` for ClientFrame/ServerFrame ser/deser.
            Defaults to ``tabula_wire.proto.v1.ProtoCodec``.
        max_frame_size: pre-decrypt ciphertext cap. Default 1 MiB.
        recv_queue_size: bounded queue between the reader pump and
            ``on_session``. Caps memory under a stalled consumer.
        send_queue_size: bounded queue between ``send_frame`` and the
            writer pump. Caps memory under a slow client.

    Returns:
        The ``asyncio.Server`` from ``asyncio.start_server``. Call
        ``server.close()`` and ``await server.wait_closed()`` to stop.

    Note:
        This function returns as soon as the listener is bound; it
        does NOT call ``serve_forever``. The caller controls the
        accept loop's lifetime via the returned ``Server``.
    """
    if responder_factory is None:
        responder_factory = _default_responder_factory
    if codec is None:
        codec = _default_codec()

    async def _client_connected(
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        await _handle_connection(
            reader,
            writer,
            static_key=static_key,
            on_session=on_session,
            responder_factory=responder_factory,
            codec=codec,
            max_frame_size=max_frame_size,
            recv_queue_size=recv_queue_size,
            send_queue_size=send_queue_size,
        )

    server = await asyncio.start_server(_client_connected, host=host, port=port)
    if log.isEnabledFor(logging.INFO):
        socks = ", ".join(str(s.getsockname()) for s in (server.sockets or ()))
        log.info("wire.server listening on %s", socks)
    return server
