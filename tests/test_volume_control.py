"""Volume policy tests with fake pipelines/devices; never contact speakers."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from micast.audio_bridge import AudioBridge
from micast.config import settings
from micast.routes import playback


def test_sender_mode_is_latched_and_linked_does_not_attenuate(monkeypatch):
    async def run():
        bridge = object.__new__(AudioBridge)
        gains = []
        bridge._pipelines = {
            "one-L": SimpleNamespace(set_input_volume=gains.append, set_loudness_level=lambda _p: None)
        }
        bridge._volume_modes = {}
        bridge._sender_volumes = {}
        bridge._airplay_targets = None
        bridge.on_receiver_volume = AsyncMock()
        monkeypatch.setattr(settings, "sender_volume_mode", "independent")
        await bridge._local_volume("one", 40)
        assert gains[-1] == 40
        monkeypatch.setattr(settings, "sender_volume_mode", "linked")
        await bridge._local_volume("one", 30)
        assert gains[-1] == 30  # active session retains its policy
        bridge._volume_modes["one"] = "linked"
        await bridge._local_volume("one", 25)
        assert gains[-1] == 100  # no double attenuation
        bridge.on_receiver_volume.assert_awaited_with("one", 25)
    asyncio.run(run())


def test_relative_volume_preserves_difference_and_clamps():
    async def run():
        values = {"a": 40, "b": 30, "c": 98}
        async def get(did, refresh=False):
            return values[did]
        async def put(did, value):
            values[did] = value
            return value
        manager = SimpleNamespace(get_volume=get, set_volume=put)
        old_routes = list(playback.router.routes)
        try:
            router = playback.install(None, manager)
            endpoint = [r.endpoint for r in router.routes if r.path == "/api/playback/volume"][-1]
            result = await endpoint({"delta": 5, "device_ids": ["a", "b", "c"]})
            assert result["ok"]
            assert values == {"a": 45, "b": 35, "c": 100}
            result = await endpoint({"volume": 20, "device_ids": ["a"]})
            assert values == {"a": 20, "b": 35, "c": 100}
        finally:
            playback.router.routes[:] = old_routes
    asyncio.run(run())
