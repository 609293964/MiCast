import type { State } from "../state";

export function renderToast(state: State["toast"]): string {
  return `
    <div class="toast ${state.visible ? "visible" : ""}" role="status" aria-live="polite">
      ${state.message}
    </div>
  `;
}
