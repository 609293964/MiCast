"""SSRF guard for user-supplied playback URLs.

Private LAN addresses stay allowed on purpose: pointing a speaker at a home
NAS (192.168.x.x) is a legitimate use of these endpoints. Loopback, link-local
(including the cloud metadata range), multicast and non-routable targets are
rejected.
"""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from urllib.parse import urlsplit


def _blocked(ip: str) -> bool:
    addr = ipaddress.ip_address(ip)
    return (
        addr.is_loopback
        or addr.is_link_local
        or addr.is_multicast
        or addr.is_unspecified
        or addr.is_reserved
    )


async def validate_http_url(url: str) -> str:
    """Return the cleaned URL or raise ValueError with a user-facing reason."""
    cleaned = url.strip()
    parts = urlsplit(cleaned)
    if parts.scheme not in ("http", "https"):
        raise ValueError("仅支持 http/https 地址")
    host = parts.hostname
    if not host:
        raise ValueError("地址缺少主机名")
    try:
        # IP literals skip DNS; strip an optional IPv6 zone id
        ips = {str(ipaddress.ip_address(host.split("%", 1)[0]))}
    except ValueError:
        try:
            infos = await asyncio.to_thread(socket.getaddrinfo, host, parts.port)
        except OSError as exc:
            raise ValueError(f"无法解析主机名 {host}") from exc
        ips = {info[4][0] for info in infos}
    if not ips or any(_blocked(ip) for ip in ips):
        raise ValueError("目标地址不在允许范围内（回环/链路本地/组播地址不可用）")
    return cleaned
