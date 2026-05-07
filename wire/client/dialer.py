"""Tabula chat client: TCP dialer + Noise XX initiator + framed I/O.

This module is the client-side counterpart to the server listener in #20.
It opens a TCP connection, drives the Noise XX initiator handshake, verifies
the responder's static pubkey against a caller-supplied pinned value, and
returns a :class:`ChatChannel` exposing ``send`` / ``recv`` / ``close``.

Wire format (must match #20):
- Each direction is a sequence of length-prefixed frames.
- During the handshake phase, each frame's payload is a Noise XX handshake
  message (cleartext modulo Noise's own MAC).
- After the handshake completes, each frame's payload is a Noise transport
  ciphertext whose plaintext is a serialized ``ServerFrame`` (server -> client)
  or ``ClientFrame`` (client -> server).

The length-prefix codec lives in :mod:`wire.framing` so the server (#20) and
the client (this module) share a single source of truth.

Threading / cancellation model:
- :func:`connect` opens the connection and runs the handshake under
  ``asyncio.wait_for``. On timeout we close the writer before raising.
- :class:`ChatChannel` has no background task: ``recv`` reads on demand and
  is cancellation-safe. ``close`` is idempotent and may be called
  concurrently with a pending ``recv`` — the pending ``recv`` will raise
  ``ProtocolError`` (because the underlying read fails on closed socket) or
  return a ``CancelledError`` if cancelled by the caller.
"""

from __future__ import annotations

import asyncio
import socket
from typing import AsyncIterator

from wire.crypto import KeyMismatchError, KeyPair, NoiseError, XXInitiator
from wire.framing import FrameError, FrameTooLarge, encode_frame, read_frame
from wire.proto import ClientFrame, ProtoError, ServerFrame

from .exceptions import (
    ConnectRefused,
    ConnectTimeout,
    DnsError,
    ProtocolError,
    ServerKeyMismatch,
)

DEFAULT_CONNECT_TIMEOUT_S: float = 10.0


class ChatChannel:
    """Bidirectional encrypted chat channel over Noise XX transport.

    Constructed by :func:`connect` after a successful handshake and pinning
    check. Do not instantiate directly.
    """

    def __init__(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        noise: XXInitiator,
        remote_static_pubkey: bytes,
    ) -> None:
        self._reader = reader
        self._writer = writer
        self._noise = noise
        self._remote_static_pubkey = remote_static_pubkey
        self._closed = False
        self._send_lock = asyncio.Lock()
        self._recv_lock = asyncio.Lock()

    @property
    def remote_static_pubkey(self) -> bytes:
        """The verified static public key of the server peer."""
        return self._remote_static_pubkey

    @property
    def closed(self) -> bool:
        return self._closed

    async def send(self, frame: ClientFrame) -> None:
        """Serialize, encrypt, frame, and send a :class:`ClientFrame`.

        Raises :class:`ProtocolError` if the channel is closed or the write
        fails for any reason.
        """
        if self._closed:
            raise ProtocolError("send on closed ChatChannel")
        try:
            plaintext = frame.SerializeToString()
        except Exception as exc:
            raise ProtocolError(f"failed to serialize ClientFrame: {exc}") from exc
        try:
            ciphertext = self._noise.encrypt(plaintext)
        except NoiseError as exc:
            raise ProtocolError(f"noise encrypt failed: {exc}") from exc
        framed = encode_frame(ciphertext)
        async with self._send_lock:
            if self._closed:
                raise ProtocolError("send on closed ChatChannel")
            try:
                self._writer.write(framed)
                await self._writer.drain()
            except (ConnectionError, OSError) as exc:
                await self._close_internal()
                raise ProtocolError(f"send failed: {exc}") from exc

    async def recv(self) -> ServerFrame:
        """Read, decrypt, and parse one :class:`ServerFrame` from the stream.

        Raises :class:`ProtocolError` on any framing, decrypt, or parse
        failure, or if the channel is closed (cleanly or not). On error the
        channel is closed before the exception propagates.
        """
        if self._closed:
            raise ProtocolError("recv on closed ChatChannel")
        async with self._recv_lock:
            if self._closed:
                raise ProtocolError("recv on closed ChatChannel")
            try:
                ciphertext = await read_frame(self._reader)
            except asyncio.IncompleteReadError as exc:
                await self._close_internal()
                raise ProtocolError(
                    f"unexpected EOF reading frame header (got "
                    f"{len(exc.partial)}/{exc.expected} bytes)"
                ) from exc
            except FrameTooLarge as exc:
                await self._close_internal()
                raise ProtocolError(str(exc)) from exc
            except FrameError as exc:
                await self._close_internal()
                raise ProtocolError(str(exc)) from exc
            except (ConnectionError, OSError) as exc:
                await self._close_internal()
                raise ProtocolError(f"socket error during recv: {exc}") from exc

            try:
                plaintext = self._noise.decrypt(ciphertext)
            except NoiseError as exc:
                await self._close_internal()
                raise ProtocolError(f"noise decrypt failed: {exc}") from exc

            try:
                return ServerFrame.FromString(plaintext)
            except ProtoError as exc:
                await self._close_internal()
                raise ProtocolError(f"malformed ServerFrame: {exc}") from exc
            except Exception as exc:  # protobuf parse errors etc.
                await self._close_internal()
                raise ProtocolError(f"malformed ServerFrame: {exc}") from exc

    def __aiter__(self) -> AsyncIterator[ServerFrame]:
        return self._iter()

    async def _iter(self) -> AsyncIterator[ServerFrame]:
        while not self._closed:
            try:
                yield await self.recv()
            except ProtocolError:
                # Surface end-of-stream / errors by stopping iteration after
                # close. Callers who want the exception should call recv()
                # directly.
                if self._closed:
                    return
                raise

    async def close(self) -> None:
        """Close the channel. Idempotent and safe from any state."""
        await self._close_internal()

    async def _close_internal(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self._writer.close()
        except Exception:
            pass
        try:
            await self._writer.wait_closed()
        except Exception:
            # wait_closed can raise if the peer aborted; we don't care --
            # close() is documented as idempotent and best-effort.
            pass


async def connect(
    host: str,
    port: int,
    local_static_key: KeyPair,
    expected_remote_static_pubkey: bytes,
    *,
    connect_timeout_s: float = DEFAULT_CONNECT_TIMEOUT_S,
) -> ChatChannel:
    """Open a chat channel to ``host``:``port``.

    Steps:
        1. ``asyncio.open_connection(host, port)``.
        2. Run the Noise XX initiator handshake (3 messages).
        3. Verify the responder's static pubkey equals
           ``expected_remote_static_pubkey``.
        4. Return a :class:`ChatChannel`.

    Steps 1-3 collectively must finish within ``connect_timeout_s`` seconds
    or :class:`ConnectTimeout` is raised and the socket is closed.

    Raises:
        ConnectTimeout: dial + handshake exceeded the deadline.
        ConnectRefused: peer refused the TCP connection.
        DnsError: ``host`` could not be resolved.
        ServerKeyMismatch: handshake completed but pubkey != pinned. Socket
            is closed before this raises; no application frames have been
            read or written.
        ProtocolError: framing / decrypt / proto failure during handshake.
    """
    try:
        return await asyncio.wait_for(
            _dial_and_handshake(
                host, port, local_static_key, expected_remote_static_pubkey
            ),
            timeout=connect_timeout_s,
        )
    except asyncio.TimeoutError as exc:
        raise ConnectTimeout(
            f"connect to {host}:{port} timed out after {connect_timeout_s}s"
        ) from exc


async def _dial_and_handshake(
    host: str,
    port: int,
    local_static_key: KeyPair,
    expected_remote_static_pubkey: bytes,
) -> ChatChannel:
    # Step 1: TCP dial. Map low-level OSError into typed exceptions.
    try:
        reader, writer = await asyncio.open_connection(host, port)
    except socket.gaierror as exc:
        raise DnsError(f"could not resolve {host}: {exc}") from exc
    except ConnectionRefusedError as exc:
        raise ConnectRefused(f"connection refused: {host}:{port}") from exc
    except OSError as exc:
        # Some platforms (e.g. macOS) raise OSError(ECONNREFUSED) rather than
        # ConnectionRefusedError. Try to disambiguate by errno before falling
        # back to a generic ProtocolError.
        import errno as _errno

        if exc.errno in (_errno.ECONNREFUSED,):
            raise ConnectRefused(
                f"connection refused: {host}:{port}"
            ) from exc
        if exc.errno in (_errno.EHOSTUNREACH, _errno.ENETUNREACH):
            raise ConnectRefused(
                f"host unreachable: {host}:{port}"
            ) from exc
        raise ProtocolError(f"socket error during connect: {exc}") from exc

    # From here on, any failure must close the writer before raising.
    try:
        return await _run_handshake(
            reader, writer, local_static_key, expected_remote_static_pubkey
        )
    except BaseException:
        # Includes asyncio.CancelledError from the wait_for timeout.
        try:
            writer.close()
        except Exception:
            pass
        try:
            await writer.wait_closed()
        except Exception:
            pass
        raise


async def _run_handshake(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    local_static_key: KeyPair,
    expected_remote_static_pubkey: bytes,
) -> ChatChannel:
    noise = XXInitiator(
        local_static_key,
        remote_static_pubkey=expected_remote_static_pubkey,
    )

    # Msg 1 (-> e): write
    try:
        msg1 = noise.write_message(b"")
        writer.write(encode_frame(msg1))
        await writer.drain()
    except NoiseError as exc:
        raise ProtocolError(f"handshake msg1 build failed: {exc}") from exc
    except (ConnectionError, OSError) as exc:
        raise ProtocolError(f"handshake msg1 send failed: {exc}") from exc

    # Msg 2 (<- e, ee, s, es): read
    try:
        msg2 = await read_frame(reader)
    except asyncio.IncompleteReadError as exc:
        raise ProtocolError(
            f"handshake msg2: EOF after {len(exc.partial)}/{exc.expected} bytes"
        ) from exc
    except FrameTooLarge as exc:
        raise ProtocolError(f"handshake msg2 too large: {exc}") from exc
    except FrameError as exc:
        raise ProtocolError(f"handshake msg2 framing error: {exc}") from exc
    except (ConnectionError, OSError) as exc:
        raise ProtocolError(f"handshake msg2 recv failed: {exc}") from exc

    try:
        noise.read_message(msg2)
    except NoiseError as exc:
        raise ProtocolError(f"handshake msg2 decode failed: {exc}") from exc

    # Msg 3 (-> s, se): write. This completes the handshake.
    try:
        msg3 = noise.write_message(b"")
        writer.write(encode_frame(msg3))
        await writer.drain()
    except NoiseError as exc:
        raise ProtocolError(f"handshake msg3 build failed: {exc}") from exc
    except (ConnectionError, OSError) as exc:
        raise ProtocolError(f"handshake msg3 send failed: {exc}") from exc

    if not noise.handshake_finished:
        # Should be unreachable: msg3 in XX completes the handshake on the
        # initiator side. Defensive check anyway.
        raise ProtocolError("Noise XX handshake did not complete after msg3")

    # Pinning check: verify *immediately* and *before* yielding ChatChannel
    # so no application frames are ever read or written under an unverified
    # peer identity.
    actual_pubkey = noise.remote_static_pubkey
    if actual_pubkey is None:
        raise ProtocolError("Noise handshake completed but no remote static key")
    if actual_pubkey != expected_remote_static_pubkey:
        raise ServerKeyMismatch(
            f"server static pubkey {actual_pubkey.hex()} does not match pinned "
            f"{expected_remote_static_pubkey.hex()}"
        )

    return ChatChannel(
        reader=reader,
        writer=writer,
        noise=noise,
        remote_static_pubkey=actual_pubkey,
    )


__all__ = ["ChatChannel", "connect", "DEFAULT_CONNECT_TIMEOUT_S"]
