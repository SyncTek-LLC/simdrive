#!/bin/bash
# build.sh — Compile the SpecterQA XCTest runner for iOS Simulator.
#
# Usage:
#   ./build.sh [--clean] [--derived-data PATH]
#
# Options:
#   --clean               Remove existing build artifacts before building
#   --derived-data PATH   Override derived data path (default: ~/.specterqa/runner-build)
#
# Output:
#   Prints the path to the produced .xctestrun file on success.
#   Exits non-zero on failure.
#
# The built runner can then be deployed with:
#   xcodebuild test-without-building \
#       -xctestrun <path/to/*.xctestrun> \
#       -destination "id=<UDID>"

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT="$SCRIPT_DIR/SpecterQARunner.xcodeproj"
SCHEME="SpecterQARunner"
DERIVED_DATA="${SPECTERQA_DERIVED_DATA:-$HOME/.specterqa/runner-build}"
CLEAN=0

# ── Argument parsing ────────────────────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
    case "$1" in
        --clean)
            CLEAN=1
            shift
            ;;
        --derived-data)
            DERIVED_DATA="$2"
            shift 2
            ;;
        *)
            echo "Unknown argument: $1" >&2
            echo "Usage: $0 [--clean] [--derived-data PATH]" >&2
            exit 1
            ;;
    esac
done

# ── Sanity checks ───────────────────────────────────────────────────────────────
if ! command -v xcodebuild &>/dev/null; then
    echo "ERROR: xcodebuild not found. Install Xcode or Xcode Command Line Tools." >&2
    exit 1
fi

if [[ ! -f "$PROJECT/project.pbxproj" ]]; then
    echo "ERROR: Project not found at $PROJECT" >&2
    exit 1
fi

# ── Stale-cache detection ────────────────────────────────────────────────────────
# If any Swift source file is newer than the cached .xctestrun, invalidate the
# cache so the runner always reflects the current source.  This prevents stale
# binary deployments after source edits without requiring --clean.
if [[ "$CLEAN" -eq 0 && -d "$DERIVED_DATA" ]]; then
    CACHED_XCTESTRUN=$(find "$DERIVED_DATA" -name "*.xctestrun" 2>/dev/null | head -1)
    if [[ -n "$CACHED_XCTESTRUN" ]]; then
        STALE=0
        while IFS= read -r -d '' SRC; do
            if [[ "$SRC" -nt "$CACHED_XCTESTRUN" ]]; then
                STALE=1
                echo "  Source newer than cache: $SRC"
                break
            fi
        done < <(find "$SCRIPT_DIR/Sources" -name "*.swift" -print0 2>/dev/null)
        if [[ "$STALE" -eq 1 ]]; then
            echo "▶ Source files changed — invalidating stale build cache …"
            rm -rf "$DERIVED_DATA"
        fi
    fi
fi

# ── Optional clean ──────────────────────────────────────────────────────────────
if [[ "$CLEAN" -eq 1 && -d "$DERIVED_DATA" ]]; then
    echo "▶ Cleaning $DERIVED_DATA …"
    rm -rf "$DERIVED_DATA"
fi

mkdir -p "$DERIVED_DATA"

# ── Build ───────────────────────────────────────────────────────────────────────
echo "▶ Building SpecterQA runner …"
echo "  Project : $PROJECT"
echo "  Scheme  : $SCHEME"
echo "  Output  : $DERIVED_DATA"
echo ""

xcodebuild build-for-testing \
    -project "$PROJECT" \
    -scheme "$SCHEME" \
    -sdk iphonesimulator \
    -destination "generic/platform=iOS Simulator" \
    -derivedDataPath "$DERIVED_DATA" \
    CODE_SIGN_IDENTITY="-" \
    CODE_SIGNING_REQUIRED=NO \
    CODE_SIGNING_ALLOWED=YES \
    DEVELOPMENT_TEAM="" \
    SUPPORTED_PLATFORMS="iphonesimulator" \
    ARCHS="\$(ARCHS_STANDARD)" \
    2>&1

# ── Locate .xctestrun ───────────────────────────────────────────────────────────
XCTESTRUN=$(find "$DERIVED_DATA" -name "*.xctestrun" 2>/dev/null | head -1)

if [[ -z "$XCTESTRUN" ]]; then
    echo "" >&2
    echo "ERROR: Build succeeded but no .xctestrun file found in $DERIVED_DATA" >&2
    echo "       Check xcodebuild output above for warnings." >&2
    exit 1
fi

echo ""
echo "✓ Build succeeded."
echo "  xctestrun: $XCTESTRUN"
echo ""
echo "Deploy with:"
echo "  xcodebuild test-without-building \\"
echo "      -xctestrun \"$XCTESTRUN\" \\"
echo "      -destination \"id=<SIMULATOR-UDID>\""
