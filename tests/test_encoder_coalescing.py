"""Encoder-output coalescing: muxer flush granularity must not reach the
stream server as hundreds of tiny per-client queue items.

Measured on the real chain (flac/48000): one muxed frame produced ~55 output
writes whose sizes vary wildly (tiny control writes up to full frames). Each
write became one broadcast and one per-client queue item, so a 256-item queue
held anywhere from milliseconds to minutes of audio depending on write sizes,
and every tiny write multiplied drop accounting by an arbitrary factor.
"""

import asyncio
from unittest.mock import AsyncMock

import pytest

from micast.speaker_pipeline import SpeakerPipeline


class _FakeEncodedReader:
    """Async reader with read()/read_nowait() over staged chunk lists. The
    stages reproduce encoder cadence: everything pending in one flush, then a
    gap, then the next frame."""

    def __init__(self, stages: list[list[bytes]]):
        self._stages = [list(stage) for stage in stages]

    async def read(self, n: int = -1) -> bytes:
        await asyncio.sleep(0.01)  # encoder produces the next frame later
        while self._stages and not self._stages[0]:
            self._stages.pop(0)
        if not self._stages:
            return b""
        return self._stages[0].pop(0)

    def read_nowait(self) -> bytes:
        # Only the CURRENT flush is pending; a later frame is not merged.
        if not self._stages or not self._stages[0]:
            return b""
        return self._stages[0].pop(0)


@pytest.mark.asyncio
async def test_pump_coalesces_immediately_pending_encoder_writes():
    pipeline = object.__new__(SpeakerPipeline)
    pipeline._running = True
    pipeline._stream_id = "airplay2"
    pipeline._status = "running"
    broadcasts: list[bytes] = []
    pipeline._stream_server = AsyncMock()
    pipeline._stream_server.broadcast = AsyncMock(
        side_effect=lambda stream_id, chunk: broadcasts.append(chunk)
    )
    # One encoded frame flushed as many small writes; the next frame arrives
    # only after a cadence gap.
    small = [b"\xAA" * 640, b"\xBB" * 512, b"\xCC" * 2048] + [b"\xDD" * 100] * 50
    reader = _FakeEncodedReader([small, [b"\xEE" * 40000]])

    task = asyncio.create_task(pipeline._pump_encoder_to_stream(reader))
    await asyncio.wait_for(task, timeout=5)

    small_total = 640 + 512 + 2048 + 50 * 100
    assert broadcasts == [b"".join(small), b"\xEE" * 40000]
    assert len(broadcasts[0]) == small_total


@pytest.mark.asyncio
async def test_pump_passes_single_frame_unchanged():
    pipeline = object.__new__(SpeakerPipeline)
    pipeline._running = True
    pipeline._stream_id = "r1"
    pipeline._status = "running"
    broadcasts: list[bytes] = []
    pipeline._stream_server = AsyncMock()
    pipeline._stream_server.broadcast = AsyncMock(
        side_effect=lambda stream_id, chunk: broadcasts.append(chunk)
    )
    frame = b"\xAB" * 32768
    reader = _FakeEncodedReader([[frame]])

    task = asyncio.create_task(pipeline._pump_encoder_to_stream(reader))
    await asyncio.wait_for(task, timeout=5)

    assert broadcasts == [frame]
