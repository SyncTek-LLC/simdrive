#!/bin/bash
# Build and install TestKitApp on the booted iOS Simulator.
# Usage: ./TestKitApp/build.sh [UDID]
#
# If UDID is omitted, uses the first booted simulator.
# On success, prints the installed bundle path and exits 0.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
BUILD_DIR="${TMPDIR:-/tmp}/specterqa-testkit-build"
BUNDLE_ID="io.synctek.specterqa.testkit"
SCHEME="TestKitApp"

# ── 1. Find booted simulator ─────────────────────────────────────────────────
UDID="${1:-}"
if [ -z "$UDID" ]; then
    UDID=$(xcrun simctl list devices booted -j | python3 -c "
import json, sys
data = json.load(sys.stdin)
for runtime, devices in data.get('devices', {}).items():
    for d in devices:
        if d.get('state') == 'Booted':
            print(d['udid']); sys.exit(0)
sys.exit(1)
" 2>/dev/null || true)
fi

if [ -z "$UDID" ]; then
    echo "ERROR: No booted simulator found. Boot one first:" >&2
    echo "  xcrun simctl boot 'iPhone 16 Pro'" >&2
    exit 1
fi

echo "Target simulator: $UDID"

# ── 2. Build ──────────────────────────────────────────────────────────────────
DERIVED_DATA="$BUILD_DIR/DerivedData"
mkdir -p "$DERIVED_DATA"

echo "Building $SCHEME (iphonesimulator)..."
xcodebuild build \
    -project "$SCRIPT_DIR/TestKitApp.xcodeproj" \
    -scheme "$SCHEME" \
    -sdk iphonesimulator \
    -destination "id=$UDID" \
    -configuration Debug \
    -derivedDataPath "$DERIVED_DATA" \
    CODE_SIGNING_ALLOWED=NO \
    CODE_SIGNING_REQUIRED=NO \
    ONLY_ACTIVE_ARCH=YES \
    2>&1 | xcpretty 2>/dev/null || cat  # fall back to raw output if xcpretty absent

# ── 3. Locate the .app bundle ─────────────────────────────────────────────────
APP_PATH=$(find "$DERIVED_DATA" -name "TestKitApp.app" -type d | head -n 1)
if [ -z "$APP_PATH" ]; then
    echo "ERROR: Build succeeded but TestKitApp.app not found under $DERIVED_DATA" >&2
    exit 1
fi
echo "Built: $APP_PATH"

# ── 4. Install on simulator ───────────────────────────────────────────────────
echo "Installing on simulator $UDID..."
xcrun simctl install "$UDID" "$APP_PATH"

echo "Installed: $BUNDLE_ID"
echo "Launch with:"
echo "  xcrun simctl launch $UDID $BUNDLE_ID"
echo ""
echo "Done."
