"""FastAPI application entrypoint."""

import asyncio
import contextlib
import logging
import re
import sys
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from micast import __version__
from micast.access import COOKIE_NAME, AccessManager
from micast.audio_bridge import AudioBridge
from micast.config import resolve_port, settings
from micast.deployment import airplay2_mode
from micast.dlna import DlnaService
from micast.lyrics import LyricsSession
from micast.notify import Notifier, notify_expired_soon
from micast.paths import APP_BASE_PATH
from micast.routes import (
    access,
    airplay2,
    airplay_devices,
    config,
    debug,
    devices,
    dlna,
    dlna_devices,
    playback,
    receivers,
    status,
    topology,
    ws,
    xiaomi,
)
from micast.runtime_log import install_asyncio_exception_filter, install_runtime_log
from micast.xiaomi.auth import XiaomiAuth
from micast.xiaomi.device_manager import DeviceManager


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan events."""
    install_asyncio_exception_filter(asyncio.get_running_loop())
    settings.configure_airplay2_deployment(airplay2_mode())
    # Speakers pull audio from stream_port; if it's taken and the user didn't
    # pin it explicitly, slide to a free one rather than failing the session.
    settings.stream_port = resolve_port(settings.stream_port, "MICAST_STREAM_PORT")
    app.state.auth.on_login_expired = lambda: notify_expired_soon(app.state.notifier)
    bridge: AudioBridge = app.state.bridge
    device_manager: DeviceManager = app.state.device_manager
    dlna_service: DlnaService = app.state.dlna
    pending_stops: dict[str, asyncio.Task] = {}
    pending_group_recoveries: dict[str, asyncio.Task] = {}
    lyrics_sessions: dict[str, LyricsSession] = {}
    # receiver_id -> matched audioID (for /api/status now-playing display)
    bridge.lyrics_matched = {}

    async def resend_with_audio_id(receiver_id: str, audio_id: str) -> None:
        """Re-issue play with a library audioID so touch-screen speakers swap
        the default cover for real cover art + scrolling lyrics."""
        bridge.lyrics_matched[receiver_id] = audio_id
        for did in device_manager.playing_ids():
            if device_manager.owner_of(did) != receiver_id:
                continue
            url = device_manager.stream_url_of(did)
            if url:
                await device_manager.play_stream(
                    did, url, owner=receiver_id, force=True, audio_id=audio_id
                )

    async def start_lyrics_session(receiver_id: str) -> None:
        if not settings.touchscreen_lyrics:
            return
        server = bridge.local_server(receiver_id)
        if server is None:
            return
        old = lyrics_sessions.pop(receiver_id, None)
        if old:
            await old.stop()
        session = LyricsSession(
            receiver_id,
            server,
            device_manager,
            lambda audio_id: resend_with_audio_id(receiver_id, audio_id),
        )
        lyrics_sessions[receiver_id] = session
        session.start()

    async def apply_default_volume(receiver_id: str) -> None:
        if not settings.default_volume_enabled:
            return
        if bridge._volume_modes.get(receiver_id) == "linked":
            return  # The sender owns volume in linked mode; do not race it.
        await asyncio.sleep(0.5)  # let the play command land first
        for did in device_manager.owned_targets(receiver_id, receiver_id):
            try:
                await device_manager.set_volume(did, settings.default_volume)
            except Exception:
                logger.debug("默认音量设置失败 %s", did, exc_info=True)

    async def play_receiver(receiver_id: str, url: str, steal: bool = True):
        # Any playback (re)start cancels a pending stop from a recent teardown:
        # the stream outlives individual sessions, so quick reconnects are free.
        pending = pending_stops.pop(receiver_id, None)
        if pending:
            pending.cancel()
        targets = settings.receiver_targets(receiver_id)
        if not targets:
            logger.warning("Receiver %s has no playback target", receiver_id)
            return
        if not steal:
            # Resume blips only re-assert targets this receiver still owns;
            # never rip a speaker away from another active receiver.
            owned = [did for did in targets if device_manager.owner_of(did) in (None, receiver_id)]
            skipped = [did for did in targets if did not in owned]
            if skipped:
                logger.info(
                    "Receiver %s resumed; skipping %s (owned by another receiver)",
                    receiver_id,
                    skipped,
                )
            targets = owned
            if not targets:
                return
        # Delay now lives in the stream server's per-client buffer (keyed by
        # ?sink=): session start fires every target at once and the sink buffer
        # holds each speaker back by its own normalized offset. No start-time
        # stagger, and a member can also be pulled earlier in place.

        # One offline/unreachable speaker must not break the session-start
        # chain for the others — gather everything, then record the failures
        # so the watchdog can retry them and the UI can show them.
        attempted: dict[str, str] = {}

        async def play_target(did: str):
            # Stereo/EQ splits give each speaker its own stream suffix.
            # The suffix belongs to the path; the cache-buster query goes last.
            base = url + settings.stream_suffix(receiver_id, did)
            play_url = f"{base}/for/{receiver_id}/{did}?s={time.time_ns()}"
            attempted[did] = play_url
            await device_manager.play_stream(did, play_url, owner=receiver_id)

        results = await asyncio.gather(
            *(play_target(did) for did in targets), return_exceptions=True
        )
        for did, result in zip(targets, results, strict=True):
            if isinstance(result, Exception):
                logger.warning("Speaker %s failed to start for %s: %s", did, receiver_id, result)
                device_manager.note_play_error(
                    did, receiver_id, str(result), attempted.get(did)
                )
            else:
                device_manager.clear_play_error(did)

    async def on_session_start(receiver_id: str):
        url = f"http://{settings.effective_stream_host}:{settings.stream_port}/stream/{receiver_id}"
        await play_receiver(receiver_id, url)
        await start_lyrics_session(receiver_id)
        asyncio.create_task(apply_default_volume(receiver_id))

    async def on_session_stop(receiver_id: str):
        bridge._sender_volumes.pop(receiver_id, None)
        bridge._volume_modes.pop(receiver_id, None)
        lyrics = lyrics_sessions.pop(receiver_id, None)
        if lyrics:
            await lyrics.stop()
        bridge.lyrics_matched.pop(receiver_id, None)

        async def delayed_stop():
            try:
                await asyncio.sleep(SESSION_STOP_GRACE_SECONDS)
                await asyncio.gather(
                    *(
                        device_manager.stop_playback(did, owner=receiver_id)
                        for did in settings.receiver_targets(receiver_id)
                    )
                )
                # Give paused speakers a clean EOF: otherwise they hold the
                # HTTP connection open forever, silently waiting for data.
                bridge.drop_stream_clients(receiver_id)
                # External AirPlay targets get the same grace as the speakers:
                # a reconnect within the window never tore them down.
                await bridge.stop_airplay_targets(receiver_id)
                await bridge.stop_dlna_targets(receiver_id)
            except asyncio.CancelledError:
                pass

        pending = pending_stops.pop(receiver_id, None)
        if pending:
            pending.cancel()
        pending_stops[receiver_id] = asyncio.create_task(delayed_stop())

    async def reconcile_group(group_id: str, removed_dids: list[str]):
        """Live membership edit on a group: stop the removed speakers and
        incrementally play the added ones. Pipelines are untouched — every
        needed stream variant already exists (the route rebuilds otherwise).
        steal=False means only unowned or already-owned speakers are touched,
        and same-URL replays are skipped, so playing speakers are undisturbed.
        """
        for receiver in settings.active_receivers():
            if receiver.target_type != "group" or receiver.target_id != group_id:
                continue
            owns_session = any(
                device_manager.owner_of(did) == receiver.id
                for did in (*device_manager.playing_ids(), *removed_dids)
            )
            if not owns_session:
                continue  # no live session on this receiver
            url = (
                f"http://{settings.effective_stream_host}:{settings.stream_port}"
                f"/stream/{receiver.id}"
            )
            for did in removed_dids:
                await device_manager.stop(did, owner=receiver.id)
            await play_receiver(receiver.id, url, steal=False)

    app.state.reconcile_group = reconcile_group
    # Plan diffs surface membership edits through this hook (stop removed,
    # replay added) — the routes no longer call it directly.
    bridge.on_group_membership_changed = reconcile_group

    async def pause_group_for_anchor(group_id: str) -> None:
        """Anchor went offline: pause every other member (keep their stream
        URLs so resume is cheap) and tear down network targets. The anchor
        itself is left alone so its watchdog keeps detecting recovery."""
        for receiver in settings.active_receivers():
            if receiver.target_type != "group" or receiver.target_id != group_id:
                continue
            group = settings.group_for_receiver(receiver.id)
            anchor = group.anchor_did if group else None
            for did in settings.receiver_targets(receiver.id):
                if did == anchor:
                    continue
                if device_manager.owner_of(did) == receiver.id:
                    await device_manager.stop(did, owner=receiver.id)
            await bridge.stop_airplay_targets(receiver.id)
            await bridge.stop_dlna_targets(receiver.id)

    async def resume_group_for_anchor(group_id: str) -> None:
        """Anchor recovered: re-assert the paused members (steal=False only
        touches speakers this receiver still owns)."""
        for receiver in settings.active_receivers():
            if receiver.target_type != "group" or receiver.target_id != group_id:
                continue
            url = (
                f"http://{settings.effective_stream_host}:{settings.stream_port}"
                f"/stream/{receiver.id}"
            )
            await play_receiver(receiver.id, url, steal=False)

    device_manager.on_anchor_offline = pause_group_for_anchor
    device_manager.on_anchor_recovered = resume_group_for_anchor

    async def recover_group_streams(receiver_id: str, disconnected_sink: str) -> None:
        """Rejoin every Xiaomi member after one unexpected HTTP disconnect."""
        current = pending_group_recoveries.get(receiver_id)
        if current is not None and not current.done():
            return

        async def recover() -> None:
            try:
                # Ignore transient socket replacement and normal session end.
                await asyncio.sleep(0.75)
                server = bridge.local_server(receiver_id)
                group = settings.group_for_receiver(receiver_id)
                if server is None or server.sessions <= 0 or group is None:
                    return
                # Xiaomi players commonly open a short probe connection and
                # immediately replace it with the real pull. If the same sink
                # has reconnected during the debounce window, playback is
                # healthy and restarting the whole group creates a stutter loop.
                if bridge._stream_server.sink_connected(receiver_id, disconnected_sink):
                    logger.info(
                        "Grouped sink %s replaced its HTTP connection; recovery skipped",
                        disconnected_sink,
                    )
                    return
                targets = [
                    did for did in group.speaker_ids
                    if device_manager.owner_of(did) == receiver_id
                ]
                if len(targets) < 2:
                    return
                logger.warning(
                    "Grouped sink %s disconnected from %s; resynchronizing %s",
                    disconnected_sink,
                    receiver_id,
                    targets,
                )
                bridge._stream_server.begin_group_recovery(receiver_id, targets)
                bridge.drop_stream_clients(receiver_id)
                base = (
                    f"http://{settings.effective_stream_host}:{settings.stream_port}"
                    f"/stream/{receiver_id}"
                )

                async def replay(did: str) -> None:
                    stream = base + settings.stream_suffix(receiver_id, did)
                    url = f"{stream}/for/{receiver_id}/{did}?s={time.time_ns()}"
                    await device_manager.play_stream(
                        did, url, owner=receiver_id, force=True
                    )

                results = await asyncio.gather(
                    *(replay(did) for did in targets), return_exceptions=True
                )
                failures = [result for result in results if isinstance(result, Exception)]
                if failures:
                    bridge._stream_server.abort_group_recovery(receiver_id)
                    logger.warning(
                        "Group stream recovery command failed for %s: %s",
                        receiver_id,
                        failures[0],
                    )
            finally:
                pending_group_recoveries.pop(receiver_id, None)

        pending_group_recoveries[receiver_id] = asyncio.create_task(recover())

    bridge._stream_server.on_client_disconnected = recover_group_streams

    bridge.on_session_start = on_session_start
    bridge.on_session_stop = on_session_stop
    bridge.on_local_stream = play_receiver

    async def on_receiver_volume(receiver_id: str, percent: int):
        if bridge._volume_modes.get(receiver_id) != "linked":
            return
        # Only affect speakers owned by this session, never another source.
        await asyncio.gather(*(
            device_manager.set_volume(did, percent)
            for did in device_manager.owned_targets(receiver_id, receiver_id)
        ))

    bridge.on_receiver_volume = on_receiver_volume
    bridge.on_volume_session_start = apply_default_volume

    async def on_audio_restarted():
        # Encoding or topology changed under live connections; playing speakers
        # must reconnect to pick up the new codec or channel URL. Recompute the
        # URL from the speaker's current OWNER — when several receivers target
        # the same speaker (single + group), a settings-order lookup would pick
        # the wrong stream and hand the speaker silence.
        for did in device_manager.playing_ids():
            owner = device_manager.owner_of(did)
            url = _stream_url_for_receiver(owner, did) if owner else None
            if not url:
                url = device_manager.stream_url_of(did)
            if url:
                play_url = f"{url}/for/{owner}/{did}?s={time.time_ns()}"
                await device_manager.play_stream(did, play_url, force=True)

    bridge.on_audio_restarted = on_audio_restarted

    await bridge.start()
    await dlna_service.start()
    # Silently rotate Xiaomi serviceTokens in the background so the stored
    # passToken keeps the login alive past the ~30-day serviceToken expiry.
    renewal_task = asyncio.create_task(app.state.auth.run_token_renewal())
    try:
        yield
    finally:
        renewal_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await renewal_task
        await dlna_service.stop()
        await app.state.auth.close()
        await app.state.notifier.close()
        await bridge.stop()


logger = logging.getLogger(__name__)
install_runtime_log()


def _stream_url_for_receiver(receiver_id: str, device_id: str) -> str:
    """Stream URL of one receiver for one speaker, channel/EQ suffix included."""
    return (
        f"http://{settings.effective_stream_host}:{settings.stream_port}"
        f"/stream/{receiver_id}{settings.stream_suffix(receiver_id, device_id)}"
    )


# Grace period before a torn-down AirPlay session pauses its speakers;
# reconnects within this window are transparent to the speakers.
SESSION_STOP_GRACE_SECONDS = 3.0

app = FastAPI(
    title="MiCast",
    description="让智能音箱获得 AirPlay 2 接收能力",
    version=__version__,
    lifespan=lifespan,
)

# Shared state
app.state.bridge = AudioBridge()
app.state.access = AccessManager()
app.state.auth = XiaomiAuth()
app.state.device_manager = DeviceManager(app.state.auth)
app.state.dlna = DlnaService(app.state.device_manager)
app.state.bridge._stream_server.media_volume = app.state.dlna.media_volume
app.state.notifier = Notifier()


def _stream_active_for(device_id: str) -> bool:
    """Watchdog ground truth: is the speaker really pulling its stream right
    now? The cloud reports "playing" even when the speaker fetches nothing."""
    url = app.state.device_manager.stream_url_of(device_id) or ""
    # Only the first path segment is the registered stream id. Per-speaker
    # routing lives in later segments (``/for/{receiver}/{sink}``); including
    # those made every healthy pull look inactive and the watchdog repeatedly
    # restarted both speakers.
    match = re.search(r"/stream/([^/?]+)", url)
    if not match:
        return True  # not one of our streams — don't interfere
    stream_id = match.group(1)
    bridge = app.state.bridge
    server = bridge._stream_server
    if server.client_count(stream_id) == 0:
        return False
    if server.is_flowing(stream_id, window=10.0):
        return True
    # Connected but byteless. A paused sender looks exactly the same here, so
    # only call it dead when the pipeline itself starved mid-session — then the
    # watchdog's restore re-joins the speaker once the source restart (driven
    # by the pipeline's own stall watchdog) brings the bytes back.
    return not bridge.stream_starved(stream_id)


app.state.device_manager.stream_active = _stream_active_for

# API routes. /app/micast is canonical on every platform. The unprefixed
# aliases are transitional compatibility for older clients and cached pages.
api_routers = [
    access.install(app.state.access),
    config.install(app.state.bridge, app.state.dlna),
    status.install(app.state.bridge),
    receivers.install(
        app.state.bridge,
        app.state.dlna,
    ),
    devices.install(app.state.device_manager, app.state.bridge),
    xiaomi.install(app.state.auth),
    playback.install(app.state.bridge, app.state.device_manager),
    debug.install(app.state.bridge, app.state.device_manager),
    dlna.install(app.state.dlna),
    airplay2.install(app.state.bridge),
    airplay_devices.install(app.state.bridge),
    dlna_devices.install(app.state.bridge),
    topology.install(app.state.bridge, app.state.device_manager),
    ws.install(app.state.bridge, app.state.device_manager, app.state.access),
]
for api_router in api_routers:
    app.include_router(api_router, prefix=APP_BASE_PATH)
    app.include_router(api_router, include_in_schema=False)


@app.middleware("http")
async def require_local_access(request: Request, call_next):
    """Protect every API after setup while keeping the SPA and bootstrap API reachable."""
    path = request.url.path
    logical_path = path.removeprefix(APP_BASE_PATH) if path.startswith(APP_BASE_PATH) else path
    # Speakers fetch the local calibration tone themselves and cannot carry a
    # browser session cookie.  This read-only finite audio asset is safe to
    # expose on the LAN; every control endpoint remains authenticated.
    public = (
        logical_path == "/health"
        or logical_path == "/api/debug/test-tone"
        or (request.method == "GET" and logical_path.startswith("/api/debug/media/"))
        or logical_path.startswith("/api/access/")
        or (
            request.method == "POST"
            and logical_path in {
                "/api/playback/start",
                "/api/playback/session/stop",
                "/api/playback/session/volume",
            }
        )
    )
    manager: AccessManager = app.state.access
    if (
        logical_path.startswith("/api/")
        and not public
        and (
            not manager.access_configured
            or (
                manager.auth_enabled
                and not manager.valid_session(request.cookies.get(COOKIE_NAME))
            )
        )
    ):
        return JSONResponse({"detail": "需要登录 MiCast"}, status_code=401)
    return await call_next(request)


@app.get(f"{APP_BASE_PATH}/health", tags=["system"])
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/health", include_in_schema=False)
async def legacy_health() -> dict[str, str]:
    return await health()


@app.get(f"{APP_BASE_PATH}/api/desktop/show", include_in_schema=False)
@app.get("/api/desktop/show", include_in_schema=False)
async def desktop_show() -> dict[str, bool]:
    """Raise the desktop window; a second-instance launch lands here.

    Lives in main.py (not desktop.py) because the SPA catch-all below swallows
    any route registered after import time. The callback is installed by
    micast.desktop in packaged desktop mode; elsewhere it's a no-op.
    """
    fn = getattr(app.state, "desktop_show", None)
    if fn:
        fn()
    return {"ok": True}


# Static web UI. Packaged (PyInstaller) builds carry web/dist inside the
# bundle (_MEIPASS); source checkouts resolve it from the repo root.
project_root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
web_root = project_root / "web"
web_dist = web_root / "dist"
if web_dist.exists():
    app.mount(
        f"{APP_BASE_PATH}/assets",
        StaticFiles(directory=str(web_dist / "assets")),
        name="app-assets",
    )
    app.mount(
        f"{APP_BASE_PATH}/icons",
        StaticFiles(directory=str(web_dist / "icons")),
        name="app-icons",
    )
    # Cached pre-prefix pages may still request these during migration.
    app.mount("/assets", StaticFiles(directory=str(web_dist / "assets")), name="legacy-assets")
    app.mount("/icons", StaticFiles(directory=str(web_dist / "icons")), name="legacy-icons")

    @app.get(f"{APP_BASE_PATH}/site.webmanifest", include_in_schema=False)
    @app.get("/site.webmanifest", include_in_schema=False)
    async def web_manifest() -> FileResponse:
        return FileResponse(
            str(web_dist / "site.webmanifest"),
            media_type="application/manifest+json",
        )

    @app.get("/")
    async def root() -> FileResponse:
        """Serve the SPA when a reverse proxy strips APP_BASE_PATH.

        fnOS' Unix-socket gateway publishes the app at APP_BASE_PATH but
        forwards the remaining path ("/") to the package process.  Serving
        the document here avoids redirecting the browser back through the
        public prefix a second time.
        """
        return FileResponse(str(web_dist / "index.html"))

    @app.get(APP_BASE_PATH, include_in_schema=False)
    async def app_root_without_slash() -> RedirectResponse:
        return RedirectResponse(f"{APP_BASE_PATH}/", status_code=307)

    @app.get(f"{APP_BASE_PATH}/")
    async def app_root() -> FileResponse:
        index_file = web_dist / "index.html" if web_dist.exists() else web_root / "index.html"
        return FileResponse(str(index_file))

    @app.get(f"{APP_BASE_PATH}/{{path:path}}", include_in_schema=False)
    async def spa_fallback(path: str) -> FileResponse:
        """Serve the SPA for browser routes while never masking missing assets/API calls."""
        if path.startswith(("api/", "assets/")) or "." in Path(path).name:
            from fastapi import HTTPException

            raise HTTPException(status_code=404, detail="Not found")
        return FileResponse(str(web_dist / "index.html"))
