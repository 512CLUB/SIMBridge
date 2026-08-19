#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP_NAME="SIMBridge"
APP_PATH="$ROOT/dist/$APP_NAME.app"
RELEASE_DIR="$ROOT/release"
DMG_PATH="$RELEASE_DIR/$APP_NAME.dmg"
ZIP_PATH="$RELEASE_DIR/$APP_NAME.app.zip"

if [[ ! -d "$APP_PATH" ]]; then
  echo "App not found: $APP_PATH" >&2
  echo "Run ./scripts/build-macos-arm64.sh first." >&2
  exit 1
fi

mkdir -p "$RELEASE_DIR"
STAGING_DIR="$(mktemp -d "$ROOT/build/dmg-staging.XXXXXX")"
trap 'rm -rf "$STAGING_DIR"' EXIT

ditto "$APP_PATH" "$STAGING_DIR/$APP_NAME.app"
cp "$ROOT/README.md" "$STAGING_DIR/README.md"
ln -s /Applications "$STAGING_DIR/应用程序"

hdiutil create \
  -volname "$APP_NAME" \
  -srcfolder "$STAGING_DIR" \
  -ov \
  -format UDZO \
  "$DMG_PATH"

ditto -c -k --sequesterRsrc --keepParent "$APP_PATH" "$ZIP_PATH"

(
  cd "$RELEASE_DIR"
  shasum -a 256 "$APP_NAME.dmg" "$APP_NAME.app.zip" > CHECKSUMS.txt
)

echo "Created: $DMG_PATH"
echo "Created: $ZIP_PATH"
echo "Created: $RELEASE_DIR/CHECKSUMS.txt"
