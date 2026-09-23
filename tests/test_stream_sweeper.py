"""Stale-client sweeper: idle HTTP clients with no owning session get kicked."""

import asyncio
import time
from unittest.mock import AsyncMock

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


def test_stale_session_timeout_is_configurable(monkeypatch):
    monkeypatch.setattr(settings, "stale_session_timeout", 10)
    bridge = _bridge(monkeypatch, "r1")
    queue = _attach_client(bridge, "r1")
    bridge._active_sessions.add("r1")
    # Simulate a sender that has been paused for 30s: past the 10s threshold.
    bridge._stale_active_since["r1"] = time.monotonic() - 30

    bridge._sweep_stale_once()

    assert queue.get_nowait() is None  # expired session's client was kicked
    assert "r1" not in bridge._active_sessions


def test_stale_session_timeout_default_keeps_recently_paused_session(monkeypatch):
    """Well under the default 60s: same sweep shape must keep the session."""
    bridge = _bridge(monkeypatch, "r1")
    queue = _attach_client(bridge, "r1")
    bridge._active_sessions.add("r1")
    bridge._stale_active_since["r1"] = time.monotonic() - 30

    bridge._sweep_stale_once()

    assert queue.empty()
    assert "r1" in bridge._active_sessions


def test_stale_session_timeout_zero_disables_expiry(monkeypatch):
    monkeypatch.setattr(settings, "stale_session_timeout", 0)
    bridge = _bridge(monkeypatch, "r1")
    queue = _attach_client(bridge, "r1")
    bridge._active_sessions.add("r1")
    bridge._stale_active_since["r1"] = time.monotonic() - 3600

    bridge._sweep_stale_once()

    assert queue.empty()
    assert "r1" in bridge._active_sessions


def test_stale_session_timeout_route_validates_and_persists(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from micast.config import Settings
    from micast.routes import config as config_routes

    monkeypatch.setattr(config_routes.settings, "stale_session_timeout", 60)
    monkeypatch.setattr(Settings, "save_to_file", lambda self: None)
    app = FastAPI()
    app.include_router(config_routes.install(AsyncMock()))
    client = TestClient(app)

    response = client.post("/api/config/stale-session-timeout", json={"seconds": 120})
    assert response.status_code == 200
    assert config_routes.settings.stale_session_timeout == 120

    for bad in (-1, "abc", None):
        response = client.post("/api/config/stale-session-timeout", json={"seconds": bad})
        assert response.status_code == 400
    assert config_routes.settings.stale_session_timeout == 120


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


def test_group_expiry_timer_waits_until_no_member_flows(monkeypatch):
    """One flowing sink keeps the whole group's session alive: the expiry
    timer must not even start (nor be restarted) while any member still
    receives audio, no matter how long another member has been silent."""
    monkeypatch.setattr(settings, "stale_session_timeout", 10)
    bridge = _bridge(monkeypatch, "r1")
    idle_queue = _attach_client(bridge, "r1-L")
    _attach_client(bridge, "r1-R")
    bridge._active_sessions.add("r1")
    bridge._stale_active_since["r1"] = time.monotonic() - 3600  # stale from before

    bridge._stream_server._last_broadcast["r1-R"] = time.monotonic()  # R plays
    bridge._sweep_stale_once()

    assert "r1" in bridge._active_sessions
    assert "r1" not in bridge._stale_active_since  # timer reset, not aged
    assert idle_queue.empty()  # silent member kept while the group plays


def test_group_expires_only_after_all_members_silent_past_timeout(monkeypatch):
    """All members silent: the session expires once the fully-silent window
    exceeds the configured timeout."""
    monkeypatch.setattr(settings, "stale_session_timeout", 10)
    bridge = _bridge(monkeypatch, "r1")
    _attach_client(bridge, "r1-L")
    _attach_client(bridge, "r1-R")
    bridge._active_sessions.add("r1")
    bridge._stale_active_since["r1"] = time.monotonic() - 30

    bridge._sweep_stale_once()

    assert "r1" not in bridge._active_sessions


def test_recently_silent_group_is_kept(monkeypatch):
    """All members silent but inside the timeout: session survives and the
    timer is preserved (not reset) across sweeps."""
    monkeypatch.setattr(settings, "stale_session_timeout", 60)
    bridge = _bridge(monkeypatch, "r1")
    _attach_client(bridge, "r1-L")
    _attach_client(bridge, "r1-R")
    bridge._active_sessions.add("r1")
    started = time.monotonic() - 30
    bridge._stale_active_since["r1"] = started

    bridge._sweep_stale_once()

    assert "r1" in bridge._active_sessions
    assert bridge._stale_active_since["r1"] == started
