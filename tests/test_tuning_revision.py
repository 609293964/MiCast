import asyncio
import contextlib
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from micast.config import Settings
from micast.routes import tuning


def _endpoint(router, path: str):
    return [route.endpoint for route in router.routes if route.path == path][-1]


@pytest.fixture()
def setup(monkeypatch):
    monkeypatch.setattr(Settings, "save_to_file", lambda self: None)
    settings = Settings()
    settings.set_speaker_eq_curve("speaker", enabled=True, points=[(100, 2)], preset="bass")
    bridge = SimpleNamespace(apply_config_change=AsyncMock())
    monkeypatch.setattr(tuning, "settings", settings)
    old_routes = list(tuning.router.routes)
    router = tuning.install(bridge)
    yield settings, bridge, router
    tuning.router.routes[:] = old_routes


@pytest.mark.asyncio
async def test_stale_tuning_revision_cannot_overwrite_newer_curve(setup):
    settings, bridge, router = setup
    endpoint = _endpoint(router, "/api/tuning/eq")
    before = settings.get_speaker("speaker").model_copy(deep=True)

    with pytest.raises(HTTPException) as caught:
        await endpoint(
            {
                "did": "speaker",
                "enabled": True,
                "points": [[100, -5]],
                "preset": "",
                "expected_revision": before.eq_revision - 1,
            }
        )

    assert caught.value.status_code == 409
    assert settings.get_speaker("speaker").eq_points == before.eq_points
    bridge.apply_config_change.assert_not_awaited()


@pytest.mark.asyncio
async def test_tuning_response_exposes_revision_and_durable_undo(setup):
    settings, bridge, router = setup
    set_curve = _endpoint(router, "/api/tuning/eq")
    undo = _endpoint(router, "/api/tuning/undo")
    revision = settings.get_speaker("speaker").eq_revision

    changed = await set_curve(
        {
            "did": "speaker",
            "enabled": True,
            "points": [[100, -5]],
            "preset": "",
            "expected_revision": revision,
        }
    )
    assert changed["revision"] == revision + 1
    assert changed["undo_available"] is True

    restored = await undo({"did": "speaker", "expected_revision": changed["revision"]})
    assert restored["points"] == [[100.0, 2.0]]
    assert restored["preset"] == "bass"
    assert restored["undo_available"] is False
    assert bridge.apply_config_change.await_count == 2


@pytest.mark.asyncio
async def test_reference_target_persists_without_rebuilding_audio_or_replacing_undo(setup):
    settings, bridge, router = setup
    set_curve = _endpoint(router, "/api/tuning/eq")
    set_target = _endpoint(router, "/api/tuning/target")
    revision = settings.get_speaker("speaker").eq_revision

    changed = await set_curve(
        {
            "did": "speaker",
            "enabled": True,
            "points": [[100, -5]],
            "preset": "",
            "expected_revision": revision,
        }
    )
    undo_before = settings.get_speaker("speaker").eq_undo.model_copy(deep=True)
    bridge.apply_config_change.reset_mock()

    referenced = await set_target(
        {
            "did": "speaker",
            "target": "harman",
            "expected_revision": changed["revision"],
        }
    )

    assert referenced["target"] == "harman"
    assert referenced["revision"] == changed["revision"] + 1
    assert settings.get_speaker("speaker").eq_undo == undo_before
    bridge.apply_config_change.assert_not_awaited()


@pytest.mark.asyncio
async def test_concurrent_stale_revision_loses_to_in_flight_commit(setup):
    """The revision guard runs INSIDE the config transaction lock. Two /eq
    commits racing with different expected_revisions must serialize: the stale
    one gets a 409 and must not overwrite the winner's curve (TOCTOU pin)."""
    settings, bridge, router = setup
    endpoint = _endpoint(router, "/api/tuning/eq")
    revision = settings.get_speaker("speaker").eq_revision

    async def commit(points: list, expected_revision: int) -> tuple[str, object]:
        try:
            result = await endpoint(
                {
                    "did": "speaker",
                    "enabled": True,
                    "points": points,
                    "preset": "",
                    "expected_revision": expected_revision,
                }
            )
            return "ok", result
        except HTTPException as exc:
            return "http", exc

    fresh = asyncio.create_task(commit([[100, -5]], revision))
    stale = asyncio.create_task(commit([[500, 8]], revision - 1))
    outcomes = dict(await asyncio.gather(fresh, stale))

    winner = outcomes["ok"]
    conflict = outcomes["http"]
    assert winner["points"] == [[100.0, -5.0]]
    assert conflict.status_code == 409
    # The stale commit was rejected before mutating anything.
    assert settings.get_speaker("speaker").eq_points[0].gain_db == -5.0
    assert all(point.freq != 500.0 for point in settings.get_speaker("speaker").eq_points)


@pytest.mark.asyncio
async def test_revision_conflict_has_no_runtime_side_effects(setup):
    """A rejected (409) commit never reaches the runtime apply step: the
    bridge must not restart encoders for a curve it never persisted."""
    settings, bridge, router = setup
    endpoint = _endpoint(router, "/api/tuning/eq")
    revision = settings.get_speaker("speaker").eq_revision

    async def commit(expected_revision: int) -> None:
        with contextlib.suppress(HTTPException):
            await endpoint(
                {
                    "did": "speaker",
                    "enabled": True,
                    "points": [[100, -5]],
                    "preset": "",
                    "expected_revision": expected_revision,
                }
            )

    await asyncio.gather(commit(revision), commit(revision - 1))

    # Exactly one runtime application: the winning commit only. The conflicted
    # one was rejected inside the lock before apply_runtime could run.
    bridge.apply_config_change.assert_awaited_once()
    assert settings.get_speaker("speaker").eq_revision == revision + 1


@pytest.mark.asyncio
async def test_revision_conflict_leaves_undo_checkpoint_untouched(setup):
    """A 409 conflict must not consume or replace the durable undo slot — the
    rejected commit never became audible, so nothing about it is undoable."""
    settings, bridge, router = setup
    endpoint = _endpoint(router, "/api/tuning/eq")
    undo_before = settings.get_speaker("speaker").eq_undo
    revision = settings.get_speaker("speaker").eq_revision

    with pytest.raises(HTTPException) as caught:
        await endpoint(
            {
                "did": "speaker",
                "enabled": True,
                "points": [[100, -5]],
                "preset": "",
                "expected_revision": revision + 5,
            }
        )

    assert caught.value.status_code == 409
    assert settings.get_speaker("speaker").eq_undo == undo_before
    assert settings.get_speaker("speaker").eq_revision == revision
