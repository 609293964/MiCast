"""Configuration routes."""

import logging
import os
import re

from fastapi import APIRouter, HTTPException

from micast.access import AccessManager
from micast.audio_bridge import AudioBridge
from micast.config import (
    EDITABLE_PORTS,
    default_data_dir,
    default_log_dir,
    env_pinned,
    settings,
    storage_mode,
)
from micast.deployment import airplay2_available, airplay2_mode, deployment_mode
from micast.dlna import DlnaService
from micast.raop import server as raop_server
from micast.xiaomi.auth import XiaomiAuth
from micast.xiaomi.device_manager import DeviceManager

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/config", tags=["config"])

# Local state wiped by /reset (清空数据). Matches the migration file list in
# config.py — keep both in sync when adding new persisted files.
RESET_FILES = ("micast.json", "access.json", "xiaomi-account.json", "xiaomi-tokens.enc")


def _port_mode(field: str) -> str:
    """auto (default) / custom (user-set) / env (pinned by a real env var)."""
    env_var, default = EDITABLE_PORTS[field]
    if env_pinned(env_var):
        return "env"
    if field in ("port", "stream_port"):
        return "custom" if settings.preferred_port(field) != default else "auto"
    return "custom" if getattr(settings, field) is not None else "auto"


def _preferred(field: str, fallback: int) -> int:
    if field in ("port", "stream_port"):
        return settings.preferred_port(field)
    return getattr(settings, field) or fallback


def _shairport_actual_port() -> int | None:
    """The port the bundled shairport-sync actually bound (fnOS single mode)."""
    conf = default_data_dir() / "shairport-sync.conf"
    try:
        match = re.search(r"^\s*port\s*=\s*(\d+)", conf.read_text(encoding="utf-8"), re.M)
    except OSError:
        return None
    return int(match.group(1)) if match else None


def _ports_report(bridge: AudioBridge, dlna: DlnaService | None) -> list[dict]:
    """Per-service port status for the settings page, pruned by deployment."""
    unix_socket = os.environ.get("MICAST_UNIX_SOCKET", "").strip()
    ap2_mode = airplay2_mode()
    entries: list[dict] = []

    if unix_socket:
        entries.append({
            "id": "port",
            "name": "管理界面",
            "protocol": "tcp",
            "mode": "fixed",
            "preferred": None,
            "actual": None,
            "status": "hosted",
            "detail": "由 fnOS 统一网关托管（Unix Socket），无需配置",
            "editable": False,
        })
    else:
        entries.append({
            "id": "port",
            "name": "管理界面",
            "protocol": "tcp",
            "mode": _port_mode("port"),
            "preferred": _preferred("port", 3000),
            "actual": settings.port,
            "status": "listening",
            "detail": "浏览器访问的管理界面端口；修改后需重启应用生效",
            "editable": not env_pinned("MICAST_PORT"),
        })

    entries.append({
        "id": "stream_port",
        "name": "音频流服务",
        "protocol": "tcp",
        "mode": _port_mode("stream_port"),
        "preferred": _preferred("stream_port", 8080),
        "actual": settings.stream_port,
        "status": "listening",
        "detail": "音箱从该端口拉取音频流；被占用时自动顺延",
        "editable": not env_pinned("MICAST_STREAM_PORT"),
    })

    rtsp_ports = sorted(raop_server._reserved_rtsp_ports)
    entries.append({
        "id": "airplay_rtsp_port",
        "name": "AirPlay RTSP",
        "protocol": "tcp",
        "mode": _port_mode("airplay_rtsp_port"),
        "preferred": _preferred("airplay_rtsp_port", 5000),
        "actual": rtsp_ports[0] if rtsp_ports else None,
        "status": "listening" if rtsp_ports else "off",
        "detail": (
            f"经典 AirPlay 会话端口，从首选端口起扫描 32 个"
            + (f"；当前绑定 {', '.join(map(str, rtsp_ports))}" if len(rtsp_ports) > 1 else "")
        ),
        "editable": not env_pinned("MICAST_AIRPLAY_RTSP_PORT"),
    })

    udp_base, udp_top = raop_server.udp_pool()
    udp_in_use = sorted(raop_server._reserved_udp_bases)
    entries.append({
        "id": "airplay_udp_base",
        "name": "AirPlay 音频通道",
        "protocol": "udp",
        "mode": _port_mode("airplay_udp_base"),
        "preferred": _preferred("airplay_udp_base", 6000),
        "actual": udp_in_use or None,
        "status": "listening" if udp_in_use else "off",
        "detail": f"每个 AirPlay 会话占 3 个 UDP 端口，范围 {udp_base}-{udp_top}",
        "editable": not env_pinned("MICAST_AIRPLAY_UDP_BASE"),
    })

    if airplay2_available():
        if ap2_mode == "single":
            actual = _shairport_actual_port()
            enabled = settings.airplay2_enabled
            entries.append({
                "id": "airplay2_port",
                "name": "AirPlay 2",
                "protocol": "tcp",
                "mode": _port_mode("airplay2_port"),
                "preferred": _preferred("airplay2_port", 7000),
                "actual": actual,
                "status": "listening" if (enabled and actual) else "off",
                "detail": "shairport-sync 接收端口，从首选端口起扫描 32 个",
                "editable": not env_pinned("MICAST_AIRPLAY2_PORT"),
            })
            entries.append({
                "id": "nqptp",
                "name": "AirPlay 2 时钟同步",
                "protocol": "udp",
                "mode": "fixed",
                "preferred": None,
                "actual": [319, 320],
                "status": "listening" if enabled else "off",
                "detail": "NQPTP 的 PTP 时钟同步，协议固定端口，不可修改",
                "editable": False,
            })
        else:
            entries.append({
                "id": "airplay2_port",
                "name": "AirPlay 2",
                "protocol": "tcp",
                "mode": "fixed",
                "preferred": 7000,
                "actual": 7000,
                "status": "hosted",
                "detail": "接收容器独立 IP 内部端口，不存在冲突，无需配置",
                "editable": False,
            })

    entries.append({
        "id": "mdns",
        "name": "mDNS 服务发现",
        "protocol": "udp",
        "mode": "fixed",
        "preferred": None,
        "actual": 5353,
        "status": "listening",
        "detail": "AirPlay/DLNA 发现广播，协议固定端口，可与其他 mDNS 服务共存",
        "editable": False,
    })

    dlna_status = dlna.status if dlna else "unavailable"
    entries.append({
        "id": "ssdp",
        "name": "DLNA/SSDP 发现",
        "protocol": "udp",
        "mode": "fixed",
        "preferred": None,
        "actual": 1900,
        "status": {
            "running": "listening",
            "stopped": "off",
            "error": "error",
        }.get(dlna_status, "off"),
        "detail": (dlna.detail if dlna else "DLNA 服务不可用") + "；协议固定端口",
        "editable": False,
    })
    return entries


def install(
    bridge: AudioBridge,
    dlna: DlnaService | None = None,
    auth: XiaomiAuth | None = None,
    access: AccessManager | None = None,
    device_manager: DeviceManager | None = None,
) -> APIRouter:
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
            "deployment": deployment_mode(),
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
            "network_discovery_enabled": settings.network_discovery_enabled,
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
            "ports": _ports_report(bridge, dlna),
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

    @router.post("/network-discovery")
    async def set_network_discovery(payload: dict):
        enabled = payload.get("enabled")
        if not isinstance(enabled, bool):
            raise HTTPException(status_code=400, detail="enabled boolean required")
        settings.set_network_discovery_enabled(enabled)
        await bridge.set_network_discovery(enabled)
        return {"network_discovery_enabled": enabled}

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

    @router.post("/ports")
    async def set_port(payload: dict):
        key = str(payload.get("id") or "")
        if key not in EDITABLE_PORTS:
            raise HTTPException(status_code=400, detail=f"未知端口项: {key or '(空)'}")
        env_var = EDITABLE_PORTS[key][0]
        if env_pinned(env_var):
            raise HTTPException(
                status_code=409, detail=f"{env_var} 环境变量已固定该端口，无法在此修改"
            )
        raw = payload.get("port")
        if raw in ("", None):
            value = None
        else:
            try:
                value = int(raw)
            except (TypeError, ValueError):
                raise HTTPException(status_code=400, detail="端口必须是数字") from None
        try:
            settings.set_ports({key: value})
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        restart_required = False
        if key == "port":
            # uvicorn can't rebind a running listener; restart to apply.
            restart_required = True
        elif key == "stream_port":
            await bridge.restart_stream_server()
        elif key in ("airplay_rtsp_port", "airplay_udp_base"):
            raop_server.configure_ports(settings.airplay_rtsp_port, settings.airplay_udp_base)
            await bridge.restart()
        elif key == "airplay2_port" and settings.airplay2_enabled:
            await bridge.restart()
        return {
            "ok": True,
            "restart_required": restart_required,
            "ports": _ports_report(bridge, dlna),
        }

    @router.post("/reset")
    async def reset_all():
        """清空数据：删除本地配置、米家登录与管理账号，回到初始引导页。"""
        logger.warning("Resetting all local data on user request")
        await bridge.stop()
        if dlna:
            await dlna.stop()
        if auth:
            auth.logout()
        for name in RESET_FILES:
            try:
                (default_data_dir() / name).unlink()
            except OSError:
                pass
        settings.reset_runtime()
        settings.configure_airplay2_deployment(airplay2_mode())
        if access:
            access.reset()
        if device_manager:
            device_manager.reset()
        await bridge.start()
        if dlna:
            await dlna.start()
        return {"ok": True}

    return router
