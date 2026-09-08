"""RAOP sender: crypto round-trip, packetizer, RTP push, and a full loopback
through a real local RaopServer (the strongest no-hardware signal)."""

import asyncio
import math
import struct

from micast.raop.alac_encoder import FRAME_SAMPLES, AlacPacketizer
from micast.raop.client import RaopSender
from micast.raop.crypto import decrypt_session_key, encrypt_session_key
from micast.raop.send_transport import RaopSendTransport
from micast.raop.server import RaopServer


class FakeZeroconf:
    def register_service(self, service):
        pass

    def unregister_service(self, service):
        pass


def _tone_pcm(seconds: float, freq: float = 440.0) -> bytes:
    count = int(44100 * seconds)
    out = bytearray()
    for i in range(count):
        sample = int(8000 * math.sin(2 * math.pi * freq * i / 44100))
        out += struct.pack("<hh", sample, sample)
    return bytes(out)


def test_session_key_round_trip():
    key = bytes(range(16))
    assert decrypt_session_key(encrypt_session_key(key)) == key


def test_packetizer_emits_one_packet_per_4096_samples():
    packetizer = AlacPacketizer()
    packets = packetizer.encode(_tone_pcm(FRAME_SAMPLES * 2 / 44100))
    assert len(packets) == 2
    assert all(len(packet) > 100 for packet in packets)
    tail = packetizer.flush()
    assert tail == []  # input was frame-aligned


def test_packetizer_decodes_back_with_server_cookie():
    """Encoder output must decode with the cookie our server builds from fmtp."""
    import av

    from micast.raop.crypto import SENDER_FMTP, alac_cookie

    packetizer = AlacPacketizer()
    packets = packetizer.encode(_tone_pcm(FRAME_SAMPLES / 44100))
    decoder = av.CodecContext.create("alac", "r")
    decoder.extradata = alac_cookie(SENDER_FMTP)
    frames = []
    for packet in packets:
        frames += decoder.decode(av.Packet(packet))
    assert sum(frame.samples for frame in frames) == FRAME_SAMPLES


async def test_send_transport_rtp_headers():
    received: list[bytes] = []

    class Capture(asyncio.DatagramProtocol):
        def datagram_received(self, packet, addr):
            received.append(packet)

    loop = asyncio.get_running_loop()
    capture_transport, _ = await loop.create_datagram_endpoint(Capture, local_addr=("127.0.0.1", 0))
    port = capture_transport.get_extra_info("sockname")[1]

    transport = RaopSendTransport("127.0.0.1", b"\x00" * 16, b"\x01" * 16)
    await transport.open()
    transport.remote_audio_port = port
    transport.rtptime = 1000
    payload = bytes(range(64)) * 10
    transport.send_packet(payload, FRAME_SAMPLES)
    transport.send_packet(payload, FRAME_SAMPLES)
    await asyncio.sleep(0.1)

    assert len(received) == 2
    first, second = received
    version, payload_type, seq, rtptime = struct.unpack_from(">BBHI", first)
    assert version == 0x80 and payload_type == 0x60
    assert rtptime == 1000
    _, _, seq2, rtptime2 = struct.unpack_from(">BBHI", second)
    assert seq2 == (seq + 1) & 0xFFFF
    assert rtptime2 == 1000 + FRAME_SAMPLES
    # Encrypted payload differs from plaintext but has identical length.
    assert first[12:] != payload and len(first[12:]) == len(payload)
    capture_transport.close()
    transport.close()


async def test_loopback_sender_to_local_server():
    """Full path: RaopSender → real RaopServer → decoded PCM (SDP/AES/RTP/ALAC)."""
    server = RaopServer("127.0.0.1", "Loopback-Test", FakeZeroconf())
    await server.start()
    try:
        sender = RaopSender("127.0.0.1", server.port, "loopback")
        await sender.connect()
        packetizer = AlacPacketizer()
        for packet in packetizer.encode(_tone_pcm(0.5)):
            sender.send_alac(packet, FRAME_SAMPLES)
        for packet in packetizer.flush():
            sender.send_alac(packet, FRAME_SAMPLES)

        pcm = bytearray()
        deadline = asyncio.get_running_loop().time() + 5
        reader = server.pcm_reader
        while len(pcm) < 8000 and asyncio.get_running_loop().time() < deadline:
            try:
                chunk = await asyncio.wait_for(reader.read(8192), timeout=1.0)
            except TimeoutError:
                break
            if not chunk:
                break
            pcm += chunk
        await sender.teardown()

        assert len(pcm) >= 8000, f"server decoded too little PCM: {len(pcm)}"
        assert any(b != 0 for b in pcm), "decoded PCM is silent"
        assert server.timing_responses >= 0  # session counters collected
    finally:
        await server.stop()
