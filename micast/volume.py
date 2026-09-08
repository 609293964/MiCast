"""Shared source-volume curve, separate from device volume commands."""

import math
import struct


def db_to_percent(db: float) -> int:
    if not math.isfinite(db) or not (db == -144 or -30 <= db <= 0):
        raise ValueError("无效的投放音量")
    return 0 if db == -144 else max(0, min(100, round((db + 30) / 30 * 100)))


def apply_pcm_gain(chunk: bytes, percent: int) -> bytes:
    """Attenuate s16le PCM exactly once; zero is digital silence."""
    if percent >= 100 or not chunk:
        return chunk
    if percent <= 0:
        return bytes(len(chunk))
    if len(chunk) % 2:
        raise ValueError("PCM samples must be aligned")
    gain = 10 ** ((percent * 0.3 - 30) / 20)
    output = bytearray(len(chunk))
    for index, (sample,) in enumerate(struct.iter_unpack("<h", chunk)):
        struct.pack_into("<h", output, index * 2, round(sample * gain))
    return bytes(output)
