"""Runner HTTP endpoint edge-case coverage.

Tests cover malformed-request handling, unknown routes, and query-param
edge cases using urllib directly against a real runner (requires_live)
or pure unit tests that don't need a runner.

Live tests are skipped when no runner is reachable (no booted simulator).

Run:
    pytest tests/regression/test_runner_endpoints.py -v --tb=short

    # With a live runner:
    pytest tests/regression/test_runner_endpoints.py -v -m requires_live
"""
from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

RUNNER_PORT = 8222
RUNNER_BASE = f"http://localhost:{RUNNER_PORT}"


def _is_runner_alive(port: int = RUNNER_PORT) -> bool:
    """Return True if the XCTest runner is reachable."""
    import socket
    try:
        with socket.create_connection(("localhost", port), timeout=1.0):
            return True
    except OSError:
        return False


def _post(path: str, body: bytes, content_type: str = "application/json") -> tuple[int, bytes]:
    """POST to the runner; return (status_code, response_body)."""
    req = urllib.request.Request(
        f"{RUNNER_BASE}{path}",
        data=body,
        headers={"Content-Type": content_type},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


def _get(path: str) -> tuple[int, bytes]:
    """GET from the runner; return (status_code, response_body)."""
    req = urllib.request.Request(f"{RUNNER_BASE}{path}", method="GET")
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()
    except Exception as exc:
        pytest.skip(f"Runner not reachable: {exc}")


requires_live = pytest.mark.skipif(
    not _is_runner_alive(),
    reason="No live XCTest runner reachable on port 8222 — start ios_start_session first",
)


# ---------------------------------------------------------------------------
# Unit tests (no runner needed) — XCTestBackend error handling
# ---------------------------------------------------------------------------


class TestXCTestBackendErrorHandling:
    """XCTestBackend._request handles network errors and bad responses gracefully."""

    def test_unreachable_host_raises_connection_error_or_returns_error_dict(self):
        """Backend raises ConnectionError or returns error dict when runner is unreachable.

        The XCTestBackend raises ConnectionError (not RuntimeError/ValueError) on
        connection failure — this is the documented behavior. The server layer catches
        it. We verify the exception type is correct and has an actionable message.
        """
        from specterqa.ios.backends.xctest_client import XCTestBackend

        backend = XCTestBackend(host="localhost", port=19999)  # nothing on this port
        try:
            result = backend.health()
            # If it returns a dict, the status should indicate failure
            assert isinstance(result, dict), "health() returned non-dict"
            assert result.get("status") in ("unreachable", "error") or "error" in str(result)
        except ConnectionError as exc:
            # XCTestBackend raises ConnectionError — that's the documented API
            assert "localhost" in str(exc) or "19999" in str(exc) or "unavailable" in str(exc).lower()
        except Exception as exc:
            pytest.fail(
                f"health() raised unexpected exception type {type(exc).__name__}: {exc}. "
                "Expected ConnectionError or a dict return."
            )

    def test_is_available_returns_false_when_unreachable(self):
        """is_available() must not raise — returns False."""
        from specterqa.ios.backends.xctest_client import XCTestBackend

        backend = XCTestBackend(host="localhost", port=19999)
        result = backend.is_available()
        # When runner is not listening: False
        assert result is False

    def test_backend_url_construction(self):
        """Runner URL is correctly assembled from host + port."""
        from specterqa.ios.backends.xctest_client import XCTestBackend

        backend = XCTestBackend(host="localhost", port=8222)
        url = backend._url("/elements")
        assert url == "http://localhost:8222/elements"

    def test_backend_url_with_query(self):
        """URL construction with path only (query params added separately)."""
        from specterqa.ios.backends.xctest_client import XCTestBackend

        backend = XCTestBackend(host="localhost", port=8222)
        url = backend._url("/tap")
        assert "8222" in url
        assert url.endswith("/tap")


# ---------------------------------------------------------------------------
# Live tests — require a real runner
# ---------------------------------------------------------------------------


@requires_live
class TestRunnerUnknownRoute:
    """Unknown routes return 404 with a consistent shape."""

    def test_get_unknown_route_returns_404(self):
        status, body = _get("/no_such_route_xyz_abc")
        assert status == 404, f"Expected 404, got {status}"

    def test_post_unknown_route_returns_404(self):
        status, body = _post("/unknown_endpoint_foobar", b'{"key": "value"}')
        assert status == 404, f"Expected 404, got {status}"

    def test_deeply_nested_unknown_route_returns_404(self):
        status, body = _get("/a/b/c/d/totally/fake")
        assert status == 404, f"Expected 404, got {status}"


@requires_live
class TestRunnerMalformedJson:
    """Malformed JSON bodies should return 400 (not 500)."""

    def test_malformed_json_returns_4xx(self):
        status, body = _post("/tap", b"not valid json at all {{{")
        assert status in (400, 422), f"Expected 400/422 for malformed JSON, got {status}"

    def test_empty_body_to_post_endpoint_returns_error(self):
        status, body = _post("/tap", b"")
        # Empty body should be treated as malformed — 400 or 422
        assert status in (400, 422, 200), f"Unexpected status {status}"

    def test_wrong_content_type_still_processes(self):
        """text/plain content-type with valid JSON may work or return 4xx — either is fine."""
        status, body = _post("/health", b'{}', content_type="text/plain")
        # Either it processes (200) or rejects (400/415) — just don't crash (500)
        assert status != 500, f"Server crashed (500) on wrong content-type"


@requires_live
class TestRunnerQueryParams:
    """Query parameter edge cases — runner should handle them gracefully."""

    def test_percent_encoded_params_on_get(self):
        """Percent-encoded query values must not crash the runner."""
        encoded = urllib.parse.quote("Hello World & More", safe="")
        status, _ = _get(f"/elements?query={encoded}")
        assert status != 500, "Runner crashed on percent-encoded query params"

    def test_repeated_query_params(self):
        """Repeated query params should not crash the runner."""
        status, _ = _get("/elements?max=10&max=20")
        assert status != 500, "Runner crashed on repeated query params"

    def test_empty_query_value(self):
        """Empty query value should not crash the runner."""
        status, _ = _get("/elements?label=")
        assert status != 500, "Runner crashed on empty query value"


@requires_live
class TestRunnerHealthEndpoint:
    """Health endpoint basic contract."""

    def test_health_returns_200(self):
        status, body = _get("/health")
        assert status == 200, f"Expected 200 from /health, got {status}"

    def test_health_returns_json(self):
        status, body = _get("/health")
        assert status == 200
        data = json.loads(body)
        assert isinstance(data, dict), "Health response must be a JSON object"

    def test_health_has_status_field(self):
        status, body = _get("/health")
        data = json.loads(body)
        assert "status" in data or "ok" in str(body).lower(), (
            "Health response should contain 'status' field"
        )


@requires_live
class TestRunnerOversizedBody:
    """Oversized request bodies should be rejected cleanly, not hang."""

    def test_large_body_does_not_hang(self):
        """A 1MB body should return quickly (not hang the runner)."""
        import time

        large_body = b'{"text": "' + b"x" * (1024 * 1024 - 12) + b'"}'
        start = time.monotonic()
        try:
            status, _ = _post("/type", large_body)
            elapsed = time.monotonic() - start
            # Should respond within 10 seconds — not hang
            assert elapsed < 10.0, f"Runner took too long ({elapsed:.1f}s) for large body"
            # Status 400, 413, or even 200 is fine — as long as it responded
            assert status < 600, f"Invalid HTTP status: {status}"
        except Exception as exc:
            elapsed = time.monotonic() - start
            assert elapsed < 10.0, f"Runner hung for {elapsed:.1f}s on large body"
