#!/bin/bash
# launch.sh — Build and deploy the SpecterQA XCTest runner to an iOS Simulator.
#
# Usage:
#   ./launch.sh [UDID] [PORT] [BUNDLE_ID]
#
# Arguments:
#   UDID       — Simulator UDID (default: "booted" — uses the currently booted sim)
#   PORT       — HTTP port the runner listens on (default: 8222)
#   BUNDLE_ID  — Bundle ID of the app-under-test (default: com.example.app)
#
# Environment variables (alternative to positional args):
#   SPECTERQA_UDID       — Simulator UDID
#   SPECTERQA_PORT       — HTTP port
#   SPECTERQA_BUNDLE_ID  — Bundle ID of the app-under-test
#
# Examples:
#   ./launch.sh                                        # booted sim, port 8222
#   ./launch.sh XXXXXXXX-XXXX-XXXX-XXXX-XXXXXXXXXXXX 9000 com.myco.myapp
#   SPECTERQA_PORT=9000 ./launch.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ── Configuration ──────────────────────────────────────────────────────────────
UDID="${1:-${SPECTERQA_UDID:-booted}}"
PORT="${2:-${SPECTERQA_PORT:-8222}}"
BUNDLE_ID="${3:-${SPECTERQA_BUNDLE_ID:-com.example.app}}"
DERIVED_DATA="/tmp/specterqa-runner-build"
SCHEME="SpecterQARunner"

echo "╔══════════════════════════════════════════════════════════════╗"
echo "║  SpecterQA iOS Runner                                        ║"
echo "╠══════════════════════════════════════════════════════════════╣"
echo "║  Simulator : ${UDID}"
echo "║  Port      : ${PORT}"
echo "║  Bundle ID : ${BUNDLE_ID}"
echo "╚══════════════════════════════════════════════════════════════╝"
echo ""

# ── Sanity checks ──────────────────────────────────────────────────────────────
if ! command -v xcodebuild &>/dev/null; then
    echo "ERROR: xcodebuild not found — install Xcode command-line tools." >&2
    exit 1
fi

# Ensure the simulator is booted (skip if UDID is 'booted')
if [[ "$UDID" != "booted" ]]; then
    SIM_STATE=$(xcrun simctl list devices | grep "$UDID" | grep -oP '\(\K[^)]+(?=\))' | tail -1 || true)
    if [[ "$SIM_STATE" != "Booted" ]]; then
        echo "Booting simulator $UDID …"
        xcrun simctl boot "$UDID"
        # Wait for boot
        for i in {1..30}; do
            STATE=$(xcrun simctl list devices | grep "$UDID" | grep -oP '\(\K[^)]+(?=\))' | tail -1 || true)
            [[ "$STATE" == "Booted" ]] && break
            sleep 1
        done
    fi
fi

# ── Build ──────────────────────────────────────────────────────────────────────
echo "▶ Building test bundle …"
xcodebuild build-for-testing \
    -scheme "$SCHEME" \
    -destination "id=$UDID" \
    -derivedDataPath "$DERIVED_DATA" \
    SPECTERQA_PORT="$PORT" \
    SPECTERQA_BUNDLE_ID="$BUNDLE_ID" \
    2>&1 | tail -20

# Locate the .xctestrun file produced by build-for-testing
XCTESTRUN=$(find "$DERIVED_DATA" -name "*.xctestrun" | head -1)
if [[ -z "$XCTESTRUN" ]]; then
    echo "ERROR: No .xctestrun file found in $DERIVED_DATA" >&2
    exit 1
fi

echo ""
echo "▶ Test plan: $XCTESTRUN"
echo ""

# ── Run ────────────────────────────────────────────────────────────────────────
echo "▶ Starting HTTP runner on port $PORT …"
echo "  (Press Ctrl-C or POST /shutdown to stop)"
echo ""

xcodebuild test-without-building \
    -xctestrun "$XCTESTRUN" \
    -destination "id=$UDID" \
    SPECTERQA_PORT="$PORT" \
    SPECTERQA_BUNDLE_ID="$BUNDLE_ID" \
    2>&1
