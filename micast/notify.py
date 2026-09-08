"""Expiry notification: push a message to a webhook when the Xiaomi login
dies, so a silently-broken bridge gets noticed.

Supported targets: 飞书自定义机器人 (default payload) and WxPusher
(URL containing "wxpusher" switches the payload shape).
"""

import asyncio
import logging
import time

import httpx

logger = logging.getLogger(__name__)

_RESEND_INTERVAL_SECONDS = 12 * 3600


class Notifier:
    def __init__(self):
        self._last_sent_at = 0.0
        self._client = httpx.AsyncClient(timeout=8.0)

    async def close(self) -> None:
        await self._client.aclose()

    async def notify_expired(self) -> bool:
        """Send "login expired" once per resend interval; False when skipped."""
        from micast.config import settings

        url = settings.notify_webhook_url.strip()
        if not url:
            return False
        if time.monotonic() - self._last_sent_at < _RESEND_INTERVAL_SECONDS:
            return False
        self._last_sent_at = time.monotonic()
        text = "【MiCast】小米账号登录已失效，投放已中断。请打开 MiCast 面板重新扫码登录。"
        try:
            if "wxpusher" in url:
                await self._client.post(url, json={
                    "msgtype": "text",
                    "text": {"content": text},
                })
            else:  # 飞书自定义机器人
                await self._client.post(url, json={
                    "msg_type": "text",
                    "content": {"text": text},
                })
            logger.info("登录失效通知已推送")
            return True
        except Exception:
            logger.exception("登录失效通知推送失败")
            return False


def notify_expired_soon(notifier: Notifier) -> None:
    """Schedule a notification from sync code (auth callbacks are sync)."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    loop.create_task(notifier.notify_expired())
