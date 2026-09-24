"""Stream-plan fingerprints: diff classification for every mutation kind.

These tests pin the mechanism that replaced per-route hand-rolled rebuild
dispatch — each regression maps to a "config changed but the running pipeline
didn't" defect found on fnOS.
"""

import pytest

from micast.config import (
    AirPlay2InstanceConfig,
    EqPoint,
    ReceiverConfig,
    Settings,
    SpeakerConfig,
    SpeakerGroupConfig,
)
from micast.stream_plan import compute_plan, diff_plans, entry_fingerprint


@pytest.fixture(autouse=True)
def _no_persistence(monkeypatch):
    """Never touch the real config file from tests."""
    monkeypatch.setattr(Settings, "save_to_file", lambda self: None)


def _settings() -> Settings:
    s = Settings()
    s.airplay_engine = "local"
    s.sync_groups_enabled = True
    s.airplay2_enabled = True
    s.groups = [SpeakerGroupConfig(id="g1", name="全屋", speaker_ids=["a", "b"], anchor_did="a")]
    s.receivers = [
        ReceiverConfig(id="r1", name="全屋", target_type="group", target_id="g1", enabled=True),
    ]
    s.airplay2_instances = [
        AirPlay2InstanceConfig(id="ap2", name="客厅 AP2", target_type="group", target_id="g1")
    ]
    return s


def test_fingerprint_is_stable_and_order_insensitive():
    s = _settings()
    first = compute_plan(s)
    second = compute_plan(s)
    assert first == second
    assert diff_plans(first, second).noop
    # Membership ORDER is cosmetic: fingerprints sort members by did.
    s.groups[0].speaker_ids = ["b", "a"]
    reordered = compute_plan(s)
    assert diff_plans(first, reordered).noop


def test_no_change_is_a_noop():
    s = _settings()
    plan = compute_plan(s)
    assert diff_plans(plan, compute_plan(s)).noop


def test_airplay2_retarget_and_rename_rebuild_despite_same_suffix_set():
    """Defect 1: the old reuse check compared only {id}{suffix} sets."""
    s = _settings()
    old = compute_plan(s)
    s.airplay2_instances[0].target_type = "speaker"
    s.airplay2_instances[0].target_id = "a"
    diff = diff_plans(old, compute_plan(s))
    assert diff.airplay2_rebuild == {"ap2"}

    s = _settings()
    old = compute_plan(s)
    s.airplay2_instances[0].name = "新名字"
    diff = diff_plans(old, compute_plan(s))
    assert diff.airplay2_rebuild == {"ap2"}


def test_audio_format_change_restarts_encoders_on_both_engines():
    """A format swap is encoder-level: pipelines, PCM sources (a live AirPlay 2
    session!) and stream endpoints all stay up."""
    s = _settings()
    old = compute_plan(s)
    s.audio.format = "flac"
    diff = diff_plans(old, compute_plan(s))
    assert diff.audio_only
    assert not diff.classic_rebuild
    assert not diff.airplay2_rebuild
    assert diff.encoder_restart == {"r1", "ap2"}


def test_eq_curve_change_restarts_encoders_without_rebuilding():
    """Defect: retuning a curve mid-cast used to rebuild the AirPlay 2
    instance, killing shairport and the phone's session."""
    s = _settings()
    s.speakers = [
        SpeakerConfig(
            did="a",
            alias="A",
            eq_enabled=True,
            eq_points=[EqPoint(freq=100.0, gain_db=2.0)],
        )
    ]
    old = compute_plan(s)
    s.speakers[0].eq_points = [EqPoint(freq=100.0, gain_db=4.0)]
    diff = diff_plans(old, compute_plan(s))
    assert not diff.classic_rebuild
    assert not diff.airplay2_rebuild
    assert not diff.audio_only
    assert diff.encoder_restart == {"r1", "ap2"}

    # Flat ↔ EQ'd toggles the -q{n} suffix set: that IS structural.
    s2 = _settings()
    old = compute_plan(s2)
    s2.speakers = [
        SpeakerConfig(
            did="a",
            alias="A",
            eq_enabled=True,
            eq_points=[EqPoint(freq=100.0, gain_db=2.0)],
        )
    ]
    diff = diff_plans(old, compute_plan(s2))
    assert diff.classic_rebuild
    assert diff.airplay2_rebuild == {"ap2"}


def test_remove_group_refused_while_airplay2_references_it():
    """Defect 3: dangling AirPlay 2 mapping after group deletion."""
    s = _settings()
    refs = s.target_references("group", "g1")
    assert {ref["kind"] for ref in refs} == {"receiver", "airplay2"}
    assert not s.remove_group("g1")
    # With only the classic receiver gone the airplay2 reference still blocks.
    s.receivers = []
    assert not s.remove_group("g1")
    s.airplay2_instances = []
    assert s.remove_group("g1")


def test_external_airplay_delay_change_reconnects_every_entry_of_the_group():
    """Defect 4: external AirPlay delay is a connect-time pre-buffer — it must
    diff as a target change for BOTH the classic receiver and the AirPlay 2
    instance mapped to the same group."""
    s = _settings()
    s.groups[0].airplay_targets = ["apdev"]
    s.groups[0].delays_ms = {"apdev": 300}
    old = compute_plan(s)
    s.groups[0].delays_ms = {"apdev": 600}
    diff = diff_plans(old, compute_plan(s))
    assert diff.external_airplay_changed == {"r1", "ap2"}
    assert not diff.delay_only
    assert not diff.classic_rebuild
    assert not diff.airplay2_rebuild


def test_xiaomi_only_delay_change_is_live_applied():
    s = _settings()
    s.groups[0].delays_ms = {"b": 500}
    old = compute_plan(s)
    s.groups[0].delays_ms = {"b": 900}
    diff = diff_plans(old, compute_plan(s))
    assert diff.delay_only
    assert diff.noop is False
    assert not diff.classic_rebuild
    assert not diff.airplay2_rebuild
    assert not diff.external_airplay_changed


def test_membership_change_reports_removed_and_skips_rebuild_when_variants_hold():
    s = _settings()
    old = compute_plan(s)
    s.groups[0].speaker_ids = ["a"]  # remove b (no EQ → variant plan unchanged)
    diff = diff_plans(old, compute_plan(s))
    assert diff.membership_changed == {"g1": ["b"]}
    # Speakers carry channel/eq/gain in the fingerprint, so a membership edit
    # does alter the group structure: a rebuild is expected.
    assert diff.classic_rebuild
    assert diff.airplay2_rebuild == {"ap2"}

    s = _settings()
    old = compute_plan(s)
    s.groups[0].speaker_ids = ["a", "b", "c"]
    diff = diff_plans(old, compute_plan(s))
    assert diff.membership_changed == {"g1": []}


def test_stereo_flip_rebuilds_both_engines():
    s = _settings()
    old = compute_plan(s)
    s.groups[0].mode = "stereo"
    s.groups[0].channels = {"a": "left", "b": "right"}
    diff = diff_plans(old, compute_plan(s))
    assert diff.classic_rebuild
    assert diff.airplay2_rebuild == {"ap2"}


def test_receiver_rename_republishes_the_entry():
    """The name lives in the mDNS advertisement, not the pipelines."""
    s = _settings()
    old = compute_plan(s)
    s.receivers[0].name = "新名字"
    diff = diff_plans(old, compute_plan(s))
    assert diff.classic_added_removed
    assert not diff.classic_rebuild


def test_engine_and_first_snapshot_require_full_restart():
    s = _settings()
    assert diff_plans(None, compute_plan(s)).full_restart_required
    old = compute_plan(s)
    s.airplay_engine = "airplay2"
    assert diff_plans(old, compute_plan(s)).full_restart_required


def test_disabled_entries_drop_out_of_the_plan():
    s = _settings()
    old = compute_plan(s)
    s.airplay2_instances[0].enabled = False
    diff = diff_plans(old, compute_plan(s))
    assert diff.airplay2_removed == {"ap2"}
    s2 = _settings()
    s2.receivers[0].enabled = False
    diff = diff_plans(old, compute_plan(s2))
    assert diff.classic_added_removed
    assert not diff.airplay2_removed  # ap2 is still enabled in s2


def test_entry_fingerprint_unknown_id_is_none():
    assert entry_fingerprint(_settings(), "nope") is None


def test_fingerprint_stable_with_full_tuning_surface():
    """Regression: mid-playback rebuilds on fnOS 0.3.0 were suspected to come
    from nondeterministic fingerprints. Pin stability across repeated computes
    with EQ, night mode, loudness, stereo channels, gains, delays, and
    external network targets all live."""
    s = _settings()
    group = s.groups[0]
    group.mode = "stereo"
    group.anchor_did = "a"
    group.delays_ms = {"a": 0, "b": 1800}
    group.channels = {"a": "left", "b": "right"}
    group.gains_db = {"a": -1.5, "b": 2.0}
    group.airplay_targets = ["aa1122334455"]
    group.dlna_targets = ["uuid:renderer-1"]
    group.network_channels = {"aa1122334455": "right"}
    s.speakers = [
        SpeakerConfig(
            did="a",
            eq_enabled=True,
            eq_points=[EqPoint(freq=100, gain_db=2.0), EqPoint(freq=1000, gain_db=-1.0)],
            night_mode=True,
            loudness_comp_enabled=True,
        ),
        SpeakerConfig(did="b", eq_enabled=True, eq_points=[EqPoint(freq=200, gain_db=1.5)]),
    ]
    first = compute_plan(s)
    for _ in range(5):
        assert compute_plan(s) == first
    assert diff_plans(first, compute_plan(s)).noop
    # Sanity: the tuning surface really is represented (so the test isn't
    # vacuously comparing empty fingerprints).
    receiver_fp = first["entries"]["r1"]
    assert receiver_fp["group"]["speakers"][0]["eq"] is not None
    assert {v["suffix"] for v in receiver_fp["variants"]} == {"", "-L-q1", "-R", "-R-q1"}
