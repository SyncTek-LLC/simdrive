"""M5: NetworkInspector — iOS Simulator network request tracking.

Captures and tracks network requests made by an app running in the iOS
Simulator, with optional :class:`~specterqa.ios.security.redactor.DataRedactor`
integration to sanitise sensitive data before output.

INIT-2026-492 — SpecterQA iOS Simulator Driver, Phase 2.
"""

from __future__ import annotations

import re
import time
import threading
from dataclasses import dataclass, field
from typing import Any, List, Optional


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
        return bool(
            re.search(r"(?i)(oauth|token|authenticate|authorize)", self.url)
        )

    @property
    def is_failed(self) -> bool:
        """True when the request has a 4xx/5xx status code or a non-None error."""
        if self.error is not None:
            return True
        if self.status_code is not None and self.status_code >= 400:
            return True
        return False


# ---------------------------------------------------------------------------
# NetworkInspector
# ---------------------------------------------------------------------------

class NetworkInspector:
    """Tracks network requests made by an app running in the iOS Simulator.

    Requests are injected via :meth:`_add_request` (used by the driver's
    network-intercept layer) or directly in tests.  Output methods optionally
    pass all data through a :class:`~specterqa.ios.security.redactor.DataRedactor`
    to redact sensitive headers and URL parameters before returning results.

    Args:
        device_id: Simulator device UDID or ``"booted"`` (default).
        redactor: Optional :class:`~specterqa.ios.security.redactor.DataRedactor`
            instance.  When provided, all output methods pass request data
            through :meth:`~specterqa.ios.security.redactor.DataRedactor.redact_dict`
            before returning it.
    """

    def __init__(
        self,
        device_id: str = "booted",
        redactor: Any = None,
    ) -> None:
        self._device_id = device_id
        self._redactor = redactor

        self._lock = threading.Lock()
        self._requests: list[NetworkRequest] = []
        self._running = False

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Begin monitoring.  No-op in the current implementation (hook-based)."""
        self._running = True

    def stop(self) -> None:
        """Stop monitoring."""
        self._running = False

    # ------------------------------------------------------------------
    # Internal: request ingestion
    # ------------------------------------------------------------------

    def _add_request(self, request: NetworkRequest) -> None:
        """Store *request* in the internal list.

        Thread-safe: protected by ``_lock``.

        Args:
            request: The :class:`NetworkRequest` to record.
        """
        with self._lock:
            self._requests.append(request)

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
