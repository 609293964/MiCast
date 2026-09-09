"""Stale-client sweeper: idle HTTP clients with no owning session get kicked."""

import asyncio
import time

from micast.audio_bridge import AudioBridge, _stream_owner
from micast.audio_encoder import raw_pcm_format
from micast.config import AirPlay2InstanceConfig, ReceiverConfig, settings


def _bridge(monkeypatch, *receiver_ids: str) -> AudioBridge:
    monkeypatch.setattr(
        settings,
        "receivers",
        [ReceiverConfig(id=rid, name=rid) for rid in receiver_ids],
    )
    return AudioBridge()


def _attach_client(bridge: AudioBridge, stream_id: str) -> asyncio.Queue:
    bridge._stream_server.register_stream(stream_id, raw_pcm_format(48000))
    queue: asyncio.Queue = asyncio.Queue()
    bridge._stream_server._clients[stream_id].add(queue)
    return queue


def test_stream_owner_matches_longest_receiver_prefix():
    ids = ["speaker-abc", "speaker", "r1"]
    assert _stream_owner("speaker-abc-q1", ids) == "speaker-abc"
    assert _stream_owner("speaker-abc", ids) == "speaker-abc"
    assert _stream_owner("r1-L", ids) == "r1"
    assert _stream_owner("unknown-L", ids) is None


def test_idle_client_without_session_is_kicked(monkeypatch):
    bridge = _bridge(monkeypatch, "r1")
    queue = _attach_client(bridge, "r1")
    variant_queue = _attach_client(bridge, "r1-L")
    bridge._sweep_stale_once()

    assert queue.get_nowait() is None  # EOF marker from the kick
    assert variant_queue.get_nowait() is None


def test_idle_client_with_active_session_is_kept(monkeypatch):
    bridge = _bridge(monkeypatch, "r1")
    queue = _attach_client(bridge, "r1")
    bridge._active_sessions.add("r1")  # paused sender: speaker is waiting by design

    bridge._sweep_stale_once()

    assert queue.empty()


def test_flowing_stream_is_never_kicked(monkeypatch):
    bridge = _bridge(monkeypatch, "r1")
    queue = _attach_client(bridge, "r1")
    bridge._stream_server._last_broadcast["r1"] = time.monotonic()

    bridge._sweep_stale_once()

    assert queue.empty()


def test_unknown_owner_stream_is_left_alone(monkeypatch):
    bridge = _bridge(monkeypatch, "r1")
    queue = _attach_client(bridge, "airplay2-xyz")
    bridge._sweep_stale_once()

    assert queue.empty()


def test_idle_airplay2_client_without_active_session_is_kicked(monkeypatch):
    bridge = _bridge(monkeypatch, "r1")
    monkeypatch.setattr(
        settings,
        "airplay2_instances",
        [
            AirPlay2InstanceConfig(
                id="airplay2-xyz",
                name="MiCast",
                enabled=True,
                target_type="speaker",
                target_id="speaker-1",
            )
        ],
    )
    queue = _attach_client(bridge, "airplay2-xyz")

    bridge._sweep_stale_once()

    assert queue.get_nowait() is None
