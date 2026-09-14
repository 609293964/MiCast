"""Stream plan fingerprints: what audio should be running, per entry point.

Pure functions over ``Settings`` — no I/O, no asyncio — so computing and
diffing plans is cheap, deterministic, and unit-testable. ``AudioBridge``
snapshots the plan after every successful (re)start and folds every config
mutation through ``apply_config_change()``: recompute → diff → dispatch only
the affected rebuilds. This replaces the per-route hand-rolled "what do I
restart now" dispatch that let running pipelines drift from saved config.

Fingerprint scope per entry (classic receiver or AirPlay 2 instance):
entry identity (name/target), the resolved group (mode/anchor/members with
channel+EQ+gain), the stream-variant plan, global audio format, and the
external AirPlay/DLNA targets WITH their delays — external AirPlay delay is a
connect-time pre-buffer, so it must diff as a rebuild trigger, unlike Xiaomi
holds which the stream server re-reads live per chunk (``member_delays`` is
tracked only to classify delay-only edits).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from micast.config import Settings

EntryFingerprint = dict[str, Any]
PlanSnapshot = dict[str, Any]


def entry_fingerprint(s: Settings, entry_id: str) -> EntryFingerprint | None:
    """Fingerprint one entry (classic receiver or AirPlay 2 instance).

    Returns None when the id matches neither a receiver nor an instance.
    Disabled entries are excluded by ``compute_plan`` before calling this.
    """
    receiver = next((item for item in s.receivers if item.id == entry_id), None)
    instance = next((item for item in s.airplay2_instances if item.id == entry_id), None)
    if receiver is not None:
        kind = "receiver"
        name = receiver.name
        target = {
            "type": receiver.target_type,
            # Resolve "selected" so re-selecting a speaker diffs this entry.
            "id": receiver.target_id
            or (s.selected_device_id if receiver.target_type == "selected" else None),
        }
    elif instance is not None:
        kind = "airplay2"
        name = instance.name
        target = {"type": instance.target_type, "id": instance.target_id}
    else:
        return None
    group = s.group_for_receiver(entry_id)
    return {
        "kind": kind,
        "name": name,
        "target": target,
        "group": (
            {
                "id": group.id,
                "mode": group.mode,
                "anchor": group.anchor_did,
                "speakers": [
                    {
                        "did": did,
                        "channel": s.receiver_channel(entry_id, did),
                        "eq": s.speaker_eq_curve(did),
                        "loudness": s.speaker_loudness(did),
                        "gain_db": group.gains_db.get(did, 0.0),
                    }
                    for did in sorted(group.speaker_ids)
                ],
            }
            if group
            else None
        ),
        # Order-normalized: membership reordering must not diff.
        "variants": sorted(
            s.receiver_stream_variants(entry_id),
            key=lambda v: (v["suffix"], v["base"], v["channel"] or "", str(v["eq"]), v["loudness"]),
        ),
        "audio": s.audio.model_dump(),
        "external_airplay": dict(sorted(s.receiver_airplay_delays(entry_id).items())),
        "external_dlna": sorted(s.receiver_dlna_targets(entry_id)),
        "network_channels": dict(sorted(s.receiver_network_channels(entry_id).items())),
        "member_delays": (
            {did: group.delay_holds().get(did, 0) for did in sorted(group.speaker_ids)}
            if group
            else {}
        ),
    }


def compute_plan(s: Settings) -> PlanSnapshot:
    """Fingerprint every entry that should be running right now."""
    entries: dict[str, EntryFingerprint] = {}
    if s.airplay_engine == "local":
        for receiver in s.active_receivers():
            fingerprint = entry_fingerprint(s, receiver.id)
            if fingerprint is not None:
                entries[receiver.id] = fingerprint
    if s.airplay2_enabled:
        for instance in s.airplay2_instances:
            if not instance.enabled:
                continue
            fingerprint = entry_fingerprint(s, instance.id)
            if fingerprint is not None:
                entries[instance.id] = fingerprint
    return {
        "engine": {
            "airplay_engine": s.airplay_engine,
            "receiver_mode": s.receiver_mode,
            "sync_groups_enabled": s.sync_groups_enabled,
        },
        "entries": entries,
    }


@dataclass
class PlanDiff:
    """The classified difference between two plan snapshots."""

    full_restart_required: bool = False
    classic_added_removed: bool = False
    classic_rebuild: bool = False
    audio_only: bool = False
    airplay2_added: set[str] = field(default_factory=set)
    airplay2_removed: set[str] = field(default_factory=set)
    airplay2_rebuild: set[str] = field(default_factory=set)
    external_airplay_changed: set[str] = field(default_factory=set)
    external_dlna_changed: set[str] = field(default_factory=set)
    # group_id -> removed Xiaomi dids (added members replay via the same hook)
    membership_changed: dict[str, list[str]] = field(default_factory=dict)
    delay_only: bool = False

    @property
    def noop(self) -> bool:
        return not any(
            (
                self.full_restart_required,
                self.classic_added_removed,
                self.classic_rebuild,
                self.audio_only,
                self.airplay2_added,
                self.airplay2_removed,
                self.airplay2_rebuild,
                self.external_airplay_changed,
                self.external_dlna_changed,
                self.membership_changed,
                self.delay_only,
            )
        )


def _group_structure(group: dict[str, Any] | None) -> dict[str, Any] | None:
    """Group fields whose change rebuilds pipelines.

    Anchor is excluded: re-anchoring shifts the delay reference frame but
    keeps physical timing (update_group re-anchors offsets), and holds are
    applied live. Mode/membership/channel/EQ/gain all reshape streams.
    """
    if group is None:
        return None
    return {"id": group["id"], "mode": group["mode"], "speakers": group["speakers"]}


def diff_plans(old: PlanSnapshot | None, new: PlanSnapshot) -> PlanDiff:
    diff = PlanDiff()
    if old is None or old.get("engine") != new["engine"]:
        diff.full_restart_required = True
        return diff

    old_entries: dict[str, EntryFingerprint] = old["entries"]
    new_entries: dict[str, EntryFingerprint] = new["entries"]

    for entry_id in sorted(old_entries.keys() - new_entries.keys()):
        if old_entries[entry_id]["kind"] == "airplay2":
            diff.airplay2_removed.add(entry_id)
        else:
            diff.classic_added_removed = True
    for entry_id in sorted(new_entries.keys() - old_entries.keys()):
        if new_entries[entry_id]["kind"] == "airplay2":
            diff.airplay2_added.add(entry_id)
        else:
            diff.classic_added_removed = True

    delays_moved = False
    for entry_id in sorted(old_entries.keys() & new_entries.keys()):
        o, n = old_entries[entry_id], new_entries[entry_id]
        og, ng = o["group"], n["group"]
        if og and ng and og["id"] == ng["id"]:
            old_dids = {speaker["did"] for speaker in og["speakers"]}
            new_dids = {speaker["did"] for speaker in ng["speakers"]}
            if old_dids != new_dids:
                diff.membership_changed[ng["id"]] = sorted(old_dids - new_dids)

        hard = (
            o["target"] != n["target"]
            or o["variants"] != n["variants"]
            or _group_structure(og) != _group_structure(ng)
            or (og is None) != (ng is None)
        )
        audio = o["audio"] != n["audio"]
        if o["name"] != n["name"]:
            # The name is broadcast by the discovery layer itself (RAOP mDNS /
            # orchestrator), not the pipelines — a rename re-publishes the entry.
            if n["kind"] == "receiver":
                diff.classic_added_removed = True
            else:
                diff.airplay2_rebuild.add(entry_id)
        if n["kind"] == "receiver":
            if hard:
                diff.classic_rebuild = True
            elif audio:
                diff.audio_only = True
        elif hard or audio:
            diff.airplay2_rebuild.add(entry_id)
        if (
            o["external_airplay"] != n["external_airplay"]
            or o["network_channels"] != n["network_channels"]
        ):
            diff.external_airplay_changed.add(entry_id)
        if (
            o["external_dlna"] != n["external_dlna"]
            or o["network_channels"] != n["network_channels"]
        ):
            diff.external_dlna_changed.add(entry_id)
        delays_moved = delays_moved or o["member_delays"] != n["member_delays"]

    # A pure delay edit is hot-applied (Xiaomi holds are re-read per chunk);
    # external-AirPlay hold changes already surfaced as external_*_changed.
    diff.delay_only = (
        delays_moved
        and not diff.classic_added_removed
        and not diff.classic_rebuild
        and not diff.audio_only
        and not diff.airplay2_added
        and not diff.airplay2_removed
        and not diff.airplay2_rebuild
        and not diff.external_airplay_changed
        and not diff.external_dlna_changed
        and not diff.membership_changed
    )
    return diff
