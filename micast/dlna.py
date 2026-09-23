"""Small local UPnP/DLNA MediaRenderer implementation.

The renderer is deliberately a control bridge: a controller supplies an HTTP
media URI, and MiCast asks the receiver's Xiaomi speaker target(s) to fetch it.
No third-party renderer process is required.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import socket
import uuid
from dataclasses import dataclass
from email.utils import formatdate
from xml.etree import ElementTree as ET

from micast.config import ReceiverConfig, settings
from micast.xiaomi.device_manager import DeviceManager

logger = logging.getLogger(__name__)

SSDP_ADDRESS = "239.255.255.250"
SSDP_PORT = 1900
SERVER_HEADER = "MiCast/0.1 UPnP/1.0 DLNA/1.5"
MEDIA_RENDERER = "urn:schemas-upnp-org:device:MediaRenderer:1"
AV_TRANSPORT = "urn:schemas-upnp-org:service:AVTransport:1"
RENDERING_CONTROL = "urn:schemas-upnp-org:service:RenderingControl:1"
CONNECTION_MANAGER = "urn:schemas-upnp-org:service:ConnectionManager:1"


@dataclass
class DlnaTransportState:
    uri: str = ""
    metadata: str = ""
    next_uri: str = ""
    next_metadata: str = ""
    state: str = "STOPPED"
    volume: int = 50
    muted: bool = False
    volume_mode: str = ""
    session_id: str = ""
    muted_volumes: dict[str, int] | None = None
    volume_received: bool = False


class _SsdpProtocol(asyncio.DatagramProtocol):
    def __init__(self, service: DlnaService):
        self.service = service
        self.transport: asyncio.DatagramTransport | None = None

    def connection_made(self, transport) -> None:
        self.transport = transport

    def datagram_received(self, data: bytes, addr) -> None:
        text = data.decode("utf-8", errors="ignore")
        if not text.startswith("M-SEARCH") or "ssdp:discover" not in text.lower():
            return
        headers = _headers(text)
        search_target = headers.get("st", "ssdp:all")
        # Response construction is synchronous and non-blocking; creating one
        # task per multicast discovery packet allows a LAN burst to create an
        # unbounded task backlog.
        self.service.respond(addr, search_target)


class DlnaService:
    """Advertise and control one virtual DMR for every active playback target."""

    def __init__(self, device_manager: DeviceManager):
        self.device_manager = device_manager
        self.states: dict[str, DlnaTransportState] = {}
        self._transport: asyncio.DatagramTransport | None = None
        self._protocol: _SsdpProtocol | None = None
        self._announce_task: asyncio.Task | None = None
        self._advertised: dict[str, ReceiverConfig] = {}
        self._boot_id = 1
        self._location_host = ""
        self.status = "stopped"
        self.detail = "DLNA 已关闭"

    def active_receivers(self) -> list[ReceiverConfig]:
        return settings.active_receivers() if settings.dlna_enabled else []

    def receiver(self, receiver_id: str) -> ReceiverConfig | None:
        return next((item for item in self.active_receivers() if item.id == receiver_id), None)

    async def start(self) -> None:
        if not settings.dlna_enabled:
            self.status = "stopped"
            self.detail = "DLNA 已关闭"
            return
        if self._transport:
            await self.reconcile()
            return
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind(("", SSDP_PORT))
            interface_ip = _multicast_interface_ip()
            membership = socket.inet_aton(SSDP_ADDRESS) + socket.inet_aton(interface_ip)
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, membership)
            if interface_ip != "0.0.0.0":
                sock.setsockopt(
                    socket.IPPROTO_IP,
                    socket.IP_MULTICAST_IF,
                    socket.inet_aton(interface_ip),
                )
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
            sock.setblocking(False)
            loop = asyncio.get_running_loop()
            transport, protocol = await loop.create_datagram_endpoint(
                lambda: _SsdpProtocol(self), sock=sock
            )
            self._transport = transport
            self._protocol = protocol
            self.status = "running"
            self._advertised = {item.id: item for item in self.active_receivers()}
            self.detail = f"DLNA · {len(self.active_receivers())} 个播放入口"
            await self.announce("ssdp:alive")
            self._announce_task = asyncio.create_task(self._announce_loop())
            logger.info("DLNA discovery listening on UDP %s", SSDP_PORT)
        except Exception as exc:
            self.status = "error"
            self.detail = f"SSDP 启动失败: {exc}"
            logger.exception("Unable to start DLNA discovery")

    async def stop(self) -> None:
        if self._transport:
            await self.announce("ssdp:byebye", self._advertised.values())
        if self._announce_task:
            self._announce_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._announce_task
        self._announce_task = None
        if self._transport:
            self._transport.close()
        self._transport = None
        self._protocol = None
        self._advertised.clear()
        self.status = "stopped"
        self.detail = "DLNA 已关闭"

    async def reconcile(self) -> None:
        if not settings.dlna_enabled:
            await self.stop()
            return
        if not self._transport:
            await self.start()
            return
        active = {item.id: item for item in self.active_receivers()}
        removed = [item for key, item in self._advertised.items() if key not in active]
        if removed:
            await self.announce("ssdp:byebye", removed)
        valid = set(active)
        self.states = {key: value for key, value in self.states.items() if key in valid}
        self._advertised = active
        self.detail = f"DLNA · {len(valid)} 个播放入口"
        await self.announce("ssdp:alive")

    async def _announce_loop(self) -> None:
        while True:
            await asyncio.sleep(30)
            await self.announce("ssdp:alive")

    def uuid_for(self, receiver_id: str) -> str:
        return str(uuid.uuid5(uuid.NAMESPACE_URL, f"micast:dlna:{receiver_id}"))

    def location_for(self, receiver_id: str) -> str:
        return (
            f"http://{settings.effective_stream_host}:{settings.port}"
            f"/dlna/{receiver_id}/description.xml"
        )

    def search_targets(self, receiver: ReceiverConfig) -> list[tuple[str, str]]:
        device_uuid = f"uuid:{self.uuid_for(receiver.id)}"
        return [
            ("upnp:rootdevice", f"{device_uuid}::upnp:rootdevice"),
            (device_uuid, device_uuid),
            (MEDIA_RENDERER, f"{device_uuid}::{MEDIA_RENDERER}"),
            (AV_TRANSPORT, f"{device_uuid}::{AV_TRANSPORT}"),
            (RENDERING_CONTROL, f"{device_uuid}::{RENDERING_CONTROL}"),
            (CONNECTION_MANAGER, f"{device_uuid}::{CONNECTION_MANAGER}"),
        ]

    def respond(self, addr, requested: str) -> None:
        if not self._transport:
            return
        self._refresh_boot_id()
        for receiver in self.active_receivers():
            for target, usn in self.search_targets(receiver):
                if requested not in ("ssdp:all", target):
                    continue
                packet = "\r\n".join(
                    [
                        "HTTP/1.1 200 OK",
                        "CACHE-CONTROL: max-age=120",
                        f"DATE: {formatdate(usegmt=True)}",
                        "EXT:",
                        f"LOCATION: {self.location_for(receiver.id)}",
                        f"SERVER: {SERVER_HEADER}",
                        f"ST: {target}",
                        f"USN: {usn}",
                        f"BOOTID.UPNP.ORG: {self._boot_id}",
                        "CONFIGID.UPNP.ORG: 1",
                        "",
                        "",
                    ]
                ).encode()
                self._transport.sendto(packet, addr)

    async def announce(self, subtype: str, receivers=None) -> None:
        if not self._transport:
            return
        self._refresh_boot_id()
        destination = (SSDP_ADDRESS, SSDP_PORT)
        for receiver in receivers if receivers is not None else self.active_receivers():
            for target, usn in self.search_targets(receiver):
                lines = [
                    "NOTIFY * HTTP/1.1",
                    f"HOST: {SSDP_ADDRESS}:{SSDP_PORT}",
                    f"NT: {target}",
                    f"NTS: {subtype}",
                    f"USN: {usn}",
                ]
                if subtype == "ssdp:alive":
                    lines.extend(
                        [
                            "CACHE-CONTROL: max-age=120",
                            f"LOCATION: {self.location_for(receiver.id)}",
                            f"SERVER: {SERVER_HEADER}",
                            f"BOOTID.UPNP.ORG: {self._boot_id}",
                            "CONFIGID.UPNP.ORG: 1",
                        ]
                    )
                packet = ("\r\n".join(lines) + "\r\n\r\n").encode()
                self._transport.sendto(packet, destination)

    def _refresh_boot_id(self) -> None:
        host = settings.effective_stream_host
        if self._location_host and host != self._location_host:
            self._boot_id += 1
            logger.info("DLNA publish address changed: %s -> %s", self._location_host, host)
        self._location_host = host

    def state_for(self, receiver_id: str) -> DlnaTransportState:
        return self.states.setdefault(receiver_id, DlnaTransportState())

    def _owner(self, receiver_id: str) -> str:
        """DLNA ingress namespaces speaker ownership so it can coexist with an
        AirPlay session targeting the same receiver_id."""
        return f"dlna:{receiver_id}"

    async def set_uri(self, receiver_id: str, uri: str, metadata: str = "") -> None:
        state = self.state_for(receiver_id)
        state.uri = uri
        state.metadata = metadata
        state.state = "STOPPED"
        state.volume_mode = settings.sender_volume_mode
        state.session_id = uuid.uuid4().hex
        logger.info("DLNA %s received media URI: %s", receiver_id, uri)

    async def set_next_uri(self, receiver_id: str, uri: str, metadata: str = "") -> None:
        state = self.state_for(receiver_id)
        state.next_uri = uri
        state.next_metadata = metadata
        logger.info("DLNA %s queued next media URI: %s", receiver_id, uri)

    def media_volume(self, receiver_id: str, session_id: str) -> int:
        state = self.states.get(receiver_id)
        if not state or state.session_id != session_id or state.muted or state.state != "PLAYING":
            return 0
        return state.volume if state.volume_mode == "independent" else 100

    def _media_url(self, receiver_id: str, seek_seconds: float = 0) -> str:
        from urllib.parse import urlencode

        state = self.state_for(receiver_id)
        return (
            f"http://{settings.effective_stream_host}:{settings.stream_port}/dlna-media?"
            + urlencode(
                {
                    "url": state.uri,
                    "ss": seek_seconds,
                    "receiver": receiver_id,
                    "session": state.session_id,
                }
            )
        )

    async def play(self, receiver_id: str) -> None:
        receiver = self.receiver(receiver_id)
        state = self.state_for(receiver_id)
        if receiver is None or not state.uri:
            raise ValueError("No media URI or playback target")
        # Some controllers send SetNextAVTransportURI before the current item
        # finishes and then issue Play to advance. Promote it here instead of
        # merely storing NextURI; otherwise the second track is acknowledged
        # by SOAP but never reaches the Xiaomi targets.
        if state.state != "STOPPED" and state.next_uri:
            state.uri, state.metadata = state.next_uri, state.next_metadata
            state.next_uri = ""
            state.next_metadata = ""
            state.session_id = uuid.uuid4().hex
        targets = settings.receiver_targets(receiver_id)
        state.volume_mode = state.volume_mode or settings.sender_volume_mode
        url = self._media_url(receiver_id) if state.volume_mode == "independent" else state.uri

        # DLNA casting has no per-speaker delay path — the speakers fetch the
        # media URI (or the /dlna-media proxy) directly, outside the stream
        # server's sink buffer — so every target plays the live edge together.
        if not targets:
            raise ValueError("Playback target has no speakers")
        results = await asyncio.gather(
            *(
                self.device_manager.play_stream(
                    did, url, owner=self._owner(receiver_id), force=True
                )
                for did in targets
            ),
            return_exceptions=True,
        )
        accepted = sum(result is True for result in results)
        if not accepted:
            failures = [str(result) for result in results if isinstance(result, Exception)]
            if failures:
                logger.warning("DLNA %s speaker commands failed: %s", receiver_id, failures)
            raise ValueError("No speaker accepted the playback command")
        state.state = "PLAYING"
        if accepted < len(targets):
            logger.warning("DLNA %s started on %s/%s speakers", receiver_id, accepted, len(targets))
        else:
            logger.info("DLNA %s playing on %s speaker(s)", receiver_id, accepted)
        if state.volume_mode == "linked" and (state.volume_received or state.muted):
            for did in targets:
                await self.device_manager.set_volume(did, 0 if state.muted else state.volume)
        if settings.default_volume_enabled and state.volume_mode == "independent":
            for did in targets:
                if self.device_manager.owner_of(did) == self._owner(receiver_id):
                    await self.device_manager.set_volume(did, settings.default_volume)

    async def seek(self, receiver_id: str, target_seconds: float) -> None:
        """DLNA Seek (REL_TIME): replay the current media through the
        transcoding proxy at the requested position."""

        state = self.state_for(receiver_id)
        if not state.uri:
            raise ValueError("No media URI for seek")
        proxy = self._media_url(receiver_id, max(0.0, target_seconds))
        await asyncio.gather(
            *(
                self.device_manager.play_stream(
                    did, proxy, owner=self._owner(receiver_id), force=True
                )
                for did in settings.receiver_targets(receiver_id)
            )
        )
        state.state = "PLAYING"

    async def pause(self, receiver_id: str) -> None:
        await asyncio.gather(
            *(
                self.device_manager.stop(did, owner=self._owner(receiver_id))
                for did in settings.receiver_targets(receiver_id)
            )
        )
        self.state_for(receiver_id).state = "PAUSED_PLAYBACK"

    async def stop_playback(self, receiver_id: str) -> None:
        await asyncio.gather(
            *(
                self.device_manager.stop_playback(did)
                for did in settings.receiver_targets(receiver_id)
            )
        )
        self.state_for(receiver_id).state = "STOPPED"

    async def set_volume(self, receiver_id: str, volume: int) -> None:
        state = self.state_for(receiver_id)
        state.volume_mode = state.volume_mode or settings.sender_volume_mode
        state.volume_received = True
        if state.volume_mode == "independent" or state.muted or state.state == "STOPPED":
            state.volume = volume
            if state.muted_volumes is not None:
                state.muted_volumes = dict.fromkeys(state.muted_volumes, volume)
            return
        await asyncio.gather(
            *(
                self.device_manager.set_volume(did, volume)
                for did in self._owned_volume_targets(receiver_id)
            )
        )
        self.state_for(receiver_id).volume = volume

    def _owned_volume_targets(self, receiver_id: str) -> list[str]:
        return self.device_manager.owned_targets(receiver_id, self._owner(receiver_id))

    async def get_volume(self, receiver_id: str) -> int:
        state = self.state_for(receiver_id)
        if (state.volume_mode or settings.sender_volume_mode) == "linked" and not state.muted:
            values = await asyncio.gather(
                *(
                    self.device_manager.get_volume(did, refresh=True)
                    for did in self._owned_volume_targets(receiver_id)
                )
            )
            known = [value for value in values if value is not None]
            if known:
                state.volume = round(sum(known) / len(known))
        return state.volume

    async def set_mute(self, receiver_id: str, muted: bool) -> None:
        state = self.state_for(receiver_id)
        if state.muted == muted:
            return
        if (state.volume_mode or settings.sender_volume_mode) == "linked":
            if muted:
                previous = {}
                for did in self._owned_volume_targets(receiver_id):
                    value = await self.device_manager.get_volume(did, refresh=True)
                    if value is None:
                        raise ValueError("无法读取音量，不能安全恢复静音")
                    previous[did] = value
                state.muted_volumes = previous
                # Save each speaker's own level, not the group's average.
                for did in previous:
                    await self.device_manager.set_volume(did, 0)
            else:
                for did, value in (state.muted_volumes or {}).items():
                    if did in self._owned_volume_targets(receiver_id):
                        await self.device_manager.set_volume(did, value)
                state.muted_volumes = None
        state.muted = muted


def _headers(message: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for line in message.splitlines()[1:]:
        if ":" in line:
            key, value = line.split(":", 1)
            result[key.strip().lower()] = value.strip()
    return result


def _multicast_interface_ip() -> str:
    """IP of the interface the kernel routes LAN multicast through."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.connect((SSDP_ADDRESS, SSDP_PORT))
            return probe.getsockname()[0]
    except OSError:
        return "0.0.0.0"


def xml_value(body: bytes, name: str, default: str = "") -> str:
    try:
        root = ET.fromstring(body)
    except ET.ParseError:
        return default
    for element in root.iter():
        if element.tag.rsplit("}", 1)[-1] == name:
            return element.text or default
    return default
