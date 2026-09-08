"""Canonical public URL paths shared by every MiCast deployment."""

APP_BASE_PATH = "/app/micast"
API_BASE_PATH = f"{APP_BASE_PATH}/api"


def app_path(path: str = "") -> str:
    """Return an absolute path below the canonical MiCast application root."""
    if not path:
        return APP_BASE_PATH
    return f"{APP_BASE_PATH}/{path.lstrip('/')}"

