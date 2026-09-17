#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STAGE_DIR="${ROOT_DIR}/build/fnos/micast"
OUTPUT_DIR="${ROOT_DIR}/dist/fnos"
PYTHON_BIN="${PYTHON_BIN:-python3}"

command -v fnpack >/dev/null 2>&1 || {
  echo "fnpack 未安装或不在 PATH 中" >&2
  exit 1
}
command -v npm >/dev/null 2>&1 || {
  echo "npm 未安装或不在 PATH 中" >&2
  exit 1
}

rm -rf "$STAGE_DIR"
mkdir -p "$STAGE_DIR/app/vendor" "$STAGE_DIR/app/web" "$STAGE_DIR/app/ui/images" "$OUTPUT_DIR"
cp -a "$ROOT_DIR/packaging/fnos/." "$STAGE_DIR/"

npm --prefix "$ROOT_DIR/web" ci
npm --prefix "$ROOT_DIR/web" run build

cp -a "$ROOT_DIR/micast" "$STAGE_DIR/app/"
# Keep the dist/ level: the app resolves the UI as <root>/web/dist, matching
# the PowerShell builder's layout.
cp -a "$ROOT_DIR/web/dist" "$STAGE_DIR/app/web/"

"$PYTHON_BIN" -m pip install \
  --disable-pip-version-check \
  --no-compile \
  --target "$STAGE_DIR/app/vendor" \
  "$ROOT_DIR"

cp "$ROOT_DIR/web/public/icons/fnos-64.png" "$STAGE_DIR/ICON.PNG"
cp "$ROOT_DIR/web/public/icons/fnos-256.png" "$STAGE_DIR/ICON_256.PNG"
cp "$ROOT_DIR/web/public/icons/fnos-64.png" "$STAGE_DIR/app/ui/images/icon_64.png"
cp "$ROOT_DIR/web/public/icons/fnos-256.png" "$STAGE_DIR/app/ui/images/icon_256.png"

find "$STAGE_DIR/cmd" -type f -exec chmod 0755 {} +
find "$STAGE_DIR/app" -type d -name '__pycache__' -prune -exec rm -rf {} +
find "$STAGE_DIR/app" -type f \( -name '*.pyc' -o -name '*.pyo' \) -delete
find "$STAGE_DIR/app/vendor" -type d \( -name tests -o -name test -o -name __pycache__ \) -prune -exec rm -rf {} +
find "$STAGE_DIR/app/vendor" -type f -name '*.pyi' -delete

(
  cd "$STAGE_DIR"
  fnpack build
)

find "$STAGE_DIR" -maxdepth 1 -type f -name '*.fpk' -exec cp {} "$OUTPUT_DIR/" \;

# Rename to the versioned artifact name, matching the Windows build script.
VERSION="$(sed -n 's/^version=//p' "$STAGE_DIR/manifest" | tr -d '\r')"
PLATFORM="$(sed -n 's/^platform=//p' "$STAGE_DIR/manifest" | tr -d '\r')"
if [ -f "$OUTPUT_DIR/micast.fpk" ]; then
  mv "$OUTPUT_DIR/micast.fpk" "$OUTPUT_DIR/micast-$PLATFORM-$VERSION.fpk"
fi
echo "fnOS package: $OUTPUT_DIR"
