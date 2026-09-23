"""External DLNA renderer discovery routes."""

from fastapi import APIRouter

from micast.audio_bridge import AudioBridge
from micast.config import settings

router = APIRouter(prefix="/api/dlna-devices", tags=["dlna-devices"])


def install(bridge: AudioBridge) -> APIRouter:
    @router.get("")
    async def list_dlna_devices():
        discovery = bridge.dlna_discovery
        attached = {
            target_id: group.id for group in settings.groups for target_id in group.dlna_targets
        }
        statuses = bridge.diagnostics.get("dlna_targets", {})
        runtime_by_id = {
            runtime["id"]: runtime for targets in statuses.values() for runtime in targets.values()
        }
        devices = []
        for device in discovery.devices() if discovery else []:
            runtime = runtime_by_id.get(device.id, {})
            devices.append(
                {
                    "id": device.id,
                    "name": device.name,
                    "model": device.model,
                    "kind": device.kind,
                    "online": device.online,
                    "supported": bool(device.control_url),
                    "unsupported_reason": "" if device.control_url else "不支持 AVTransport 投放",
                    "attached_group": attached.get(device.id),
                    "stream_status": runtime.get("status", ""),
                    "stream_detail": runtime.get("detail", ""),
                    "volume_control": bool(device.rendering_url) and device.online,
                    "volume_readback": bool(device.rendering_url) and device.online,
                }
            )
        known = {device["id"] for device in devices}
        for target_id, group_id in attached.items():
            if target_id in known:
                continue
            runtime = runtime_by_id.get(target_id, {})
            devices.append(
                {
                    "id": target_id,
                    "name": runtime.get("name") or target_id,
                    "model": "",
                    "kind": "speaker",
                    "online": False,
                    "supported": True,
                    "unsupported_reason": "",
                    "attached_group": group_id,
                    "stream_status": runtime.get("status", ""),
                    "stream_detail": runtime.get("detail", ""),
                }
            )
        return devices

    return router
