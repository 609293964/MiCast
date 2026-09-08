"""Built-in test tone: valid finite WAV with actual audio content."""

import struct

import httpx
import pytest

from micast.stream_server import StreamServer
from micast.test_tone import test_tone_wav as make_test_tone_wav


def test_tone_is_valid_wav_with_audio():
    data = make_test_tone_wav()
    assert data[:4] == b"RIFF" and data[8:12] == b"WAVE"
    data_size = struct.unpack("<I", data[40:44])[0]
    assert data_size == len(data) - 44
    # Not silence: some sample must deviate from zero.
    samples = struct.unpack(f"<{data_size // 2}h", data[44:])
    assert any(abs(sample) > 1000 for sample in samples)


def test_tone_is_cached():
    assert make_test_tone_wav() is make_test_tone_wav()


@pytest.mark.asyncio
async def test_diagnostic_tone_is_served_on_stream_server():
    server = StreamServer()
    transport = httpx.ASGITransport(app=server._app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/diagnostic/builtin.wav")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("audio/wav")
    assert response.content == make_test_tone_wav()


@pytest.mark.asyncio
async def test_uploaded_diagnostic_media_is_served_and_revoked(tmp_path):
    media = tmp_path / "sample.mp3"
    media.write_bytes(b"ID3-test-audio")
    server = StreamServer()
    server.register_diagnostic_media("token", media, "audio/mpeg")
    transport = httpx.ASGITransport(app=server._app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/diagnostic/media/token")
        server.unregister_diagnostic_media("token")
        missing = await client.get("/diagnostic/media/token")

    assert response.status_code == 200
    assert response.content == b"ID3-test-audio"
    assert missing.status_code == 404
