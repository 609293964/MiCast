"""Per-speaker audio pipeline: PCM source → in-process encoder → stream server."""

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable

from micast.audio_encoder import AudioEncoder, firequalizer_available, raw_pcm_format, wav_header
from micast.config import settings
from micast.curve_fit import (
    add_curve,
    equalizer_chain,
    firequalizer_args,
    gain_table,
    loudness_band,
    loudness_curve,
)
from micast.pcm_source import PCMSource
from micast.stream_server import StreamServer

logger = logging.getLogger(__name__)

# A PCM source (TCP socket, receiver stdout) can die silently: the read hangs
# forever, the encoder never exits, and no error is logged anywhere — the only
# symptom is speakers reconnecting to a byteless stream every ~2 seconds. While
# a sender session is live the source must produce continuously; this timeout
# is the signal that it wedged.
SOURCE_STALL_TIMEOUT_SECONDS = 8.0
SOURCE_STALL_CHECK_SECONDS = 2.0


class SpeakerPipeline:
    """Handles one AirPlay receiver's audio pipeline.

    A stereo-pair receiver runs two of these (one per channel); ``group_id`` +
    ``channel`` bind the pipeline to one side of the pair. The audio filter is
    resolved from the live group config: the channel itself decides the pan,
    and the loudness trim follows whichever speaker currently holds that
    channel — so swapping channels needs only an encoder restart. (Delay
    alignment lives downstream in the stream server, keyed per speaker.)
    """

    def __init__(
        self,
        device_id: str,
        alias: str,
        pcm_source: PCMSource,
        stream_server: StreamServer,
        on_session_start: Callable[[str], Awaitable[None]] | None = None,
        input_sample_rate: int | None = None,
        stream_id: str | None = None,
        group_id: str | None = None,
        channel: str | None = None,
        eq_curve: list[tuple[float, float]] | None = None,
        loudness: bool = False,
        pace_source: bool = True,
        session_active: Callable[[], bool] | None = None,
        on_source_stall: Callable[[str], Awaitable[None]] | None = None,
    ):
        self.device_id = device_id
        self.alias = alias
        self._pcm_source = pcm_source
        self._stream_server = stream_server
        self._on_session_start = on_session_start
        self._input_sample_rate = input_sample_rate
        self._stream_id = stream_id or device_id
        self._group_id = group_id
        self._channel = channel
        self._eq_curve = list(eq_curve) if eq_curve else None
        self._loudness = loudness
        self._pace_source = pace_source
        # Ground truth for the stall watchdog: True while a sender session is
        # live on this pipeline's receiver. None disables stall detection.
        self._session_active = session_active
        self._on_source_stall = on_source_stall
        self._stall_task: asyncio.Task | None = None
        self._source_restart_lock = asyncio.Lock()
        self._last_feed_at = 0.0
        # Armed only while a newly-started sender session has not produced its
        # first bytes. Once audio has flowed, later silence may be a pause or a
        # track transition and must not restart the receiver underneath it.
        self._stall_armed = True
        self._encoder: AudioEncoder | None = None
        self._tasks: list[asyncio.Task] = []
        self._running = False
        self._status = "idle"
        # AirPlay sender volume is stream gain, independent from the physical
        # speaker volume controlled by the Web UI.
        self._input_volume = 100
        # Equal-loudness state: the listening level drives the compensation
        # curve (in both volume modes it is the sender volume), quantized so a
        # nudge inside a band never rebuilds the encoder.
        self._loudness_level = 100
        self._loudness_band = loudness_band(self._loudness_level)
        self._loudness_restarting = False

    @property
    def status(self) -> str:
        return self._status

    @property
    def stream_url(self) -> str:
        return f"http://{settings.effective_stream_host}:{settings.stream_port}/stream/{self._stream_id}"

    def _build_audio_filter(self) -> list[tuple[str, str]] | None:
        """Audio filter chain: per-speaker EQ curve first, then stereo shaping.

        Returns structured ``(filter_name, args)`` links consumed by
        micast.audio_encoder._build_filter_graph. The drawn EQ curve renders
        as one firequalizer gain table, or a multi-band equalizer chain when
        the bundled libavfilter lacks firequalizer.
        """
        parts: list[tuple[str, str]] = []
        # The effective curve is the drawn EQ plus the equal-loudness shelf; a
        # loudness-only speaker (flat EQ) still gets a non-empty curve so it is
        # encoded rather than bypassed raw.
        curve = self._eq_curve
        if self._loudness:
            shelf = loudness_curve(self._loudness_level)
            if shelf:
                curve = add_curve(curve or [], shelf)
        if curve:
            table = gain_table(curve)
            if firequalizer_available():
                parts.append(("firequalizer", firequalizer_args(table)))
            else:
                for link in equalizer_chain(table):
                    name, _, args = link.partition("=")
                    parts.append((name, args))
        group = next((g for g in settings.groups if g.id == self._group_id), None)
        stereo = group is not None and group.mode == "stereo" and self._channel in ("left", "right")
        if not stereo:
            return parts or None
        # Keep the stream stereo (duplicate the picked channel to both sides):
        # byte-rate pacing and speaker decoders all assume two channels.
        side = "FL" if self._channel == "left" else "FR"
        parts.append(("pan", f"stereo|c0={side}|c1={side}"))
        # Trims are configured per speaker; follow whoever holds this channel.
        holder = self._channel_holder()
        if holder:
            gain = float(group.gains_db.get(holder, 0.0))
            if gain:
                parts.append(("volume", f"{gain}dB"))
        return parts

    def _channel_holder(self) -> str | None:
        """The speaker currently assigned to this pipeline's stereo channel."""
        group = next((g for g in settings.groups if g.id == self._group_id), None)
        if group is None or group.mode != "stereo" or self._channel not in ("left", "right"):
            return None
        return next((did for did, ch in group.channels.items() if ch == self._channel), None)

    def _audio_config(self):
        """Encoding config: filtered streams always go through the encoder, so
        the raw-PCM bypass becomes a WAV encode at the input rate (no resample)."""
        if settings.audio.auto_transcode:
            return settings.audio
        return settings.audio.model_copy(
            update={"format": "wav", "sample_rate": self._input_sample_rate or 44100}
        )

    def set_input_volume(self, percent: int) -> None:
        self._input_volume = max(0, min(100, int(percent)))

    def set_loudness_level(self, percent: int) -> None:
        """Update the listening level driving equal-loudness compensation.

        The curve is quantized into bands, so only a band crossing rebuilds the
        encoder (and even then just the encoder, never the stream/session).
        """
        percent = max(0, min(100, int(percent)))
        self._loudness_level = percent
        if not self._loudness:
            return
        band = loudness_band(percent)
        if band == self._loudness_band or not self._running:
            self._loudness_band = band
            return
        self._loudness_band = band
        if not self._loudness_restarting:
            asyncio.create_task(self._rebuild_loudness())

    async def _rebuild_loudness(self) -> None:
        """Rebuild only the encoder after a loudness band crossing."""
        if self._loudness_restarting:
            return
        self._loudness_restarting = True
        try:
            await self.restart_encoder()
        finally:
            self._loudness_restarting = False

    def _apply_input_gain(self, chunk: bytes) -> bytes:
        """Apply the sender volume to signed 16-bit little-endian PCM."""
        from micast.volume import apply_pcm_gain

        return apply_pcm_gain(chunk, self._input_volume)

    @property
    def source_silence_seconds(self) -> float | None:
        """Seconds since the PCM source last produced bytes (None when idle)."""
        if not self._running or not self._last_feed_at:
            return None
        return time.monotonic() - self._last_feed_at

    def register_stream(self) -> None:
        stream_format = (
            AudioEncoder(self._audio_config(), self._input_sample_rate).format
            if settings.audio.auto_transcode or self._build_audio_filter()
            else raw_pcm_format(self._input_sample_rate or 44100)
        )
        self._stream_server.register_stream(self._stream_id, stream_format)

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._status = "running"
        self._last_feed_at = time.monotonic()
        self._stall_armed = True
        logger.info("Starting pipeline for %s (%s)", self._stream_id, self.alias)

        self.register_stream()

        try:
            source_reader = await self._pcm_source.start()
            await self._start_encoder(source_reader)
        except Exception:
            logger.exception("Failed to start pipeline for %s", self._stream_id)
            self._status = "error"
            raise
        if self._session_active is not None and self._stall_task is None:
            self._stall_task = asyncio.create_task(self._watch_source_stall())

    async def _start_encoder(self, source_reader: asyncio.StreamReader) -> None:
        audio_filter = self._build_audio_filter()
        if settings.audio.auto_transcode or audio_filter:
            self._encoder = AudioEncoder(
                self._audio_config(), self._input_sample_rate, audio_filter=audio_filter
            )
            await self._encoder.start()
            self._tasks = [
                asyncio.create_task(
                    self._pump_source_to_encoder(source_reader, self._encoder.stdin)
                ),
                asyncio.create_task(self._pump_encoder_to_stream(self._encoder.stdout)),
                asyncio.create_task(self._watch_encoder()),
            ]
        else:
            self._encoder = None
            self._tasks = [
                asyncio.create_task(self._pump_source_to_stream(source_reader)),
            ]

    async def restart_encoder(self) -> None:
        """Rebuild only the encoder stage with current audio settings.

        The PCM source (AirPlay session) and the speaker's stream endpoint stay
        up, so a format change costs a sub-second encoder gap instead of a full
        engine restart that drops every connection.
        """
        if not self._running:
            return
        logger.info("Restarting encoder for %s (%s)", self._stream_id, self.alias)
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
        if self._encoder:
            await self._encoder.stop()
            self._encoder = None

        self.register_stream()
        source_reader = await self._pcm_source.start()
        await self._start_encoder(source_reader)
        self._last_feed_at = time.monotonic()

    async def stop(self, keep_stream: bool = False) -> None:
        if not self._running:
            return
        self._running = False
        self._status = "stopping"
        logger.info("Stopping pipeline for %s", self._stream_id)

        if self._stall_task and not self._stall_task.done():
            self._stall_task.cancel()
            await asyncio.gather(self._stall_task, return_exceptions=True)
        self._stall_task = None

        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()

        await self._pcm_source.stop()
        if self._encoder:
            await self._encoder.stop()

        # keep_stream: a rebuilt pipeline re-registers the same stream id, so
        # connected speakers survive a topology change without reconnecting.
        if not keep_stream:
            self._stream_server.unregister_stream(self._stream_id)
        self._status = "idle"

    async def session_start(self) -> None:
        logger.info("AirPlay session started on receiver %s", self.device_id)
        # Pipelines are long-lived. Without resetting this timestamp, a fresh
        # connection inherits all idle time since startup and can be declared
        # stalled before its first packet arrives.
        self._last_feed_at = time.monotonic()
        self._stall_armed = True
        if self._on_session_start:
            try:
                await self._on_session_start(self.device_id)
            except Exception:
                logger.exception("session_start hook failed for %s", self.device_id)

    def _note_source_bytes(self, chunk: bytes) -> None:
        if chunk:
            self._last_feed_at = time.monotonic()
            self._stall_armed = False

    async def _watch_source_stall(self) -> None:
        """Restart the PCM source when it stops producing during a live session.

        The reads above have no timeout, so a wedged TCP socket or a dead
        receiver process that never closes its pipe stalls the encoder forever
        without a single error. Speakers then reconnect to a silent stream in a
        tight loop. Rebuilding the source (reconnect/respawn) is the only cure.
        """
        try:
            while self._running:
                await asyncio.sleep(SOURCE_STALL_CHECK_SECONDS)
                if not self._session_active or not self._session_active():
                    self._stall_armed = False
                    continue
                if self._source_restart_lock.locked():
                    continue
                silence = self.source_silence_seconds
                if silence is None or silence < SOURCE_STALL_TIMEOUT_SECONDS:
                    continue
                if not self._stall_armed:
                    continue
                self._stall_armed = False
                logger.warning(
                    "PCM source for %s produced nothing for %.0fs during a live "
                    "session; restarting the source",
                    self._stream_id,
                    silence,
                )
                if self._on_source_stall is not None:
                    # A ReaderPCMSource cannot repair its upstream RAOP/TCP
                    # producer by restarting around the same dead reader.
                    asyncio.create_task(self._on_source_stall(self._stream_id))
                    return
                await self._restart_source()
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.exception("Source stall watchdog failed for %s", self._stream_id)

    async def _restart_source(self) -> None:
        """Rebuild source + encoder after a stall. The stream endpoint stays
        registered, so connected speakers rejoin the moment bytes flow again."""
        async with self._source_restart_lock:
            if not self._running:
                return
            for task in self._tasks:
                task.cancel()
            await asyncio.gather(*self._tasks, return_exceptions=True)
            self._tasks.clear()
            if self._encoder:
                await self._encoder.stop()
                self._encoder = None
            await self._pcm_source.stop()
            self._last_feed_at = time.monotonic()
            self.register_stream()
            try:
                source_reader = await self._pcm_source.start()
                await self._start_encoder(source_reader)
            except Exception:
                logger.exception("Failed to restart stalled source for %s", self._stream_id)
                self._status = "error"

    async def _pump_source_to_encoder(self, reader: asyncio.StreamReader, writer) -> None:
        try:
            rate = self._input_sample_rate or 44100
            byte_rate = rate * 4  # s16 stereo
            loop = asyncio.get_running_loop()
            started_at = loop.time()
            fed_bytes = 0
            while self._running:
                chunk = await reader.read(32768)
                if not chunk:
                    break
                self._note_source_bytes(chunk)
                writer.write(self._apply_input_gain(chunk))
                fed_bytes += len(chunk)
                # Never run ahead of real time: a tee backlog (restart window)
                # must drain at 1x, not burst into the encoder and overflow
                # client queues.
                ahead = fed_bytes / byte_rate - (loop.time() - started_at)
                if self._pace_source and ahead > 0:
                    await asyncio.sleep(ahead)
                await writer.drain()
            writer.write_eof()
            await writer.drain()
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.exception("Error pumping PCM to encoder for %s", self._stream_id)
            self._status = "error"

    async def _pump_encoder_to_stream(self, reader) -> None:
        try:
            while self._running:
                chunk = await reader.read(32768)
                if not chunk:
                    break
                await self._stream_server.broadcast(self._stream_id, chunk)
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.exception("Error pumping encoder to stream for %s", self._stream_id)
            self._status = "error"

    async def _pump_source_to_stream(self, reader: asyncio.StreamReader) -> None:
        try:
            rate = self._input_sample_rate or 44100
            byte_rate = rate * 4
            loop = asyncio.get_running_loop()
            started_at = loop.time()
            fed_bytes = 0
            if self._running:
                # Raw PCM bypass: prepend a streaming WAV header so the stream
                # is a valid container (and gets cached as the join prefix).
                await self._stream_server.broadcast(self._stream_id, wav_header(rate))
            while self._running:
                chunk = await reader.read(32768)
                if not chunk:
                    break
                self._note_source_bytes(chunk)
                await self._stream_server.broadcast(self._stream_id, self._apply_input_gain(chunk))
                fed_bytes += len(chunk)
                ahead = fed_bytes / byte_rate - (loop.time() - started_at)
                if self._pace_source and ahead > 0:
                    await asyncio.sleep(ahead)
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.exception("Error pumping PCM to stream for %s", self._stream_id)
            self._status = "error"

    async def _watch_encoder(self) -> None:
        if not self._encoder:
            return
        try:
            await self._encoder.wait()
            if self._running:
                logger.warning(
                    "Encoder exited for %s with code %s",
                    self._stream_id,
                    self._encoder.returncode,
                )
                self._status = "restarting"
                asyncio.create_task(self._delayed_restart())
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.exception("Error watching encoder for %s", self._stream_id)

    async def _delayed_restart(self) -> None:
        await asyncio.sleep(3)
        if self._running:
            await self.stop()
            await self.start()
