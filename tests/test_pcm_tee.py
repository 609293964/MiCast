import asyncio

import pytest

from micast.pcm_tee import BoundedPCMReader, PCMTee


@pytest.mark.asyncio
async def test_bounded_pcm_reader_keeps_live_edge_and_eof():
    reader = BoundedPCMReader(max_chunks=2)
    reader.feed_data(b"old")
    reader.feed_data(b"middle")
    reader.feed_data(b"latest")
    reader.feed_eof()

    assert await reader.read() == b"latest"
    assert await reader.read() == b""
    assert reader.at_eof()


@pytest.mark.asyncio
async def test_pcm_tee_outputs_are_bounded():
    source = asyncio.StreamReader()
    tee = PCMTee(source, outputs=2)
    tee.start()
    source.feed_data(b"x" * 32768 * 100)
    source.feed_eof()
    await tee._task

    assert tee.outputs[0]._queue.qsize() <= 64
    assert tee.outputs[1]._queue.qsize() <= 64
