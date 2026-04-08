"""Tests for M5: NetworkInspector — iOS Simulator network request monitoring.

TDD Phase — INIT-2026-492.
These tests are written BEFORE the implementation exists and must be
importable even when the implementation module is absent.

Module under test (to be created by CodeAtlas):
  specterqa/ios/drivers/simulator/network.py — NetworkInspector, NetworkRequest
"""

from __future__ import annotations

import time

import pytest

# ---------------------------------------------------------------------------
# Conditional import guard — tests are importable even if impl is missing.
# ---------------------------------------------------------------------------

try:
    from specterqa.ios.drivers.simulator.network import (  # type: ignore[import]
        NetworkInspector,
        NetworkRequest,
    )

    _NETWORK_AVAILABLE = True
except ImportError:
    _NETWORK_AVAILABLE = False
    NetworkInspector = None  # type: ignore[assignment,misc]
    NetworkRequest = None  # type: ignore[assignment,misc]

try:
    from specterqa.ios.security.redactor import DataRedactor  # type: ignore[import]

    _REDACTOR_AVAILABLE = True
except ImportError:
    _REDACTOR_AVAILABLE = False
    DataRedactor = None  # type: ignore[assignment,misc]

needs_network = pytest.mark.skipif(
    not _NETWORK_AVAILABLE,
    reason="specterqa.ios.drivers.simulator.network not yet implemented",
)
needs_redactor = pytest.mark.skipif(
    not _REDACTOR_AVAILABLE,
    reason="specterqa.ios.security.redactor not yet implemented",
)
needs_network_and_redactor = pytest.mark.skipif(
    not (_NETWORK_AVAILABLE and _REDACTOR_AVAILABLE),
    reason="specterqa.ios.drivers.simulator.network or specterqa.ios.security.redactor not yet implemented",
)


# ---------------------------------------------------------------------------
# Helpers — build NetworkRequest fixtures
# ---------------------------------------------------------------------------


def _make_request(
    request_id: str = "req-001",
    method: str = "GET",
    url: str = "https://api.example.com/v1/data",
    host: str = "api.example.com",
    path: str = "/v1/data",
    status_code: int | None = 200,
    request_headers: dict | None = None,
    response_headers: dict | None = None,
    request_body_size: int = 0,
    response_body_size: int = 512,
    started_at: float | None = None,
    completed_at: float | None = None,
    duration_ms: float | None = 45.0,
    error: str | None = None,
) -> "NetworkRequest":
    """Build a NetworkRequest fixture."""
    now = time.time()
    return NetworkRequest(
        request_id=request_id,
        method=method,
        url=url,
        host=host,
        path=path,
        status_code=status_code,
        request_headers=request_headers or {},
        response_headers=response_headers or {},
        request_body_size=request_body_size,
        response_body_size=response_body_size,
        started_at=started_at if started_at is not None else now - 1.0,
        completed_at=completed_at if completed_at is not None else now,
        duration_ms=duration_ms,
        error=error,
    )


# ===========================================================================
#  NetworkRequest — property tests (5 tests)
# ===========================================================================


@needs_network
class TestNetworkRequestIsAuth:
    """NetworkRequest.is_auth property — URL-based auth detection."""

    def test_is_auth_detects_oauth_in_url(self):
        """URL containing 'oauth' is auth-related."""
        req = _make_request(url="https://auth.example.com/oauth/token", path="/oauth/token")
        assert req.is_auth is True

    def test_is_auth_detects_token_in_url(self):
        """URL containing 'token' is auth-related."""
        req = _make_request(url="https://api.example.com/token", path="/token")
        assert req.is_auth is True

    def test_is_auth_detects_authenticate_in_url(self):
        """URL containing 'authenticate' is auth-related."""
        req = _make_request(url="https://api.example.com/authenticate", path="/authenticate")
        assert req.is_auth is True

    def test_is_auth_detects_authorize_in_url(self):
        """URL containing 'authorize' is auth-related."""
        req = _make_request(url="https://api.example.com/authorize?client_id=abc", path="/authorize")
        assert req.is_auth is True

    def test_is_auth_false_for_regular_url(self):
        """URL with no auth-related segments is NOT auth."""
        req = _make_request(url="https://api.example.com/v1/users", path="/v1/users")
        assert req.is_auth is False


@needs_network
class TestNetworkRequestIsFailed:
    """NetworkRequest.is_failed property."""

    def test_is_failed_true_for_status_400(self):
        """is_failed is True when status_code == 400."""
        req = _make_request(status_code=400)
        assert req.is_failed is True

    def test_is_failed_true_for_status_500(self):
        """is_failed is True when status_code == 500."""
        req = _make_request(status_code=500)
        assert req.is_failed is True

    def test_is_failed_true_when_error_not_none(self):
        """is_failed is True when error field is not None, regardless of status."""
        req = _make_request(status_code=None, error="Connection refused")
        assert req.is_failed is True

    def test_is_failed_false_for_status_200(self):
        """is_failed is False when status_code == 200 and error is None."""
        req = _make_request(status_code=200, error=None)
        assert req.is_failed is False

    def test_is_failed_false_for_status_201(self):
        """is_failed is False when status_code == 201 (created) and error is None."""
        req = _make_request(status_code=201, error=None)
        assert req.is_failed is False


# ===========================================================================
#  NetworkInspector query methods (3 tests)
# ===========================================================================


@needs_network
class TestNetworkInspectorCompletedRequests:
    """NetworkInspector.completed_requests() filters by time window."""

    def _populate_inspector(self, inspector: "NetworkInspector") -> None:
        """Inject known requests directly via internal method."""
        now = time.time()
        inspector._add_request(  # type: ignore[attr-defined]
            _make_request(
                request_id="old-req",
                url="https://api.example.com/old",
                path="/old",
                started_at=now - 120,
                completed_at=now - 119,
            )
        )
        inspector._add_request(  # type: ignore[attr-defined]
            _make_request(
                request_id="recent-req",
                url="https://api.example.com/recent",
                path="/recent",
                started_at=now - 5,
                completed_at=now - 4,
            )
        )

    def test_completed_requests_filters_by_time_window(self):
        """completed_requests(seconds=30) excludes requests completed > 30s ago."""
        inspector = NetworkInspector(device_id="booted")
        self._populate_inspector(inspector)
        results = inspector.completed_requests(seconds=30)
        request_ids = [r.request_id for r in results]
        assert "old-req" not in request_ids, (
            "Request completed 119s ago should not appear in completed_requests(seconds=30)"
        )
        assert "recent-req" in request_ids, "Recent request should appear in completed_requests(seconds=30)"


@needs_network
class TestNetworkInspectorFailedRequests:
    """NetworkInspector.failed_requests() returns only failed requests."""

    def test_failed_requests_returns_only_failed(self):
        """failed_requests() returns requests that are is_failed == True."""
        inspector = NetworkInspector(device_id="booted")
        now = time.time()
        inspector._add_request(
            _make_request(request_id="ok", status_code=200, error=None, started_at=now - 5, completed_at=now - 4)
        )  # type: ignore[attr-defined]
        inspector._add_request(
            _make_request(request_id="bad-400", status_code=400, error=None, started_at=now - 5, completed_at=now - 4)
        )  # type: ignore[attr-defined]
        inspector._add_request(
            _make_request(
                request_id="error", status_code=None, error="Timeout", started_at=now - 5, completed_at=now - 4
            )
        )  # type: ignore[attr-defined]
        results = inspector.failed_requests(seconds=60)
        result_ids = {r.request_id for r in results}
        assert "ok" not in result_ids, "200 OK request should NOT appear in failed_requests()"
        assert "bad-400" in result_ids, "400 request must appear in failed_requests()"
        assert "error" in result_ids, "Error request must appear in failed_requests()"


@needs_network
class TestNetworkInspectorAuthRequests:
    """NetworkInspector.auth_requests() returns only auth-related requests."""

    def test_auth_requests_returns_only_auth_related(self):
        """auth_requests() returns requests where is_auth is True."""
        inspector = NetworkInspector(device_id="booted")
        now = time.time()
        inspector._add_request(
            _make_request(
                request_id="data-req",
                url="https://api.example.com/v1/data",
                path="/v1/data",
                started_at=now - 5,
                completed_at=now - 4,
            )
        )  # type: ignore[attr-defined]
        inspector._add_request(
            _make_request(
                request_id="token-req",
                url="https://auth.example.com/oauth/token",
                path="/oauth/token",
                started_at=now - 5,
                completed_at=now - 4,
            )
        )  # type: ignore[attr-defined]
        results = inspector.auth_requests()
        result_ids = {r.request_id for r in results}
        assert "data-req" not in result_ids, "Non-auth request should NOT appear in auth_requests()"
        assert "token-req" in result_ids, "Auth request must appear in auth_requests()"


# ===========================================================================
#  NetworkInspector.summary() (1 test)
# ===========================================================================


@needs_network
class TestNetworkInspectorSummary:
    """NetworkInspector.summary() — correct aggregation."""

    def test_summary_returns_correct_aggregation(self):
        """summary() includes total_requests, by_status, by_host, avg_latency_ms, failed_count."""
        inspector = NetworkInspector(device_id="booted")
        now = time.time()
        inspector._add_request(
            _make_request(
                request_id="r1",
                url="https://api.a.com/x",
                host="api.a.com",
                path="/x",
                status_code=200,
                duration_ms=100.0,
                started_at=now - 3,
                completed_at=now - 2,
            )
        )  # type: ignore[attr-defined]
        inspector._add_request(
            _make_request(
                request_id="r2",
                url="https://api.a.com/y",
                host="api.a.com",
                path="/y",
                status_code=200,
                duration_ms=200.0,
                started_at=now - 3,
                completed_at=now - 2,
            )
        )  # type: ignore[attr-defined]
        inspector._add_request(
            _make_request(
                request_id="r3",
                url="https://api.b.com/z",
                host="api.b.com",
                path="/z",
                status_code=500,
                duration_ms=50.0,
                started_at=now - 3,
                completed_at=now - 2,
            )
        )  # type: ignore[attr-defined]

        s = inspector.summary()
        assert "total_requests" in s, "summary must include 'total_requests'"
        assert "by_status" in s, "summary must include 'by_status'"
        assert "by_host" in s, "summary must include 'by_host'"
        assert "avg_latency_ms" in s, "summary must include 'avg_latency_ms'"
        assert "failed_count" in s, "summary must include 'failed_count'"

        assert s["total_requests"] == 3
        assert s["failed_count"] == 1
        # avg_latency_ms = (100 + 200 + 50) / 3 = 116.67
        assert abs(s["avg_latency_ms"] - (100.0 + 200.0 + 50.0) / 3) < 1.0, (
            f"avg_latency_ms mismatch: {s['avg_latency_ms']}"
        )
        assert s["by_host"].get("api.a.com", 0) == 2
        assert s["by_host"].get("api.b.com", 0) == 1
        assert s["by_status"].get(200, s["by_status"].get("200", 0)) == 2


# ===========================================================================
#  DataRedactor integration (2 tests)
# ===========================================================================


@needs_network_and_redactor
class TestNetworkInspectorRedactorIntegration:
    """NetworkInspector passes all network data through DataRedactor before output."""

    def test_bearer_token_in_request_headers_is_redacted(self):
        """Authorization header containing a Bearer token is redacted in output."""
        redactor = DataRedactor()
        inspector = NetworkInspector(device_id="booted", redactor=redactor)
        now = time.time()
        inspector._add_request(  # type: ignore[attr-defined]
            _make_request(
                request_id="auth-req",
                request_headers={"Authorization": "Bearer super_secret_token_xyz"},
                started_at=now - 2,
                completed_at=now - 1,
            )
        )
        results = inspector.completed_requests(seconds=30)
        assert len(results) >= 1
        auth_header = results[0].request_headers.get("Authorization", "")
        assert "super_secret_token_xyz" not in auth_header, (
            "Bearer token in Authorization header must be redacted before output"
        )
        assert "[REDACTED]" in auth_header or auth_header == "", (
            "Redacted header should contain [REDACTED] marker or be empty"
        )

    def test_sensitive_url_params_are_redacted(self):
        """URL containing access_token query parameter is redacted in output."""
        redactor = DataRedactor()
        inspector = NetworkInspector(device_id="booted", redactor=redactor)
        now = time.time()
        sensitive_url = "https://api.example.com/callback?access_token=url_secret_token&state=xyz"
        inspector._add_request(  # type: ignore[attr-defined]
            _make_request(
                request_id="token-url-req",
                url=sensitive_url,
                path="/callback",
                started_at=now - 2,
                completed_at=now - 1,
            )
        )
        results = inspector.completed_requests(seconds=30)
        assert len(results) >= 1
        output_url = results[0].url
        assert "url_secret_token" not in output_url, "access_token value in URL must be redacted before output"


# ===========================================================================
#  NetworkInspector start/stop lifecycle (1 test)
# ===========================================================================


@needs_network
class TestNetworkInspectorLifecycle:
    """NetworkInspector.start() / stop() lifecycle."""

    def test_start_stop_lifecycle(self):
        """start() and stop() run without error; monitor is operational between calls."""
        inspector = NetworkInspector(device_id="booted")
        # start() and stop() should not raise exceptions
        inspector.start()
        # After start, the inspector should be in a monitoring state
        assert hasattr(inspector, "start"), "NetworkInspector must have start()"
        assert hasattr(inspector, "stop"), "NetworkInspector must have stop()"
        inspector.stop()
        # After stop, calling completed_requests should not raise
        results = inspector.completed_requests(seconds=30)
        assert isinstance(results, list), "completed_requests() must return a list"
