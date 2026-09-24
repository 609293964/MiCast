"""Source-stall silence feed and FLAC delay-line activation.

Plan item 1 (encoder-side keepalive): when the PCM source stops delivering
while HTTP clients are connected, the pipeline pumps synthesize zero-PCM
chunks so the running encoder keeps producing valid frames (flac has no
precomputable silence; a starved Xiaomi pull player drops the response in
~2s). Synthesized chunks advance the pacing clock but NOT the stall-watchdog
timestamp, so a genuinely wedged source still gets restarted. Resumed source
data flows at the pacing clock — no burst.

Plan item 2 (flac delay line): clients of a format without a nominal byte
rate get the reserve/lag delay line only once the broadcast-observed rate EMA
is well-sampled and above an absolute floor; otherwise they stay on the
transparent passthrough.
"""

import asyncio

import pytest
from fastapi import Request

from micast.audio_encoder import StreamFormat
from micast.speaker_pipeline import SOURCE_SILENCE_CHUNK_BYTES, SpeakerPipeline
from micast.stream_server import (
    DELAY_LINE_MIN_BYTES_PER_SECOND,
    DELAY_LINE_MIN_SAMPLES,
    StreamServer,
)


class _RecordingWriter:
    def __init__(self):
        self.chunks: list[bytes] = []

    def write(self, data: bytes) -> None:
        self.chunks.append(bytes(data))

    def write_eof(self) -> None:
        pass

    async def drain(self) -> None:
        return None


class _FakeStreamServer:
    def __init__(self, clients: int):
        self._clients = clients
        self.broadcasts: list[bytes] = []

    def client_count(self, stream_id: str) -> int:
        return self._clients

    async def broadcast(self, stream_id: str, chunk: bytes) -> None:
        self.broadcasts.append(chunk)


def _pipeline(client_count: int) -> tuple[SpeakerPipeline, _RecordingWriter, _FakeStreamServer]:
    pipeline = object.__new__(SpeakerPipeline)
    pipeline._running = True
    pipeline._stream_id = "airplay2"
    pipeline._input_sample_rate = 48000
    pipeline._pace_source = False  # no pacing sleeps: the stall timeout paces
    pipeline._input_volume = 100
    pipeline._status = "running"
    pipeline._last_feed_at = 0.0
    pipeline._stall_armed = True
    pipeline._spectrum = None
    pipeline._stream_server = _FakeStreamServer(client_count)
    writer = _RecordingWriter()
    return pipeline, writer, pipeline._stream_server


@pytest.mark.asyncio
async def test_encoder_pump_feeds_silence_while_clients_connected():
    pipeline, writer, _ = _pipeline(client_count=1)
    reader = asyncio.StreamReader()
    reader.feed_data(b"\x7f" * SOURCE_SILENCE_CHUNK_BYTES)  # one real chunk

    task = asyncio.create_task(pipeline._pump_source_to_encoder(reader, writer))
    # One real chunk, then synthesized silence every ~0.17s of stall.
    deadline = asyncio.get_running_loop().time() + 5
    while len(writer.chunks) < 4 and asyncio.get_running_loop().time() < deadline:
        await asyncio.sleep(0.02)

    assert len(writer.chunks) >= 4
    assert writer.chunks[0] == b"\x7f" * SOURCE_SILENCE_CHUNK_BYTES
    for silence in writer.chunks[1:]:
        assert silence == b"\x00" * SOURCE_SILENCE_CHUNK_BYTES
    # Synthesized silence must NOT advance the stall-watchdog timestamp: a
    # genuinely wedged source still needs the restart path to fire (~8s of
    # no REAL bytes).
    import time

    assert time.monotonic() - pipeline._last_feed_at > 0.3

    # Source resumes: the real chunk is delivered after the silence, no burst
    # (each synthesized chunk was already paced at one chunk period).
    reader.feed_data(b"\x55" * SOURCE_SILENCE_CHUNK_BYTES)
    deadline = asyncio.get_running_loop().time() + 3
    while writer.chunks[-1] != b"\x55" * SOURCE_SILENCE_CHUNK_BYTES and (
        asyncio.get_running_loop().time() < deadline
    ):
        await asyncio.sleep(0.02)
    assert writer.chunks[-1] == b"\x55" * SOURCE_SILENCE_CHUNK_BYTES

    task.cancel()
    await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_encoder_pump_stays_quiet_without_clients():
    pipeline, writer, _ = _pipeline(client_count=0)
    reader = asyncio.StreamReader()
    reader.feed_data(b"\x7f" * SOURCE_SILENCE_CHUNK_BYTES)

    task = asyncio.create_task(pipeline._pump_source_to_encoder(reader, writer))
    await asyncio.sleep(0.7)  # > one chunk period: no silence may be fed
    assert len(writer.chunks) == 1  # only the real chunk

    task.cancel()
    await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_raw_pump_broadcasts_silence_while_clients_connected():
    pipeline, _, server = _pipeline(client_count=1)
    reader = asyncio.StreamReader()
    reader.feed_data(b"\x7f" * SOURCE_SILENCE_CHUNK_BYTES)

    task = asyncio.create_task(pipeline._pump_source_to_stream(reader))
    deadline = asyncio.get_running_loop().time() + 5
    while len(server.broadcasts) < 5 and asyncio.get_running_loop().time() < deadline:
        await asyncio.sleep(0.02)

    assert len(server.broadcasts) >= 5  # header + real + ≥3 silence
    assert server.broadcasts[0].startswith(b"RIFF")  # streaming WAV header
    assert server.broadcasts[1] == b"\x7f" * SOURCE_SILENCE_CHUNK_BYTES
    for silence in server.broadcasts[2:]:
        assert silence == b"\x00" * SOURCE_SILENCE_CHUNK_BYTES

    task.cancel()
    await asyncio.gather(task, return_exceptions=True)


def _request() -> Request:
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/stream/airplay2",
            "query_string": b"",
            "headers": [],
            "client": ("192.168.0.128", 9),
        }
    )


def _flac_server() -> StreamServer:
    server = StreamServer()
    server.register_stream("airplay2", StreamFormat("audio/flac", "flac", None))
    return server


@pytest.mark.asyncio
async def test_flac_delay_line_activates_with_stable_observed_rate():
    """After enough steady broadcasts the flac delay line throttles a burst:
    the excess is skipped to live once (lag drop), not delivered late."""
    server = _flac_server()
    rate = 100_000
    server._observed_byte_rate["airplay2"] = float(rate)
    server._rate_samples["airplay2"] = DELAY_LINE_MIN_SAMPLES

    response = await server._serve_stream(_request(), "airplay2")
    iterator = response.body_iterator
    state = next(iter(server._client_delay.values()))

    # Mirror test_delay_line_caps_lag: one huge burst must hit the ceiling.
    from micast.config import settings
    from micast.stream_server import CLIENT_MAX_LAG_SECONDS

    reserve = int(rate * settings.stream_buffer_seconds)
    ceiling = reserve + int(rate * CLIENT_MAX_LAG_SECONDS)
    server._broadcast_to("airplay2", b"\x01" * (ceiling * 2))

    received = bytearray()
    for _ in range(1000):
        try:
            received.extend(await asyncio.wait_for(anext(iterator), timeout=0.5))
        except TimeoutError:
            break

    assert state["lag_drops"] == 1  # burst skipped to live, like mp3/wav
    assert 0 < len(received) <= ceiling - reserve + 1024
    await iterator.aclose()


@pytest.mark.asyncio
async def test_flac_passthrough_during_rate_warmup():
    """Before the EMA has enough samples the client stays transparent: a
    burst passes through whole, unthrottled."""
    server = _flac_server()
    server._observed_byte_rate["airplay2"] = 100_000.0
    server._rate_samples["airplay2"] = DELAY_LINE_MIN_SAMPLES - 1

    response = await server._serve_stream(_request(), "airplay2")
    iterator = response.body_iterator
    state = next(iter(server._client_delay.values()))

    burst = b"\x02" * 500_000
    server._broadcast_to("airplay2", burst)
    received = bytearray()
    for _ in range(10):
        try:
            received.extend(await asyncio.wait_for(anext(iterator), timeout=0.5))
        except TimeoutError:
            break

    assert bytes(received) == burst  # nothing held back, nothing dropped
    assert state["lag_drops"] == 0
    await iterator.aclose()


@pytest.mark.asyncio
async def test_flac_passthrough_when_observed_rate_below_floor():
    """A near-silence EMA (below the absolute floor) must not throttle."""
    server = _flac_server()
    server._observed_byte_rate["airplay2"] = float(DELAY_LINE_MIN_BYTES_PER_SECOND - 1)
    server._rate_samples["airplay2"] = DELAY_LINE_MIN_SAMPLES * 5

    response = await server._serve_stream(_request(), "airplay2")
    iterator = response.body_iterator
    state = next(iter(server._client_delay.values()))

    burst = b"\x03" * 200_000
    server._broadcast_to("airplay2", burst)
    received = bytearray()
    for _ in range(10):
        try:
            received.extend(await asyncio.wait_for(anext(iterator), timeout=0.5))
        except TimeoutError:
            break

    assert bytes(received) == burst
    assert state["lag_drops"] == 0
    await iterator.aclose()


@pytest.mark.asyncio
async def test_broadcast_accumulates_rate_samples_for_delay_line():
    server = _flac_server()
    assert server._delay_line_byte_rate("airplay2") is None
    for _ in range(DELAY_LINE_MIN_SAMPLES):
        await server.broadcast("airplay2", b"\x00" * 20_000)
        await asyncio.sleep(0.01)  # ~2 MB/s observed
    rate = server._delay_line_byte_rate("airplay2")
    assert rate is not None and rate >= DELAY_LINE_MIN_BYTES_PER_SECOND
    # Re-registration resets the sample count: warmup starts over.
    server.register_stream("airplay2", StreamFormat("audio/flac", "flac", None))
    assert server._delay_line_byte_rate("airplay2") is None
