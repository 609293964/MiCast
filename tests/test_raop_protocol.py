from micast.raop.protocol import parse_request, response


def test_rtsp_parser_waits_for_complete_body():
    partial = b"ANNOUNCE rtsp://speaker RTSP/1.0\r\nCSeq: 7\r\nContent-Length: 4\r\n\r\nab"
    request, remaining = parse_request(partial)
    assert request is None
    assert remaining == partial


def test_rtsp_parser_preserves_pipelined_remainder():
    payload = (
        b"SET_PARAMETER * RTSP/1.0\r\nCSeq: 8\r\nContent-Length: 4\r\n\r\nvol!"
        b"OPTIONS * RTSP/1.0\r\nCSeq: 9\r\n\r\n"
    )
    request, remaining = parse_request(payload)
    assert request is not None
    assert request.method == "SET_PARAMETER"
    assert request.cseq == "8"
    assert request.body == b"vol!"
    assert remaining.startswith(b"OPTIONS")


def test_rtsp_response_includes_sequence():
    payload = response("12")
    assert b"CSeq: 12\r\n" in payload
    assert b"Server: AirTunes/105.1\r\n" in payload
