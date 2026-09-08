"""Topology snapshot routes: one-shot REST plus an SSE stream for the map view."""

import asyncio
import json
import logging

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from micast.audio_bridge import AudioBridge
from micast.topology import build_topology
from micast.xiaomi.device_manager import DeviceManager

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/topology", tags=["topology"])

# How often the snapshot is rebuilt; frames are only sent when it changed.
TICK_SECONDS = 1.0
# Comment heartbeat so proxies and browsers keep the connection alive.
HEARTBEAT_SECONDS = 15.0


def install(bridge: AudioBridge, device_manager: DeviceManager) -> APIRouter:
    @router.get("")
    async def get_topology():
        return build_topology(bridge, device_manager)

    @router.get("/stream")
    async def stream_topology(request: Request):
        async def events():
            last_payload: str | None = None
            heartbeat_in = 0.0
            while not await request.is_disconnected():
                try:
                    snapshot = build_topology(bridge, device_manager)
                    payload = json.dumps(snapshot, ensure_ascii=False)
                except Exception:
                    logger.exception("Failed to build topology snapshot")
                    await asyncio.sleep(TICK_SECONDS)
                    continue
                if payload != last_payload:
                    last_payload = payload
                    yield f"data: {payload}\n\n"
                    heartbeat_in = HEARTBEAT_SECONDS
                elif heartbeat_in <= 0:
                    yield ": ping\n\n"
                    heartbeat_in = HEARTBEAT_SECONDS
                heartbeat_in -= TICK_SECONDS
                await asyncio.sleep(TICK_SECONDS)

        return StreamingResponse(
            events(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    return router
