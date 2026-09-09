import type { State } from "../state";
import type { NetworkDevice, SpeakerGroup } from "../api";
import { icon, brandIcon } from "../icons";
import { api } from "../api";
import { store } from "../state";
import { getTestMedia } from "../test-media";

// Discovered LAN devices (AirPlay + DLNA) are fetched outside the store: they
// change on their own schedule, and a poll-driven re-render must never rebuild
// a section the user is editing.
interface NetworkLists {
  airplay: NetworkDevice[];
  dlna: NetworkDevice[];
}
let networkDevices: NetworkLists | null = null;
let networkDevicesFetchedAt = 0;
let networkDevicesLoading = false;
const NETWORK_DEVICES_TTL_MS = 30_000;

// Create-form state also lives outside the DOM: a status-poll re-render
// rebuilds the whole section and would otherwise wipe half-filled forms
// (the "checkbox unchecks itself" symptom). Network keys are "proto:id".
const createFormSpeakers = new Set<string>();
const createFormNetworks = new Set<string>();
let createFormName = "";
// Async group changes survive a full rerender, so controls can acknowledge the
// click immediately and keep showing an honest intermediate state.
const pendingGroupActions = new Set<string>();

const groupActionKey = (kind: "mode" | "channel" | "anchor", groupId: string, speakerId = "") =>
  `${kind}:${groupId}:${speakerId}`;

function resetCreateFormState() {
  createFormSpeakers.clear();
  createFormNetworks.clear();
  createFormName = "";
}

const KIND_ICON: Record<string, "loudspeaker" | "tv" | "projector"> = {
  speaker: "loudspeaker",
  tv: "tv",
  projector: "projector",
};

function findNetworkDevice(id: string): { device: NetworkDevice; proto: "airplay" | "dlna" } | null {
  if (!networkDevices) return null;
  const airplay = networkDevices.airplay.find((item) => item.id === id);
  if (airplay) return { device: airplay, proto: "airplay" };
  const dlna = networkDevices.dlna.find((item) => item.id === id);
  return dlna ? { device: dlna, proto: "dlna" } : null;
}

function networkStatusCaption(device: NetworkDevice | undefined): string {
  if (!device) return "未发现该设备";
  if (!device.supported) return device.unsupported_reason;
  if (device.stream_status === "streaming" || device.stream_status === "playing") return "播放中";
  if (device.stream_status === "error") return device.stream_detail || "播放失败";
  return device.online ? "就绪" : "离线";
}

/** Shared checkbox row — used in the group's "add" picker and the create form. */
function networkCheckboxRow(
  device: NetworkDevice,
  proto: "airplay" | "dlna",
  checked: boolean
): string {
  return `<label class="net-device ${device.supported ? "" : "unsupported"}">
    <input type="checkbox" value="${escapeHtml(device.id)}" data-proto="${proto}" ${checked ? "checked" : ""} ${device.supported ? "" : "disabled"}>
    <span class="net-device-icon">${icon(KIND_ICON[device.kind] ?? "loudspeaker")}</span>
    <span class="net-device-name">${escapeHtml(device.name)}</span>
    <span class="proto-badge">${proto === "airplay" ? "AirPlay" : "DLNA"}</span>
    <span class="caption">${escapeHtml(device.supported ? (device.online ? (device.host || "在线") : "离线") : device.unsupported_reason)}</span>
  </label>`;
}

/** Group card network area: only the devices ALREADY in the group, plus a
 * collapsed "add" picker with the full discovery list. */
function networkTargetsInner(group: SpeakerGroup): string {
  if (networkDevices === null) {
    return `<span class="caption">正在搜索网络中的播放设备…</span>`;
  }
  const attachedAirplay = group.airplay_targets ?? [];
  const attachedDlna = group.dlna_targets ?? [];
  const attachedRows: string[] = [];
  attachedAirplay.forEach((id) => {
    const found = findNetworkDevice(id);
    attachedRows.push(networkAttachedRow(group, id, found?.device, "airplay"));
  });
  attachedDlna.forEach((id) => {
    const found = findNetworkDevice(id);
    attachedRows.push(networkAttachedRow(group, id, found?.device, "dlna"));
  });

  const attachedSet = new Set([...attachedAirplay, ...attachedDlna]);
  const available = [
    ...networkDevices.airplay.map((d) => ({ d, proto: "airplay" as const })),
    ...networkDevices.dlna.map((d) => ({ d, proto: "dlna" as const })),
  ].filter(({ d }) => !attachedSet.has(d.id));
  const pickerRows = available.map(({ d, proto }) => networkCheckboxRow(d, proto, false));

  return `
    ${attachedRows.length ? `<div class="net-attached">${attachedRows.join("")}</div>` : `<span class="caption">尚未加入网络播放设备</span>`}
    ${pickerRows.length ? `
      <button class="button plain net-add-toggle" type="button" data-net-add-toggle>+ 添加网络设备</button>
      <div class="net-picker" ${netPickerOpen.has(group.id) ? "" : "hidden"}>
        <fieldset class="speaker-checks airplay-checks"><legend>加入此组合的网络设备</legend>${pickerRows.join("")}</fieldset>
      </div>` : ""}
  `;
}

const NORMAL_DELAY_LIMIT_MS = 5000;
const LARGE_DELAY_LIMIT_MS = 15000;
let largeDelayEnabled = false;

function delayLimit(): number {
  return largeDelayEnabled ? LARGE_DELAY_LIMIT_MS : NORMAL_DELAY_LIMIT_MS;
}

function networkAttachedRow(
  group: SpeakerGroup,
  id: string,
  device: NetworkDevice | undefined,
  proto: "airplay" | "dlna"
): string {
  const name = device?.name ?? id;
  const caption = networkStatusCaption(device);
  const failed = device?.stream_status === "error";
  const delay = group.delays_ms?.[id] ?? 0;
  const limit = delayLimit();
  const channel = group.network_channels?.[id] ?? "";
  const channelPicker = group.mode === "stereo" ? `
    <div class="segmented-control net-channel" role="group" aria-label="${escapeHtml(name)} 声道">
      <button class="segment ${channel === "" ? "active" : ""}" data-net-channel="${escapeHtml(id)}" data-channel="">双</button>
      <button class="segment ${channel === "left" ? "active" : ""}" data-net-channel="${escapeHtml(id)}" data-channel="left">左</button>
      <button class="segment ${channel === "right" ? "active" : ""}" data-net-channel="${escapeHtml(id)}" data-channel="right">右</button>
    </div>` : "";
  return `<div class="net-attached-item" data-net-item="${escapeHtml(id)}">
    <div class="net-attached-row">
      <span class="net-device-icon">${icon(KIND_ICON[device?.kind ?? "speaker"] ?? "loudspeaker")}</span>
      <span class="net-device-name">${escapeHtml(name)}</span>
      <span class="proto-badge">${proto === "airplay" ? "AirPlay" : "DLNA"}</span>
      <span class="caption ${failed ? "danger-text" : ""}">${escapeHtml(caption)}</span>
      ${channelPicker}
      <button class="button plain danger-text net-remove" type="button" data-net-remove="${escapeHtml(id)}" data-proto="${proto}" aria-label="从组合移除 ${escapeHtml(name)}">移除</button>
    </div>
    ${proto === "airplay" && device?.supported ? `<label class="sync-delay-row net-delay-row">
      <span>延迟</span>
      <input type="range" min="-${limit}" max="${limit}" step="50" value="${delay}"
        data-net-delay="${escapeHtml(id)}" aria-label="${escapeHtml(name)} 相对基准偏移">
      <output>${delay} ms</output>
    </label>` : ""}
  </div>`;
}

function renderNetworkTargets(group: SpeakerGroup): string {
  return `<div class="airplay-targets" data-network-targets="${escapeHtml(group.id)}">${networkTargetsInner(group)}</div>`;
}

/** Create-form picker: every discovered device, as checkboxes. */
function createFormPickerInner(): string {
  if (networkDevices === null) {
    return `<span class="caption">正在搜索网络中的播放设备…</span>`;
  }
  const rows = [
    ...networkDevices.airplay.map((d) => networkCheckboxRow(d, "airplay", createFormNetworks.has(`airplay:${d.id}`))),
    ...networkDevices.dlna.map((d) => networkCheckboxRow(d, "dlna", createFormNetworks.has(`dlna:${d.id}`))),
  ];
  if (!rows.length) return `<span class="caption">未发现网络播放设备</span>`;
  return `<fieldset class="speaker-checks airplay-checks"><legend>组合中的网络设备（可选）</legend>${rows.join("")}</fieldset>`;
}

function bindNetworkSections(container: HTMLElement) {
  const sections = Array.from(container.querySelectorAll<HTMLElement>("[data-network-targets]"));
  const createPicker = container.querySelector<HTMLElement>("[data-create-network-picker]");
  if (!sections.length && !createPicker) return;

  const fill = () => {
    sections.forEach((section) => {
      const groupId = section.dataset.networkTargets!;
      const group = store.get().fullConfig?.groups.find((item) => item.id === groupId);
      if (!group) return;
      section.innerHTML = networkTargetsInner(group);
      bindNetworkSection(section, groupId);
    });
    if (createPicker) {
      createPicker.innerHTML = createFormPickerInner();
      bindCreateFormPicker(createPicker);
    }
  };

  const fresh = networkDevices !== null && Date.now() - networkDevicesFetchedAt < NETWORK_DEVICES_TTL_MS;
  if (fresh) {
    fill();
    return;
  }
  if (!networkDevicesLoading) {
    networkDevicesLoading = true;
    Promise.all([api.getAirPlayDevices(), api.getDlnaDevices()])
      .then(([airplay, dlna]) => {
        networkDevices = { airplay, dlna };
        networkDevicesFetchedAt = Date.now();
        fill();
      })
      .catch(() => {
        networkDevices = { airplay: [], dlna: [] };
        networkDevicesFetchedAt = Date.now();
        fill();
      })
      .finally(() => {
        networkDevicesLoading = false;
      });
  }
}

/** The create form's network picker re-renders from networkDevices; mirror
 * every toggle into createFormNetworks so the selection survives. */
function bindCreateFormPicker(picker: HTMLElement) {
  picker.querySelectorAll<HTMLInputElement>('input[type="checkbox"]').forEach((checkbox) => {
    checkbox.addEventListener("change", () => {
      const key = `${checkbox.dataset.proto}:${checkbox.value}`;
      if (checkbox.checked) createFormNetworks.add(key);
      else createFormNetworks.delete(key);
    });
  });
}

async function saveNetworkMembership(groupId: string, airplayIds: string[], dlnaIds: string[]) {
  const updated = await api.updateGroup(groupId, {
    airplay_targets: airplayIds,
    dlna_targets: dlnaIds,
  });
  // Mutate in place instead of store.set: the section must NOT re-render
  // in response to its own save.
  const group = store.get().fullConfig?.groups.find((item) => item.id === groupId);
  if (group) {
    group.airplay_targets = updated.airplay_targets;
    group.dlna_targets = updated.dlna_targets;
  }
  return updated;
}

function bindNetworkSection(section: HTMLElement, groupId: string) {
  const group = () => store.get().fullConfig?.groups.find((item) => item.id === groupId);
  const groupName = () => group()?.name ?? "组合";

  section.querySelector<HTMLElement>("[data-net-add-toggle]")?.addEventListener("click", () => {
    const picker = section.querySelector<HTMLElement>(".net-picker");
    if (!picker) return;
    picker.hidden = !picker.hidden;
    if (picker.hidden) netPickerOpen.delete(groupId);
    else netPickerOpen.add(groupId);
  });

  // Adding a device from the picker: save, then re-render just this section
  // so the device moves into the attached list.
  section.querySelectorAll<HTMLInputElement>('.net-picker input[type="checkbox"]').forEach((checkbox) => {
    checkbox.addEventListener("change", async () => {
      if (store.get().saving) return;
      const current = group();
      if (!current) return;
      const airplayIds = [...(current.airplay_targets ?? [])];
      const dlnaIds = [...(current.dlna_targets ?? [])];
      (checkbox.dataset.proto === "airplay" ? airplayIds : dlnaIds).push(checkbox.value);
      const deviceName =
        checkbox.closest("label")?.querySelector(".net-device-name")?.textContent ?? "";
      store.set({ saving: true });
      try {
        await saveNetworkMembership(groupId, airplayIds, dlnaIds);
        store.set({ saving: false });
        store.showToast(`已把「${deviceName}」加入组合「${groupName()}」`);
        section.innerHTML = networkTargetsInner(group()!);
        bindNetworkSection(section, groupId);
        section.querySelector<HTMLElement>(".net-picker")?.removeAttribute("hidden");
      } catch (err) {
        store.set({ saving: false });
        checkbox.checked = false;
        store.showToast(`保存失败: ${err instanceof Error ? err.message : "未知错误"}`);
      }
    });
  });

  section.querySelectorAll<HTMLElement>("[data-net-remove]").forEach((button) => {
    button.addEventListener("click", async () => {
      if (store.get().saving) return;
      const current = group();
      if (!current) return;
      const id = button.dataset.netRemove!;
      const proto = button.dataset.proto;
      const airplayIds = (current.airplay_targets ?? []).filter((item) => item !== id);
      const dlnaIds = (current.dlna_targets ?? []).filter((item) => item !== id);
      if (current.speaker_ids.length + airplayIds.length + dlnaIds.length < 2) {
        store.showToast("组合至少需要两个成员，不能再移除了");
        return;
      }
      const deviceName =
        button.closest(".net-attached-item")?.querySelector(".net-device-name")?.textContent ?? "";
      store.set({ saving: true });
      try {
        await saveNetworkMembership(groupId, airplayIds, dlnaIds);
        store.set({ saving: false });
        store.showToast(`已把「${deviceName}」从组合「${groupName()}」移除`);
        section.innerHTML = networkTargetsInner(group()!);
        bindNetworkSection(section, groupId);
      } catch (err) {
        store.set({ saving: false });
        store.showToast(`保存失败: ${err instanceof Error ? err.message : "未知错误"}`);
      }
    });
  });

  // Channel assignment (stereo groups): segmented per attached device.
  section.querySelectorAll<HTMLElement>("[data-net-channel]").forEach((button) => {
    button.addEventListener("click", async () => {
      if (store.get().saving) return;
      const current = group();
      if (!current) return;
      const targetId = button.dataset.netChannel!;
      const channel = button.dataset.channel as "" | "left" | "right";
      if ((current.network_channels?.[targetId] ?? "") === channel) return;
      const channels = { ...(current.network_channels ?? {}) };
      if (channel === "") {
        delete channels[targetId];
      } else {
        channels[targetId] = channel;
      }
      // Flip the active segment immediately; persist without re-rendering.
      button.closest(".net-channel")?.querySelectorAll(".segment").forEach((seg) => {
        seg.classList.toggle("active", seg === button);
      });
      store.set({ saving: true });
      try {
        const updated = await api.updateGroup(groupId, { network_channels: channels });
        current.network_channels = updated.network_channels;
        store.set({ saving: false });
      } catch (err) {
        store.set({ saving: false });
        store.showToast(`声道保存失败: ${err instanceof Error ? err.message : "未知错误"}`);
      }
    });
  });

  // Per-target delay: commit on release, in-place mutation, no re-render
  // while dragging (same rule as every other slider in this view).
  section.querySelectorAll<HTMLInputElement>("[data-net-delay]").forEach((input) => {
    input.addEventListener("input", () => {
      const output = input.nextElementSibling as HTMLOutputElement | null;
      if (output) output.value = `${Number(input.value)} ms`;
    });
    input.addEventListener("change", async () => {
      const targetId = input.dataset.netDelay!;
      const value = Number(input.value);
      const current = group();
      if (!current) return;
      try {
        const updated = await api.updateGroup(groupId, {
          delays_ms: { ...(current.delays_ms ?? {}), [targetId]: value },
        });
        current.delays_ms = updated.delays_ms;
      } catch (err) {
        store.showToast(`延迟保存失败: ${err instanceof Error ? err.message : "未知错误"}`);
      }
    });
  });
}

// Group card expansion: an explicit user toggle always wins; the default is
// "expanded while the group is actively playing".
const groupExpansion = new Map<string, boolean>();

// The "+ 添加网络设备" / "+ 添加音箱" pickers are plain <div hidden> toggles —
// a poll-driven re-render rebuilds them closed, so the open state lives here
// and is rendered back into the markup.
const netPickerOpen = new Set<string>();
const speakerPickerOpen = new Set<string>();

function isGroupExpanded(group: SpeakerGroup, state: State): boolean {
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

export function renderReceiversView(state: State): string {
  largeDelayEnabled = Boolean(state.fullConfig?.large_delay_enabled);
  const { receivers, fullConfig, status } = state;
  const dlnaEnabled = fullConfig?.dlna_enabled ?? false;
  const airplay2Entries = state.airplay2?.instances ?? [];
  const visibleAirPlay2Entries = state.airplay2?.enabled ? airplay2Entries : [];
  const totalEntries = receivers.length + visibleAirPlay2Entries.length;

  const content =
    receivers.length > 0
      ? `<div class="receiver-grid">${receivers
          .map((r) => {
            const detail = compactTargetLabel(r.name, r.did, state);
            const definition = fullConfig?.receivers.find((item) => item.id === r.did);
            const mapped = Boolean(definition && settingsTargetIds(definition, state).length);
            const available = r.status === "running" && mapped;
            return `
              <div class="receiver-card">
                <div class="receiver-heading">
                  <div>
                    <div class="device-name">${escapeHtml(r.name)}</div>
                    <span class="protocol-badge">经典 AirPlay</span>
                    ${dlnaEnabled ? `<span class="protocol-badge">DLNA</span>` : ""}
                    ${detail ? `<span class="caption">${escapeHtml(detail)}</span>` : ""}
                  </div>
                <span class="status-pill ${available ? "running" : r.status === "error" ? "error" : ""}">
                  ${!mapped ? "未设置" : available ? "可连接" : r.status === "error" ? "不可用" : "准备中"}
                </span>
                </div>
              </div>
            `;
          })
          .join("")}</div>`
      : `
          <div class="group">
            <div class="empty-state">
              <div class="empty-state-icon">${icon("antenna")}</div>
              <span class="body">尚未添加播放入口</span>
              <span class="caption" style="max-width: 260px;">
                添加后，它会出现在手机的 AirPlay${dlnaEnabled ? " 和 DLNA" : ""} 列表中。
              </span>
            </div>
          </div>
        `;

  return `
    <div class="page-heading">
      <h2 class="page-title">播放</h2>
      <p>管理手机中看到的播放名称，以及每个名称实际播放到哪里。</p>
    </div>
    <div class="group-header">播放入口 · 共 ${totalEntries} 个</div>
    ${content}
    ${state.airplay2?.enabled ? renderAirPlay2Entries(state) : ""}
    ${renderManagement(state)}
  `;
}

function renderAirPlay2Entries(state: State): string {
  const entries = state.airplay2?.instances ?? [];
  if (!entries.length) return "";
  return `
    <div class="group-header">AirPlay 2 播放入口 <span class="feature-badge">实验性</span></div>
    <div class="receiver-grid">${entries.map((item) => {
      const available = item.enabled && item.status === "running";
      return `<div class="receiver-card">
        <div class="receiver-heading">
          <div class="receiver-entry-summary">
            <div class="receiver-entry-identity"><span class="device-name">${escapeHtml(item.name)}</span>
            <span class="protocol-badge">AirPlay 2</span>
            </div><span class="caption">播放到 ${escapeHtml(item.target_name)}</span>
          </div>
          <span class="status-pill ${available ? "running" : item.status === "error" ? "error" : ""}">${available ? "可连接" : item.status === "error" ? "不可用" : "准备中"}</span>
        </div>
      </div>`;
    }).join("")}</div>`;
}

function renderManagement(state: State): string {
  const config = state.fullConfig;
  if (!config) return "";
  const publishedReceivers = config.receivers.filter(
    (item) => config.sync_groups_enabled || item.target_type !== "group"
  );
  const usedTargets = new Set(
    config.receivers
      .filter((item) => item.target_type !== "selected" && item.target_id)
      .map((item) => `${item.target_type}:${item.target_id}`)
  );
  const targetOptions = [
    `<option value="" disabled selected>选择音箱或组合</option>`,
    ...state.devices.filter((item) => !usedTargets.has(`speaker:${item.did}`)).map((item) => `<option value="speaker:${escapeHtml(item.did)}">音箱 · ${escapeHtml(item.alias || item.name)}</option>`),
    ...(config.sync_groups_enabled
      ? config.groups.filter((item) => !usedTargets.has(`group:${item.id}`)).map((item) => `<option value="group:${escapeHtml(item.id)}">音箱组合 · ${escapeHtml(item.name)}</option>`)
      : []),
  ].join("");
  const hasAvailableTarget = targetOptions.includes('value="speaker:') || targetOptions.includes('value="group:');
  // fnOS ships one native AirPlay 2 receiver. Its only meaningful setting is
  // the playback target; creating more advertised receivers is unsupported.
  return `
    <div class="group-header">入口管理</div>
    <div class="group">
      ${hasAvailableTarget ? `<form class="cell receiver-form" data-create-receiver>
        <div class="cell-content"><span class="cell-title">添加播放入口</span><span class="cell-subtitle">选择一台音箱或一个音箱组合</span></div>
        <select class="input" name="target" required>${targetOptions}</select>
        <button class="button primary" type="submit">添加</button>
      </form>` : `<div class="cell"><div class="cell-content"><span class="cell-title">所有音箱均已添加</span><span class="cell-subtitle">发现新音箱后可继续添加</span></div></div>`}
      ${publishedReceivers.map((item) => {
        // The toggle advertises the entry over AirPlay (and DLNA when on) —
        // name the actual places it appears, not "the phone".
        const visibleWhere = config.dlna_enabled ? "AirPlay / DLNA" : "AirPlay";
        return `<div class="cell receiver-entry-row" data-receiver-row="${escapeHtml(item.id)}">
        <div class="cell-content"><span class="cell-title">${escapeHtml(item.name)}</span><span class="cell-subtitle">${escapeHtml(targetLabel(item.id, state))}</span></div>
        <label class="receiver-visibility">
          <span>${item.enabled ? `显示在 ${visibleWhere}` : "已隐藏"}</span>
          <input class="switch" type="checkbox" ${item.enabled ? "checked" : ""} ${state.saving ? "disabled" : ""} data-receiver-enabled="${escapeHtml(item.id)}" aria-label="在播放列表中显示 ${escapeHtml(item.name)}">
        </label>
      </div>`;
      }).join("")}
    </div>

    <div class="group-header">音箱组合 <span class="feature-badge">实验性</span></div>
    <p class="group-header-hint">以时间基准为参照：比基准先响（快）向 + 调，比基准后响（慢）向 − 调。</p>
    <div class="group">
      ${!config.sync_groups_enabled ? `<div class="cell"><div class="cell-content"><span class="cell-title">音箱组合已关闭</span><span class="cell-subtitle">已有组合会保留，可在设置中重新开启</span></div></div>` : config.groups.map((group) => {
        const isStereo = group.mode === "stereo";
        const expanded = isGroupExpanded(group, state);
        const netCount = (group.airplay_targets?.length ?? 0) + (group.dlna_targets?.length ?? 0);
        const summary = `${isStereo ? "立体声" : "同声播放"} · ${group.speaker_ids.length ? `${group.speaker_ids.length} 音箱` : ""}${group.speaker_ids.length && netCount ? " + " : ""}${netCount ? `${netCount} 网络设备` : ""}`;
        return `<div class="sync-group">
        <div class="cell sync-group-header">
          <button class="group-toggle ${expanded ? "expanded" : ""}" type="button" data-group-toggle="${escapeHtml(group.id)}" aria-label="展开或收起组合 ${escapeHtml(group.name)}">${icon("chevron")}</button>
          <div class="cell-content"><span class="cell-title">${escapeHtml(group.name)}</span><span class="cell-subtitle">${escapeHtml(summary)}</span></div>
          <div class="segmented-control ${pendingGroupActions.has(groupActionKey("mode", group.id)) ? "control-pending" : ""}" role="group" aria-label="播放模式" aria-busy="${pendingGroupActions.has(groupActionKey("mode", group.id))}">
            <button class="segment ${!isStereo ? "active" : ""}" data-group-mode="${escapeHtml(group.id)}" data-mode="mirror" ${pendingGroupActions.has(groupActionKey("mode", group.id)) ? "disabled" : ""}>同声播放</button>
            <button class="segment ${isStereo ? "active" : ""}" data-group-mode="${escapeHtml(group.id)}" data-mode="stereo" ${pendingGroupActions.has(groupActionKey("mode", group.id)) ? "disabled" : ""}>立体声</button>
          </div>
          <button class="button plain" type="button" data-group-calibrate="${escapeHtml(group.id)}">辅助校准</button>
          <button class="button plain danger-text group-delete" data-delete-group="${escapeHtml(group.id)}">删除组合</button>
        </div>
        <div class="sync-group-body" data-group-body="${escapeHtml(group.id)}" ${expanded ? "" : "hidden"}>
          ${speakerAreaInner(group, state)}
          ${renderNetworkTargets(group)}
          ${isStereo ? `<p class="footnote" style="margin: var(--space-xs) var(--space-lg) var(--space-sm);">分配左右声道，并用响度、延迟微调同步。</p>` : ""}
        </div>
      </div>`;
      }).join("")}
      ${config.sync_groups_enabled ? `<form class="group-form" data-create-group>
        <div class="group-form-heading"><span class="cell-title">新建音箱组合</span><span class="cell-subtitle">选择至少两个播放设备</span></div>
        <input class="input group-form-name" name="name" maxlength="50" required placeholder="例如：全屋播放" aria-label="组合名称" value="${escapeHtml(createFormName)}">
        <fieldset class="speaker-checks"><legend>组合中的音箱</legend>${state.devices.map((item) => `<label><input type="checkbox" name="speaker" value="${escapeHtml(item.did)}" ${createFormSpeakers.has(item.did) ? "checked" : ""}> <span class="speaker-row-icon">${brandIcon("xiaomi")}</span> <span>${escapeHtml(item.alias || item.name)}</span></label>`).join("") || `<span class="caption">暂无可选音箱</span>`}</fieldset>
        <div data-create-network-picker></div>
        <div class="group-form-actions"><button class="button primary" type="submit">创建组合</button></div>
      </form>` : ""}
    </div>`;
}

function speakerAreaInner(group: import("../api").SpeakerGroup, state: State): string {
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

function speakerRemoveButton(groupId: string, did: string, name: string, canRemove: boolean): string {
  return canRemove
    ? `<button class="button plain danger-text net-remove" type="button" data-speaker-remove="${escapeHtml(did)}" data-group-id="${escapeHtml(groupId)}" aria-label="从组合移除 ${escapeHtml(name)}">移除</button>`
    : "";
}

function groupMemberCount(group: import("../api").SpeakerGroup): number {
  return (
    group.speaker_ids.length +
    (group.airplay_targets?.length ?? 0) +
    (group.dlna_targets?.length ?? 0)
  );
}

function replaceGroup(config: NonNullable<State["fullConfig"]>, updated: SpeakerGroup) {
  return { ...config, groups: config.groups.map((item) => item.id === updated.id ? updated : item) };
}

/** Change only the timing coordinate system. Normalized playback holds stay identical. */
function rebaseGroup(group: SpeakerGroup, anchorDid: string): SpeakerGroup {
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

/** Anchor badge (on the reference speaker) or "设为基准" button (on others). */
function anchorControl(group: SpeakerGroup, did: string): string {
  const pending = pendingGroupActions.has(groupActionKey("anchor", group.id));
  if (group.anchor_did === did) {
    return `<span class="time-reference ${pending ? "control-pending" : ""}" title="其他音箱的时间偏移以它为参照" aria-live="polite"><span aria-hidden="true"></span>${pending ? "切换中…" : "时间基准"}</span>`;
  }
  return `<button class="button plain reference-action" type="button" data-group-anchor="${escapeHtml(group.id)}" data-speaker-id="${escapeHtml(did)}" aria-label="将 ${escapeHtml(did)} 设为时间基准" ${pending ? "disabled" : ""}>设为时间基准</button>`;
}

function renderMirrorRows(group: import("../api").SpeakerGroup, state: State): string {
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

/** 离线/播放失败 hint for a Xiaomi speaker row; empty string when healthy. */
function speakerHealth(did: string, state: State): string {
  const device = state.devices.find((item) => item.did === did);
  if (device?.play_error) return "播放失败，重试中";
  if (device?.presence === "offline") return "离线";
  return "";
}

function renderStereoRows(group: import("../api").SpeakerGroup, state: State): string {
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

function confirmCalibration(groupName: string): Promise<string | null> {
  return new Promise((resolve) => {
    const dialog = document.createElement("dialog");
    dialog.className = "confirm-dialog";
    const media = getTestMedia();
    dialog.innerHTML = `<form method="dialog">
      <div class="confirm-dialog-copy"><h3>校准「${escapeHtml(groupName)}」？</h3><p>将循环播放测试音，退出后恢复原内容。</p></div>
      ${media ? `<label class="test-url-field"><span>校准音频</span><select class="input" name="calibration-media"><option value="">内置节拍（推荐）</option><option value="${escapeHtml(media.token)}">${escapeHtml(media.name)}</option></select></label>` : ""}
      <div class="confirm-dialog-actions"><button class="button plain" value="cancel">取消</button><button class="button primary" value="confirm">进入校准</button></div>
    </form>`;
    dialog.addEventListener("close", () => {
      const selected = dialog.querySelector<HTMLSelectElement>('[name="calibration-media"]')?.value ?? "";
      resolve(dialog.returnValue === "confirm" ? selected : null);
      dialog.remove();
    }, { once: true });
    document.body.append(dialog);
    dialog.showModal();
  });
}

function liveCalibration(group: SpeakerGroup, state: State): Promise<boolean> {
  return new Promise((resolve) => {
    const original = { ...group.delays_ms };
    const delays = { ...original };
    const members = group.speaker_ids.filter((did) => did !== group.anchor_did);
    const limit = delayLimit();
    const dialog = document.createElement("dialog");
    dialog.className = "confirm-dialog calibration-dialog";
    dialog.innerHTML = `<form>
      <div class="confirm-dialog-copy"><h3>调整播放时间</h3><p>以「${escapeHtml(speakerName(group.anchor_did!, state))}」为基准，让其他音箱的节拍重合。</p></div>
      <div class="calibration-guide-list">${members.map((did) => {
        const value = delays[did] ?? 0;
        return `<fieldset class="calibration-guide-row" data-live-calibration="${escapeHtml(did)}"><legend>${escapeHtml(speakerName(did, state))}</legend>
          <div class="live-calibration-control">
            <button class="button plain" type="button" data-nudge="-100">−100</button><button class="button plain" type="button" data-nudge="-10">−10</button>
            <input type="range" min="-${limit}" max="${limit}" step="10" value="${value}" aria-label="播放时间调整">
            <button class="button plain" type="button" data-nudge="10">+10</button><button class="button plain" type="button" data-nudge="100">+100</button>
            <output>${value} ms</output>
          </div></fieldset>`;
      }).join("")}</div>
      <p class="caption calibration-hint">先响（快）向 + 调，后响（慢）向 − 调。</p>
      <div class="confirm-dialog-actions"><button class="button plain" type="button" data-live-cancel>取消并还原</button><button class="button primary" type="submit">完成并保存</button></div>
    </form>`;
    let saved = false;
    const apply = async (did: string, value: number) => {
      if (value) delays[did] = value; else delete delays[did];
      const updated = await api.updateGroup(group.id, { delays_ms: delays });
      const config = store.get().fullConfig;
      if (config) store.set({ fullConfig: replaceGroup(config, updated) });
    };
    dialog.querySelectorAll<HTMLElement>("[data-live-calibration]").forEach((row) => {
      const did = row.dataset.liveCalibration!;
      const slider = row.querySelector<HTMLInputElement>('input[type="range"]')!;
      const output = row.querySelector<HTMLOutputElement>("output")!;
      const set = (value: number) => { slider.value = String(Math.max(-limit, Math.min(limit, value))); output.value = `${slider.value} ms`; };
      slider.addEventListener("input", () => set(Number(slider.value)));
      slider.addEventListener("change", () => apply(did, Number(slider.value)).catch((e) => store.showToast(`调整失败: ${message(e)}`)));
      row.querySelectorAll<HTMLButtonElement>("[data-nudge]").forEach((button) => button.addEventListener("click", () => {
        set(Number(slider.value) + Number(button.dataset.nudge));
        apply(did, Number(slider.value)).catch((e) => store.showToast(`调整失败: ${message(e)}`));
      }));
    });
    dialog.querySelector("form")?.addEventListener("submit", (event) => { event.preventDefault(); saved = true; dialog.close(); });
    dialog.querySelector("[data-live-cancel]")?.addEventListener("click", () => dialog.close());
    dialog.addEventListener("close", async () => {
      if (!saved) {
        try { await api.updateGroup(group.id, { delays_ms: original }); }
        catch (e) { store.showToast(`还原延迟失败: ${message(e)}`); }
      }
      resolve(saved);
      dialog.remove();
    }, { once: true });
    document.body.append(dialog);
    dialog.showModal();
  });
}

function guideCalibration(
  group: SpeakerGroup,
  state: State,
  requestOffsets: Record<string, number>
): Promise<Record<string, number> | null> {
  return new Promise((resolve) => {
    const anchor = group.anchor_did!;
    const anchorName = speakerName(anchor, state);
    const members = group.speaker_ids.filter((did) => did !== anchor);
    const limit = delayLimit();
    const dialog = document.createElement("dialog");
    dialog.className = "confirm-dialog calibration-dialog";
    dialog.innerHTML = `<form>
      <div class="confirm-dialog-copy"><h3>根据听感完成校准</h3><p>以「${escapeHtml(anchorName)}」为基准，选择每台音箱刚才听起来的先后。后台启动差只用于建议幅度，不决定方向。</p></div>
      <div class="calibration-guide-list">
        ${members.map((did) => {
          const name = speakerName(did, state);
          const current = group.delays_ms?.[did] ?? 0;
          const reference = Math.abs(requestOffsets[did] ?? 0);
          const magnitude = Math.abs(current) || Math.max(100, reference);
          const selected = current > 0 ? "ahead" : current < 0 ? "behind" : "";
          return `<fieldset class="calibration-guide-row">
            <legend>${escapeHtml(name)}</legend>
            <div class="calibration-direction" role="radiogroup" aria-label="${escapeHtml(name)} 与基准的先后">
              <label><input type="radio" name="direction:${escapeHtml(did)}" value="ahead" ${selected === "ahead" ? "checked" : ""} required><span>先响</span></label>
              <label><input type="radio" name="direction:${escapeHtml(did)}" value="sync" required><span>同步</span></label>
              <label><input type="radio" name="direction:${escapeHtml(did)}" value="behind" ${selected === "behind" ? "checked" : ""} required><span>后响</span></label>
            </div>
            <label class="calibration-amount"><span>调整幅度</span><input class="input" type="number" name="amount:${escapeHtml(did)}" min="0" max="${limit}" step="50" value="${Math.min(limit, magnitude)}"><span>ms</span></label>
          </fieldset>`;
        }).join("")}
      </div>
      <p class="caption calibration-hint">“先响”会延后这台音箱；“后响”会让其他音箱等待它。结果写入普通延迟，之后仍可拖动微调。</p>
      <div class="confirm-dialog-actions"><button class="button plain" type="button" data-calibration-cancel>暂不修改</button><button class="button primary" type="submit">应用校准</button></div>
    </form>`;
    let submitted = false;
    dialog.querySelector("[data-calibration-cancel]")?.addEventListener("click", () => dialog.close());
    dialog.querySelector("form")?.addEventListener("submit", (event) => {
      event.preventDefault();
      const data = new FormData(event.currentTarget as HTMLFormElement);
      const delays = { ...group.delays_ms };
      for (const did of members) {
        const direction = String(data.get(`direction:${did}`) || "");
        const amount = Math.max(0, Math.min(limit, Number(data.get(`amount:${did}`)) || 0));
        if (direction === "ahead") delays[did] = amount;
        else if (direction === "behind") delays[did] = -amount;
        else if (direction === "sync") delete delays[did];
      }
      submitted = true;
      resolve(delays);
      dialog.close();
    });
    dialog.addEventListener("close", () => {
      if (!submitted) resolve(null);
      dialog.remove();
    }, { once: true });
    document.body.append(dialog);
    dialog.showModal();
  });
}

export function bindReceiversView(container: HTMLElement, rerender: () => void) {  bindNetworkSections(container);
  container.querySelector<HTMLFormElement>("[data-create-receiver]")?.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (store.get().saving) return;
    const form = event.currentTarget as HTMLFormElement;
    const data = new FormData(form);
    const [target_type, target_id] = String(data.get("target") || "").split(":", 2);
    const state = store.get();
    const config = state.fullConfig;
    if (!config) return;
    const fallbackName = target_type === "speaker"
      ? state.devices.find((item) => item.did === target_id)?.alias || state.devices.find((item) => item.did === target_id)?.name
      : config.groups.find((item) => item.id === target_id)?.name;
    const submit = form.querySelector<HTMLButtonElement>('button[type="submit"]');
    if (submit) { submit.disabled = true; submit.textContent = "正在添加…"; }
    store.set({ saving: true });
    try {
      await api.createReceiver({ name: fallbackName || "AirPlay", target_type: target_type as "selected" | "speaker" | "group", target_id: target_id || null });
      await refreshRuntimeState(); store.set({ saving: false }); rerender(); store.showToast("已添加到 AirPlay");
    } catch (e) {
      store.set({ saving: false });
      if (submit) { submit.disabled = false; submit.textContent = "添加"; }
      store.showToast(`创建失败: ${message(e)}`);
    }
  });

  container.querySelectorAll<HTMLInputElement>("[data-receiver-enabled]").forEach((input) => input.addEventListener("change", async () => {
    if (store.get().saving) return;
    const receiverId = input.dataset.receiverEnabled!;
    const enabled = input.checked;
    const previousReceivers = store.get().receivers;
    const previousConfig = store.get().fullConfig;
    const definition = previousConfig?.receivers.find((item) => item.id === receiverId);
    store.set({
      saving: true,
      fullConfig: previousConfig ? {
        ...previousConfig,
        receivers: previousConfig.receivers.map((item) => item.id === receiverId ? { ...item, enabled } : item),
      } : previousConfig,
      receivers: enabled
        ? previousReceivers.some((item) => item.did === receiverId)
          ? previousReceivers
          : [...previousReceivers, {
              did: receiverId,
              name: definition?.name || "AirPlay",
              status: "idle",
              stream_url: "",
              detail: "正在准备播放入口",
            }]
        : previousReceivers.filter((item) => item.did !== receiverId),
    });
    rerender();
    try {
      await api.updateReceiver(receiverId, { enabled });
      await refreshRuntimeState();
      store.set({ saving: false });
      rerender();
      store.showToast(enabled ? "播放入口已显示" : "播放入口已隐藏");
    } catch (e) {
      store.set({ fullConfig: previousConfig, receivers: previousReceivers, saving: false });
      input.checked = !enabled;
      rerender();
      store.showToast(`设置失败: ${message(e)}`);
    }
  }));

  container.querySelector<HTMLFormElement>("[data-create-group]")?.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (store.get().saving) return;
    const form = event.currentTarget as HTMLFormElement;
    const submit = form.querySelector<HTMLButtonElement>('button[type="submit"]');
    if (submit) { submit.disabled = true; submit.textContent = "正在创建…"; }
    store.set({ saving: true });
    try {
      // Read from the persistent sets, not the DOM: a poll re-render may have
      // rebuilt the form between the user's click and this submit.
      await api.createGroup(
        createFormName.trim(),
        [...createFormSpeakers],
        [...createFormNetworks].filter((k) => k.startsWith("airplay:")).map((k) => k.slice(8)),
        [...createFormNetworks].filter((k) => k.startsWith("dlna:")).map((k) => k.slice(5))
      );
      resetCreateFormState();
      await refreshRuntimeState(); store.set({ saving: false }); rerender(); store.showToast("音箱组合已创建");
    } catch (e) {
      store.set({ saving: false });
      if (submit) { submit.disabled = false; submit.textContent = "创建组合"; }
      store.showToast(`创建失败: ${message(e)}`);
    }
  });

  const createForm = container.querySelector<HTMLElement>("[data-create-group]");
  createForm?.querySelectorAll<HTMLInputElement>('input[name="speaker"]').forEach((checkbox) => {
    checkbox.addEventListener("change", () => {
      if (checkbox.checked) createFormSpeakers.add(checkbox.value);
      else createFormSpeakers.delete(checkbox.value);
    });
  });
  createForm?.querySelector<HTMLInputElement>('input[name="name"]')?.addEventListener("input", (event) => {
    createFormName = (event.target as HTMLInputElement).value;
  });

  container.querySelectorAll<HTMLElement>("[data-delete-group]").forEach((button) => button.addEventListener("click", async () => {
    const groupId = button.dataset.deleteGroup!;
    const previous = store.get();
    const config = previous.fullConfig;
    if (!config || previous.saving) return;
    const receiverIds = new Set(
      config.receivers
        .filter((item) => item.target_type === "group" && item.target_id === groupId)
        .map((item) => item.id)
    );
    store.set({
      saving: true,
      fullConfig: {
        ...config,
        groups: config.groups.filter((item) => item.id !== groupId),
        receivers: config.receivers.filter((item) => !receiverIds.has(item.id)),
      },
      receivers: previous.receivers.filter((item) => !receiverIds.has(item.did)),
    });
    rerender();
    try {
      await api.deleteGroup(groupId);
      await refreshRuntimeState();
      store.set({ saving: false });
      rerender();
      store.showToast("音箱组合已删除");
    } catch (e) {
      store.set({
        saving: false,
        fullConfig: previous.fullConfig,
        status: previous.status,
        receivers: previous.receivers,
      });
      rerender();
      store.showToast(`删除失败，已恢复: ${message(e)}`);
    }
  }));

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

  // Live speaker membership: add via picker, remove via row button. The
  // server keeps playing speakers untouched and only stops/starts the delta.
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

  const saveSpeakerMembership = async (groupId: string, speakerIds: string[], toastText: string) => {
    store.set({ saving: true });
    try {
      await api.updateGroup(groupId, { speaker_ids: speakerIds });
      await refreshRuntimeState();
      store.set({ saving: false });
      rerender();
      store.showToast(toastText);
    } catch (e) {
      store.set({ saving: false });
      rerender();
      store.showToast(`保存失败: ${message(e)}`);
    }
  };

  container.querySelectorAll<HTMLInputElement>("[data-speaker-add]").forEach((checkbox) => {
    checkbox.addEventListener("change", async () => {
      if (store.get().saving) return;
      const groupId = checkbox.dataset.speakerAdd!;
      const group = store.get().fullConfig?.groups.find((item) => item.id === groupId);
      if (!group) return;
      const name = checkbox.closest("label")?.querySelector("span")?.textContent ?? "";
      await saveSpeakerMembership(groupId, [...group.speaker_ids, checkbox.value], `已把「${name}」加入组合「${group.name}」`);
    });
  });

  container.querySelectorAll<HTMLElement>("[data-speaker-remove]").forEach((button) => {
    button.addEventListener("click", async () => {
      if (store.get().saving) return;
      const groupId = button.dataset.groupId!;
      const did = button.dataset.speakerRemove!;
      const group = store.get().fullConfig?.groups.find((item) => item.id === groupId);
      if (!group) return;
      await saveSpeakerMembership(
        groupId,
        group.speaker_ids.filter((item) => item !== did),
        `已把「${speakerName(did, store.get())}」从组合「${group.name}」移除`
      );
    });
  });

  container.querySelectorAll<HTMLInputElement>("[data-group-delay]").forEach((input) => {
    input.addEventListener("input", () => {
      const output = input.nextElementSibling as HTMLOutputElement | null;
      if (output) output.value = `${input.value} ms`;
    });
    // Commit on release; mid-drag saves fight the user's finger.
    input.addEventListener("change", async () => {
      const groupId = input.dataset.groupDelay!;
      const speakerId = input.dataset.speakerId!;
      const config = store.get().fullConfig;
      const group = config?.groups.find((item) => item.id === groupId);
      if (!config || !group) return;
      const delays = { ...group.delays_ms, [speakerId]: Number(input.value) };
      const optimistic = { ...group, delays_ms: delays };
      store.set({ fullConfig: replaceGroup(config, optimistic) });
      try {
        const updated = await api.updateGroup(groupId, { delays_ms: delays });
        const latest = store.get().fullConfig;
        if (latest) store.set({ fullConfig: replaceGroup(latest, updated) });
      } catch (e) {
        store.set({ fullConfig: config });
        rerender();
        store.showToast(`延迟保存失败，已恢复: ${message(e)}`);
      }
    });
  });

  container.querySelectorAll<HTMLButtonElement>("[data-group-calibrate]").forEach((button) => {
    button.addEventListener("click", async () => {
      if (store.get().saving) return;
      const groupId = button.dataset.groupCalibrate!;
      const group = store.get().fullConfig?.groups.find((item) => item.id === groupId);
      if (!group) return;
      const calibrationMedia = await confirmCalibration(group.name);
      if (calibrationMedia === null) return;
      button.disabled = true;
      button.textContent = "正在校准…";
      store.set({ saving: true });
      try {
        await api.startGroupCalibration(groupId, calibrationMedia || undefined);
        store.set({ saving: false });
        rerender();
        const saved = await liveCalibration(group, store.get());
        await api.stopGroupCalibration(groupId);
        rerender();
        store.showToast(saved ? "校准已保存" : "已还原校准前的延迟");
      } catch (e) {
        await api.stopGroupCalibration(groupId).catch(() => undefined);
        store.set({ saving: false });
        rerender();
        store.showToast(`校准失败: ${message(e)}`);
      }
    });
  });

  container.querySelectorAll<HTMLElement>("[data-group-anchor]").forEach((button) => {
    button.addEventListener("click", async () => {
      if (store.get().saving) return;
      const groupId = button.dataset.groupAnchor!;
      const speakerId = button.dataset.speakerId!;
      const config = store.get().fullConfig;
      const group = config?.groups.find((item) => item.id === groupId);
      if (!config || !group || group.anchor_did === speakerId) return;
      const actionKey = groupActionKey("anchor", groupId);
      if (pendingGroupActions.has(actionKey)) return;
      pendingGroupActions.add(actionKey);
      const optimistic = rebaseGroup(group, speakerId);
      store.set({ fullConfig: replaceGroup(config, optimistic) });
      rerender();
      try {
        const updated = await api.updateGroup(groupId, { anchor_did: speakerId });
        const latest = store.get().fullConfig;
        if (latest) store.set({ fullConfig: replaceGroup(latest, updated) });
        rerender();
        store.showToast("时间基准已切换，当前播放时序保持不变");
      } catch (e) {
        store.set({ fullConfig: config });
        store.showToast(`设置失败，已恢复: ${message(e)}`);
      } finally {
        pendingGroupActions.delete(actionKey);
        rerender();
      }
    });
  });

  // Stereo mode: switching mode or channel rebuilds the pipelines server-side.
  // Apply the change optimistically so the UI flips instantly, then persist.
  container.querySelectorAll<HTMLElement>("[data-group-mode]").forEach((button) => {
    button.addEventListener("click", async () => {
      const groupId = button.dataset.groupMode!;
      const mode = button.dataset.mode as "mirror" | "stereo";
      const config = store.get().fullConfig;
      const group = config?.groups.find((item) => item.id === groupId);
      if (!config || !group || group.mode === mode) return;
      const actionKey = groupActionKey("mode", groupId);
      if (pendingGroupActions.has(actionKey)) return;
      if (mode === "stereo" && groupMemberCount(group) < 2) {
        store.showToast("立体声模式至少需要两个成员（音箱或网络设备）");
        return;
      }
      pendingGroupActions.add(actionKey);
      const optimistic = mode === "stereo"
        ? Object.fromEntries(group.speaker_ids.map((did, i) => [did, (group.channels?.[did] ?? (i === 0 ? "left" : "right"))]))
        : group.channels;
      store.set({
        fullConfig: {
          ...config,
          groups: config.groups.map((item) =>
            item.id === groupId ? { ...item, mode, channels: optimistic } : item
          ),
        },
      });
      rerender();
      try {
        const updated = await api.updateGroup(groupId, { mode });
        const latest = store.get().fullConfig;
        if (latest) {
          store.set({ fullConfig: { ...latest, groups: latest.groups.map((item) => item.id === groupId ? updated : item) } });
        }
        const status = await api.getStatus();
        store.set({ status, receivers: status.receivers });
        rerender();
        store.showToast(mode === "stereo" ? "已切换为立体声，左右声道各走一条流" : "已切换为镜像播放");
      } catch (e) {
        store.set({ fullConfig: config });
        store.showToast(`切换失败: ${message(e)}`);
      } finally {
        pendingGroupActions.delete(actionKey);
        rerender();
      }
    });
  });

  container.querySelectorAll<HTMLElement>("[data-group-channel]").forEach((button) => {
    button.addEventListener("click", async () => {
      const groupId = button.dataset.groupChannel!;
      const speakerId = button.dataset.speakerId!;
      const channel = button.dataset.channel as "left" | "right" | "both";
      const config = store.get().fullConfig;
      const group = config?.groups.find((item) => item.id === groupId);
      if (!config || !group || group.channels?.[speakerId] === channel) return;
      const actionKey = groupActionKey("channel", groupId, speakerId);
      if (pendingGroupActions.has(actionKey)) return;
      pendingGroupActions.add(actionKey);
      // Channel choice is free per speaker — several may share a side.
      const channels: Record<string, "left" | "right" | "both"> = { ...group.channels, [speakerId]: channel };
      store.set({
        fullConfig: {
          ...config,
          groups: config.groups.map((item) => (item.id === groupId ? { ...item, channels } : item)),
        },
      });
      rerender();
      try {
        const updated = await api.updateGroup(groupId, { channels });
        const latest = store.get().fullConfig;
        if (latest) {
          store.set({ fullConfig: { ...latest, groups: latest.groups.map((item) => item.id === groupId ? updated : item) } });
        }
        const status = await api.getStatus();
        store.set({ status, receivers: status.receivers });
        rerender();
      } catch (e) {
        store.set({ fullConfig: config });
        store.showToast(`声道设置失败: ${message(e)}`);
      } finally {
        pendingGroupActions.delete(actionKey);
        rerender();
      }
    });
  });

  // Loudness trims restart only the encoder server-side (the AirPlay session
  // survives), so they can be tuned while listening. Visuals update on input;
  // the value is committed on release (change) — no mid-drag saves. Signed
  // delay offsets live in the separate [data-group-delay] handler above.
  const bindTrim = (selector: string, format: (value: number) => string) => {
    container.querySelectorAll<HTMLInputElement>(selector).forEach((input) => {
      input.addEventListener("input", () => {
        const output = input.nextElementSibling as HTMLOutputElement | null;
        if (output) output.value = format(Number(input.value));
      });
      input.addEventListener("change", async () => {
        const groupId = input.dataset.groupGain!;
        const speakerId = input.dataset.speakerId!;
        const value = Number(input.value);
        const config = store.get().fullConfig;
        const group = config?.groups.find((item) => item.id === groupId);
        if (!config || !group) return;
        const gains = { ...group.gains_db, [speakerId]: value };
        store.set({ fullConfig: replaceGroup(config, { ...group, gains_db: gains }) });
        try {
          const updated = await api.updateGroup(groupId, { gains_db: gains });
          const latest = store.get().fullConfig;
          if (latest) store.set({ fullConfig: replaceGroup(latest, updated) });
        } catch (e) {
          store.set({ fullConfig: config });
          rerender();
          store.showToast(`响度保存失败，已恢复: ${message(e)}`);
        }
      });
    });
  };
  bindTrim("[data-group-gain]", (v) => `${v > 0 ? "+" : ""}${v.toFixed(1)} dB`);
}

async function refreshRuntimeState() {
  const [fullConfig, status] = await Promise.all([api.getConfig(), api.getStatus()]);
  store.set({ fullConfig, status, receivers: status.receivers });
}
function message(error: unknown) { return error instanceof Error ? error.message : "未知错误"; }

/** Speaker display name: live device list first, then persisted aliases —
 * the latter is available before the device list finishes loading. */
function speakerName(did: string, state: State): string {
  const device = state.devices.find((item) => item.did === did);
  return device?.alias || device?.name || state.fullConfig?.speaker_names?.[did] || did;
}

function targetLabel(receiverId: string, state: State): string {
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

function settingsTargetIds(
  definition: NonNullable<State["fullConfig"]>["receivers"][number],
  state: State
): string[] {
  if (definition.target_type === "selected") {
    return state.fullConfig?.selected_device_id ? [state.fullConfig.selected_device_id] : [];
  }
  return definition.target_id ? [definition.target_id] : [];
}

function compactTargetLabel(receiverName: string, receiverId: string, state: State): string {
  const label = targetLabel(receiverId, state);
  const targetName = label.replace(/^播放到(?:分组)?：/, "");
  return targetName === receiverName ? "" : label;
}

function escapeHtml(text: string): string {
  return text
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
}
