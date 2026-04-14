"""Fixtures for live simulator smoke tests.

These tests require:
1. A booted iOS simulator
2. TestKitApp installed (io.synctek.specterqa.testkit)
3. Active SpecterQA session

Tests are marked with @pytest.mark.live and skip gracefully when prerequisites aren't met.
"""
import pytest
import subprocess
import urllib.request
import json

TESTKIT_BUNDLE_ID = "io.synctek.specterqa.testkit"
RUNNER_BASE = "http://127.0.0.1:8222"

def _runner_healthy():
    try:
        with urllib.request.urlopen(f"{RUNNER_BASE}/health", timeout=3) as resp:
            return resp.status == 200
    except Exception:
        return False

def _simulator_booted():
    try:
        out = subprocess.check_output(
            ["xcrun", "simctl", "list", "devices", "booted", "-j"],
            text=True, timeout=10
        )
        data = json.loads(out)
        for runtime, devices in data.get("devices", {}).items():
            for d in devices:
                if d.get("state") == "Booted":
                    return True
        return False
    except Exception:
        return False

requires_live = pytest.mark.skipif(
    not (_simulator_booted() and _runner_healthy()),
    reason="Requires booted simulator with active SpecterQA session on TestKitApp"
)
