#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP_NAME="SIMBridge"
BUNDLE_ID="${BUNDLE_ID:-com.wangquanrun.simbridge}"
PYTHON_BIN="${PYTHON_BIN:-python3}"
VENV_DIR="${VENV_DIR:-$ROOT/.venv}"
ICON_SOURCE="$ROOT/assets/sim_card_icon.png"
ICON_PATH="$ROOT/build/SIMBridge.icns"

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "This build script is intended for macOS." >&2
  exit 1
fi

if [[ ! -d "$VENV_DIR" ]]; then
  "$PYTHON_BIN" -m venv "$VENV_DIR"
fi

source "$VENV_DIR/bin/activate"
python -m pip install --upgrade pip
python -m pip install -r "$ROOT/requirements.txt"

LIBUSB_PATH="${LIBUSB_PATH:-}"
if [[ -z "$LIBUSB_PATH" ]]; then
  for candidate in \
    "/opt/homebrew/lib/libusb-1.0.dylib" \
    "/opt/homebrew/opt/libusb/lib/libusb-1.0.dylib" \
    "/usr/local/lib/libusb-1.0.dylib" \
    "/usr/local/opt/libusb/lib/libusb-1.0.dylib"; do
    if [[ -f "$candidate" ]]; then
      LIBUSB_PATH="$candidate"
      break
    fi
  done
fi

if [[ -z "$LIBUSB_PATH" ]]; then
  echo "libusb-1.0.dylib not found. Install with: brew install libusb" >&2
  exit 1
fi

if [[ ! -f "$ICON_SOURCE" ]]; then
  echo "App icon not found: $ICON_SOURCE" >&2
  exit 1
fi

mkdir -p "$ROOT/build"
sips -s format icns "$ICON_SOURCE" --out "$ICON_PATH" >/dev/null

python -m PyInstaller \
  --noconfirm \
  --clean \
  --windowed \
  --name "$APP_NAME" \
  --osx-bundle-identifier "$BUNDLE_ID" \
  --icon "$ICON_PATH" \
  --distpath "$ROOT/dist" \
  --workpath "$ROOT/build/pyinstaller" \
  --specpath "$ROOT/build/spec" \
  --add-data "$ROOT/src/static:static" \
  --add-binary "$LIBUSB_PATH:lib" \
  --collect-data webview \
  --collect-submodules webview \
  --hidden-import usb.backend.libusb1 \
  --hidden-import webview.platforms.cocoa \
  "$ROOT/src/launcher.py"

echo "Built: $ROOT/dist/$APP_NAME.app"
