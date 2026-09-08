"""Xiaomi account login routes."""

import logging
from io import BytesIO

import aiohttp
import qrcode
import zxingcpp
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse
from PIL import Image

from micast.xiaomi.auth import XiaomiAuth

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/xiaomi", tags=["xiaomi"])

# In-memory QR state (single active QR per process)
_qr_state: dict = {}


def install(auth: XiaomiAuth) -> APIRouter:
    @router.post("/login/qr/start")
    async def qr_start():
        try:
            result = await auth.start_qr_login()
            _qr_state["lp_url"] = result["scan_token"]
            _qr_state["device_id"] = result["device_id"]
            return {"qr_url": result["qr_url"], "scan_token": result["scan_token"]}
        except Exception as e:
            logger.exception("QR start failed")
            raise HTTPException(status_code=500, detail=str(e)) from e

    @router.get("/login/qr/image")
    async def qr_image(url: str = Query(...)):
        """Download Xiaomi's outer QR and return a login QR for its inner URL."""
        try:
            async with (
                aiohttp.ClientSession() as session,
                session.get(url, headers={"User-Agent": "Mozilla/5.0"}) as resp,
            ):
                if resp.status != 200:
                    raise HTTPException(status_code=502, detail="Failed to fetch QR from Xiaomi")
                outer_bytes = await resp.read()

            # Decode outer QR (zxing-cpp: pure wheel, no system zbar needed)
            outer_img = Image.open(BytesIO(outer_bytes))
            decoded = zxingcpp.read_barcodes(outer_img)
            if not decoded:
                raise HTTPException(status_code=502, detail="Could not decode outer QR")

            inner_url = decoded[0].text
            logger.info("Decoded inner QR URL: %s", inner_url)

            # Generate inner QR image
            qr = qrcode.QRCode(version=None, box_size=10, border=2)
            qr.add_data(inner_url)
            qr.make(fit=True)
            inner_img = qr.make_image(fill_color="black", back_color="white").convert("RGB")

            buf = BytesIO()
            inner_img.save(buf, format="PNG")
            buf.seek(0)
            return StreamingResponse(buf, media_type="image/png")
        except HTTPException:
            raise
        except Exception as e:
            logger.exception("QR image generation failed")
            raise HTTPException(status_code=500, detail=str(e)) from e

    @router.get("/login/qr/poll")
    async def qr_poll(scan_token: str = Query(...)):
        try:
            result = await auth.poll_qr_login(scan_token)
            if result.get("status") == "confirmed":
                _qr_state.clear()
            return {"status": result["status"]}
        except Exception as e:
            logger.exception("QR poll failed")
            raise HTTPException(status_code=500, detail=str(e)) from e

    @router.post("/login/cookie")
    async def cookie_login(payload: dict):
        user_id = payload.get("user_id")
        pass_token = payload.get("pass_token")
        if not user_id or not pass_token:
            raise HTTPException(status_code=400, detail="user_id and pass_token required")
        try:
            await auth.login_with_cookie(user_id, pass_token)
            return {"success": True}
        except Exception as e:
            logger.exception("Cookie login failed")
            raise HTTPException(status_code=401, detail=str(e)) from e

    @router.get("/status")
    async def xiaomi_status():
        """Return the current persisted Xiaomi login identity."""
        try:
            return auth.connection_state()
        except Exception as e:
            logger.exception("Failed to load token status")
            raise HTTPException(status_code=500, detail=str(e)) from e

    @router.post("/logout")
    async def xiaomi_logout():
        """Clear stored Xiaomi tokens."""
        try:
            auth.logout()
            return {"ok": True}
        except Exception as e:
            logger.exception("Logout failed")
            raise HTTPException(status_code=500, detail=str(e)) from e

    return router
