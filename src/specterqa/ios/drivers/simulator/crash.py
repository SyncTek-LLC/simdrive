"""M8: CrashDetector — crash detection for iOS Simulator apps.

Monitors the DiagnosticReports directory for new .ips crash log files,
parses them, and filters by bundle ID. Maintains a baseline set so only
crashes that occurred after :meth:`start` are reported.
"""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


@dataclass
class CrashReport:
    """Parsed representation of a single iOS crash report (.ips file).

    All fields default to ``"unknown"`` / empty so that malformed files
    can still produce a safe return value.
    """

    timestamp: str = "unknown"
    exception_type: str = "unknown"
    exception_code: str = "unknown"
    crashing_thread: int = 0
    backtrace: list[str] = field(default_factory=list)
    last_exception: Optional[str] = None
    app_version: str = "unknown"
    os_version: str = "unknown"
    device: str = "unknown"
    raw_path: str = ""


class CrashDetector:
    """Detect new crash reports for an iOS Simulator app.

    Monitors ``~/Library/Logs/DiagnosticReports/`` (or an injected path)
    for ``.ips`` files that appeared *after* :meth:`start` was called.

    Args:
        device_id: Simulator device UDID or "booted".
        bundle_id: The app's bundle identifier (e.g. "com.example.myapp").
    """

    def __init__(self, device_id: str, bundle_id: str) -> None:
        self.device_id = device_id
        self.bundle_id = bundle_id
        self._baseline: set[str] = set()
        self._reports_dir: str = os.path.expanduser("~/Library/Logs/DiagnosticReports")
        # Internal cache populated by check()
        self._detected_crashes: list[CrashReport] = []

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Record the current set of .ips files as the baseline.

        Any .ips file that exists *before* this call will be ignored by
        subsequent calls to :meth:`check`.
        """
        reports_path = Path(self._reports_dir)
        if reports_path.exists():
            self._baseline = {p.name for p in reports_path.iterdir() if p.suffix == ".ips"}
        else:
            self._baseline = set()
        # Reset crash cache on each start
        self._detected_crashes = []

    # ------------------------------------------------------------------
    # Detection
    # ------------------------------------------------------------------

    def check(self) -> list[CrashReport]:
        """Return new CrashReports that appeared after :meth:`start`.

        Diffs the current directory listing against the baseline, parses
        each new .ips file, and filters by :attr:`bundle_id`.

        Returns:
            List of :class:`CrashReport` objects — one per new matching crash.
            Returns an empty list when no new crashes are present.
        """
        reports_path = Path(self._reports_dir)
        if not reports_path.exists():
            return []

        current_files = {p.name for p in reports_path.iterdir() if p.suffix == ".ips"}
        new_files = current_files - self._baseline

        crashes: list[CrashReport] = []
        for filename in new_files:
            full_path = str(reports_path / filename)
            report = self._parse_ips(full_path)
            if report is None:
                continue
            # Filter to only our target app
            # Re-read bundle ID from raw content for filtering
            try:
                raw = json.loads(Path(full_path).read_text())
                file_bundle_id = raw.get("bundleID", "")
            except Exception:
                file_bundle_id = ""
            if file_bundle_id == self.bundle_id:
                crashes.append(report)

        # Accumulate in internal cache for latest_crash()
        self._detected_crashes.extend(crashes)
        return crashes

    def _parse_ips(self, path: str) -> Optional[CrashReport]:
        """Parse a single .ips JSON file into a :class:`CrashReport`.

        Handles malformed or non-JSON files gracefully by returning None.

        Args:
            path: Absolute path to the .ips file.

        Returns:
            A :class:`CrashReport`, or None if the file cannot be parsed.
        """
        try:
            raw = json.loads(Path(path).read_text())
        except Exception:
            return None

        # Extract exception info
        exception_block = raw.get("exception", {}) or {}
        exception_type = exception_block.get("type", "unknown")
        exception_code = exception_block.get("codes", "unknown")

        # Extract backtrace for the crashing thread
        crashing_thread_id = raw.get("crashing_thread", 0)
        backtrace: list[str] = []
        for thread in raw.get("threads", []):
            if thread.get("id") == crashing_thread_id:
                bt = thread.get("backtrace", [])
                backtrace = bt if isinstance(bt, list) else []
                break

        return CrashReport(
            timestamp=raw.get("timestamp", "unknown"),
            exception_type=exception_type,
            exception_code=exception_code,
            crashing_thread=crashing_thread_id,
            backtrace=backtrace,
            last_exception=raw.get("NSException"),
            app_version=raw.get("app_version", "unknown"),
            os_version=raw.get("os_version", "unknown"),
            device=raw.get("device", "unknown"),
            raw_path=path,
        )

    # ------------------------------------------------------------------
    # Process status
    # ------------------------------------------------------------------

    def is_app_running(self) -> bool:
        """Return True if the app process is currently active.

        Queries ``launchctl list`` via simctl and checks for the bundle ID.

        Returns:
            True when the bundle ID appears in launchctl output.
        """
        result = subprocess.run(
            ["xcrun", "simctl", "spawn", self.device_id, "launchctl", "list"],
            capture_output=True,
            text=True,
        )
        return self.bundle_id in result.stdout

    # ------------------------------------------------------------------
    # Convenience
    # ------------------------------------------------------------------

    def stop(self) -> None:
        """Stop the crash detector.

        Resets the baseline and clears accumulated crash records.  Provided
        for symmetry with other driver modules that have a start/stop lifecycle.
        This is a no-op beyond clearing internal state — no background process
        is running.
        """
        self._baseline = set()
        self._detected_crashes = []

    def latest_crash(self) -> Optional[CrashReport]:
        """Return the most recent crash by timestamp from accumulated check() results.

        Returns:
            The :class:`CrashReport` with the lexicographically latest
            ``timestamp`` string, or None if no crashes have been detected.
        """
        all_crashes = self._detected_crashes
        if not all_crashes:
            # Try a fresh check to populate
            fresh = self.check()
            if not fresh:
                return None
            all_crashes = fresh

        if not all_crashes:
            return None

        return max(all_crashes, key=lambda r: r.timestamp)
