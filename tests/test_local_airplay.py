from types import SimpleNamespace

import pytest

from micast.local_airplay import LocalAirPlayProvider


class FakeServer:
    def __init__(self, hostname, name, zeroconf):
        self.hostname = hostname
        self.name = name
        self.zeroconf = zeroconf
        self.on_play_start = None
        self.on_play_stop = None
        self._stream_server = SimpleNamespace(stream_url=f"http://{hostname}/{name}")
        self.started = 0
        self.stopped = 0

    async def start(self):
        self.started += 1

    async def stop(self):
        self.stopped += 1


class FakeZeroconf:
    def close(self):
        pass


@pytest.mark.asyncio
async def test_reconcile_keeps_unchanged_receiver_running():
    provider = LocalAirPlayProvider(FakeServer, FakeZeroconf)
    await provider.start([("living", "客厅")], "192.168.0.13", None, None)
    first = provider.receivers["living"].server

    await provider.start([("living", "客厅"), ("kitchen", "厨房")], "192.168.0.13", None, None)

    assert provider.receivers["living"].server is first
    assert first.started == 1
    assert provider.receivers["kitchen"].status == "running"
    await provider.stop()
