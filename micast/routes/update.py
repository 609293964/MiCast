"""Software update routes: check GitHub releases, exe-only in-app download."""

import asyncio
import logging

from fastapi import APIRouter, HTTPException

from micast import update_checker
from micast.deployment import update_download_supported

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/update", tags=["update"])


def install() -> APIRouter:
    @router.get("/check")
    async def check(force: bool = False):
        try:
            return await update_checker.check_for_update(force=force)
        except Exception as e:
            logger.warning("Update check failed: %s", e)
            raise HTTPException(status_code=502, detail=f"检查更新失败：{e}") from e

    @router.post("/download")
    async def download():
        if not update_download_supported():
            raise HTTPException(status_code=403, detail="当前安装方式不支持应用内下载，请前往发布页手动更新")
        try:
            info = await update_checker.check_for_update()
        except Exception as e:
            raise HTTPException(status_code=502, detail=f"检查更新失败：{e}") from e
        asset = info.get("asset")
        if not info.get("update_available") or not asset:
            raise HTTPException(status_code=404, detail="没有可下载的更新")
        asyncio.create_task(update_checker.download_update(asset))
        return {"started": True, "asset": asset["name"]}

    @router.get("/download/status")
    async def download_status():
        return update_checker.download_state

    @router.post("/apply")
    async def apply():
        if not update_download_supported():
            raise HTTPException(status_code=403, detail="当前安装方式不支持应用内更新")
        try:
            path = update_checker.apply_update()
            return {"ok": True, "path": path}
        except Exception as e:
            raise HTTPException(status_code=400, detail=str(e)) from e

    return router
