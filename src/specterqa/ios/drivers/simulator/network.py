"""M5: NetworkInspector — iOS Simulator network request tracking.

Captures and tracks network requests made by an app running in the iOS
Simulator using two complementary data sources:

1. **CFNetwork / URLSession log watcher** — registers a :class:`LogWatcher`
   on an active :class:`ConsoleMonitor` to parse HTTP activity from
   ``os_log`` entries emitted by ``CFNetwork`` and ``URLSession``.

2. **nettop bandwidth sampling** — a background thread polls
   ``nettop -l 2 -P -n`` to collect per-process byte-in / byte-out counters
   and derive real-time throughput.

Both sources feed into a shared :class:`NetworkSnapshot` that the MCP
``ios_network`` tool exposes to Claude.

INIT-2026-492 — SpecterQA iOS Simulator Driver, Phase 2.
"""

from __future__ import annotations

import logging
import re
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Optional

logger = logging.getLogger("specterqa.ios.drivers.simulator.network")


# ---------------------------------------------------------------------------
# NetworkRequest
# ---------------------------------------------------------------------------


@dataclass
class NetworkRequest:
    """A single captured network request/response pair.

    Args:
        request_id: Unique identifier for this request.
        method: HTTP method (``"GET"``, ``"POST"``, etc.).
        url: Full request URL.
        host: Hostname extracted from the URL.
        path: Path component of the URL.
        status_code: HTTP response status code, or ``None`` if the request
            failed before receiving a response.
        request_headers: Dict of request headers.
        response_headers: Dict of response headers.
        request_body_size: Size of the request body in bytes.
        response_body_size: Size of the response body in bytes.
        started_at: Unix timestamp when the request was initiated.
        completed_at: Unix timestamp when the response was received (or the
            error occurred).
        duration_ms: Round-trip duration in milliseconds, or ``None``.
        error: Error message if the request failed, otherwise ``None``.
    """

    request_id: str
    method: str
    url: str
    host: str
    path: str
    status_code: Optional[int]
    request_headers: dict[str, Any]
    response_headers: dict[str, Any]
    request_body_size: int
    response_body_size: int
    started_at: float
    completed_at: Optional[float]
    duration_ms: Optional[float]
    error: Optional[str]

    # ------------------------------------------------------------------
    # Derived properties
    # ------------------------------------------------------------------

    @property
    def is_auth(self) -> bool:
        """True when the URL contains auth-related path segments (case-insensitive)."""
        return bool(re.search(r"(?i)(oauth|token|authenticate|authorize)", self.url))

    @property
    def is_failed(self) -> bool:
        """True when the request has a 4xx/5xx status code or a non-None error."""
        if self.error is not None:
            return True
        if self.status_code is not None and self.status_code >= 400:
            return True
        return False


# ---------------------------------------------------------------------------
# NetworkSnapshot — point-in-time bandwidth + request summary
# ---------------------------------------------------------------------------


@dataclass
class NetworkSnapshot:
    """Point-in-time summary of network activity for the app under test.

    Args:
        bytes_in: Cumulative bytes received by the process since monitoring
            started (from nettop, best-effort).
        bytes_out: Cumulative bytes sent by the process since monitoring
            started (from nettop, best-effort).
        throughput_in: Bytes per second received (delta from last nettop sample).
        throughput_out: Bytes per second sent (delta from last nettop sample).
        requests: Recent :class:`NetworkRequest` objects from CFNetwork log
            parsing, newest first.
        active_connections: Estimated number of in-flight connections derived
            from the request buffer (started but not yet completed).
        nettop_available: Whether nettop produced usable data for this snapshot.
    """

    bytes_in: int = 0
    bytes_out: int = 0
    throughput_in: float = 0.0
    throughput_out: float = 0.0
    requests: list = field(default_factory=list)
    active_connections: int = 0
    nettop_available: bool = False


# ---------------------------------------------------------------------------
# NetworkInspector
# ---------------------------------------------------------------------------


class NetworkInspector:
    """Tracks network requests made by an app running in the iOS Simulator.

    Uses two complementary data sources:

    1. **CFNetwork / URLSession log watcher** — parses HTTP activity from
       ``os_log`` entries emitted by CFNetwork.  Registered via
       :meth:`setup_log_watcher` on an active :class:`ConsoleMonitor`.

    2. **nettop bandwidth sampling** — background thread polls ``nettop``
       for per-process byte counters.  Started automatically from
       :meth:`start` if a ``pid`` was provided.

    Requests injected via :meth:`_add_request` are also accepted (for
    driver-level hook-based capture).  All output methods optionally pass
    data through a :class:`~specterqa.ios.security.redactor.DataRedactor`.

    Args:
        device_id: Simulator device UDID or ``"booted"`` (default).
        pid: Optional process ID of the app under test.  When provided,
            nettop bandwidth monitoring is enabled automatically.
        redactor: Optional :class:`~specterqa.ios.security.redactor.DataRedactor`
            instance.  When provided, all output methods pass request data
            through :meth:`~specterqa.ios.security.redactor.DataRedactor.redact_dict`
            before returning it.
    """

    # Regex patterns for CFNetwork / URLSession log lines
    # Matches lines like: "Task <ABC123>.<N>] sending request, URL: https://..."
    _URL_PATTERN = re.compile(
        r"https?://[^\s\"'>)]+", re.IGNORECASE
    )
    _STATUS_PATTERN = re.compile(
        r"(?:response|status)[^\d]*(\d{3})", re.IGNORECASE
    )
    _METHOD_PATTERN = re.compile(
        r"\b(GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS)\b", re.IGNORECASE
    )

    def __init__(
        self,
        device_id: str = "booted",
        pid: Optional[int] = None,
        redactor: Any = None,
    ) -> None:
        self._device_id = device_id
        self._pid = pid
        self._redactor = redactor

        self._lock = threading.Lock()
        self._requests: list[NetworkRequest] = []
        self._running = False

        # nettop state
        self._bytes_in: int = 0
        self._bytes_out: int = 0
        self._throughput_in: float = 0.0
        self._throughput_out: float = 0.0
        self._nettop_available: bool = False
        self._nettop_thread: Optional[threading.Thread] = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Begin monitoring.

        If a ``pid`` was provided at construction, starts the nettop
        background bandwidth-sampling thread.  The CFNetwork log watcher
        must be registered separately via :meth:`setup_log_watcher` once
        a :class:`ConsoleMonitor` is available.
        """
        self._running = True
        if self._pid is not None:
            self._nettop_thread = threading.Thread(
                target=self._nettop_loop, daemon=True, name="specterqa-nettop"
            )
            self._nettop_thread.start()

    def stop(self) -> None:
        """Stop monitoring (nettop thread will exit on next iteration)."""
        self._running = False

    # ------------------------------------------------------------------
    # CFNetwork log watcher
    # ------------------------------------------------------------------

    def setup_log_watcher(self, console_monitor: Any) -> None:
        """Register a :class:`LogWatcher` on *console_monitor* for HTTP traffic.

        Watches for log entries whose subsystem contains ``CFNetwork`` or
        ``NSURLSession``, or whose message contains an HTTP URL.  Matching
        entries are parsed and stored as :class:`NetworkRequest` objects.

        Args:
            console_monitor: An active :class:`ConsoleMonitor` instance.
        """
        try:
            from specterqa.ios.drivers.simulator.console import LogWatcher
        except ImportError:
            logger.warning("ConsoleMonitor not available — CFNetwork watcher skipped")
            return

        watcher = LogWatcher(
            name="specterqa-network",
            pattern=r"(?i)(https?://|CFNetwork|NSURLSession|URLSession|sendingRequest|response.*status)",
            callback=self._on_log_entry,
        )
        try:
            console_monitor.add_watcher(watcher)
            logger.debug("CFNetwork log watcher registered on ConsoleMonitor")
        except Exception as exc:
            logger.warning("Failed to register CFNetwork log watcher: %s", exc)

    def _on_log_entry(self, entry: Any) -> None:
        """Callback invoked by ConsoleMonitor for each matching log line.

        Parses the entry for HTTP URLs, methods, and status codes and
        synthesises a :class:`NetworkRequest` to store in the request buffer.

        Args:
            entry: A :class:`LogEntry` from the ConsoleMonitor.
        """
        if not self._running:
            return

        message = getattr(entry, "message", "") or ""
        subsystem = getattr(entry, "subsystem", "") or ""

        # Only process entries that look network-related
        cfnet_subsystem = bool(
            re.search(r"(?i)(CFNetwork|NSURLSession|URLSession)", subsystem)
        )
        url_match = self._URL_PATTERN.search(message)

        if not cfnet_subsystem and not url_match:
            return

        if url_match is None:
            return  # Can't build a meaningful request without a URL

        url = url_match.group(0).rstrip(".,;)")
        try:
            from urllib.parse import urlparse
            parsed = urlparse(url)
            host = parsed.netloc or url
            path = parsed.path or "/"
        except Exception:
            host = url
            path = "/"

        method_match = self._METHOD_PATTERN.search(message)
        method = method_match.group(1).upper() if method_match else "GET"

        status_match = self._STATUS_PATTERN.search(message)
        status_code = int(status_match.group(1)) if status_match else None

        ts = time.time()
        request = NetworkRequest(
            request_id=str(uuid.uuid4()),
            method=method,
            url=url,
            host=host,
            path=path,
            status_code=status_code,
            request_headers={},
            response_headers={},
            request_body_size=0,
            response_body_size=0,
            started_at=ts,
            completed_at=ts if status_code is not None else None,
            duration_ms=None,
            error=None,
        )
        self._add_request(request)

    # ------------------------------------------------------------------
    # nettop bandwidth sampling
    # ------------------------------------------------------------------

    def _nettop_loop(self) -> None:
        """Background thread: poll nettop for per-process byte counters.

        Runs ``nettop -l 2 -P -n`` which emits two samples separated by a
        1-second interval and exits.  We parse bytes_in / bytes_out for the
        target PID from the second (delta) sample and repeat every 5 seconds.
        """
        while self._running:
            try:
                self._sample_nettop()
            except Exception as exc:
                logger.debug("nettop sample failed: %s", exc)
            # Wait before the next sample; exit early if stopped
            for _ in range(50):
                if not self._running:
                    return
                time.sleep(0.1)

    def _sample_nettop(self) -> None:
        """Run one nettop poll and update bandwidth counters."""
        if self._pid is None:
            return

        result = subprocess.run(
            ["nettop", "-l", "2", "-P", "-n", "-p", str(self._pid)],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0 or not result.stdout.strip():
            return

        lines = result.stdout.splitlines()
        # nettop -l 2 outputs two full tables.  The SECOND table is the delta.
        # Each data line looks like:
        #   <proc>.<pid>   bytes_in   bytes_out   ...
        # We find the line for our PID in the second block.
        pid_str = f".{self._pid}"
        found_second_block = False
        header_count = 0

        cumulative_in = 0
        cumulative_out = 0
        delta_in = 0.0
        delta_out = 0.0
        found = False

        for line in lines:
            # nettop headers typically start with "time" or are blank
            if line.startswith("time") or (not line.strip() and found_second_block):
                header_count += 1
                if header_count >= 2:
                    found_second_block = True
                continue

            if pid_str not in line:
                continue

            parts = line.split()
            if len(parts) < 3:
                continue

            try:
                b_in = int(parts[1])
                b_out = int(parts[2])
            except (ValueError, IndexError):
                continue

            if found_second_block:
                delta_in = float(b_in)
                delta_out = float(b_out)
                found = True
                break
            else:
                cumulative_in = b_in
                cumulative_out = b_out
                found = True

        if found:
            with self._lock:
                self._bytes_in += cumulative_in
                self._bytes_out += cumulative_out
                self._throughput_in = delta_in
                self._throughput_out = delta_out
                self._nettop_available = True

    # ------------------------------------------------------------------
    # Internal: request ingestion
    # ------------------------------------------------------------------

    def _add_request(self, request: NetworkRequest) -> None:
        """Store *request* in the internal list.

        Thread-safe: protected by ``_lock``.  Caps the buffer at 500 entries
        (oldest dropped first) to bound memory usage.

        Args:
            request: The :class:`NetworkRequest` to record.
        """
        with self._lock:
            self._requests.append(request)
            if len(self._requests) > 500:
                self._requests = self._requests[-500:]

    # ------------------------------------------------------------------
    # Snapshot
    # ------------------------------------------------------------------

    def snapshot(self, seconds: float = 30.0) -> NetworkSnapshot:
        """Return a point-in-time :class:`NetworkSnapshot`.

        Args:
            seconds: Time window for recent requests (default 30s).

        Returns:
            A :class:`NetworkSnapshot` with bandwidth counters and recent
            requests from the CFNetwork log watcher.
        """
        recent = self.completed_requests(seconds)
        with self._lock:
            in_flight = [
                r for r in self._requests
                if r.completed_at is None and (time.time() - r.started_at) < seconds
            ]
            bytes_in = self._bytes_in
            bytes_out = self._bytes_out
            throughput_in = self._throughput_in
            throughput_out = self._throughput_out
            nettop_available = self._nettop_available

        return NetworkSnapshot(
            bytes_in=bytes_in,
            bytes_out=bytes_out,
            throughput_in=throughput_in,
            throughput_out=throughput_out,
            requests=recent,
            active_connections=len(in_flight),
            nettop_available=nettop_available,
        )

    # ------------------------------------------------------------------
    # Query methods
    # ------------------------------------------------------------------

    def completed_requests(self, seconds: float) -> list[NetworkRequest]:
        """Return completed requests within the last *seconds* time window.

        When a redactor is configured, sensitive header values and URL
        parameters are redacted before the requests are returned.

        Args:
            seconds: Time window in seconds.  Requests whose
                ``completed_at`` timestamp is older than ``now - seconds``
                are excluded.

        Returns:
            List of :class:`NetworkRequest` objects, in insertion order.
        """
        cutoff = time.time() - seconds
        with self._lock:
            snapshot = list(self._requests)

        results: list[NetworkRequest] = []
        for req in snapshot:
            if req.completed_at is None or req.completed_at < cutoff:
                continue
            results.append(self._redact_request(req) if self._redactor else req)
        return results

    def failed_requests(self, seconds: float) -> list[NetworkRequest]:
        """Return failed requests within the last *seconds* time window.

        Args:
            seconds: Time window in seconds.

        Returns:
            List of :class:`NetworkRequest` objects where
            :attr:`~NetworkRequest.is_failed` is ``True``.
        """
        cutoff = time.time() - seconds
        with self._lock:
            snapshot = list(self._requests)

        results: list[NetworkRequest] = []
        for req in snapshot:
            completed = req.completed_at if req.completed_at is not None else req.started_at
            if completed < cutoff:
                continue
            if not req.is_failed:
                continue
            results.append(self._redact_request(req) if self._redactor else req)
        return results

    def auth_requests(self) -> list[NetworkRequest]:
        """Return all stored requests where :attr:`~NetworkRequest.is_auth` is ``True``.

        No time filter is applied — all buffered auth-related requests are
        returned.

        Returns:
            List of :class:`NetworkRequest` objects where ``is_auth`` is ``True``.
        """
        with self._lock:
            snapshot = list(self._requests)

        results: list[NetworkRequest] = []
        for req in snapshot:
            if not req.is_auth:
                continue
            results.append(self._redact_request(req) if self._redactor else req)
        return results

    def summary(self) -> dict[str, Any]:
        """Return aggregate statistics for all stored requests.

        Returns:
            Dict with keys:
            - ``total_requests``: int — total stored requests.
            - ``by_status``: dict[int, int] — count per status code.
            - ``by_host``: dict[str, int] — count per host.
            - ``avg_latency_ms``: float — mean duration across requests that
              have a non-None ``duration_ms``.  ``0.0`` when no data exists.
            - ``failed_count``: int — number of failed requests.
        """
        with self._lock:
            snapshot = list(self._requests)

        by_status: dict[int, int] = {}
        by_host: dict[str, int] = {}
        total_latency = 0.0
        latency_count = 0
        failed_count = 0

        for req in snapshot:
            # by_status
            if req.status_code is not None:
                by_status[req.status_code] = by_status.get(req.status_code, 0) + 1
            # by_host
            by_host[req.host] = by_host.get(req.host, 0) + 1
            # latency
            if req.duration_ms is not None:
                total_latency += req.duration_ms
                latency_count += 1
            # failed
            if req.is_failed:
                failed_count += 1

        avg_latency = total_latency / latency_count if latency_count > 0 else 0.0

        return {
            "total_requests": len(snapshot),
            "by_status": by_status,
            "by_host": by_host,
            "avg_latency_ms": avg_latency,
            "failed_count": failed_count,
        }

    # ------------------------------------------------------------------
    # Private: redaction helper
    # ------------------------------------------------------------------

    def _redact_request(self, req: NetworkRequest) -> NetworkRequest:
        """Return a copy of *req* with headers and URL redacted.

        Uses :meth:`~specterqa.ios.security.redactor.DataRedactor.redact_dict`
        for header dicts and
        :meth:`~specterqa.ios.security.redactor.DataRedactor.redact` for the
        URL string.

        Args:
            req: The original :class:`NetworkRequest`.

        Returns:
            A new :class:`NetworkRequest` with sensitive fields redacted.
            The original is not mutated.
        """
        import dataclasses

        redacted_req_headers = self._redactor.redact_dict(req.request_headers)
        redacted_resp_headers = self._redactor.redact_dict(req.response_headers)
        redacted_url = self._redactor.redact(req.url)

        return dataclasses.replace(
            req,
            request_headers=redacted_req_headers,
            response_headers=redacted_resp_headers,
            url=redacted_url,
        )
