#!/bin/sh
# Start the MiCast HTTP service inside Docker.

exec python -m uvicorn micast.main:app --host "${MICAST_HOST:-0.0.0.0}" --port "${MICAST_PORT:-3000}"
