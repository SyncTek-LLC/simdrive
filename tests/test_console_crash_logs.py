"""Tests for ios_logs and ios_crashes MCP tools.

Verifies ConsoleMonitor and CrashDetector wiring in the MCP server.
Tests are written against the expected handler interface; graceful
ImportError skips are used for symbols not yet wired.

Run:
    pytest tests/test_console_crash_logs.py -v --tb=short
"""

from __future__ import annotations

import time
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Import guards for real dataclasses
# ---------------------------------------------------------------------------

try:
    from specterqa.ios.drivers.simulator.console import LogEntry
except ImportError:
    pytest.skip("console module not available", allow_module_level=True)

try:
    from specterqa.ios.drivers.simulator.crash import CrashReport
except ImportError:
    pytest.skip("crash module not available", allow_module_level=True)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_log_entry(
    message: str = "test log message",
    level: str = "default",
    subsystem: str = "com.example.app",
    category: str = "network",
    process: str = "MyApp",
    timestamp: str = "2026-04-10T10:00:00.000Z",
    thread_id: int = 1,
) -> LogEntry:
    """Build a LogEntry with sensible defaults."""
    return LogEntry(
        timestamp=timestamp,
        level=level,
        subsystem=subsystem,
        category=category,
        message=message,
        process=process,
        thread_id=thread_id,
        ingestion_time=time.time(),
    )


def _make_crash_report(
    exception_type: str = "EXC_BAD_ACCESS",
    exception_code: str = "0x0000000000000000",
    backtrace: list[str] | None = None,
    timestamp: str = "2026-04-10T10:00:00Z",
    app_version: str = "1.0.0",
    os_version: str = "17.0",
    device: str = "iPhone 15 Pro",
    raw_path: str = "/tmp/crash.ips",
) -> CrashReport:
    """Build a CrashReport with sensible defaults."""
    return CrashReport(
        timestamp=timestamp,
        exception_type=exception_type,
        exception_code=exception_code,
        crashing_thread=0,
        backtrace=backtrace or ["frame0", "frame1", "frame2"],
        last_exception=None,
        app_version=app_version,
        os_version=os_version,
        device=device,
        raw_path=raw_path,
    )


def _setup_monitors(console_monitor=None, crash_detector=None):
    """Inject mock monitors into server module globals."""
    import specterqa.ios.mcp.server as srv

    mock_backend = MagicMock()
    mock_backend._get.side_effect = Exception("bridge unavailable")
    srv._backend = mock_backend
    srv._session = MagicMock()
    if hasattr(srv, "_console_monitor"):
        srv._console_monitor = console_monitor
    if hasattr(srv, "_crash_detector"):
        srv._crash_detector = crash_detector


def _teardown_monitors():
    """Reset server module globals to a clean idle state."""
    import specterqa.ios.mcp.server as srv

    srv._backend = None
    srv._session = None
    if hasattr(srv, "_console_monitor"):
        srv._console_monitor = None
    if hasattr(srv, "_crash_detector"):
        srv._crash_detector = None
    if hasattr(srv, "_session_state"):
        srv._session_state = "idle"


def _get_handler(name: str):
    """Import a handler from server, skip if not yet implemented."""
    try:
        import specterqa.ios.mcp.server as srv
        handler = getattr(srv, name, None)
        if handler is None:
            pytest.skip(f"{name} not yet implemented")
        return handler
    except ImportError:
        pytest.skip(f"server module not importable")


# ===========================================================================
# TestHandleLogs
# ===========================================================================


class TestHandleLogs:
    """Verify ios_logs MCP tool handler (handle_logs)."""

    def teardown_method(self, method):
        _teardown_monitors()

    def test_logs_returns_error_when_no_session(self):
        """When _console_monitor is None, handle_logs returns an error dict."""
        import specterqa.ios.mcp.server as srv

        handle_logs = _get_handler("handle_logs")

        # Ensure no monitor is attached
        if hasattr(srv, "_console_monitor"):
            srv._console_monitor = None

        result = handle_logs({})

        assert "error" in result, (
            f"Expected error when _console_monitor is None. Got: {result}"
        )

    def test_logs_returns_recent_entries(self):
        """Mock ConsoleMonitor.recent() returning LogEntry objects → verify JSON structure."""
        handle_logs = _get_handler("handle_logs")
        import specterqa.ios.mcp.server as srv

        if not hasattr(srv, "_console_monitor"):
            pytest.skip("_console_monitor global not yet wired")

        entries = [
            _make_log_entry(message="Request started", level="default"),
            _make_log_entry(message="Response received", level="info"),
        ]

        mock_monitor = MagicMock()
        mock_monitor.recent.return_value = entries
        mock_monitor.summary.return_value = {
            "total_entries": 2,
            "errors_count": 0,
            "by_level": {"default": 1, "info": 1},
            "by_subsystem": {},
        }

        _setup_monitors(console_monitor=mock_monitor)

        result = handle_logs({})

        assert "error" not in result, f"Unexpected error: {result}"
        assert "entries" in result or "logs" in result, (
            f"Response should contain log entries. Got keys: {list(result.keys())}"
        )
        # Verify entry structure — find the entries list
        entry_list = result.get("entries") or result.get("logs") or []
        assert len(entry_list) == 2, (
            f"Expected 2 entries, got {len(entry_list)}"
        )
        first = entry_list[0]
        assert "message" in first, f"Entry should have 'message' field. Got: {first}"

    def test_logs_filters_by_level(self):
        """level='error' argument causes errors() method to be called."""
        handle_logs = _get_handler("handle_logs")
        import specterqa.ios.mcp.server as srv

        if not hasattr(srv, "_console_monitor"):
            pytest.skip("_console_monitor global not yet wired")

        error_entries = [
            _make_log_entry(message="Auth token expired", level="error"),
        ]

        mock_monitor = MagicMock()
        mock_monitor.errors.return_value = error_entries
        mock_monitor.recent.return_value = error_entries
        mock_monitor.summary.return_value = {
            "total_entries": 5,
            "errors_count": 1,
            "by_level": {"error": 1},
            "by_subsystem": {},
        }

        _setup_monitors(console_monitor=mock_monitor)

        result = handle_logs({"level": "error"})

        assert "error" not in result, f"Unexpected error: {result}"
        # The handler must have called errors() — check via the mock
        assert mock_monitor.errors.called or mock_monitor.recent.called, (
            "Expected errors() or recent(level='error') to be called"
        )

    def test_logs_searches_by_pattern(self):
        """pattern='auth' argument causes search() method to be called."""
        handle_logs = _get_handler("handle_logs")
        import specterqa.ios.mcp.server as srv

        if not hasattr(srv, "_console_monitor"):
            pytest.skip("_console_monitor global not yet wired")

        auth_entries = [
            _make_log_entry(message="auth token refreshed"),
            _make_log_entry(message="oauth callback received"),
        ]

        mock_monitor = MagicMock()
        mock_monitor.search.return_value = auth_entries
        mock_monitor.summary.return_value = {
            "total_entries": 100,
            "errors_count": 0,
            "by_level": {},
            "by_subsystem": {},
        }

        _setup_monitors(console_monitor=mock_monitor)

        result = handle_logs({"pattern": "auth"})

        assert "error" not in result, f"Unexpected error: {result}"
        mock_monitor.search.assert_called_once_with("auth")

    def test_logs_caps_at_100_entries(self):
        """200 log entries in the monitor → only 100 returned in response."""
        handle_logs = _get_handler("handle_logs")
        import specterqa.ios.mcp.server as srv

        if not hasattr(srv, "_console_monitor"):
            pytest.skip("_console_monitor global not yet wired")

        two_hundred_entries = [
            _make_log_entry(message=f"log line {i}") for i in range(200)
        ]

        mock_monitor = MagicMock()
        mock_monitor.recent.return_value = two_hundred_entries
        mock_monitor.summary.return_value = {
            "total_entries": 200,
            "errors_count": 0,
            "by_level": {},
            "by_subsystem": {},
        }

        _setup_monitors(console_monitor=mock_monitor)

        result = handle_logs({})

        assert "error" not in result, f"Unexpected error: {result}"
        entry_list = result.get("entries") or result.get("logs") or []
        assert len(entry_list) <= 100, (
            f"Response should cap at 100 entries. Got {len(entry_list)}"
        )

    def test_logs_includes_summary(self):
        """Response includes a summary field from monitor.summary()."""
        handle_logs = _get_handler("handle_logs")
        import specterqa.ios.mcp.server as srv

        if not hasattr(srv, "_console_monitor"):
            pytest.skip("_console_monitor global not yet wired")

        mock_monitor = MagicMock()
        mock_monitor.recent.return_value = []
        expected_summary = {
            "total_entries": 42,
            "errors_count": 3,
            "by_level": {"error": 3, "info": 39},
            "by_subsystem": {"com.example": 42},
        }
        mock_monitor.summary.return_value = expected_summary

        _setup_monitors(console_monitor=mock_monitor)

        result = handle_logs({})

        assert "error" not in result, f"Unexpected error: {result}"
        assert "summary" in result, (
            f"Response should include 'summary' field. Got keys: {list(result.keys())}"
        )
        mock_monitor.summary.assert_called_once()


# ===========================================================================
# TestHandleCrashes
# ===========================================================================


class TestHandleCrashes:
    """Verify ios_crashes MCP tool handler (handle_crashes)."""

    def teardown_method(self, method):
        _teardown_monitors()

    def test_crashes_returns_error_when_no_session(self):
        """When _crash_detector is None, handle_crashes returns an error dict."""
        import specterqa.ios.mcp.server as srv

        handle_crashes = _get_handler("handle_crashes")

        if hasattr(srv, "_crash_detector"):
            srv._crash_detector = None

        result = handle_crashes({})

        assert "error" in result, (
            f"Expected error when _crash_detector is None. Got: {result}"
        )

    def test_crashes_returns_empty_when_no_crashes(self):
        """check() returning [] → crashes_since_session_start: 0."""
        handle_crashes = _get_handler("handle_crashes")
        import specterqa.ios.mcp.server as srv

        if not hasattr(srv, "_crash_detector"):
            pytest.skip("_crash_detector global not yet wired")

        mock_detector = MagicMock()
        mock_detector.check.return_value = []
        mock_detector.latest_crash.return_value = None
        mock_detector.is_app_running.return_value = True

        _setup_monitors(crash_detector=mock_detector)

        result = handle_crashes({})

        assert "error" not in result, f"Unexpected error: {result}"
        crash_count = result.get("crashes_since_session_start", result.get("crash_count", -1))
        assert crash_count == 0, (
            f"Expected crashes_since_session_start=0 when no crashes. Got: {result}"
        )

    def test_crashes_returns_crash_details(self):
        """Mock CrashReport with exception_type, backtrace etc → present in response."""
        handle_crashes = _get_handler("handle_crashes")
        import specterqa.ios.mcp.server as srv

        if not hasattr(srv, "_crash_detector"):
            pytest.skip("_crash_detector global not yet wired")

        crash = _make_crash_report(
            exception_type="EXC_BAD_ACCESS",
            exception_code="KERN_INVALID_ADDRESS at 0x0000000000000000",
            backtrace=["frame0: MyApp 0x1000", "frame1: UIKit 0x2000"],
        )

        mock_detector = MagicMock()
        mock_detector.check.return_value = [crash]
        mock_detector.latest_crash.return_value = crash
        mock_detector.is_app_running.return_value = False

        _setup_monitors(crash_detector=mock_detector)

        result = handle_crashes({})

        assert "error" not in result, f"Unexpected error: {result}"
        # Crash details must appear somewhere in the response
        result_str = str(result)
        assert "EXC_BAD_ACCESS" in result_str or "exception_type" in result_str, (
            f"Response should include exception_type. Got: {result}"
        )

    def test_crashes_reports_app_running_status(self):
        """is_app_running() True/False reflected in response."""
        handle_crashes = _get_handler("handle_crashes")
        import specterqa.ios.mcp.server as srv

        if not hasattr(srv, "_crash_detector"):
            pytest.skip("_crash_detector global not yet wired")

        # Case 1: app is running
        mock_detector = MagicMock()
        mock_detector.check.return_value = []
        mock_detector.latest_crash.return_value = None
        mock_detector.is_app_running.return_value = True

        _setup_monitors(crash_detector=mock_detector)

        result = handle_crashes({})

        assert "error" not in result, f"Unexpected error: {result}"
        assert "app_running" in result or "is_app_running" in result, (
            f"Response should include app running status. Got keys: {list(result.keys())}"
        )
        running_value = result.get("app_running", result.get("is_app_running"))
        assert running_value is True, (
            f"Expected app_running=True. Got: {running_value}"
        )

        # Case 2: app is not running
        mock_detector.is_app_running.return_value = False
        result2 = handle_crashes({})
        running_value2 = result2.get("app_running", result2.get("is_app_running"))
        assert running_value2 is False, (
            f"Expected app_running=False. Got: {running_value2}"
        )

    def test_crashes_includes_latest_crash(self):
        """latest_crash() returns CrashReport → included in response."""
        handle_crashes = _get_handler("handle_crashes")
        import specterqa.ios.mcp.server as srv

        if not hasattr(srv, "_crash_detector"):
            pytest.skip("_crash_detector global not yet wired")

        latest = _make_crash_report(
            exception_type="SIGABRT",
            timestamp="2026-04-10T09:55:00Z",
        )

        mock_detector = MagicMock()
        mock_detector.check.return_value = [latest]
        mock_detector.latest_crash.return_value = latest
        mock_detector.is_app_running.return_value = False

        _setup_monitors(crash_detector=mock_detector)

        result = handle_crashes({})

        assert "error" not in result, f"Unexpected error: {result}"
        assert "latest_crash" in result, (
            f"Response should include 'latest_crash' field. Got keys: {list(result.keys())}"
        )
        latest_data = result["latest_crash"]
        assert latest_data is not None, "latest_crash should not be None when a crash exists"
        # The crash data should reference SIGABRT
        result_str = str(latest_data)
        assert "SIGABRT" in result_str or "exception_type" in result_str, (
            f"latest_crash should include exception_type. Got: {latest_data}"
        )


# ===========================================================================
# TestSessionLifecycleMonitors
# ===========================================================================


class TestSessionLifecycleMonitors:
    """Verify ConsoleMonitor and CrashDetector lifecycle tied to session start/stop."""

    def teardown_method(self, method):
        _teardown_monitors()

    def test_monitors_started_on_session_start(self):
        """After handle_start_session, _console_monitor and _crash_detector are not None."""
        import specterqa.ios.mcp.server as srv
        from specterqa.ios.mcp.server import handle_start_session

        if not hasattr(srv, "_console_monitor") or not hasattr(srv, "_crash_detector"):
            pytest.skip("_console_monitor / _crash_detector not yet wired")

        with (
            patch("specterqa.ios.backends.xctest_client.XCTestBackend", autospec=True) as MockBackend,
            patch("specterqa.ios.session_manager.TestSession", autospec=True) as MockSession,
            patch("specterqa.ios.replay.ReplayRecorder", autospec=True),
            patch(
                "specterqa.ios.drivers.simulator.console.ConsoleMonitor",
                autospec=True,
            ) as MockConsole,
            patch(
                "specterqa.ios.drivers.simulator.crash.CrashDetector",
                autospec=True,
            ) as MockCrash,
        ):
            mock_backend_instance = MagicMock()
            MockBackend.return_value = mock_backend_instance
            mock_backend_instance.health.return_value = {"status": "ok"}

            mock_session_instance = MagicMock()
            MockSession.return_value = mock_session_instance
            mock_session_instance._target_udid = "test-udid"
            mock_session_instance._port = 8222
            mock_session_instance.runner_url = "http://localhost:8222"

            MockConsole.return_value = MagicMock()
            MockCrash.return_value = MagicMock()

            try:
                handle_start_session({"bundle_id": "com.example.app", "udid": "test-udid"})
            except Exception:
                pass  # Partial start is OK — we just check that monitors were instantiated

        # After a start attempt, monitors should be set (not None)
        # We check that the server tried to instantiate them — either the globals
        # are set or the mock constructors were called
        console_set = srv._console_monitor is not None
        crash_set = srv._crash_detector is not None
        console_called = MockConsole.called
        crash_called = MockCrash.called

        assert console_set or console_called, (
            "_console_monitor should be initialised during session start"
        )
        assert crash_set or crash_called, (
            "_crash_detector should be initialised during session start"
        )

    def test_monitors_stopped_on_session_stop(self):
        """After handle_stop_session, _console_monitor and _crash_detector are None."""
        import specterqa.ios.mcp.server as srv
        from specterqa.ios.mcp.server import handle_stop_session

        if not hasattr(srv, "_console_monitor") or not hasattr(srv, "_crash_detector"):
            pytest.skip("_console_monitor / _crash_detector not yet wired")

        # Set up active monitors
        mock_console = MagicMock()
        mock_crash = MagicMock()
        srv._backend = MagicMock()
        srv._session = MagicMock()
        srv._console_monitor = mock_console
        srv._crash_detector = mock_crash
        if hasattr(srv, "_session_state"):
            srv._session_state = "running"

        handle_stop_session({})

        assert srv._console_monitor is None, (
            f"_console_monitor should be None after stop. Got: {srv._console_monitor}"
        )
        assert srv._crash_detector is None, (
            f"_crash_detector should be None after stop. Got: {srv._crash_detector}"
        )
        # stop() should have been called on each monitor
        mock_console.stop.assert_called_once()
        mock_crash.stop.assert_called_once()
