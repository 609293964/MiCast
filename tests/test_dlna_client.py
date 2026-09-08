"""DLNA discovery XML parsing, target config, and classify_device."""


from micast.airplay_discovery import classify_device
from micast.config import Settings, SpeakerGroupConfig
from micast.dlna_client import _soap_action

DESCRIPTION = """<?xml version="1.0"?>
<root xmlns="urn:schemas-upnp-org:device-1-0">
  <device>
    <deviceType>urn:schemas-upnp-org:device:MediaRenderer:1</deviceType>
    <friendlyName>客厅电视</friendlyName>
    <modelName>SomeTV</modelName>
    <UDN>uuid:1234abcd-0000-1111-2222-333344445555</UDN>
    <serviceList>
      <service>
        <serviceType>urn:schemas-upnp-org:service:AVTransport:1</serviceType>
        <controlURL>/upnp/control/avtransport</controlURL>
      </service>
    </serviceList>
  </device>
</root>
"""


def test_classify_device():
    assert classify_device("AppleTV2,1", "JMGO-O2 Pro") == "projector"  # name wins
    assert classify_device("AppleTV3,2", "Apple TV") == "tv"
    assert classify_device("AirPort10,115", "客厅音箱") == "speaker"
    assert classify_device("", "索尼电视") == "tv"
    assert classify_device("AudioAccessory5,1", "HomePod") == "speaker"


def test_soap_action_envelope():
    action, body = _soap_action(
        "urn:schemas-upnp-org:service:AVTransport:1", "Play", {"InstanceID": "0", "Speed": "1"}
    )
    assert action == '"urn:schemas-upnp-org:service:AVTransport:1#Play"'
    assert "<u:Play" in body and "<InstanceID>0</InstanceID>" in body


def test_update_group_sanitizes_dlna_targets(monkeypatch):
    monkeypatch.setattr(Settings, "save_to_file", lambda self: None)
    cfg = Settings()
    cfg.groups = [SpeakerGroupConfig(id="g1", name="全屋", speaker_ids=["a", "b"])]
    group = cfg.update_group(
        "g1", dlna_targets=["uuid:abc", " uuid:abc ", "", "uuid:def"]
    )
    assert group.dlna_targets == ["uuid:abc", "uuid:def"]
