/**
 * Drawable EQ curve editor on <canvas>.
 *
 * Log-frequency axis (20 Hz – 20 kHz), dB axis, PCHIP-smoothed curve through
 * sparse control points. Interaction model (see slider-commit-on-release):
 * drags only repaint the canvas locally; `onCommit` fires on pointer release
 * and the host must NOT trigger a full re-render in response.
 */

export interface CurvePoint {
  freq: number;
  gain: number;
}

export interface CurveCanvasOptions {
  points: CurvePoint[];
  /** Reference/target curve drawn dashed underneath (no interaction). */
  target?: CurvePoint[];
  freqRange?: [number, number];
  gainRange?: [number, number];
  readOnly?: boolean;
  onCommit?: (points: CurvePoint[]) => void;
}

const GRID_FREQS = [31, 62, 125, 250, 500, 1000, 2000, 4000, 8000, 16000];
const GRID_FREQ_LABELS = ["31", "62", "125", "250", "500", "1k", "2k", "4k", "8k", "16k"];
const GRID_GAINS = [-12, -6, 0, 6, 12];
const HIT_RADIUS = 14; // px, pointer hit area around a control point
const PAD = { top: 24, right: 16, bottom: 30, left: 44 };

// --- PCHIP (Fritsch–Carlson) on the log-frequency axis, mirrors curve_fit.py

function pchipSlopes(xs: number[], ys: number[]): number[] {
  const n = xs.length;
  if (n === 1) return [0];
  const h: number[] = [];
  const d: number[] = [];
  for (let i = 0; i < n - 1; i++) {
    h.push(xs[i + 1] - xs[i]);
    d.push((ys[i + 1] - ys[i]) / h[i]);
  }
  const m = new Array<number>(n).fill(0);
  for (let i = 1; i < n - 1; i++) {
    if (d[i - 1] * d[i] <= 0) {
      m[i] = 0;
    } else {
      const w1 = 2 * h[i] + h[i - 1];
      const w2 = h[i] + 2 * h[i - 1];
      m[i] = (w1 + w2) / (w1 / d[i - 1] + w2 / d[i]);
    }
  }
  m[0] = d[0];
  m[n - 1] = d[n - 1];
  if (m[0] * d[0] < 0) m[0] = 0;
  if (m[n - 1] * d[n - 1] < 0) m[n - 1] = 0;
  return m;
}

export function evalCurve(points: CurvePoint[], freq: number): number {
  if (points.length === 0) return 0;
  if (points.length === 1) return points[0].gain;
  const xs = points.map((p) => Math.log10(p.freq));
  const ys = points.map((p) => p.gain);
  const x = Math.log10(freq);
  if (x <= xs[0]) return ys[0];
  if (x >= xs[xs.length - 1]) return ys[ys.length - 1];
  let i = 0;
  while (x > xs[i + 1] && i < xs.length - 2) i++;
  const m = pchipSlopes(xs, ys);
  const h = xs[i + 1] - xs[i];
  const t = (x - xs[i]) / h;
  const t2 = t * t;
  const t3 = t2 * t;
  return (
    (2 * t3 - 3 * t2 + 1) * ys[i] +
    (t3 - 2 * t2 + t) * h * m[i] +
    (-2 * t3 + 3 * t2) * ys[i + 1] +
    (t3 - t2) * h * m[i + 1]
  );
}

function normalize(points: CurvePoint[], gainRange: [number, number]): CurvePoint[] {
  const byFreq = new Map<number, number>();
  for (const p of points) byFreq.set(p.freq, Math.max(gainRange[0], Math.min(gainRange[1], p.gain)));
  return [...byFreq.entries()]
    .map(([freq, gain]) => ({ freq, gain }))
    .sort((a, b) => a.freq - b.freq)
    .slice(0, 24);
}

export class EqCurveCanvas {
  private canvas: HTMLCanvasElement;
  private opts: CurveCanvasOptions;
  private points: CurvePoint[];
  private gridLayer: HTMLCanvasElement | null = null;
  private dragIndex = -1;
  private hoverIndex = -1;
  private moved = false;
  private resizeObserver: ResizeObserver | null = null;
  private themeObserver: MutationObserver | null = null;
  private colorSchemeQuery: MediaQueryList | null = null;
  private destroyed = false;

  constructor(canvas: HTMLCanvasElement, opts: CurveCanvasOptions) {
    this.canvas = canvas;
    this.opts = opts;
    this.points = normalize(opts.points, this.gainRange);
    this.attach();
  }

  get gainRange(): [number, number] {
    return this.opts.gainRange ?? [-12, 12];
  }

  get freqRange(): [number, number] {
    return this.opts.freqRange ?? [20, 20000];
  }

  /** Replace the curve (e.g. preset applied); repaints without committing. */
  setPoints(points: CurvePoint[]) {
    this.points = normalize(points, this.gainRange);
    this.repaint();
  }

  setTarget(points: CurvePoint[] | undefined) {
    this.opts.target = points;
    this.repaint();
  }

  destroy() {
    this.destroyed = true;
    this.resizeObserver?.disconnect();
    this.themeObserver?.disconnect();
    this.colorSchemeQuery?.removeEventListener("change", this.onThemeChange);
    const c = this.canvas;
    c.removeEventListener("pointerdown", this.onPointerDown);
    c.removeEventListener("pointermove", this.onPointerMove);
    c.removeEventListener("pointerup", this.onPointerUp);
    c.removeEventListener("pointercancel", this.onPointerUp);
    c.removeEventListener("dblclick", this.onDoubleClick);
  }

  // ---- coordinates ----

  private plotRect(): { x: number; y: number; w: number; h: number } {
    const w = this.canvas.clientWidth;
    const h = this.canvas.clientHeight;
    return { x: PAD.left, y: PAD.top, w: Math.max(10, w - PAD.left - PAD.right), h: Math.max(10, h - PAD.top - PAD.bottom) };
  }

  private xOf(freq: number): number {
    const [fmin, fmax] = this.freqRange;
    const { x, w } = this.plotRect();
    const t = (Math.log10(freq) - Math.log10(fmin)) / (Math.log10(fmax) - Math.log10(fmin));
    return x + t * w;
  }

  private yOf(gain: number): number {
    const [gmin, gmax] = this.gainRange;
    const { y, h } = this.plotRect();
    return y + ((gmax - gain) / (gmax - gmin)) * h;
  }

  private freqAt(px: number): number {
    const [fmin, fmax] = this.freqRange;
    const { x, w } = this.plotRect();
    const t = Math.min(1, Math.max(0, (px - x) / w));
    return 10 ** (Math.log10(fmin) + t * (Math.log10(fmax) - Math.log10(fmin)));
  }

  private gainAt(py: number): number {
    const [gmin, gmax] = this.gainRange;
    const { y, h } = this.plotRect();
    const t = Math.min(1, Math.max(0, (py - y) / h));
    return gmax - t * (gmax - gmin);
  }

  // ---- rendering ----

  private attach() {
    const c = this.canvas;
    c.style.touchAction = "none";
    c.addEventListener("pointerdown", this.onPointerDown);
    c.addEventListener("pointermove", this.onPointerMove);
    c.addEventListener("pointerup", this.onPointerUp);
    c.addEventListener("pointercancel", this.onPointerUp);
    c.addEventListener("dblclick", this.onDoubleClick);
    this.resizeObserver = new ResizeObserver(() => this.repaint());
    this.resizeObserver.observe(c);
    // Theme tokens change without a resize — drop the cached grid layer.
    this.themeObserver = new MutationObserver(this.onThemeChange);
    this.themeObserver.observe(document.documentElement, {
      attributes: true,
      attributeFilter: ["data-theme"],
    });
    this.colorSchemeQuery = window.matchMedia("(prefers-color-scheme: dark)");
    this.colorSchemeQuery.addEventListener("change", this.onThemeChange);
    this.repaint();
  }

  private onThemeChange = () => {
    this.gridLayer = null;
    this.repaint();
  };

  private cssVar(name: string, fallback: string): string {
    const v = getComputedStyle(this.canvas).getPropertyValue(name).trim();
    return v || fallback;
  }

  private renderGrid() {
    const rect = this.plotRect();
    const grid = document.createElement("canvas");
    const dpr = window.devicePixelRatio || 1;
    grid.width = this.canvas.clientWidth * dpr;
    grid.height = this.canvas.clientHeight * dpr;
    const ctx = grid.getContext("2d");
    if (!ctx) return;
    ctx.scale(dpr, dpr);
    // Theme-aware contrast: separator-strong at partial alpha reads as a
    // subtle hairline on both light and dark wraps; labels use secondary text.
    const gridLine = this.cssVar("--separator-strong", "rgba(128,128,128,0.4)");
    const label = this.cssVar("--text-secondary", "rgba(128,128,128,1)");
    const faint = this.cssVar("--text-tertiary", "rgba(128,128,128,0.7)");
    ctx.lineWidth = 1;
    ctx.font = "11px system-ui, sans-serif";
    // Vertical octave lines + frequency labels.
    GRID_FREQS.forEach((f, i) => {
      const px = Math.round(this.xOf(f)) + 0.5;
      ctx.globalAlpha = 0.45;
      ctx.strokeStyle = gridLine;
      ctx.beginPath();
      ctx.moveTo(px, rect.y);
      ctx.lineTo(px, rect.y + rect.h);
      ctx.stroke();
      ctx.globalAlpha = 1;
      ctx.fillStyle = label;
      ctx.textAlign = "center";
      ctx.fillText(GRID_FREQ_LABELS[i], px, rect.y + rect.h + 17);
    });
    // Horizontal dB lines; 0 dB emphasized.
    for (const g of GRID_GAINS) {
      const py = Math.round(this.yOf(g)) + 0.5;
      ctx.globalAlpha = g === 0 ? 0.95 : 0.45;
      ctx.strokeStyle = gridLine;
      ctx.lineWidth = g === 0 ? 1.5 : 1;
      ctx.beginPath();
      ctx.moveTo(rect.x, py);
      ctx.lineTo(rect.x + rect.w, py);
      ctx.stroke();
      ctx.globalAlpha = 1;
      ctx.lineWidth = 1;
      ctx.fillStyle = label;
      ctx.textAlign = "right";
      ctx.fillText(`${g > 0 ? "+" : ""}${g}`, rect.x - 7, py + 4);
    }
    // Axis titles with units.
    ctx.fillStyle = faint;
    ctx.textAlign = "left";
    ctx.fillText("增益 (dB)", 2, 13);
    ctx.textAlign = "right";
    ctx.fillText("频率 (Hz)", rect.x + rect.w, rect.y + rect.h + 17);
    this.gridLayer = grid;
  }

  private drawCurve(ctx: CanvasRenderingContext2D, points: CurvePoint[], color: string, width: number, dashed = false) {
    if (points.length === 0) return;
    const rect = this.plotRect();
    ctx.save();
    ctx.strokeStyle = color;
    ctx.lineWidth = width;
    ctx.lineJoin = "round";
    ctx.lineCap = "round";
    if (dashed) ctx.setLineDash([5, 4]);
    ctx.beginPath();
    const steps = Math.max(64, Math.floor(rect.w / 2));
    for (let s = 0; s <= steps; s++) {
      const px = rect.x + (s / steps) * rect.w;
      const gain = evalCurve(points, this.freqAt(px));
      const py = this.yOf(gain);
      if (s === 0) ctx.moveTo(px, py);
      else ctx.lineTo(px, py);
    }
    ctx.stroke();
    ctx.restore();
  }

  /** Soft accent wash between the curve and the 0 dB line. */
  private drawCurveFill(ctx: CanvasRenderingContext2D, accent: string) {
    if (this.points.length === 0) return;
    const rect = this.plotRect();
    const zeroY = this.yOf(0);
    ctx.save();
    const grad = ctx.createLinearGradient(0, rect.y, 0, rect.y + rect.h);
    grad.addColorStop(0, this.withAlpha(accent, 0.16));
    grad.addColorStop(0.5, this.withAlpha(accent, 0.05));
    grad.addColorStop(1, this.withAlpha(accent, 0.16));
    ctx.fillStyle = grad;
    ctx.beginPath();
    const steps = Math.max(64, Math.floor(rect.w / 2));
    for (let s = 0; s <= steps; s++) {
      const px = rect.x + (s / steps) * rect.w;
      const py = this.yOf(evalCurve(this.points, this.freqAt(px)));
      if (s === 0) ctx.moveTo(px, py);
      else ctx.lineTo(px, py);
    }
    ctx.lineTo(rect.x + rect.w, zeroY);
    ctx.lineTo(rect.x, zeroY);
    ctx.closePath();
    ctx.fill();
    ctx.restore();
  }

  /** accent is "rgb(r, g, b)"; produce a translucent variant. */
  private withAlpha(color: string, alpha: number): string {
    const m = color.match(/(\d+)[,\s]+(\d+)[,\s]+(\d+)/);
    if (m) return `rgba(${m[1]}, ${m[2]}, ${m[3]}, ${alpha})`;
    return color;
  }

  private formatFreq(freq: number): string {
    return freq >= 1000 ? `${(freq / 1000).toFixed(freq >= 10000 ? 0 : 1)} kHz` : `${Math.round(freq)} Hz`;
  }

  /** Floating "250 Hz · +3.0 dB" chip next to the hovered/dragged point. */
  private drawReadout(ctx: CanvasRenderingContext2D, index: number) {
    const p = this.points[index];
    if (!p) return;
    const text = `${this.formatFreq(p.freq)} · ${p.gain >= 0 ? "+" : ""}${p.gain.toFixed(1)} dB`;
    ctx.save();
    ctx.font = "11px system-ui, sans-serif";
    const padX = 9;
    const w = ctx.measureText(text).width + padX * 2;
    const h = 22;
    const rect = this.plotRect();
    let x = this.xOf(p.freq) + 12;
    let y = this.yOf(p.gain) - h - 12;
    if (x + w > rect.x + rect.w) x = this.xOf(p.freq) - w - 12;
    if (y < rect.y) y = this.yOf(p.gain) + 14;
    ctx.beginPath();
    ctx.roundRect(x, y, w, h, 6);
    ctx.fillStyle = this.cssVar("--bg", "#fff");
    ctx.globalAlpha = 0.92;
    ctx.fill();
    ctx.globalAlpha = 1;
    ctx.strokeStyle = this.cssVar("--separator-strong", "rgba(128,128,128,0.4)");
    ctx.lineWidth = 1;
    ctx.stroke();
    ctx.fillStyle = this.cssVar("--text", "#111");
    ctx.textAlign = "left";
    ctx.textBaseline = "middle";
    ctx.fillText(text, x + padX, y + h / 2 + 0.5);
    ctx.restore();
  }

  private repaint() {
    if (this.destroyed) return;
    const c = this.canvas;
    const dpr = window.devicePixelRatio || 1;
    const w = c.clientWidth;
    const h = c.clientHeight;
    if (w === 0 || h === 0) return;
    if (c.width !== w * dpr || c.height !== h * dpr) {
      c.width = w * dpr;
      c.height = h * dpr;
      this.gridLayer = null;
    }
    if (!this.gridLayer) this.renderGrid();
    const ctx = c.getContext("2d");
    if (!ctx) return;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, w, h);
    if (this.gridLayer) ctx.drawImage(this.gridLayer, 0, 0, w, h);
    if (this.opts.target && this.opts.target.length > 1) {
      this.drawCurve(ctx, normalize(this.opts.target, this.gainRange), this.cssVar("--text-tertiary", "#888"), 1.5, true);
    }
    const accent = this.cssVar("--accent", "#0a84ff");
    this.drawCurveFill(ctx, accent);
    this.drawCurve(ctx, this.points, accent, 2.5);
    // Control points: accent dot with a halo ring cut from the wrap color.
    const halo = this.cssVar("--bg-secondary", this.cssVar("--bg", "#fff"));
    this.points.forEach((p, i) => {
      const px = this.xOf(p.freq);
      const py = this.yOf(p.gain);
      const active = i === this.hoverIndex || i === this.dragIndex;
      ctx.save();
      if (active) {
        ctx.shadowColor = this.withAlpha(accent, 0.55);
        ctx.shadowBlur = 10;
      }
      ctx.beginPath();
      ctx.arc(px, py, active ? 7 : 5.5, 0, Math.PI * 2);
      ctx.fillStyle = accent;
      ctx.fill();
      ctx.restore();
      ctx.beginPath();
      ctx.arc(px, py, active ? 7 : 5.5, 0, Math.PI * 2);
      ctx.lineWidth = 2;
      ctx.strokeStyle = halo;
      ctx.stroke();
    });
    const readoutIndex = this.dragIndex >= 0 ? this.dragIndex : this.hoverIndex;
    if (readoutIndex >= 0) this.drawReadout(ctx, readoutIndex);
  }

  // ---- interaction ----

  private eventPos(e: PointerEvent | MouseEvent): { px: number; py: number } {
    const rect = this.canvas.getBoundingClientRect();
    return { px: e.clientX - rect.left, py: e.clientY - rect.top };
  }

  private hitPoint(px: number, py: number): number {
    let best = -1;
    let bestDist = HIT_RADIUS;
    this.points.forEach((p, i) => {
      const d = Math.hypot(this.xOf(p.freq) - px, this.yOf(p.gain) - py);
      if (d < bestDist) {
        bestDist = d;
        best = i;
      }
    });
    return best;
  }

  private onPointerDown = (e: PointerEvent) => {
    if (this.opts.readOnly) return;
    const { px, py } = this.eventPos(e);
    const hit = this.hitPoint(px, py);
    if (hit >= 0) {
      this.dragIndex = hit;
    } else {
      // Click on empty canvas inserts a control point and starts dragging it.
      const point = { freq: this.freqAt(px), gain: this.gainAt(py) };
      this.points.push(point);
      this.points = normalize(this.points, this.gainRange);
      this.dragIndex = this.points.findIndex(
        (p) => Math.abs(p.freq - point.freq) < point.freq * 0.02
      );
    }
    if (this.dragIndex >= 0) {
      this.moved = false;
      this.canvas.setPointerCapture(e.pointerId);
      e.preventDefault();
    }
  };

  private onPointerMove = (e: PointerEvent) => {
    const { px, py } = this.eventPos(e);
    if (this.dragIndex < 0) {
      const hover = this.hitPoint(px, py);
      if (hover !== this.hoverIndex) {
        this.hoverIndex = hover;
        this.canvas.style.cursor = hover >= 0 ? "grab" : "crosshair";
        this.repaint();
      }
      return;
    }
    const point = this.points[this.dragIndex];
    if (!point) return;
    point.freq = this.freqAt(px);
    point.gain = this.gainAt(py);
    const [gmin, gmax] = this.gainRange;
    point.gain = Math.max(gmin, Math.min(gmax, point.gain));
    // Keep the array sorted so the renderer walks it in frequency order.
    this.points.sort((a, b) => a.freq - b.freq);
    this.dragIndex = this.points.indexOf(point);
    this.moved = true;
    this.repaint(); // local repaint only — no server round-trip mid-drag
  };

  private onPointerUp = (e: PointerEvent) => {
    if (this.dragIndex < 0) return;
    this.dragIndex = -1;
    this.canvas.releasePointerCapture?.(e.pointerId);
    this.repaint();
    // Insert-on-click commits too (a new point is a curve change by itself).
    this.opts.onCommit?.(this.points.map((p) => ({ ...p })));
  };

  private onDoubleClick = (e: MouseEvent) => {
    if (this.opts.readOnly) return;
    const { px, py } = this.eventPos(e);
    const hit = this.hitPoint(px, py);
    if (hit >= 0 && this.points.length > 0) {
      this.points.splice(hit, 1);
      this.repaint();
      this.opts.onCommit?.(this.points.map((p) => ({ ...p })));
    }
  };
}
