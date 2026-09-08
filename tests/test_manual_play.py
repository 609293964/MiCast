"""Manual (debug) playback is observed in status surfaces but never restored."""

import asyncio

from micast.xiaomi.device_manager import MANUAL_PLAY_OWNER, DeviceManager


def _run(fn):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(fn())
    finally:
        for task in asyncio.all_tasks(loop):
            task.cancel()
        loop.run_until_complete(asyncio.gather(*asyncio.all_tasks(loop), return_exceptions=True))
        loop.close()


def test_note_manual_play_marks_speaker_playing():
    async def scenario():
        dm = DeviceManager(auth=None)  # watchdog loop is inert without a service
        dm.note_manual_play("didA", "http://local/api/debug/test-tone")
        assert dm.is_playing("didA")
        assert dm.stream_url_of("didA") == "http://local/api/debug/test-tone"
        assert dm.owner_of("didA") == MANUAL_PLAY_OWNER

    _run(scenario)


def test_note_manual_play_keeps_existing_receiver_ownership():
    async def scenario():
        dm = DeviceManager(auth=None)
        dm._owners["didA"] = "r1"
        dm._stream_urls["didA"] = "http://local/stream/r1"
        dm.note_manual_play("didA", "http://local/api/debug/test-tone")
        # Ownership and the receiver's stream URL survive a manual test play.
        assert dm.owner_of("didA") == "r1"
        assert dm.stream_url_of("didA") == "http://local/stream/r1"
        assert dm.is_playing("didA")

    _run(scenario)
