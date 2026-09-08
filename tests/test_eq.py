"""Per-speaker EQ: config signatures, stream variants, filter chain, topology."""

import pytest
from test_topology import FakeBridge, FakeDeviceManager, _latency, _raop_diag

from micast import topology
from micast.config import ReceiverConfig, Settings, SpeakerConfig, SpeakerGroupConfig
from micast.speaker_pipeline import SpeakerPipeline


@pytest.fixture()
def cfg(monkeypatch):
    monkeypatch.setattr(Settings, "save_to_file", lambda self: None)
    return Settings()


def test_eq_bands_none_when_disabled_or_flat(cfg):
    cfg.speakers = [SpeakerConfig(did="didA")]
    assert cfg.speaker_eq_bands("didA") is None

    cfg.set_speaker_eq("didA", enabled=True, bands=[0] * 10)
    assert cfg.speaker_eq_bands("didA") is None  # flat EQ keeps the base stream

    cfg.set_speaker_eq("didA", enabled=True, bands=[3] + [0] * 9)
    assert cfg.speaker_eq_bands("didA") == [3] + [0] * 9

    cfg.set_speaker_eq("didA", enabled=False, bands=[3] + [0] * 9)
    assert cfg.speaker_eq_bands("didA") is None


def test_eq_bands_are_clamped_and_padded(cfg):
    speaker = cfg.set_speaker_eq("didA", enabled=True, bands=[99, -99, 1])
    assert speaker.eq_bands == [12.0, -12.0, 1.0] + [0.0] * 7


def test_legacy_five_band_config_migrates_to_nearest_iso_band(cfg):
    """Old 60/250/1k/4k/12k gains land on 62/250/1k/4k/16k, not the first five."""
    speaker = SpeakerConfig(did="didA", eq_enabled=True, eq_bands=[3, 1, -2, 2, -4])
    assert speaker.eq_bands == [0.0, 3.0, 0.0, 1.0, 0.0, -2.0, 0.0, 2.0, 0.0, -4.0]
    # The API path migrates the same way for a stale client posting 5 values.
    updated = cfg.set_speaker_eq("didB", enabled=True, bands=[3, 1, -2, 2, -4])
    assert updated.eq_bands == [0.0, 3.0, 0.0, 1.0, 0.0, -2.0, 0.0, 2.0, 0.0, -4.0]


def test_mirror_variants_split_by_eq_signature(cfg):
    cfg.speakers = [SpeakerConfig(did="didA"), SpeakerConfig(did="didB")]
    cfg.receivers = [ReceiverConfig(id="r1", name="全屋", target_type="group", target_id="g1")]
    cfg.groups = [SpeakerGroupConfig(id="g1", name="全屋", speaker_ids=["didA", "didB"])]

    # No EQ anywhere: a single base stream.
    variants = cfg.receiver_stream_variants("r1")
    assert [v["suffix"] for v in variants] == [""]

    # One speaker with EQ: base stream + one split stream.
    cfg.set_speaker_eq("didB", enabled=True, bands=[4, 2] + [0] * 8, preset="bass")
    variants = cfg.receiver_stream_variants("r1")
    assert [v["suffix"] for v in variants] == ["", "-q1"]
    assert cfg.stream_suffix("r1", "didA") == ""
    assert cfg.stream_suffix("r1", "didB") == "-q1"

    # Same EQ on both: one shared split stream; the base stream still exists
    # (kept as a cheap raw bypass for DLNA/network targets even when no
    # speaker uses it).
    cfg.set_speaker_eq("didA", enabled=True, bands=[4, 2] + [0] * 8, preset="bass")
    variants = cfg.receiver_stream_variants("r1")
    assert [v["suffix"] for v in variants] == ["-q1", ""]
    assert cfg.stream_suffix("r1", "didA") == "-q1"

    # Different EQs: two split streams in order of first appearance.
    cfg.set_speaker_eq("didA", enabled=True, bands=[0, 0, 3] + [0] * 7, preset="vocal")
    variants = cfg.receiver_stream_variants("r1")
    assert [v["suffix"] for v in variants] == ["-q1", "-q2", ""]
    assert cfg.stream_suffix("r1", "didA") == "-q1"  # didA appears first
    assert cfg.stream_suffix("r1", "didB") == "-q2"


def test_stereo_variants_combine_channel_and_eq(cfg):
    cfg.speakers = [SpeakerConfig(did="didA"), SpeakerConfig(did="didB")]
    cfg.receivers = [ReceiverConfig(id="r1", name="立体声", target_type="group", target_id="g1")]
    cfg.groups = [
        SpeakerGroupConfig(
            id="g1", name="立体声", speaker_ids=["didA", "didB"], mode="stereo",
            channels={"didA": "left", "didB": "right"},
        )
    ]
    variants = cfg.receiver_stream_variants("r1")
    assert [v["suffix"] for v in variants] == ["-L", "-R", ""]

    cfg.set_speaker_eq("didA", enabled=True, bands=[5] + [0] * 9)
    variants = cfg.receiver_stream_variants("r1")
    assert [v["suffix"] for v in variants] == ["-L-q1", "-R", ""]
    assert cfg.stream_suffix("r1", "didA") == "-L-q1"
    assert cfg.stream_suffix("r1", "didB") == "-R"


def test_pipeline_filter_includes_equalizer(cfg, monkeypatch):
    monkeypatch.setattr("micast.speaker_pipeline.settings", cfg)
    pipeline = SpeakerPipeline(
        device_id="r1", alias="t", pcm_source=None, stream_server=None,
        eq_bands=[3, 0, -2] + [0] * 7,
    )
    filt = pipeline._build_audio_filter()
    assert "equalizer=f=31:t=q:w=1.0:g=3" in filt
    assert "equalizer=f=125:t=q:w=1.0:g=-2" in filt
    assert "f=62" not in filt  # zero-gain bands are skipped

    flat = SpeakerPipeline(
        device_id="r1", alias="t", pcm_source=None, stream_server=None,
        eq_bands=[0] * 10,
    )
    assert flat._build_audio_filter() is None


def test_topology_shows_eq_stream_variant(cfg, monkeypatch):
    monkeypatch.setattr(topology, "settings", cfg)
    cfg.speakers = [SpeakerConfig(did="didA"), SpeakerConfig(did="didB")]
    cfg.receivers = [ReceiverConfig(id="r1", name="全屋", target_type="group", target_id="g1")]
    cfg.groups = [SpeakerGroupConfig(id="g1", name="全屋", speaker_ids=["didA", "didB"])]
    cfg.set_speaker_eq("didB", enabled=True, bands=[4, 2] + [0] * 8, preset="bass")

    bridge = FakeBridge(
        {
            "raop": _raop_diag(),
            "streams": {
                "r1": {"clients": 1, "flowing": True, "bytes_sent": 0, "dropped_chunks": 0, "latency": _latency()},
                "r1-q1": {"clients": 1, "flowing": True, "bytes_sent": 0, "dropped_chunks": 0, "latency": _latency()},
            },
        }
    )
    snap = topology.build_topology(bridge, FakeDeviceManager(playing=["didA", "didB"]))

    stream_ids = {n["id"] for n in snap["nodes"] if n["kind"] == "stream"}
    assert stream_ids == {"stream:r1", "stream:r1-q1"}
    eq_node = next(n for n in snap["nodes"] if n["id"] == "stream:r1-q1")
    assert eq_node["eq"] is True

    pull = [e for e in snap["edges"] if e.get("direction") == "pull"]
    routes = {(e["from"], e["to"]) for e in pull}
    assert routes == {("stream:r1", "spk:didA"), ("stream:r1-q1", "spk:didB")}


def test_split_stream_id_with_eq_suffix():
    assert topology._split_stream_id("r1") == ("r1", None)
    assert topology._split_stream_id("r1-L") == ("r1", "left")
    assert topology._split_stream_id("r1-q1") == ("r1", None)
    assert topology._split_stream_id("r1-R-q2") == ("r1", "right")
