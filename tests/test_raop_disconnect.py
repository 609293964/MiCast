import asyncio
from types import SimpleNamespace

import pytest

from micast.raop.server import RaopServer


class HangingWriter:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True

    async def wait_closed(self):
        await asyncio.Future()


@pytest.mark.asyncio
async def test_disconnect_does_not_wait_forever_for_mobile_client(monkeypatch):
    monkeypatch.setattr("micast.raop.server.RAOP_CLOSE_TIMEOUT_SECONDS", 0.01)
    server = object.__new__(RaopServer)
    writer = HangingWriter()
    session = SimpleNamespace(stop_notified=False)
    server._sessions_by_writer = {writer: session}
    closed_sessions = []
    server._close_session = closed_sessions.append

    disconnected = await asyncio.wait_for(server.disconnect_clients(), timeout=0.2)

    assert disconnected == 1
    assert writer.closed is True
    assert session.stop_notified is True
    assert closed_sessions == [session]
