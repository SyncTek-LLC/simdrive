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
from typing import Any, Callable, Optional


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
    ingestion_time: float = field(default_factory=time.time)

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
        return bool(re.search(r"(?i)(oauth|oidc|token|auth|login|credential)", self.message))

    @property
    def is_network_related(self) -> bool:
        """True when the message contains network-related keywords (case-insensitive)."""
        return bool(re.search(r"(?i)(http|url|connection|dns)", self.message))


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
        self._error_buffer: collections.deque[LogEntry] = collections.deque(maxlen=error_buffer_size)
        self._watchers: list[LogWatcher] = []

        self._process: Optional[subprocess.Popen[str]] = None
        self._reader_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Spawn the log stream process and begin reading in a background thread.

        After starting the background reader, calls :meth:`wait_for_ready` with
        a short timeout so that the first call to :meth:`recent` or
        :meth:`errors` is more likely to return buffered entries.
        """
        self._stop_event.clear()
        cmd = [
            "xcrun",
            "simctl",
            "spawn",
            self._device_id,
            "log",
            "stream",
            "--level",
            "debug",
            "--style",
            "json",
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
        # Block briefly until at least one log entry is available so that
        # callers don't race with the background reader on the first query.
        self.wait_for_ready(timeout=2.0)

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

    def wait_for_ready(self, timeout: float = 5.0) -> bool:
        """Block until at least one log entry has been buffered, or *timeout* expires.

        Useful after :meth:`start` to ensure the background reader has received
        and parsed the first entries from the log stream before the caller
        begins querying.  Also verifies that the subprocess is alive (its
        ``poll()`` returns ``None``).

        Args:
            timeout: Maximum number of seconds to wait.  Defaults to ``5.0``.

        Returns:
            ``True`` when at least one entry was buffered within *timeout*,
            ``False`` on timeout or if the subprocess exited unexpectedly.
        """
        deadline = time.time() + timeout
        while time.time() < deadline:
            # Check that the subprocess is still running
            if self._process is not None and self._process.poll() is not None:
                # Process exited — no more entries will arrive
                return False
            with self._lock:
                if len(self._buffer) > 0:
                    return True
            time.sleep(0.05)
        with self._lock:
            return len(self._buffer) > 0

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

        ``entry.ingestion_time`` is set by the :class:`LogEntry` dataclass
        ``field(default_factory=time.time)`` at construction time (inside
        ``_parse_json_line``).  This gives a wall-clock timestamp for
        ``recent()`` to use when the log's own timestamp string is stale or
        far in the past.  We do not override it here so that test code that
        constructs entries with explicit timestamps is not affected.

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
            effective_ts = _effective_timestamp(entry)
            if effective_ts < cutoff:
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
        return [e for e in snapshot if _effective_timestamp(e) >= cutoff]

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

    Handles several formats produced by xcrun log stream and test fixtures:
    - ``"2026-03-29T10:00:00.000Z"``
    - ``"2026-03-29 10:00:00.000000+0000"``
    - ``"2026-03-29T10:00:00.000000+00:00"``

    Returns 0.0 if parsing fails (entry treated as infinitely old).
    """
    import datetime
    import re as _re

    if not timestamp:
        return 0.0
    # Normalise: replace trailing Z with +00:00 for fromisoformat compatibility
    if timestamp.endswith("Z"):
        ts = timestamp[:-1] + "+00:00"
    else:
        ts = timestamp
    # Strip sub-second precision beyond 6 digits WITHOUT touching the timezone
    # offset.  A timestamp like "2026-03-29 10:00:00.000000+0000" has its
    # fractional part followed by a timezone offset (+HHMM or +HH:MM).
    if "." in ts:
        dot_idx = ts.index(".")
        # Everything after the dot may be: digits, then optional tz offset
        after_dot = ts[dot_idx + 1 :]
        # Split fractional digits from any trailing timezone offset
        m = _re.match(r"(\d+)(.*)", after_dot)
        if m:
            frac_digits = m.group(1)[:6]  # keep at most 6 fractional digits
            tz_suffix = m.group(2)  # e.g. "+0000", "+00:00", or ""
        else:
            frac_digits = after_dot[:6]
            tz_suffix = ""
        # Normalise +HHMM → +HH:MM so fromisoformat accepts it
        tz_suffix = _re.sub(r"([+-])(\d{2})(\d{2})$", r"\1\2:\3", tz_suffix)
        ts = f"{ts[:dot_idx]}.{frac_digits}{tz_suffix}"
    else:
        # No fractional seconds — still normalise bare +HHMM offset if present
        ts = _re.sub(r"([+-])(\d{2})(\d{2})$", r"\1\2:\3", ts)
    try:
        dt = datetime.datetime.fromisoformat(ts)
        # Treat as UTC if no timezone info
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=datetime.timezone.utc)
        return dt.timestamp()
    except (ValueError, TypeError):
        return 0.0


def _effective_timestamp(entry: "LogEntry") -> float:
    """Return the best-effort effective timestamp for recency filtering.

    Uses the log timestamp when it's valid and consistent with the ingestion
    time (within a 1-hour tolerance).  Falls back to ``ingestion_time`` when:
    - the log timestamp cannot be parsed (returns 0.0), or
    - the log timestamp is more than 3600 seconds older than ingestion time
      (indicating the entry's log clock is stale or from a replayed buffer).

    This prevents real-time entries from being filtered out when the device's
    log daemon emits timestamps that lag behind wall-clock time, while still
    honouring test-injected entries whose log timestamps represent their
    intended age.

    Args:
        entry: The :class:`LogEntry` to evaluate.

    Returns:
        A Unix epoch float suitable for comparing against ``time.time()``.
    """
    log_ts = _parse_timestamp(entry.timestamp)
    ingestion = entry.ingestion_time

    if log_ts == 0.0:
        # Log timestamp unparseable — fall back to ingestion time
        return ingestion if ingestion > 0 else 0.0

    if ingestion > 0 and (ingestion - log_ts) > 3600:
        # Log timestamp is more than 1 hour behind ingestion → use ingestion
        return ingestion

    return log_ts
