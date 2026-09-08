"""Status routes."""

from fastapi import APIRouter

from micast.audio_bridge import AudioBridge

router = APIRouter(prefix="/api/status", tags=["status"])


def install(bridge: AudioBridge) -> APIRouter:
    @router.get("")
    async def get_status():
        return bridge.status

    return router
