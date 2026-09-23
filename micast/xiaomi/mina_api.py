"""Thin wrapper around MiNAService for Xiaomi speaker control."""

import asyncio
import logging
import re
import time

from miservice import MiNAService

logger = logging.getLogger(__name__)
COMMAND_TIMEOUT_SECONDS = 15.0


class MinaAPI:
    """High-level API for a single Xiaomi speaker."""

    def __init__(self, service: MiNAService, device_id: str):
        self.service = service
        self.device_id = device_id

    async def _call(self, operation):
        """Bound third-party calls so one device cannot hold its lock forever.

        miservice-fork runs on a caller-owned aiohttp ClientSession, and
        aiohttp absorbs CancelledError by releasing the connection back to
        its pool, so wait_for's timeout cancellation is connection-safe and
        never poisons the shared session.
        """
        try:
            return await asyncio.wait_for(operation, timeout=COMMAND_TIMEOUT_SECONDS)
        except TimeoutError:
            raise TimeoutError(
                f"小米音箱命令超时（{COMMAND_TIMEOUT_SECONDS:.0f}s）：{self.device_id}"
            ) from None

    async def device_list(self) -> list[dict]:
        """Return all Xiaomi AI devices."""
        result = await self._call(self.service.device_list())
        if isinstance(result, list):
            return result
        return result.get("data", [])

    async def play_url(self, url: str) -> dict:
        logger.info("Playing URL on %s: %s", self.device_id, url)
        return await self._call(self.service.play_by_url(self.device_id, url))

    async def play_music_url(self, url: str, audio_id: str | None = None) -> dict:
        """Use the newer player_play_music ubus method; works better on some devices.

        audio_id: a Xiaomi music-library song id — touch-screen speakers then
        show that song's cover and scrolling lyrics while playing our stream.
        """
        logger.info("Playing music URL on %s: %s", self.device_id, url)
        kwargs = {"audio_id": audio_id} if audio_id else {}
        return await self._call(self.service.play_by_music_url(self.device_id, url, **kwargs))

    async def search_audio_id(
        self, title: str, artist: str = "", fuzzy_fallback: bool = True
    ) -> str:
        """Search Xiaomi's music library for a matching song's audioID.

        Prefers an exact title + artist-corroborated hit. fuzzy_fallback=False
        is for continuous matching (lyrics watcher): a scrolling-lyrics line
        fuzzy-"matching" a song would look like a track change and trigger
        repeated re-plays, so strict rejection is required there.
        """
        title = (title or "").strip()
        if not title:
            return ""
        artist = (artist or "").strip()
        # Senders often glue the title into the artist field
        # ("周杰伦--告白气球"); drop the duplicated half from the query.
        query_artist = artist
        for sep in ("--", " — ", " · ", "—", "·"):
            if sep not in artist:
                continue
            parts = [p.strip() for p in artist.split(sep)]
            if len(parts) != 2 or not all(parts):
                continue
            if parts[0].lower() == title.lower():
                query_artist = parts[1]
            elif parts[1].lower() == title.lower():
                query_artist = parts[0]
            break
        query = f"{title}-{query_artist}" if query_artist else title
        try:
            result = await self._call(self.service.mina_request(
                "/music/search",
                {
                    "query": query,
                    "queryType": "1",
                    "offset": "0",
                    "count": "6",
                    "timestamp": str(int(time.time() * 1000)),
                },
            ))
        except Exception as exc:
            logger.warning("曲库搜索失败 (%s): %s", query, exc)
            return ""
        song_list = (result or {}).get("data", {}).get("songList") or []
        if not song_list:
            return ""
        # Exact hit: title equal (case-insensitive); artist corroborated by
        # substring in either direction (sender formats vary wildly).
        first_artist = re.split(r"[;；,，&、/·・—]", artist)[0].strip() if artist else ""
        artist_l = artist.lower()
        for song in song_list:
            name = song.get("name") or ""
            song_artist = (song.get("artist") or {}).get("name") or ""
            if name.lower() != title.lower():
                continue
            if (
                first_artist
                and first_artist.lower() not in song_artist.lower()
                and not (song_artist and song_artist.lower() in artist_l)
            ):
                continue
            audio_id = str(song.get("audioID") or "")
            if audio_id:
                logger.info("曲库精确命中 (%s) audioID=%s", query, audio_id)
                return audio_id
        if fuzzy_fallback:
            audio_id = str(song_list[0].get("audioID") or "")
            if audio_id:
                logger.info(
                    "曲库无精确匹配，回退首条 (%s) %s audioID=%s",
                    query,
                    song_list[0].get("name", ""),
                    audio_id,
                )
                return audio_id
        return ""

    async def pause(self) -> dict:
        return await self._call(self.service.player_pause(self.device_id))

    async def play(self) -> dict:
        return await self._call(self.service.player_play(self.device_id))

    async def stop(self) -> dict:
        return await self._call(self.service.player_stop(self.device_id))

    async def set_volume(self, volume: int) -> dict:
        return await self._call(self.service.player_set_volume(self.device_id, volume))

    async def get_status(self) -> dict:
        return await self._call(self.service.player_get_status(self.device_id))
