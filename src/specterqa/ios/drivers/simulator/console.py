"""M4: ConsoleMonitor — iOS Simulator real-time log stream monitoring.

Spawns ``xcrun simctl spawn <device> log stream --level debug --style json``
and continuously parses JSON log lines into :class:`LogEntry` objects stored
in a thread-safe ring buffer.

INIT-2026-492 — SpecterQA iOS Simulator Driver, Phase 2.
"""

from __future__ import annotations

import collections
import json
import re
import subprocess
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, List, Optional


# ---------------------------------------------------------------------------
# LogEntry
# ---------------------------------------------------------------------------

@dataclass
class LogEntry:
    """A single log entry from ``xcrun simctl log stream --style json``."""

    timestamp: str
    level: str
    subsystem: str
    category: str
    message: str
    process: str
    thread_id: int

    # ------------------------------------------------------------------
    # Derived properties
    # ------------------------------------------------------------------

    @property
    def is_error(self) -> bool:
        """True when the entry level is ``'error'`` or ``'fault'``."""
        return self.level in ("error", "fault")

    @property
    def is_auth_related(self) -> bool:
        """True when the message contains auth-related keywords (case-insensitive)."""
        return bool(
            re.search(r"(?i)(oauth|oidc|token|auth|login|credential)", self.message)
        )

    @property
    def is_network_related(self) -> bool:
        """True when the message contains network-related keywords (case-insensitive)."""
        return bool(
            re.search(r"(?i)(http|url|connection|dns)", self.message)
        )


# ---------------------------------------------------------------------------
# LogWatcher
# ---------------------------------------------------------------------------

@dataclass
class LogWatcher:
    """Watches for log entries matching a regex pattern and calls a callback.

    Args:
        name: Human-readable name for this watcher (used in diagnostics).
        pattern: Regex pattern applied to :attr:`LogEntry.message`.
        callback: Callable invoked with the matching :class:`LogEntry` when
            the pattern fires.
    """

    name: str
    pattern: str
    callback: Callable[["LogEntry"], None]

    def __post_init__(self) -> None:
        self._compiled: re.Pattern[str] = re.compile(self.pattern)

    def check(self, entry: "LogEntry") -> None:
        """Invoke callback if *entry.message* matches the watcher pattern.

        Args:
            entry: The log entry to test.
        """
        if self._compiled.search(entry.message):
            self.callback(entry)


# ---------------------------------------------------------------------------
# ConsoleMonitor
# ---------------------------------------------------------------------------

class ConsoleMonitor:
    """Real-time iOS Simulator log stream monitor.

    Spawns ``xcrun simctl spawn <device_id> log stream --level debug
    --style json`` in a background process and continuously parses its JSON
    output into :class:`LogEntry` objects stored in a thread-safe ring buffer.

    Args:
        device_id: Simulator device UDID or ``"booted"`` (default).
        buffer_size: Maximum entries in the main ring buffer (default 5000).
        error_buffer_size: Maximum entries in the dedicated error buffer
            (default 500).  Only ``error``/``fault`` level entries are stored
            here.
    """

    def __init__(
        self,
        device_id: str = "booted",
        buffer_size: int = 5000,
        error_buffer_size: int = 500,
    ) -> None:
        self._device_id = device_id
        self._buffer_size = buffer_size
        self._error_buffer_size = error_buffer_size

        self._lock = threading.Lock()
        self._buffer: collections.deque[LogEntry] = collections.deque(maxlen=buffer_size)
        self._error_buffer: collections.deque[LogEntry] = collections.deque(
            maxlen=error_buffer_size
        )
        self._watchers: list[LogWatcher] = []

        self._process: Optional[subprocess.Popen[str]] = None
        self._reader_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Spawn the log stream process and begin reading in a background thread."""
        self._stop_event.clear()
        cmd = [
            "xcrun", "simctl", "spawn", self._device_id,
            "log", "stream",
            "--level", "debug",
            "--style", "json",
        ]
        self._process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
        )
        self._reader_thread = threading.Thread(
            target=self._read_loop,
            daemon=True,
            name=f"ConsoleMonitor-{self._device_id}",
        )
        self._reader_thread.start()

    def stop(self) -> None:
        """Terminate the log stream process and wait for the reader thread to exit."""
        self._stop_event.set()
        if self._process is not None:
            try:
                self._process.terminate()
            except Exception:
                try:
                    self._process.kill()
                except Exception:
                    pass
            self._process = None
        if self._reader_thread is not None:
            self._reader_thread.join(timeout=2.0)
            self._reader_thread = None

    # ------------------------------------------------------------------
    # Internal: reader loop and entry ingestion
    # ------------------------------------------------------------------

    def _read_loop(self) -> None:
        """Background thread: read stdout lines, parse JSON, add entries."""
        if self._process is None or self._process.stdout is None:
            return
        try:
            for line in self._process.stdout:
                if self._stop_event.is_set():
                    break
                line = line.strip()
                if not line:
                    continue
                entry = self._parse_json_line(line)
                if entry is not None:
                    self._add_entry(entry)
        except Exception:
            pass

    @staticmethod
    def _parse_json_line(line: str) -> Optional[LogEntry]:
        """Parse a single JSON log line into a :class:`LogEntry`.

        The xcrun ``--style json`` output fields used:
        - ``timestamp`` → :attr:`LogEntry.timestamp`
        - ``messageType`` → :attr:`LogEntry.level` (lower-cased)
        - ``subsystem`` → :attr:`LogEntry.subsystem`
        - ``category`` → :attr:`LogEntry.category`
        - ``eventMessage`` → :attr:`LogEntry.message`
        - ``processImagePath`` basename → :attr:`LogEntry.process`
        - ``threadID`` → :attr:`LogEntry.thread_id`
        """
        try:
            data: dict[str, Any] = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            return None

        import os.path
        process_path: str = data.get("processImagePath", "")
        process_name: str = os.path.basename(process_path) if process_path else data.get("process", "")

        return LogEntry(
            timestamp=data.get("timestamp", ""),
            level=data.get("messageType", "default").lower(),
            subsystem=data.get("subsystem", ""),
            category=data.get("category", ""),
            message=data.get("eventMessage", ""),
            process=process_name,
            thread_id=int(data.get("threadID", 0)),
        )

    def _add_entry(self, entry: LogEntry) -> None:
        """Add *entry* to the ring buffer (and error buffer if applicable).

        Thread-safe: protected by ``_lock``.

        Args:
            entry: The :class:`LogEntry` to store.
        """
        with self._lock:
            self._buffer.append(entry)
            if entry.is_error:
                self._error_buffer.append(entry)
            # Snapshot watchers under lock to avoid mutation during iteration
            watchers = list(self._watchers)

        # Call watchers outside the lock to avoid holding it during callbacks
        for watcher in watchers:
            watcher.check(entry)

    # ------------------------------------------------------------------
    # Query methods
    # ------------------------------------------------------------------

    def recent(
        self,
        seconds: float,
        level: Optional[str] = None,
        category: Optional[str] = None,
    ) -> list[LogEntry]:
        """Return entries from the ring buffer within the last *seconds* window.

        Args:
            seconds: Time window in seconds.  Entries whose timestamp parses
                to an epoch older than ``now - seconds`` are excluded.
            level: Optional level filter (e.g. ``"error"``).  Only entries
                with exactly this level are included.
            category: Optional category filter.  Only entries with exactly
                this category are included.

        Returns:
            List of matching :class:`LogEntry` objects, oldest first.
        """
        cutoff = time.time() - seconds
        with self._lock:
            snapshot = list(self._buffer)

        results: list[LogEntry] = []
        for entry in snapshot:
            entry_ts = _parse_timestamp(entry.timestamp)
            if entry_ts < cutoff:
                continue
            if level is not None and entry.level != level:
                continue
            if category is not None and entry.category != category:
                continue
            results.append(entry)
        return results

    def errors(self, seconds: float) -> list[LogEntry]:
        """Return error/fault entries from the dedicated error buffer.

        Args:
            seconds: Time window in seconds.

        Returns:
            List of :class:`LogEntry` objects with level ``'error'`` or
            ``'fault'`` that fall within the given time window.
        """
        cutoff = time.time() - seconds
        with self._lock:
            snapshot = list(self._error_buffer)
        return [
            e for e in snapshot
            if _parse_timestamp(e.timestamp) >= cutoff
        ]

    def search(self, pattern: str) -> list[LogEntry]:
        """Search all buffered entries by regex on the message field.

        Args:
            pattern: Regular expression string applied to
                :attr:`LogEntry.message`.

        Returns:
            List of matching :class:`LogEntry` objects.
        """
        compiled = re.compile(pattern)
        with self._lock:
            snapshot = list(self._buffer)
        return [e for e in snapshot if compiled.search(e.message)]

    def add_watcher(self, watcher: LogWatcher) -> None:
        """Register a :class:`LogWatcher` to be called for every new entry.

        Args:
            watcher: The watcher to register.
        """
        with self._lock:
            self._watchers.append(watcher)

    def summary(self) -> dict[str, Any]:
        """Return aggregate statistics for the current buffer contents.

        Returns:
            Dict with keys:
            - ``total_entries``: int — total entries in the main buffer.
            - ``errors_count``: int — total entries in the error buffer.
            - ``by_level``: dict[str, int] — count per log level.
            - ``by_subsystem``: dict[str, int] — count per subsystem.
        """
        with self._lock:
            main_snapshot = list(self._buffer)
            error_count = len(self._error_buffer)

        by_level: dict[str, int] = {}
        by_subsystem: dict[str, int] = {}
        for entry in main_snapshot:
            by_level[entry.level] = by_level.get(entry.level, 0) + 1
            by_subsystem[entry.subsystem] = by_subsystem.get(entry.subsystem, 0) + 1

        return {
            "total_entries": len(main_snapshot),
            "errors_count": error_count,
            "by_level": by_level,
            "by_subsystem": by_subsystem,
        }


# ---------------------------------------------------------------------------
# Timestamp parsing helper
# ---------------------------------------------------------------------------

def _parse_timestamp(timestamp: str) -> float:
    """Parse an ISO 8601 timestamp string to a Unix epoch float.

    Handles the format used in :class:`LogEntry` fixtures:
    ``"2026-03-29T10:00:00.000Z"``

    Returns 0.0 if parsing fails (entry treated as infinitely old).
    """
    import datetime
    if not timestamp:
        return 0.0
    # Normalise: replace trailing Z with +00:00 for fromisoformat compatibility
    ts = timestamp.rstrip("Z")
    # Strip sub-second precision beyond 6 digits (Python's limit)
    if "." in ts:
        parts = ts.split(".")
        frac = parts[1][:6]
        ts = f"{parts[0]}.{frac}"
    try:
        dt = datetime.datetime.fromisoformat(ts)
        # Treat as UTC if no timezone info
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=datetime.timezone.utc)
        return dt.timestamp()
    except (ValueError, TypeError):
        return 0.0
