"""Room measurement: synthetic-response recovery and level matching."""

import io
import wave

import numpy as np
import pytest

from micast.curve_fit import pchip_eval
from micast.room_measure import (
    SWEEP_RATE,
    compensation_points,
    measure_response,
    recording_level_dbfs,
    sweep_signal,
)


def _to_wav(samples: np.ndarray, rate: int = SWEEP_RATE) -> bytes:
    s16 = (np.clip(samples, -1, 1) * 32767).astype("<i2")
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(s16.tobytes())
    return buf.getvalue()


def _apply_room(
    samples: np.ndarray,
    room_points: list[tuple[float, float]],
    delay_s: float = 0.0,
    gain: float = 1.0,
) -> np.ndarray:
    """Filter the sweep through a synthetic LTI 'room' of known shape."""
    spectrum = np.fft.rfft(samples, n=len(samples))
    freqs = np.fft.rfftfreq(len(samples), 1 / SWEEP_RATE)
    mag_db = np.array([pchip_eval(room_points, f) for f in freqs])
    spectrum *= 10 ** (mag_db / 20)
    out = np.fft.irfft(spectrum, n=len(samples))
    if delay_s > 0:
        out = np.concatenate([np.zeros(int(delay_s * SWEEP_RATE)), out])
    return (out * gain).astype(np.float32)


ROOM = [(100, -6.0), (300, 4.0), (1000, 0.0), (3000, -5.0), (8000, 2.0)]


def test_measure_response_recovers_known_shape():
    rec = _apply_room(sweep_signal(), ROOM, delay_s=1.2, gain=0.3)
    freqs, gains = measure_response(_to_wav(rec))
    # Midrange recovery tracks the injected response within ~1 dB; band edges
    # (mic/speaker roll-off, smoothing windows) are allowed more slack.
    for f, expected in [(300, 4.0), (1000, 0.0), (3000, -5.0), (8000, 2.0)]:
        idx = min(range(len(freqs)), key=lambda i: abs(freqs[i] - f))
        assert abs(gains[idx] - expected) < 1.2, f"{f} Hz: {gains[idx]:.2f} vs {expected}"


def test_measure_response_rejects_short_recording():
    with pytest.raises(ValueError):
        measure_response(_to_wav(np.zeros(SWEEP_RATE // 2, dtype=np.float32)))


def test_compensation_points_invert_the_room():
    rec = _apply_room(sweep_signal(), ROOM)
    freqs, gains = measure_response(_to_wav(rec))
    points = compensation_points(freqs, gains)
    # Compensation ≈ inverse of the measured response in the midrange.
    for f, expected in [(300, -4.0), (3000, 5.0)]:
        assert abs(pchip_eval(points, f) - expected) < 1.5, f"{f} Hz"


def test_compensation_limits_deep_bass_boost():
    # A room with a huge measured bass null must not produce extreme boost.
    rec = _apply_room(sweep_signal(), [(60, -20.0), (500, 0.0), (20000, 0.0)])
    freqs, gains = measure_response(_to_wav(rec))
    points = compensation_points(freqs, gains)
    assert all(g <= 6.0 for f, g in points if f < 80)


def test_compensation_aims_at_target():
    rec = _apply_room(sweep_signal(), [(1000, 0.0)])  # acoustically flat room
    freqs, gains = measure_response(_to_wav(rec))
    points = compensation_points(freqs, gains, target="harman")
    # Flat room + Harman target ≈ the Harman curve itself.
    assert pchip_eval(points, 100) > 2.0  # bass shelf of the target
    assert pchip_eval(points, 12000) < -1.0  # treble roll-off


def test_recording_level_scales_with_gain():
    quiet = _to_wav(_apply_room(sweep_signal(), [(1000, 0.0)], gain=0.1))
    loud = _to_wav(_apply_room(sweep_signal(), [(1000, 0.0)], gain=0.5))
    diff = recording_level_dbfs(loud) - recording_level_dbfs(quiet)
    assert 12 < diff < 16  # 5× amplitude ≈ +14 dB
