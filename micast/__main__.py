"""`python -m micast` — run the MiCast server without a separate uvicorn call.

Shared by source checkouts and the PyInstaller-packaged app (frozen builds
import the app object directly; an import string would not survive freezing).
Frozen Windows builds get the desktop shell (WebView2 window + tray);
MICAST_NO_DESKTOP=1 forces plain server mode (useful for debugging).
"""

import os
import sys
import threading
import webbrowser
from pathlib import Path

import uvicorn

from micast.config import resolve_port, settings


def main() -> None:
    unix_socket = os.environ.get("MICAST_UNIX_SOCKET", "").strip()
    # A normal user's machine may already have something on 3000 — slide to a
    # free port instead of failing. Explicit MICAST_PORT stays strict.
    if not unix_socket:
        settings.apply_resolved_port("port", resolve_port(settings.port, "MICAST_PORT"))

    frozen = getattr(sys, "frozen", False)
    if frozen and sys.platform == "win32" and os.environ.get("MICAST_NO_DESKTOP") != "1":
        from micast.desktop import run_desktop  # noqa: PLC0415 — desktop-only deps

        run_desktop()
        return

    from micast.main import app  # noqa: PLC0415 — deferred until settings load

    if frozen and not unix_socket:
        # Packaged non-Windows app: take the user straight to the UI.
        url = f"http://127.0.0.1:{settings.port}"
        threading.Timer(1.5, lambda: webbrowser.open(url)).start()

    if unix_socket:
        socket_path = Path(unix_socket)
        socket_path.parent.mkdir(parents=True, exist_ok=True)
        socket_path.unlink(missing_ok=True)
        uvicorn.run(app, uds=str(socket_path), log_level="info")
    else:
        uvicorn.run(app, host=settings.host, port=settings.port, log_level="info")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        # Windowed (console=False) builds lose tracebacks entirely — drop them
        # in <data dir>/crash.log so users can actually report what happened.
        if getattr(sys, "frozen", False):
            import traceback

            from micast.config import default_log_dir

            crash = default_log_dir() / "crash.log"
            crash.parent.mkdir(parents=True, exist_ok=True)
            crash.write_text(traceback.format_exc(), encoding="utf-8")
        raise
