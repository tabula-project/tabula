"""Minimal Noise XX responder used as a test fixture for the client dialer.

A small, self-contained server that exercises the client end-to-end:

- accept a TCP connection
- run the Noise XX responder handshake using ``tabula_wire.crypto.XXResponder``
- send the configured ``ServerFrame`` payloads
- echo any received ``ClientFrame.user_message`` back as
  ``ServerFrame.token`` (an ``AssistantToken`` echoing the text)

This keeps the dialer tests independent of the higher-level session loop in
:mod:`tabula_wire.server`.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Optional

from tabula_wire.crypto import SecretKey, XXResponder
from tabula_wire.crypto.noise_xx import HandshakeError
from tabula_wire.framing import (
    FrameTooLargeError,
    FramingError,
    read_frame,
    write_frame,
)
from tabula_wire.proto.v1 import AssistantToken, ClientFrame, ServerFrame


@dataclass
class FakeServer:
    """Test fixture server.

    Pytest auto-collects classes whose names start with ``Test`` so we use
    ``FakeServer`` to keep the collector quiet.
    """

    static_key: SecretKey
    server: Optional[asyncio.base_events.Server] = None
    on_session: Optional[
        Callable[
            [XXResponder, asyncio.StreamReader, asyncio.StreamWriter],
            Awaitable[None],
        ]
    ] = None
    # Behaviour switches used by individual tests:
    refuse_after_accept: bool = False
    skip_handshake_msg2: bool = False
    inject_garbage_after_handshake: bool = False
    sessions_started: int = 0
    accept_event: asyncio.Event = field(default_factory=asyncio.Event)
    _handler_tasks: list[asyncio.Task] = field(default_factory=list)

    @property
    def port(self) -> int:
        assert self.server is not None
        sockets = self.server.sockets
        assert sockets
        return sockets[0].getsockname()[1]

    async def start(self) -> None:
        self.server = await asyncio.start_server(
            self._handle, host="127.0.0.1", port=0
        )

    async def stop(self) -> None:
        # Cancel any in-flight handler tasks so we don't block on a long
        # ``asyncio.sleep`` (e.g. the ``refuse_after_accept`` path).
        for t in self._handler_tasks:
            if not t.done():
                t.cancel()
        for t in self._handler_tasks:
            try:
                await t
            except (asyncio.CancelledError, Exception):
                pass
        self._handler_tasks.clear()
        if self.server is not None:
            self.server.close()
            await self.server.wait_closed()
            self.server = None

    async def _handle(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        # Track this task so ``stop()`` can cancel it deterministically.
        current = asyncio.current_task()
        if current is not None:
            self._handler_tasks.append(current)
        self.sessions_started += 1
        self.accept_event.set()
        try:
            if self.refuse_after_accept:
                # Accept TCP, then never respond. The client's connect_timeout_s
                # should fire.
                await asyncio.sleep(60)
                return

            noise = XXResponder(self.static_key)

            # Msg 1
            try:
                m1 = await read_frame(reader)
            except (FramingError, FrameTooLargeError, asyncio.IncompleteReadError):
                return
            try:
                noise.read_handshake_message(m1)
            except HandshakeError:
                return

            if self.skip_handshake_msg2:
                # Drop the connection mid-handshake. Client should timeout.
                return

            # Msg 2
            m2 = noise.write_handshake_message(b"")
            await write_frame(writer, m2)

            # Msg 3
            try:
                m3 = await read_frame(reader)
            except (FramingError, FrameTooLargeError, asyncio.IncompleteReadError):
                return
            try:
                noise.read_handshake_message(m3)
            except HandshakeError:
                return

            assert noise.handshake_finished

            if self.inject_garbage_after_handshake:
                # Send an unencrypted blob the client will try to Noise-decrypt
                # and reject as ProtocolError. We frame it correctly so the
                # framing layer accepts the bytes; the noise decrypt is what
                # should fail.
                await write_frame(writer, b"\x00" * 32)
                return

            if self.on_session is not None:
                await self.on_session(noise, reader, writer)
        except Exception:
            # Test fixture: swallow all exceptions on the server side so the
            # listener does not crash the event loop.
            pass
        finally:
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass


async def echo_session(
    noise: XXResponder,
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
) -> None:
    """Read ClientFrames; for each ``user_message`` echo the text as an
    ``AssistantToken`` inside a ``ServerFrame``.
    """
    while True:
        try:
            ct = await read_frame(reader)
        except (FramingError, FrameTooLargeError, asyncio.IncompleteReadError):
            return
        try:
            pt = noise.decrypt(ct)
        except HandshakeError:
            return
        try:
            req = ClientFrame.FromString(pt)
        except Exception:
            return
        if req.WhichOneof("payload") != "user_message":
            # Only echo user_message frames; ignore Hello/end for this fixture.
            continue
        text = req.user_message.text
        turn_id = req.user_message.turn_id
        resp = ServerFrame(
            token=AssistantToken(text=text, sequence=0, turn_id=turn_id)
        )
        try:
            out = noise.encrypt(resp.SerializeToString())
            await write_frame(writer, out)
        except (HandshakeError, ConnectionError, OSError):
            return


def send_then_close(
    frames: list[ServerFrame],
) -> Callable[
    [XXResponder, asyncio.StreamReader, asyncio.StreamWriter],
    Awaitable[None],
]:
    """Build an ``on_session`` that sends each frame in order, then closes."""

    async def _h(noise, reader, writer):
        for f in frames:
            ct = noise.encrypt(f.SerializeToString())
            await write_frame(writer, ct)

    return _h
