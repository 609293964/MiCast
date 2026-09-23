"""mDNS discovery of external AirPlay receivers on the LAN (_raop._tcp).

Browse-only; the registry is runtime state (mDNS data goes stale), so nothing
is persisted — groups store only the stable device id (MAC hex) and resolve it
fresh at session start.
"""

import asyncio
import hashlib
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass

from zeroconf import ServiceBrowser, ServiceListener, Zeroconf

logger = logging.getLogger(__name__)

SERVICE_TYPE = "_raop._tcp.local."
_SERVICE_NAME_RE = re.compile(r"^(?P<mac>[0-9A-Fa-f]{12})@(?P<name>.*)\._raop\._tcp\.local\.$")


@dataclass
class AirPlayDevice:
    id: str  # lowercase MAC hex from the service name prefix
    name: str
    host: str = ""
    port: int = 0
    model: str = ""  # txt "am"
    kind: str = "speaker"  # speaker | tv | projector — drives the UI icon
    needs_password: bool = False
    own: bool = False  # MiCast's own receiver, never shown as an external device


def classify_device(model: str, name: str) -> str:
    """Best-effort device category from the advertised model and name."""
    text = f"{model} {name}".lower()
    if any(token in text for token in ("projector", "jmgo", "投影", "xgimi", "极米")):
        return "projector"
    if "appletv" in text or "电视" in text or text.endswith(" tv"):
        return "tv"
    return "speaker"


class AirPlayDiscovery(ServiceListener):
    def __init__(
        self,
        zeroconf: Zeroconf,
        own_ids: Callable[[], set[str]] | None = None,
        on_change: Callable[[], None] | None = None,
    ):
        self._zeroconf = zeroconf
        self._own_ids = own_ids or (lambda: set())
        self._on_change = on_change
        self._devices: dict[str, AirPlayDevice] = {}
        self._browser: ServiceBrowser | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._debounce: asyncio.TimerHandle | None = None

    async def start(self) -> None:
        if self._browser:
            return
        self._loop = asyncio.get_running_loop()
        self._browser = await asyncio.to_thread(ServiceBrowser, self._zeroconf, SERVICE_TYPE, self)
        logger.info("Browsing LAN for AirPlay devices (%s)", SERVICE_TYPE)

    async def stop(self) -> None:
        if self._debounce:
            self._debounce.cancel()
            self._debounce = None
        if self._browser:
            await asyncio.to_thread(self._browser.cancel)
            self._browser = None

    async def rebind(self, zeroconf: Zeroconf) -> None:
        """Re-point at a new Zeroconf instance (the provider recreates its own
        on every bridge restart)."""
        if zeroconf is self._zeroconf and self._browser:
            return
        await self.stop()
        self._devices.clear()
        self._zeroconf = zeroconf
        await self.start()

    def devices(self, include_own: bool = False) -> list[AirPlayDevice]:
        return sorted(
            (device for device in self._devices.values() if include_own or not device.own),
            key=lambda device: device.name.lower(),
        )

    def resolve(self, device_id: str) -> AirPlayDevice | None:
        device = self._devices.get(device_id)
        if device is None or device.own or not device.host:
            return None
        return device

    # -- zeroconf ServiceListener callbacks (zeroconf worker thread) ---------

    def add_service(self, zc: Zeroconf, type_: str, name: str) -> None:
        self._refresh(zc, type_, name)

    def update_service(self, zc: Zeroconf, type_: str, name: str) -> None:
        self._refresh(zc, type_, name)

    def remove_service(self, zc: Zeroconf, type_: str, name: str) -> None:
        device_id = _service_name_to_id(name)
        if device_id and device_id in self._devices:
            del self._devices[device_id]
            self._schedule_change()

    # -----------------------------------------------------------------------

    def _refresh(self, zc: Zeroconf, type_: str, name: str) -> None:
        try:
            self._refresh_inner(zc, type_, name)
        except Exception:
            logger.exception("Failed to process AirPlay service %s", name)

    def _refresh_inner(self, zc: Zeroconf, type_: str, name: str) -> None:
        info = zc.get_service_info(type_, name, timeout=2000)
        if info is None:
            return
        device = _device_from_info(name, info)
        if device is None:
            return
        device.own = device.id in self._own_ids()
        if self._devices.get(device.id) == device:
            return
        self._devices[device.id] = device
        if not device.own:
            logger.info(
                "Discovered AirPlay device: %s (%s:%s)%s",
                device.name,
                device.host,
                device.port,
                " [需要密码]" if device.needs_password else "",
            )
        self._schedule_change()

    def _schedule_change(self) -> None:
        """Coalesce discovery bursts (one device can fire several updates)."""
        if not self._loop or not self._on_change:
            return
        if self._debounce:
            self._debounce.cancel()

        def fire() -> None:
            self._debounce = None
            self._on_change()

        self._loop.call_soon_threadsafe(self._arm, fire)

    def _arm(self, fire) -> None:
        self._debounce = self._loop.call_later(0.3, fire)


def _service_name_to_id(name: str) -> str | None:
    match = _SERVICE_NAME_RE.match(name)
    if match:
        return match.group("mac").lower()
    if name.endswith(f".{SERVICE_TYPE}"):
        # Non-Apple naming: fall back to a stable hash of the full name.
        logger.warning("AirPlay service without MAC prefix: %s", name)
        return hashlib.sha1(name.encode()).hexdigest()[:12]
    return None


def _device_from_info(name: str, info) -> AirPlayDevice | None:
    device_id = _service_name_to_id(name)
    if not device_id:
        return None
    match = _SERVICE_NAME_RE.match(name)
    display = match.group("name") if match else name.split(".")[0]
    # Prefer a routable IPv4: devices commonly also publish link-local and
    # IPv6 addresses that are useless to us.
    addresses = info.parsed_addresses()
    host = next(
        (addr for addr in addresses if "." in addr and not addr.startswith("169.254.")),
        addresses[0] if addresses else "",
    )
    properties = {
        key.decode(errors="replace").lower(): value.decode(errors="replace")
        for key, value in (info.properties or {}).items()
    }
    return AirPlayDevice(
        id=device_id,
        name=display,
        host=host,
        port=info.port or 5000,
        model=properties.get("am", ""),
        kind=classify_device(properties.get("am", ""), display),
        needs_password=properties.get("pw", "false").lower() == "true",
    )
