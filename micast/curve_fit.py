"""EQ curve math: control points → smooth gain tables → filter parameters.

A user's EQ curve is persisted as a sparse list of control points
``(freq_hz, gain_db)`` — the only form that ever hits disk. Everything else
is derived one-way:

    control points  →  dense gain table (PCHIP on a log-frequency axis)
                    →  firequalizer gain_entry string / equalizer chain

PCHIP (Fritsch–Carlson monotone cubic) is implemented by hand so this module
stays dependency-free; unlike a natural cubic it never overshoots, which keeps
a hand-drawn curve free of surprise peaks between control points.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence

# Valid range for control points and generated tables.
CURVE_FREQ_RANGE = (20.0, 20000.0)
CURVE_GAIN_RANGE = (-12.0, 12.0)
CURVE_MAX_POINTS = 24

# Dense table resolution handed to firequalizer (entries on a log grid).
GAIN_TABLE_SIZE = 120
# Fallback path: how many equalizer bands approximate the curve.
FALLBACK_BANDS = 24

ControlPoint = tuple[float, float]  # (freq_hz, gain_db)


# ---------------------------------------------------------------------------
# Control points
# ---------------------------------------------------------------------------


def normalize_points(points: Iterable[Sequence[float]]) -> list[ControlPoint]:
    """Sort, clamp, and dedupe control points (later duplicates win)."""
    flo, fhi = CURVE_FREQ_RANGE
    glo, ghi = CURVE_GAIN_RANGE
    by_freq: dict[float, float] = {}
    for p in points or []:
        if len(p) < 2:
            continue
        freq = min(fhi, max(flo, float(p[0])))
        gain = min(ghi, max(glo, float(p[1])))
        by_freq[freq] = gain
    ordered = sorted(by_freq.items())
    if len(ordered) > CURVE_MAX_POINTS:
        # Thin evenly across the log axis rather than truncating the tail —
        # a dense imported table must keep its whole frequency span.
        stride = (len(ordered) - 1) / (CURVE_MAX_POINTS - 1)
        picked = sorted({round(i * stride) for i in range(CURVE_MAX_POINTS)})
        ordered = [ordered[i] for i in picked]
    return ordered


def is_flat(points: Sequence[ControlPoint] | None) -> bool:
    return not points or all(abs(g) < 0.05 for _, g in points)


def curve_signature(points: Sequence[ControlPoint] | None) -> tuple[ControlPoint, ...] | None:
    """Canonical hashable form for stream-variant dedup; None when flat."""
    if not points or is_flat(points):
        return None
    return tuple((round(f, 1), round(g, 2)) for f, g in normalize_points(points))


def legacy_bands_to_points(bands_hz: Sequence[int], gains_db: Sequence[float]) -> list[ControlPoint]:
    """Old 10-band (or migrated 5-band) slider state → control points."""
    return normalize_points(zip(bands_hz, gains_db))


# ---------------------------------------------------------------------------
# PCHIP interpolation on the log-frequency axis
# ---------------------------------------------------------------------------


def _pchip_slopes(xs: list[float], ys: list[float]) -> list[float]:
    """Fritsch–Carlson monotone derivatives at the knots."""
    n = len(xs)
    if n == 1:
        return [0.0]
    h = [xs[i + 1] - xs[i] for i in range(n - 1)]
    d = [(ys[i + 1] - ys[i]) / h[i] for i in range(n - 1)]
    m = [0.0] * n
    for i in range(1, n - 1):
        if d[i - 1] * d[i] <= 0:
            m[i] = 0.0
        else:
            w1 = 2 * h[i] + h[i - 1]
            w2 = h[i] + 2 * h[i - 1]
            m[i] = (w1 + w2) / (w1 / d[i - 1] + w2 / d[i])
    # Endpoints: one-sided, clamped to preserve monotonicity.
    m[0] = d[0]
    m[-1] = d[-1]
    for i in (0, n - 1):
        if m[i] * d[0 if i == 0 else -1] < 0:
            m[i] = 0.0
    return m


def pchip_eval(points: Sequence[ControlPoint], freq: float) -> float:
    """Evaluate the curve at ``freq``; flat beyond the outermost points."""
    if not points:
        return 0.0
    pts = [(math.log10(f), g) for f, g in points]
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    x = math.log10(min(CURVE_FREQ_RANGE[1], max(CURVE_FREQ_RANGE[0], freq)))
    if len(pts) == 1:
        return ys[0]
    if x <= xs[0]:
        return ys[0]
    if x >= xs[-1]:
        return ys[-1]
    i = 0
    while x > xs[i + 1] and i < len(xs) - 2:
        i += 1
    m = _pchip_slopes(xs, ys)
    h = xs[i + 1] - xs[i]
    t = (x - xs[i]) / h
    t2, t3 = t * t, t * t * t
    return (
        (2 * t3 - 3 * t2 + 1) * ys[i]
        + (t3 - 2 * t2 + t) * h * m[i]
        + (-2 * t3 + 3 * t2) * ys[i + 1]
        + (t3 - t2) * h * m[i + 1]
    )


def gain_table(
    points: Sequence[ControlPoint],
    size: int = GAIN_TABLE_SIZE,
    fmin: float = CURVE_FREQ_RANGE[0],
    fmax: float = CURVE_FREQ_RANGE[1],
) -> list[ControlPoint]:
    """Dense log-spaced (freq, gain) table for filter construction."""
    if not points:
        return []
    log_lo, log_hi = math.log10(fmin), math.log10(fmax)
    step = (log_hi - log_lo) / (size - 1)
    freqs = [10 ** (log_lo + i * step) for i in range(size)]
    return [(f, pchip_eval(points, f)) for f in freqs]


def add_curve(
    base: Sequence[ControlPoint], extra: Sequence[ControlPoint]
) -> list[ControlPoint]:
    """Pointwise sum of two curves on the union of their control frequencies.

    Used to layer a fixed shelf (night-mode bass attenuation) onto a speaker's
    active curve without special-casing the filter chain. Gains are summed and
    clamped to the shared gain range.
    """
    base = normalize_points(base or [])
    extra = normalize_points(extra or [])
    if not base:
        return extra
    if not extra:
        return base
    freqs = sorted({f for f, _ in base} | {f for f, _ in extra})
    merged = [(f, pchip_eval(base, f) + pchip_eval(extra, f)) for f in freqs]
    return normalize_points(merged)


# ---------------------------------------------------------------------------
# Filter construction
# ---------------------------------------------------------------------------


def firequalizer_args(table: Sequence[ControlPoint]) -> str:
    """firequalizer gain_entry option string from a dense gain table."""
    entries = ";".join(f"entry({f:.1f},{g:.2f})" for f, g in table)
    return f"gain_entry='{entries}'"


def equalizer_chain(table: Sequence[ControlPoint], bands: int = FALLBACK_BANDS) -> list[str]:
    """Fallback approximation: ``equalizer`` filter args on a subsampled table.

    The firequalizer gain table carries the full 120-point curve; when that
    filter is unavailable we thin it to a few dozen peaking bands.
    """
    if not table:
        return []
    if len(table) > bands:
        stride = len(table) / bands
        picked = [table[int(i * stride)] for i in range(bands)]
    else:
        picked = list(table)
    glo, ghi = CURVE_GAIN_RANGE
    out = []
    for freq, gain in picked:
        gain = min(ghi, max(glo, gain))
        if abs(gain) >= 0.05:
            out.append(f"equalizer=f={freq:.1f}:t=q:w=1.0:g={gain:.2f}")
    return out


# ---------------------------------------------------------------------------
# Target curves (reference overlays + calibration goals)
# ---------------------------------------------------------------------------

# Simplified named target responses as control points. These describe how a
# pleasing in-room response typically deviates from flat; calibration aims the
# measured result at the chosen target.
TARGET_CURVES: dict[str, list[ControlPoint]] = {
    "flat": [],
    "harman": [
        (20, 6.0), (60, 5.0), (120, 3.5), (200, 2.0), (400, 1.0),
        (1000, 0.0), (2000, 0.5), (4000, -0.5), (8000, -2.0),
        (12000, -3.5), (16000, -4.5), (20000, -5.0),
    ],
    "diffuse_field": [
        (20, 3.0), (100, 2.0), (500, 0.5), (1000, 0.0),
        (4000, -1.0), (8000, -3.0), (16000, -5.0), (20000, -6.0),
    ],
}


def target_table(name: str, size: int = GAIN_TABLE_SIZE) -> list[ControlPoint]:
    """Dense table for a named target curve; empty for unknown/flat."""
    return gain_table(normalize_points(TARGET_CURVES.get(name, [])), size=size)


# Fixed low-frequency shelf layered onto the active curve when night mode is on.
# It tames bass/rumble after dark while leaving the hand-drawn curve's own shape
# intact (see add_curve); the near-0 dB top anchors keep the shelf from leaning
# on the treble side.
NIGHT_ATTENUATION: list[ControlPoint] = [
    (20, -5.0), (40, -4.5), (80, -3.5), (160, -2.0), (320, -0.8), (640, 0.0), (20000, 0.0),
]


# ---------------------------------------------------------------------------
# Equal-loudness compensation (Fletcher–Munson style)
# ---------------------------------------------------------------------------

# Compensation is a low/high shelf that grows as the listening level drops: at
# low volume the ear hears less bass and less treble, so those bands are lifted.
# At full volume no compensation applies. The curve is quantized into bands so a
# volume nudge inside a band never triggers a filter rebuild.
LOUDNESS_BANDS = 11  # 0..10 → 0%..100% listening level

_LOUDNESS_REFERENCE: list[ControlPoint] = [
    (20, 10.0), (40, 9.0), (80, 7.5), (160, 5.5), (320, 3.5),
    (640, 2.0), (1000, 1.0), (2000, 1.5), (4000, 3.0),
    (8000, 5.0), (16000, 7.0), (20000, 8.0),
]


def loudness_band(percent: int) -> int:
    """Volume percent → one of ``LOUDNESS_BANDS`` equal-loudness bands."""
    return max(0, min(LOUDNESS_BANDS - 1, int(round(percent / 10))))


def loudness_curve(percent: int) -> list[ControlPoint]:
    """Equal-loudness compensation for a listening level; empty at full volume."""
    band = loudness_band(percent)
    if band >= LOUDNESS_BANDS - 1:
        return []
    strength = (LOUDNESS_BANDS - 1 - band) / (LOUDNESS_BANDS - 1)
    return [(round(f, 1), round(g * strength, 2)) for f, g in _LOUDNESS_REFERENCE]


# ---------------------------------------------------------------------------
# AutoEq interchange (GraphicEQ text: "<freq> <gain>" per line)
# ---------------------------------------------------------------------------


def parse_graphic_eq(text: str) -> list[ControlPoint]:
    """Parse AutoEq GraphicEQ / generic 'freq gain' line formats."""
    points: list[ControlPoint] = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith(("#", "//")):
            continue
        parts = line.replace(",", " ").split()
        if len(parts) < 2:
            continue
        try:
            points.append((float(parts[0]), float(parts[1])))
        except ValueError:
            continue
    return normalize_points(points)


def format_graphic_eq(table: Sequence[ControlPoint]) -> str:
    """Export a dense table in AutoEq GraphicEQ.txt format."""
    return "\n".join(f"{f:.1f} {g:.2f}" for f, g in table) + "\n"


# ---------------------------------------------------------------------------
# Response fitting (measurement → control points)
# ---------------------------------------------------------------------------


def fit_points(
    freqs: Sequence[float],
    gains_db: Sequence[float],
    max_points: int = CURVE_MAX_POINTS,
) -> list[ControlPoint]:
    """Greedy peak-picking fit of a dense measured response to control points.

    Picks the largest-error extremum, inserts it, and repeats — the points land
    where the curve actually bends instead of wasting knots on flat stretches.
    """
    flo, fhi = CURVE_FREQ_RANGE
    pairs = sorted(
        (min(fhi, max(flo, f)), g) for f, g in zip(freqs, gains_db) if flo <= f <= fhi
    )
    if not pairs:
        return []
    pts: list[ControlPoint] = [(pairs[0][0], pairs[0][1]), (pairs[-1][0], pairs[-1][1])]
    while len(pts) < max_points:
        worst_err, worst = 0.25, None
        for f, g in pairs:
            err = abs(g - pchip_eval(pts, f))
            if err > worst_err:
                worst_err, worst = err, (f, g)
        if worst is None:
            break
        pts.append(worst)
        new_pts = normalize_points(pts)
        if len(new_pts) <= len(pts) - 1:
            # The extremum deduped against an existing knot — no progress.
            break
        pts = new_pts
    return pts
