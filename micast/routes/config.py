"""Configuration routes."""

from fastapi import APIRouter, HTTPException

from micast.audio_bridge import AudioBridge
from micast.config import default_data_dir, default_log_dir, settings, storage_mode
from micast.deployment import airplay2_available, airplay2_mode
from micast.dlna import DlnaService

router = APIRouter(prefix="/api/config", tags=["config"])


def install(bridge: AudioBridge, dlna: DlnaService | None = None) -> APIRouter:
    @router.get("/audio")
    async def get_audio_config():
        return settings.audio.model_dump()

    @router.post("/audio")
    async def set_audio_config(payload: dict):
        allowed = {"format", "bitrate", "sample_rate", "auto_transcode"}
        updates = {k: v for k, v in payload.items() if k in allowed}
        settings.update_audio(**updates)
        # The plan diff routes this to per-pipeline encoder restarts for
        # classic entries AND rebuilds of AirPlay 2 pipelines (which used to
        # keep the old format until a full restart).
        await bridge.apply_config_change()
        return settings.audio.model_dump()

    @router.get("")
    async def get_config():
        return {
            "audio": settings.audio.model_dump(),
            "app": settings.app.model_dump(),
            "receiver_mode": settings.receiver_mode,
            "airplay_protocol": settings.airplay_protocol,
            "airplay_engine": settings.airplay_engine,
            "dlna_enabled": settings.dlna_enabled,
            "sync_groups_enabled": settings.sync_groups_enabled,
            "large_delay_enabled": settings.large_delay_enabled,
            "touchscreen_lyrics": settings.touchscreen_lyrics,
            "default_volume": settings.default_volume,
            "default_volume_enabled": settings.default_volume_enabled,
            "sender_volume_mode": settings.sender_volume_mode,
            "notify_webhook_url": settings.notify_webhook_url,
            "airplay2_enabled": settings.airplay2_enabled,
            "airplay2_available": airplay2_available(),
            "airplay2_mode": airplay2_mode(),
            "airplay2_can_add_instances": airplay2_mode() == "multi",
            "storage": {
                "mode": storage_mode(),
                "data_dir": str(default_data_dir()),
                "log_dir": str(default_log_dir()),
            },
            "dlna_status": {
                "status": dlna.status if dlna else "unavailable",
                "detail": dlna.detail if dlna else "DLNA 服务不可用",
            },
            "selected_device_id": settings.selected_device_id,
            "receivers": [item.model_dump() for item in settings.receivers],
            "groups": [item.model_dump() for item in settings.groups],
            # Persisted aliases let the UI name speakers before the live
            # device list finishes loading.
            "speaker_names": {
                speaker.did: speaker.alias
                for speaker in settings.speakers
                if speaker.alias
            },
        }

    @router.post("/app-name")
    async def set_app_name(payload: dict):
        name = payload.get("name")
        if not name or not isinstance(name, str):
            raise HTTPException(status_code=400, detail="name required")
        settings.update_app_name(name)
        return settings.app.model_dump()

    @router.post("/receiver-mode")
    async def set_receiver_mode(payload: dict):
        mode = payload.get("mode")
        if mode not in ("single", "multi"):
            raise HTTPException(status_code=400, detail="mode must be 'single' or 'multi'")
        settings.set_receiver_mode(mode)
        await bridge.restart()
        return {"receiver_mode": mode}

    @router.post("/airplay-protocol")
    async def set_airplay_protocol(payload: dict):
        protocol = payload.get("protocol")
        if protocol not in ("auto", "classic", "airplay2"):
            raise HTTPException(
                status_code=400, detail="protocol must be auto, classic, or airplay2"
            )
        settings.set_airplay_protocol(protocol)
        await bridge.restart()
        return {"airplay_protocol": protocol}

    @router.post("/dlna")
    async def set_dlna(payload: dict):
        enabled = payload.get("enabled")
        if not isinstance(enabled, bool):
            raise HTTPException(status_code=400, detail="enabled boolean required")
        settings.set_dlna_enabled(enabled)
        if dlna:
            await dlna.reconcile()
        return {"dlna_enabled": enabled}

    @router.post("/sync-groups")
    async def set_sync_groups(payload: dict):
        enabled = payload.get("enabled")
        if not isinstance(enabled, bool):
            raise HTTPException(status_code=400, detail="enabled boolean required")
        settings.set_sync_groups_enabled(enabled)
        await bridge.apply_config_change()
        if dlna:
            await dlna.reconcile()
        return {"sync_groups_enabled": enabled}

    @router.post("/large-delay")
    async def set_large_delay(payload: dict):
        enabled = payload.get("enabled")
        if not isinstance(enabled, bool):
            raise HTTPException(status_code=400, detail="enabled boolean required")
        settings.set_large_delay_enabled(enabled)
        return {"large_delay_enabled": enabled}

    @router.post("/touchscreen-lyrics")
    async def set_touchscreen_lyrics(payload: dict):
        enabled = payload.get("enabled")
        if not isinstance(enabled, bool):
            raise HTTPException(status_code=400, detail="enabled boolean required")
        settings.set_touchscreen_lyrics(enabled)
        return {"touchscreen_lyrics": enabled}

    @router.post("/default-volume")
    async def set_default_volume(payload: dict):
        try:
            volume = max(0, min(100, int(payload.get("volume", 0))))
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="volume must be 0-100") from None
        enabled = payload.get("enabled", settings.default_volume_enabled)
        if not isinstance(enabled, bool):
            raise HTTPException(status_code=400, detail="enabled boolean required")
        settings.default_volume_enabled = enabled
        settings.set_default_volume(volume)
        return {"default_volume": volume, "default_volume_enabled": enabled}

    @router.post("/sender-volume")
    async def set_sender_volume(payload: dict):
        mode = payload.get("mode")
        if mode not in ("independent", "linked"):
            raise HTTPException(status_code=400, detail="无效的音量控制方式")
        settings.sender_volume_mode = mode
        settings.save_to_file()
        return {"sender_volume_mode": mode}

    @router.post("/notify-webhook")
    async def set_notify_webhook(payload: dict):
        url = str(payload.get("url", "")).strip()
        if url and not url.startswith(("http://", "https://")):
            raise HTTPException(status_code=400, detail="url must be http(s)")
        settings.set_notify_webhook(url)
        return {"notify_webhook_url": url}

    @router.post("/airplay2")
    async def set_airplay2_enabled(payload: dict):
        enabled = payload.get("enabled")
        if not isinstance(enabled, bool):
            raise HTTPException(status_code=400, detail="enabled boolean required")
        if enabled and not airplay2_available():
            raise HTTPException(status_code=409, detail="当前安装方式不支持 AirPlay 2")
        if not enabled:
            try:
                await bridge.shutdown_airplay2()
            except Exception as exc:
                raise HTTPException(
                    status_code=502, detail=f"AirPlay 2 实例尚未全部停止：{exc}"
                ) from exc
        settings.set_airplay2_enabled(enabled)
        if enabled:
            await bridge.reconcile_airplay2()
            if airplay2_mode() == "single":
                runtime = bridge.status.get("airplay2_instances", [])
                live = next(iter(runtime), {})
                if live.get("status") != "running":
                    detail = str(live.get("detail") or "原生 AirPlay 2 接收器启动失败")
                    await bridge.shutdown_airplay2()
                    settings.set_airplay2_enabled(False)
                    await bridge.apply_config_change()
                    raise HTTPException(status_code=502, detail=detail)
        # Sync the plan snapshot with what the explicit start/stop above did.
        await bridge.apply_config_change()
        return {"airplay2_enabled": enabled}

    return router
