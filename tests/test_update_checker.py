"""Update-checker cache semantics: success caches for hours, failures for minutes."""

import pytest

from micast import update_checker


@pytest.fixture(autouse=True)
def _reset_cache(monkeypatch):
    monkeypatch.setattr(update_checker, "_cache", None)
    monkeypatch.setattr(update_checker, "_cache_error", None)
    monkeypatch.setattr(update_checker, "_cache_at", 0.0)
    _FailingSession.constructions = 0


class _FailingSession:
    constructions = 0

    def __init__(self, *args, **kwargs):
        type(self).constructions += 1
        raise ConnectionError("network down")


@pytest.mark.asyncio
async def test_failed_check_is_not_refetched_within_negative_ttl(monkeypatch):
    monkeypatch.setattr(update_checker.aiohttp, "ClientSession", _FailingSession)

    with pytest.raises(ConnectionError):
        await update_checker.check_for_update()
    with pytest.raises(RuntimeError, match="network down"):
        # Served from the negative cache: no second network attempt.
        await update_checker.check_for_update()

    assert _FailingSession.constructions == 1


@pytest.mark.asyncio
async def test_force_bypasses_negative_cache(monkeypatch):
    monkeypatch.setattr(update_checker.aiohttp, "ClientSession", _FailingSession)

    with pytest.raises(ConnectionError):
        await update_checker.check_for_update()

    with pytest.raises(ConnectionError):
        await update_checker.check_for_update(force=True)

    assert _FailingSession.constructions == 2


@pytest.mark.asyncio
async def test_success_after_failure_clears_error_cache(monkeypatch):
    monkeypatch.setattr(update_checker.aiohttp, "ClientSession", _FailingSession)
    with pytest.raises(ConnectionError):
        await update_checker.check_for_update()

    class _OkSession:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        def get(self, url, headers=None):
            return _OkResponse()

    class _OkResponse:
        status = 200

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def json(self):
            return {"tag_name": "v0.0.1", "assets": []}

    monkeypatch.setattr(update_checker.aiohttp, "ClientSession", _OkSession)
    # Within the negative TTL only a forced (user-initiated) retry re-fetches.
    result = await update_checker.check_for_update(force=True)

    assert result["update_available"] is False
    # And the success replaces the failure: next call is served from cache.
    result2 = await update_checker.check_for_update()
    assert result2 == result
