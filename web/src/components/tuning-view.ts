/**
 * Full-screen tuning page: drawable EQ curve editor for one speaker.
 *
 * Opened as a secondary page (ui.tuningDid set) over the normal section
 * content — it takes no sidebar slot. Curve commits POST on release only;
 * the page mutates its canvas locally and never triggers a full re-render.
 */

import { api, type Device, type EqPresetsResponse, type SpeakerEq } from "../api";
import { appUrl } from "../paths";
import { store } from "../state";
import { CalibrationWizard } from "./calibration-wizard";
import { EqCurveCanvas, type CurvePoint } from "./eq-curve-canvas";

const PRESET_LABELS: Record<string, string> = {
  flat: "平直",
  bass: "低音增强",
  vocal: "人声清晰",
  night: "轻音",
  live: "现场感",
  harman: "Harman",
};

const TARGET_LABELS: Record<string, string> = {
  "": "无",
  harman: "Harman 目标",
  diffuse_field: "扩散场",
};

interface TuningState {
  did: string;
  enabled: boolean;
  points: CurvePoint[];
  preset: string;
  target: string;
}

let presetsCache: EqPresetsResponse | null = null;
let editor: EqCurveCanvas | null = null;
let commitTimer: number | null = null;

export function tuningViewActive(): boolean {
  return store.get().ui.tuningDid != null;
}

export function openTuning(did: string) {
  store.setUi({ tuningDid: did });
}

export function closeTuning() {
  store.setUi({ tuningDid: null });
}

export function renderTuningView(device: Device | undefined): string {
  const eq = device?.eq;
  const name = device ? device.alias || device.name : "音箱";
  const advancedOpen = store.get().ui.tuningAdvanced;
  return `
    <div class="page-heading tuning-heading">
      <button type="button" class="icon-button" data-tuning-back aria-label="返回">${"<"}</button>
      <div>
        <h2 class="page-title">调音台 · ${escapeHtml(name)}</h2>
        <p>拖动曲线上的圆点调整；点击空白添加控制点，双击删除。松手后生效。</p>
      </div>
    </div>
    <div class="tuning-canvas-wrap">
      <canvas class="tuning-canvas" data-tuning-canvas aria-label="EQ 曲线编辑器"></canvas>
    </div>
    <div class="tuning-toolbar">
      <label class="tuning-target">
        <span class="caption">曲线</span>
        <select data-tuning-curve aria-label="曲线库"></select>
      </label>
      <button type="button" class="button secondary" data-tuning-curve-save>存为曲线</button>
      <button type="button" class="button secondary" data-tuning-curve-rename disabled>重命名</button>
      <button type="button" class="button secondary" data-tuning-curve-delete disabled>删除</button>
      <span class="tuning-divider-v"></span>
      <button type="button" class="button secondary" data-tuning-import>导入</button>
      <button type="button" class="button secondary" data-tuning-export>导出</button>
      <input type="file" accept=".txt,.eq,text/plain" data-tuning-import-file hidden>
      <span class="caption tuning-hint">文件兼容 AutoEq 格式</span>
    </div>
    <div class="tuning-toolbar">
      <span class="caption">叠加</span>
      <label class="tuning-enable">
        <input type="checkbox" class="switch" data-tuning-night ${eq?.night_mode ? "checked" : ""} aria-label="夜间模式">
        <span>夜间模式</span>
      </label>
      <label class="tuning-enable">
        <input type="checkbox" class="switch" data-tuning-loudness ${eq?.loudness_comp_enabled ? "checked" : ""} aria-label="响度补偿">
        <span>响度补偿</span>
      </label>
    </div>
    <div class="tuning-advanced">
      <button type="button" class="tuning-advanced-toggle ${advancedOpen ? "open" : ""}" data-tuning-advanced-toggle aria-expanded="${advancedOpen}">
        高级功能
      </button>
      <div class="tuning-advanced-body" data-tuning-advanced-body ${advancedOpen ? "" : "hidden"}>
        <div class="tuning-toolbar">
          <label class="tuning-target" title="标准曲线：不是你的当前曲线，只是叠加在画布上的参考虚线，不改变声音">
            <span class="caption">目标曲线</span>
            <select data-tuning-target aria-label="目标曲线">
              ${Object.entries(TARGET_LABELS)
                .map(
                  ([key, label]) =>
                    `<option value="${key}" ${(eq?.target ?? "") === key ? "selected" : ""}>${label}</option>`
                )
                .join("")}
            </select>
          </label>
          <span class="caption tuning-hint">标准曲线，仅参考，不改变声音</span>
          <button type="button" class="button secondary" data-tuning-calibrate>自动校准<span class="tuning-badge">实验性</span></button>
        </div>
        <div class="tuning-toolbar">
          <button type="button" class="button secondary" data-tuning-ab-toggle>盲听对比</button>
        </div>
        <div class="tuning-wizard" data-tuning-wizard hidden></div>
      </div>
    </div>
    <div class="ab-modal" data-ab-modal hidden>
      <div class="ab-dialog" role="dialog" aria-modal="true" aria-label="盲听对比">
        <h3>盲听对比</h3>
        <div class="ab-pick" data-ab-pick>
          <label class="tuning-target">
            <span class="caption">对比项 1</span>
            <select data-ab-choice="0" aria-label="对比项 1"></select>
          </label>
          <label class="tuning-target">
            <span class="caption">对比项 2</span>
            <select data-ab-choice="1" aria-label="对比项 2"></select>
          </label>
        </div>
        <p class="caption ab-status" data-ab-status></p>
        <div class="ab-actions">
          <button type="button" class="button primary" data-ab-start>开始</button>
          <button type="button" class="button secondary" data-ab-listen-a hidden>听 A</button>
          <button type="button" class="button secondary" data-ab-listen-b hidden>听 B</button>
          <button type="button" class="button secondary" data-ab-reveal hidden>揭示</button>
          <button type="button" class="button plain" data-ab-close>关闭</button>
        </div>
      </div>
    </div>
    <div class="tuning-footer caption">
      仅作用于这台音箱；曲线不同的音箱会使用独立音频流。
    </div>
  `;
}

export function bindTuningView(container: HTMLElement, onClose: () => void) {
  editor?.destroy();
  editor = null;
  const did = store.get().ui.tuningDid;
  if (!did) return;
  const device = store.get().devices.find((d) => d.did === did);
  const eq = device?.eq;

  const state: TuningState = {
    did,
    enabled: eq?.enabled ?? false,
    points: (eq?.points ?? []).map(([freq, gain]) => ({ freq, gain })),
    preset: eq?.preset ?? "",
    target: eq?.target ?? "",
  };

  let wizard: CalibrationWizard | null = null;
  // Blind test (modal): two user-chosen curves are randomly assigned to A/B.
  // On open playback pauses and slot A's curve is already applied, so 「开始」
  // is a pure resume with no encoder-rebuild gap.
  let ab: {
    original: CurvePoint[];
    choiceKeys: [string, string];
    aIsFirst: boolean;
    started: boolean;
    heardA: boolean;
    heardB: boolean;
    revealed: boolean;
    pausedByUs: boolean;
  } | null = null;

  container.querySelector<HTMLElement>("[data-tuning-back]")?.addEventListener("click", () => {
    wizard?.destroy();
    wizard = null;
    onClose();
  });

  const canvas = container.querySelector<HTMLCanvasElement>("[data-tuning-canvas]");
  const nightToggle = container.querySelector<HTMLInputElement>("[data-tuning-night]");
  const loudnessToggle = container.querySelector<HTMLInputElement>("[data-tuning-loudness]");
  const targetSelect = container.querySelector<HTMLSelectElement>("[data-tuning-target]");
  const curveSelect = container.querySelector<HTMLSelectElement>("[data-tuning-curve]");
  const curveSaveBtn = container.querySelector<HTMLButtonElement>("[data-tuning-curve-save]");
  const curveRenameBtn = container.querySelector<HTMLButtonElement>("[data-tuning-curve-rename]");
  const curveDeleteBtn = container.querySelector<HTMLButtonElement>("[data-tuning-curve-delete]");

  // ---- curve library (global): presets + user-saved curves ----

  const samePoints = (a: CurvePoint[], b: Array<[number, number]>) =>
    a.length === b.length &&
    a.every((p, i) => Math.abs(p.freq - b[i][0]) < 0.05 && Math.abs(p.gain - b[i][1]) < 0.005);

  /** Which library entry the current canvas state corresponds to. */
  const currentCurveKey = (): string => {
    if (state.preset && presetsCache?.presets[state.preset]) return `preset:${state.preset}`;
    for (const [name, pts] of Object.entries(presetsCache?.saved ?? {})) {
      if (samePoints(state.points, pts)) return `saved:${name}`;
    }
    return "current";
  };

  const refreshCurveSelect = () => {
    if (!curveSelect) return;
    const key = currentCurveKey();
    const group = (label: string, options: string) =>
      options ? `<optgroup label="${label}">${options}</optgroup>` : "";
    const option = (value: string, label: string) =>
      `<option value="${escapeHtml(value)}" ${value === key ? "selected" : ""}>${escapeHtml(label)}</option>`;
    curveSelect.innerHTML =
      `<optgroup label="当前">${option("current", "自定义曲线（未保存）")}</optgroup>` +
      group(
        "我的曲线",
        Object.keys(presetsCache?.saved ?? {})
          .map((name) => option(`saved:${name}`, name))
          .join("")
      ) +
      group(
        "系统预设",
        Object.keys(PRESET_LABELS)
          .map((k) => option(`preset:${k}`, PRESET_LABELS[k]))
          .join("")
      );
    curveSelect.value = key;
    if (curveSelect.value !== key) curveSelect.value = "current"; // unsaved edit
    const isSaved = curveSelect.value.startsWith("saved:");
    if (curveRenameBtn) curveRenameBtn.disabled = !isSaved;
    if (curveDeleteBtn) curveDeleteBtn.disabled = !isSaved;
  };

  /** Selecting a library entry replaces the canvas and commits. */
  const applyCurveKey = (key: string) => {
    if (key === "current") return;
    const [kind, name] = [key.split(":")[0], key.slice(key.indexOf(":") + 1)];
    const gains =
      kind === "preset" ? presetsCache?.presets[name] : presetsCache?.saved?.[name];
    if (!gains) return;
    const points = gains.map(([freq, gain]) => ({ freq, gain }));
    editor?.setPoints(points);
    state.points = points;
    state.preset = kind === "preset" ? name : "";
    save();
  };

  const markCurve = () => refreshCurveSelect();

  const applyTarget = (key: string) => {
    const pts = key && presetsCache?.targets[key]
      ? presetsCache.targets[key].map(([freq, gain]) => ({ freq, gain }))
      : key === "flat"
        ? []
        : undefined;
    editor?.setTarget(pts && pts.length ? pts : key ? [] : undefined);
  };

  // ---- backend sync (never a full re-render, so an open drag survives) ----

  const syncLocal = (resp: SpeakerEq) => {
    const dev = store.get().devices.find((d) => d.did === state.did);
    if (dev) dev.eq = resp;
    if (nightToggle) nightToggle.checked = Boolean(resp.night_mode);
    if (loudnessToggle) loudnessToggle.checked = Boolean(resp.loudness_comp_enabled);
  };

  const syncFull = (resp: SpeakerEq) => {
    syncLocal(resp);
    state.enabled = resp.enabled;
    state.points = (resp.points ?? []).map(([f, g]) => ({ freq: f, gain: g }));
    state.preset = resp.preset ?? "";
    state.target = resp.target ?? "";
    editor?.setPoints(state.points);
    applyTarget(state.target);
    markCurve();
  };

  const postCurve = () =>
    api
      .setDeviceEqCurve(state.did, {
        enabled: state.enabled,
        points: state.points.map((p) => [p.freq, p.gain]),
        preset: state.preset,
        target: state.target,
      })
      .then(syncLocal);

  const save = (patch: Partial<TuningState> = {}) => {
    Object.assign(state, patch);
    // Debounce: rapid commits (drag releases, preset taps) collapse into one
    // pipeline rebuild; each rebuild costs a sub-second encoder gap.
    if (commitTimer != null) window.clearTimeout(commitTimer);
    commitTimer = window.setTimeout(() => {
      commitTimer = null;
      postCurve().catch((e) => {
        store.showToast(`EQ 保存失败: ${e instanceof Error ? e.message : "未知错误"}`);
      });
    }, 300);
  };

  // Immediate commit (A/B switching) — flush any pending debounce first.
  // Awaitable so the blind test can sequence "apply curve → resume playback".
  const commitNow = () => {
    if (commitTimer != null) {
      window.clearTimeout(commitTimer);
      commitTimer = null;
    }
    return postCurve().catch((e) => {
      store.showToast(`EQ 保存失败: ${e instanceof Error ? e.message : "未知错误"}`);
    });
  };

  // ---- canvas ----

  if (canvas) {
    editor = new EqCurveCanvas(canvas, {
      points: state.points,
      freqRange: [20, 20000],
      gainRange: [-12, 12],
      onCommit: (points) => {
        state.points = points;
        markCurve();
        state.preset = "";
        save();
      },
    });
    // Presets/targets/library arrive async; apply once loaded.
    if (!presetsCache) {
      api
        .getEqPresets()
        .then((r) => {
          presetsCache = r;
          applyTarget(state.target);
          refreshCurveSelect();
        })
        .catch(() => undefined);
    } else {
      applyTarget(state.target);
    }
  }
  refreshCurveSelect();

  // ---- primary controls ----

  const advancedToggle = container.querySelector<HTMLElement>("[data-tuning-advanced-toggle]");
  const advancedBody = container.querySelector<HTMLElement>("[data-tuning-advanced-body]");
  advancedToggle?.addEventListener("click", () => {
    const open = advancedBody ? advancedBody.hidden : false;
    if (advancedBody) advancedBody.hidden = !open;
    advancedToggle.classList.toggle("open", open);
    advancedToggle.setAttribute("aria-expanded", String(open));
    // Persist across visits. The poll-skipped re-render keeps the DOM as-is;
    // the flags above already flipped it.
    store.setUi({ tuningAdvanced: open });
  });

  nightToggle?.addEventListener("change", () => {
    api
      .setDeviceNightMode(state.did, nightToggle.checked)
      .then(syncLocal)
      .catch((e) => {
        nightToggle.checked = !nightToggle.checked;
        store.showToast(`夜间模式切换失败: ${e instanceof Error ? e.message : "未知错误"}`);
      });
  });

  loudnessToggle?.addEventListener("change", () => {
    api
      .setDeviceLoudness(state.did, loudnessToggle.checked)
      .then(syncLocal)
      .catch((e) => {
        loudnessToggle.checked = !loudnessToggle.checked;
        store.showToast(`响度补偿切换失败: ${e instanceof Error ? e.message : "未知错误"}`);
      });
  });

  targetSelect?.addEventListener("change", () => {
    const key = targetSelect.value;
    const pts = key && presetsCache?.targets[key]
      ? presetsCache.targets[key].map(([freq, gain]) => ({ freq, gain }))
      : key
        ? []
        : undefined;
    editor?.setTarget(key === "" ? undefined : (pts ?? []));
    save({ target: key });
  });

  curveSelect?.addEventListener("change", () => {
    applyCurveKey(curveSelect.value);
    refreshCurveSelect();
  });

  const applyCurveList = (curves: Record<string, [number, number][]>) => {
    presetsCache = { ...(presetsCache ?? { presets: {}, targets: {}, freq_range: [20, 20000], gain_range: [-12, 12] }), saved: curves };
    refreshCurveSelect();
  };

  curveSaveBtn?.addEventListener("click", async () => {
    const name = window.prompt("给这条曲线起个名字：");
    if (!name?.trim()) return;
    try {
      const resp = await api.saveCurve(name.trim(), state.points.map((p) => [p.freq, p.gain]));
      applyCurveList(resp.curves);
      store.showToast(`已保存曲线「${name.trim()}」`);
    } catch (e) {
      store.showToast(`保存失败: ${e instanceof Error ? e.message : "未知错误"}`);
    }
  });

  curveRenameBtn?.addEventListener("click", async () => {
    const oldName = curveSelect?.value.startsWith("saved:") ? curveSelect.value.slice(6) : null;
    if (!oldName) return;
    const newName = window.prompt("新名字：", oldName);
    if (!newName?.trim() || newName.trim() === oldName) return;
    try {
      const resp = await api.renameCurve(oldName, newName.trim());
      applyCurveList(resp.curves);
      if (curveSelect) curveSelect.value = `saved:${newName.trim()}`;
      store.showToast("已重命名");
    } catch (e) {
      store.showToast(`重命名失败: ${e instanceof Error ? e.message : "未知错误"}`);
    }
  });

  curveDeleteBtn?.addEventListener("click", async () => {
    const name = curveSelect?.value.startsWith("saved:") ? curveSelect.value.slice(6) : null;
    if (!name || !window.confirm(`删除曲线「${name}」？音箱上正在使用的曲线不受影响。`)) return;
    try {
      const resp = await api.deleteCurve(name);
      applyCurveList(resp.curves);
      store.showToast(`已删除「${name}」`);
    } catch (e) {
      store.showToast(`删除失败: ${e instanceof Error ? e.message : "未知错误"}`);
    }
  });

  // ---- import / export ----

  const importFile = container.querySelector<HTMLInputElement>("[data-tuning-import-file]");
  container.querySelector<HTMLElement>("[data-tuning-import]")?.addEventListener("click", () => importFile?.click());
  importFile?.addEventListener("change", async () => {
    const file = importFile.files?.[0];
    importFile.value = "";
    if (!file) return;
    try {
      const resp = await api.importGraphicEq(state.did, await file.text());
      syncFull(resp);
      store.showToast("已导入 AutoEq 曲线");
    } catch (e) {
      store.showToast(`导入失败: ${e instanceof Error ? e.message : "未知错误"}`);
    }
  });

  container.querySelector<HTMLElement>("[data-tuning-export]")?.addEventListener("click", () => {
    // pywebview (WebView2) ignores in-page blob downloads; navigating to an
    // attachment endpoint triggers the native download in both webview and
    // plain browsers.
    window.location.assign(appUrl(`/api/tuning/${encodeURIComponent(state.did)}/export.txt`));
  });

  // ---- A/B blind comparison (modal) ----

  const abModal = container.querySelector<HTMLElement>("[data-ab-modal]");
  const abStatus = container.querySelector<HTMLElement>("[data-ab-status]");
  const abPick = container.querySelector<HTMLElement>("[data-ab-pick]");
  const abStart = container.querySelector<HTMLElement>("[data-ab-start]");
  const abListenA = container.querySelector<HTMLElement>("[data-ab-listen-a]");
  const abListenB = container.querySelector<HTMLElement>("[data-ab-listen-b]");
  const abReveal = container.querySelector<HTMLElement>("[data-ab-reveal]");
  const abCloseBtn = container.querySelector<HTMLElement>("[data-ab-close]");
  const abChoices = [
    container.querySelector<HTMLSelectElement>('[data-ab-choice="0"]'),
    container.querySelector<HTMLSelectElement>('[data-ab-choice="1"]'),
  ];

  // Candidates: the current curve, flat, every preset, and the saved library.
  const abOptions = (): Array<[string, string]> => {
    const opts: Array<[string, string]> = [
      ["current", "当前曲线"],
      ["flat", "平直"],
    ];
    for (const key of Object.keys(PRESET_LABELS)) {
      if (key !== "flat" && presetsCache?.presets[key]) opts.push([`preset:${key}`, PRESET_LABELS[key]]);
    }
    for (const name of Object.keys(presetsCache?.saved ?? {})) {
      opts.push([`saved:${name}`, name]);
    }
    return opts;
  };

  const abChoiceLabel = (key: string) => abOptions().find(([k]) => k === key)?.[1] ?? key;

  const abResolve = (key: string): CurvePoint[] => {
    if (key === "current") return ab?.original.map((p) => ({ ...p }) as CurvePoint) ?? [];
    if (key === "flat") return [];
    const [kind, name] = [key.split(":")[0], key.slice(key.indexOf(":") + 1)];
    const gains = kind === "preset" ? presetsCache?.presets[name] : presetsCache?.saved?.[name];
    return (gains ?? []).map(([freq, gain]) => ({ freq, gain }));
  };

  const abSlotCurve = (slot: "a" | "b"): CurvePoint[] => {
    if (!ab) return [];
    const key = slot === "a" === ab.aIsFirst ? ab.choiceKeys[0] : ab.choiceKeys[1];
    return abResolve(key);
  };

  const abApply = async (slot: "a" | "b") => {
    if (!ab) return;
    state.points = abSlotCurve(slot);
    state.preset = "";
    // True blind: the canvas keeps showing the original curve until reveal.
    if (ab.revealed) {
      editor?.setPoints(state.points);
      markCurve();
    }
    abListenA?.classList.toggle("active", slot === "a");
    abListenB?.classList.toggle("active", slot === "b");
    await commitNow();
  };

  const abSync = () => {
    if (!ab || !abStatus) return;
    if (ab.revealed) {
      const aKey = ab.aIsFirst ? ab.choiceKeys[0] : ab.choiceKeys[1];
      const bKey = ab.aIsFirst ? ab.choiceKeys[1] : ab.choiceKeys[0];
      abStatus.textContent = `A = ${abChoiceLabel(aKey)} · B = ${abChoiceLabel(bKey)}`;
    } else if (ab.started) {
      abStatus.textContent = "A、B 可以随时切换着听；两条都听过之后就可以揭示了。";
    } else {
      abStatus.textContent = "A/B 已随机分配，音源已提前切到 A。点「开始」继续播放。";
    }
    if (abStart) abStart.hidden = ab.started;
    if (abListenA) abListenA.hidden = !ab.started;
    if (abListenB) abListenB.hidden = !ab.started;
    if (abReveal) abReveal.hidden = ab.revealed || !(ab.heardA && ab.heardB);
    abPick?.querySelectorAll("select").forEach((s) => (s.disabled = ab!.started));
  };

  // Re-apply slot A while paused whenever the picks change (pre-start only),
  // so playback resumes straight into the right curve.
  const abPreloadA = () => {
    if (!ab || ab.started) return;
    void abApply("a");
  };

  const abOpen = () => {
    if (ab) return;
    const opts = abOptions();
    abChoices.forEach((sel, i) => {
      if (!sel) return;
      sel.innerHTML = opts
        .map(([key, label]) => `<option value="${key}">${label}</option>`)
        .join("");
      sel.value = i === 0 ? "current" : "flat";
    });
    const playback = store.get().playback;
    ab = {
      original: state.points.map((p) => ({ ...p })),
      choiceKeys: ["current", "flat"],
      aIsFirst: Math.random() < 0.5,
      started: false,
      heardA: false,
      heardB: false,
      revealed: false,
      pausedByUs: Boolean(playback?.playing && !playback.paused),
    };
    if (abModal) abModal.hidden = false;
    abSync();
    // Pause first, then cut the stream over to slot A's curve while silent.
    if (ab.pausedByUs) void api.pause().catch(() => undefined);
    void abApply("a");
  };

  const abClose = () => {
    if (!ab) return;
    const { original, started, pausedByUs } = ab;
    ab = null;
    if (abModal) abModal.hidden = true;
    // Slot A was applied on open, so restore even if the test never started —
    // but skip the rebuild when nothing actually changed.
    if (JSON.stringify(state.points) !== JSON.stringify(original)) {
      state.points = original.map((p) => ({ ...p }));
      state.preset = "";
      editor?.setPoints(state.points);
      markCurve();
      void commitNow();
    }
    // If we paused and the user never resumed, restart playback for them.
    if (pausedByUs && !started) void api.play().catch(() => undefined);
  };

  container.querySelector<HTMLElement>("[data-tuning-ab-toggle]")?.addEventListener("click", abOpen);
  abStart?.addEventListener("click", () => {
    if (!ab || ab.started) return;
    if (JSON.stringify(abResolve(ab.choiceKeys[0])) === JSON.stringify(abResolve(ab.choiceKeys[1]))) {
      if (abStatus) abStatus.textContent = "两个对比项的曲线相同，没法对比，换一条再开始。";
      return;
    }
    ab.started = true;
    ab.heardA = true;
    abSync();
    // Slot A was already applied on open/change — this is a pure resume.
    void api.play().catch(() => undefined);
  });
  abListenA?.addEventListener("click", () => {
    if (!ab) return;
    ab.heardA = true;
    abSync();
    void abApply("a");
  });
  abListenB?.addEventListener("click", () => {
    if (!ab) return;
    ab.heardB = true;
    abSync();
    void abApply("b");
  });
  abReveal?.addEventListener("click", () => {
    if (!ab) return;
    ab.revealed = true;
    // Show the curve that is actually playing now.
    editor?.setPoints(state.points);
    markCurve();
    abSync();
  });
  abCloseBtn?.addEventListener("click", abClose);
  abChoices.forEach((sel, i) =>
    sel?.addEventListener("change", () => {
      if (!ab) return;
      ab.choiceKeys[i] = sel.value;
      abPreloadA();
    })
  );

  // After calibration (and optional level match) the speaker's curve and the
  // group gains live server-side; re-read them without a full re-render so the
  // open editor reflects what was applied.
  const refreshFromDevice = async () => {
    try {
      const devices = await api.getDevices();
      store.set({ devices, deviceLoadError: null });
    } catch {
      // The apply() toast already confirmed success; keep local state on error.
    }
    const resp = store.get().devices.find((d) => d.did === did)?.eq;
    if (resp) syncFull(resp);
  };

  const wizardHost = container.querySelector<HTMLElement>("[data-tuning-wizard]");
  container.querySelector<HTMLElement>("[data-tuning-calibrate]")?.addEventListener("click", () => {
    if (!wizardHost) return;
    wizard?.destroy();
    wizardHost.hidden = false;
    wizardHost.innerHTML = "";
    wizard = new CalibrationWizard(wizardHost, did, () => {
      void refreshFromDevice();
    });
  });
}

function escapeHtml(text: string): string {
  return text
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}
