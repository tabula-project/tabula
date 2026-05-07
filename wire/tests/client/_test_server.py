"""Minimal Noise XX responder used as a test fixture for the client dialer.

This is a stand-in for the real server listener that lands in #20. The
client dialer test (#27) calls for a loopback test against a real listener;
since #20 has not landed yet, this fixture provides exactly enough server
behaviour to exercise the client end-to-end:

- accept a TCP connection
- run the Noise XX responder handshake
- send the configured ``ServerFrame`` payloads
- echo any received ``ClientFrame`` back as ``ServerFrame`` (kind ``echo``)

When #20 merges, tests in this file should be migrated to the real
``tabula_wire.server.listener.serve`` and this fixture deleted.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Awaitable, Callable

from tabula_wire.crypto.noise_xx import KeyPair, NoiseError, XXResponder
from tabula_wire.framing import FrameError, FrameTooLarge, encode_frame, read_frame
from tabula_wire.proto.v1 import ClientFrame, ServerFrame


@dataclass
class FakeServer:
    """Test fixture server. Renamed from ``TestServer`` because pytest
    auto-collects classes whose names start with ``Test`` and complains."""

    static_key: KeyPair
    server: asyncio.base_events.Server | None = None
    on_session: Callable[[XXResponder, asyncio.StreamReader, asyncio.StreamWriter], Awaitable[None]] | None = None
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
            except (FrameError, FrameTooLarge, asyncio.IncompleteReadError):
                return
            try:
                noise.read_message(m1)
            except NoiseError:
                return

            if self.skip_handshake_msg2:
                # Drop the connection mid-handshake. Client should timeout.
                return

            # Msg 2
            m2 = noise.write_message(b"")
            writer.write(encode_frame(m2))
            await writer.drain()

            # Msg 3
            try:
                m3 = await read_frame(reader)
            except (FrameError, FrameTooLarge, asyncio.IncompleteReadError):
                return
            try:
                noise.read_message(m3)
            except NoiseError:
                return

            assert noise.handshake_finished

            if self.inject_garbage_after_handshake:
                # Send an unencrypted blob the client will try to Noise-decrypt
                # and reject as ProtocolError. We frame it correctly so the
                # framing layer accepts the bytes; the noise decrypt is what
                # should fail.
                writer.write(encode_frame(b"\x00" * 32))
                await writer.drain()
                return

            if self.on_session is not None:
                await self.on_session(noise, reader, writer)
        except Exception:
            # Test fixture: swallow all exceptions on the server side so the
            # listener does not crash the event loop. Real #20 server has
            # the same swallowing requirement; failures should just close
            # the per-connection state.
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
    """Read ClientFrames, echo them back as ServerFrames (kind='echo')."""
    while True:
        try:
            ct = await read_frame(reader)
        except (FrameError, FrameTooLarge, asyncio.IncompleteReadError):
            return
        try:
            pt = noise.decrypt(ct)
        except NoiseError:
            return
        try:
            req = ClientFrame.FromString(pt)
        except Exception:
            return
        resp = ServerFrame(kind="echo", payload=req.payload)
        try:
            out = noise.encrypt(resp.SerializeToString())
            writer.write(encode_frame(out))
            await writer.drain()
        except (NoiseError, ConnectionError, OSError):
            return


async def send_then_close(
    frames: list[ServerFrame],
) -> Callable[[XXResponder, asyncio.StreamReader, asyncio.StreamWriter], Awaitable[None]]:
    """Build an ``on_session`` that sends each frame in order, then closes."""

    async def _h(noise, reader, writer):
        for f in frames:
            ct = noise.encrypt(f.SerializeToString())
            writer.write(encode_frame(ct))
            await writer.drain()

    return _h
