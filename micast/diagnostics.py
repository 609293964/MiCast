"""Sanitized diagnostic report export.

One click in the diagnostics page downloads a JSON bundle with the settings,
live state and recent logs a maintainer needs to debug a report. Everything
passes through the sanitizer so credentials (Xiaomi tokens, webhook secrets,
orchestrator keys) never leave the machine inside the file.
"""

from __future__ import annotations

import json
import logging
import platform
import re
import sys
from datetime import datetime, timezone
from typing import Any
from urllib.parse import parse_qsl, urlsplit

from micast import __version__
from micast.config import settings
from micast.runtime_log import runtime_logs

logger = logging.getLogger(__name__)

REDACTED = "***"

# key=value / JSON "key": "value" pairs for common credential names
_KV_PATTERN = re.compile(
    r"(?i)\b(pass[_-]?token|service[_-]?token|ssecurity|psecurity|c_user|"
    r"app[_-]?token|access[_-]?token|refresh[_-]?token|sessionToken|token|sid|uid|"
    r"authorization|password|secret|encryption[_-]?key)"
    r"([\"']?\s*[:=]\s*[\"']?)([^\s\"'&,;}]+)"
)
# Cookie headers carry several of the above in one blob
_COOKIE_PATTERN = re.compile(r"(?i)\b(cookie\s*[:=]\s*)([^\n\"']+)")
# Feishu bot webhooks put the secret in the URL path
_FEISHU_PATTERN = re.compile(r"(open\.feishu\.cn/open-apis/bot/v2/hook/)[\w-]+")
# WxPusher secrets ride in the query string
_WXPUSHER_PATTERN = re.compile(r"(?i)((?:appToken|uid|token)=)[\w-]{8,}")

# Settings fields dropped from the report entirely
_SECRET_FIELDS = {"orchestrator_token", "encryption_key"}


def _known_secrets() -> list[str]:
    """Concrete secret values worth redacting wherever they appear."""
    values: list[str] = []
    for raw in (settings.orchestrator_token, settings.encryption_key):
        if raw:
            values.append(raw)
    webhook = settings.notify_webhook_url.strip()
    if webhook:
        parts = urlsplit(webhook)
        tail = parts.path.rstrip("/").rsplit("/", 1)[-1]
        if tail:
            values.append(tail)
        values.extend(value for _, value in parse_qsl(parts.query) if value)
    return [value for value in dict.fromkeys(values) if len(value) >= 6]


def sanitize_text(text: str) -> str:
    text = _KV_PATTERN.sub(lambda m: f"{m.group(1)}{m.group(2)}{REDACTED}", text)
    text = _COOKIE_PATTERN.sub(lambda m: f"{m.group(1)}{REDACTED}", text)
    text = _FEISHU_PATTERN.sub(rf"\g<1>{REDACTED}", text)
    text = _WXPUSHER_PATTERN.sub(rf"\g<1>{REDACTED}", text)
    for secret in _known_secrets():
        text = text.replace(secret, REDACTED)
    return text


def sanitize_obj(obj: Any) -> Any:
    """Round-trip through JSON so every string leaf gets scrubbed."""
    try:
        return json.loads(sanitize_text(json.dumps(obj, ensure_ascii=False, default=str)))
    except (TypeError, ValueError):
        logger.exception("Failed to sanitize diagnostic payload")
        return {"error": "payload unavailable"}


def public_settings() -> dict[str, Any]:
    data = settings.model_dump(mode="json")
    for key in _SECRET_FIELDS:
        data.pop(key, None)
    webhook = str(data.get("notify_webhook_url") or "")
    if webhook:
        parts = urlsplit(webhook)
        data["notify_webhook_url"] = f"{parts.scheme}://{parts.netloc}/{REDACTED}"
    return data


async def collect_state(bridge, device_manager) -> dict[str, Any]:
    """Live state snapshot shared by /api/debug/state and the report."""
    service = await device_manager.auth.ensure_service()
    devices = await device_manager.list_devices()
    return {
        "logged_in": service is not None,
        "selected_device_id": device_manager.selected_device_id,
        "devices": [
            {
                "did": d.get("deviceID"),
                "name": d.get("name"),
                "hardware": d.get("hardware"),
                "presence": d.get("presence"),
                "miotDID": d.get("miotDID"),
            }
            for d in devices
        ],
        "pcm_source": settings.pcm_source,
        "stream_url": bridge.status["stream_url"],
        "audio_config": settings.audio.model_dump(mode="json"),
        "bridge_status": bridge.status,
        "stream_clients": bridge._stream_server.total_flowing_clients(),
        "stream_bytes_sent": bridge._stream_server.total_bytes(),
        "diagnostics": bridge.diagnostics,
        "logs": runtime_logs.snapshot(),
    }


async def build_report(bridge, device_manager) -> dict[str, Any]:
    logs = runtime_logs.snapshot(limit=1500)
    for item in logs:
        item["message"] = sanitize_text(item.get("message", ""))
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "app": "MiCast",
        "version": __version__,
        "platform": platform.platform(),
        "python": sys.version.split()[0],
        "settings": sanitize_obj(public_settings()),
        "state": sanitize_obj(await collect_state(bridge, device_manager)),
        "logs": logs,
    }
