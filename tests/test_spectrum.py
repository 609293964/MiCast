"""Spectrum tap: band mapping, normalization, silence."""

import numpy as np

from micast.spectrum import BAND_COUNT, FREQ_RANGE, SpectrumAnalyzer, band_edges


def _sine_pcm(freq: float, seconds: float = 0.5, rate: int = 44100, amp: float = 0.9) -> bytes:
    t = np.arange(int(rate * seconds)) / rate
    wave = (np.sin(2 * np.pi * freq * t) * amp * 32767).astype("<i2")
    stereo = np.column_stack([wave, wave])
    return stereo.tobytes()


def _band_index(freq: float) -> int:
    edges = band_edges()
    return int(np.searchsorted(edges, freq, side="right")) - 1


def test_sine_peaks_in_its_band():
    analyzer = SpectrumAnalyzer(44100)
    analyzer.feed(_sine_pcm(1000.0))
    bands = analyzer.bands()
    assert len(bands) == BAND_COUNT
    peak = int(np.argmax(bands))
    assert peak == _band_index(1000.0)
    assert bands[peak] > 0.9  # near-full-scale sine ≈ 0 dBFS
    # Neighboring bands are far quieter (tone is narrow).
    assert bands[peak] - bands[max(0, peak - 3)] > 0.3


def test_bass_sine_lands_in_low_band():
    analyzer = SpectrumAnalyzer(44100)
    analyzer.feed(_sine_pcm(60.0))
    bands = analyzer.bands()
    # 5.4 Hz FFT bins straddle the band edge nearest 60 Hz; allow the peak in
    # the expected band or its immediate neighbor.
    assert abs(int(np.argmax(bands)) - _band_index(60.0)) <= 1


def test_silence_and_short_buffer_are_zero():
    analyzer = SpectrumAnalyzer(44100)
    assert analyzer.bands() == [0.0] * BAND_COUNT
    analyzer.feed(b"\x00" * 4096)  # less than one FFT window
    assert analyzer.bands() == [0.0] * BAND_COUNT
    analyzer.feed(b"\x00" * (8192 * 4))
    assert analyzer.bands() == [0.0] * BAND_COUNT


def test_ring_buffer_wraps_without_garbage():
    analyzer = SpectrumAnalyzer(44100)
    chunk = _sine_pcm(4000.0, seconds=0.1)
    for _ in range(10):  # feed more than the ring holds, in pieces
        analyzer.feed(chunk)
    bands = analyzer.bands()
    assert int(np.argmax(bands)) == _band_index(4000.0)


def test_band_edges_span_the_curve_axis():
    edges = band_edges()
    assert edges[0] == np.float64(FREQ_RANGE[0]) or abs(edges[0] - FREQ_RANGE[0]) < 1e-6
    assert abs(edges[-1] - FREQ_RANGE[1]) < 1e-6
    assert len(edges) == BAND_COUNT + 1
    assert np.all(np.diff(edges) > 0)
