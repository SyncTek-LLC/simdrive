"""Tests for v15.1.0 forgiving detection (sections 1, 2, 5).

Section 1: _verify_sim_alive retries before declaring dead.
Section 2: _restart_runner_for_relaunch pre-checks runner health before recovery.
Section 5: ios_capture_state, ios_tap retry once on transient before bubbling error.

All tests use mocked subprocess / backend — no live simulator required.
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch, call
from typing import Any

import pytest

import specterqa.ios.mcp.server as _srv


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

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
    _srv._mcp_runner_ref = None


@pytest.fixture(autouse=True)
def reset_state():
    _reset_server_state()
    yield
    _reset_server_state()


TEST_UDID = "FORGIVING-TEST-UDID-0001"


def _make_simctl_response(udid: str, state: str) -> MagicMock:
    r = MagicMock()
    r.returncode = 0
    r.stdout = json.dumps({
        "devices": {
            "com.apple.CoreSimulator.SimRuntime.iOS-26-0": [
                {"udid": udid, "state": state, "name": "iPhone 15"}
            ]
        }
    })
    return r


def _inject_active_session(udid: str = TEST_UDID):
    """Inject minimal active session so session guards pass."""
    backend = MagicMock()
    backend.udid = udid
    backend.health = MagicMock(side_effect=ConnectionRefusedError("runner down"))
    backend._get = MagicMock(return_value={})
    backend._post = MagicMock(return_value={})
    backend.app_state = MagicMock(return_value={"state": "foreground"})

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

    _srv._backend = backend
    _srv._session_udid = udid
    _srv._session_state = "running"
    _srv._annotator = annotator
    _srv._last_elements = [el]
    return backend


# ---------------------------------------------------------------------------
# Section 1: _verify_sim_alive retries before declaring dead
# ---------------------------------------------------------------------------

class TestVerifySimAliveRetriesBeforeDeclaringDead:
    """_verify_sim_alive must poll for up to 15s before returning dead."""

    def test_verify_sim_alive_retries_before_declaring_dead(self):
        """When simctl returns Shutdown for 3 calls then Booted, _verify_sim_alive
        should return alive (not dead) after retry — SpringBoard respawn pattern."""
        backend = _inject_active_session()

        # Runner health always fails (unreachable)
        backend.health.side_effect = ConnectionRefusedError("runner unreachable")

        # simctl: Shutdown x3, then Booted
        _call_count = [0]
        def _simctl_side_effect(*args, **kwargs):
            cmd = args[0] if args else kwargs.get("args", [])
            if "simctl" in str(cmd):
                _call_count[0] += 1
                if _call_count[0] <= 3:
                    return _make_simctl_response(TEST_UDID, "Shutdown")
                else:
                    return _make_simctl_response(TEST_UDID, "Booted")
            return MagicMock(returncode=0, stdout="", stderr="")

        with patch("subprocess.run", side_effect=_simctl_side_effect), \
             patch("time.sleep"):  # don't actually sleep
            alive, state = _srv._verify_sim_alive(TEST_UDID, poll_budget_s=15.0)

        assert alive is True, f"Expected alive=True after SpringBoard respawn, got alive={alive!r} state={state!r}"

    def test_verify_sim_alive_returns_dead_after_budget_exhausted(self):
        """When simctl always reports Shutdown and budget expires, return dead."""
        backend = _inject_active_session()
        backend.health.side_effect = ConnectionRefusedError("runner unreachable")

        def _simctl_always_shutdown(*args, **kwargs):
            return _make_simctl_response(TEST_UDID, "Shutdown")

        import time as _real_time
        _start = [None]

        def _mock_monotonic():
            if _start[0] is None:
                _start[0] = 1000.0
                return 1000.0
            # Advance 2s per call to exhaust budget fast
            _start[0] += 2.0
            return _start[0]

        with patch("subprocess.run", side_effect=_simctl_always_shutdown), \
             patch("time.sleep"), \
             patch("time.monotonic", side_effect=_mock_monotonic):
            alive, state = _srv._verify_sim_alive(TEST_UDID, poll_budget_s=5.0)

        assert alive is False, f"Expected dead after budget exhausted, got alive={alive!r}"
        assert state in ("Shutdown", "ShuttingDown"), f"Expected dead state, got state={state!r}"

    def test_verify_sim_alive_returns_true_immediately_when_runner_healthy(self):
        """When runner health check succeeds, returns alive immediately without polling."""
        backend = _inject_active_session()
        backend.health.return_value = {"status": "ok"}
        backend.health.side_effect = None  # override fixture

        with patch("subprocess.run") as mock_run:
            alive, state = _srv._verify_sim_alive(TEST_UDID)

        # subprocess.run should NOT be called (runner health bypasses simctl)
        assert alive is True
        assert state == "Booted"
        mock_run.assert_not_called()

    def test_verify_sim_alive_treats_unknown_state_as_alive(self):
        """Unknown / Booting state should be treated as alive (don't block)."""
        backend = _inject_active_session()
        backend.health.side_effect = ConnectionRefusedError("unreachable")

        with patch("subprocess.run", return_value=_make_simctl_response(TEST_UDID, "Booting")), \
             patch("time.sleep"), \
             patch("time.monotonic", side_effect=[1000.0, 1001.0]):
            alive, state = _srv._verify_sim_alive(TEST_UDID, poll_budget_s=5.0)

        assert alive is True


# ---------------------------------------------------------------------------
# Section 2: _restart_runner_for_relaunch pre-checks before recovery
# ---------------------------------------------------------------------------

class TestRestartRunnerSkipsRecoveryIfRunnerRecovers:
    """Pre-flight health poll: skip recovery if runner becomes healthy within 10s."""

    def test_restart_runner_skips_recovery_if_runner_recovers_during_precheck(self):
        """If _backend.health() returns ok during the 10s pre-check, return None (skip)."""
        from specterqa.ios.runner_process import RunnerState

        runner_mock = MagicMock()
        runner_mock._port = 8222
        runner_mock._udid = TEST_UDID
        runner_mock.state = RunnerState.RUNNING
        runner_mock.stop = MagicMock()

        _srv._mcp_runner_ref = runner_mock
        _srv._session = runner_mock

        backend_mock = MagicMock()
        # First call fails (Shutdown signal), second call succeeds (recovered)
        backend_mock.health.side_effect = [
            ConnectionRefusedError("down"),
            {"status": "ok"},  # recovered on second check
        ]
        _srv._backend = backend_mock

        # monotonic must never exhaust — use an incrementing counter
        _t = [0.0]
        def _inc_monotonic():
            _t[0] += 1.0
            return _t[0]

        with patch("time.sleep"), \
             patch("time.monotonic", side_effect=_inc_monotonic):
            result = _srv._restart_runner_for_relaunch(TEST_UDID, "com.test.app")

        # Should return None (success / skip) because runner recovered
        assert result is None, f"Expected None (recovery skipped), got: {result!r}"

    def test_restart_runner_proceeds_to_recovery_when_runner_stays_unreachable(self):
        """If runner never becomes healthy during pre-check, proceed to full recovery."""
        from specterqa.ios.runner_process import RunnerState, RunnerDeployError

        runner_mock = MagicMock()
        runner_mock._port = 8222
        runner_mock._udid = TEST_UDID
        runner_mock.state = RunnerState.RUNNING
        runner_mock.stop = MagicMock()

        _srv._mcp_runner_ref = runner_mock
        _srv._session = runner_mock

        backend_mock = MagicMock()
        # Pre-check: always unreachable
        backend_mock.health.side_effect = ConnectionRefusedError("always down")
        _srv._backend = backend_mock

        new_runner_mock = MagicMock()
        new_runner_mock._port = 8222
        new_runner_mock._udid = TEST_UDID
        new_runner_mock.state = RunnerState.RUNNING
        new_runner_mock.deploy = MagicMock()
        new_runner_mock.healthcheck = MagicMock(return_value=True)
        new_runner_mock.stop = MagicMock()

        RunnerDeployErrorClass = type("RunnerDeployError", (Exception,), {})

        runner_process_module = MagicMock()
        runner_process_module.RunnerProcess.acquire.return_value = new_runner_mock
        runner_process_module.RunnerDeployError = RunnerDeployErrorClass
        runner_process_module.RunnerState = RunnerState

        xctest_module = MagicMock()

        # Sim transitions: Booted → Shutdown (stable)
        _simctl_responses = [
            _make_simctl_response(TEST_UDID, "Shutdown"),  # shutdown poll
        ] * 40  # plenty for all polls

        call_count = [0]
        def _mono():
            call_count[0] += 1
            # Pre-check: calls 1-15 within 10s budget
            if call_count[0] <= 15:
                return float(call_count[0])
            # Recovery calls: still within 120s
            return float(call_count[0]) + 10.0

        with patch("time.sleep"), \
             patch("time.monotonic", side_effect=_mono), \
             patch("subprocess.run", side_effect=_simctl_responses), \
             patch.dict("sys.modules", {
                 "specterqa.ios.runner_process": runner_process_module,
                 "specterqa.ios.backends.xctest_client": xctest_module,
             }):
            # Should try recovery — will likely fail on boot step, but shouldn't skip
            result = _srv._restart_runner_for_relaunch(TEST_UDID, "com.test.app")

        # new_runner_mock.deploy should have been called (recovery started)
        new_runner_mock.deploy.assert_called()


# ---------------------------------------------------------------------------
# Section 5: Transparent retry on transient for user-visible tools
# ---------------------------------------------------------------------------

class TestTransparentRetry:
    """ios_capture_state and ios_tap retry once on transient failures."""

    def test_capture_state_retries_once_on_transient_then_succeeds(self):
        """If handle_capture_state first returns a transient error then succeeds,
        _retry_once_on_transient should return the success result."""
        success_result = {
            "captured_at": "2026-04-21T00:00:00.000Z",
            "elements": [{"index": 1, "label": "Identity", "type": "StaticText"}],
        }
        transient_result = {"error": "Connection refused"}

        call_count = [0]
        def _mock_capture(args):
            call_count[0] += 1
            if call_count[0] == 1:
                return transient_result
            return success_result

        with patch("time.sleep"):
            result = _srv._retry_once_on_transient(_mock_capture, {})

        assert "error" not in result, f"Expected success after retry, got: {result}"
        assert "elements" in result or "captured_at" in result
        assert call_count[0] == 2, f"Expected 2 calls (1 fail + 1 retry), got {call_count[0]}"

    def test_tap_retries_once_on_transient_then_succeeds(self):
        """If handle_tap first returns a transient Connection refused error then succeeds,
        _retry_once_on_transient should return the success."""
        tap_success = {"status": "ok", "tapped": "Submit", "x": 195.0, "y": 400.0}
        tap_transient = {"error": "ConnectionRefusedError: runner unreachable"}

        call_count = [0]
        def _mock_tap(args):
            call_count[0] += 1
            if call_count[0] == 1:
                return tap_transient
            return tap_success

        with patch("time.sleep"):
            result = _srv._retry_once_on_transient(_mock_tap, {"label": "Submit"})

        assert result.get("status") == "ok", f"Expected ok after retry, got: {result}"
        assert call_count[0] == 2

    def test_capture_state_does_not_retry_on_non_transient(self):
        """Non-retryable errors (e.g. 'No active session') are not retried."""
        fatal_result = {"error": "No active session. Call ios_start_session first."}

        call_count = [0]
        def _mock_capture(args):
            call_count[0] += 1
            return fatal_result

        with patch("time.sleep"):
            result = _srv._retry_once_on_transient(_mock_capture, {})

        # Should return the error without retry
        assert result.get("error") == "No active session. Call ios_start_session first."
        assert call_count[0] == 1, f"Expected 1 call (no retry on fatal), got {call_count[0]}"

    def test_capture_state_bubbles_error_after_two_transient_failures(self):
        """If both calls return transient error, the second error is returned."""
        transient_result = {"error": "Connection refused"}

        call_count = [0]
        def _mock_capture(args):
            call_count[0] += 1
            return transient_result

        with patch("time.sleep"):
            result = _srv._retry_once_on_transient(_mock_capture, {})

        assert "error" in result
        assert call_count[0] == 2

    def test_retryable_flag_added_on_second_transient_failure(self):
        """When second call also fails with transient, result carries retryable=True."""
        transient_result = {"error": "sim_shutdown_during_session"}

        with patch("time.sleep"):
            result = _srv._retry_once_on_transient(lambda _: transient_result, {})

        assert result.get("retryable") is True, f"Expected retryable=True, got: {result}"
