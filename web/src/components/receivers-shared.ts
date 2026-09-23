import type { State } from "../state";
import type { SpeakerGroup } from "../api";

// Shared helpers for every receivers-view submodule. Nothing here may import
// the view or the other submodules — dependency direction is one-way.

export function escapeHtml(text: string): string {
  return text
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
}

export function message(error: unknown): string {
  return error instanceof Error ? error.message : "未知错误";
}

export const KIND_ICON: Record<string, "loudspeaker" | "tv" | "projector"> = {
  speaker: "loudspeaker",
  tv: "tv",
  projector: "projector",
};

// Delay slider range: the settings page can unlock a wider range.
export const NORMAL_DELAY_LIMIT_MS = 5000;
export const LARGE_DELAY_LIMIT_MS = 15000;
let largeDelayEnabled = false;

export function setLargeDelayEnabled(enabled: boolean): void {
  largeDelayEnabled = enabled;
}

export function delayLimit(): number {
  return largeDelayEnabled ? LARGE_DELAY_LIMIT_MS : NORMAL_DELAY_LIMIT_MS;
}

// Async group changes survive a full rerender, so controls can acknowledge the
// click immediately and keep showing an honest intermediate state.
export const pendingGroupActions = new Set<string>();

export const groupActionKey = (kind: "mode" | "channel" | "anchor", groupId: string, speakerId = "") =>
  `${kind}:${groupId}:${speakerId}`;

export function groupMemberCount(group: SpeakerGroup): number {
  return (
    group.speaker_ids.length +
    (group.airplay_targets?.length ?? 0) +
    (group.dlna_targets?.length ?? 0)
  );
}

export function replaceGroup(config: NonNullable<State["fullConfig"]>, updated: SpeakerGroup) {
  return { ...config, groups: config.groups.map((item) => item.id === updated.id ? updated : item) };
}

/** Change only the timing coordinate system. Normalized playback holds stay identical. */
export function rebaseGroup(group: SpeakerGroup, anchorDid: string): SpeakerGroup {
  const members = [
    ...group.speaker_ids,
    ...(group.airplay_targets ?? []),
    ...(group.dlna_targets ?? []),
  ];
  const offsets = Object.fromEntries(members.map((did) => [did, group.delays_ms?.[did] ?? 0]));
  const shift = offsets[anchorDid] ?? 0;
  return {
    ...group,
    anchor_did: anchorDid,
    delays_ms: Object.fromEntries(
      members
        .filter((did) => did !== anchorDid)
        .map((did) => [did, offsets[did] - shift])
        .filter(([, value]) => value !== 0)
    ),
  };
}

export function speakerRemoveButton(groupId: string, did: string, name: string, canRemove: boolean): string {
  return canRemove
    ? `<button class="button plain danger-text net-remove" type="button" data-speaker-remove="${escapeHtml(did)}" data-group-id="${escapeHtml(groupId)}" aria-label="从组合移除 ${escapeHtml(name)}">移除</button>`
    : "";
}

/** 离线/播放失败 hint for a Xiaomi speaker row; empty string when healthy. */
export function speakerHealth(did: string, state: State): string {
  const device = state.devices.find((item) => item.did === did);
  if (device?.play_error) return "播放失败，重试中";
  if (device?.presence === "offline") return "离线";
  return "";
}

/** Speaker display name: live device list first, then persisted aliases —
 * the latter is available before the device list finishes loading. */
export function speakerName(did: string, state: State): string {
  const device = state.devices.find((item) => item.did === did);
  return device?.alias || device?.name || state.fullConfig?.speaker_names?.[did] || did;
}

export function targetLabel(receiverId: string, state: State): string {
  const definition = state.fullConfig?.receivers.find((item) => item.id === receiverId);
  if (!definition) return "尚未指定播放目标";
  if (definition.target_type === "selected") {
    const selected = state.fullConfig?.selected_device_id;
    const name = selected ? speakerName(selected, state) : "";
    return name && name !== selected ? name : "尚未指定播放目标";
  }
  if (definition.target_type === "group") {
    const group = state.fullConfig?.groups.find((item) => item.id === definition.target_id);
    return `组合 · ${group?.name || "未知分组"}`;
  }
  return definition.target_id ? speakerName(definition.target_id, state) : "未知音箱";
}

export function settingsTargetIds(
  definition: NonNullable<State["fullConfig"]>["receivers"][number],
  state: State
): string[] {
  if (definition.target_type === "selected") {
    return state.fullConfig?.selected_device_id ? [state.fullConfig.selected_device_id] : [];
  }
  return definition.target_id ? [definition.target_id] : [];
}

export function compactTargetLabel(receiverName: string, receiverId: string, state: State): string {
  const label = targetLabel(receiverId, state);
  const targetName = label.replace(/^播放到(?:分组)?：/, "");
  return targetName === receiverName ? "" : label;
}
