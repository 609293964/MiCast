import asyncio
import struct

import pytest

from micast.raop.transport import RaopSession


class FakeTransport:
    def __init__(self):
        self.sent = []

    def sendto(self, packet, address):
        self.sent.append((packet, address))


def test_gap_requests_missing_packets_once():
    session = RaopSession(lambda _data: None)
    session.expected = 10
    session.client_host = "192.168.0.20"
    session.client_control_port = 6001
    session.control_transport = FakeTransport()
    session.pending[10] = b"first"
    session.push(12, b"third")
    session.push(12, b"third")

    assert len(session.control_transport.sent) == 1
    packet, address = session.control_transport.sent[0]
    assert address == ("192.168.0.20", 6001)
    assert struct.unpack(">BBHHH", packet)[3:] == (11, 1)


def test_late_retransmissions_are_discarded_without_false_wrap_drop():
    session = RaopSession(lambda _data: None)
    session.expected = 100
    decoded = []
    session._decode = decoded.append

    # Packets 99 and 98 are behind the play head. Repeating them must neither
    # fill the jitter buffer nor look like 65,535 missing forward packets.
    for _ in range(20):
        session.push(99, b"late")
        session.push(98, b"older")

    assert session.pending == {}
    assert session.expected == 100
    assert session.dropped_packets == 0
    assert decoded == []


def test_sequence_wrap_decodes_normally():
    session = RaopSession(lambda _data: None)
    session.expected = 0xFFFF
    decoded = []
    session._decode = decoded.append

    session.push(0, b"zero")
    session.push(0xFFFF, b"last")

    assert decoded == [b"last", b"zero"]
    assert session.expected == 1
    assert session.dropped_packets == 0


def test_real_gap_skip_drains_buffer_immediately():
    session = RaopSession(lambda _data: None)
    session.expected = 10
    decoded = []
    session._decode = decoded.append

    for sequence in range(11, 28):
        session.push(sequence, bytes([sequence]))

    assert session.dropped_packets == 1
    assert session.expected == 28
    assert session.pending == {}
    assert decoded == [bytes([sequence]) for sequence in range(11, 28)]


@pytest.mark.asyncio
async def test_timing_probe_is_sent_to_sender_port():
    session = RaopSession(lambda _data: None)
    session.client_host = "192.168.0.20"
    session.client_timing_port = 6002
    session.timing_transport = FakeTransport()

    task = asyncio.create_task(session._timing_loop())
    await asyncio.sleep(0.01)
    task.cancel()
    await task

    packet, address = session.timing_transport.sent[0]
    assert packet[:2] == b"\x80\xd2"
    assert len(packet) == 32
    assert address == ("192.168.0.20", 6002)
