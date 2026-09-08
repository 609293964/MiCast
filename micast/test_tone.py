"""Built-in test tone for speaker diagnostics (no public-internet dependency)."""

import math
import struct

SAMPLE_RATE = 44100
# A short, recognizable arpeggio (C5 E5 G5 C6) repeated, with per-note decay.
_NOTES = [(523.25, 0.4), (659.25, 0.4), (783.99, 0.4), (1046.50, 0.8)]
_REPEATS = 4
_AMPLITUDE = 0.4

_cache: bytes | None = None


def test_tone_wav() -> bytes:
    """Synthesize (once) a finite stereo 16-bit WAV test tone."""
    global _cache
    if _cache is not None:
        return _cache

    frames = bytearray()
    for _ in range(_REPEATS):
        for freq, seconds in _NOTES:
            count = int(SAMPLE_RATE * seconds)
            for i in range(count):
                t = i / SAMPLE_RATE
                envelope = math.exp(-3.0 * t / seconds)  # gentle pluck decay
                sample = int(_AMPLITUDE * 32767 * envelope * math.sin(2 * math.pi * freq * t))
                frames.extend(struct.pack("<hh", sample, sample))

    data = bytes(frames)
    byte_rate = SAMPLE_RATE * 2 * 2
    header = (
        b"RIFF"
        + struct.pack("<I", 36 + len(data))
        + b"WAVE"
        + b"fmt "
        + struct.pack("<IHHIIHH", 16, 1, 2, SAMPLE_RATE, byte_rate, 4, 16)
        + b"data"
        + struct.pack("<I", len(data))
    )
    _cache = header + data
    return _cache
