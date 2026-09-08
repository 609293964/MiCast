"""AirPlay discovery parsing and group airplay_targets config."""

import pytest

from micast.airplay_discovery import _device_from_info, _service_name_to_id
from micast.config import ReceiverConfig, Settings, SpeakerGroupConfig


@pytest.fixture()
def cfg(monkeypatch):
    monkeypatch.setattr(Settings, "save_to_file", lambda self: None)
    return Settings()


class FakeInfo:
    def __init__(self, port=7000, addresses=("192.168.1.50",), properties=None):
        self.port = port
        self._addresses = list(addresses)
        self.properties = properties or {}

    def parsed_addresses(self):
        return self._addresses


def test_service_name_parsing():
    assert _service_name_to_id("AABBCCDDEEFF@客厅 Soundbar._raop._tcp.local.") == "aabbccddeeff"
    assert _service_name_to_id("001122334455@Amp._raop._tcp.local.") == "001122334455"
    # Non-Apple name: stable 12-hex fallback
    fallback = _service_name_to_id("Weird Name._raop._tcp.local.")
    assert fallback and len(fallback) == 12
    assert _service_name_to_id("unrelated._airplay._tcp.local.") is None


def test_device_from_info_extracts_fields():
    info = FakeInfo(properties={b"am": b"SoundbarX,1", b"pw": b"false"})
    device = _device_from_info("AABBCCDDEEFF@客厅 Soundbar._raop._tcp.local.", info)
    assert device.id == "aabbccddeeff"
    assert device.name == "客厅 Soundbar"
    assert device.host == "192.168.1.50"
    assert device.port == 7000
    assert device.model == "SoundbarX,1"
    assert device.needs_password is False

    locked = _device_from_info(
        "AABBCCDDEEFF@Locked._raop._tcp.local.", FakeInfo(properties={b"pw": b"true"})
    )
    assert locked.needs_password is True


def test_update_group_sanitizes_airplay_targets(cfg):
    cfg.groups = [SpeakerGroupConfig(id="g1", name="全屋", speaker_ids=["a", "b"])]
    group = cfg.update_group(
        "g1",
        airplay_targets=["AABBCCDDEEFF", "aabbccddeeff", "not-a-mac", "001122334455"],
    )
    assert group.airplay_targets == ["aabbccddeeff", "001122334455"]


def test_receiver_airplay_targets(cfg):
    cfg.groups = [
        SpeakerGroupConfig(id="g1", name="全屋", speaker_ids=["a", "b"], airplay_targets=["aabbccddeeff"])
    ]
    cfg.receivers = [ReceiverConfig(id="r1", name="全屋", target_type="group", target_id="g1")]
    assert cfg.receiver_airplay_targets("r1") == ["aabbccddeeff"]
    assert cfg.receiver_airplay_targets("unknown") == []
