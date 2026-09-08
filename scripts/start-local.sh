#!/usr/bin/env sh
set -eu

PROJECT_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$PROJECT_ROOT"

if [ ! -x .venv/bin/python ]; then
  python3 -m venv .venv
fi

if ! .venv/bin/python -c "import micast, av, Crypto, zeroconf" >/dev/null 2>&1; then
  .venv/bin/python -m pip install -e .
fi

if [ ! -d web/node_modules ]; then
  npm --prefix web ci
fi
npm --prefix web run build

export MICAST_AIRPLAY_ENGINE=local
exec .venv/bin/python -m uvicorn micast.main:app --host 0.0.0.0 --port 3000
