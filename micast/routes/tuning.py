"""Curve-EQ tuning API: drawn curves, measurement calibration, level matching.

The legacy 10-band slider endpoints live in routes/devices.py; this router
serves the curve editor and the calibration wizard (tuning view).
"""

import asyncio
import re
import secrets
import time
from urllib.parse import quote

from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel

from micast.audio_bridge import AudioBridge
from micast.config import CONTENT_PROFILES, settings
from micast.curve_fit import (
    CURVE_FREQ_RANGE,
    CURVE_GAIN_RANGE,
    format_graphic_eq,
    gain_table,
    normalize_points,
    parse_graphic_eq,
)
from micast.room_measure import (
    SWEEP_PAD_SECONDS,
    SWEEP_SECONDS,
    compensation_points,
    measure_response,
    recording_level_dbfs,
    sweep_pcm,
)
from micast.xiaomi.device_manager import DeviceManager

router = APIRouter(prefix="/api/tuning", tags=["tuning"])

# Active sweep-playback sessions by token.
_calibrations: dict[str, dict] = {}


class LevelMatchPayload(BaseModel):
    group_id: str
    levels: dict[str, float]  # did → recorded sweep level (dBFS)


def install(bridge: AudioBridge | None, device_manager: DeviceManager | None = None) -> APIRouter:
    def _speaker_state(did: str) -> dict:
        speaker = settings.get_speaker(did)
        return {
            "did": did,
            "enabled": speaker.eq_enabled if speaker else False,
            "points": (
                [[p.freq, p.gain_db] for p in speaker.eq_points] if speaker else []
            ),
            "preset": speaker.eq_preset if speaker else "",
            "target": speaker.eq_target if speaker else "",
            "night_mode": speaker.night_mode if speaker else False,
            "loudness_comp_enabled": speaker.loudness_comp_enabled if speaker else False,
            "content_profile": speaker.content_profile if speaker else "",
            "profiles": (
                {k: [[p.freq, p.gain_db] for p in v] for k, v in speaker.eq_profiles.items()}
                if speaker
                else {}
            ),
            "freq_range": list(CURVE_FREQ_RANGE),
            "gain_range": list(CURVE_GAIN_RANGE),
        }

    @router.get("/{did}")
    async def get_curve(did: str):
        return _speaker_state(did)

    @router.post("/eq")
    async def set_curve(payload: dict):
        did = payload.get("did")
        enabled = payload.get("enabled")
        points = payload.get("points")
        if not did or not isinstance(enabled, bool) or not isinstance(points, list):
            raise HTTPException(status_code=400, detail="did, enabled and points required")
        try:
            clean = normalize_points(points)
        except (TypeError, ValueError) as e:
            raise HTTPException(status_code=400, detail=f"invalid points: {e}") from e
        try:
            settings.set_speaker_eq_curve(
                did,
                enabled=enabled,
                points=clean,
                preset=str(payload.get("preset", "")),
                target=payload.get("target") if "target" in payload else None,
            )
        except (TypeError, ValueError) as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        # Curve edits re-split streams by EQ signature; pipelines rebuild and
        # playing speakers re-point. The editor debounces drag commits.
        if bridge:
            await bridge.apply_config_change()
        return _speaker_state(did)

    # ------------------------------------------------------------------
    # Night mode + per-speaker content scenes + AutoEq interchange.
    # ------------------------------------------------------------------

    @router.post("/night-mode")
    async def set_night_mode(payload: dict):
        did = payload.get("did")
        enabled = payload.get("enabled")
        if not did or not isinstance(enabled, bool):
            raise HTTPException(status_code=400, detail="did and enabled required")
        settings.set_speaker_night_mode(did, enabled)
        if bridge:
            await bridge.apply_config_change()
        return _speaker_state(did)

    @router.post("/loudness")
    async def set_loudness(payload: dict):
        did = payload.get("did")
        enabled = payload.get("enabled")
        if not did or not isinstance(enabled, bool):
            raise HTTPException(status_code=400, detail="did and enabled required")
        settings.set_speaker_loudness(did, enabled)
        if bridge:
            await bridge.apply_config_change()
        return _speaker_state(did)

    # ---- Global curve library ----
    # Library mutations never touch the audio path; they only persist named
    # curves the frontend can then assign to any speaker.

    @router.post("/curves/save")
    async def save_named_curve(payload: dict):
        name = payload.get("name")
        points = payload.get("points")
        if not isinstance(name, str) or not isinstance(points, list):
            raise HTTPException(status_code=400, detail="name and points required")
        try:
            settings.save_curve(name, normalize_points(points))
        except (TypeError, ValueError) as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        return {"curves": settings.list_saved_curves()}

    @router.post("/curves/rename")
    async def rename_named_curve(payload: dict):
        old, new = payload.get("old"), payload.get("new")
        if not isinstance(old, str) or not isinstance(new, str):
            raise HTTPException(status_code=400, detail="old and new required")
        try:
            settings.rename_saved_curve(old, new)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        return {"curves": settings.list_saved_curves()}

    @router.post("/curves/delete")
    async def delete_named_curve(payload: dict):
        name = payload.get("name")
        if not isinstance(name, str):
            raise HTTPException(status_code=400, detail="name required")
        settings.delete_saved_curve(name)
        return {"curves": settings.list_saved_curves()}

    @router.post("/profile/save")
    async def save_profile(payload: dict):
        did = payload.get("did")
        profile = str(payload.get("profile", ""))
        if not did or profile not in CONTENT_PROFILES:
            raise HTTPException(status_code=400, detail="did and valid profile required")
        settings.save_speaker_profile(did, profile)
        return _speaker_state(did)

    @router.post("/profile/switch")
    async def switch_profile(payload: dict):
        did = payload.get("did")
        profile = str(payload.get("profile", ""))
        if not did or profile not in CONTENT_PROFILES:
            raise HTTPException(status_code=400, detail="did and valid profile required")
        try:
            settings.set_speaker_content_profile(did, profile)
        except ValueError as e:
            raise HTTPException(status_code=404, detail=str(e)) from e
        if bridge:
            await bridge.apply_config_change()
        return _speaker_state(did)

    @router.post("/profile/delete")
    async def delete_profile(payload: dict):
        did = payload.get("did")
        profile = str(payload.get("profile", ""))
        if not did or profile not in CONTENT_PROFILES:
            raise HTTPException(status_code=400, detail="did and valid profile required")
        settings.delete_speaker_profile(did, profile)
        return _speaker_state(did)

    @router.get("/{did}/export")
    async def export_curve(did: str):
        """Export the active curve as AutoEq GraphicEQ text."""
        speaker = settings.get_speaker(did)
        if speaker is None:
            raise HTTPException(status_code=404, detail="未找到音箱")
        points = [(p.freq, p.gain_db) for p in speaker.eq_points]
        return {
            "did": did,
            "graphic_eq": format_graphic_eq(gain_table(points)),
            "points": [[f, g] for f, g in points],
        }

    @router.get("/{did}/export.txt")
    async def export_curve_download(did: str):
        """Same export served as an attachment — pywebview's WebView2 ignores
        in-page blob downloads, so the UI navigates here to trigger the
        native download."""
        speaker = settings.get_speaker(did)
        if speaker is None:
            raise HTTPException(status_code=404, detail="未找到音箱")
        text = format_graphic_eq(gain_table([(p.freq, p.gain_db) for p in speaker.eq_points]))
        name = re.sub(r"[^\w.-]+", "_", speaker.alias or did) or did
        ascii_name = re.sub(r"[^A-Za-z0-9._-]+", "_", name) or did
        disposition = (
            f"attachment; filename=\"micast-{ascii_name}-eq.txt\"; "
            f"filename*=UTF-8''{quote(f'micast-{name}-eq.txt')}"
        )
        return PlainTextResponse(
            text,
            media_type="text/plain; charset=utf-8",
            headers={"Content-Disposition": disposition},
        )

    @router.post("/{did}/import")
    async def import_curve(did: str, payload: dict):
        """Replace the active curve from AutoEq GraphicEQ text."""
        points = parse_graphic_eq(str(payload.get("text", "")))
        if not points:
            raise HTTPException(status_code=400, detail="无法解析 GraphicEQ 曲线")
        settings.set_speaker_eq_curve(did, enabled=True, points=points, preset="", target=None)
        if bridge:
            await bridge.apply_config_change()
        return _speaker_state(did)

    # ------------------------------------------------------------------
    # Measurement calibration: play a sweep on one speaker, the browser
    # records it through its microphone, the server derives a compensation
    # curve.
    # ------------------------------------------------------------------

    async def _stop_calibration(token: str) -> None:
        session = _calibrations.pop(token, None)
        if session is None or bridge is None or device_manager is None:
            return
        bridge._stream_server.end_delay_calibration(token)
        did = session["did"]

        async def bounded(operation):
            try:
                return await asyncio.wait_for(operation, timeout=8)
            except TimeoutError:
                return False

        await bounded(device_manager.stop_playback(did, owner=session["owner"]))
        url, owner = session["previous"]
        if url:
            await bounded(device_manager.play_stream(did, url, owner=owner, force=True))

    @router.post("/calibration/start")
    async def start_calibration(payload: dict):
        """Point one speaker at a finite sweep; returns how long it plays."""
        if bridge is None or device_manager is None:
            raise HTTPException(status_code=503, detail="服务未就绪")
        did = payload.get("did")
        if not did:
            raise HTTPException(status_code=400, detail="did required")
        token = secrets.token_urlsafe(12)
        bridge._stream_server.begin_delay_calibration(token, [did], None, sweep_pcm())
        previous = (device_manager.stream_url_of(did), device_manager.owner_of(did))
        owner = f"tuning-calibration:{token}"
        _calibrations[token] = {"did": did, "owner": owner, "previous": previous}
        base = f"http://{settings.effective_stream_host}:{settings.stream_port}"
        try:
            started = await asyncio.wait_for(
                device_manager.play_stream(
                    did,
                    f"{base}/calibration/{token}/{did}.wav",
                    owner=owner,
                    force=True,
                    audio_id=str(time.time_ns()),
                ),
                timeout=8,
            )
        except TimeoutError as exc:
            await _stop_calibration(token)
            raise HTTPException(status_code=502, detail="音箱未能开始播放扫频信号") from exc
        if started is False:
            await _stop_calibration(token)
            raise HTTPException(status_code=502, detail="音箱未能开始播放扫频信号")
        duration = SWEEP_PAD_SECONDS + SWEEP_SECONDS + SWEEP_PAD_SECONDS
        return {"token": token, "duration_seconds": duration}

    @router.post("/calibration/stop")
    async def stop_calibration(payload: dict):
        token = str(payload.get("token", ""))
        await _stop_calibration(token)
        return {"ok": True}

    @router.post("/calibration/analyze")
    async def analyze_calibration(
        file: UploadFile = File(...),
        did: str = "",
        target: str = "",
    ):
        """Uploaded microphone recording → measured response + compensation."""
        data = await file.read()
        if len(data) < 4096:
            raise HTTPException(status_code=400, detail="录音太短或为空")
        try:
            freqs, gains = await asyncio.to_thread(measure_response, data)
            level = await asyncio.to_thread(recording_level_dbfs, data)
            points = compensation_points(freqs, gains, target=target)
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e)) from e
        return {
            "did": did,
            "measured": {"freqs": freqs, "gains": gains},
            "points": [[f, g] for f, g in points],
            "level_dbfs": level,
        }

    @router.post("/calibration/apply")
    async def apply_calibration(payload: dict):
        """Adopt the compensation curve as the speaker's EQ."""
        did = payload.get("did")
        points = payload.get("points")
        if not did or not isinstance(points, list) or not points:
            raise HTTPException(status_code=400, detail="did and points required")
        settings.set_speaker_eq_curve(
            did,
            enabled=True,
            points=normalize_points(points),
            preset="",
            target=str(payload.get("target", "")),
        )
        if bridge:
            await bridge.apply_config_change()
        return _speaker_state(did)

    @router.post("/level-match")
    async def level_match(payload: LevelMatchPayload):
        """Level-match group members from per-speaker sweep recordings.

        The quietest member becomes the reference (gains only ever attenuate),
        written into the group's existing loudness trims.
        """
        group = next((g for g in settings.groups if g.id == payload.group_id), None)
        if group is None:
            raise HTTPException(status_code=404, detail="未找到音箱组合")
        known = {did: v for did, v in payload.levels.items() if did in group.speaker_ids}
        if len(known) < 2:
            raise HTTPException(status_code=400, detail="至少需要两台音箱的测量电平")
        reference = min(known.values())
        gains = dict(group.gains_db)
        for did, level in known.items():
            gains[did] = round(max(-12.0, reference - level), 1)
        settings.update_group(group.id, gains_db=gains)
        if bridge:
            await bridge.apply_config_change()
        return {"group_id": group.id, "gains_db": gains}

    return router
