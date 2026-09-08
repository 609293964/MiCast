import av

from micast.raop.crypto import alac_cookie
from micast.raop.transport import RaopSession


def test_transport_decoder_opens_without_optional_numpy():
    session = RaopSession(lambda _data: None)
    session.configure(
        b"0" * 16,
        b"1" * 16,
        [96, 352, 0, 16, 40, 10, 14, 2, 255, 0, 0, 44100],
    )
    assert isinstance(session.decoder, av.CodecContext)
    assert session.decoder.extradata == alac_cookie(
        [96, 352, 0, 16, 40, 10, 14, 2, 255, 0, 0, 44100]
    )
