"""Speaker ownership filtering and the DLNA owner namespace."""

from types import SimpleNamespace

from micast.config import ReceiverConfig, SpeakerGroupConfig, settings
from micast.dlna import DlnaService
from micast.xiaomi.device_manager import DeviceManager


def test_owned_targets_filters_by_exact_owner(monkeypatch):
    monkeypatch.setattr(
        settings,
        "receivers",
        [ReceiverConfig(id="r1", name="组播", target_type="group", target_id="g1")],
    )
    monkeypatch.setattr(
        settings, "groups", [SpeakerGroupConfig(id="g1", name="组播", speaker_ids=["a", "b", "c"])]
    )
    dm = DeviceManager(auth=None)
    dm._owners = {"a": "r1", "b": "dlna:r1", "c": None}
    assert dm.owned_targets("r1", "r1") == ["a"]
    assert dm.owned_targets("r1", "dlna:r1") == ["b"]
    assert dm.owned_targets("r1", "nobody") == []


def test_dlna_owner_namespaces_dlna_ingress():
    service = DlnaService(SimpleNamespace())
    assert service._owner("r1") == "dlna:r1"
