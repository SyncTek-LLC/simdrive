"""Gap test — B9: MCP ios_start_session must deploy the XCTest runner.

In 13.2.0 the MCP path calls build.sh (compile only) but never deploys via
xcodebuild test-without-building. So XCTestBackend.is_available() → False →
"Requested backend 'xctest' is not available on this system."

This test is marked @pytest.mark.live — it requires a booted simulator and is
skipped in CI environments without one. It verifies the full MCP→deploy loop
end-to-end, not mocked.

Run (requires booted iPhone sim):
    pytest tests/regression/test_mcp_xctest_session.py -v -m live

The test is intentionally skipped when no simulator is booted so it doesn't
block the non-live test suite.
"""
from __future__ import annotations

import subprocess
import time
import urllib.request
import json as _json

import pytest

# ---------------------------------------------------------------------------
# Live marker — skip when no simulator is booted
# ---------------------------------------------------------------------------

pytestmark = pytest.mark.live


def _booted_udid() -> str | None:
    """Return UDID of the first booted simulator, or None."""
    try:
        r = subprocess.run(
            ["xcrun", "simctl", "list", "devices", "booted", "-j"],
            capture_output=True, text=True, timeout=10,
        )
        if r.returncode != 0:
            return None
        data = _json.loads(r.stdout)
        for devs in data.get("devices", {}).values():
            for d in devs:
                if d.get("state") == "Booted":
                    return d.get("udid")
    except Exception:
        return None
    return None


@pytest.fixture(autouse=True)
def require_simulator():
    udid = _booted_udid()
    if udid is None:
        pytest.skip("No booted iOS simulator — skipping live MCP XCTest test")
    return udid


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

RUNNER_PORT = 8222
HEALTH_URL = f"http://localhost:{RUNNER_PORT}/health"


def _probe_health(timeout_s: float = 5.0) -> bool:
    """Poll /health with a short timeout; return True if the runner responds."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(HEALTH_URL, timeout=2) as resp:
                data = _json.loads(resp.read())
                if data.get("status") == "ok":
                    return True
        except Exception:
            pass
        time.sleep(0.3)
    return False


# ---------------------------------------------------------------------------
# Test 1: handle_start_session with backend="xctest" must deploy the runner
# ---------------------------------------------------------------------------


def test_mcp_xctest_backend_deploys_runner(require_simulator):
    """ios_start_session(backend='xctest') must deploy the runner and return backend='xctest'.

    Fails on 13.2.0 main: returns {"error": "Requested backend 'xctest' is not available..."}
    Passes after B9 fix: MCP path runs xcodebuild test-without-building before probing.
    """
    from specterqa.ios.mcp.server import handle_start_session, handle_stop_session

    udid = require_simulator

    try:
        result = handle_start_session({
            "bundle_id": "io.synctek.specterqa.testkit",
            "device_id": udid,
            "backend": "xctest",
            "license_key": "founder",
        })

        assert "error" not in result, (
            f"B9 regression: MCP handle_start_session returned error: {result.get('error')}\n"
            "Expected xctest backend to deploy runner before probing :8222/health"
        )

        backend_used = result.get("backend", "")
        assert backend_used == "xctest" or result.get("status") == "ok", (
            f"Expected backend='xctest' in response, got: {result}"
        )

        # Verify the runner is actually listening
        assert _probe_health(timeout_s=5.0), (
            f"Runner is not responding at {HEALTH_URL} after ios_start_session succeeded"
        )

    finally:
        try:
            handle_stop_session({})
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Test 2: runner shuts down cleanly on ios_stop_session
# ---------------------------------------------------------------------------


def test_mcp_xctest_runner_stops_on_stop_session(require_simulator):
    """After ios_stop_session, :8222/health must stop responding."""
    from specterqa.ios.mcp.server import handle_start_session, handle_stop_session

    udid = require_simulator

    start_result = handle_start_session({
        "bundle_id": "io.synctek.specterqa.testkit",
        "device_id": udid,
        "backend": "xctest",
        "license_key": "founder",
    })

    if "error" in start_result:
        pytest.skip(f"Could not start session (B9 not yet fixed?): {start_result['error']}")

    handle_stop_session({})

    # Runner should no longer respond after stop
    still_up = _probe_health(timeout_s=3.0)
    assert not still_up, "Runner is still responding at :8222/health after ios_stop_session"
