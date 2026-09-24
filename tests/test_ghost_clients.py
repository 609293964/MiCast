"""Ghost-client reaping: a half-open HTTP pull must not eat chunks forever.

Field report (fnOS 0.3.0): while casting via AirPlay 2, dropped_chunks on
/stream/airplay2 grew continuously (~12/s) with repeated "Client queue ...
overflowed" logs. The Xiaomi player had replaced its pull socket during
reconnect churn and the dead queue was never drained — every broadcast to it
dropped one chunk, and neither the stale sweep (active sessions are skipped)
nor TCP noticed the dead peer.
"""

import time

import pytest
from fastapi import Request

from micast.audio_encoder import StreamFormat
from micast.stream_server import CLIENT_UNDRAINED_SECONDS, StreamServer


def _request() -> Request:
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/stream/airplay2",
            "query_string": b"",
            "headers": [],
            "client": ("192.168.0.128", 51000),
        }
    )


def _flac_server() -> StreamServer:
    server = StreamServer()
    server.register_stream("airplay2", StreamFormat("audio/flac", "flac", None))
    return server


async def _connect(server: StreamServer):
    response = await server._serve_stream(_request(), "airplay2")
    queue = next(iter(server._clients["airplay2"]))
    return response, queue


@pytest.mark.asyncio
async def test_ghost_client_is_reaped_and_drops_stop_growing():
    server = _flac_server()
    response, queue = await _connect(server)
    state = server._client_delay[queue]

    # The reader never takes anything; age it past the ghost threshold.
    state["last_get_at"] = time.monotonic() - CLIENT_UNDRAINED_SECONDS - 1
    for _ in range(queue.maxsize):
        queue.put_nowait(b"\xff" * 1024)

    server._broadcast_to("airplay2", b"\xee" * 1024)

    assert queue not in server._clients["airplay2"]
    assert server.client_count("airplay2") == 0
    drops_before = server.dropped_chunks["airplay2"]

    # Further broadcasts no longer drop anything for the ghost.
    for _ in range(10):
        server._broadcast_to("airplay2", b"\xee" * 1024)
    assert server.dropped_chunks["airplay2"] == drops_before

    await response.body_iterator.aclose()


@pytest.mark.asyncio
async def test_live_slow_client_is_not_reaped():
    """A full queue with a recent read stays on the drop-oldest path."""
    server = _flac_server()
    response, queue = await _connect(server)

    for _ in range(queue.maxsize):
        queue.put_nowait(b"\xff" * 1024)
    server._broadcast_to("airplay2", b"\xee" * 1024)

    assert queue in server._client_delay  # still attached
    assert queue in server._clients["airplay2"]
    assert server.dropped_chunks["airplay2"] == 1  # one drop, then reaped later

    await response.body_iterator.aclose()


@pytest.mark.asyncio
async def test_group_recovery_waiter_is_not_reaped():
    """Clients held at the group-recovery barrier intentionally read nothing."""
    server = _flac_server()
    response, queue = await _connect(server)
    state = server._client_delay[queue]
    state["receiver"] = "airplay2"
    state["sink"] = "speaker-a"
    server.begin_group_recovery("airplay2", ["speaker-a", "speaker-b"])
    state["last_get_at"] = time.monotonic() - CLIENT_UNDRAINED_SECONDS - 1

    server._broadcast_to("airplay2", b"\xee" * 1024)

    assert queue in server._clients["airplay2"]

    server.abort_group_recovery("airplay2")
    await response.body_iterator.aclose()
