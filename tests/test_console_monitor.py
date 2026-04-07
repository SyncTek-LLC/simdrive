"""Tests for M4: ConsoleMonitor — iOS Simulator log stream monitoring.

TDD Phase — INIT-2026-492.
These tests are written BEFORE the implementation exists and must be
importable even when the implementation module is absent.

Module under test (to be created by CodeAtlas):
  specterqa/ios/drivers/simulator/console.py — ConsoleMonitor, LogEntry, LogWatcher
"""

from __future__ import annotations

import concurrent.futures
import json
import time
from io import StringIO
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Conditional import guard — tests are importable even if impl is missing.
# ---------------------------------------------------------------------------

try:
    from specterqa.ios.drivers.simulator.console import (  # type: ignore[import]
        ConsoleMonitor,
        LogEntry,
        LogWatcher,
    )

    _CONSOLE_AVAILABLE = True
except ImportError:
    _CONSOLE_AVAILABLE = False
    ConsoleMonitor = None  # type: ignore[assignment,misc]
    LogEntry = None  # type: ignore[assignment,misc]
    LogWatcher = None  # type: ignore[assignment,misc]

needs_console = pytest.mark.skipif(
    not _CONSOLE_AVAILABLE,
    reason="specterqa.ios.drivers.simulator.console not yet implemented",
)


# ---------------------------------------------------------------------------
# Helpers — build LogEntry fixtures
# ---------------------------------------------------------------------------


def _make_entry(
    message: str = "Test log message",
    level: str = "default",
    subsystem: str = "com.example.app",
    category: str = "network",
    timestamp: str = "2026-03-29T10:00:00.000Z",
    process: str = "MyApp",
    thread_id: int = 42,
) -> "LogEntry":
    """Build a LogEntry fixture. Only called when _CONSOLE_AVAILABLE is True."""
    return LogEntry(
        timestamp=timestamp,
        level=level,
        subsystem=subsystem,
        category=category,
        message=message,
        process=process,
        thread_id=thread_id,
    )


def _make_json_log_line(
    message: str = "Test message",
    level: str = "Default",
    subsystem: str = "com.example.app",
    category: str = "networking",
    process: str = "MyApp",
    timestamp: str = "2026-03-29 10:00:00.000000+0000",
    thread_id: int = 1234,
) -> str:
    """Build a JSON log line matching xcrun simctl log stream --style json output."""
    return json.dumps(
        {
            "timestamp": timestamp,
            "messageType": level,
            "subsystem": subsystem,
            "category": category,
            "eventMessage": message,
            "processImagePath": f"/usr/bin/{process}",
            "threadID": thread_id,
        }
    )


# ===========================================================================
#  LogEntry — property tests (6 tests)
# ===========================================================================


@needs_console
class TestLogEntryIsError:
    """LogEntry.is_error property."""

    def test_is_error_true_for_error_level(self):
        """is_error is True when level == 'error'."""
        entry = _make_entry(level="error")
        assert entry.is_error is True

    def test_is_error_true_for_fault_level(self):
        """is_error is True when level == 'fault'."""
        entry = _make_entry(level="fault")
        assert entry.is_error is True

    def test_is_error_false_for_info(self):
        """is_error is False when level == 'info'."""
        entry = _make_entry(level="info")
        assert entry.is_error is False

    def test_is_error_false_for_debug(self):
        """is_error is False when level == 'debug'."""
        entry = _make_entry(level="debug")
        assert entry.is_error is False

    def test_is_error_false_for_default(self):
        """is_error is False when level == 'default'."""
        entry = _make_entry(level="default")
        assert entry.is_error is False


@needs_console
class TestLogEntryIsAuthRelated:
    """LogEntry.is_auth_related property — regex-based on message content."""

    def test_is_auth_related_detects_oauth(self):
        """Message containing 'oauth' (case-insensitive) is auth-related."""
        entry = _make_entry(message="Starting OAuth flow for user")
        assert entry.is_auth_related is True

    def test_is_auth_related_detects_oidc_uppercase(self):
        """Message containing 'OIDC' (case-insensitive) is auth-related."""
        entry = _make_entry(message="OIDC token validation passed")
        assert entry.is_auth_related is True

    def test_is_auth_related_detects_token(self):
        """Message containing 'token' is auth-related."""
        entry = _make_entry(message="Refreshing access token")
        assert entry.is_auth_related is True

    def test_is_auth_related_detects_auth(self):
        """Message containing 'auth' is auth-related."""
        entry = _make_entry(message="Auth header not present")
        assert entry.is_auth_related is True

    def test_is_auth_related_detects_login(self):
        """Message containing 'login' is auth-related."""
        entry = _make_entry(message="User login succeeded")
        assert entry.is_auth_related is True

    def test_is_auth_related_detects_credential(self):
        """Message containing 'credential' is auth-related."""
        entry = _make_entry(message="Credential store updated")
        assert entry.is_auth_related is True

    def test_is_auth_related_false_for_unrelated_message(self):
        """Plain unrelated message is NOT auth-related."""
        entry = _make_entry(message="UI layout pass completed")
        assert entry.is_auth_related is False


@needs_console
class TestLogEntryIsNetworkRelated:
    """LogEntry.is_network_related property — regex-based on message content."""

    def test_is_network_related_detects_http(self):
        """Message containing 'http' is network-related."""
        entry = _make_entry(message="HTTP request failed with 500")
        assert entry.is_network_related is True

    def test_is_network_related_detects_url(self):
        """Message containing 'url' is network-related."""
        entry = _make_entry(message="Invalid URL format detected")
        assert entry.is_network_related is True

    def test_is_network_related_detects_connection(self):
        """Message containing 'connection' is network-related."""
        entry = _make_entry(message="TCP connection established")
        assert entry.is_network_related is True

    def test_is_network_related_detects_dns(self):
        """Message containing 'dns' is network-related."""
        entry = _make_entry(message="DNS lookup for api.example.com")
        assert entry.is_network_related is True

    def test_is_network_related_false_for_unrelated(self):
        """Plain unrelated message is NOT network-related."""
        entry = _make_entry(message="Button tapped at coordinate 100,200")
        assert entry.is_network_related is False


# ===========================================================================
#  LogWatcher — callback behaviour (2 tests)
# ===========================================================================


@needs_console
class TestLogWatcherCheck:
    """LogWatcher.check() — calls callback when pattern matches, skips when not."""

    def test_check_calls_callback_on_match(self):
        """callback is invoked when the entry message matches the watcher's pattern."""
        callback = MagicMock()
        watcher = LogWatcher(name="auth_watcher", pattern=r"oauth", callback=callback)
        entry = _make_entry(message="Starting oauth flow")
        watcher.check(entry)
        callback.assert_called_once_with(entry)

    def test_check_does_not_call_callback_on_no_match(self):
        """callback is NOT called when the entry message does not match."""
        callback = MagicMock()
        watcher = LogWatcher(name="auth_watcher", pattern=r"oauth", callback=callback)
        entry = _make_entry(message="UI frame rendered successfully")
        watcher.check(entry)
        callback.assert_not_called()


# ===========================================================================
#  ConsoleMonitor — ring buffer (2 tests)
# ===========================================================================


@needs_console
class TestConsoleMonitorRingBuffer:
    """ConsoleMonitor ring buffer capacity and thread safety."""

    def test_ring_buffer_limits_to_buffer_size(self):
        """Adding more entries than buffer_size keeps only the most recent buffer_size entries."""
        monitor = ConsoleMonitor(buffer_size=5)
        # Directly exercise the internal buffer — inject entries via the parse path
        # by simulating what start() would produce, using the internal method if available,
        # or by running start() with a mocked subprocess that emits lines.
        #
        # Strategy: mock subprocess to emit 10 log lines; after stop(), recent() should
        # return at most 5 entries.
        lines = [_make_json_log_line(message=f"line {i}") for i in range(10)]
        output = "\n".join(lines) + "\n"

        mock_proc = MagicMock()
        mock_proc.stdout = StringIO(output)
        mock_proc.poll.return_value = None
        mock_proc.returncode = 0

        with patch("subprocess.Popen", return_value=mock_proc):
            monitor.start()
            # Give the background thread a moment to drain the output
            time.sleep(0.15)
            monitor.stop()

        all_entries = monitor.recent(seconds=9999)
        assert len(all_entries) <= 5, f"Ring buffer should hold at most 5 entries; got {len(all_entries)}"

    def test_ring_buffer_is_thread_safe(self):
        """Concurrent writes via ThreadPoolExecutor do not corrupt the buffer."""
        monitor = ConsoleMonitor(buffer_size=1000)

        # Access the internal _add_entry method (or equivalent) to write directly.
        # If the implementation exposes it, use it; otherwise drive via mocked subprocess.
        # We expect the implementation to expose _add_entry or similar for testing.
        errors: list[Exception] = []

        def write_entries(start: int, count: int) -> None:
            for i in range(count):
                try:
                    entry = _make_entry(message=f"thread entry {start + i}")
                    monitor._add_entry(entry)  # type: ignore[attr-defined]
                except Exception as exc:
                    errors.append(exc)

        with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
            futures = [executor.submit(write_entries, i * 100, 100) for i in range(3)]
            for f in concurrent.futures.as_completed(futures):
                f.result()

        assert len(errors) == 0, f"Thread safety violations: {errors}"
        all_entries = monitor.recent(seconds=9999)
        assert len(all_entries) <= 1000


# ===========================================================================
#  ConsoleMonitor.recent() — filtering (3 tests)
# ===========================================================================


@needs_console
class TestConsoleMonitorRecent:
    """ConsoleMonitor.recent() time/level/category filtering."""

    def _populate_monitor(self, monitor: "ConsoleMonitor") -> None:
        """Inject a set of known entries directly into the monitor."""
        now = time.time()
        entries = [
            _make_entry(message="old entry", level="info", category="ui", timestamp=_fmt_ts(now - 120)),
            _make_entry(message="recent info", level="info", category="network", timestamp=_fmt_ts(now - 3)),
            _make_entry(message="recent debug", level="debug", category="network", timestamp=_fmt_ts(now - 2)),
            _make_entry(message="recent error", level="error", category="auth", timestamp=_fmt_ts(now - 1)),
        ]
        for e in entries:
            monitor._add_entry(e)  # type: ignore[attr-defined]

    def test_recent_filters_by_time_window(self):
        """recent(seconds=10) excludes entries older than 10 seconds."""
        monitor = ConsoleMonitor(buffer_size=100)
        self._populate_monitor(monitor)
        results = monitor.recent(seconds=10)
        assert all("old entry" not in e.message for e in results), (
            "Old entry (120s ago) should not appear in recent(seconds=10)"
        )
        assert any("recent" in e.message for e in results)

    def test_recent_filters_by_level(self):
        """recent(level='error') returns only error-level entries."""
        monitor = ConsoleMonitor(buffer_size=100)
        self._populate_monitor(monitor)
        results = monitor.recent(seconds=9999, level="error")
        assert all(e.level == "error" for e in results), "recent(level='error') returned non-error entries"

    def test_recent_filters_by_category(self):
        """recent(category='network') returns only entries with that category."""
        monitor = ConsoleMonitor(buffer_size=100)
        self._populate_monitor(monitor)
        results = monitor.recent(seconds=9999, category="network")
        assert all(e.category == "network" for e in results), (
            "recent(category='network') returned entries with other categories"
        )


def _fmt_ts(epoch: float) -> str:
    """Format a Unix timestamp as ISO 8601 for use in LogEntry."""
    import datetime

    return datetime.datetime.utcfromtimestamp(epoch).strftime("%Y-%m-%dT%H:%M:%S.000Z")


# ===========================================================================
#  ConsoleMonitor.errors() (1 test)
# ===========================================================================


@needs_console
class TestConsoleMonitorErrors:
    """ConsoleMonitor.errors() returns only error/fault entries from error buffer."""

    def test_errors_returns_only_error_fault(self):
        """errors() returns only entries with level 'error' or 'fault'."""
        monitor = ConsoleMonitor(buffer_size=100, error_buffer_size=50)
        now = time.time()
        monitor._add_entry(_make_entry(message="normal info", level="info", timestamp=_fmt_ts(now - 1)))  # type: ignore[attr-defined]
        monitor._add_entry(_make_entry(message="an error", level="error", timestamp=_fmt_ts(now - 1)))  # type: ignore[attr-defined]
        monitor._add_entry(_make_entry(message="a fault", level="fault", timestamp=_fmt_ts(now - 1)))  # type: ignore[attr-defined]
        monitor._add_entry(_make_entry(message="debug line", level="debug", timestamp=_fmt_ts(now - 1)))  # type: ignore[attr-defined]
        results = monitor.errors(seconds=60)
        assert all(e.level in ("error", "fault") for e in results), (
            "errors() must return only error/fault level entries"
        )
        assert len(results) == 2, f"Expected 2 error/fault entries, got {len(results)}"


# ===========================================================================
#  ConsoleMonitor.search() (1 test)
# ===========================================================================


@needs_console
class TestConsoleMonitorSearch:
    """ConsoleMonitor.search() regex search across all buffered entries."""

    def test_search_finds_matching_entries(self):
        """search(pattern) returns entries whose message matches the regex."""
        monitor = ConsoleMonitor(buffer_size=100)
        monitor._add_entry(_make_entry(message="oauth token refreshed"))  # type: ignore[attr-defined]
        monitor._add_entry(_make_entry(message="frame layout pass"))  # type: ignore[attr-defined]
        monitor._add_entry(_make_entry(message="oauth flow started"))  # type: ignore[attr-defined]
        results = monitor.search(r"oauth")
        assert len(results) == 2, f"Expected 2 oauth matches, got {len(results)}"
        assert all("oauth" in e.message for e in results)


# ===========================================================================
#  ConsoleMonitor.add_watcher() (1 test)
# ===========================================================================


@needs_console
class TestConsoleMonitorAddWatcher:
    """ConsoleMonitor.add_watcher() — watcher is triggered on matching entries."""

    def test_add_watcher_triggers_callback_on_match(self):
        """After registering a watcher, adding a matching entry calls its callback."""
        monitor = ConsoleMonitor(buffer_size=100)
        callback = MagicMock()
        watcher = LogWatcher(name="token_watcher", pattern=r"token", callback=callback)
        monitor.add_watcher(watcher)
        match_entry = _make_entry(message="Access token acquired")
        no_match_entry = _make_entry(message="UI frame rendered")
        monitor._add_entry(match_entry)  # type: ignore[attr-defined]
        monitor._add_entry(no_match_entry)  # type: ignore[attr-defined]
        callback.assert_called_once_with(match_entry)


# ===========================================================================
#  ConsoleMonitor.summary() (1 test)
# ===========================================================================


@needs_console
class TestConsoleMonitorSummary:
    """ConsoleMonitor.summary() returns correct aggregate counts."""

    def test_summary_returns_correct_counts(self):
        """summary() returns total_entries, errors_count, by_level, by_subsystem."""
        monitor = ConsoleMonitor(buffer_size=100, error_buffer_size=50)
        entries = [
            _make_entry(message="m1", level="info", subsystem="com.app.auth"),
            _make_entry(message="m2", level="info", subsystem="com.app.ui"),
            _make_entry(message="m3", level="error", subsystem="com.app.auth"),
            _make_entry(message="m4", level="debug", subsystem="com.app.net"),
        ]
        for e in entries:
            monitor._add_entry(e)  # type: ignore[attr-defined]

        s = monitor.summary()
        assert "total_entries" in s, "summary must include 'total_entries'"
        assert "errors_count" in s, "summary must include 'errors_count'"
        assert "by_level" in s, "summary must include 'by_level'"
        assert "by_subsystem" in s, "summary must include 'by_subsystem'"
        assert s["total_entries"] == 4
        assert s["errors_count"] == 1
        assert s["by_level"].get("info", 0) == 2
        assert s["by_level"].get("error", 0) == 1
        assert s["by_subsystem"].get("com.app.auth", 0) == 2


# ===========================================================================
#  ConsoleMonitor.start() / stop() / JSON parsing (3 tests)
# ===========================================================================


@needs_console
class TestConsoleMonitorStartStop:
    """ConsoleMonitor.start() spawns correct subprocess; stop() terminates it."""

    def test_start_spawns_subprocess_with_correct_args(self):
        """start() calls subprocess.Popen with xcrun simctl spawn <device> log stream
        --level debug --style json."""
        monitor = ConsoleMonitor(device_id="booted")
        mock_proc = MagicMock()
        mock_proc.stdout = StringIO("")
        mock_proc.poll.return_value = 0

        with patch("subprocess.Popen", return_value=mock_proc) as mock_popen:
            monitor.start()
            time.sleep(0.05)
            monitor.stop()

        assert mock_popen.called, "subprocess.Popen was not called"
        cmd = mock_popen.call_args[0][0]  # First positional arg is the command list
        cmd_str = " ".join(cmd) if isinstance(cmd, list) else cmd
        assert "xcrun" in cmd_str, f"Expected 'xcrun' in command: {cmd_str}"
        assert "simctl" in cmd_str, f"Expected 'simctl' in command: {cmd_str}"
        assert "log" in cmd_str, f"Expected 'log' in command: {cmd_str}"
        assert "stream" in cmd_str, f"Expected 'stream' in command: {cmd_str}"
        assert "--level" in cmd_str or "debug" in cmd_str, f"Expected '--level debug' in command: {cmd_str}"
        assert "json" in cmd_str, f"Expected '--style json' in command: {cmd_str}"

    def test_stop_kills_subprocess_and_joins_thread(self):
        """stop() terminates the subprocess and the background reader thread exits."""
        monitor = ConsoleMonitor(device_id="booted")
        mock_proc = MagicMock()
        mock_proc.stdout = StringIO("")
        mock_proc.poll.return_value = None

        with patch("subprocess.Popen", return_value=mock_proc):
            monitor.start()
            time.sleep(0.05)
            monitor.stop()

        # The process kill/terminate method should have been called
        terminated = mock_proc.terminate.called or mock_proc.kill.called
        assert terminated, "stop() must terminate or kill the subprocess"

    def test_parses_json_log_stream_into_log_entries(self):
        """start() parses JSON lines from xcrun log stream into LogEntry objects."""
        monitor = ConsoleMonitor(device_id="booted", buffer_size=50)
        line = _make_json_log_line(
            message="Network request to api.example.com failed",
            level="Error",
            subsystem="com.example.app",
            category="networking",
        )
        mock_proc = MagicMock()
        mock_proc.stdout = StringIO(line + "\n")
        mock_proc.poll.side_effect = [None, None, 0]

        with patch("subprocess.Popen", return_value=mock_proc):
            monitor.start()
            time.sleep(0.2)
            monitor.stop()

        entries = monitor.recent(seconds=9999)
        assert len(entries) >= 1, "Expected at least one parsed LogEntry"
        assert any("api.example.com" in e.message for e in entries), (
            "Parsed entry should contain the original log message"
        )
        # Verify the entry is a LogEntry instance (has expected attributes)
        first = entries[0]
        assert hasattr(first, "timestamp"), "LogEntry must have timestamp"
        assert hasattr(first, "level"), "LogEntry must have level"
        assert hasattr(first, "message"), "LogEntry must have message"
        assert hasattr(first, "subsystem"), "LogEntry must have subsystem"
        assert hasattr(first, "category"), "LogEntry must have category"
