"""Pure-bytes tests for ``wire.framing``.

These tests don't open a socket; they use ``asyncio.StreamReader``
fed directly to verify the codec's behaviour at the byte level.
"""

from __future__ import annotations

import asyncio
import struct

import pytest

from wire.framing import (
    FrameTooLargeError,
    LENGTH_PREFIX_BYTES,
    MAX_FRAME_SIZE,
    encode_length_prefix,
    read_frame,
    write_frame,
)


def _stream_reader_with(data: bytes) -> asyncio.StreamReader:
    reader = asyncio.StreamReader()
    reader.feed_data(data)
    reader.feed_eof()
    return reader


@pytest.mark.asyncio
async def test_read_frame_roundtrip():
    body = b"hello, frames"
    reader = _stream_reader_with(struct.pack(">I", len(body)) + body)
    out = await read_frame(reader)
    assert out == body


@pytest.mark.asyncio
async def test_read_frame_zero_length_legal():
    reader = _stream_reader_with(struct.pack(">I", 0))
    out = await read_frame(reader)
    assert out == b""


@pytest.mark.asyncio
async def test_read_frame_oversize_rejected_pre_body():
    # Announce 1 byte over the cap — body bytes are NOT supplied;
    # read_frame must reject before attempting to read them.
    reader = _stream_reader_with(struct.pack(">I", MAX_FRAME_SIZE + 1))
    with pytest.raises(FrameTooLargeError) as exc:
        await read_frame(reader)
    assert exc.value.announced == MAX_FRAME_SIZE + 1
    assert exc.value.limit == MAX_FRAME_SIZE


@pytest.mark.asyncio
async def test_read_frame_truncated_header():
    reader = _stream_reader_with(b"\x00\x00")  # only 2 of 4 bytes
    with pytest.raises(asyncio.IncompleteReadError):
        await read_frame(reader)


@pytest.mark.asyncio
async def test_read_frame_truncated_body():
    reader = _stream_reader_with(struct.pack(">I", 16) + b"only-8b!")
    with pytest.raises(asyncio.IncompleteReadError):
        await read_frame(reader)


@pytest.mark.asyncio
async def test_write_frame_oversize_rejected():
    # write_frame must refuse oversize without touching the writer.
    body = b"x" * (MAX_FRAME_SIZE + 1)

    class _StubWriter:
        def write(self, _data: bytes) -> None:
            raise AssertionError("should not be called for oversize body")

        async def drain(self) -> None:
            raise AssertionError("should not be called for oversize body")

    with pytest.raises(FrameTooLargeError):
        await write_frame(_StubWriter(), body)  # type: ignore[arg-type]


def test_encode_length_prefix_format():
    assert LENGTH_PREFIX_BYTES == 4
    assert encode_length_prefix(0) == b"\x00\x00\x00\x00"
    assert encode_length_prefix(1) == b"\x00\x00\x00\x01"
    # Big-endian byte ordering, within the 1 MiB cap.
    assert encode_length_prefix(0x000F0E0D) == b"\x00\x0f\x0e\x0d"
    assert encode_length_prefix(MAX_FRAME_SIZE) == struct.pack(">I", MAX_FRAME_SIZE)


def test_encode_length_prefix_oversize():
    with pytest.raises(FrameTooLargeError):
        encode_length_prefix(MAX_FRAME_SIZE + 1)
    with pytest.raises(FrameTooLargeError):
        encode_length_prefix(-1)


