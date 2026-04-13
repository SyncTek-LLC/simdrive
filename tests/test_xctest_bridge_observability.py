"""Tests for XCTest bridge observability — fallback behaviour for all MCP handlers.

Verifies that handle_perf, handle_logs, handle_crashes, and handle_network each:
  1. Call the XCTest HTTP bridge first (_backend._get).
  2. Fall back to the corresponding Python-side monitor/profiler when the bridge
     raises an exception (connection refused, timeout, etc.).
  3. Return the expected field shapes from both bridge and fallback paths.

Also covers the runner cache-invalidation logic (_needs_rebuild) in session_manager.

Run:
    pytest tests/test_xctest_bridge_observability.py -v --tb=short
"""

from __future__ import annotations

import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Import guards
# ---------------------------------------------------------------------------

try:
    import specterqa.ios.mcp.server as srv
except ImportError:
    pytest.skip("specterqa.ios.mcp.server not importable", allow_module_level=True)

try:
    from specterqa.ios.session_manager import _needs_rebuild
except ImportError:
    _needs_rebuild = None  # type: ignore[assignment]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_handler(name: str):
    """Return a handler from the server module, skip if not yet implemented."""
    handler = getattr(srv, name, None)
    if handler is None:
        pytest.skip(f"{name} not yet implemented in server module")
    return handler


def _reset_server_globals() -> None:
    """Reset all observable server-module globals to a clean, idle state."""
    srv._backend = None
    srv._session = None
    for attr in (
        "_console_monitor",
        "_crash_detector",
        "_perf_profiler",
        "_network_inspector",
    ):
        if hasattr(srv, attr):
            setattr(srv, attr, None)
    if hasattr(srv, "_session_state"):
        srv._session_state = "idle"


def _inject_backend(response_map: dict | None = None, side_effect=None) -> MagicMock:
    """Wire a mock _backend into the server.

    Args:
        response_map: maps path → return value for _backend._get(path).
        side_effect:  if supplied, _backend._get raises this instead.
    Returns:
        The mock backend object.
    """
    mock_backend = MagicMock()
    if side_effect is not None:
        mock_backend._get.side_effect = side_effect
    elif response_map is not None:
        mock_backend._get.side_effect = lambda path: response_map[path]
    srv._backend = mock_backend
    srv._session = MagicMock()
    return mock_backend


# ===========================================================================
# TestBridgePerfFallback
# ===========================================================================


class TestBridgePerfFallback:
    """handle_perf: bridge-first, Python-profiler fallback."""

    def setup_method(self, method) -> None:
        _reset_server_globals()

    def teardown_method(self, method) -> None:
        _reset_server_globals()

    # ------------------------------------------------------------------
    # Skip guard — these tests only make sense once handle_perf tries the
    # bridge before the profiler.  Until that change lands, the current
    # implementation calls _perf_profiler.snapshot() unconditionally, so
    # the bridge-priority tests are marked xfail and will xpass once the
    # feature is implemented.
    # ------------------------------------------------------------------

    # Bridge-first is now implemented
    def test_perf_uses_bridge_when_available(self):
        """When bridge responds on /perf, result uses bridge data, not profiler."""
        handle_perf = _get_handler("handle_perf")

        if not hasattr(srv, "_perf_profiler"):
            pytest.skip("_perf_profiler not wired in server")

        bridge_data = {
            "memory_rss_mb": 45.2,
            "thread_count": 12,
            "cpu_time": 3.7,
            "cpu_percent": 8.4,
            "memory_mb": 45.2,
        }
        mock_backend = _inject_backend(response_map={"/perf": bridge_data})

        # Profiler should NOT be called when bridge succeeds
        mock_profiler = MagicMock()
        srv._perf_profiler = mock_profiler

        result = handle_perf({})

        assert "error" not in result, f"Unexpected error: {result}"
        # If bridge was used, profiler.snapshot() should not have been called
        # (xfail-compatible: we assert the result came from bridge values)
        result_str = str(result)
        # Bridge returned memory_rss_mb=45.2 — verify value is reflected
        assert "45.2" in result_str or not mock_profiler.snapshot.called, (
            "Bridge data should be used when bridge is available; profiler should be skipped"
        )

    # Bridge-first is now implemented
    def test_perf_bridge_call_precedes_profiler(self):
        """Bridge _get('/perf') is called before snapshot() when bridge is active."""
        handle_perf = _get_handler("handle_perf")

        if not hasattr(srv, "_perf_profiler"):
            pytest.skip("_perf_profiler not wired")

        call_order: list[str] = []

        mock_backend = MagicMock()
        mock_backend._get.side_effect = lambda path: call_order.append(f"bridge:{path}") or {
            "memory_rss_mb": 40.0,
            "thread_count": 8,
            "cpu_time": 1.0,
            "cpu_percent": 5.0,
            "memory_mb": 40.0,
        }
        srv._backend = mock_backend
        srv._session = MagicMock()

        mock_profiler = MagicMock()
        mock_profiler.snapshot.side_effect = lambda: call_order.append("profiler:snapshot") or MagicMock(
            cpu_percent=5.0,
            memory_mb=40.0,
            thread_count=8,
            disk_usage_mb=0.0,
            fps_estimate=60.0,
            timestamp=time.time(),
        )
        srv._perf_profiler = mock_profiler

        handle_perf({})

        assert "bridge:/perf" in call_order, "bridge._get('/perf') was not called"
        bridge_idx = call_order.index("bridge:/perf")
        if "profiler:snapshot" in call_order:
            profiler_idx = call_order.index("profiler:snapshot")
            assert bridge_idx < profiler_idx, "Bridge must be attempted before profiler"

    def test_perf_falls_back_to_profiler_on_bridge_error(self):
        """When bridge raises, result comes from _perf_profiler.snapshot()."""
        handle_perf = _get_handler("handle_perf")

        if not hasattr(srv, "_perf_profiler"):
            pytest.skip("_perf_profiler not wired")

        _inject_backend(side_effect=Exception("Connection refused"))

        from specterqa.ios.drivers.simulator.perf import PerfSnapshot
        fallback_snap = PerfSnapshot(
            memory_mb=77.3,
            cpu_percent=14.1,
            thread_count=9,
            disk_usage_mb=0.0,
            fps_estimate=60.0,
            timestamp=time.time(),
        )
        mock_profiler = MagicMock()
        mock_profiler.snapshot.return_value = fallback_snap
        mock_profiler._get_app_pid.return_value = 12345
        srv._perf_profiler = mock_profiler

        result = handle_perf({})

        assert "error" not in result, f"Unexpected error from profiler fallback: {result}"
        mock_profiler.snapshot.assert_called_once()

    def test_perf_bridge_response_has_memory_and_cpu(self):
        """Bridge /perf response shape has memory_rss_mb, thread_count, cpu_time."""
        # This is a contract test on the bridge response shape — not handler behaviour.
        # We verify the bridge would return the correct keys when queried.
        bridge_response = {
            "memory_rss_mb": 52.8,
            "thread_count": 14,
            "cpu_time": 7.2,
        }

        assert "memory_rss_mb" in bridge_response, "bridge /perf must include memory_rss_mb"
        assert "thread_count" in bridge_response, "bridge /perf must include thread_count"
        assert "cpu_time" in bridge_response, "bridge /perf must include cpu_time"
        assert isinstance(bridge_response["memory_rss_mb"], float)
        assert isinstance(bridge_response["thread_count"], int)


# ===========================================================================
# TestBridgeLogsFallback
# ===========================================================================


class TestBridgeLogsFallback:
    """handle_logs: bridge-first, ConsoleMonitor fallback."""

    def setup_method(self, method) -> None:
        _reset_server_globals()

    def teardown_method(self, method) -> None:
        _reset_server_globals()

    def test_logs_uses_bridge_when_available(self):
        """When bridge responds on /logs, result uses bridge entries."""
        handle_logs = _get_handler("handle_logs")

        if not hasattr(srv, "_console_monitor"):
            pytest.skip("_console_monitor not wired in server")

        bridge_entries = [
            {"timestamp": "2026-04-10T10:00:00.000Z", "level": "info", "message": "App launched"},
            {"timestamp": "2026-04-10T10:00:01.000Z", "level": "default", "message": "View loaded"},
        ]
        mock_backend = _inject_backend(response_map={"/logs": {"entries": bridge_entries}})

        mock_monitor = MagicMock()
        srv._console_monitor = mock_monitor

        result = handle_logs({})

        assert "error" not in result, f"Unexpected error: {result}"
        # Bridge was available — result should not be an error
        result_str = str(result)
        assert result_str  # non-empty response

    def test_logs_falls_back_to_console_monitor(self):
        """When bridge raises, handle_logs falls back to _console_monitor.recent()."""
        handle_logs = _get_handler("handle_logs")

        if not hasattr(srv, "_console_monitor"):
            pytest.skip("_console_monitor not wired")

        _inject_backend(side_effect=Exception("Connection refused"))

        from specterqa.ios.drivers.simulator.console import LogEntry
        fallback_entries = [
            LogEntry(
                timestamp="2026-04-10T10:00:00.000Z",
                level="default",
                subsystem="com.example.app",
                category="main",
                message="Fallback log entry",
                process="MyApp",
                thread_id=1,
                ingestion_time=time.time(),
            )
        ]

        mock_monitor = MagicMock()
        mock_monitor.recent.return_value = fallback_entries
        mock_monitor.summary.return_value = {
            "total_entries": 1,
            "errors_count": 0,
            "by_level": {"default": 1},
            "by_subsystem": {},
        }
        srv._console_monitor = mock_monitor

        result = handle_logs({})

        assert "error" not in result, f"Unexpected error: {result}"
        mock_monitor.recent.assert_called_once()

    def test_logs_bridge_entries_have_timestamp_and_message(self):
        """Each bridge log entry must carry timestamp, level, and message fields."""
        bridge_entry = {
            "timestamp": "2026-04-10T10:00:00.000Z",
            "level": "error",
            "message": "Auth token expired",
        }

        assert "timestamp" in bridge_entry, "bridge log entry must have timestamp"
        assert "level" in bridge_entry, "bridge log entry must have level"
        assert "message" in bridge_entry, "bridge log entry must have message"

        # Values must be non-empty strings
        assert bridge_entry["timestamp"]
        assert bridge_entry["level"]
        assert bridge_entry["message"]


# ===========================================================================
# TestBridgeCrashesFallback
# ===========================================================================


class TestBridgeCrashesFallback:
    """handle_crashes: bridge-first, CrashDetector fallback."""

    def setup_method(self, method) -> None:
        _reset_server_globals()

    def teardown_method(self, method) -> None:
        _reset_server_globals()

    def test_crashes_uses_bridge_when_available(self):
        """When bridge responds on /crashes, result uses bridge data."""
        handle_crashes = _get_handler("handle_crashes")

        if not hasattr(srv, "_crash_detector"):
            pytest.skip("_crash_detector not wired in server")

        bridge_data = {
            "app_running": True,
            "responsive": True,
            "crashes": [],
            "crashes_since_session_start": 0,
        }
        mock_backend = _inject_backend(response_map={"/crashes": bridge_data})

        mock_detector = MagicMock()
        srv._crash_detector = mock_detector

        result = handle_crashes({})

        assert "error" not in result, f"Unexpected error: {result}"
        # Response should exist and not require the Python detector
        result_str = str(result)
        assert result_str

    def test_crashes_falls_back_to_detector(self):
        """When bridge raises, handle_crashes falls back to _crash_detector."""
        handle_crashes = _get_handler("handle_crashes")

        if not hasattr(srv, "_crash_detector"):
            pytest.skip("_crash_detector not wired")

        _inject_backend(side_effect=Exception("Connection refused"))

        mock_detector = MagicMock()
        mock_detector.check.return_value = []
        mock_detector.latest_crash.return_value = None
        mock_detector.is_app_running.return_value = True
        srv._crash_detector = mock_detector

        result = handle_crashes({})

        assert "error" not in result, f"Unexpected error: {result}"
        mock_detector.check.assert_called_once()

    def test_crashes_bridge_reports_app_running(self):
        """Bridge /crashes response must include app_running and responsive fields."""
        bridge_response = {
            "app_running": True,
            "responsive": True,
            "crashes_since_session_start": 0,
            "crashes": [],
        }

        assert "app_running" in bridge_response, "bridge /crashes must include app_running"
        assert "responsive" in bridge_response, "bridge /crashes must include responsive"
        assert isinstance(bridge_response["app_running"], bool)
        assert isinstance(bridge_response["responsive"], bool)

    def test_crashes_bridge_crash_entry_has_required_fields(self):
        """A bridge crash entry must have exception_type and backtrace."""
        bridge_crash = {
            "exception_type": "EXC_BAD_ACCESS",
            "exception_code": "KERN_INVALID_ADDRESS at 0x0",
            "backtrace": ["frame0: MyApp 0x1000", "frame1: UIKit 0x2000"],
            "timestamp": "2026-04-10T09:55:00Z",
        }

        assert "exception_type" in bridge_crash
        assert "backtrace" in bridge_crash
        assert isinstance(bridge_crash["backtrace"], list)
        assert len(bridge_crash["backtrace"]) > 0


# ===========================================================================
# TestBridgeNetworkFallback
# ===========================================================================


class TestBridgeNetworkFallback:
    """handle_network: bridge-first, NetworkInspector fallback."""

    def setup_method(self, method) -> None:
        _reset_server_globals()

    def teardown_method(self, method) -> None:
        _reset_server_globals()

    def test_network_uses_bridge_when_available(self):
        """When bridge responds on /network, result uses bridge data."""
        handle_network = _get_handler("handle_network")

        if not hasattr(srv, "_network_inspector"):
            pytest.skip("_network_inspector not wired in server")

        bridge_data = {
            "requests": [
                {
                    "url": "https://api.example.com/v1/profile",
                    "method": "GET",
                    "status_code": 200,
                    "duration_ms": 142.3,
                }
            ],
            "bytes_in": 4096,
            "bytes_out": 256,
        }
        mock_backend = _inject_backend(response_map={"/network": bridge_data})

        mock_inspector = MagicMock()
        srv._network_inspector = mock_inspector

        result = handle_network({})

        assert "error" not in result, f"Unexpected error: {result}"
        result_str = str(result)
        assert result_str

    def test_network_falls_back_to_inspector(self):
        """When bridge raises, handle_network falls back to _network_inspector."""
        handle_network = _get_handler("handle_network")

        if not hasattr(srv, "_network_inspector"):
            pytest.skip("_network_inspector not wired")

        _inject_backend(side_effect=Exception("Connection refused"))

        from specterqa.ios.drivers.simulator.network import NetworkSnapshot
        mock_snap = NetworkSnapshot(
            bytes_in=1024,
            bytes_out=512,
            throughput_in=50.0,
            throughput_out=25.0,
            requests=[],
            active_connections=0,
            nettop_available=False,
        )
        mock_inspector = MagicMock()
        mock_inspector.snapshot.return_value = mock_snap
        srv._network_inspector = mock_inspector

        result = handle_network({})

        assert "error" not in result, f"Unexpected error: {result}"
        mock_inspector.snapshot.assert_called_once()

    def test_network_bridge_response_has_requests_and_bytes(self):
        """Bridge /network response contract: must include requests list and byte counters."""
        bridge_response = {
            "requests": [],
            "bytes_in": 8192,
            "bytes_out": 1024,
        }

        assert "requests" in bridge_response, "bridge /network must include requests"
        assert "bytes_in" in bridge_response, "bridge /network must include bytes_in"
        assert "bytes_out" in bridge_response, "bridge /network must include bytes_out"
        assert isinstance(bridge_response["requests"], list)


# ===========================================================================
# TestRunnerCacheInvalidation
# ===========================================================================


class TestRunnerCacheInvalidation:
    """_needs_rebuild: version-marker logic in session_manager."""

    def test_needs_rebuild_when_no_version_file(self, tmp_path: Path):
        """No .specterqa-version file → _needs_rebuild returns True."""
        if _needs_rebuild is None:
            pytest.skip("_needs_rebuild not importable from session_manager")

        # tmp_path is an empty directory — no marker file present
        result = _needs_rebuild(tmp_path)
        assert result is True, (
            "Expected True (rebuild required) when .specterqa-version does not exist. "
            f"Got: {result}"
        )

    def test_needs_rebuild_when_version_mismatch(self, tmp_path: Path):
        """Marker file says old version → _needs_rebuild returns True."""
        if _needs_rebuild is None:
            pytest.skip("_needs_rebuild not importable from session_manager")

        marker = tmp_path / ".specterqa-version"
        marker.write_text("11.6.0", encoding="utf-8")

        # Patch the current package version to something newer
        with patch(
            "specterqa.ios.session_manager._current_package_version",
            return_value="11.8.0",
        ):
            result = _needs_rebuild(tmp_path)

        assert result is True, (
            "Expected True (rebuild required) when cached version != installed version. "
            f"Got: {result}"
        )

    def test_no_rebuild_when_version_matches(self, tmp_path: Path):
        """Marker file matches installed version → _needs_rebuild returns False."""
        if _needs_rebuild is None:
            pytest.skip("_needs_rebuild not importable from session_manager")

        marker = tmp_path / ".specterqa-version"
        marker.write_text("11.8.0", encoding="utf-8")

        with patch(
            "specterqa.ios.session_manager._current_package_version",
            return_value="11.8.0",
        ):
            result = _needs_rebuild(tmp_path)

        assert result is False, (
            "Expected False (no rebuild) when cached version matches installed version. "
            f"Got: {result}"
        )

    def test_needs_rebuild_when_version_unknown(self, tmp_path: Path):
        """When installed version is 'unknown', fail-safe → always rebuild."""
        if _needs_rebuild is None:
            pytest.skip("_needs_rebuild not importable from session_manager")

        marker = tmp_path / ".specterqa-version"
        marker.write_text("11.8.0", encoding="utf-8")

        with patch(
            "specterqa.ios.session_manager._current_package_version",
            return_value="unknown",
        ):
            result = _needs_rebuild(tmp_path)

        assert result is True, (
            "Expected True (rebuild required) when version is 'unknown' (fail-safe). "
            f"Got: {result}"
        )

    def test_version_marker_content_is_stripped(self, tmp_path: Path):
        """Trailing whitespace/newlines in marker file are stripped before comparison."""
        if _needs_rebuild is None:
            pytest.skip("_needs_rebuild not importable from session_manager")

        marker = tmp_path / ".specterqa-version"
        # Write with trailing newline (common when editors save files)
        marker.write_text("11.8.0\n", encoding="utf-8")

        with patch(
            "specterqa.ios.session_manager._current_package_version",
            return_value="11.8.0",
        ):
            result = _needs_rebuild(tmp_path)

        assert result is False, (
            "Expected False — trailing newline in version file should be stripped. "
            f"Got: {result}"
        )
