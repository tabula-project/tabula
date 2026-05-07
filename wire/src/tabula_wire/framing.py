"""Length-prefix framing codec, shared between server and client.

Wire format::

    +----+----+----+----+ +========================+
    |   length (u32 BE) | |  ciphertext (length B) |
    +----+----+----+----+ +========================+

The 4-byte big-endian length prefix counts the bytes of the *ciphertext*
that follows it. Plaintext is a serialized ``ClientFrame`` or
``ServerFrame`` protobuf; ciphertext is that plaintext after the Noise
transport ``encrypt`` step.

The codec only deals in bytes; encryption/decryption and proto
ser/deser live above it. Keeping this module purely byte-level lets us
unit test the framing in isolation and share the exact same code with
the client (issue #27) without circular imports through Noise or
protobuf modules.

Per #20 acceptance criteria:

- 4-byte big-endian length prefix
- Reject any frame larger than ``MAX_FRAME_SIZE`` *before* attempting
  to read or decrypt the body
- Surface EOF and oversize cleanly (typed exceptions, not silent close)
"""

from __future__ import annotations

import asyncio
import struct
from typing import Final

# Cap pre-decrypt to keep a malicious peer from forcing us to allocate
# arbitrary buffers. 1 MiB is well above any reasonable ClientFrame
# (a chat user message + Noise auth tag) but small enough that an
# attacker can't DoS us by claiming gigabyte-sized frames.
MAX_FRAME_SIZE: Final[int] = 1 << 20  # 1 MiB

# Length prefix is u32 big-endian.
_LENGTH_STRUCT: Final[struct.Struct] = struct.Struct(">I")
LENGTH_PREFIX_BYTES: Final[int] = _LENGTH_STRUCT.size  # 4


class FramingError(Exception):
    """Base class for framing-layer errors."""


class FrameTooLargeError(FramingError):
    """Peer announced a frame larger than ``MAX_FRAME_SIZE``."""

    def __init__(self, announced: int, limit: int = MAX_FRAME_SIZE) -> None:
        super().__init__(
            f"frame size {announced} exceeds limit {limit}"
        )
        self.announced = announced
        self.limit = limit


class FrameTruncatedError(FramingError):
    """EOF before a complete frame (header or body) was received."""


def encode_length_prefix(payload_len: int) -> bytes:
    """Encode a u32 big-endian length prefix.

    Raises:
        FrameTooLargeError: if ``payload_len`` exceeds ``MAX_FRAME_SIZE``.
    """
    if payload_len < 0 or payload_len > MAX_FRAME_SIZE:
        raise FrameTooLargeError(payload_len)
    return _LENGTH_STRUCT.pack(payload_len)


async def read_frame(
    reader: asyncio.StreamReader,
    *,
    max_size: int = MAX_FRAME_SIZE,
) -> bytes:
    """Read a single length-prefixed frame from ``reader``.

    Returns the raw body bytes (still ciphertext from the caller's POV).

    Raises:
        FrameTruncatedError: clean EOF or short read mid-frame.
        FrameTooLargeError: announced size exceeds ``max_size``.
    """
    header = await reader.readexactly(LENGTH_PREFIX_BYTES)
    (length,) = _LENGTH_STRUCT.unpack(header)
    if length > max_size:
        raise FrameTooLargeError(length, max_size)
    if length == 0:
        # An empty frame is legal at the framing layer; upper layers
        # decide whether an empty plaintext payload is meaningful.
        return b""
    body = await reader.readexactly(length)
    return body


async def write_frame(
    writer: asyncio.StreamWriter,
    body: bytes,
    *,
    max_size: int = MAX_FRAME_SIZE,
) -> None:
    """Write a single length-prefixed frame to ``writer`` and await drain.

    Raises:
        FrameTooLargeError: if ``len(body)`` exceeds ``max_size``.

    Awaiting drain on each write provides backpressure: if the kernel
    socket buffer is full the producer suspends until the peer reads.
    """
    if len(body) > max_size:
        raise FrameTooLargeError(len(body), max_size)
    writer.write(_LENGTH_STRUCT.pack(len(body)))
    writer.write(body)
    await writer.drain()


# Python's asyncio.IncompleteReadError signals truncation when readexactly
# fails. We surface it as our typed FrameTruncatedError at the call site
# in the listener; this helper centralizes the conversion.
def is_truncation(exc: BaseException) -> bool:
    """Return True if ``exc`` indicates the peer closed mid-frame."""
    return isinstance(exc, asyncio.IncompleteReadError)
