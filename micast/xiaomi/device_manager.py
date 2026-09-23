"""Device discovery and playback control manager."""

import asyncio
import json
import logging
import os
import tempfile
import time
from collections.abc import Awaitable, Callable
from pathlib import Path

from micast.config import settings
from micast.xiaomi.auth import XiaomiAuth, XiaomiAuthError
from micast.xiaomi.mina_api import MinaAPI

# Owner tag for playback started outside MiCast streams (debug test tone/URL).
# Watched but never restored: when the speaker goes quiet the state is cleared.
MANUAL_PLAY_OWNER = "debug"

logger = logging.getLogger(__name__)

# Minimum seconds between watchdog-triggered stream restores per speaker;
# tighter loops make the speaker announce 播放失败 repeatedly when the stream
# is genuinely unavailable (e.g. during a bridge restart).
RESTORE_MIN_INTERVAL_SECONDS = 15.0

# Seconds between retries for speakers that rejected the session-start play
# command (offline at start, cloud hiccup). Retries back off exponentially and
# stop after PLAY_ERROR_MAX_ATTEMPTS so a speaker that left the account or is
# permanently offline does not hammer the cloud forever; the recorded error
# stays visible until the session stops.
PLAY_ERROR_RETRY_SECONDS = 30.0
PLAY_ERROR_MAX_ATTEMPTS = 5
PLAY_ERROR_BACKOFF_FACTOR = 2.0
PLAY_ERROR_MAX_BACKOFF_SECONDS = 300.0

# Xiaomi's status helper performs an implicit device-list request before each
# player query. A two-second watchdog therefore doubled cloud traffic without
# improving recovery in practice; three five-second misses still fit the
# existing 15-second restore guard.
STATUS_CHECK_INTERVAL_SECONDS = 5.0

# Cloud device-list cache; see DeviceManager._devices_fetched_at.
DEVICE_LIST_CACHE_SECONDS = 30.0


class DeviceManager:
    """Manages Xiaomi speakers, aliases, enabled targets, and playback."""

    def __init__(self, auth: XiaomiAuth):
        self.auth = auth
        self._service = None
        self._devices: list[dict] = []
        # device_list hits Xiaomi's cloud; the 诊断 page alone would otherwise
        # hammer it every 1.5s and get the account throttled (devices then
        # "vanish"). Serve the cached list within the TTL; callers needing
        # ground truth (post-login, explicit refresh) pass force=True.
        self._devices_fetched_at = 0.0
        self._watchdog_tasks: dict[str, asyncio.Task] = {}
        self._playing: set[str] = set()
        self._paused: set[str] = set()
        self._stream_urls: dict[str, str] = {}
        self._volumes: dict[str, int] = {}
        # Web-UI physical mute: muted speakers and the level to restore on unmute.
        self._muted: set[str] = set()
        self._pre_mute_volumes: dict[str, int] = {}
        # Serializes cloud commands per speaker so pause/play cannot interleave,
        # and tracks which receiver currently owns each speaker.
        self._locks: dict[str, asyncio.Lock] = {}
        # Ownership keys are namespaced per ingress: bare ``receiver_id`` for
        # AirPlay/local sessions, ``dlna:{receiver_id}`` for the DLNA ingress
        # (see DlnaService._owner), and MANUAL_PLAY_OWNER ("debug") for the
        # manual test-tone/URL player.
        self._owners: dict[str, str] = {}
        self._last_restore: dict[str, float] = {}
        # Speakers that rejected the play command at session start (offline,
        # cloud timeout…). Retried periodically while the session lives.
        self._play_errors: dict[str, dict] = {}
        # Failed-retry count per device, driving the backoff above.
        self._play_error_attempts: dict[str, int] = {}
        # Learned from real HTTP pulls, not cloud command acknowledgements.
        # Kept in memory because firmware updates can change decoder behavior.
        self._codec_capabilities: dict[str, dict[str, bool]] = {}
        self._codec_capability_meta: dict[str, dict[str, dict]] = {}
        self._codec_capability_path = settings.config_path.parent / "xiaomi-codec-capabilities.json"
        self._codec_save_scheduled = False
        self._codec_save_deadline = 0.0
        self._load_codec_capabilities()
        self._error_retry_task: asyncio.Task | None = None
        # Optional ground-truth hook (wired by main): True when the speaker is
        # actually pulling its stream. The cloud reports "playing" even when
        # the speaker fetches nothing, so the watchdog double-checks with this.
        self.stream_active: Callable[[str], bool] | None = None
        # Groups paused because their anchor speaker went offline (keyed by
        # group id). The anchor's own watchdog keeps polling to detect recovery.
        self._anchor_paused: set[str] = set()
        # Wired by main.py: pause/resume the whole group when its anchor drops.
        self.on_anchor_offline: Callable[[str], Awaitable[None]] | None = None
        self.on_anchor_recovered: Callable[[str], Awaitable[None]] | None = None

    @property
    def selected_device_id(self) -> str | None:
        return settings.selected_device_id

    def reset(self) -> None:
        """Drop every cached device/playback state (清空数据 → 回到引导页)."""
        for task in self._watchdog_tasks.values():
            task.cancel()
        self._watchdog_tasks.clear()
        self._devices = []
        self._service = None
        self._playing.clear()
        self._paused.clear()
        self._stream_urls.clear()
        self._volumes.clear()
        self._muted.clear()
        self._pre_mute_volumes.clear()
        self._owners.clear()
        self._last_restore.clear()
        self._play_errors.clear()
        self._play_error_attempts.clear()
        self._codec_capabilities.clear()
        self._codec_capability_meta.clear()
        # Engine restarts land here; an unconditional mkdir+fsync on that hot
        # path is wasteful. The debounced flush covers it (sync fallback when
        # no loop is running), and close() force-flushes before shutdown.
        self._schedule_codec_capability_save()
        self._anchor_paused.clear()
        if self._error_retry_task:
            self._error_retry_task.cancel()
            self._error_retry_task = None

    async def close(self) -> None:
        """Cancel and join every manager-owned background task."""
        # Persist any capability record still waiting in the debounce window.
        self._codec_save_deadline = 0.0
        self._save_codec_capabilities()
        tasks = [task for task in self._watchdog_tasks.values() if not task.done()]
        if self._error_retry_task and not self._error_retry_task.done():
            tasks.append(self._error_retry_task)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._watchdog_tasks.clear()
        self._error_retry_task = None

    @selected_device_id.setter
    def selected_device_id(self, value: str | None) -> None:
        settings.select_device(value)

    async def refresh_service(self) -> bool:
        """Ensure MiNAService is available."""
        # XiaomiAuth replaces its cached service after silent token renewal.
        # Always take its current instance so a long-running DeviceManager
        # never keeps issuing commands with the retired serviceToken.
        service = await self.auth.ensure_service()
        if self._service is not service:
            self._service = service
        return self._service is not None

    async def list_devices(self, force: bool = False) -> list[dict]:
        _, account_id = self.auth.stored_identity()
        settings.bind_provider_account(account_id)
        if (
            not force
            and self._devices
            and (time.monotonic() - self._devices_fetched_at < DEVICE_LIST_CACHE_SECONDS)
        ):
            return self._devices
        if not await self.refresh_service():
            return []
        api = MinaAPI(self._service, "")
        try:
            self._devices = await api.device_list()
        except Exception as exc:
            # Never trust miservice's error text: "Login failed" also wraps
            # pure network errors, and an expired serviceToken can surface as
            # an opaque {"code": ...} body. Ask Xiaomi directly: a rejected
            # passToken kills the login, a working one heals the serviceToken
            # and earns one retry, and an inconclusive check keeps everything.
            logger.warning("device_list failed (%s); verifying login", exc)
            verdict = await self.auth.recover_after_failure()
            if verdict == "rejected":
                self._service = None
                self._devices = []
                raise XiaomiAuthError("小米登录已失效，请重新登录") from exc
            if verdict == "healed" and await self.refresh_service():
                self._devices = await MinaAPI(self._service, "").device_list()
            else:
                raise
        self._devices_fetched_at = time.monotonic()
        settings.merge_speakers(self._devices)
        return self._devices

    def consume_merge_rewrite(self) -> bool:
        """True (once) when the last device merge migrated an obsolete device
        id — callers must then rebuild affected pipelines (stream ids still
        reference the old did until they are)."""
        return settings.consume_merge_rewrite()

    def get_alias(self, device_id: str) -> str:
        speaker = settings.get_speaker(device_id)
        if speaker and speaker.alias:
            return speaker.alias
        device = next((d for d in self._devices if d.get("deviceID") == device_id), None)
        if device:
            return device.get("name") or device.get("hardware") or device_id
        return device_id

    def set_alias(self, device_id: str, alias: str) -> None:
        settings.set_alias(device_id, alias)

    def is_enabled(self, device_id: str) -> bool:
        speaker = settings.get_speaker(device_id)
        return speaker.enabled if speaker else False

    def set_enabled(self, device_id: str, enabled: bool) -> None:
        settings.set_enabled(device_id, enabled)

    def select_device(self, device_id: str | None) -> bool:
        if device_id is None:
            self.selected_device_id = None
            return True
        if any(d.get("deviceID") == device_id for d in self._devices):
            self.selected_device_id = device_id
            return True
        return False

    def _lock_for(self, device_id: str) -> asyncio.Lock:
        lock = self._locks.get(device_id)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[device_id] = lock
        return lock

    def owner_of(self, device_id: str) -> str | None:
        return self._owners.get(device_id)

    def owned_targets(self, receiver_id: str, owner: str) -> list[str]:
        """Xiaomi speakers of `receiver_id` currently owned by `owner`.

        Ownership is an exact-string match. AirPlay/local sessions store the
        bare ``receiver_id``; the DLNA ingress namespaces its owner as
        ``dlna:{receiver_id}`` (see DlnaService._owner) so both can target the
        same group without stealing each other's speakers.
        """
        return [
            did for did in settings.receiver_targets(receiver_id) if self.owner_of(did) == owner
        ]

    def stream_url_of(self, device_id: str) -> str | None:
        return self._stream_urls.get(device_id)

    def anchor_group_of(self, did: str) -> str | None:
        """Return the id of the group whose anchor speaker is `did`, if any."""
        for group in settings.groups:
            if group.anchor_did == did:
                return group.id
        return None

    async def play_stream(
        self,
        device_id: str,
        url: str,
        owner: str | None = None,
        force: bool = False,
        audio_id: str | None = None,
    ) -> bool:
        """Start a stream on a speaker; serialized per device and idempotent.

        ``owner`` is the receiver driving this playback. A different owner steals
        control (latest AirPlay session wins); repeats from the current owner
        with the same URL are skipped so session churn does not restart audio.
        ``force`` bypasses the idempotency check (used by the watchdog restore).
        ``audio_id`` attaches a Xiaomi library song (cover + lyrics on
        touch-screen speakers).
        """
        async with self._lock_for(device_id):
            if not await self.refresh_service():
                return False
            current_owner = self._owners.get(device_id)
            if (
                not force
                and owner is not None
                and device_id in self._playing
                and self._stream_urls.get(device_id) == url
                and (current_owner is None or current_owner == owner)
            ):
                logger.debug("Speaker %s already playing %s; skipping", device_id, url)
                return True
            if owner is not None and current_owner and current_owner != owner:
                logger.info("Speaker %s ownership: %s -> %s", device_id, current_owner, owner)
            api = MinaAPI(self._service, device_id)
            try:
                await api.play_music_url(url, audio_id=audio_id)
            except Exception:
                logger.warning("play_music_url failed for %s, falling back to play_url", device_id)
                await api.play_url(url)
            self._playing.add(device_id)
            self._paused.discard(device_id)
            self._stream_urls[device_id] = url
            if owner is not None:
                self._owners[device_id] = owner
            self._start_watchdog(device_id)
            return True

    async def search_audio_id(
        self, title: str, artist: str = "", fuzzy_fallback: bool = True
    ) -> str:
        """Search Xiaomi's music library for a song's audioID ("" if no hit)."""
        if not await self.refresh_service():
            return ""
        api = MinaAPI(self._service, next(iter(self._playing), ""))
        return await api.search_audio_id(title, artist, fuzzy_fallback)

    async def resume(self, device_id: str) -> bool:
        """Resume a paused speaker by re-pushing its stream URL.

        A plain player "play" is acknowledged by the cloud but the speaker
        rarely re-pulls the URL it dropped when paused — it reports "playing"
        while fetching nothing. Our streams are live, so replaying the URL
        simply rejoins the live edge."""
        async with self._lock_for(device_id):
            if not await self.refresh_service():
                return False
            api = MinaAPI(self._service, device_id)
            url = self._stream_urls.get(device_id)
            try:
                if url:
                    await api.play_music_url(url)
                else:
                    await api.play()
            except Exception:
                if not url:
                    raise
                logger.warning("resume failed for %s, re-issuing stream URL", device_id)
                await api.play_url(url)
            self._playing.add(device_id)
            self._paused.discard(device_id)
            self._start_watchdog(device_id)
            return True

    async def stop(self, device_id: str, owner: str | None = None) -> None:
        """Pause a speaker; ignored when a different receiver owns it now."""
        async with self._lock_for(device_id):
            if owner is not None and self._owners.get(device_id) not in (None, owner):
                logger.info(
                    "Ignoring stop from %s on %s: owned by %s",
                    owner,
                    device_id,
                    self._owners.get(device_id),
                )
                return
            if not self._service:
                return
            api = MinaAPI(self._service, device_id)
            try:
                await api.pause()
            except Exception as exc:
                logger.warning("pause failed for %s: %s", device_id, exc)
            self._playing.discard(device_id)
            self._paused.add(device_id)
            self._play_errors.pop(device_id, None)
            self._play_error_attempts.pop(device_id, None)
            if owner is not None and self._owners.get(device_id) == owner:
                self._owners.pop(device_id, None)
            self._stop_watchdog(device_id)

    async def stop_playback(
        self, device_id: str, owner: str | None = None, *, keep_error: bool = False
    ) -> None:
        """Fully stop a speaker (not pause) and forget its stream state.

        Unlike pause, this unloads the stream URL — a paused speaker keeps the
        URL and retries it on its own, which leaves ghost connections on the
        stream server long after the phone is gone.

        ``keep_error`` preserves a recorded session-start error (callers that
        stop the speaker BECAUSE of that error want the error to stay visible)."""
        async with self._lock_for(device_id):
            if owner is not None and self._owners.get(device_id) not in (None, owner):
                logger.info(
                    "Ignoring stop_playback from %s on %s: owned by %s",
                    owner,
                    device_id,
                    self._owners.get(device_id),
                )
                return
            if await self.refresh_service():
                api = MinaAPI(self._service, device_id)
                # player_stop alone is ignored by some firmware during
                # player_play_music playback; pause actually cuts the audio.
                for command in (api.pause, api.stop):
                    try:
                        await command()
                    except Exception as exc:
                        logger.warning("%s failed for %s: %s", command.__name__, device_id, exc)
            self._playing.discard(device_id)
            self._paused.discard(device_id)
            if not keep_error:
                self._play_errors.pop(device_id, None)
                self._play_error_attempts.pop(device_id, None)
            self._stream_urls.pop(device_id, None)
            self._owners.pop(device_id, None)
            self._stop_watchdog(device_id)

    def playing_ids(self) -> list[str]:
        return list(self._playing)

    def note_play_error(
        self,
        device_id: str,
        receiver_id: str,
        error: str,
        url: str | None = None,
        *,
        retry: bool = True,
    ) -> None:
        """Record a session-start failure so it can be retried and displayed."""
        attempts_map = getattr(self, "_play_error_attempts", None)
        if attempts_map is None:  # tolerate __new__-built instances in tests
            attempts_map = self._play_error_attempts = {}
        self._play_errors[device_id] = {
            "receiver": receiver_id,
            "error": error,
            "url": url or self._stream_urls.get(device_id),
        }
        attempts_map[device_id] = 0
        if retry and (self._error_retry_task is None or self._error_retry_task.done()):
            self._error_retry_task = asyncio.create_task(self._error_retry_loop())

    def clear_play_error(self, device_id: str) -> None:
        self._play_errors.pop(device_id, None)
        self._play_error_attempts.pop(device_id, None)

    def play_errors(self) -> dict[str, str]:
        return {did: entry["error"] for did, entry in self._play_errors.items()}

    def note_codec_capability(
        self, device_id: str, fmt: str, supported: bool, reason: str = "stream_verified"
    ) -> None:
        if fmt:
            self._codec_capabilities.setdefault(device_id, {})[fmt] = supported
            self._codec_capability_meta.setdefault(device_id, {})[fmt] = {
                "status": "supported" if supported else "unsupported",
                "verified_at": int(time.time()),
                "reason": reason,
            }
            self._schedule_codec_capability_save()

    def codec_capabilities(self, device_id: str) -> dict[str, bool]:
        return dict(self._codec_capabilities.get(device_id, {}))

    def codec_capability_details(self, device_id: str) -> dict[str, dict]:
        return {
            fmt: dict(meta)
            for fmt, meta in self._codec_capability_meta.get(device_id, {}).items()
        }

    def _load_codec_capabilities(self) -> None:
        try:
            raw = json.loads(self._codec_capability_path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                return
            for did, formats in raw.items():
                if not isinstance(formats, dict):
                    continue
                for fmt, meta in formats.items():
                    if not isinstance(meta, dict) or meta.get("status") not in {
                        "supported",
                        "unsupported",
                    }:
                        continue
                    self._codec_capability_meta.setdefault(str(did), {})[str(fmt)] = dict(meta)
                    supported = meta["status"] == "supported"
                    self._codec_capabilities.setdefault(str(did), {})[str(fmt)] = supported
        except FileNotFoundError:
            return
        except Exception:
            logger.exception("Failed to load persisted Xiaomi codec capabilities")

    def _schedule_codec_capability_save(self) -> None:
        """Debounce disk writes: a session-start probe can record several
        speakers within milliseconds, and each is not worth its own fsync."""
        self._codec_save_deadline = time.monotonic() + 2.0
        if self._codec_save_scheduled:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            self._save_codec_capabilities()
            return
        self._codec_save_scheduled = True
        loop.call_later(2.0, self._flush_codec_capability_save)

    def _flush_codec_capability_save(self) -> None:
        self._codec_save_scheduled = False
        # A newer record arrived after this flush was scheduled: slide once more.
        remaining = self._codec_save_deadline - time.monotonic()
        if remaining > 0:
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                self._save_codec_capabilities()
                return
            self._codec_save_scheduled = True
            loop.call_later(remaining, self._flush_codec_capability_save)
            return
        self._save_codec_capabilities()

    def _save_codec_capabilities(self) -> None:
        try:
            self._codec_capability_path.parent.mkdir(parents=True, exist_ok=True)
            payload = json.dumps(self._codec_capability_meta, ensure_ascii=False, indent=2)
            # Same atomic pattern as the main config: a half-written file on
            # NAS storage must never be picked up on the next boot.
            fd, temporary = tempfile.mkstemp(
                prefix=f".{self._codec_capability_path.name}.",
                suffix=".tmp",
                dir=self._codec_capability_path.parent,
                text=True,
            )
            temporary_path = Path(temporary)
            try:
                with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
                    handle.write(payload)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temporary_path, self._codec_capability_path)
            finally:
                temporary_path.unlink(missing_ok=True)
        except Exception:
            logger.exception("Failed to persist Xiaomi codec capabilities")

    def codec_compatibility(self, device_ids: list[str]) -> dict:
        formats = ("MP3", "FLAC", "WAV", "PCM/WAV")
        ids = list(dict.fromkeys(device_ids))
        members = {did: self.codec_capabilities(did) for did in ids}
        possible = [
            fmt for fmt in formats if all(cap.get(fmt) is not False for cap in members.values())
        ]
        confirmed = [
            fmt for fmt in formats if ids and all(cap.get(fmt) is True for cap in members.values())
        ]
        status = "confirmed" if confirmed else "incompatible" if not possible else "needs_check"
        return {
            "members": members,
            "possible_common_formats": possible,
            "confirmed_common_formats": confirmed,
            "recommended_format": "MP3"
            if "MP3" in possible
            else (possible[0] if possible else None),
            "status": status,
            "unknown_members": [did for did, cap in members.items() if not cap],
        }

    async def _error_retry_loop(self) -> None:
        """Re-issue the stream to speakers that failed at session start.

        Backs off exponentially per device and gives up after
        PLAY_ERROR_MAX_ATTEMPTS: a speaker removed from the account or powered
        off for good must not be retried (and cloud-hammered) forever. The
        recorded error is dropped together with the retry state; normal clears
        (session stop, manual stop, successful retry) also prune the counter.
        """
        attempts_map = getattr(self, "_play_error_attempts", None)
        if attempts_map is None:  # tolerate __new__-built instances in tests
            attempts_map = self._play_error_attempts = {}
        while self._play_errors:
            pending = [did for did in self._play_errors if did in attempts_map]
            delays = [
                min(
                    PLAY_ERROR_RETRY_SECONDS
                    * (PLAY_ERROR_BACKOFF_FACTOR ** max(0, attempts_map[did] - 1)),
                    PLAY_ERROR_MAX_BACKOFF_SECONDS,
                )
                for did in pending
            ]
            await asyncio.sleep(min(delays) if delays else PLAY_ERROR_RETRY_SECONDS)
            for did, entry in list(self._play_errors.items()):
                url = entry.get("url")
                if not url:
                    self._play_errors.pop(did, None)
                    attempts_map.pop(did, None)
                    continue
                if attempts_map.get(did, 0) >= PLAY_ERROR_MAX_ATTEMPTS:
                    logger.warning(
                        "Speaker %s still not playing after %d retries; "
                        "giving up until the next session",
                        did,
                        PLAY_ERROR_MAX_ATTEMPTS,
                    )
                    self._play_errors.pop(did, None)
                    attempts_map.pop(did, None)
                    continue
                try:
                    await self.play_stream(did, url, owner=entry.get("receiver"), force=True)
                except Exception as exc:
                    logger.debug("Retry of failed speaker %s: %s", did, exc)
                    attempts_map[did] = attempts_map.get(did, 0) + 1
                    continue
                logger.info("Speaker %s recovered after a failed start", did)
                self._play_errors.pop(did, None)
                attempts_map.pop(did, None)

    async def set_volume(self, device_id: str, volume: int) -> int:
        """Set and cache speaker volume after the device accepts it."""
        if not 0 <= volume <= 100:
            raise ValueError("volume must be 0-100")
        if not await self.refresh_service():
            raise XiaomiAuthError("小米登录已失效，请重新登录")
        api = MinaAPI(self._service, device_id)
        await api.set_volume(volume)
        self._volumes[device_id] = volume
        return volume

    async def get_volume(self, device_id: str, refresh: bool = False) -> int | None:
        """Fresh reads never disguise stale cache as the current device level."""
        if not refresh and device_id in self._volumes:
            return self._volumes[device_id]
        if not await self.refresh_service():
            return None if refresh else self._volumes.get(device_id)
        try:
            status = await MinaAPI(self._service, device_id).get_status()
            volume = _find_volume(status)
            if volume is not None:
                self._volumes[device_id] = volume
            return volume if refresh or volume is not None else self._volumes.get(device_id)
        except Exception as exc:
            logger.debug("Unable to read volume for %s: %s", device_id, exc)
            return None if refresh else self._volumes.get(device_id)

    def is_playing(self, device_id: str) -> bool:
        return device_id in self._playing

    def is_paused(self, device_id: str) -> bool:
        return device_id in self._paused

    def is_muted(self, device_id: str) -> bool:
        return device_id in self._muted

    def muted_devices(self) -> list[str]:
        return sorted(self._muted)

    def note_manual_play(self, device_id: str, url: str) -> None:
        """Record playback started outside the stream pipeline (debug tests).

        Unlike play_stream this only *observes*: the watchdog clears the state
        when the speaker goes quiet instead of restoring the URL, so one-shot
        media (test tones) is not replayed in a loop. Existing receiver
        ownership is kept — a manual test does not steal the speaker.
        """
        self._playing.add(device_id)
        self._paused.discard(device_id)
        self._stream_urls.setdefault(device_id, url)
        if self._owners.get(device_id) is None:
            self._owners[device_id] = MANUAL_PLAY_OWNER
        self._start_watchdog(device_id)

    def _start_watchdog(self, device_id: str) -> None:
        task = self._watchdog_tasks.get(device_id)
        if task and not task.done():
            return
        self._watchdog_tasks[device_id] = asyncio.create_task(self._watchdog_loop(device_id))

    def _stop_watchdog(self, device_id: str) -> None:
        task = self._watchdog_tasks.pop(device_id, None)
        if task and not task.done():
            task.cancel()

    async def _watchdog_loop(self, device_id: str) -> None:
        """Restore an active AirPlay stream after voice-assistant interruptions."""
        inactive_checks = 0
        while device_id in self._playing:
            try:
                await asyncio.sleep(STATUS_CHECK_INTERVAL_SECONDS)
                if not self._service:
                    continue
                api = MinaAPI(self._service, device_id)
                status = await api.get_status()
                logger.debug("Speaker %s status: %s", device_id, status)
                play_status = _find_play_status(status)
                active = play_status is None or play_status == 1
                if active and play_status == 1 and self.stream_active is not None:
                    try:
                        active = self.stream_active(device_id)
                    except Exception:
                        active = True  # never restore on a checker failure
                if active:
                    inactive_checks = 0
                    anchor_group = self.anchor_group_of(device_id)
                    if (
                        anchor_group is not None
                        and anchor_group in self._anchor_paused
                        and self.on_anchor_recovered is not None
                    ):
                        self._anchor_paused.discard(anchor_group)
                        try:
                            await self.on_anchor_recovered(anchor_group)
                        except Exception:
                            logger.exception("anchor-recovered hook failed for %s", anchor_group)
                    continue
                inactive_checks += 1
                if inactive_checks < 3:
                    continue
                anchor_group = self.anchor_group_of(device_id)
                if (
                    anchor_group is not None
                    and anchor_group not in self._anchor_paused
                    and self.on_anchor_offline is not None
                ):
                    # The anchor dropped: pause the whole group instead of
                    # restoring just this speaker. Its watchdog keeps polling
                    # and fires the recovered hook when it comes back.
                    self._anchor_paused.add(anchor_group)
                    try:
                        await self.on_anchor_offline(anchor_group)
                    except Exception:
                        logger.exception("anchor-offline hook failed for %s", anchor_group)
                    inactive_checks = 0
                    continue
                url = self._stream_urls.get(device_id)
                if not url or device_id not in self._playing:
                    continue
                if self._owners.get(device_id) == MANUAL_PLAY_OWNER:
                    # One-shot manual playback ended on its own; forget it.
                    self._playing.discard(device_id)
                    self._owners.pop(device_id, None)
                    self._stream_urls.pop(device_id, None)
                    break
                now = time.monotonic()
                if now - self._last_restore.get(device_id, 0.0) < RESTORE_MIN_INTERVAL_SECONDS:
                    continue
                self._last_restore[device_id] = now
                logger.info(
                    "Speaker %s was interrupted (status=%s); restoring AirPlay stream",
                    device_id,
                    play_status,
                )
                await self.play_stream(
                    device_id, url, owner=self._owners.get(device_id), force=True
                )
                inactive_checks = 0
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning("Watchdog error for %s: %s", device_id, e)

    def get_active_targets(self) -> list[dict]:
        """Return devices that should currently output audio.

        In single mode this is the selected device. In multi mode it is all enabled devices.
        """
        if settings.receiver_mode == "multi":
            return [d for d in self._devices if self.is_enabled(d.get("deviceID", ""))]
        selected = self.selected_device_id
        if selected:
            return [d for d in self._devices if d.get("deviceID") == selected]
        return []

    def get_control_targets(self) -> list[dict]:
        """Prefer speakers in the current session for playback controls."""
        if self._playing:
            return [device for device in self._devices if device.get("deviceID") in self._playing]
        if self._paused:
            return [device for device in self._devices if device.get("deviceID") in self._paused]
        return self.get_active_targets()

    def list_targets(self) -> list[dict]:
        """Return devices enriched with alias, enabled, and selected fields."""
        result = []
        for device in self._devices:
            did = device.get("deviceID", "")
            speaker = settings.get_speaker(did)
            result.append(
                {
                    **device,
                    "did": did,
                    "alias": self.get_alias(did),
                    "enabled": speaker.enabled if speaker else False,
                    "selected": did == self.selected_device_id,
                    "play_error": self._play_errors.get(did, {}).get("error"),
                    "codec_capabilities": self.codec_capabilities(did),
                }
            )
        return result


def _find_volume(value) -> int | None:
    """Extract volume from the different MiNA status response shapes."""
    if isinstance(value, str):
        import json

        try:
            return _find_volume(json.loads(value))
        except (json.JSONDecodeError, TypeError):
            return int(value) if value.isdigit() and 0 <= int(value) <= 100 else None
    if isinstance(value, dict):
        for key in ("volume", "volume_level", "volumeLevel"):
            candidate = value.get(key)
            if isinstance(candidate, (int, float)):
                return max(0, min(100, int(candidate)))
            if isinstance(candidate, str) and candidate.isdigit():
                return max(0, min(100, int(candidate)))
        for nested in value.values():
            found = _find_volume(nested)
            if found is not None:
                return found
    elif isinstance(value, list):
        for nested in value:
            found = _find_volume(nested)
            if found is not None:
                return found
    return None


def _find_play_status(value) -> int | None:
    """Extract MiNA's player status, including its JSON-encoded ``data.info`` value."""
    if isinstance(value, str):
        import json

        try:
            return _find_play_status(json.loads(value))
        except (json.JSONDecodeError, TypeError):
            return None
    if isinstance(value, dict):
        info = value.get("info")
        if info is not None:
            found = _find_play_status(info)
            if found is not None:
                return found
        status = value.get("status")
        if isinstance(status, int) and not isinstance(status, bool):
            return status
        if isinstance(status, str) and status.isdigit():
            return int(status)
        for key, nested in value.items():
            if key in {"info", "status"}:
                continue
            found = _find_play_status(nested)
            if found is not None:
                return found
    elif isinstance(value, list):
        for nested in value:
            found = _find_play_status(nested)
            if found is not None:
                return found
    return None
