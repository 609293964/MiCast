import type { Device, PlaybackState, SpeakerEq } from "../api";
import { api } from "../api";
import { store } from "../state";
import { brandIcon, icon } from "../icons";

// 10-band EQ: must match the backend (micast/config.py EQ_BANDS_HZ / EQ_PRESETS).
// The preset list is refreshed from /api/devices/eq/presets when available.
const EQ_FREQ_LABELS = ["31", "62", "125", "250", "500", "1k", "2k", "4k", "8k", "16k"];
export const EQ_PRESET_LABELS: Record<string, string> = {
  flat: "平直",
  bass: "低音增强",
  vocal: "人声清晰",
  night: "夜间模式",
  live: "现场感",
};
const FALLBACK_EQ_PRESETS: Record<string, number[]> = {
  flat: [0, 0, 0, 0, 0, 0, 0, 0, 0, 0],
  bass: [4, 5, 4, 2, 1, 0, 0, -1, 0, 0],
  vocal: [-2, -1, 0, 0, 1, 3, 2, 2, 1, 0],
  night: [-4, -4, -3, -2, -1, 0, 0, -1, -2, -3],
  live: [2, 1, 0, 0, 0, 1, 1, 2, 3, 3],
};
let eqPresets: Record<string, number[]> = FALLBACK_EQ_PRESETS;
let eqPresetsLoaded = false;

function loadEqPresets() {
  if (eqPresetsLoaded) return;
  eqPresetsLoaded = true;
  api
    .getEqPresets()
    .then((r) => {
      eqPresets = r.presets;
    })
    .catch(() => {
      eqPresetsLoaded = false;
    });
}

interface DeviceVisual {
  html: string;
  className: string;
}

const PRODUCT_IMAGES: Record<string, string> = {
  lx01: "assets/devices/xiaomi-wifispeaker-lx01.png",
  lx06: "assets/devices/xiaomi-wifispeaker-lx06-card.png",
  l06a: "assets/devices/xiaomi-wifispeaker-l06a.png",
  s12: "assets/devices/xiaomi-wifispeaker-s12.png",
  l16a: "assets/devices/xiaomi-wifispeaker-l16a.png",
  oh2: "assets/devices/xiaomi-wifispeaker-oh2-card.png",
  oh2p: "assets/devices/xiaomi-wifispeaker-oh2p-card.png",
  l05b: "assets/devices/xiaomi-wifispeaker-l05b.png",
  l05c: "assets/devices/xiaomi-wifispeaker-l05c.png",
  l15a: "assets/devices/xiaomi-wifispeaker-l15a.png",
  l17a: "assets/devices/xiaomi-wifispeaker-l17a.png",
};

interface DevicesProps {
  devices: Device[];
  expandedDid: string | null;
  status: string;
  pcmSource: string;
  streamUrl: string;
  loggedIn: boolean;
  loadError: string | null;
  playback: PlaybackState | null;
}

export function renderDevicesView(props: DevicesProps): string {
  const { devices, expandedDid, status, loggedIn, loadError, playback } = props;
  const onlineCount = devices.filter((item) => item.presence === "online").length;
  const offlineCount = devices.filter((item) => item.presence === "offline").length;
  const unknownCount = devices.length - onlineCount - offlineCount;
  const statusClass = onlineCount > 0
    ? offlineCount > 0 || unknownCount > 0 ? "warning" : "running"
    : devices.length ? (offlineCount === devices.length ? "error" : "warning") : "";
  const statusText = devices.length
    ? offlineCount > 0 ? `${offlineCount} 台离线` : unknownCount > 0 ? `${unknownCount} 台待确认` : "全部在线"
    : status === "error" ? "加载失败" : "尚无设备";

  return `
    <div class="page-heading">
      <h2 class="page-title">音箱</h2>
      <p>查看音箱状态、调整音量和修改显示名称。</p>
    </div>

    <div class="group-header">音箱状态</div>
    <div class="group">
      <div class="cell">
        <div class="cell-icon blue">${icon("wave")}</div>
        <div class="cell-content">
          <span class="cell-title">${devices.length ? `已发现 ${devices.length} 台音箱` : "尚未发现音箱"}</span>
          <span class="cell-subtitle">${devices.length ? `${onlineCount} 台在线${offlineCount ? `，${offlineCount} 台离线` : ""}${unknownCount ? `，${unknownCount} 台待确认` : ""}` : "登录后会自动显示账号下的音箱"}</span>
        </div>
        <span class="status-pill ${statusClass}" data-status-label>${statusText}</span>
      </div>
      ${renderMasterVolume(devices, playback)}
    </div>

    <div class="group-header">我的音箱</div>
    <div class="device-list">
      ${devices.length === 0
        ? `
            <div class="group">
              <div class="empty-state">
                <div class="empty-state-icon">${icon("speaker")}</div>
                <span class="body">${loadError ? "设备加载失败" : "暂无设备"}</span>
                <span class="caption">${loadError || (loggedIn ? "当前账号下未发现支持的音箱" : "米家连接已失效，请重新连接")}</span>
              </div>
            </div>
          `
        : devices
            .map((d) => renderDeviceCard(d, expandedDid === d.did, playback))
            .join("")}
    </div>
  `;
}

function deviceVolume(device: Device, playback: PlaybackState | null): number {
  const live = playback?.devices.find((item) => item.did === device.did);
  return device.volume ?? live?.volume ?? 50;
}

function deviceMuted(device: Device, playback: PlaybackState | null): boolean {
  const live = playback?.devices.find((item) => item.did === device.did);
  return live?.muted ?? device.muted ?? false;
}

function renderMasterVolume(devices: Device[], playback: PlaybackState | null): string {
  const online = devices.filter((d) => d.presence === "online");
  if (online.length < 2) return "";
  const volumes = online.map((d) => deviceVolume(d, playback));
  const volume = Math.round(volumes.reduce((sum, v) => sum + v, 0) / volumes.length);
  const mixed = new Set(volumes).size > 1;
  const allMuted = online.length > 0 && online.every((d) => deviceMuted(d, playback));
  const dids = online.map((d) => d.did).join(",");
  return `
    <div class="cell">
      <div class="cell-icon ${mixed ? "gray" : "blue"}" data-master-icon>${icon("speaker")}</div>
      <div class="cell-content">
        <span class="cell-title">全部音量</span>
        <span class="cell-subtitle" data-master-subtitle>${mixed ? `${online.length} 台音箱音量不同` : `${online.length} 台音箱`}</span>
      </div>
      <label class="volume-control device-volume-inline ${mixed ? "mixed" : ""}" title="全部音箱音量" data-master-control>
        <span class="volume-icon">${volume === 0 ? "×" : icon("speaker")}</span>
        <input type="range" min="0" max="100" value="${volume}" data-master-volume="${escapeHtml(dids)}" aria-label="全部音箱音量" style="--volume:${volume}%">
        <output>${volume}</output>
      </label>
      <button type="button" class="icon-button compact" data-mute="${escapeHtml(dids)}" aria-pressed="${allMuted}" aria-label="${allMuted ? "取消全部静音" : "全部静音"}" title="${allMuted ? "取消全部静音" : "全部静音"}">${icon(allMuted ? "mute" : "speaker")}</button>
    </div>
    <div class="cell">
      <div class="cell-content"><span class="cell-title">相对调节</span><span class="cell-subtitle">保留音量差，每次 5 格</span></div>
      <div class="settings-inline-control"><button class="button secondary" data-volume-step="-5" data-volume-targets="${escapeHtml(dids)}" aria-label="全部音箱降低 5 格">−5</button><button class="button secondary" data-volume-step="5" data-volume-targets="${escapeHtml(dids)}" aria-label="全部音箱提高 5 格">+5</button></div>
    </div>
  `;
}

function renderDeviceCard(device: Device, expanded: boolean, playback: PlaybackState | null): string {
  const isOnline = device.presence === "online";
  const displayName = device.alias || device.name;
  const volume = deviceVolume(device, playback);
  const muted = deviceMuted(device, playback);
  const visual = deviceVisual(device);

  return `
    <div class="device-card ${expanded ? "expanded" : ""}" data-did="${device.did}">
      <div class="device-card-header" data-device-header>
        <div class="cell-icon device-brand ${isOnline ? visual.className : "gray"}">${visual.html}</div>
        <div class="device-info">
          <span class="device-name">${escapeHtml(displayName)}</span>
          <span class="device-meta">${escapeHtml(device.name)} · ${device.model} · ${isOnline ? "在线" : "离线"}</span>
        </div>
        <div class="device-actions">
          <label class="volume-control device-volume-inline" title="${escapeHtml(displayName)}音量">
            <span class="volume-icon">${icon("speaker")}</span>
            <input type="range" min="0" max="100" value="${volume}" data-device-volume="${escapeHtml(device.did)}" aria-label="${escapeHtml(displayName)}音量" style="--volume:${volume}%">
            <output>${volume}</output>
          </label>
          <button type="button" class="icon-button compact" data-mute="${escapeHtml(device.did)}" aria-pressed="${muted}" aria-label="${muted ? "取消静音" : "静音"}" title="${muted ? "取消静音" : "静音"}">${icon(muted ? "mute" : "speaker")}</button>
          <span class="device-expand-icon ${expanded ? "expanded" : ""}" aria-hidden="true">${icon("chevron")}</span>
        </div>
      </div>
      ${expanded ? renderDeviceDetails(device, playback) : ""}
    </div>
  `;
}

function deviceVisual(device: Device): DeviceVisual {
  const productImage = PRODUCT_IMAGES[device.model.toLowerCase()];
  if (productImage) {
    return {
      html: `<img class="device-product-image" src="${productImage}" alt="">`,
      className: "device-product",
    };
  }
  const identity = `${device.name} ${device.model}`.toLowerCase();
  if (identity.includes("xiaomi") || identity.includes("redmi") || identity.includes("小爱")) {
    return { html: brandIcon("xiaomi"), className: "xiaomi" };
  }
  return { html: icon("speaker"), className: "blue" };
}

function renderDeviceDetails(device: Device, _playback: PlaybackState | null): string {
  return `
    <div class="device-card-body">
      <div class="device-detail-row">
        <span class="caption">显示名称</span>
        <input type="text" class="alias-input" value="${escapeHtml(device.alias || device.name)}" placeholder="${escapeHtml(device.name)}" required maxlength="64" data-alias-input style="max-width: 220px;" aria-label="${escapeHtml(device.alias || device.name)}的音箱别名与 AirPlay 名称">
      </div>
      <div class="device-detail-row">
        <span class="caption">原生型号</span>
        <span class="cell-value">${escapeHtml(device.model)}</span>
      </div>
      <div class="device-detail-row">
        <span class="caption">设备 ID</span>
        <span class="cell-value footnote">${escapeHtml(device.did)}</span>
      </div>
      ${renderEqSection(device)}
    </div>
  `;
}

function renderEqSection(device: Device): string {
  const eq: SpeakerEq = device.eq ?? { enabled: false, bands: EQ_FREQ_LABELS.map(() => 0), preset: "" };
  const bands = EQ_FREQ_LABELS.map((_, i) => eq.bands[i] ?? 0);
  const presetKey = eq.preset && eq.preset in EQ_PRESET_LABELS ? eq.preset : "";
  return `
    <div class="device-detail-row eq-title-row">
      <span class="caption">均衡器 EQ</span>
      <input type="checkbox" class="switch" data-eq-toggle ${eq.enabled ? "checked" : ""} aria-label="启用均衡器">
    </div>
    <div class="eq-panel ${eq.enabled ? "" : "disabled"}" data-eq-panel>
      <div class="eq-presets" role="group" aria-label="EQ 预设">
        ${Object.entries(EQ_PRESET_LABELS)
          .map(
            ([key, label]) => `
          <button type="button" class="eq-preset-chip ${presetKey === key ? "active" : ""}" data-eq-preset="${key}">${label}</button>`
          )
          .join("")}
      </div>
      <div class="eq-bands">
        ${bands
          .map(
            (gain, i) => `
          <label class="eq-band">
            <output>${formatGain(gain)}</output>
            <input type="range" min="-12" max="12" step="0.5" value="${gain}" data-eq-band="${i}" aria-label="${EQ_FREQ_LABELS[i]}Hz 增益">
            <span class="eq-freq">${EQ_FREQ_LABELS[i]}</span>
          </label>`
          )
          .join("")}
      </div>
      <span class="caption eq-hint">仅作用于这台音箱；不同 EQ 的音箱会使用独立音频流</span>
    </div>
  `;
}

function formatGain(gain: number): string {
  return `${gain > 0 ? "+" : ""}${gain}`;
}

function bindEqSection(container: HTMLElement) {
  loadEqPresets();
  container.querySelectorAll<HTMLElement>("[data-did]").forEach((card) => {
    const did = card.dataset.did!;
    const toggle = card.querySelector<HTMLInputElement>("[data-eq-toggle]");
    const panel = card.querySelector<HTMLElement>("[data-eq-panel]");
    if (!toggle || !panel) return;
    const bandInputs = Array.from(card.querySelectorAll<HTMLInputElement>("[data-eq-band]"));
    const chips = Array.from(card.querySelectorAll<HTMLElement>("[data-eq-preset]"));

    const currentBands = () =>
      bandInputs
        .sort((a, b) => Number(a.dataset.eqBand) - Number(b.dataset.eqBand))
        .map((input) => Number(input.value));

    const matchingPreset = (bands: number[]) =>
      Object.entries(eqPresets).find(([, gains]) =>
        EQ_FREQ_LABELS.every((_, i) => Math.abs((gains[i] ?? 0) - bands[i]) < 0.01)
      )?.[0] ?? "";

    const markChips = (preset: string) => {
      chips.forEach((chip) =>
        chip.classList.toggle("active", chip.dataset.eqPreset === preset)
      );
    };

    const save = () => {
      const bands = currentBands();
      const payload: SpeakerEq = {
        enabled: toggle.checked,
        bands,
        preset: matchingPreset(bands),
      };
      // Sliders already show the value; save silently so the panel never
      // re-renders mid-drag (the classic "slider jumps" bug).
      api.setDeviceEq(did, payload).catch((e) => {
        store.showToast(`EQ 保存失败: ${e instanceof Error ? e.message : "未知错误"}`);
      });
    };

    toggle.addEventListener("change", () => {
      panel.classList.toggle("disabled", !toggle.checked);
      save();
    });

    chips.forEach((chip) => {
      chip.addEventListener("click", () => {
        const key = chip.dataset.eqPreset!;
        const gains = eqPresets[key];
        if (!gains) return;
        bandInputs.forEach((input, i) => {
          input.value = String(gains[i] ?? 0);
          const output = input.parentElement?.querySelector("output");
          if (output) output.value = formatGain(gains[i] ?? 0);
        });
        markChips(key);
        save();
      });
    });

    bandInputs.forEach((input) => {
      input.addEventListener("input", () => {
        const output = input.parentElement?.querySelector("output");
        if (output) output.value = formatGain(Number(input.value));
        markChips(matchingPreset(currentBands()));
      });
      // Commit on release: mid-drag saves make the server value round-trip
      // back into the control and the thumb lags behind the finger.
      input.addEventListener("change", save);
    });
  });
}

export function bindDevicesView(
  container: HTMLElement,
  onExpandedChange: (did: string | null) => void
) {
  bindEqSection(container);
  container.querySelectorAll("[data-device-header]").forEach((el) => {
    el.addEventListener("click", (e) => {
      // Don't toggle expand when interacting with a slider, switch or button.
      const target = e.target as HTMLElement;
      if (target.closest("input, button")) return;

      const card = el.closest("[data-did]") as HTMLElement | null;
      const did = card?.dataset.did;
      if (!did) return;

      const current = store.get().ui.expandedDeviceDid;
      onExpandedChange(current === did ? null : did);
    });
  });

  container.querySelectorAll("[data-alias-input]").forEach((el) => {
    const input = el as HTMLInputElement;
    const saveAlias = async () => {
      const card = input.closest("[data-did]") as HTMLElement | null;
      const did = card?.dataset.did;
      if (!did) return;
      const alias = input.value.trim();
      if (!alias) {
        store.showToast("名称不能为空");
        input.value = input.defaultValue;
        return;
      }
      if (store.get().saving) return;
      store.set({ saving: true });
      try {
        await api.setAlias(did, alias);
        const [devices, fullConfig, status] = await Promise.all([api.getDevices(), api.getConfig(), api.getStatus()]);
        store.set({ devices, fullConfig, status, receivers: status.receivers, saving: false });
        store.showToast("名称已更新，AirPlay 列表将自动刷新");
      } catch (err) {
        store.set({ saving: false });
        store.showToast(`保存失败: ${err instanceof Error ? err.message : "未知错误"}`);
      }
    };

    input.addEventListener("blur", saveAlias);
    input.addEventListener("keydown", (e) => {
      if (e.key === "Enter") {
        e.preventDefault();
        input.blur();
      }
    });
  });

  container.querySelectorAll<HTMLInputElement>("[data-device-volume]").forEach((input) => {
    input.addEventListener("input", () => {
      const value = Number(input.value);
      input.style.setProperty("--volume", `${value}%`);
      const output = input.nextElementSibling as HTMLOutputElement | null;
      if (output) output.value = String(value);
    });
    // Commit on release only — keeps the thumb glued to the finger.
    input.addEventListener("change", async () => {
      try {
        await api.setVolume(Number(input.value), [input.dataset.deviceVolume!]);
        store.showToast("音箱音量已调整");
      } catch (e) {
        store.showToast(`音量设置失败: ${e instanceof Error ? e.message : "未知错误"}`);
      }
    });
  });

  const master = container.querySelector<HTMLInputElement>("[data-master-volume]");
  if (master) {
    const dids = (master.dataset.masterVolume || "").split(",").filter(Boolean);
    const output = master.nextElementSibling as HTMLOutputElement | null;
    master.addEventListener("input", () => {
      const value = Number(master.value);
      master.style.setProperty("--volume", `${value}%`);
      if (output) output.value = String(value);
      // Dragging means "unify": light the control immediately.
      container.querySelector<HTMLElement>("[data-master-control]")?.classList.remove("mixed");
      // Mirror the change on each per-device slider immediately for feedback.
      container.querySelectorAll<HTMLInputElement>("[data-device-volume]").forEach((input) => {
        if (!dids.includes(input.dataset.deviceVolume!)) return;
        input.value = String(value);
        input.style.setProperty("--volume", `${value}%`);
        const deviceOutput = input.nextElementSibling as HTMLOutputElement | null;
        if (deviceOutput) deviceOutput.value = String(value);
      });
    });
    master.addEventListener("change", async () => {
      const value = Number(master.value);
      try {
        await api.setVolume(value, dids);
        // All targets now share one volume: light the control up in place.
        const control = container.querySelector<HTMLElement>("[data-master-control]");
        control?.classList.remove("mixed");
        const iconCell = container.querySelector<HTMLElement>("[data-master-icon]");
        iconCell?.classList.remove("gray");
        iconCell?.classList.add("blue");
        const subtitle = container.querySelector<HTMLElement>("[data-master-subtitle]");
        if (subtitle) subtitle.textContent = `同时调整 ${dids.length} 台在线音箱`;
      } catch (e) {
        store.showToast(`音量调整失败: ${e instanceof Error ? e.message : "未知错误"}`);
      }
    });
  }
}

function escapeHtml(text: string): string {
  return text
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
}
