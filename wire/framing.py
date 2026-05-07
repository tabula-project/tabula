"""Length-prefix framing codec shared by client and server.

Wire format: 4-byte big-endian unsigned length, followed by exactly that many
bytes of payload. The payload is opaque to this module — the caller is
responsible for any encryption/serialization wrapping.

This module owns three things and nothing else:

1. The constant ``MAX_FRAME_SIZE`` (default 1 MiB) — frames larger than this
   are rejected pre-decrypt to keep an attacker from wedging the reader.
2. ``encode_frame(payload)`` — bytes -> framed bytes.
3. ``read_frame(reader)`` — async helper that reads exactly one frame off an
   ``asyncio.StreamReader`` and returns the payload bytes, raising
   :class:`FrameTooLarge` if the length prefix exceeds ``MAX_FRAME_SIZE`` and
   :class:`FrameError` on EOF mid-frame.

The companion server issue (#20) calls out that we should share a single
length-prefix codec rather than duplicate; this module is that single source
of truth.
"""

from __future__ import annotations

import asyncio
import struct

# 1 MiB. Matches the value the server issue (#20) calls for; larger frames
# are rejected before they hit Noise decrypt to bound memory use.
MAX_FRAME_SIZE: int = 1 * 1024 * 1024

# 4-byte big-endian unsigned length prefix.
_LEN_FMT = ">I"
_LEN_SIZE = 4


class FrameError(Exception):
    """Generic framing error (e.g. unexpected EOF mid-frame)."""


class FrameTooLarge(FrameError):
    """The peer announced a frame larger than ``MAX_FRAME_SIZE``."""


def encode_frame(payload: bytes) -> bytes:
    """Return ``payload`` prefixed by its 4-byte big-endian length.

    Raises :class:`FrameTooLarge` if the payload exceeds ``MAX_FRAME_SIZE``.
    """
    if len(payload) > MAX_FRAME_SIZE:
        raise FrameTooLarge(
            f"frame size {len(payload)} exceeds MAX_FRAME_SIZE {MAX_FRAME_SIZE}"
        )
    return struct.pack(_LEN_FMT, len(payload)) + payload


async def read_frame(
    reader: asyncio.StreamReader,
    *,
    max_size: int = MAX_FRAME_SIZE,
) -> bytes:
    """Read exactly one length-prefixed frame from ``reader``.

    Returns the payload bytes (without the length prefix).

    Raises:
        FrameTooLarge: if the announced length exceeds ``max_size``.
        FrameError: on EOF mid-frame.
    """
    header = await reader.readexactly(_LEN_SIZE)
    (length,) = struct.unpack(_LEN_FMT, header)
    if length > max_size:
        raise FrameTooLarge(
            f"announced frame size {length} exceeds max_size {max_size}"
        )
    if length == 0:
        return b""
    try:
        payload = await reader.readexactly(length)
    except asyncio.IncompleteReadError as exc:  # pragma: no cover - thin wrap
        raise FrameError(
            f"EOF after {len(exc.partial)}/{length} bytes of frame payload"
        ) from exc
    return payload


__all__ = [
    "MAX_FRAME_SIZE",
    "FrameError",
    "FrameTooLarge",
    "encode_frame",
    "read_frame",
]
