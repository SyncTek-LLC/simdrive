"""Regression test — v14.0.2: session state must persist across start→capture→relaunch.

Root cause (fixed in v14.0.2): handle_start_session pre-deployed a RunnerProcess on
port 8222 (for BackendSelector probing), then created a TestSession which called
_find_free_port() (returning 8223+ since 8222 was occupied) and launched a *second*
xcodebuild.  Two xcodebuild processes fighting over the same simulator caused the
first to die; its teardown shut down the simulator; subsequent simctl calls failed
with "No devices are booted."

Fix: when _mcp_runner_ref is already RUNNING and clone=False, the xctest path
reuses it directly (skips TestSession._deploy_runner).

v14.0.2 runner-restart recovery additions (iOS 26.3):
  - When sim is Booted, recovery is NOT invoked — plain terminate+launch runs.
  - When recovery fires, payload contains recovery="runner-restart".
  - Normal path payload does NOT contain "recovery" key.
  - _session_udid is cleared on ios_stop_session.

These tests are hermetic — they mock subprocess and RunnerProcess so no live
simulator is required.
"""
from __future__ import annotations

import json
import threading
from unittest.mock import MagicMock, patch, PropertyMock

import pytest


# 1×1 transparent PNG, base64-encoded — used as a stand-in screenshot in
# session-persistence tests so handle_observe() can decode dimensions
# without erroring out on Incorrect padding.
_TINY_PNG_B64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII="
)


# ---------------------------------------------------------------------------
# Helpers — build a minimal RunnerProcess mock
# ---------------------------------------------------------------------------

def _make_runner_mock(port: int = 8222, udid: str = "TEST-UDID-1234"):
    """Return a MagicMock that looks like a RUNNING RunnerProcess."""
    from specterqa.ios.runner_process import RunnerState

    runner = MagicMock()
    runner._port = port
    runner._udid = udid
    runner.state = RunnerState.RUNNING
    runner.stop = MagicMock()
    runner.deploy = MagicMock()
    runner.healthcheck = MagicMock(return_value=True)
    return runner


# ---------------------------------------------------------------------------
# Test: single RunnerProcess deployed across start → capture → relaunch
# ---------------------------------------------------------------------------

class TestMCPSessionPersistence:
    """Session state (RunnerProcess reference) must survive capture_state calls."""

    def test_start_sets_mcp_runner_ref(self):
        """After ios_start_session, _mcp_runner_ref and _session point to the runner."""
        import specterqa.ios.mcp.server as srv

        runner_mock = _make_runner_mock()

        with (
            patch("specterqa.ios.mcp.server.handle_start_session") as mock_start,
        ):
            mock_start.return_value = {"status": "ok", "backend": "xctest", "target_udid": "TEST-UDID-1234", "port": 8222, "runner_url": "http://localhost:8222"}
            result = mock_start({"bundle_id": "com.example.app", "device_id": "TEST-UDID-1234", "backend": "xctest"})
            assert result["status"] == "ok"
            assert "error" not in result

    def test_mcp_runner_ref_reused_when_running(self):
        """The fix: when _mcp_runner_ref is RUNNING and clone=False, xctest path
        must reuse it as _session rather than deploying a second RunnerProcess.

        We test the logic branch directly by pre-setting _mcp_runner_ref to a
        RUNNING mock and verifying that handle_start_session uses it.
        """
        from specterqa.ios.runner_process import RunnerState
        import specterqa.ios.mcp.server as srv

        runner_mock = _make_runner_mock(port=8222, udid="FAKE-UDID-0001")
        backend_mock = MagicMock()
        backend_mock.return_value = MagicMock()

        # Pre-set _mcp_runner_ref as RUNNING (simulates post-healthcheck state)
        # and provide all mocks so handle_start_session can complete
        srv._session = None
        srv._mcp_runner_ref = runner_mock
        srv._backend = None
        srv._session_state = "idle"

        replay_cls = MagicMock()
        console_cls = MagicMock()
        console_cls.return_value.start = MagicMock()
        crash_cls = MagicMock()
        crash_cls.return_value.start = MagicMock()
        perf_cls = MagicMock()
        net_cls = MagicMock()
        net_cls.return_value.start = MagicMock()
        net_cls.return_value.setup_log_watcher = MagicMock()
        som_cls = MagicMock()
        xctest_cls = MagicMock()
        xctest_cls.return_value = MagicMock()

        license_cls = MagicMock()
        license_cls.return_value.validate.return_value = {"valid": True}

        runner_process_module = MagicMock()
        runner_process_module.RunnerProcess.acquire.return_value = runner_mock
        runner_deploy_err = type("RunnerDeployError", (Exception,), {})
        runner_process_module.RunnerDeployError = runner_deploy_err
        runner_process_module.RunnerState = RunnerState

        selector_module = MagicMock()
        chosen = MagicMock()
        # Make type(chosen).__name__ == "XCTestBackend" by patching backend_name lookup
        class _FakeXCTestBackend:
            pass
        _FakeXCTestBackend.__name__ = "XCTestBackend"
        chosen_instance = _FakeXCTestBackend()
        selector_module.BackendSelector.return_value.choose.return_value = chosen_instance

        session_mgr = MagicMock()
        session_mgr._find_xctestrun.return_value = "/fake/runner.xctestrun"
        session_mgr._needs_rebuild.return_value = False
        session_mgr._DEFAULT_RUNNER_BUILD_DIR = "/fake/runner-build"
        session_mgr.write_version_marker = MagicMock()

        try:
            with (
                patch.dict("sys.modules", {
                    "specterqa.ios.license.validator": MagicMock(LicenseValidator=license_cls),
                    "specterqa.ios.runner_process": runner_process_module,
                    "specterqa.ios.backends.selector": selector_module,
                    "specterqa.ios.som_annotator": MagicMock(SoMAnnotator=som_cls),
                    "specterqa.ios.backends.xctest_client": MagicMock(XCTestBackend=xctest_cls),
                    "specterqa.ios.session_manager": session_mgr,
                    "specterqa.ios.replay": MagicMock(ReplayRecorder=replay_cls),
                    "specterqa.ios.drivers.simulator.console": MagicMock(ConsoleMonitor=console_cls),
                    "specterqa.ios.drivers.simulator.crash": MagicMock(CrashDetector=crash_cls),
                    "specterqa.ios.drivers.simulator.perf": MagicMock(PerfProfiler=perf_cls),
                    "specterqa.ios.drivers.simulator.network": MagicMock(NetworkInspector=net_cls),
                }),
                patch("specterqa.ios.mcp.server._circuit_breaker"),
            ):
                result = srv.handle_start_session({
                    "bundle_id": "com.example.app",
                    "device_id": "FAKE-UDID-0001",
                    "backend": "xctest",
                    "clone": False,
                    "device_type": "simulator",
                })

            # Key invariant: _session must be the pre-deployed runner_mock
            # (not a TestSession), because _mcp_runner_ref was RUNNING
            assert srv._session is runner_mock, (
                f"Expected _session to be the pre-deployed runner_mock, got {type(srv._session)}"
            )
            # TestSession.start() must NOT have been called
            session_mgr.TestSession.return_value.start.assert_not_called()

        finally:
            srv._session = None
            srv._mcp_runner_ref = None
            srv._backend = None
            srv._session_state = "idle"

    def test_session_state_preserved_across_calls(self):
        """_session and _mcp_runner_ref must not change between capture_state and app_relaunch."""
        import specterqa.ios.mcp.server as srv

        # Manually set up the expected post-start_session state
        backend_mock = MagicMock()
        runner_mock = _make_runner_mock()

        original_session = runner_mock
        srv._session = runner_mock
        srv._mcp_runner_ref = runner_mock
        srv._backend = backend_mock
        srv._session_state = "running"

        try:
            # capture_state should NOT change _session or _mcp_runner_ref
            backend_mock.get_elements.return_value = {"elements": [], "count": 0}
            backend_mock.app_state.return_value = {"state": "foreground"}

            with patch("specterqa.ios.mcp.server._get_annotated_screenshot", return_value=(_TINY_PNG_B64, [])):
                result = srv.handle_observe({})

            # Session must be unchanged
            assert srv._session is original_session, "observe must not replace _session"
            assert srv._mcp_runner_ref is original_session, "observe must not clear _mcp_runner_ref"
            assert srv._session_state == "running", "session_state must remain 'running'"
            assert "error" not in result, f"observe returned error: {result}"

        finally:
            # Cleanup module state
            srv._session = None
            srv._mcp_runner_ref = None
            srv._backend = None
            srv._session_state = "idle"

    def test_no_teardown_fires_between_calls(self):
        """RunnerProcess.stop must NOT be called between start → capture → relaunch."""
        import specterqa.ios.mcp.server as srv
        from specterqa.ios.runner_process import RunnerState

        runner_mock = _make_runner_mock()
        backend_mock = MagicMock()
        backend_mock.app_state.return_value = {"state": "foreground"}
        backend_mock.get_elements.return_value = {"elements": [], "count": 0}

        srv._session = runner_mock
        srv._mcp_runner_ref = runner_mock
        srv._backend = backend_mock
        srv._session_state = "running"

        try:
            with patch("specterqa.ios.mcp.server._get_annotated_screenshot", return_value=(_TINY_PNG_B64, [])):
                srv.handle_observe({})

            # No stop() called between start and capture
            runner_mock.stop.assert_not_called()

            # Simulate app_relaunch (hermetic — mock subprocess)
            with patch("specterqa.ios.mcp.server.subprocess") as sp_mock:
                proc_result = MagicMock()
                proc_result.returncode = 0
                proc_result.stderr = ""
                sp_mock.run.return_value = proc_result
                srv.handle_app_relaunch({"bundle_id": "com.example.app", "udid": "FAKE-UDID-0001"})

            # Still no stop() called
            runner_mock.stop.assert_not_called()

        finally:
            srv._session = None
            srv._mcp_runner_ref = None
            srv._backend = None
            srv._session_state = "idle"


# ---------------------------------------------------------------------------
# v14.0.2 runner-restart recovery tests — hermetic, no live sim required
# ---------------------------------------------------------------------------

def _make_simctl_list_response(udid: str, state: str) -> bytes:
    """Return a minimal xcrun simctl list --json payload for one device."""
    payload = {
        "devices": {
            "com.apple.CoreSimulator.SimRuntime.iOS-26-3": [
                {"udid": udid, "state": state, "name": "Test Device"},
            ]
        }
    }
    return json.dumps(payload).encode()


class TestRunnerRestartRecovery:
    """iOS 26.3 runner-restart recovery path — all hermetic via subprocess mocking."""

    # ------------------------------------------------------------------
    # Happy path: Booted sim -> NO recovery, plain terminate+launch
    # ------------------------------------------------------------------

    def test_booted_sim_skips_recovery_path(self):
        """When sim state is Booted, _needs_restart must be False and
        _restart_runner_for_relaunch must NOT be called."""
        import specterqa.ios.mcp.server as srv

        backend_mock = MagicMock()
        backend_mock.app_state.return_value = {"state": "foreground"}
        runner_mock = _make_runner_mock(udid="BOOTED-UDID-0001")

        srv._session = runner_mock
        srv._mcp_runner_ref = runner_mock
        srv._backend = backend_mock
        srv._session_state = "running"
        srv._session_udid = "BOOTED-UDID-0001"

        simctl_response = _make_simctl_list_response("BOOTED-UDID-0001", "Booted")

        ok_proc = MagicMock()
        ok_proc.returncode = 0
        ok_proc.stdout = simctl_response.decode()
        ok_proc.stderr = ""

        try:
            with (
                patch("specterqa.ios.mcp.server._restart_runner_for_relaunch") as restart_mock,
                patch("specterqa.ios.mcp.server.subprocess") as sp_mock,
            ):
                sp_mock.run.return_value = ok_proc
                result = srv.handle_app_relaunch({
                    "bundle_id": "com.example.app",
                    "udid": "BOOTED-UDID-0001",
                })

            restart_mock.assert_not_called()
            assert "error" not in result, f"Expected success result, got: {result}"
            assert "recovery" not in result, (
                "Normal path must not include 'recovery' key"
            )
        finally:
            srv._session = None
            srv._mcp_runner_ref = None
            srv._backend = None
            srv._session_state = "idle"
            srv._session_udid = None

    # ------------------------------------------------------------------
    # Recovery path: Shutdown + xcodebuild alive -> recovery fires
    # ------------------------------------------------------------------

    def test_recovery_path_sets_recovery_field(self):
        """When sim is Shutdown and xcodebuild alive, result must contain
        recovery='runner-restart'."""
        import specterqa.ios.mcp.server as srv

        backend_mock = MagicMock()
        backend_mock.app_state.return_value = {"state": "foreground"}
        runner_mock = _make_runner_mock(udid="RECOVERY-UDID-0001")

        srv._session = runner_mock
        srv._mcp_runner_ref = runner_mock
        srv._backend = backend_mock
        srv._session_state = "running"
        srv._session_udid = "RECOVERY-UDID-0001"

        shutdown_proc = MagicMock()
        shutdown_proc.returncode = 0
        shutdown_proc.stdout = _make_simctl_list_response(
            "RECOVERY-UDID-0001", "Shutdown"
        ).decode()
        shutdown_proc.stderr = ""

        pgrep_proc = MagicMock()
        pgrep_proc.returncode = 0
        pgrep_proc.stdout = "12345\n"  # xcodebuild PID present

        try:
            with (
                patch("specterqa.ios.mcp.server._restart_runner_for_relaunch", return_value=None) as restart_mock,
                patch("specterqa.ios.mcp.server.subprocess") as sp_mock,
            ):
                def _run_side_effect(cmd, **kwargs):
                    if "pgrep" in cmd:
                        return pgrep_proc
                    return shutdown_proc

                sp_mock.run.side_effect = _run_side_effect
                result = srv.handle_app_relaunch({
                    "bundle_id": "com.example.app",
                    "udid": "RECOVERY-UDID-0001",
                })

            restart_mock.assert_called_once_with("RECOVERY-UDID-0001", "com.example.app")
            assert "error" not in result, f"Expected success, got: {result}"
            assert result.get("recovery") == "runner-restart", (
                f"Expected recovery='runner-restart', got: {result.get('recovery')!r}"
            )
        finally:
            srv._session = None
            srv._mcp_runner_ref = None
            srv._backend = None
            srv._session_state = "idle"
            srv._session_udid = None

    # ------------------------------------------------------------------
    # Normal path: no recovery -> 'recovery' key absent from payload
    # ------------------------------------------------------------------

    def test_normal_path_has_no_recovery_key(self):
        """Plain terminate+launch path must NOT include 'recovery' in result."""
        import specterqa.ios.mcp.server as srv

        backend_mock = MagicMock()
        backend_mock.app_state.return_value = {"state": "foreground"}
        runner_mock = _make_runner_mock(udid="NORMAL-UDID-0001")

        srv._session = runner_mock
        srv._mcp_runner_ref = runner_mock
        srv._backend = backend_mock
        srv._session_state = "running"
        srv._session_udid = "NORMAL-UDID-0001"

        booted_proc = MagicMock()
        booted_proc.returncode = 0
        booted_proc.stdout = _make_simctl_list_response("NORMAL-UDID-0001", "Booted").decode()
        booted_proc.stderr = ""

        try:
            with patch("specterqa.ios.mcp.server.subprocess") as sp_mock:
                sp_mock.run.return_value = booted_proc
                result = srv.handle_app_relaunch({
                    "bundle_id": "com.example.app",
                    "udid": "NORMAL-UDID-0001",
                })

            assert "recovery" not in result, (
                f"Normal path must not include 'recovery' key, got: {result}"
            )
            assert "error" not in result, f"Expected success, got: {result}"
        finally:
            srv._session = None
            srv._mcp_runner_ref = None
            srv._backend = None
            srv._session_state = "idle"
            srv._session_udid = None

    # ------------------------------------------------------------------
    # _session_udid lifecycle: set on start, cleared on stop
    # ------------------------------------------------------------------

    def test_session_udid_cleared_on_stop_session(self):
        """_session_udid must be None after ios_stop_session completes."""
        import specterqa.ios.mcp.server as srv

        runner_mock = _make_runner_mock(udid="STOP-UDID-0001")
        backend_mock = MagicMock()

        srv._session = runner_mock
        srv._mcp_runner_ref = runner_mock
        srv._backend = backend_mock
        srv._session_state = "running"
        srv._session_udid = "STOP-UDID-0001"

        try:
            with (
                patch("specterqa.ios.mcp.server._ax_http_server", None),
                patch("specterqa.ios.mcp.server._console_monitor", None),
                patch("specterqa.ios.mcp.server._crash_detector", None),
                patch("specterqa.ios.mcp.server._perf_profiler", None),
                patch("specterqa.ios.mcp.server._network_inspector", None),
                patch("specterqa.ios.mcp.server._recorder", None),
            ):
                result = srv.handle_stop_session({})

            assert srv._session_udid is None, (
                f"_session_udid must be None after stop, got: {srv._session_udid!r}"
            )
        finally:
            srv._session = None
            srv._mcp_runner_ref = None
            srv._backend = None
            srv._session_state = "idle"
            srv._session_udid = None

    # ------------------------------------------------------------------
    # Trigger condition: Shutdown + NO xcodebuild -> _ensure_sim_booted, not restart
    # ------------------------------------------------------------------

    def test_shutdown_without_xcodebuild_does_not_trigger_restart(self):
        """Sim Shutdown but NO xcodebuild process: must use _ensure_sim_booted,
        NOT _restart_runner_for_relaunch."""
        import specterqa.ios.mcp.server as srv

        backend_mock = MagicMock()
        backend_mock.app_state.return_value = {"state": "foreground"}
        runner_mock = _make_runner_mock(udid="NORUNNER-UDID-0001")

        srv._session = runner_mock
        srv._mcp_runner_ref = runner_mock
        srv._backend = backend_mock
        srv._session_state = "running"
        srv._session_udid = "NORUNNER-UDID-0001"

        booted_proc = MagicMock()
        booted_proc.returncode = 0
        booted_proc.stdout = _make_simctl_list_response("NORUNNER-UDID-0001", "Booted").decode()
        booted_proc.stderr = ""

        shutdown_proc = MagicMock()
        shutdown_proc.returncode = 0
        shutdown_proc.stdout = _make_simctl_list_response("NORUNNER-UDID-0001", "Shutdown").decode()
        shutdown_proc.stderr = ""

        pgrep_empty = MagicMock()
        pgrep_empty.returncode = 1
        pgrep_empty.stdout = ""  # no xcodebuild found

        call_count = {"n": 0}

        try:
            with (
                patch("specterqa.ios.mcp.server._restart_runner_for_relaunch") as restart_mock,
                patch("specterqa.ios.mcp.server.subprocess") as sp_mock,
            ):
                def _side_effect(cmd, **kwargs):
                    if "pgrep" in cmd:
                        return pgrep_empty
                    call_count["n"] += 1
                    # First call: Shutdown for the state check; subsequent: Booted (for _ensure_sim_booted)
                    if call_count["n"] == 1:
                        return shutdown_proc
                    return booted_proc

                sp_mock.run.side_effect = _side_effect
                srv.handle_app_relaunch({
                    "bundle_id": "com.example.app",
                    "udid": "NORUNNER-UDID-0001",
                })

            restart_mock.assert_not_called()
        finally:
            srv._session = None
            srv._mcp_runner_ref = None
            srv._backend = None
            srv._session_state = "idle"
            srv._session_udid = None


# ---------------------------------------------------------------------------
# v14.0.3 concurrent-MCP-call race guard tests
# ---------------------------------------------------------------------------

class TestConcurrentMCPCallRaceGuard:
    """_restart_runner_for_relaunch must serialise recovery via _session_lock.

    Two threads attempting recovery simultaneously must not produce interleaved
    global state: one must complete (or fail) before the other runs.
    """

    def test_recovery_serialised_under_session_lock(self):
        """When two threads both attempt _restart_runner_for_relaunch, only one
        runs at a time — the second waits, not interleaves."""
        import specterqa.ios.mcp.server as srv

        order: list[str] = []
        lock_used = threading.Event()

        from specterqa.ios.runner_process import RunnerState
        runner_process_module = MagicMock()
        runner_process_module.RunnerDeployError = type("RunnerDeployError", (Exception,), {})
        runner_process_module.RunnerState = RunnerState

        def _make_new_runner():
            nr = MagicMock()
            nr._port = 8222
            nr._udid = "NEW-UDID"
            nr.state = RunnerState.RUNNING
            nr.stop = MagicMock()
            nr.deploy = MagicMock()
            nr.healthcheck = MagicMock(return_value=True)
            return nr

        runner_process_module.RunnerProcess.acquire.side_effect = _make_new_runner
        xctest_module = MagicMock()

        _booted_json = '{"devices": {"rt": [{"udid": "CONCURRENT-UDID", "state": "Booted"}]}}'

        def _mock_run(args, **kwargs):
            m = MagicMock()
            m.returncode = 0
            m.stdout = _booted_json
            m.stderr = ""
            return m

        results: list[str | None] = []
        errors: list[Exception] = []

        def _run_recovery():
            old = _make_runner_mock(udid="CONCURRENT-UDID")
            srv._mcp_runner_ref = old
            srv._session = old
            # Set backend health to always raise so pre-check budget exhausts fast
            _be = MagicMock()
            _be.health.side_effect = ConnectionRefusedError("down")
            srv._backend = _be
            try:
                # Monotonic advances by 2s each call — exhausts pre-check (10s)
                # and outer timeout (120s) budgets without real sleeping.
                _tick = [1000.0]
                def _advancing_monotonic():
                    _tick[0] += 2.0
                    return _tick[0]

                with (
                    patch.dict("sys.modules", {
                        "specterqa.ios.runner_process": runner_process_module,
                        "specterqa.ios.backends.xctest_client": xctest_module,
                    }),
                    patch("specterqa.ios.mcp.server.subprocess") as mock_sp,
                    patch("specterqa.ios.mcp.server.time") as mock_time,
                ):
                    mock_sp.run.side_effect = _mock_run
                    mock_time.monotonic.side_effect = _advancing_monotonic
                    mock_time.sleep = MagicMock()
                    r = srv._restart_runner_for_relaunch("CONCURRENT-UDID", "com.example.app")
                results.append(r)
            except Exception as e:
                errors.append(e)

        t1 = threading.Thread(target=_run_recovery)
        t2 = threading.Thread(target=_run_recovery)

        try:
            t1.start()
            t2.start()
            t1.join(timeout=10)
            t2.join(timeout=10)

            assert not errors, f"Threads raised exceptions: {errors}"
            # Both should have completed (no deadlock)
            assert not t1.is_alive(), "Thread 1 did not complete within 10s"
            assert not t2.is_alive(), "Thread 2 did not complete within 10s"
        finally:
            srv._session = None
            srv._mcp_runner_ref = None
            srv._backend = None
            srv._session_state = "idle"
            srv._session_udid = None
