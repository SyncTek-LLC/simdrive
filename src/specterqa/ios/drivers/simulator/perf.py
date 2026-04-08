"""M6: PerfProfiler — performance measurement for iOS Simulator apps.

Measures CPU, memory, thread count, and launch time via simctl and ps commands.
Tracks a history of PerfSnapshot objects for trend detection.
"""

from __future__ import annotations

import logging
import subprocess
import time

logger = logging.getLogger("specterqa.ios.drivers.simulator.perf")
from dataclasses import dataclass
from typing import Callable, Optional


@dataclass
class PerfSnapshot:
    """Immutable snapshot of app performance metrics at a point in time."""

    memory_mb: float
    cpu_percent: float
    thread_count: int
    disk_usage_mb: float
    fps_estimate: float
    timestamp: float


class PerfProfiler:
    """Measures performance metrics for an iOS Simulator app process.

    Uses ``xcrun simctl`` and ``ps`` to collect CPU, memory, and thread data.
    Maintains an internal history of snapshots for trend analysis.

    Args:
        device_id: Simulator device UDID or "booted".
        bundle_id: The app's bundle identifier (e.g. "com.example.myapp").
    """

    def __init__(self, device_id: str, bundle_id: str) -> None:
        self.device_id = device_id
        self.bundle_id = bundle_id
        self._snapshots: list[PerfSnapshot] = []

    # ------------------------------------------------------------------
    # Private helpers — subprocess wrappers
    # ------------------------------------------------------------------

    def _get_app_pid(self) -> Optional[int]:
        """Return the PID for bundle_id, or None if not running.

        Strategy 1: ``xcrun simctl spawn <device> launchctl list`` — works when
        the simctl spawn pathway is available and the app is registered with
        launchctl inside the simulator.

        Strategy 2 (fallback): ``ps aux | grep <bundle_id>`` — simulator apps
        run as native host processes on macOS, so the bundle ID often appears
        in the process argument list even when launchctl doesn't expose it.
        """
        # Strategy 1: simctl launchctl
        result = subprocess.run(
            ["xcrun", "simctl", "spawn", self.device_id, "launchctl", "list"],
            capture_output=True,
            text=True,
        )
        for line in result.stdout.splitlines():
            parts = line.split("\t")
            if len(parts) >= 3 and parts[2].strip() == self.bundle_id:
                try:
                    pid = int(parts[0].strip())
                    if pid > 0:
                        return pid
                except ValueError:
                    pass

        # Strategy 2: host ps aux — simulator app processes appear on the host
        try:
            ps_result = subprocess.run(
                ["ps", "aux"],
                capture_output=True,
                text=True,
            )
            for line in ps_result.stdout.splitlines():
                if self.bundle_id in line:
                    # ps aux columns: USER PID %CPU %MEM VSZ RSS TTY STAT START TIME COMMAND
                    parts = line.split()
                    if len(parts) >= 2:
                        try:
                            return int(parts[1])
                        except ValueError:
                            pass
        except OSError:
            pass

        return None

    def _get_memory(self, pid: int) -> float:
        """Return resident memory in MB for the given PID.

        Reads RSS (resident set size) in KB from ``ps`` and converts to MB.
        """
        result = subprocess.run(
            ["ps", "-p", str(pid), "-o", "rss="],
            capture_output=True,
            text=True,
        )
        rss_kb = float(result.stdout.strip())
        return rss_kb / 1024.0

    def _get_cpu(self, pid: int) -> float:
        """Return the CPU usage percentage for the given PID."""
        result = subprocess.run(
            ["ps", "-p", str(pid), "-o", "%cpu="],
            capture_output=True,
            text=True,
        )
        return float(result.stdout.strip())

    def _get_thread_count(self, pid: int) -> int:
        """Return the thread count for the given PID."""
        result = subprocess.run(
            ["ps", "-p", str(pid), "-o", "nlwp="],
            capture_output=True,
            text=True,
        )
        return int(result.stdout.strip())

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def snapshot(self) -> PerfSnapshot:
        """Collect a single performance snapshot.

        When the app is not running, all numeric fields default to zero.
        The snapshot is appended to the internal history for trend analysis.

        Returns:
            A :class:`PerfSnapshot` with current metrics (or zero defaults).
        """
        pid = self._get_app_pid()
        if pid is None:
            snap = PerfSnapshot(
                memory_mb=0.0,
                cpu_percent=0.0,
                thread_count=0,
                disk_usage_mb=0.0,
                fps_estimate=0.0,
                timestamp=time.time(),
            )
        else:
            snap = PerfSnapshot(
                memory_mb=self._get_memory(pid),
                cpu_percent=self._get_cpu(pid),
                thread_count=self._get_thread_count(pid),
                disk_usage_mb=0.0,
                fps_estimate=0.0,
                timestamp=time.time(),
            )
        self._snapshots.append(snap)
        return snap

    def measure_launch_time(self, launch_fn: Callable[[], None]) -> float:
        """Measure how long until the app is responsive after launch.

        Calls *launch_fn* to trigger the launch, then polls until the app
        process is visible in launchctl or a subprocess call returns output.

        Args:
            launch_fn: Zero-argument callable that initiates the app launch.

        Returns:
            Elapsed time in seconds as a float (>= 0).
        """
        start = time.time()
        launch_fn()
        # Poll via subprocess until the pid is visible or a short timeout.
        deadline = start + 30.0
        while time.time() < deadline:
            result = subprocess.run(
                ["xcrun", "simctl", "spawn", self.device_id, "launchctl", "list"],
                capture_output=True,
                text=True,
            )
            if result.stdout:
                break
            time.sleep(0.1)
        return time.time() - start

    def summary(self) -> dict:
        """Return a trend summary over the recorded snapshot history.

        Memory trend is "growing" when the latest snapshot's memory exceeds
        the oldest by more than 10 MB; otherwise "stable".

        Returns:
            A dict with keys including ``memory_trend``, ``snapshot_count``,
            and the latest snapshot values.
        """
        if not self._snapshots:
            return {
                "memory_trend": "stable",
                "snapshot_count": 0,
                "latest_memory_mb": 0.0,
                "latest_cpu_percent": 0.0,
                "latest_thread_count": 0,
            }

        oldest = self._snapshots[0]
        latest = self._snapshots[-1]
        delta = latest.memory_mb - oldest.memory_mb
        memory_trend = "growing" if delta > 10.0 else "stable"

        return {
            "memory_trend": memory_trend,
            "snapshot_count": len(self._snapshots),
            "latest_memory_mb": latest.memory_mb,
            "latest_cpu_percent": latest.cpu_percent,
            "latest_thread_count": latest.thread_count,
            "memory_delta_mb": delta,
        }
