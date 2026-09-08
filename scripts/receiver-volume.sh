#!/bin/sh
set -eu
# Only numbers are passed by Shairport Sync. Keep HTTP callbacks bounded.
case "${1:-}" in ''|*[!0-9.-]*) exit 2 ;; esac
curl -fsS --connect-timeout 2 --max-time 3 \
  -X POST "${MICAST_CALLBACK_BASE}/app/micast/api/playback/session/volume" \
  -H "Content-Type: application/json" \
  --data "{\"device_id\":\"${MICAST_DEVICE_ID}\",\"token\":\"${MICAST_CALLBACK_TOKEN}\",\"db\":$1}" \
  >/dev/null || { echo "MiCast volume callback failed" >&2; exit 1; }
