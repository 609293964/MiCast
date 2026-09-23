from collections import defaultdict

import pytest

from micast.routes.debug import _restore_streams


class RestoreManager:
    def __init__(self, failures: dict[str, int]):
        self.failures = failures
        self.calls = defaultdict(int)
        self.recovery = []

    async def play_stream(self, did, url, owner=None, force=False):
        self.calls[did] += 1
        return self.calls[did] > self.failures.get(did, 0)

    def note_play_error(self, did, owner, error, url=None):
        self.recovery.append((did, owner, error, url))


@pytest.mark.asyncio
async def test_calibration_restore_retries_only_failed_speakers():
    manager = RestoreManager({"b": 1})
    failed = await _restore_streams(
        manager,
        {"a": ("http://stream/a", "group"), "b": ("http://stream/b", "group")},
        retry_delay=0,
    )

    assert failed == []
    assert manager.calls == {"a": 1, "b": 2}
    assert manager.recovery == []


@pytest.mark.asyncio
async def test_calibration_restore_reports_and_schedules_persistent_failure():
    manager = RestoreManager({"b": 99})
    failed = await _restore_streams(
        manager,
        {"a": ("http://stream/a", "group"), "b": ("http://stream/b", "group")},
        attempts=2,
        retry_delay=0,
    )

    assert failed == ["b"]
    assert manager.calls == {"a": 1, "b": 2}
    assert manager.recovery == [("b", "group", "恢复播放失败", "http://stream/b")]


@pytest.mark.asyncio
async def test_calibration_restore_skips_speakers_that_were_not_playing():
    manager = RestoreManager({})
    failed = await _restore_streams(
        manager,
        {"a": (None, None), "b": ("http://stream/b", "group")},
        retry_delay=0,
    )

    assert failed == []
    assert manager.calls == {"b": 1}
