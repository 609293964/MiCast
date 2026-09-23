import type { State } from "../state";
import type { SpeakerGroup } from "../api";
import { brandIcon } from "../icons";
import {
  delayLimit,
  escapeHtml,
  groupActionKey,
  groupMemberCount,
  pendingGroupActions,
  speakerHealth,
  speakerName,
  speakerRemoveButton,
} from "./receivers-shared";
import { findNetworkDevice } from "./network-targets";

// Group card expansion: an explicit user toggle always wins; the default is
// "expanded while the group is actively playing".
const groupExpansion = new Map<string, boolean>();

// The "+ 添加音箱" picker is a plain <div hidden> toggle — a poll-driven
// re-render rebuilds it closed, so the open state lives here and is rendered
// back into the markup.
const speakerPickerOpen = new Set<string>();

export function isGroupExpanded(group: SpeakerGroup, state: State): boolean {
  const explicit = groupExpansion.get(group.id);
  if (explicit !== undefined) return explicit;
  const speakerPlaying = group.speaker_ids.some(
    (did) => state.devices.find((item) => item.did === did)?.playing
  );
  const netPlaying = [...(group.airplay_targets ?? []), ...(group.dlna_targets ?? [])].some(
    (id) => {
      const status = findNetworkDevice(id)?.device.stream_status;
      return status === "streaming" || status === "playing";
    }
  );
  return speakerPlaying || netPlaying;
}

export function bindGroupToggles(container: HTMLElement) {
  // Collapsible group cards: explicit toggles are remembered across the
  // poll-driven re-renders (groupExpansion map).
  container.querySelectorAll<HTMLElement>("[data-group-toggle]").forEach((button) => {
    button.addEventListener("click", () => {
      const groupId = button.dataset.groupToggle!;
      const body = container.querySelector<HTMLElement>(`[data-group-body="${groupId}"]`);
      if (!body) return;
      const expand = body.hidden;
      groupExpansion.set(groupId, expand);
      body.hidden = !expand;
      button.classList.toggle("expanded", expand);
    });
  });

  container.querySelectorAll<HTMLElement>("[data-speaker-add-toggle]").forEach((button) => {
    button.addEventListener("click", () => {
      const groupId = button.dataset.speakerAddToggle!;
      const picker = button.nextElementSibling as HTMLElement | null;
      if (!picker?.classList.contains("speaker-picker")) return;
      picker.hidden = !picker.hidden;
      if (picker.hidden) speakerPickerOpen.delete(groupId);
      else speakerPickerOpen.add(groupId);
    });
  });
}

export function speakerAreaInner(group: SpeakerGroup, state: State): string {
  const rows = group.mode === "stereo" ? renderStereoRows(group, state) : renderMirrorRows(group, state);
  const candidates = state.devices.filter((item) => !group.speaker_ids.includes(item.did));
  const picker = candidates.length
    ? `<button class="button plain net-add-toggle" type="button" data-speaker-add-toggle="${escapeHtml(group.id)}">+ 添加音箱</button>
       <div class="speaker-picker" ${speakerPickerOpen.has(group.id) ? "" : "hidden"}>
         <fieldset class="speaker-checks"><legend>加入此组合的音箱</legend>${candidates
           .map(
             (item) =>
               `<label><input type="checkbox" value="${escapeHtml(item.did)}" data-speaker-add="${escapeHtml(group.id)}"> <span class="speaker-row-icon">${brandIcon("xiaomi")}</span> <span>${escapeHtml(item.alias || item.name)}</span></label>`
           )
           .join("")}</fieldset>
       </div>`
    : "";
  return `<div class="speaker-area">${rows}${picker}</div>`;
}

export function renderGroupCodecHint(group: SpeakerGroup, state: State): string {
  const devices = group.speaker_ids.map((id) => state.devices.find((item) => item.did === id)).filter(Boolean);
  if (devices.length < 2) return "";
  const formats = ["MP3", "FLAC", "WAV", "PCM/WAV"];
  const possible = formats.filter((fmt) => devices.every((device) => device!.codec_capabilities?.[fmt] !== false));
  const confirmed = formats.filter((fmt) => devices.every((device) => device!.codec_capabilities?.[fmt] === true));
  const unknown = devices.some((device) => !Object.keys(device!.codec_capabilities ?? {}).length);
  const text = !possible.length
    ? "当前已知能力没有共同格式，请先逐台检测"
    : confirmed.length
      ? `已确认共同格式：${confirmed.join("、")}`
      : `建议先用 MP3；${unknown ? "部分音箱尚未检测，播放后会自动确认。" : "播放后会自动确认。"}`;
  return `<p class="codec-compatibility-hint">格式兼容性：${escapeHtml(text)}</p>`;
}

/** Anchor badge (on the reference speaker) or "设为基准" button (on others). */
function anchorControl(group: SpeakerGroup, did: string): string {
  const pending = pendingGroupActions.has(groupActionKey("anchor", group.id));
  if (group.anchor_did === did) {
    return `<span class="time-reference ${pending ? "control-pending" : ""}" title="其他音箱的时间偏移以它为参照" aria-live="polite"><span aria-hidden="true"></span>${pending ? "切换中…" : "时间基准"}</span>`;
  }
  return `<button class="button plain reference-action" type="button" data-group-anchor="${escapeHtml(group.id)}" data-speaker-id="${escapeHtml(did)}" aria-label="将 ${escapeHtml(did)} 设为时间基准" ${pending ? "disabled" : ""}>设为时间基准</button>`;
}

export function renderMirrorRows(group: SpeakerGroup, state: State): string {
  // Keep at least two members in the group (speakers + network devices).
  const canRemove = groupMemberCount(group) > 2;
  const limit = delayLimit();
  return `<div class="sync-delay-list">
    ${group.speaker_ids.map((did) => {
      const name = speakerName(did, state);
      const isAnchor = group.anchor_did === did;
      const delay = isAnchor ? 0 : (group.delays_ms[did] ?? 0);
      const health = speakerHealth(did, state);
      return `<div class="sync-delay-row ${isAnchor ? "is-time-reference" : ""}">
        <span class="${health ? "speaker-offline" : ""}"><span class="speaker-row-icon">${brandIcon("xiaomi")}</span>${escapeHtml(name)}${health ? ` <span class="caption danger-text">${escapeHtml(health)}</span>` : ""}</span>
        ${isAnchor ? `<span class="sync-note">其它音箱对齐到这台</span>` : `<label class="sync-control"><span class="sync-control-label">延迟</span><input type="range" min="-${limit}" max="${limit}" step="50" value="${delay}"
          data-group-delay="${escapeHtml(group.id)}" data-speaker-id="${escapeHtml(did)}"
          aria-label="${escapeHtml(name)} 相对时间基准的偏移">
        <output>${delay} ms</output></label>`}
        ${anchorControl(group, did)}${speakerRemoveButton(group.id, did, name, canRemove)}
      </div>`;
    }).join("")}
  </div>`;
}

export function renderStereoRows(group: SpeakerGroup, state: State): string {
  // Keep at least two members in the group (speakers + network devices).
  const canRemove = groupMemberCount(group) > 2;
  const limit = delayLimit();
  return `<div class="sync-delay-list stereo-list">
    ${group.speaker_ids.map((did) => {
      const name = speakerName(did, state);
      const channel = group.channels?.[did] || "left";
      const gain = group.gains_db?.[did] ?? 0;
      const isAnchor = group.anchor_did === did;
      const delay = isAnchor ? 0 : (group.delays_ms?.[did] ?? 0);
      const health = speakerHealth(did, state);
      const channelPending = pendingGroupActions.has(groupActionKey("channel", group.id, did));
      return `<div class="stereo-speaker ${isAnchor ? "is-time-reference" : ""}">
        <div class="stereo-head">
          <span class="stereo-identity ${health ? "speaker-offline" : ""}"><span class="speaker-row-icon">${brandIcon("xiaomi")}</span>${escapeHtml(name)}${health ? ` <span class="caption danger-text">${escapeHtml(health)}</span>` : ""}</span>
          <div class="segmented-control ${channelPending ? "control-pending" : ""}" role="group" aria-label="${escapeHtml(name)} 声道" aria-busy="${channelPending}">
            <button class="segment ${channel === "both" ? "active" : ""}" data-group-channel="${escapeHtml(group.id)}" data-speaker-id="${escapeHtml(did)}" data-channel="both" ${channelPending ? "disabled" : ""}>双</button>
            <button class="segment ${channel === "left" ? "active" : ""}" data-group-channel="${escapeHtml(group.id)}" data-speaker-id="${escapeHtml(did)}" data-channel="left" ${channelPending ? "disabled" : ""}>左</button>
            <button class="segment ${channel === "right" ? "active" : ""}" data-group-channel="${escapeHtml(group.id)}" data-speaker-id="${escapeHtml(did)}" data-channel="right" ${channelPending ? "disabled" : ""}>右</button>
          </div>
          ${anchorControl(group, did)}${speakerRemoveButton(group.id, did, name, canRemove)}
        </div>
        <div class="stereo-tuning">
          <label class="sync-control" title="只微调这台音箱；0 dB 表示保持原始响度">
            <span class="sync-control-label">响度</span>
            <input type="range" min="-12" max="12" step="0.5" value="${gain}"
              data-group-gain="${escapeHtml(group.id)}" data-speaker-id="${escapeHtml(did)}"
              aria-label="${escapeHtml(name)} 响度补偿">
            <output>${gain > 0 ? "+" : ""}${gain.toFixed(1)} dB</output>
          </label>
          ${isAnchor ? "" : `<label class="sync-control">
            <span class="sync-control-label">延迟</span>
            <input type="range" min="-${limit}" max="${limit}" step="50" value="${delay}"
              data-group-delay="${escapeHtml(group.id)}" data-speaker-id="${escapeHtml(did)}"
              aria-label="${escapeHtml(name)} 相对时间基准的偏移">
            <output>${delay} ms</output>
          </label>`}
        </div>
      </div>`;
    }).join("")}
  </div>`;
}
