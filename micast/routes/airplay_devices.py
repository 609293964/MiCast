"""External AirPlay device discovery routes."""

from fastapi import APIRouter

from micast.audio_bridge import AudioBridge
from micast.config import settings

router = APIRouter(prefix="/api/airplay-devices", tags=["airplay-devices"])


def install(bridge: AudioBridge) -> APIRouter:
    @router.get("")
    async def list_airplay_devices():
        discovery = bridge.airplay_discovery
        attached = {
            target_id: group.id for group in settings.groups for target_id in group.airplay_targets
        }
        statuses = bridge.diagnostics.get("airplay_targets", {})
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
                    "host": device.host,
                    "port": device.port,
                    "model": device.model,
                    "kind": device.kind,
                    "online": bool(device.host),
                    "supported": not device.needs_password,
                    "unsupported_reason": "需要密码，暂不支持" if device.needs_password else "",
                    "attached_group": attached.get(device.id),
                    "stream_status": runtime.get("status", ""),
                    "stream_detail": runtime.get("detail", ""),
                    "volume_control": runtime.get("status") == "streaming",
                    "volume_readback": False,
                }
            )
        # Targets attached to a group but not currently discoverable stay
        # visible (offline) so the user can still detach them.
        known = {device["id"] for device in devices}
        for target_id, group_id in attached.items():
            if target_id in known:
                continue
            runtime = runtime_by_id.get(target_id, {})
            devices.append(
                {
                    "id": target_id,
                    "name": runtime.get("name") or target_id,
                    "host": "",
                    "port": 0,
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
