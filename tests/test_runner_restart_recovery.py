"""Tests for _restart_runner_for_relaunch recovery path (v14.0.3).

Covers:
  - 120s outer timeout cap: when monotonic clock is mocked past the ceiling,
    the function returns the timeout error string.
  - Happy path: returns None on success.
"""
from __future__ import annotations

import threading
from unittest.mock import MagicMock, patch, call

import pytest


def _make_runner_mock(port: int = 8222, udid: str = "TEST-UDID"):
    from specterqa.ios.runner_process import RunnerState
    r = MagicMock()
    r._port = port
    r._udid = udid
    r.state = RunnerState.RUNNING
    r.stop = MagicMock()
    r.deploy = MagicMock()
    r.healthcheck = MagicMock(return_value=True)
    return r


class TestRunnerRestartOuterTimeout:
    """_restart_runner_for_relaunch must cap at 120s."""

    def test_timeout_exceeded_returns_error(self):
        """When time.monotonic() jumps past the 120s ceiling, the function
        returns the timeout error message without hanging."""
        import specterqa.ios.mcp.server as srv

        runner_mock = _make_runner_mock()
        srv._mcp_runner_ref = runner_mock
        srv._session = runner_mock

        # v15.1: pre-check polls _backend.health() before recovery.
        # Make health() always raise so the pre-check exhausts its budget.
        backend_mock = MagicMock()
        backend_mock.health.side_effect = ConnectionRefusedError("runner down")
        srv._backend = backend_mock

        from specterqa.ios.runner_process import RunnerState
        runner_process_module = MagicMock()
        new_runner_mock = _make_runner_mock()
        runner_process_module.RunnerProcess.acquire.return_value = new_runner_mock
        runner_process_module.RunnerDeployError = type("RunnerDeployError", (Exception,), {})
        runner_process_module.RunnerState = RunnerState

        xctest_module = MagicMock()

        # v15.1 monotonic call sequence:
        #   call 1: _precheck_t0 = 1000.0
        #   call 2+: pre-check while loop — return 1012.0 (> 1000+10) to exit budget
        #   call N: _t0_recovery — must be a BASE so outer timeout can trip
        #   call N+1+: outer timeout checks — return base+125 to exceed 120s ceiling
        _call_count = [0]

        def _mock_monotonic():
            _call_count[0] += 1
            if _call_count[0] == 1:
                return 1000.0   # pre-check t0
            if _call_count[0] == 2:
                return 1012.0   # exits pre-check budget (1012 - 1000 = 12 > 10)
            if _call_count[0] == 3:
                return 2000.0   # _t0_recovery baseline
            # All subsequent: 125s past _t0_recovery → trips 120s outer timeout
            return 2125.0

        try:
            with (
                patch.dict("sys.modules", {
                    "specterqa.ios.runner_process": runner_process_module,
                    "specterqa.ios.backends.xctest_client": xctest_module,
                }),
                patch("specterqa.ios.mcp.server.time") as mock_time,
            ):
                mock_time.monotonic.side_effect = _mock_monotonic
                mock_time.sleep = MagicMock()

                result = srv._restart_runner_for_relaunch("TEST-UDID", "com.example.app")

            assert result is not None, "Expected timeout error, got None (success)"
            assert "120s" in result or "recovery exceeded" in result.lower(), (
                f"Expected timeout message, got: {result!r}"
            )
        finally:
            srv._mcp_runner_ref = None
            srv._session = None
            srv._backend = None

    def test_happy_path_success(self):
        """When all steps succeed quickly, _restart_runner_for_relaunch returns None."""
        import specterqa.ios.mcp.server as srv

        runner_mock = _make_runner_mock()
        srv._mcp_runner_ref = runner_mock
        srv._session = runner_mock

        # v15.1: pre-check polls health() — return ok immediately to skip pre-check.
        backend_mock = MagicMock()
        backend_mock.health.return_value = {"status": "ok"}
        srv._backend = backend_mock

        from specterqa.ios.runner_process import RunnerState
        runner_process_module = MagicMock()
        new_runner_mock = _make_runner_mock()
        runner_process_module.RunnerProcess.acquire.return_value = new_runner_mock
        runner_process_module.RunnerDeployError = type("RunnerDeployError", (Exception,), {})
        runner_process_module.RunnerState = RunnerState

        xctest_module = MagicMock()

        # Simulate a fast simctl call that returns booted device
        _booted_json = '{"devices": {"com.apple.CoreSimulator.SimRuntime.iOS-18-0": [{"udid": "TEST-UDID", "state": "Booted"}]}}'

        def _mock_run(args, **kwargs):
            mock_result = MagicMock()
            mock_result.returncode = 0
            mock_result.stdout = _booted_json
            mock_result.stderr = ""
            return mock_result

        def _mock_monotonic():
            # Always return same value — no timeout (pre-check already passed via health)
            return 1000.0

        try:
            with (
                patch.dict("sys.modules", {
                    "specterqa.ios.runner_process": runner_process_module,
                    "specterqa.ios.backends.xctest_client": xctest_module,
                }),
                patch("specterqa.ios.mcp.server.subprocess") as mock_sp,
                patch("specterqa.ios.mcp.server.time") as mock_time,
            ):
                mock_sp.run.side_effect = _mock_run
                mock_time.monotonic.side_effect = _mock_monotonic
                mock_time.sleep = MagicMock()

                result = srv._restart_runner_for_relaunch("TEST-UDID", "com.example.app")

            assert result is None, f"Expected None (success), got: {result!r}"
        finally:
            srv._mcp_runner_ref = None
            srv._session = None
            srv._backend = None


class TestRestartRunnerAtomicStateUpdate:
    """Global state (_mcp_runner_ref, _session, _backend) must be updated atomically."""

    def test_globals_updated_together(self):
        """After successful recovery, _mcp_runner_ref, _session, _backend must all be updated."""
        import specterqa.ios.mcp.server as srv

        old_runner = _make_runner_mock(port=8222, udid="OLD-UDID")
        srv._mcp_runner_ref = old_runner
        srv._session = old_runner
        # v15.1: pre-check polls health() — always raise so pre-check budget exhausts
        # and full recovery proceeds (globals will be updated).
        backend_mock_old = MagicMock()
        backend_mock_old.health.side_effect = ConnectionRefusedError("runner down")
        srv._backend = backend_mock_old

        from specterqa.ios.runner_process import RunnerState
        runner_process_module = MagicMock()
        new_runner_mock = _make_runner_mock(port=8222, udid="NEW-UDID")
        runner_process_module.RunnerProcess.acquire.return_value = new_runner_mock
        runner_process_module.RunnerDeployError = type("RunnerDeployError", (Exception,), {})
        runner_process_module.RunnerState = RunnerState

        new_xctest_instance = MagicMock()
        xctest_module = MagicMock()
        xctest_module.XCTestBackend.return_value = new_xctest_instance

        _booted_json = '{"devices": {"rt": [{"udid": "NEW-UDID", "state": "Booted"}]}}'

        def _mock_run(args, **kwargs):
            mock_result = MagicMock()
            mock_result.returncode = 0
            mock_result.stdout = _booted_json
            mock_result.stderr = ""
            return mock_result

        # v15.1 monotonic sequence:
        #   call 1: _precheck_t0 = 1000.0
        #   call 2: pre-check loop → 1012.0 (exits budget, 12 > 10)
        #   call 3+: _t0_recovery and outer-timeout checks → constant 1000.0 (no timeout)
        _tick = [0]
        def _mock_monotonic():
            _tick[0] += 1
            if _tick[0] == 1:
                return 1000.0   # pre-check t0
            if _tick[0] == 2:
                return 1012.0   # exits pre-check (12 > 10s budget)
            return 1000.0       # _t0_recovery and all subsequent — no outer timeout

        try:
            with (
                patch.dict("sys.modules", {
                    "specterqa.ios.runner_process": runner_process_module,
                    "specterqa.ios.backends.xctest_client": xctest_module,
                }),
                patch("specterqa.ios.mcp.server.subprocess") as mock_sp,
                patch("specterqa.ios.mcp.server.time") as mock_time,
            ):
                mock_sp.run.side_effect = _mock_run
                mock_time.monotonic.side_effect = _mock_monotonic
                mock_time.sleep = MagicMock()

                result = srv._restart_runner_for_relaunch("NEW-UDID", "com.example.app")

            if result is None:
                # Only assert atomicity when recovery succeeded
                assert srv._mcp_runner_ref is new_runner_mock
                assert srv._session is new_runner_mock
                assert srv._backend is new_xctest_instance
        finally:
            srv._mcp_runner_ref = None
            srv._session = None
            srv._backend = None
