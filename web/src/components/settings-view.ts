import type { AccessStatus, AirPlayProtocol, AudioConfig, FullConfig } from "../api";
import { api } from "../api";
import { store, type Theme } from "../state";
import { icon } from "../icons";

interface SettingsProps {
  audio: AudioConfig | null;
  config: FullConfig | null;
  appName: string;
  protocol: AirPlayProtocol;
  airplay2Enabled: boolean;
  airplay2Available: boolean;
  dlnaEnabled: boolean;
  dlnaStatus: { status: string; detail: string } | null;
  syncGroupsEnabled: boolean;
  theme: Theme;
  status: string;
  xiaomiLoggedIn: boolean;
  deviceCount: number;
  access: AccessStatus | null;
  saving?: boolean;
}

const formats: Array<AudioConfig["format"]> = ["mp3", "flac", "wav"];
const bitrates: Array<AudioConfig["bitrate"]> = ["128k", "192k", "320k"];
const sampleRates: Array<AudioConfig["sample_rate"]> = [44100, 48000];

// Audio changes coalesce module-wide (survives re-renders): rapid clicks apply
// the optimistic UI immediately and only the final state restarts the encoder.
let audioQueue: Partial<AudioConfig> | null = null;
let audioBusy = false;
let lastConfirmedAudio: AudioConfig | null = null;

export function renderSettingsView(props: SettingsProps): string {
  const {
    audio, config, appName, airplay2Enabled, airplay2Available, dlnaEnabled, dlnaStatus,
    syncGroupsEnabled, theme, status, xiaomiLoggedIn, deviceCount, access,
  } = props;
  if (audio && (!lastConfirmedAudio || !audioBusy)) lastConfirmedAudio = audio;
  const statusLabel = status === "running"
    ? "系统就绪"
    : status === "degraded"
      ? "部分可用"
      : status === "error"
        ? "播放功能不可用"
        : "正在准备";

  const transcoding = audio?.auto_transcode ?? true;

  const formatHtml = audio
    ? renderSegments(
        "format",
        formats.map((f) => ({ value: f, label: f.toUpperCase(), active: audio.format === f })),
        !transcoding
      )
    : "";

  const bitrateHtml = audio
    ? renderSegments(
        "bitrate",
        bitrates.map((b) => ({ value: b, label: b.replace("k", ""), active: audio.bitrate === b })),
        !transcoding || audio.format !== "mp3"
      )
    : "";

  const sampleRateHtml = audio
    ? renderSegments(
        "samplerate",
        sampleRates.map((sr) => ({ value: String(sr), label: `${sr / 1000}k`, active: audio.sample_rate === sr })),
        !transcoding
      )
    : "";

  return `
    <div class="page-heading">
      <h2 class="page-title">设置</h2>
      <p>管理播放方式、音质与实验功能。</p>
    </div>

    <div class="hero-card ${status === "error" ? "error" : status === "degraded" ? "warning" : ""}">
      <span class="caption">当前状态</span>
      <div class="title-1">${statusLabel}</div>
      <span class="caption">${audio ? (transcoding ? `${audio.format.toUpperCase()} · ${audio.bitrate} · ${audio.sample_rate / 1000} kHz` : "PCM 直出 · 不转码") : "加载中…"}</span>
    </div>

    <div class="group-header">音频编码</div>
    <div class="group">
      <div class="cell">
        <div class="cell-content">
          <span class="cell-title">转码</span>
          <span class="cell-subtitle">${transcoding ? "按下方设置编码后输出" : "已关闭，PCM 原始音频直出"}</span>
        </div>
        <input type="checkbox" class="switch" id="auto-transcode" ${audio?.auto_transcode ? "checked" : ""} aria-label="开启转码">
      </div>
      ${renderCell("格式", transcoding ? "MP3 兼容性最好，FLAC/WAV 为无损" : "转码已关闭，此项不生效", formatHtml)}
      ${renderCell("码率", !transcoding ? "转码已关闭，此项不生效" : audio?.format === "mp3" ? "仅对 MP3 生效" : "当前格式不使用码率", bitrateHtml)}
      ${renderCell("采样率", transcoding ? "AirPlay 默认 48 kHz" : "转码已关闭，此项不生效", sampleRateHtml)}
    </div>

    <div class="group-header">应用</div>
    <div class="group">
      <div class="cell">
        <div class="cell-content">
          <span class="cell-title">应用名称</span>
          <span class="cell-subtitle">仅用于网页标题；播放名称跟随音箱或组合名称</span>
        </div>
        <input type="text" class="input" id="app-name-input" value="${escapeHtml(appName)}" placeholder="MiCast" style="max-width: 160px;">
      </div>
      <div class="cell">
        <div class="cell-content">
          <span class="cell-title">外观</span>
          <span class="cell-subtitle">浅色 / 深色 / 跟随系统</span>
        </div>
        ${renderSegments(
          "theme",
          [
            { value: "light", label: "浅色", active: theme === "light" },
            { value: "dark", label: "深色", active: theme === "dark" },
            { value: "auto", label: "自动", active: theme === "auto" },
          ]
        )}
      </div>
    </div>

    <div class="group-header">服务</div>
    <div class="group">
      <button class="cell settings-link" type="button" data-open-account>
        <div class="cell-icon blue">${icon("link")}</div>
        <div class="cell-content">
          <span class="cell-title">米家</span>
          <span class="cell-subtitle">${xiaomiLoggedIn ? `已连接${deviceCount ? ` · ${deviceCount} 台音箱` : ""}` : "未连接，连接后自动同步音箱"}</span>
        </div>
        <span class="settings-link-arrow" aria-hidden="true">›</span>
      </button>
    </div>

    <div class="group-header">管理访问</div>
    <div class="group">
      <button class="cell settings-link" type="button" data-access-settings-toggle>
        <div class="cell-icon blue">${icon("lock")}</div>
        <div class="cell-content">
          <span class="cell-title">账号与密码</span>
          <span class="cell-subtitle">${access?.auth_enabled ? `已启用 · ${escapeHtml(access.username)}` : "未启用，局域网内可直接访问"}</span>
        </div>
        <span class="settings-link-arrow" aria-hidden="true">›</span>
      </button>
      <form class="access-settings-form" data-access-settings hidden>
        <label class="choice-row ${access?.auth_enabled ? "selected" : ""}"><input type="radio" name="access_mode" value="protected" ${access?.auth_enabled ? "checked" : ""}><span><strong>使用账号密码</strong><small>其他设备将重新登录</small></span></label>
        <div class="access-settings-fields" ${access?.auth_enabled ? "" : "hidden"}>
          <label>用户名<input class="input" name="username" maxlength="64" autocomplete="username" value="${escapeHtml(access?.username ?? "admin")}"></label>
          <label>新密码<input class="input" name="password" type="password" minlength="6" autocomplete="new-password" placeholder="至少 6 个字符"></label>
          <label>确认新密码<input class="input" name="password_confirm" type="password" minlength="6" autocomplete="new-password"></label>
        </div>
        <label class="choice-row warning-choice ${!access?.auth_enabled ? "selected" : ""}"><input type="radio" name="access_mode" value="open" ${!access?.auth_enabled ? "checked" : ""}><span><strong>不设置管理账号</strong><small>局域网内无需登录</small></span></label>
        <div class="group-form-actions"><button class="button primary" type="submit">保存管理访问</button></div>
        <p class="settings-form-status" data-access-settings-status aria-live="polite"></p>
      </form>
      ${access?.auth_enabled && access.authenticated ? `
        <button class="cell settings-link access-logout-row" type="button" data-access-logout>
          <div class="cell-icon">${icon("lock")}</div>
          <div class="cell-content">
            <span class="cell-title danger-text">退出登录</span>
            <span class="cell-subtitle">仅退出当前浏览器</span>
          </div>
        </button>
      ` : ""}
    </div>

    <div class="group-header">播放方式</div>
    <div class="group">
      <div class="cell">
        <div class="cell-icon blue">${icon("airplay")}</div>
        <div class="cell-content">
          <span class="cell-title">AirPlay</span>
          <span class="cell-subtitle">让音箱显示在 AirPlay 播放列表中</span>
        </div>
        <span class="plain-state success">已开启</span>
      </div>
      <div class="cell">
        <div class="cell-icon blue">${icon("cast")}</div>
        <div class="cell-content">
          <span class="cell-title">DLNA</span>
          <span class="cell-subtitle">让音箱显示在支持 DLNA 的应用中</span>
        </div>
        <input type="checkbox" class="switch" id="dlna-enabled" ${dlnaEnabled ? "checked" : ""} aria-label="开启 DLNA">
      </div>
    </div>
    ${dlnaEnabled && dlnaStatus?.status === "error" ? `<div class="inline-notice error"><strong>DLNA 暂不可用</strong><span>请检查 MiCast 的网络访问权限后重试。</span></div>` : ""}

    <div class="group-header">播放增强</div>
    <div class="group">
      <div class="cell">
        <div class="cell-content">
          <span class="cell-title">触屏歌词与封面</span>
          <span class="cell-subtitle">为带屏音箱匹配封面和滚动歌词</span>
        </div>
        <input type="checkbox" class="switch" id="touchscreen-lyrics" ${config?.touchscreen_lyrics ? "checked" : ""} aria-label="开启触屏歌词与封面">
      </div>
      <div class="cell">
        <div class="cell-content">
          <span class="cell-title">起播音量</span>
          <span class="cell-subtitle">开始播放时设置音箱音量；关闭则保持原音量</span>
        </div>
        <div class="settings-inline-control">
          <input type="checkbox" class="switch" id="default-volume-enabled" ${config?.default_volume_enabled ? "checked" : ""} aria-label="启用起播音量">
          <input type="number" class="input settings-number" id="default-volume" min="0" max="100" step="5" value="${config?.default_volume ?? 0}" ${config?.default_volume_enabled ? "" : "disabled"} aria-label="起播音量">
        </div>
      </div>
      <div class="cell">
        <div class="cell-content">
          <span class="cell-title">投放音量控制</span>
            <span class="cell-subtitle">独立音量保留音箱设置；音量联动直接控制音箱</span>
        </div>
        <select class="input" id="sender-volume-mode" aria-label="投放音量控制" style="max-width: 10rem">
          <option value="independent" ${config?.sender_volume_mode !== "linked" ? "selected" : ""}>独立音量</option>
          <option value="linked" ${config?.sender_volume_mode === "linked" ? "selected" : ""}>音量联动</option>
        </select>
      </div>
      <div class="cell settings-input-cell">
        <div class="cell-content">
          <span class="cell-title">登录失效通知</span>
          <span class="cell-subtitle">小米登录失效时发送提醒；留空关闭</span>
        </div>
        <input type="url" class="input" id="notify-webhook" placeholder="https://open.feishu.cn/open-apis/bot/v2/hook/…" value="${escapeHtml(config?.notify_webhook_url ?? "")}" aria-label="通知 Webhook 地址">
        <div class="settings-save-row"><span class="caption" id="notify-webhook-status" aria-live="polite"></span><button class="button secondary" id="notify-webhook-save" type="button">保存通知地址</button></div>
      </div>
    </div>

    <div class="group-header">实验功能</div>
    <div class="group">
      <div class="cell">
        <div class="cell-content">
          <span class="cell-title">音箱组合 <span class="feature-badge">实验性</span></span>
          <span class="cell-subtitle">让两台或更多音箱一起播放；关闭后仍会保留已有组合</span>
        </div>
        <input type="checkbox" class="switch" id="sync-groups-enabled" ${syncGroupsEnabled ? "checked" : ""} aria-label="开启音箱组合">
      </div>
      <div class="cell">
        <div class="cell-content">
          <span class="cell-title">大延迟</span>
          <span class="cell-subtitle">将音箱组合的延迟调节范围从 ±5 秒扩大到 ±15 秒</span>
        </div>
        <input type="checkbox" class="switch" id="large-delay-enabled" ${config?.large_delay_enabled ? "checked" : ""} aria-label="开启大延迟范围">
      </div>
      <div class="cell">
        <div class="cell-content">
          <span class="cell-title">AirPlay 2 <span class="feature-badge">实验性</span></span>
          <span class="cell-subtitle">${airplay2Available ? (config?.airplay2_mode === "single" ? "启用一个独立的 AirPlay 2 播放入口" : "为音箱创建独立的 AirPlay 2 播放入口") : "当前安装方式不支持此功能"}</span>
        </div>
        <input type="checkbox" class="switch" id="airplay2-enabled" ${airplay2Enabled ? "checked" : ""} ${airplay2Available ? "" : "disabled"} aria-label="开启 AirPlay 2">
      </div>
      ${airplay2Available && airplay2Enabled ? `<button class="cell settings-link" type="button" data-open-airplay2>
        <div class="cell-icon blue">${icon("airplay")}</div>
        <div class="cell-content">
          <span class="cell-title">AirPlay 2 管理</span>
          <span class="cell-subtitle">${config?.airplay2_mode === "single" ? "设置播放名称、状态和目标音箱" : "管理播放入口及其对应音箱"}</span>
        </div>
        <span class="settings-link-arrow" aria-hidden="true">›</span>
      </button>` : ""}
    </div>

    <p class="footnote" style="margin: var(--space-md) var(--space-lg);">修改会自动保存，并在当前音频输出中重新应用。</p>
  `;
}

function renderCell(title: string, subtitle: string, control: string): string {
  return `
    <div class="cell">
      <div class="cell-content">
        <span class="cell-title">${title}</span>
        <span class="cell-subtitle">${subtitle}</span>
      </div>
      ${control}
    </div>
  `;
}

function renderSegments(
  group: string,
  items: Array<{ value: string; label: string; active: boolean }>,
  disabled = false
): string {
  return `
    <div class="segmented-control" role="group" aria-label="${group}" ${disabled ? 'style="opacity:0.5"' : ""}>
      ${items
        .map(
          (item) => `
            <button class="segment ${item.active ? "active" : ""}"
                    data-${group}="${item.value}"
                    ${disabled ? "disabled" : ""}>${item.label}</button>
          `
        )
        .join("")}
    </div>
  `;
}

export function bindSettingsView(
  container: HTMLElement,
  onThemeChange: (theme: Theme) => void,
  onStateChange: () => void,
  onOpenAirPlay2: () => void,
  onOpenAccount: () => void
) {
  container.querySelector("[data-open-airplay2]")?.addEventListener("click", onOpenAirPlay2);
  container.querySelector("[data-open-account]")?.addEventListener("click", onOpenAccount);
  const accessForm = container.querySelector<HTMLFormElement>("[data-access-settings]");
  container.querySelector("[data-access-settings-toggle]")?.addEventListener("click", () => {
    if (accessForm) accessForm.hidden = !accessForm.hidden;
  });
  const syncAccessForm = () => {
    const protectedMode = accessForm?.querySelector<HTMLInputElement>('input[name="access_mode"]:checked')?.value === "protected";
    const fields = accessForm?.querySelector<HTMLElement>(".access-settings-fields");
    if (fields) fields.hidden = !protectedMode;
    accessForm?.querySelectorAll<HTMLElement>(".choice-row").forEach((row) => row.classList.toggle("selected", (row.querySelector("input") as HTMLInputElement)?.checked));
  };
  accessForm?.querySelectorAll<HTMLInputElement>('input[name="access_mode"]').forEach((input) => input.addEventListener("change", syncAccessForm));
  accessForm?.addEventListener("submit", async (event) => {
    event.preventDefault();
    const data = new FormData(accessForm);
    const enabled = data.get("access_mode") === "protected";
    const button = accessForm.querySelector<HTMLButtonElement>('button[type="submit"]');
    if (button) { button.disabled = true; button.textContent = "正在保存…"; }
    try {
      await api.updateAccess({
        auth_enabled: enabled,
        username: String(data.get("username") || "admin"),
        password: enabled ? String(data.get("password") || "") : "",
        password_confirm: enabled ? String(data.get("password_confirm") || "") : "",
      });
      store.set({ access: await api.getAccessStatus() });
      store.showToast(enabled ? "管理访问已启用" : "管理访问已关闭");
      onStateChange();
    } catch (error) {
      store.showToast(`保存失败: ${error instanceof Error ? error.message : "未知错误"}`);
      if (button) { button.disabled = false; button.textContent = "保存管理访问"; }
    }
  });
  container.querySelector<HTMLButtonElement>("[data-access-logout]")?.addEventListener("click", async (event) => {
    const button = event.currentTarget as HTMLButtonElement;
    button.disabled = true;
    button.setAttribute("aria-busy", "true");
    try {
      await api.logoutAccess();
      window.location.reload();
    } catch (error) {
      button.disabled = false;
      button.removeAttribute("aria-busy");
      store.showToast(`退出失败: ${error instanceof Error ? error.message : "未知错误"}`);
    }
  });
  const updateAudio = async (changes: Partial<AudioConfig>) => {
    audioQueue = { ...(audioQueue ?? {}), ...changes };
    // Optimistic UI for every click, even while a save is in flight.
    const current = store.get().audio;
    if (current) {
      store.set({ audio: { ...current, ...changes }, saving: true });
      onStateChange();
    }
    if (audioBusy) return;
    audioBusy = true;
    try {
      while (audioQueue) {
        const batch = audioQueue;
        audioQueue = null;
        try {
          const updated = await api.setAudioConfig(batch);
          lastConfirmedAudio = updated;
          const status = await api.getStatus();
          store.set({ audio: updated, status, receivers: status.receivers });
          onStateChange();
        } catch (e) {
          audioQueue = null;
          if (lastConfirmedAudio) {
            store.set({ audio: lastConfirmedAudio });
            onStateChange();
          }
          store.showToast(`保存失败: ${e instanceof Error ? e.message : "未知错误"}`);
          break;
        }
      }
      store.showToast("设置已保存");
    } finally {
      audioBusy = false;
      store.set({ saving: false });
    }
  };

  container.querySelectorAll("[data-format]").forEach((el) => {
    el.addEventListener("click", () => {
      const format = (el as HTMLElement).dataset.format as AudioConfig["format"];
      updateAudio({ format });
    });
  });

  container.querySelectorAll("[data-bitrate]").forEach((el) => {
    el.addEventListener("click", () => {
      const bitrate = (el as HTMLElement).dataset.bitrate as AudioConfig["bitrate"];
      updateAudio({ bitrate });
    });
  });

  container.querySelectorAll("[data-samplerate]").forEach((el) => {
    el.addEventListener("click", () => {
      const sampleRate = Number((el as HTMLElement).dataset.samplerate) as AudioConfig["sample_rate"];
      updateAudio({ sample_rate: sampleRate });
    });
  });

  const autoSwitch = container.querySelector("#auto-transcode");
  if (autoSwitch) {
    autoSwitch.addEventListener("change", (e) => {
      updateAudio({ auto_transcode: (e.target as HTMLInputElement).checked });
    });
  }

  bindFeatureSwitch(
    container, "#dlna-enabled", "dlna_enabled", api.setDlnaEnabled, onStateChange
  );
  bindFeatureSwitch(
    container,
    "#airplay2-enabled",
    "airplay2_enabled",
    api.setAirPlay2Enabled,
    onStateChange
  );
  bindFeatureSwitch(
    container,
    "#sync-groups-enabled",
    "sync_groups_enabled",
    api.setSyncGroupsEnabled,
    onStateChange
  );
  bindFeatureSwitch(
    container,
    "#large-delay-enabled",
    "large_delay_enabled",
    api.setLargeDelayEnabled,
    onStateChange
  );
  bindFeatureSwitch(
    container,
    "#touchscreen-lyrics",
    "touchscreen_lyrics",
    api.setTouchscreenLyrics,
    onStateChange
  );

  const defaultVolumeInput = container.querySelector<HTMLInputElement>("#default-volume");
  const defaultVolumeEnabled = container.querySelector<HTMLInputElement>("#default-volume-enabled");
  defaultVolumeEnabled?.addEventListener("change", async () => {
    try {
      const result = await api.setDefaultVolume(Number(defaultVolumeInput?.value ?? 0), defaultVolumeEnabled.checked);
      const config = store.get().fullConfig;
      if (config) store.set({ fullConfig: { ...config, ...result } });
      if (defaultVolumeInput) defaultVolumeInput.disabled = !defaultVolumeEnabled.checked;
    } catch (e) {
      defaultVolumeEnabled.checked = !defaultVolumeEnabled.checked;
      store.showToast(`保存失败: ${e instanceof Error ? e.message : "未知错误"}`);
    }
  });
  container.querySelector<HTMLSelectElement>("#sender-volume-mode")?.addEventListener("change", async (event) => {
    const select = event.currentTarget as HTMLSelectElement;
    try {
      const result = await api.setSenderVolumeMode(select.value as "independent" | "linked");
      const config = store.get().fullConfig;
      if (config) store.set({ fullConfig: { ...config, ...result } });
      store.showToast("已保存，下次 AirPlay 投放生效");
    } catch (e) {
      select.value = store.get().fullConfig?.sender_volume_mode ?? "independent";
      store.showToast(`保存失败: ${e instanceof Error ? e.message : "未知错误"}`);
    }
  });
  if (defaultVolumeInput) {
    let debounce: ReturnType<typeof setTimeout> | null = null;
    defaultVolumeInput.addEventListener("input", () => {
      if (debounce) clearTimeout(debounce);
      debounce = setTimeout(async () => {
        const volume = Math.max(0, Math.min(100, parseInt(defaultVolumeInput.value || "0", 10) || 0));
        try {
          await api.setDefaultVolume(volume, defaultVolumeEnabled?.checked ?? false);
          const config = store.get().fullConfig;
          if (config) store.set({ fullConfig: { ...config, default_volume: volume } });
          store.showToast(`起播音量已设为 ${volume}`);
        } catch (e) {
          store.showToast(`保存失败: ${e instanceof Error ? e.message : "未知错误"}`);
        }
      }, 500);
    });
  }

  const webhookInput = container.querySelector<HTMLInputElement>("#notify-webhook");
  if (webhookInput) {
    let debounce: ReturnType<typeof setTimeout> | null = null;
    webhookInput.addEventListener("input", () => {
      if (debounce) clearTimeout(debounce);
      debounce = setTimeout(async () => {
        const url = webhookInput.value.trim();
        try {
          await api.setNotifyWebhook(url);
          const config = store.get().fullConfig;
          if (config) store.set({ fullConfig: { ...config, notify_webhook_url: url } });
          store.showToast(url ? "通知地址已保存" : "登录失效通知已关闭");
        } catch (e) {
          store.showToast(`保存失败: ${e instanceof Error ? e.message : "未知错误"}`);
        }
      }, 600);
    });
  }

  container.querySelectorAll("[data-theme]").forEach((el) => {
    el.addEventListener("click", () => {
      const theme = (el as HTMLElement).dataset.theme as Theme;
      onThemeChange(theme);
    });
  });

  container.querySelectorAll("[data-airplayprotocol]").forEach((el) => {
    el.addEventListener("click", async () => {
      if (store.get().saving) return;
      const protocol = (el as HTMLElement).dataset.airplayprotocol as AirPlayProtocol;
      const previous = store.get().fullConfig;
      store.set({ saving: true });
      if (previous) {
        store.set({ fullConfig: { ...previous, airplay_protocol: protocol } });
        onStateChange();
      }
      try {
        await api.setAirPlayProtocol(protocol);
        const [config, status] = await Promise.all([api.getConfig(), api.getStatus()]);
        store.set({ fullConfig: config, status, receivers: status.receivers, saving: false });
        onStateChange();
        store.showToast("AirPlay 设置已应用");
      } catch (e) {
        if (previous) {
          store.set({ fullConfig: previous });
          onStateChange();
        }
        store.set({ saving: false });
        store.showToast(`切换失败: ${e instanceof Error ? e.message : "未知错误"}`);
      }
    });
  });

  const appNameInput = container.querySelector("#app-name-input") as HTMLInputElement | null;
  if (appNameInput) {
    let debounceTimer: ReturnType<typeof setTimeout> | null = null;
    appNameInput.addEventListener("input", () => {
      if (debounceTimer) clearTimeout(debounceTimer);
      debounceTimer = setTimeout(async () => {
        const name = appNameInput.value.trim();
        if (!name) return;
        try {
          await api.setAppName(name);
          const config = await api.getConfig();
          store.set({ fullConfig: config });
          store.showToast("名称已保存");
        } catch (e) {
          store.showToast(`保存失败: ${e instanceof Error ? e.message : "未知错误"}`);
        }
      }, 600);
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

function bindFeatureSwitch(
  container: HTMLElement,
  selector: string,
  key: "dlna_enabled" | "sync_groups_enabled" | "large_delay_enabled" | "airplay2_enabled" | "touchscreen_lyrics",
  save: (enabled: boolean) => Promise<unknown>,
  rerender: () => void
) {
  container.querySelector<HTMLInputElement>(selector)?.addEventListener("change", async (event) => {
    if (store.get().saving) return;
    const enabled = (event.currentTarget as HTMLInputElement).checked;
    const previous = store.get().fullConfig;
    if (!previous) return;
    store.set({ saving: true, fullConfig: { ...previous, [key]: enabled } });
    rerender();
    try {
      await save(enabled);
      const [fullConfig, status] = await Promise.all([api.getConfig(), api.getStatus()]);
      store.set({ fullConfig, status, receivers: status.receivers, saving: false });
      rerender();
      const label = key === "dlna_enabled" ? "DLNA" : key === "sync_groups_enabled" ? "音箱组合" : key === "large_delay_enabled" ? "大延迟" : key === "touchscreen_lyrics" ? "触屏歌词与封面" : "AirPlay 2";
      store.showToast(`${label}已${enabled ? "开启" : "关闭"}`);
    } catch (error) {
      store.set({ fullConfig: previous, saving: false });
      rerender();
      store.showToast(`设置失败: ${error instanceof Error ? error.message : "未知错误"}`);
    }
  });
}
