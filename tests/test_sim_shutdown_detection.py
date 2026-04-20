"""Regression tests for F2: sim_shutdown_during_session detection.

Verifies that handle_capture_state and handle_tap return a structured
{"error": "sim_shutdown_during_session", ...} dict when the simulator is
in Shutdown state, rather than silently serving stale cached data.

All tests use mocked subprocess — no live simulator required.
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

import specterqa.ios.mcp.server as _srv


def _make_shutdown_simctl_response(udid: str) -> MagicMock:
    """Return a fake subprocess result where the given UDID is Shutdown."""
    r = MagicMock()
    r.returncode = 0
    r.stdout = json.dumps({
        "devices": {
            "com.apple.CoreSimulator.SimRuntime.iOS-18-0": [
                {"udid": udid, "state": "Shutdown", "name": "iPhone 15"}
            ]
        }
    })
    return r


def _make_booted_simctl_response(udid: str) -> MagicMock:
    """Return a fake subprocess result where the given UDID is Booted."""
    r = MagicMock()
    r.returncode = 0
    r.stdout = json.dumps({
        "devices": {
            "com.apple.CoreSimulator.SimRuntime.iOS-18-0": [
                {"udid": udid, "state": "Booted", "name": "iPhone 15"}
            ]
        }
    })
    return r


def _reset_server_state():
    _srv._session = None
    _srv._backend = None
    _srv._annotator = None
    _srv._last_elements = []
    _srv._recorder = None
    _srv._session_state = "idle"
    _srv._session_udid = None
    _srv._console_monitor = None
    _srv._crash_detector = None
    _srv._perf_profiler = None
    _srv._network_inspector = None
    _srv._ax_http_server = None
    _srv._perf_baseline = None


@pytest.fixture(autouse=True)
def reset_state():
    _reset_server_state()
    yield
    _reset_server_state()


TEST_UDID = "SHUTDOWN-TEST-UDID-0001"


def _inject_active_session(udid: str = TEST_UDID):
    """Inject a minimal active session so session guards pass."""
    backend = MagicMock()
    backend.udid = udid
    backend._get = MagicMock(return_value={})
    backend._post = MagicMock(return_value={})
    backend.app_state = MagicMock(return_value={"state": "foreground"})
    _srv._backend = backend
    _srv._session_udid = udid
    _srv._session_state = "running"

    el = MagicMock()
    el.index = 1
    el.label = "Button"
    el.element_type = "Button"
    el.x = 50
    el.y = 100
    el.width = 80
    el.height = 44
    annotator = MagicMock()
    annotator.get_elements_from_runner = MagicMock(return_value=[el])
    _srv._annotator = annotator
    _srv._last_elements = [el]
    return backend


class TestSimShutdownDetection:
    """handle_capture_state and handle_tap return sim_shutdown_during_session when sim is down."""

    def test_capture_state_returns_shutdown_error_when_sim_is_down(self):
        """handle_capture_state must return sim_shutdown_during_session, not stale data."""
        _inject_active_session()

        with patch("subprocess.run", return_value=_make_shutdown_simctl_response(TEST_UDID)):
            result = _srv.handle_capture_state({"include": ["elements"]})

        assert "error" in result, f"Expected error key, got: {result}"
        assert result["error"] == "sim_shutdown_during_session", (
            f"Expected 'sim_shutdown_during_session', got: {result['error']!r}"
        )
        assert "sim_state" in result, "Expected sim_state in error response"
        assert result.get("action_needed") == "boot_and_reauth"

    def test_capture_state_succeeds_when_sim_is_booted(self):
        """handle_capture_state must NOT return shutdown error when sim is Booted."""
        _inject_active_session()

        with patch("subprocess.run", return_value=_make_booted_simctl_response(TEST_UDID)):
            result = _srv.handle_capture_state({"include": ["elements"]})

        # Should not be a shutdown error — elements or captured_at should be present
        assert result.get("error") != "sim_shutdown_during_session", (
            "Should not return shutdown error when sim is Booted"
        )

    def test_tap_returns_shutdown_error_when_sim_is_down(self):
        """handle_tap must return sim_shutdown_during_session, not a tap error, when sim is down."""
        _inject_active_session()

        with patch("subprocess.run", return_value=_make_shutdown_simctl_response(TEST_UDID)):
            result = _srv.handle_tap({"label": "Submit"})

        assert "error" in result, f"Expected error key, got: {result}"
        assert result["error"] == "sim_shutdown_during_session", (
            f"Expected 'sim_shutdown_during_session', got: {result['error']!r}"
        )
        assert result.get("action_needed") == "boot_and_reauth"

    def test_shutdown_check_skipped_when_no_session_udid(self):
        """If _session_udid is None, the sim check must be skipped (no crash)."""
        # No session at all — _backend is None
        result = _srv.handle_capture_state({"include": ["elements"]})

        # Should return "No active session" error, not a sim-check crash
        assert "error" in result
        assert "sim_shutdown_during_session" not in result["error"]
