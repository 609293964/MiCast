import type { SpeakerGroup, NetworkDevice } from "../api";
import { icon } from "../icons";
import { api } from "../api";
import { store } from "../state";
import {
  KIND_ICON,
  delayLimit,
  escapeHtml,
  message,
} from "./receivers-shared";

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

// Create-form network selection lives outside the DOM: a status-poll re-render
// rebuilds the whole section and would otherwise wipe half-filled forms.
// Network keys are "proto:id".
const createFormNetworks = new Set<string>();
export const getCreateFormNetworks = () => createFormNetworks;

// The "+ 添加网络设备" picker is a plain <div hidden> toggle — a poll-driven
// re-render rebuilds it closed, so the open state lives here and is rendered
// back into the markup.
const netPickerOpen = new Set<string>();

export function findNetworkDevice(id: string): { device: NetworkDevice; proto: "airplay" | "dlna" } | null {
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
export function networkTargetsInner(group: SpeakerGroup): string {
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

export function renderNetworkTargets(group: SpeakerGroup): string {
  return `<div class="airplay-targets" data-network-targets="${escapeHtml(group.id)}">${networkTargetsInner(group)}</div>`;
}

/** Create-form picker: every discovered device, as checkboxes. */
function createFormPickerInner(): string {
  if (!store.get().fullConfig?.network_discovery_enabled) {
    return "";  // discovery off: no network section, no hint — keep the form quiet
  }
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

export function bindNetworkSections(container: HTMLElement) {
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
  // Discovery off: render attached devices from config, never hit the network.
  if (!store.get().fullConfig?.network_discovery_enabled) {
    networkDevices = { airplay: [], dlna: [] };
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
        store.showToast(`保存失败: ${message(err)}`);
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
        store.showToast(`保存失败: ${message(err)}`);
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
        store.showToast(`声道保存失败: ${message(err)}`);
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
        store.showToast(`延迟保存失败: ${message(err)}`);
      }
    });
  });
}
