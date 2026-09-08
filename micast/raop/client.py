"""RAOP client: pushes audio TO an external classic-AirPlay receiver.

Only the AirPlay 1 flow exists here (ANNOUNCE/SETUP/RECORD, ALAC over
RTP/UDP). AirPlay 2-only devices (HomePod, Apple TV, anything requiring HAP
pairing) reject the handshake — the caller surfaces that as a target error.
"""

import asyncio
import base64
import contextlib
import logging
import os

from micast.raop.crypto import SENDER_FMTP, encrypt_session_key
from micast.raop.protocol import parse_request
from micast.raop.send_transport import RaopSendTransport, ntp_rtp_now

logger = logging.getLogger(__name__)


class RaopError(Exception):
    """Handshake or streaming failure towards an external AirPlay device."""


class RaopSender:
    def __init__(self, host: str, port: int = 5000, name: str = ""):
        self.host = host
        self.port = port
        self.name = name or host
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._cseq = 0
        self._client_instance = os.urandom(8).hex().upper()
        self._aes_key = os.urandom(16)
        self._aes_iv = os.urandom(16)
        self._session = "1"
        self._transport: RaopSendTransport | None = None
        self._recorded = False
        self._request_lock = asyncio.Lock()

    @property
    def streaming(self) -> bool:
        return self._recorded

    @property
    def stats(self) -> dict[str, int]:
        transport = self._transport
        return {
            "timing_replies": transport.timing_replies if transport else 0,
            "resend_requests": transport.resend_requests if transport else 0,
        }

    async def connect(self, timeout: float = 5.0) -> None:
        self._reader, self._writer = await asyncio.wait_for(
            asyncio.open_connection(self.host, self.port), timeout
        )
        try:
            self._transport = RaopSendTransport(self.host, self._aes_key, self._aes_iv)
            await self._transport.open()
            await self._options()
            await self._announce()
            await self._setup()
            await self._record()
        except Exception:
            await self.close()
            raise
        self._recorded = True
        logger.info("RAOP sender connected to %s (%s:%s)", self.name, self.host, self.port)

    async def _options(self) -> None:
        headers, _ = await self._request(
            "OPTIONS", "*", {"Apple-Challenge": base64.b64encode(os.urandom(16)).decode()}
        )
        public = headers.get("public", "")
        if public and "SETUP" not in public.upper():
            raise RaopError(f"{self.name} 不支持 RAOP 推流")

    async def _announce(self) -> None:
        local_ip = self._writer.get_extra_info("sockname")[0]
        fmtp = " ".join(str(value) for value in SENDER_FMTP)
        sdp = (
            "v=0\r\n"
            f"o=MiCast {self._client_instance} 0 IN IP4 {local_ip}\r\n"
            "s=MiCast\r\n"
            f"c=IN IP4 {self.host}\r\n"
            "t=0 0\r\n"
            "m=audio 0 RTP/AVP 96\r\n"
            "a=rtpmap:96 AppleLossless\r\n"
            f"a=fmtp:96 {fmtp}\r\n"
            f"a=rsaaeskey:{encrypt_session_key(self._aes_key)}\r\n"
            f"a=aesiv:{base64.b64encode(self._aes_iv).decode().rstrip('=')}\r\n"
        )
        await self._request(
            "ANNOUNCE",
            self._path(),
            {"Content-Type": "application/sdp"},
            sdp.encode(),
        )

    async def _setup(self) -> None:
        headers, _ = await self._request(
            "SETUP",
            self._path(),
            {
                "Transport": (
                    "RTP/AVP/UDP;unicast;interleaved=0x0-0x1;mode=record;"
                    f"control_port={self._transport.control_port};"
                    f"timing_port={self._transport.timing_port}"
                )
            },
        )
        transport = headers.get("transport", "")
        remote_audio = _transport_port(transport, "server_port")
        if not remote_audio:
            raise RaopError(f"{self.name} 的 SETUP 响应缺少端口")
        self._transport.remote_audio_port = remote_audio
        self._transport.remote_control_port = _transport_port(transport, "control_port")
        self._transport.remote_timing_port = _transport_port(transport, "timing_port")
        self._session = headers.get("session", self._session)

    async def _record(self) -> None:
        start_seq = self._transport.seq
        start_rtp = ntp_rtp_now()
        self._transport.rtptime = start_rtp
        await self._request(
            "RECORD",
            self._path(),
            {"Session": self._session, "RTP-Info": f"seq={start_seq};rtptime={start_rtp}"},
        )

    def send_alac(self, payload: bytes, frames: int) -> None:
        if self._recorded and self._transport:
            self._transport.send_packet(payload, frames)

    async def set_volume(self, percent: int) -> None:
        if not self._writer:
            raise RaopError("音箱尚未连接")
        percent = max(0, min(100, int(percent)))
        db = -144.0 if percent <= 0 else (percent / 100 * 30.0) - 30.0
        await self._request(
            "SET_PARAMETER",
            self._path(),
            {"Content-Type": "text/parameters", "Session": self._session},
            f"volume: {db:.2f}\r\n".encode(),
        )

    async def flush(self) -> None:
        if not self._writer:
            return
        with contextlib.suppress(Exception):
            await self._request(
                "FLUSH",
                self._path(),
                {
                    "Session": self._session,
                    "RTP-Info": f"seq={self._transport.seq};rtptime={self._transport.rtptime}",
                },
            )

    async def teardown(self) -> None:
        if self._writer and self._recorded:
            with contextlib.suppress(Exception):
                await self._request("TEARDOWN", self._path(), {"Session": self._session})
        await self.close()

    async def close(self) -> None:
        self._recorded = False
        if self._transport:
            self._transport.close()
            self._transport = None
        if self._writer:
            self._writer.close()
            with contextlib.suppress(Exception):
                await self._writer.wait_closed()
        self._writer = self._reader = None

    def _path(self) -> str:
        return f"rtsp://{self.host}/{self._client_instance}"

    async def _request(
        self, method: str, path: str, headers: dict | None = None, body: bytes = b""
    ) -> tuple[dict, bytes]:
        async with self._request_lock:
            return await asyncio.wait_for(self._request_unlocked(method, path, headers, body), 8)

    async def _request_unlocked(
        self, method: str, path: str, headers: dict[str, str] | None = None, body: bytes = b""
    ) -> tuple[dict[str, str], bytes]:
        """Send one RTSP request and return (response headers, body)."""
        if not self._writer or not self._reader:
            raise RaopError("not connected")
        self._cseq += 1
        lines = [
            f"{method} {path} RTSP/1.0",
            f"CSeq: {self._cseq}",
            f"Client-Instance: {self._client_instance}",
            "DACP-ID: " + self._client_instance,
            "User-Agent: MiCast/1.0",
        ]
        for key, value in (headers or {}).items():
            lines.append(f"{key}: {value}")
        if body:
            lines.append(f"Content-Length: {len(body)}")
        self._writer.write(("\r\n".join(lines) + "\r\n\r\n").encode() + body)
        await self._writer.drain()

        buffer = b""
        while True:
            message, buffer = parse_request(buffer)
            if message is not None:
                break
            chunk = await self._reader.read(65536)
            if not chunk:
                raise RaopError(f"{self.name} 在 {method} 期间断开了连接")
            buffer += chunk
        # A response parses as method="RTSP/1.0", path="<status>".
        try:
            status = int(message.path)
        except ValueError as err:
            raise RaopError(f"{self.name} 返回了无法理解的 {method} 响应") from err
        if status != 200:
            raise RaopError(f"{self.name} 拒绝了 {method}（HTTP {status}）")
        return message.headers, message.body


def _transport_port(value: str, key: str) -> int:
    for part in value.split(";"):
        part = part.strip()
        if part.startswith(f"{key}="):
            with contextlib.suppress(ValueError):
                return int(part.split("=", 1)[1])
    return 0
