"""PCM → ALAC packetization for RAOP sending."""

import array

import av

# PyAV's ALAC encoder cannot be configured below its 4096-sample frame size
# (the frame_size option is ignored by this build), so one RTP packet carries
# 4096 samples (~93 ms) and the SDP fmtp announces that length.
FRAME_SAMPLES = 4096
FRAME_BYTES = FRAME_SAMPLES * 4  # s16le stereo interleaved


class AlacPacketizer:
    """Feed interleaved 44.1 kHz s16 stereo PCM; emit one ALAC packet per frame."""

    def __init__(self):
        self._codec = av.CodecContext.create("alac", "w")
        self._codec.sample_rate = 44100
        self._codec.layout = "stereo"
        self._codec.format = av.AudioFormat("s16p")
        self._codec.open()
        self._pending = bytearray()
        self._pts = 0

    def encode(self, pcm: bytes) -> list[bytes]:
        self._pending += pcm
        packets: list[bytes] = []
        while len(self._pending) >= FRAME_BYTES:
            chunk = bytes(self._pending[:FRAME_BYTES])
            del self._pending[:FRAME_BYTES]
            frame = av.AudioFrame(format="s16p", layout="stereo", samples=FRAME_SAMPLES)
            frame.sample_rate = 44100
            frame.pts = self._pts
            self._pts += FRAME_SAMPLES
            # Deinterleave LRLR… into planar L… / R…
            samples = array.array("h")
            samples.frombytes(chunk)
            frame.planes[0].update(samples[0::2].tobytes())
            frame.planes[1].update(samples[1::2].tobytes())
            packets.extend(bytes(packet) for packet in self._codec.encode(frame))
        return packets

    def flush(self) -> list[bytes]:
        """Pad the tail with silence and drain the encoder."""
        remainder = -len(self._pending) % FRAME_BYTES
        packets = self.encode(bytes(remainder)) if remainder else []
        packets.extend(bytes(packet) for packet in self._codec.encode(None))
        return packets
