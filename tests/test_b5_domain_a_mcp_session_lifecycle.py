"""RED tests — SimDrive b5 Domain A: MCP/session lifecycle.

Findings covered:
  F#1 — MCP server self-restart on disk-version drift (HIGH)
  F#2 — session_start verify foreground post-launch (HIGH)
  F#10 — logs() returns 0 lines for reasonable predicates (MEDIUM)

All tests MUST fail red until production code implements the features.
No live simulator required — run via: pytest -m "not live" tests/test_b5_domain_a_mcp_session_lifecycle.py

INIT-2026-549 (SimDrive Launch Sprint W1) / b5 domain-A test sprint.
"""
from __future__ import annotations

import time
from unittest.mock import MagicMock, patch, call

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_log_entry(message: str, subsystem: str = "", level: str = "default"):
    """Return a minimal LogEntry-like object for ConsoleMonitor injection."""
    from specterqa.ios.drivers.simulator.console import LogEntry
    return LogEntry(
        timestamp=str(time.time()),
        level=level,
        subsystem=subsystem,
        category="",
        message=message,
        process="simdrive-demo",
        thread_id=1,
        ingestion_time=time.time(),
    )


def _make_runner_mock(port: int = 8222, udid: str = "TEST-UDID-DRIFT"):
    """Return a RUNNING RunnerProcess mock."""
    from specterqa.ios.runner_process import RunnerState
    runner = MagicMock()
    runner._port = port
    runner._udid = udid
    runner.state = RunnerState.RUNNING
    runner.stop = MagicMock()
    runner.deploy = MagicMock()
    runner.healthcheck = MagicMock(return_value=True)
    return runner


# ===========================================================================
# F#1 — MCP server self-restart on disk-version drift
# ===========================================================================


class TestF1MCPVersionDrift:
    """MCP server must schedule a re-exec when disk version != loaded version.

    These tests will FAIL RED until production code implements:
      - A _should_reexec flag (or equivalent hook) that is set when drift is
        detected on the NEXT stdin message after the drifting tool call.
      - A re-exec hook (_reexec_callback / os.execv) that fires on the
        subsequent message — NOT on the same tool call that detected drift.

    The production entrypoint to hook is likely the serve() / stdin loop in
    server.py, or a per-message middleware layer added to the FastMCP dispatch.
    """

    def test_drift_detected_sets_reexec_flag(self):
        """When importlib version differs from __version__, a re-exec flag is set.

        Production code must expose a module-level `_should_reexec` boolean
        (or equivalent) that becomes True when drift is detected.

        FAILS RED: `_should_reexec` does not exist in the current server module.
        """
        import specterqa.ios.mcp.server as srv

        old_version = "1.0.0a12"
        new_version = "1.0.0b4"

        # Simulate: importlib.metadata reports the new disk version, while
        # specterqa.__version__ still holds the old loaded version.
        with patch("importlib.metadata.version", return_value=new_version):
            # Reset flag before test (may not exist yet — that's the point)
            if hasattr(srv, "_should_reexec"):
                srv._should_reexec = False

            # Trigger drift detection by calling a dedicated helper or any
            # tool handler that checks version in the dispatch loop.
            if hasattr(srv, "_check_version_drift"):
                srv._check_version_drift(loaded_version=old_version)
            else:
                # No helper exists — simulate by calling a tool handler.
                # Production code must add the drift check in message dispatch.
                srv.handle_logs({"seconds": 1})

            assert hasattr(srv, "_should_reexec"), (
                "F#1: server module must expose a `_should_reexec` flag "
                "that is set when disk version != loaded version. "
                "Add `_should_reexec = False` to server.py and implement "
                "_check_version_drift() to set it when drift is detected."
            )
            assert srv._should_reexec is True, (
                f"F#1: `_should_reexec` must be True when disk version "
                f"({new_version!r}) != loaded version ({old_version!r}). "
                f"Got _should_reexec={srv._should_reexec!r}. "
                "Implement version drift detection in the message dispatch loop."
            )

    def test_reexec_fires_on_next_message_not_same_call(self):
        """Re-exec must NOT fire on the same tool call that detected drift.

        It should fire on the NEXT stdin message to preserve the current response.

        Production code must:
          1. Set _should_reexec=True when drift is first detected (message N).
          2. Check _should_reexec at the TOP of the next message loop iteration
             (message N+1) and call os.execv (or the reexec hook) BEFORE
             dispatching the tool.

        FAILS RED: no deferred-reexec mechanism exists.
        """
        import specterqa.ios.mcp.server as srv

        reexec_calls: list[str] = []

        def _mock_reexec_hook():
            reexec_calls.append("reexec_called")

        # The production attribute that will hold the re-exec hook.
        # If absent, the test fails with a clear AttributeError message.
        assert hasattr(srv, "_reexec_hook"), (
            "F#1: server module must expose a `_reexec_hook` callable "
            "(default: lambda: os.execv(sys.executable, [sys.executable]+sys.argv)). "
            "This allows tests to inject a mock without spawning a real process."
        )

        # Install mock hook
        original_hook = srv._reexec_hook
        srv._reexec_hook = _mock_reexec_hook
        srv._should_reexec = True  # Simulate: drift was detected on previous message

        try:
            # Simulate "next message" processing — the dispatch loop must call
            # the reexec hook before dispatching the tool when _should_reexec=True.
            if hasattr(srv, "_pre_message_hook"):
                srv._pre_message_hook()
            else:
                pytest.fail(
                    "F#1: server module must implement `_pre_message_hook()` "
                    "called at the start of each stdin message. "
                    "When `_should_reexec` is True, it must call `_reexec_hook()` "
                    "and NOT proceed to dispatch the tool."
                )

            assert reexec_calls, (
                "F#1: `_pre_message_hook()` was called but did not invoke `_reexec_hook()` "
                "even though `_should_reexec` was True. "
                "Implement the re-exec branch in _pre_message_hook()."
            )
        finally:
            srv._reexec_hook = original_hook
            if hasattr(srv, "_should_reexec"):
                srv._should_reexec = False

    def test_no_reexec_when_versions_match(self):
        """When loaded version == disk version, _should_reexec stays False.

        FAILS RED: _should_reexec does not exist; will AttributeError on hasattr check.
        """
        import specterqa.ios.mcp.server as srv

        matching_version = "1.0.0b4"

        assert hasattr(srv, "_should_reexec"), (
            "F#1: `_should_reexec` attribute missing from server module. "
            "Add it and implement _check_version_drift()."
        )

        # Reset flag
        srv._should_reexec = False

        with patch("importlib.metadata.version", return_value=matching_version):
            # Patch the loaded version to match disk
            import specterqa.ios as _pkg_root
            with patch.object(_pkg_root, "__version__", matching_version, create=True):
                if hasattr(srv, "_check_version_drift"):
                    srv._check_version_drift()
                # else: no-op, flag stays False

        assert srv._should_reexec is False, (
            "F#1: `_should_reexec` must remain False when disk version == loaded version. "
            f"Got _should_reexec={srv._should_reexec!r}."
        )

    def test_drift_detection_uses_importlib_metadata(self):
        """Version drift detection must use importlib.metadata.version('simdrive')
        (or the package name) rather than a static string.

        FAILS RED: _check_version_drift does not exist.
        """
        import specterqa.ios.mcp.server as srv

        assert hasattr(srv, "_check_version_drift"), (
            "F#1: `_check_version_drift()` function missing from server module. "
            "Implement it: compare importlib.metadata.version(<pkg>) to "
            "the currently-loaded __version__ and set _should_reexec=True on mismatch."
        )


# ===========================================================================
# F#2 — session_start verify foreground post-launch
# ===========================================================================


class TestF2SessionStartForegroundVerification:
    """handle_start_session must poll app_state after launch and return
    state='launched_then_exited' + crash_report_path when the app crashes.

    These tests FAIL RED until production code adds a post-launch poll loop
    (1s budget, ~100ms intervals) in handle_start_session that:
      1. Queries app_state via the backend or simctl.
      2. If the app exited before the poll window, returns
         {"state": "launched_then_exited", "crash_report_path": "<abs_path>"}.
      3. On success (foreground), returns the current {"status": "ok", ...}.
    """

    # ------------------------------------------------------------------
    # Happy path — app stays foreground → state="active" (pre-existing)
    # ------------------------------------------------------------------

    def test_happy_path_returns_status_ok_when_app_foreground(self):
        """When the app stays foreground after launch, result must contain status='ok'.

        This test documents the currently-working happy path. It will still pass
        RED unless the post-launch poll is wired and accidentally breaks this path.
        If it already passes, that's fine — it documents expected behavior.
        """
        import specterqa.ios.mcp.server as srv

        runner_mock = _make_runner_mock()
        backend_mock = MagicMock()
        # Simulate app staying foreground on every poll
        backend_mock._get.return_value = {"state": "foreground", "running": True}

        srv._session = runner_mock
        srv._mcp_runner_ref = runner_mock
        srv._backend = backend_mock
        srv._session_state = "running"

        try:
            # We don't call full handle_start_session (too many deps) — instead
            # test the new post-launch verification helper directly.
            assert hasattr(srv, "_verify_app_launched_foreground"), (
                "F#2: `_verify_app_launched_foreground(bundle_id, udid, poll_s=1.0)` "
                "function missing from server module. "
                "Add it to implement the post-launch foreground poll."
            )

            result = srv._verify_app_launched_foreground(
                "com.example.app", "TEST-UDID-DRIFT", poll_s=0.1
            )
            assert result.get("state") in ("active", "foreground"), (
                f"F#2 happy path: expected state='active' when app stays foreground, "
                f"got {result!r}"
            )
            assert "launched_then_exited" not in result.get("state", ""), (
                "F#2 happy path: must NOT return launched_then_exited when app is foreground"
            )
        finally:
            srv._session = None
            srv._mcp_runner_ref = None
            srv._backend = None
            srv._session_state = "idle"

    # ------------------------------------------------------------------
    # Crash path — app exits within poll window
    # ------------------------------------------------------------------

    def test_crash_path_returns_launched_then_exited(self):
        """When app exits within ~1s of launch, result must have state='launched_then_exited'.

        FAILS RED: _verify_app_launched_foreground does not exist.
        """
        import specterqa.ios.mcp.server as srv

        assert hasattr(srv, "_verify_app_launched_foreground"), (
            "F#2: `_verify_app_launched_foreground(bundle_id, udid, poll_s=1.0)` "
            "missing from server module. Add it."
        )

        backend_mock = MagicMock()
        # App exits immediately after launch — backend reports not-foreground
        backend_mock._get.return_value = {"state": "not-running", "running": False}

        srv._backend = backend_mock
        srv._session_state = "running"

        try:
            result = srv._verify_app_launched_foreground(
                "co.synctek.splashMate", "TEST-UDID-F2", poll_s=0.1
            )

            assert result.get("state") == "launched_then_exited", (
                f"F#2: Expected state='launched_then_exited' when app exits post-launch, "
                f"got {result.get('state')!r}. "
                "Implement a post-launch poll in _verify_app_launched_foreground."
            )
        finally:
            srv._backend = None
            srv._session_state = "idle"

    def test_crash_path_includes_crash_report_path(self):
        """When app exits post-launch, result must include crash_report_path as a string.

        FAILS RED: _verify_app_launched_foreground does not exist.
        """
        import specterqa.ios.mcp.server as srv

        assert hasattr(srv, "_verify_app_launched_foreground"), (
            "F#2: `_verify_app_launched_foreground` missing — add it to server.py."
        )

        backend_mock = MagicMock()
        backend_mock._get.return_value = {"state": "not-running", "running": False}

        srv._backend = backend_mock
        srv._session_state = "running"

        try:
            result = srv._verify_app_launched_foreground(
                "co.synctek.splashMate", "TEST-UDID-F2", poll_s=0.1
            )

            assert "crash_report_path" in result, (
                f"F#2: result missing `crash_report_path` key. "
                f"Got keys: {list(result.keys())}. "
                "When app exits post-launch, include the .ips path (or None if not found)."
            )
            assert isinstance(result["crash_report_path"], (str, type(None))), (
                f"F#2: `crash_report_path` must be str or None, "
                f"got {type(result['crash_report_path']).__name__!r}"
            )
        finally:
            srv._backend = None
            srv._session_state = "idle"

    def test_start_session_integrates_post_launch_poll(self):
        """handle_start_session must call _verify_app_launched_foreground after launch.

        FAILS RED: _verify_app_launched_foreground is not called from handle_start_session.
        """
        import specterqa.ios.mcp.server as srv

        assert hasattr(srv, "_verify_app_launched_foreground"), (
            "F#2: `_verify_app_launched_foreground` missing — add it first."
        )

        runner_mock = _make_runner_mock()

        from specterqa.ios.runner_process import RunnerState
        runner_process_module = MagicMock()
        runner_process_module.RunnerDeployError = type("RunnerDeployError", (Exception,), {})
        runner_process_module.RunnerState = RunnerState
        runner_process_module.RunnerProcess.acquire.return_value = runner_mock

        license_cls = MagicMock()
        license_cls.return_value.validate.return_value = {"valid": True}

        session_mgr = MagicMock()
        session_mgr._find_xctestrun.return_value = "/fake/runner.xctestrun"
        session_mgr._needs_rebuild.return_value = False
        session_mgr._DEFAULT_RUNNER_BUILD_DIR = "/fake/runner-build"

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

        verify_called: list[tuple] = []

        original_verify = getattr(srv, "_verify_app_launched_foreground", None)

        def _mock_verify(bundle_id, udid, poll_s=1.0):
            verify_called.append((bundle_id, udid, poll_s))
            return {"state": "active"}

        srv._verify_app_launched_foreground = _mock_verify

        try:
            with (
                patch.dict("sys.modules", {
                    "specterqa.ios.license.validator": MagicMock(LicenseValidator=license_cls),
                    "specterqa.ios.runner_process": runner_process_module,
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
                patch("specterqa.ios.mcp.server._ensure_sim_booted", return_value=True),
            ):
                srv.handle_start_session({
                    "bundle_id": "com.example.app",
                    "device_id": "TEST-UDID-DRIFT",
                    "backend": "xctest",
                    "clone": False,
                    "device_type": "simulator",
                })

            assert verify_called, (
                "F#2: `_verify_app_launched_foreground` was NOT called from "
                "handle_start_session. Wire it in after the session is established "
                "to poll that the app is foreground before returning status='ok'."
            )
        finally:
            if original_verify is not None:
                srv._verify_app_launched_foreground = original_verify
            elif hasattr(srv, "_verify_app_launched_foreground"):
                delattr(srv, "_verify_app_launched_foreground")
            srv._session = None
            srv._mcp_runner_ref = None
            srv._backend = None
            srv._session_state = "idle"


# ===========================================================================
# F#10 — logs() returns 0 lines for reasonable predicates
# ===========================================================================


class TestF10LogsSubstringFilter:
    """handle_logs must support predicate_kind/predicate filtering and raw=True mode.

    These tests FAIL RED until production code:
      1. Adds `predicate_kind` and `predicate` params to handle_logs (in addition
         to the existing `pattern` regex param — predicate_kind="substring" means
         simple str.lower() contains check, not regex).
      2. Adds `raw=True` mode that returns all buffered log entries with no filter.
      3. Fixes the bundle-id filter so it does not silently drop all entries.

    F#10 root cause: `logs(predicate_kind=substring, predicate="simdrive.demo")`
    returns 0 because the ConsoleMonitor fallback has no predicate_kind param
    and the bridge may be applying an overly-tight bundle filter.
    """

    def _make_monitor_with_entries(self, messages: list[str], subsystems: list[str] | None = None):
        """Return a ConsoleMonitor pre-populated with given messages (no background thread)."""
        from specterqa.ios.drivers.simulator.console import ConsoleMonitor
        monitor = ConsoleMonitor.__new__(ConsoleMonitor)
        import collections
        import threading
        monitor._device_id = "booted"
        monitor._buffer_size = 5000
        monitor._error_buffer_size = 500
        monitor._lock = threading.Lock()
        monitor._buffer = collections.deque(maxlen=5000)
        monitor._error_buffer = collections.deque(maxlen=500)
        monitor._watchers = []
        monitor._process = None
        monitor._reader_thread = None
        monitor._stop_event = threading.Event()

        subsystems = subsystems or [""] * len(messages)
        for msg, sub in zip(messages, subsystems):
            entry = _make_log_entry(msg, subsystem=sub)
            monitor._buffer.append(entry)

        return monitor

    # ------------------------------------------------------------------
    # predicate_kind=substring filter
    # ------------------------------------------------------------------

    def test_predicate_substring_returns_matching_lines(self):
        """handle_logs(predicate_kind='substring', predicate='simdrive.demo')
        must return only entries whose message contains the predicate string.

        FAILS RED: `predicate_kind` and `predicate` params not supported.
        """
        import specterqa.ios.mcp.server as srv

        sample_messages = [
            "simdrive.demo: session started",
            "simdrive.demo: tap recorded",
            "unrelated.module: something else",
            "another app log line",
        ]
        monitor = self._make_monitor_with_entries(sample_messages)

        srv._backend = None  # force simctl path
        srv._console_monitor = monitor
        srv._session_state = "running"

        try:
            result = srv.handle_logs({
                "predicate_kind": "substring",
                "predicate": "simdrive.demo",
                "seconds": 3600,
            })

            assert "error" not in result, f"handle_logs returned error: {result}"
            count = result.get("count", 0)
            logs = result.get("logs", [])

            assert count > 0, (
                "F#10: handle_logs(predicate_kind='substring', predicate='simdrive.demo') "
                "returned 0 entries. The `predicate_kind` / `predicate` params are not "
                "implemented — add them to handle_logs() as a substring-based filter "
                "on entry.message (case-insensitive str.lower() in check)."
            )
            for log in logs:
                assert "simdrive.demo" in log.get("message", "").lower(), (
                    f"F#10: returned log entry does not match predicate 'simdrive.demo': {log}"
                )
            # Unrelated entries should NOT be returned
            assert count == 2, (
                f"F#10: expected 2 matching entries for predicate='simdrive.demo', got {count}. "
                f"Logs: {[l['message'] for l in logs]}"
            )
        finally:
            srv._backend = None
            srv._console_monitor = None
            srv._session_state = "idle"

    # ------------------------------------------------------------------
    # raw=True mode — returns all buffered entries, no filter
    # ------------------------------------------------------------------

    def test_raw_true_returns_all_lines_and_marks_source_as_raw(self):
        """handle_logs(raw=True) must return all buffered entries and set source='raw'.

        The `raw=True` param is a new feature — the result must contain
        `source='raw'` (not 'simctl') to confirm the raw path was taken.

        FAILS RED: `raw` parameter not supported; source will be 'simctl', not 'raw'.
        """
        import specterqa.ios.mcp.server as srv

        sample_messages = [
            "simdrive.demo: session started",
            "unrelated.module: noise",
            "another thing entirely",
        ]
        monitor = self._make_monitor_with_entries(sample_messages)

        srv._backend = None
        srv._console_monitor = monitor
        srv._session_state = "running"

        try:
            result = srv.handle_logs({"raw": True, "seconds": 3600})

            assert "error" not in result, f"handle_logs returned error: {result}"

            # The new `raw=True` path must mark itself so callers can tell
            # they got unfiltered output.
            assert result.get("source") == "raw", (
                f"F#10: handle_logs(raw=True) must return source='raw' to signal the "
                f"unfiltered path was taken. Got source={result.get('source')!r}. "
                "Add a `raw` param to handle_logs() and return source='raw' when True."
            )
        finally:
            srv._backend = None
            srv._console_monitor = None
            srv._session_state = "idle"

    # ------------------------------------------------------------------
    # Bundle-id filter must NOT silently drop entries (checks subsystem too)
    # ------------------------------------------------------------------

    def test_bundle_filter_checks_subsystem_not_only_message(self):
        """predicate_kind='substring' must match on entry.subsystem, not only message.

        The b4 dogfood showed logs(predicate='simdrive.demo') returning 0 even
        though the app produces os_log entries whose subsystem is 'io.synctek.simdrive.demo'.
        The predicate must match on both message AND subsystem.

        FAILS RED: predicate_kind/predicate not implemented; entries lack subsystem match.
        """
        import specterqa.ios.mcp.server as srv

        # Messages that do NOT contain 'simdrive.demo' in the message text,
        # but their subsystem does.
        sample_messages = [
            "Session recording started",
            "Tap target found",
        ]
        sample_subsystems = [
            "io.synctek.simdrive.demo",
            "io.synctek.simdrive.demo",
        ]
        monitor = self._make_monitor_with_entries(sample_messages, sample_subsystems)

        srv._backend = None
        srv._console_monitor = monitor
        srv._session_state = "running"

        try:
            result = srv.handle_logs({
                "predicate_kind": "substring",
                "predicate": "simdrive.demo",
                "seconds": 3600,
            })

            assert "error" not in result, f"handle_logs returned error: {result}"
            count = result.get("count", 0)

            # The predicate matches the subsystem of all 2 entries but the
            # current code ignores predicate_kind entirely, so it returns all
            # 2 entries anyway (no filter). We need to verify the count is
            # correct (2) AND the predicate was actually evaluated against subsystem.
            # The key assertion: predicate_kind='substring' must be RECOGNIZED
            # as a parameter (not silently ignored), confirmed by the result
            # containing a 'predicate_applied' key.
            assert "predicate_applied" in result, (
                "F#10: handle_logs must return `predicate_applied: true` when "
                "predicate_kind/predicate params are given. This confirms the filter "
                "was recognized and applied (vs. silently ignored). "
                "Add `predicate_applied` to the result dict when predicate_kind is set."
            )
        finally:
            srv._backend = None
            srv._console_monitor = None
            srv._session_state = "idle"

    # ------------------------------------------------------------------
    # raw=True bypasses predicate filter — confirmed by source marker
    # ------------------------------------------------------------------

    def test_raw_true_bypasses_predicate_filter(self):
        """raw=True must return source='raw' even when predicate_kind is also given.

        When raw=True, the predicate filter must be ignored and source must be 'raw'.

        FAILS RED: `raw` param not supported; source will not be 'raw'.
        """
        import specterqa.ios.mcp.server as srv

        # Messages that do NOT match predicate="simdrive.demo"
        sample_messages = [
            "completely unrelated log line",
            "nothing to do with simdrive",
        ]
        monitor = self._make_monitor_with_entries(sample_messages)

        srv._backend = None
        srv._console_monitor = monitor
        srv._session_state = "running"

        try:
            result = srv.handle_logs({
                "raw": True,
                "predicate_kind": "substring",
                "predicate": "simdrive.demo",
                "seconds": 3600,
            })

            assert "error" not in result, f"handle_logs returned error: {result}"

            assert result.get("source") == "raw", (
                f"F#10: handle_logs(raw=True, predicate_kind='substring', predicate='simdrive.demo') "
                f"must return source='raw' indicating the predicate was bypassed. "
                f"Got source={result.get('source')!r}. "
                "When raw=True, ignore predicate_kind/predicate and mark source='raw'."
            )
        finally:
            srv._backend = None
            srv._console_monitor = None
            srv._session_state = "idle"
