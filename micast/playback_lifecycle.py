"""Single source of truth for terminating playback output."""

import asyncio

from micast.audio_bridge import AudioBridge
from micast.config import settings
from micast.xiaomi.device_manager import DeviceManager


def _base_receiver_id(receiver_id: str) -> str:
    return receiver_id.removesuffix("-L").removesuffix("-R")


async def stop_output(
    bridge: AudioBridge,
    device_manager: DeviceManager,
    receiver_id: str | None = None,
) -> dict[str, object]:
    """Stop speakers and sender/stream connections consistently."""
    base_id = _base_receiver_id(receiver_id) if receiver_id else None
    disconnected = await bridge.disconnect_sessions(base_id)
    if base_id:
        stream_ids = [base_id, f"{base_id}-L", f"{base_id}-R"]
        kicked = sum(bridge.stream_server.kick_clients(sid) for sid in stream_ids)
        targets = settings.receiver_targets(base_id)
    else:
        kicked = sum(
            bridge.stream_server.kick_clients(sid)
            for sid in bridge.stream_server.stream_ids()
        )
        targets = [
            str(device.get("deviceID"))
            for device in device_manager.get_control_targets()
            if device.get("deviceID")
            and (
                device_manager.is_playing(str(device["deviceID"]))
                or device_manager.is_paused(str(device["deviceID"]))
            )
        ]
    await asyncio.gather(
        *(device_manager.stop_playback(did) for did in dict.fromkeys(targets)),
        return_exceptions=True,
    )
    return {"disconnected": disconnected, "kicked": kicked, "stopped": targets}
