"""RTSP framing primitives used by the native RAOP receiver."""

from dataclasses import dataclass


@dataclass(frozen=True)
class RtspRequest:
    method: str
    path: str
    version: str
    headers: dict[str, str]
    body: bytes

    @property
    def cseq(self) -> str:
        return self.headers.get("cseq", "0")


def parse_request(buffer: bytes) -> tuple[RtspRequest | None, bytes]:
    """Parse one complete RTSP request and return any remaining bytes."""
    header_end = buffer.find(b"\r\n\r\n")
    if header_end < 0:
        return None, buffer
    header_blob = buffer[:header_end].decode("utf-8", errors="replace")
    lines = header_blob.split("\r\n")
    parts = lines[0].split(" ", 2)
    if len(parts) != 3:
        raise ValueError("invalid RTSP request line")
    headers: dict[str, str] = {}
    for line in lines[1:]:
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        headers[key.strip().lower()] = value.strip()
    content_length = int(headers.get("content-length", "0"))
    message_end = header_end + 4 + content_length
    if len(buffer) < message_end:
        return None, buffer
    return (
        RtspRequest(
            method=parts[0],
            path=parts[1],
            version=parts[2],
            headers=headers,
            body=buffer[header_end + 4 : message_end],
        ),
        buffer[message_end:],
    )


def response(cseq: str, status: int = 200, headers: dict[str, str] | None = None) -> bytes:
    reasons = {200: "OK", 400: "Bad Request", 501: "Not Implemented"}
    # Classic AirPlay clients use this compatibility identity during RTSP setup.
    values = {"CSeq": cseq, "Server": "AirTunes/105.1"}
    values.update(headers or {})
    lines = [f"RTSP/1.0 {status} {reasons.get(status, '')}"]
    lines.extend(f"{key}: {value}" for key, value in values.items())
    return ("\r\n".join(lines) + "\r\n\r\n").encode()
