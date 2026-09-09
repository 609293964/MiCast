from micast.config import ReceiverConfig, SpeakerGroupConfig, settings
from micast.dlna import DlnaService
from micast.routes.dlna import _dispatch, _scpd


class FakeDeviceManager:
    def __init__(self):
        self.calls = []
        self._owners = {}

    async def play_stream(self, did, url, owner=None, force=False):
        self.calls.append(("play", did, url, owner, force))
        if owner is not None:
            self._owners[did] = owner
        return True

    async def stop(self, did, owner=None):
        self.calls.append(("pause", did, owner))

    async def stop_playback(self, did):
        self.calls.append(("stop", did))

    async def set_volume(self, did, volume):
        self.calls.append(("volume", did, volume))
        return volume

    def owned_targets(self, receiver_id, owner):
        return [
            did for did in settings.receiver_targets(receiver_id) if self._owners.get(did) == owner
        ]


def configure(monkeypatch):
    monkeypatch.setattr(settings, "sender_volume_mode", "linked")
    monkeypatch.setattr(settings, "default_volume_enabled", False)
    group = SpeakerGroupConfig(id="all", name="全屋", speaker_ids=["a", "b"])
    receivers = [
        ReceiverConfig(id="living", name="客厅", target_type="speaker", target_id="a"),
        ReceiverConfig(id="whole", name="全屋", target_type="group", target_id="all"),
    ]
    monkeypatch.setattr(settings, "receivers", receivers)
    monkeypatch.setattr(settings, "groups", [group])
    monkeypatch.setattr(settings, "dlna_enabled", True)
    monkeypatch.setattr(settings, "sync_groups_enabled", True)


def test_dlna_hides_groups_without_deleting_them(monkeypatch):
    configure(monkeypatch)
    service = DlnaService(FakeDeviceManager())

    assert [item.name for item in service.active_receivers()] == ["客厅", "全屋"]

    monkeypatch.setattr(settings, "sync_groups_enabled", False)
    assert [item.name for item in service.active_receivers()] == ["客厅"]
    assert settings.groups[0].name == "全屋"


def test_dlna_service_descriptions_declare_action_arguments_and_state_variables():
    from xml.etree import ElementTree as ET

    namespace = {"upnp": "urn:schemas-upnp-org:service-1-0"}
    transport = ET.fromstring(_scpd("AVTransport"))
    actions = {
        item.findtext("upnp:name", namespaces=namespace): item
        for item in transport.findall("upnp:actionList/upnp:action", namespace)
    }

    set_uri = actions["SetAVTransportURI"]
    arguments = set_uri.findall("upnp:argumentList/upnp:argument", namespace)
    assert [item.findtext("upnp:name", namespaces=namespace) for item in arguments] == [
        "InstanceID",
        "CurrentURI",
        "CurrentURIMetaData",
    ]
    assert all(item.find("upnp:relatedStateVariable", namespace) is not None for item in arguments)
    assert transport.findall("upnp:serviceStateTable/upnp:stateVariable", namespace)


async def test_dlna_routes_group_media_to_every_speaker(monkeypatch):
    configure(monkeypatch)
    manager = FakeDeviceManager()
    service = DlnaService(manager)

    await service.set_uri("whole", "http://media.local/song.mp3")
    await service.play("whole")

    assert manager.calls == [
        ("play", "a", "http://media.local/song.mp3", "dlna:whole", True),
        ("play", "b", "http://media.local/song.mp3", "dlna:whole", True),
    ]
    assert service.state_for("whole").state == "PLAYING"


async def test_dlna_does_not_report_playing_when_every_speaker_rejects(monkeypatch):
    configure(monkeypatch)
    manager = FakeDeviceManager()

    async def reject(*args, **kwargs):
        return False

    manager.play_stream = reject
    service = DlnaService(manager)
    await service.set_uri("living", "http://media.local/song.mp3")

    import pytest

    with pytest.raises(ValueError, match="No speaker accepted"):
        await service.play("living")
    assert service.state_for("living").state == "STOPPED"


async def test_dlna_soap_transport_and_volume(monkeypatch):
    configure(monkeypatch)
    manager = FakeDeviceManager()
    service = DlnaService(manager)
    body = b"<Envelope><CurrentURI>http://media.local/a.mp3</CurrentURI></Envelope>"

    await _dispatch(service, "living", "AVTransport", "SetAVTransportURI", body)
    await _dispatch(service, "living", "AVTransport", "Play", b"")
    info = await _dispatch(service, "living", "AVTransport", "GetTransportInfo", b"")
    await _dispatch(
        service,
        "living",
        "RenderingControl",
        "SetVolume",
        b"<Envelope><DesiredVolume>63</DesiredVolume></Envelope>",
    )

    assert info["CurrentTransportState"] == "PLAYING"
    assert ("volume", "a", 63) in manager.calls
    assert service.state_for("living").volume == 63


async def test_dlna_accepts_next_track_uri(monkeypatch):
    configure(monkeypatch)
    service = DlnaService(FakeDeviceManager())
    body = b"<Envelope><NextURI>http://media.local/next.mp3</NextURI></Envelope>"

    await _dispatch(service, "living", "AVTransport", "SetNextAVTransportURI", body)

    assert service.state_for("living").next_uri == "http://media.local/next.mp3"


def test_dlna_uses_stable_unique_device_ids(monkeypatch):
    configure(monkeypatch)
    service = DlnaService(FakeDeviceManager())

    assert service.uuid_for("living") == service.uuid_for("living")
    assert service.uuid_for("living") != service.uuid_for("whole")
