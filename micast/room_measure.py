"""Room/speaker measurement for the tuning calibration wizard.

The speaker plays an ESS (exponential sine sweep); the browser records it
through its microphone and uploads the WAV. Analysis here:

    cross-correlation alignment (absorbs seconds of network/decoder latency)
    → transfer function H = Sxy / Sxx (Welch-averaged)
    → 1/6-octave log-band smoothing
    → compensation = clamp(target − measured)

numpy only — no scipy.
"""

from __future__ import annotations

import io
import wave
from functools import lru_cache

import numpy as np

from micast.curve_fit import CURVE_GAIN_RANGE, fit_points, pchip_eval, target_table

SWEEP_RATE = 44100
SWEEP_F0 = 30.0  # below the audible band edge; keeps the 20 Hz grid honest
SWEEP_F1 = 20000.0
SWEEP_SECONDS = 5.0
SWEEP_AMPLITUDE = 0.5
# Leading/trailing silence so decoder startup never clips the sweep head.
SWEEP_PAD_SECONDS = 0.5

# Analysis grid: 1/6-octave-ish log spacing across the audible band.
ANALYSIS_BANDS = 60
BOOST_LIMIT_LOW_HZ = 80.0
BOOST_LIMIT_LOW_DB = 6.0


@lru_cache(maxsize=2)
def sweep_signal(rate: int = SWEEP_RATE, seconds: float = SWEEP_SECONDS) -> np.ndarray:
    """ESS sweep, float32 mono in [-1, 1)."""
    n = int(rate * seconds)
    t = np.arange(n, dtype=np.float64) / rate
    ratio = SWEEP_F1 / SWEEP_F0
    # ESS phase: instantaneous frequency rises exponentially f0 → f1.
    phase = 2 * np.pi * SWEEP_F0 * seconds / np.log(ratio) * (np.exp(t / seconds * np.log(ratio)) - 1)
    sig = SWEEP_AMPLITUDE * np.sin(phase)
    # Fade head/tail to avoid clicks.
    fade = int(0.02 * rate)
    env = np.ones(n)
    env[:fade] = np.linspace(0, 1, fade)
    env[-fade:] = np.linspace(1, 0, fade)
    return (sig * env).astype(np.float32)


@lru_cache(maxsize=2)
def sweep_pcm(rate: int = SWEEP_RATE) -> bytes:
    """Sweep as 44.1k s16 stereo raw PCM (no WAV header), padded with silence."""
    sig = sweep_signal(rate)
    pad = np.zeros(int(SWEEP_PAD_SECONDS * rate), dtype=np.float32)
    sig = np.concatenate([pad, sig, pad])
    s16 = (sig * 32767).astype("<i2")
    stereo = np.repeat(s16, 2)  # interleave LRLR
    return stereo.tobytes()


def sweep_wav(rate: int = SWEEP_RATE) -> bytes:
    pcm = sweep_pcm(rate)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm)
    return buf.getvalue()


def _load_wav(data: bytes) -> tuple[np.ndarray, int]:
    """Parse a PCM WAV upload into float32 mono + sample rate."""
    with wave.open(io.BytesIO(data), "rb") as w:
        channels = w.getnchannels()
        width = w.getsampwidth()
        rate = w.getframerate()
        frames = w.readframes(w.getnframes())
    if width == 2:
        samples = np.frombuffer(frames, dtype="<i2").astype(np.float32) / 32768.0
    elif width == 4:
        samples = np.frombuffer(frames, dtype="<i4").astype(np.float32) / 2147483648.0
    elif width == 1:
        samples = (np.frombuffer(frames, dtype=np.uint8).astype(np.float32) - 128.0) / 128.0
    else:
        raise ValueError(f"unsupported sample width: {width}")
    if channels > 1:
        samples = samples.reshape(-1, channels).mean(axis=1)
    return samples, rate


def _resample_to(samples: np.ndarray, src_rate: int, dst_rate: int = SWEEP_RATE) -> np.ndarray:
    if src_rate == dst_rate:
        return samples
    n_out = int(len(samples) * dst_rate / src_rate)
    x_old = np.arange(len(samples))
    x_new = np.linspace(0, len(samples) - 1, n_out)
    return np.interp(x_new, x_old, samples).astype(np.float32)


def _align(recording: np.ndarray, reference: np.ndarray, rate: int) -> np.ndarray:
    """Trim everything before the sweep via envelope cross-correlation."""
    env_ref = np.abs(reference)
    env_rec = np.abs(recording)
    # Downsample envelopes for a cheap, robust correlation.
    factor = max(1, rate // 2000)
    env_ref = env_ref[::factor]
    env_rec = env_rec[::factor]
    corr = np.correlate(env_rec, env_ref, mode="valid")
    if corr.size == 0:
        return recording
    lag = int(np.argmax(corr)) * factor
    return recording[lag:]


def measure_response(recording_wav: bytes) -> tuple[list[float], list[float]]:
    """Recorded sweep WAV → (freqs, gains_db) on the analysis grid.

    The result is normalized so its broadband median sits at 0 dB — it
    describes the speaker/room's *shape*, not its loudness (level matching is
    a separate step).
    """
    samples, rate = _load_wav(recording_wav)
    samples = _resample_to(samples, rate)
    reference = sweep_signal()
    samples = _align(samples, reference, SWEEP_RATE)
    usable = min(len(samples), len(reference))
    if usable < SWEEP_RATE:
        raise ValueError("录音太短，未捕获到完整扫频")
    rec = samples[:usable]
    ref = reference[:usable]

    # Welch-averaged transfer function magnitude.
    seg = SWEEP_RATE // 10  # 100 ms segments
    window = np.hanning(seg)
    sxy = np.zeros(seg // 2 + 1, dtype=np.complex128)
    sxx = np.zeros(seg // 2 + 1)
    count = 0
    for start in range(0, usable - seg + 1, seg // 2):
        r = np.fft.rfft(rec[start : start + seg] * window)
        x = np.fft.rfft(ref[start : start + seg] * window)
        sxy += r * np.conj(x)
        sxx += np.abs(x) ** 2
        count += 1
    transfer = np.abs(sxy) / np.maximum(sxx, 1e-12)
    freqs_fft = np.fft.rfftfreq(seg, 1 / SWEEP_RATE)

    # Log-band smoothing: mean magnitude per band, in dB.
    log_lo, log_hi = np.log10(20.0), np.log10(20000.0)
    edges = np.logspace(log_lo, log_hi, ANALYSIS_BANDS + 1)
    centers = np.sqrt(edges[:-1] * edges[1:])
    gains = np.zeros(ANALYSIS_BANDS)
    for i in range(ANALYSIS_BANDS):
        mask = (freqs_fft >= edges[i]) & (freqs_fft < edges[i + 1])
        gains[i] = 20 * np.log10(max(float(transfer[mask].mean() if mask.any() else 0.0), 1e-6))
    # Normalize shape: broadband median of the midrange sits at 0 dB.
    mid = (centers >= 200) & (centers <= 4000)
    gains -= float(np.median(gains[mid])) if mid.any() else float(np.median(gains))
    return centers.tolist(), gains.tolist()


def compensation_points(
    measured_freqs: list[float],
    measured_gains: list[float],
    target: str = "",
    max_points: int = 16,
) -> list[tuple[float, float]]:
    """Measured response → compensation control points (target − measured)."""
    glo, ghi = CURVE_GAIN_RANGE
    tgt = target_table(target) if target else []
    comp_freqs: list[float] = []
    comp_gains: list[float] = []
    for f, g in zip(measured_freqs, measured_gains):
        desired = pchip_eval(tgt, f) if tgt else 0.0
        comp = desired - g
        if f < BOOST_LIMIT_LOW_HZ:
            # Deep-bass boosting mostly feeds room modes and distortion.
            comp = min(comp, BOOST_LIMIT_LOW_DB)
        comp = min(ghi, max(glo, comp))
        comp_freqs.append(f)
        comp_gains.append(comp)
    return fit_points(comp_freqs, comp_gains, max_points=max_points)


def recording_level_dbfs(recording_wav: bytes) -> float:
    """RMS level of the sweep region; used to level-match group members."""
    samples, rate = _load_wav(recording_wav)
    samples = _resample_to(samples, rate)
    aligned = _align(samples, sweep_signal(), SWEEP_RATE)
    usable = min(len(aligned), len(sweep_signal()))
    if usable < SWEEP_RATE:
        raise ValueError("录音太短")
    rms = float(np.sqrt(np.mean(aligned[:usable] ** 2)))
    return 20 * np.log10(max(rms, 1e-9))
