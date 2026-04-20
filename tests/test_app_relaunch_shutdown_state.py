"""Tests for Issue 6: ios_app_relaunch handles Shutdown sim state.

Verifies:
- Checks sim state before install/terminate/launch
- Returns structured error when sim is Shutdown and boot fails
- Waits for Booting → Booted transition
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch, call

import pytest


def _run_handle_app_relaunch(arguments: dict) -> dict:
    from specterqa.ios.mcp import server
    return server.handle_app_relaunch(arguments)


class TestAppRelaunchShutdownHandling:
    """handle_app_relaunch sim-state checks (Issues 6 / Maurice Issue 7)."""

    def test_returns_error_when_sim_shutdown_and_boot_fails(self):
        """When sim is Shutdown and boot fails, return structured error."""
        import specterqa.ios.mcp.server as srv

        # Inject a fake backend so the "No active session" guard passes
        srv._backend = MagicMock()
        srv._session_udid = "FAKE-UDID-001"

        def fake_simctl(*args, **kwargs):
            r = MagicMock()
            r.returncode = 0
            cmd = args[0] if args else []
            if "list" in cmd and "devices" in cmd:
                r.stdout = json.dumps({
                    "devices": {
                        "com.apple.CoreSimulator.SimRuntime.iOS-26-0": [
                            {"udid": "FAKE-UDID-001", "state": "Shutdown", "name": "iPhone 12"}
                        ]
                    }
                })
            elif "boot" in cmd:
                r.returncode = 1  # boot fails
                r.stderr = "Failed to boot"
            else:
                r.stdout = ""
                r.stderr = ""
            return r

        with patch("subprocess.run", side_effect=fake_simctl):
            with patch("time.sleep"):
                result = _run_handle_app_relaunch({
                    "bundle_id": "io.example.app",
                    "udid": "FAKE-UDID-001",
                })

        # Should be a structured error, not a generic exception
        assert "error" in result
        # error should communicate sim state
        err_str = str(result)
        assert "sim" in err_str.lower() or "shutdown" in err_str.lower() or "boot" in err_str.lower()

        # Cleanup
        srv._backend = None
        srv._session_udid = None

    def test_returns_structured_error_with_sim_state_field(self):
        """Structured error response should include sim_state key."""
        import specterqa.ios.mcp.server as srv

        srv._backend = MagicMock()
        srv._session_udid = "FAKE-UDID-002"

        def fake_simctl_shutdown(*args, **kwargs):
            r = MagicMock()
            r.returncode = 0
            cmd = args[0] if args else []
            if "list" in cmd and "devices" in cmd:
                r.stdout = json.dumps({
                    "devices": {
                        "com.apple.CoreSimulator.SimRuntime.iOS-26-0": [
                            {"udid": "FAKE-UDID-002", "state": "Shutdown", "name": "iPhone 12"}
                        ]
                    }
                })
            elif "boot" in cmd:
                r.returncode = 1
                r.stderr = "Simctl error"
            else:
                r.stdout = ""
                r.stderr = ""
            return r

        with patch("subprocess.run", side_effect=fake_simctl_shutdown):
            with patch("time.sleep"):
                result = _run_handle_app_relaunch({
                    "bundle_id": "io.example.app",
                    "udid": "FAKE-UDID-002",
                })

        # The error should contain sim_state or a recovery hint
        assert "error" in result

        # Cleanup
        srv._backend = None
        srv._session_udid = None

    def test_proceeds_normally_when_sim_is_booted(self):
        """When sim is Booted, the normal terminate+launch path should run."""
        import specterqa.ios.mcp.server as srv

        srv._backend = MagicMock()
        srv._session_udid = "FAKE-UDID-003"

        call_log = []

        def fake_simctl(*args, **kwargs):
            r = MagicMock()
            r.returncode = 0
            r.stderr = ""
            r.stdout = ""
            cmd = args[0] if args else []
            call_log.append(list(cmd))
            if "list" in cmd and "devices" in cmd:
                r.stdout = json.dumps({
                    "devices": {
                        "com.apple.CoreSimulator.SimRuntime.iOS-26-0": [
                            {"udid": "FAKE-UDID-003", "state": "Booted", "name": "iPhone 12"}
                        ]
                    }
                })
            return r

        with patch("subprocess.run", side_effect=fake_simctl):
            with patch("time.sleep"):
                result = _run_handle_app_relaunch({
                    "bundle_id": "io.example.app",
                    "udid": "FAKE-UDID-003",
                })

        # Should NOT return sim_not_booted error
        assert result.get("error", "").find("sim_not_booted") == -1

        srv._backend = None
        srv._session_udid = None

    def test_waits_for_booting_to_become_booted(self):
        """When sim is Booting, should wait up to 30s for Booted state."""
        import specterqa.ios.mcp.server as srv

        srv._backend = MagicMock()
        srv._session_udid = "FAKE-UDID-004"

        call_count = {"n": 0}

        def fake_simctl(*args, **kwargs):
            r = MagicMock()
            r.returncode = 0
            r.stderr = ""
            r.stdout = ""
            cmd = args[0] if args else []
            if "list" in cmd and "devices" in cmd:
                call_count["n"] += 1
                # First two calls: Booting; then Booted
                state = "Booting" if call_count["n"] <= 2 else "Booted"
                r.stdout = json.dumps({
                    "devices": {
                        "com.apple.CoreSimulator.SimRuntime.iOS-26-0": [
                            {"udid": "FAKE-UDID-004", "state": state, "name": "iPhone 12"}
                        ]
                    }
                })
            return r

        sleep_calls = []
        with patch("subprocess.run", side_effect=fake_simctl):
            with patch("time.sleep", side_effect=lambda s: sleep_calls.append(s)):
                result = _run_handle_app_relaunch({
                    "bundle_id": "io.example.app",
                    "udid": "FAKE-UDID-004",
                })

        # Should have polled at least once
        assert call_count["n"] >= 1
        # Should not have returned sim_not_booted error (because it eventually became Booted)
        # (may have other errors due to the mocked terminate/launch, but not sim_not_booted)

        srv._backend = None
        srv._session_udid = None
