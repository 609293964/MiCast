from micast.xiaomi.device_manager import _find_play_status, _find_volume


def test_find_volume_in_common_status_shapes():
    assert _find_volume({"volume": 37}) == 37
    assert _find_volume({"data": {"volume_level": "62"}}) == 62
    assert _find_volume('{"result":{"volumeLevel":81}}') == 81


def test_find_volume_ignores_unrelated_numbers():
    assert _find_volume({"status": 1, "position": 42}) is None


def test_find_play_status_in_mina_info_payload():
    payload = {
        "code": 0,
        "data": {"code": 0, "info": '{"status":2,"volume":50,"loop_type":1}'},
    }
    assert _find_play_status(payload) == 2


def test_find_play_status_ignores_proxy_code():
    assert _find_play_status({"code": 0, "message": "ok"}) is None
