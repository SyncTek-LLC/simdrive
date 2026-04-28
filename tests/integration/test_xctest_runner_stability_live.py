"""60s XCTest runner stability gate — Maurice/Example Reader dogfood §5.1.

Permanent release gate: deploy the XCTest runner on a booted simulator, then
poll `/health` every 5 seconds for 60 seconds. All 12 polls MUST return 200.

This is the test we should have had before v15.1.0 shipped. Without it the
iOS 26 XCTest watchdog regression slipped through — the runner deployed, two
`/health` calls succeeded, then the test method was SIGKILLed and every
subsequent `ios_replay` step failed with "Connection refused at :8222".

Run with:
    pytest tests/integration/test_xctest_runner_stability_live.py \
        -v -m live -s

Skip-guards: requires `SPECTERQA_LIVE_SIM=1` (gated by `tests/conftest.py`)
plus a booted iOS Simulator. Slow: ~90s start_session + 60s poll = ~2.5min.
"""
from __future__ import annotations

import json
import subprocess
import time
import urllib.error
import urllib.request

import pytest


def _booted_udid() -> str | None:
    try:
        result = subprocess.run(
            ["xcrun", "simctl", "list", "devices", "booted", "-j"],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode != 0:
            return None
        data = json.loads(result.stdout)
        for devices in data.get("devices", {}).values():
            for d in devices:
                if d.get("state") == "Booted":
                    return d.get("udid")
    except Exception:
        return None
    return None


pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(_booted_udid() is None, reason="no booted iOS simulator"),
]


def test_xctest_runner_survives_60s_post_deploy():
    """The runner test method must remain healthy for at least 60s after
    `ios_start_session(backend='xctest')` returns ok.

    Asserts:
      - start_session returns status=ok within its 90s budget
      - /health returns 200 on every 5s poll for 60s straight (12/12)

    Pre-v15.2.0 this test would FAIL on iOS 26.x — the in-sim test method
    was killed by XCTRuntimeIssueDetectionManager within ~5s of entering
    its CFRunLoopRunInMode polling loop. v15.2.0 swapped that pattern for
    XCTWaiter.wait(for:[XCTestExpectation], timeout:) which iOS 26 treats
    as a legitimate blocking wait, not a stuck test method.
    """
    from specterqa.ios.mcp.server import handle_start_session, handle_stop_session  # noqa: PLC0415

    udid = _booted_udid()
    assert udid is not None, "skip-guard misfired"

    # Defense: kill any orphan xcodebuild test processes from a prior failed run.
    subprocess.run(["pkill", "-9", "-f", "xcodebuild test"], capture_output=True)
    time.sleep(1.0)

    t0 = time.monotonic()
    result = handle_start_session({
        "bundle_id": "com.apple.Preferences",
        "device_id": udid,
        "backend": "xctest",
        "clone": False,
        "device_type": "simulator",
    })
    deploy_elapsed = time.monotonic() - t0
    assert result.get("status") == "ok", (
        f"ios_start_session(backend='xctest') failed at t={deploy_elapsed:.1f}s — "
        f"payload: {json.dumps(result, default=str)[:500]}"
    )

    try:
        # 12 polls × 5s = 60s coverage window
        for i in range(12):
            time.sleep(5.0)
            poll_t = time.monotonic() - t0
            try:
                with urllib.request.urlopen(  # nosec B310
                    "http://localhost:8222/health", timeout=2.0,
                ) as resp:
                    assert resp.status == 200, (
                        f"Poll {i + 1}/12 at t={poll_t:.1f}s returned HTTP "
                        f"{resp.status} — runner is unhealthy"
                    )
            except (urllib.error.URLError, ConnectionRefusedError, OSError) as exc:
                pytest.fail(
                    f"Poll {i + 1}/12 at t={poll_t:.1f}s failed with {type(exc).__name__}: "
                    f"{exc}. Runner died post-deploy — check for the iOS 26 XCTest "
                    "watchdog regression that v15.2.0 was supposed to fix."
                )

    finally:
        try:
            handle_stop_session({})
        except Exception:
            pass
        # Sweep up any orphan xcodebuild for cleanliness in CI
        subprocess.run(["pkill", "-9", "-f", "xcodebuild test"], capture_output=True)
