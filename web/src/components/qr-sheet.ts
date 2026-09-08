import type { State } from "../state";

export function renderQRSheet(state: State["qr"]): string {
  if (!state.open) return "";
  return `
    <div class="sheet-overlay ${state.open ? "open" : ""}" data-close-qr></div>
    <div class="sheet ${state.open ? "open" : ""}" role="dialog" aria-modal="true">
      <div class="sheet-handle"></div>
      <h2 class="title-2" style="text-align: center; margin-bottom: var(--space-xs);">连接米家</h2>
      <p class="caption" style="text-align: center; margin-bottom: var(--space-xl);">
        使用米家 App 扫描下方二维码
      </p>
      <div class="qr-card">
        ${
          state.qrUrl
            ? `<img src="api/xiaomi/login/qr/image?url=${encodeURIComponent(state.qrUrl)}" alt="米家连接二维码">`
            : `<div class="qr-placeholder">
                 <span class="caption">加载中…</span>
               </div>`
        }
      </div>
      <div style="text-align: center; margin-bottom: var(--space-xl);">
        <span class="status-pill ${state.state === "confirmed" ? "running" : state.state === "expired" ? "error" : ""}">
          ${stateLabel(state.state)}
        </span>
      </div>
      <button class="button secondary full" data-close-qr>取消</button>
    </div>
  `;
}

function stateLabel(state: State["qr"]["state"]): string {
  const labels: Record<State["qr"]["state"], string> = {
    idle: "等待开始",
    waiting: "等待扫码",
    scanned: "已扫描，等待确认",
    confirmed: "登录成功",
    expired: "二维码已过期",
  };
  return labels[state];
}

export function bindQRSheet(container: HTMLElement, onClose: () => void) {
  container.querySelectorAll("[data-close-qr]").forEach((el) => {
    el.addEventListener("click", onClose);
  });
}
