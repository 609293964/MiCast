"""Deployment feature gates shared by API, runtime and UI state."""

import os


def integrated_airplay2_available() -> bool:
    """Only the full MiCast compose stack owns an AirPlay 2 orchestrator."""
    return os.environ.get("MICAST_DEPLOYMENT", "").strip().lower() == "integrated"


def airplay2_mode() -> str:
    """Return the deployment capability: disabled, single, or multi."""
    explicit = os.environ.get("MICAST_AIRPLAY2_MODE", "").strip().lower()
    if explicit in {"disabled", "single", "multi"}:
        return explicit
    deployment = os.environ.get("MICAST_DEPLOYMENT", "").strip().lower()
    if deployment == "fnos":
        return "single"
    if deployment == "integrated":
        return "multi"
    return "disabled"


def airplay2_available() -> bool:
    return airplay2_mode() != "disabled"
