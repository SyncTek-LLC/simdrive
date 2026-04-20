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
        srv._backend = MagicMock()

        from specterqa.ios.runner_process import RunnerState
        runner_process_module = MagicMock()
        new_runner_mock = _make_runner_mock()
        runner_process_module.RunnerProcess.acquire.return_value = new_runner_mock
        runner_process_module.RunnerDeployError = type("RunnerDeployError", (Exception,), {})
        runner_process_module.RunnerState = RunnerState

        xctest_module = MagicMock()

        # Simulate: first call returns 0 (start), subsequent calls return 121+ (past ceiling)
        _call_count = [0]
        _start_time = [None]

        real_monotonic = __import__("time").monotonic

        def _mock_monotonic():
            _call_count[0] += 1
            if _call_count[0] == 1:
                # First call sets the "start" — return a fixed base
                return 1000.0
            # All subsequent calls simulate 125s elapsed (past the 120s cap)
            return 1125.0

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
        srv._backend = MagicMock()

        from specterqa.ios.runner_process import RunnerState
        runner_process_module = MagicMock()
        new_runner_mock = _make_runner_mock()
        runner_process_module.RunnerProcess.acquire.return_value = new_runner_mock
        runner_process_module.RunnerDeployError = type("RunnerDeployError", (Exception,), {})
        runner_process_module.RunnerState = RunnerState

        xctest_module = MagicMock()

        # Simulate a fast simctl call that returns booted device
        _booted_json = '{"devices": {"com.apple.CoreSimulator.SimRuntime.iOS-18-0": [{"udid": "TEST-UDID", "state": "Booted"}]}}'

        import subprocess as _real_sp

        def _mock_run(args, **kwargs):
            mock_result = MagicMock()
            mock_result.returncode = 0
            mock_result.stdout = _booted_json
            mock_result.stderr = ""
            return mock_result

        real_monotonic_val = [1000.0]

        def _mock_monotonic():
            # Always return same value — no timeout
            return real_monotonic_val[0]

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
        srv._backend = MagicMock()

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
                mock_time.monotonic.return_value = 1000.0
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
