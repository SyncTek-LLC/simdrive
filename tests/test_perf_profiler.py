"""Tests for M6: PerfProfiler — specterqa/ios/drivers/simulator/perf.py

TDD Phase — INIT-2026-492.
These tests are written BEFORE the implementation exists and must be importable
even when the implementation module is absent.

Module under test (to be created by CodeAtlas):
  specterqa/ios/drivers/simulator/perf.py  — PerfProfiler, PerfSnapshot
"""

from __future__ import annotations

import dataclasses
import time
from dataclasses import dataclass, fields
from typing import Callable
from unittest import mock
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Conditional import guard — tests are importable even if impl is missing.
# ---------------------------------------------------------------------------

try:
    from specterqa.ios.drivers.simulator.perf import PerfProfiler, PerfSnapshot  # type: ignore[import]
    _PERF_AVAILABLE = True
except ImportError:
    _PERF_AVAILABLE = False
    PerfProfiler = None  # type: ignore[assignment,misc]
    PerfSnapshot = None  # type: ignore[assignment,misc]

needs_perf = pytest.mark.skipif(
    not _PERF_AVAILABLE,
    reason="specterqa.ios.drivers.simulator.perf not yet implemented",
)


# ---------------------------------------------------------------------------
# Mock builder helpers
# ---------------------------------------------------------------------------

def _make_subprocess_result(stdout: str = "", returncode: int = 0) -> MagicMock:
    """Build a mock subprocess.CompletedProcess result."""
    result = MagicMock()
    result.stdout = stdout
    result.returncode = returncode
    result.stderr = ""
    return result


# Fixture: realistic launchctl list output with one app entry
LAUNCHCTL_OUTPUT_WITH_APP = """\
PID\tStatus\tLabel
1234\t0\tcom.apple.Preferences
5678\t0\tcom.example.testapp
890\t0\tcom.apple.springboard
"""

LAUNCHCTL_OUTPUT_WITHOUT_APP = """\
PID\tStatus\tLabel
1234\t0\tcom.apple.Preferences
890\t0\tcom.apple.springboard
"""

PS_RSS_OUTPUT = "81920\n"    # 81920 KB == 80.0 MB
PS_CPU_OUTPUT = "12.5\n"
PS_THREADS_OUTPUT = "32\n"


# ===========================================================================
#  M6: PerfProfiler — 10 tests
# ===========================================================================


@needs_perf
class TestGetAppPid:
    """_get_app_pid() — parse launchctl output to find bundle_id PID."""

    def test_parses_pid_from_launchctl_output(self):
        """_get_app_pid returns the integer PID when bundle_id is present in output."""
        profiler = PerfProfiler(device_id="booted", bundle_id="com.example.testapp")

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = _make_subprocess_result(LAUNCHCTL_OUTPUT_WITH_APP)
            pid = profiler._get_app_pid()

        assert pid == 5678

    def test_returns_none_when_app_not_found(self):
        """_get_app_pid returns None when bundle_id is absent from launchctl output."""
        profiler = PerfProfiler(device_id="booted", bundle_id="com.example.testapp")

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = _make_subprocess_result(LAUNCHCTL_OUTPUT_WITHOUT_APP)
            pid = profiler._get_app_pid()

        assert pid is None


@needs_perf
class TestGetMemory:
    """_get_memory() — convert RSS KB to MB."""

    def test_converts_rss_kb_to_mb(self):
        """_get_memory converts RSS in KB (from ps) to MB correctly: 81920 KB == 80.0 MB."""
        profiler = PerfProfiler(device_id="booted", bundle_id="com.example.testapp")

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = _make_subprocess_result(PS_RSS_OUTPUT)
            mem_mb = profiler._get_memory(pid=5678)

        assert mem_mb == pytest.approx(80.0)


@needs_perf
class TestGetCpu:
    """_get_cpu() — parse CPU percentage from ps."""

    def test_parses_cpu_percentage(self):
        """_get_cpu parses the %cpu column from ps and returns a float."""
        profiler = PerfProfiler(device_id="booted", bundle_id="com.example.testapp")

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = _make_subprocess_result(PS_CPU_OUTPUT)
            cpu = profiler._get_cpu(pid=5678)

        assert cpu == pytest.approx(12.5)


@needs_perf
class TestGetThreadCount:
    """_get_thread_count() — parse thread count from ps output."""

    def test_returns_correct_thread_count(self):
        """_get_thread_count returns an integer thread count parsed from ps output."""
        profiler = PerfProfiler(device_id="booted", bundle_id="com.example.testapp")

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = _make_subprocess_result(PS_THREADS_OUTPUT)
            count = profiler._get_thread_count(pid=5678)

        assert count == 32


@needs_perf
class TestSnapshot:
    """snapshot() — aggregate all metrics into a PerfSnapshot."""

    def test_snapshot_returns_perf_snapshot_with_all_fields(self):
        """snapshot() calls underlying metrics methods and populates a PerfSnapshot."""
        profiler = PerfProfiler(device_id="booted", bundle_id="com.example.testapp")

        with patch.object(profiler, "_get_app_pid", return_value=5678), \
             patch.object(profiler, "_get_memory", return_value=80.0), \
             patch.object(profiler, "_get_cpu", return_value=12.5), \
             patch.object(profiler, "_get_thread_count", return_value=32):

            snap = profiler.snapshot()

        assert isinstance(snap, PerfSnapshot)
        assert snap.memory_mb == pytest.approx(80.0)
        assert snap.cpu_percent == pytest.approx(12.5)
        assert snap.thread_count == 32
        assert isinstance(snap.timestamp, float)
        assert snap.timestamp > 0

    def test_snapshot_returns_defaults_when_app_not_running(self):
        """snapshot() returns zeros/defaults for all fields when _get_app_pid returns None."""
        profiler = PerfProfiler(device_id="booted", bundle_id="com.example.testapp")

        with patch.object(profiler, "_get_app_pid", return_value=None):
            snap = profiler.snapshot()

        assert isinstance(snap, PerfSnapshot)
        assert snap.memory_mb == pytest.approx(0.0)
        assert snap.cpu_percent == pytest.approx(0.0)
        assert snap.thread_count == 0


@needs_perf
class TestMeasureLaunchTime:
    """measure_launch_time() — time from launch_fn() until first non-blank screenshot."""

    def test_returns_elapsed_time_in_seconds(self):
        """measure_launch_time calls launch_fn and returns a positive float (seconds)."""
        profiler = PerfProfiler(device_id="booted", bundle_id="com.example.testapp")

        # Provide a screenshot that simulates a non-blank UI appearing immediately.
        # The underlying screenshot mechanism should be injectable / patchable.
        launch_fn = MagicMock()

        # Patch whatever screenshot method PerfProfiler uses so it returns
        # a non-empty/non-black image immediately.
        with patch.object(profiler, "_get_app_pid", return_value=5678), \
             patch("subprocess.run") as mock_run:
            # Simulate screenshot returning some non-blank output
            mock_run.return_value = _make_subprocess_result("screenshot_data")
            elapsed = profiler.measure_launch_time(launch_fn)

        launch_fn.assert_called_once()
        assert isinstance(elapsed, float)
        assert elapsed >= 0.0


@needs_perf
class TestSummary:
    """summary() — trend analysis of memory across snapshots."""

    def test_summary_detects_growing_memory_trend(self):
        """summary() reports 'growing' when memory increased >10 MB across snapshots."""
        profiler = PerfProfiler(device_id="booted", bundle_id="com.example.testapp")

        # Inject a sequence of snapshots showing significant memory growth.
        now = time.time()
        snapshots = [
            PerfSnapshot(memory_mb=100.0, cpu_percent=5.0, thread_count=20, disk_usage_mb=50.0, fps_estimate=60.0, timestamp=now - 20),
            PerfSnapshot(memory_mb=110.0, cpu_percent=5.0, thread_count=20, disk_usage_mb=50.0, fps_estimate=60.0, timestamp=now - 10),
            PerfSnapshot(memory_mb=125.0, cpu_percent=5.0, thread_count=20, disk_usage_mb=50.0, fps_estimate=60.0, timestamp=now),
        ]
        # Inject snapshots directly into the profiler's internal history
        profiler._snapshots = snapshots  # type: ignore[attr-defined]

        result = profiler.summary()

        assert isinstance(result, dict)
        assert result.get("memory_trend") == "growing" or "growing" in str(result).lower()

    def test_summary_reports_stable_when_memory_flat(self):
        """summary() reports 'stable' when memory does not change significantly."""
        profiler = PerfProfiler(device_id="booted", bundle_id="com.example.testapp")

        now = time.time()
        snapshots = [
            PerfSnapshot(memory_mb=80.0, cpu_percent=5.0, thread_count=20, disk_usage_mb=50.0, fps_estimate=60.0, timestamp=now - 20),
            PerfSnapshot(memory_mb=80.5, cpu_percent=5.0, thread_count=20, disk_usage_mb=50.0, fps_estimate=60.0, timestamp=now - 10),
            PerfSnapshot(memory_mb=80.2, cpu_percent=5.0, thread_count=20, disk_usage_mb=50.0, fps_estimate=60.0, timestamp=now),
        ]
        profiler._snapshots = snapshots  # type: ignore[attr-defined]

        result = profiler.summary()

        assert isinstance(result, dict)
        assert result.get("memory_trend") == "stable" or "stable" in str(result).lower()
