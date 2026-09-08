from micast.routes.devices import _clean_name


def test_verified_xiaomi_speaker_names():
    assert _clean_name("旧名称", "LX06") == "小爱音箱 Pro"
    assert _clean_name("旧名称", "OH2P") == "Xiaomi 智能音箱 Pro"
    assert _clean_name("旧名称", "OH2") == "Xiaomi 智能音箱"
