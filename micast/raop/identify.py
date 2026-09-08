"""Best-effort mDNS reverse lookup of AirPlay senders.

Classic RAOP carries no sender identity (only desktop iTunes sends a
User-Agent), but iOS/macOS devices register their hostname over mDNS —
"Dys-iPhone.local", "MacBook-Pro.local" — so a PTR query against the
sender's IP usually reveals the real device name. Results are cached and
lookups never block the RTSP handshake.
"""

from __future__ import annotations

import asyncio
import logging
import socket
import struct
import time

logger = logging.getLogger(__name__)

MDNS_ADDR = ("224.0.0.251", 5353)
QUERY_TIMEOUT_SECONDS = 1.5
CACHE_TTL_SECONDS = 600
NEGATIVE_TTL_SECONDS = 60

_cache: dict[str, tuple[str | None, float]] = {}
_pending: set[str] = set()


async def identify(host: str) -> str | None:
    """Resolve a sender IP to its mDNS hostname ("Dys-iPhone.local")."""
    now = time.monotonic()
    entry = _cache.get(host)
    if entry and now - entry[1] < (CACHE_TTL_SECONDS if entry[0] else NEGATIVE_TTL_SECONDS):
        return entry[0]
    if host in _pending:
        # A lookup for this host is already in flight; wait briefly for it.
        for _ in range(10):
            await asyncio.sleep(0.2)
            entry = _cache.get(host)
            if entry and entry[0]:
                return entry[0]
        return None
    _pending.add(host)
    try:
        try:
            name = await asyncio.to_thread(_mdns_ptr_lookup, host)
        except Exception:
            logger.debug("mDNS lookup failed for %s", host, exc_info=True)
            name = None
        _cache[host] = (name, time.monotonic())
        if name:
            logger.info("Identified AirPlay sender %s as %s", host, name)
        return name
    finally:
        _pending.discard(host)


def _mdns_ptr_lookup(host: str) -> str | None:
    reversed_name = ".".join(reversed(host.split("."))) + ".in-addr.arpa"
    query = _build_ptr_query(reversed_name)
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP) as sock:
        sock.settimeout(QUERY_TIMEOUT_SECONDS)
        sock.sendto(query, MDNS_ADDR)
        deadline = time.monotonic() + QUERY_TIMEOUT_SECONDS
        while time.monotonic() < deadline:
            try:
                data, _ = sock.recvfrom(4096)
            except TimeoutError:
                break
            name = _extract_ptr_answer(data, reversed_name)
            if name:
                return name
    return None


def _encode_name(name: str) -> bytes:
    out = bytearray()
    for label in name.rstrip(".").split("."):
        out.append(len(label))
        out += label.encode("utf-8", errors="replace")
    out.append(0)
    return bytes(out)


def _build_ptr_query(name: str) -> bytes:
    # id=0, flags=0 (query), qd=1; class IN with the QU bit set so the device
    # may answer via unicast instead of multicast.
    header = struct.pack(">HHHHHH", 0, 0, 1, 0, 0, 0)
    return header + _encode_name(name) + struct.pack(">HH", 12, 0x8001)


def _read_name(data: bytes, offset: int) -> tuple[str, int]:
    """Read a possibly-compressed DNS name; returns (name, next_offset)."""
    labels: list[str] = []
    jumped = False
    next_offset = offset
    while True:
        length = data[offset]
        if length & 0xC0 == 0xC0:
            pointer = struct.unpack_from(">H", data, offset)[0] & 0x3FFF
            if not jumped:
                next_offset = offset + 2
            offset = pointer
            jumped = True
            continue
        offset += 1
        if length == 0:
            break
        labels.append(data[offset : offset + length].decode("utf-8", errors="replace"))
        offset += length
    if not jumped:
        next_offset = offset
    return ".".join(labels), next_offset


def _extract_ptr_answer(data: bytes, wanted: str) -> str | None:
    try:
        if len(data) < 12:
            return None
        _id, flags, qd, an, _ns, _ar = struct.unpack_from(">HHHHHH", data, 0)
        if not flags & 0x8000:  # responses only
            return None
        offset = 12
        for _ in range(qd):
            _, offset = _read_name(data, offset)
            offset += 4
        for _ in range(an):
            name, offset = _read_name(data, offset)
            rtype, _rclass, _ttl, rdlength = struct.unpack_from(">HHIH", data, offset)
            offset += 10
            if rtype == 12 and name.rstrip(".").lower() == wanted.lower():
                target, _ = _read_name(data, offset)
                target = target.rstrip(".")
                if target.lower().endswith(".local"):
                    target = target[:-6]
                return target
            offset += rdlength
    except (IndexError, struct.error, UnicodeDecodeError):
        return None
    return None
