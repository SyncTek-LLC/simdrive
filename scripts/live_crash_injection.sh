#!/usr/bin/env bash
# Live crash-injection check for the replay crash cross-check (FU-2026-071).
#
# Creates a throwaway simulator (never borrows one another session is using),
# builds + installs TestKitApp, then runs the live test that records a 15-tap
# journey and replays it into a REAL crash at tap 10 (TestKitApp's
# SIMDRIVE_CRASH_ON_TAP trigger). Passes only if replay reports
# halt_reason=crash_detected, attributed to step 10, from the real .ips.
#
#   scripts/live_crash_injection.sh            # local (Python: $PYTHON or python3)
#   KEEP_SIM=1 scripts/live_crash_injection.sh # keep the simulator afterwards
#
# Run by the `live-crash-injection` CI job. Needs Xcode + an iOS runtime, and
# simdrive importable from simdrive/src with the native HID helper built.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PY="${PYTHON:-python3}"

read -r RUNTIME DEVTYPE < <(xcrun simctl list -j runtimes devicetypes | "$PY" -c '
import json, sys
d = json.load(sys.stdin)
ios = [r for r in d["runtimes"] if r.get("isAvailable") and r.get("platform", "iOS") == "iOS"
       and "iOS" in r["identifier"]]
if not ios:
    sys.exit("no available iOS simulator runtime")
rt = sorted(ios, key=lambda r: [int(p) for p in r["version"].split(".")])[-1]
supported = {t["identifier"] for t in rt.get("supportedDeviceTypes", [])} or {
    t["identifier"] for t in d["devicetypes"]}
for name in ("iPhone-17-Pro", "iPhone-16-Pro", "iPhone-15-Pro"):
    ident = "com.apple.CoreSimulator.SimDeviceType." + name
    if ident in supported:
        print(rt["identifier"], ident)
        break
else:
    sys.exit("no iPhone Pro device type for " + rt["identifier"])
')

UDID="$(xcrun simctl create "simdrive-crash-live-$$" "$DEVTYPE" "$RUNTIME")"
cleanup() {
    if [ -z "${KEEP_SIM:-}" ]; then
        xcrun simctl shutdown "$UDID" >/dev/null 2>&1 || true
        xcrun simctl delete "$UDID" >/dev/null 2>&1 || true
    fi
}
trap cleanup EXIT
echo "simulator: $UDID ($DEVTYPE, $RUNTIME)"

xcrun simctl boot "$UDID"
xcrun simctl bootstatus "$UDID" -b
"$ROOT/TestKitApp/build.sh" "$UDID"
# Warm-up launch: the first launch on a cold simulator can outlast simdrive's
# 15 s launch timeout. Take that cost here, outside the test.
xcrun simctl launch "$UDID" io.synctek.specterqa.testkit
sleep 5
xcrun simctl terminate "$UDID" io.synctek.specterqa.testkit || true

cd "$ROOT/simdrive"
SIMDRIVE_LIVE_UDID="$UDID" PYTHONPATH="$ROOT/simdrive/src${PYTHONPATH:+:$PYTHONPATH}" \
    "$PY" -m pytest tests/test_live_replay_crash_injection.py -m live -v -s -p no:cacheprovider
