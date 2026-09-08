#!/bin/sh
curl -fsS -X POST "${MICAST_CALLBACK_BASE}/app/micast/api/playback/start" \
  -H "Content-Type: application/json" \
  --data "{\"device_id\":\"${MICAST_DEVICE_ID}\",\"token\":\"${MICAST_CALLBACK_TOKEN}\"}" \
  >/dev/null 2>&1 || true
