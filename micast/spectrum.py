"""Realtime spectrum tap for the tuning page's live frequency display.

A pipeline feeds raw PCM (s16 stereo, post input gain, pre-EQ — the music
content, which is what the listener wants to see dancing under the curve).
On demand the analyzer FFTs the latest window into log-spaced bands spanning
the same 20 Hz – 20 kHz axis as the EQ curve, so band i lines up with the
curve x position of its center frequency.
"""

from __future__ import annotations

import time as _time

import numpy as np

FFT_SIZE = 8192  # ~186 ms at 44.1 kHz; 5.4 Hz bins resolve the bass bands
BAND_COUNT = 64  # dense iPhone-style hairline bars over the 20 Hz – 20 kHz axis
FREQ_RANGE = (20.0, 20000.0)
DB_FLOOR = -66.0  # below this the band renders as zero


def band_edges(
    count: int = BAND_COUNT, fmin: float = FREQ_RANGE[0], fmax: float = FREQ_RANGE[1]
) -> np.ndarray:
    """count+1 log-spaced band edges; bars tile the axis without gaps."""
    return np.logspace(np.log10(fmin), np.log10(fmax), count + 1)


# The pump-loop tap is gated on an actual viewer: with no tuning page open the
# hot path costs a single boolean check, not a per-chunk copy. Polling clients
# (the GET fallback) register as a fresh timestamp instead of a connection.
_subscribers = 0
_last_poll = 0.0


def spectrum_wanted() -> bool:
    return _subscribers > 0 or _time.monotonic() - _last_poll < 2.0


def spectrum_client_connected() -> None:
    global _subscribers
    _subscribers += 1


def spectrum_client_disconnected() -> None:
    global _subscribers
    _subscribers = max(0, _subscribers - 1)


def spectrum_polled() -> None:
    global _last_poll
    _last_poll = _time.monotonic()


class SpectrumAnalyzer:
    """Ring buffer of the latest PCM window + FFT band magnitudes.

    ``feed`` is called from the pipeline's hot pump loop and only copies
    bytes; the (still cheap) FFT runs only when ``bands`` is polled, so an
    idle UI costs nothing beyond a small memmove.
    """

    def __init__(self, sample_rate: int):
        self._sample_rate = sample_rate
        self._buf = bytearray(FFT_SIZE * 4)  # s16 stereo frames
        self._write = 0
        self._filled = 0
        edges = band_edges()
        bin_hz = sample_rate / FFT_SIZE
        # [start, end) FFT bin ranges per band (bins below 2 don't exist: DC/5Hz).
        self._bin_ranges = [
            (max(2, int(np.floor(edges[i] / bin_hz))), max(2, int(np.ceil(edges[i + 1] / bin_hz))))
            for i in range(len(edges) - 1)
        ]
        self._window = np.hanning(FFT_SIZE)

    def feed(self, chunk: bytes) -> None:
        n = len(chunk)
        if n >= len(self._buf):
            self._buf[:] = chunk[-len(self._buf) :]
            self._write = 0
            self._filled = len(self._buf)
            return
        end = self._write + n
        if end <= len(self._buf):
            self._buf[self._write : end] = chunk
        else:
            first = len(self._buf) - self._write
            self._buf[self._write :] = chunk[:first]
            self._buf[: n - first] = chunk[first:]
        self._write = end % len(self._buf)
        self._filled = min(len(self._buf), self._filled + n)

    def bands(self) -> list[float]:
        """BAND_COUNT magnitudes normalized to 0..1 (DB_FLOOR..0 dBFS)."""
        if self._filled < len(self._buf):
            return [0.0] * BAND_COUNT
        # Unroll the ring so the FFT sees a contiguous, most-recent window.
        data = bytes(self._buf[self._write :]) + bytes(self._buf[: self._write])
        pcm = np.frombuffer(data, dtype="<i2").reshape(-1, 2).mean(axis=1) / 32768.0
        mag = np.abs(np.fft.rfft(pcm * self._window))
        # Compensate the Hann window's coherent gain so a full-scale sine
        # lands at ~0 dB regardless of window shape.
        mag = mag * (2.0 / self._window.sum())
        out: list[float] = []
        for lo, hi in self._bin_ranges:
            power = float(np.sum(mag[lo:hi] ** 2))
            db = 10.0 * np.log10(power + 1e-12)
            out.append(max(0.0, min(1.0, (db - DB_FLOOR) / -DB_FLOOR)))
        return out
