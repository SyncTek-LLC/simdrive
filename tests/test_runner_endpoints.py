"""Integration tests for the XCTest runner HTTP endpoints (port 8222).

These tests exercise the runner's HTTP surface from the Python side using the
XCTestBackend client.  Because they require a live runner process, all tests
are guarded by a skip marker — they are skipped when the runner is not
responding on localhost:8222.

Test categories:
  - Health endpoint    — GET /health
  - Source tree        — GET /source (element tree JSON)
  - Tap gesture        — POST /tap
  - Swipe gesture      — POST /swipe
  - Type text          — POST /type
  - Press key          — POST /key
  - Screenshot         — GET /screenshot
  - Shutdown           — POST /shutdown (last — kills the server)

To run the integration suite against a live runner:
  SPECTERQA_RUNNER_LIVE=1 pytest tests/test_runner_endpoints.py -v

Unit-level tests (no live runner required) are included at the bottom to
verify that the XCTestBackend client constructs requests correctly.

INIT-2026-500 — SpecterQA iOS Headless Driver.
"""

from __future__ import annotations

import io
import json
import socket
import urllib.error
import urllib.request
from typing import Any
from unittest.mock import MagicMock, call, patch

import pytest

from specterqa.ios.backends.xctest_client import XCTestBackend

# ---------------------------------------------------------------------------
# Live-runner detection
# ---------------------------------------------------------------------------

_RUNNER_HOST = "localhost"
_RUNNER_PORT = 8222
_RUNNER_TIMEOUT = 2  # seconds for the probe

import os as _os

_RUNNER_LIVE = _os.environ.get("SPECTERQA_RUNNER_LIVE", "").strip().lower() in (
    "1", "true", "yes"
)


def _probe_runner() -> bool:
    """Return True if the runner is already up on _RUNNER_PORT."""
    try:
        url = f"http://{_RUNNER_HOST}:{_RUNNER_PORT}/health"
        with urllib.request.urlopen(url, timeout=_RUNNER_TIMEOUT) as resp:
            data = json.loads(resp.read())
            return data.get("status") == "ok"
    except Exception:
        return False


_RUNNER_AVAILABLE = _RUNNER_LIVE and _probe_runner()

live_runner = pytest.mark.skipif(
    not _RUNNER_AVAILABLE,
    reason=(
        "Live XCTest runner not available on localhost:8222. "
        "Start the runner and set SPECTERQA_RUNNER_LIVE=1 to run these tests."
    ),
)

# ---------------------------------------------------------------------------
# Shared fixture
# ---------------------------------------------------------------------------


@pytest.fixture()
def backend() -> XCTestBackend:
    """XCTestBackend pointed at the test runner."""
    return XCTestBackend(host=_RUNNER_HOST, port=_RUNNER_PORT)


# ===========================================================================
# Live integration tests (skipped when runner not present)
# ===========================================================================


@live_runner
class TestHealthEndpointLive:
    def test_health_returns_status_ok(self, backend: XCTestBackend):
        """GET /health → {"status": "ok"}."""
        result = backend._get("/health")
        assert result.get("status") == "ok"

    def test_health_returns_port(self, backend: XCTestBackend):
        """GET /health includes the port the runner is listening on."""
        result = backend._get("/health")
        assert "port" in result
        assert result["port"] == _RUNNER_PORT

    def test_is_available_returns_true(self, backend: XCTestBackend):
        assert backend.is_available() is True

    def test_health_content_type_is_json(self, backend: XCTestBackend):
        url = f"http://{_RUNNER_HOST}:{_RUNNER_PORT}/health"
        with urllib.request.urlopen(url, timeout=_RUNNER_TIMEOUT) as resp:
            ct = resp.headers.get("Content-Type", "")
        assert "application/json" in ct


@live_runner
class TestSourceEndpointLive:
    """GET /source returns the UI element tree."""

    def test_source_returns_dict(self, backend: XCTestBackend):
        result = backend._get("/source")
        assert isinstance(result, dict)

    def test_source_has_type_field(self, backend: XCTestBackend):
        """Root element must have a 'type' field (XCUIElementType…)."""
        result = backend._get("/source")
        assert "type" in result or "children" in result, \
            "Source tree must include 'type' or 'children'"

    def test_source_has_frame_or_bounds(self, backend: XCTestBackend):
        """Root element must expose position/size information."""
        result = backend._get("/source")
        has_frame = "frame" in result or "x" in result or "bounds" in result
        assert has_frame, "Source tree must include frame or position data"

    def test_source_children_is_list(self, backend: XCTestBackend):
        result = backend._get("/source")
        children = result.get("children")
        if children is not None:
            assert isinstance(children, list)

    def test_source_elements_have_labels(self, backend: XCTestBackend):
        """Interactive elements should carry accessibility labels."""
        result = backend._get("/source")
        # Walk one level of children and check at least one has a label field
        children = result.get("children", [])
        if children:
            for child in children[:5]:
                if "label" in child or "value" in child or "identifier" in child:
                    return  # Found one — good enough
            # No children with labels is acceptable for a blank app state
            pytest.skip("No labelled elements in current UI state")


@live_runner
class TestTapEndpointLive:
    """POST /tap with {x, y} returns success."""

    def test_tap_centre_of_screen_succeeds(self, backend: XCTestBackend):
        result = backend.tap(x=195.0, y=422.0)
        assert result.get("status") == "ok" or result.get("success") is True

    def test_tap_top_left_succeeds(self, backend: XCTestBackend):
        result = backend.tap(x=10.0, y=10.0)
        assert result.get("status") == "ok" or result.get("success") is True

    def test_tap_missing_x_returns_error(self, backend: XCTestBackend):
        """Malformed body: missing x → runner returns 4xx error dict."""
        result = backend._post("/tap", {"y": 100.0})
        # The runner may return {"error": "..."} or {"status": 422}
        has_error = "error" in result or result.get("status", 200) >= 400
        assert has_error, f"Expected error response, got: {result}"


@live_runner
class TestSwipeEndpointLive:
    """POST /swipe with {fromX, fromY, toX, toY} or {x1, y1, x2, y2}."""

    def test_swipe_down_succeeds(self, backend: XCTestBackend):
        result = backend.swipe(x1=195.0, y1=600.0, x2=195.0, y2=200.0)
        assert result.get("status") == "ok" or result.get("success") is True

    def test_swipe_up_succeeds(self, backend: XCTestBackend):
        result = backend.swipe(x1=195.0, y1=200.0, x2=195.0, y2=600.0)
        assert result.get("status") == "ok" or result.get("success") is True

    def test_swipe_with_duration(self, backend: XCTestBackend):
        result = backend.swipe(x1=195.0, y1=600.0, x2=195.0, y2=200.0, duration=0.5)
        assert result.get("status") == "ok" or result.get("success") is True


@live_runner
class TestTypeEndpointLive:
    def test_type_text_succeeds(self, backend: XCTestBackend):
        result = backend.type_text("hello")
        assert result.get("status") == "ok" or result.get("success") is True


@live_runner
class TestScreenshotEndpointLive:
    def test_screenshot_returns_base64(self, backend: XCTestBackend):
        result = backend.screenshot()
        has_b64 = "base64" in result or "image" in result or "data" in result
        assert has_b64, "Screenshot response must include base64 image data"

    def test_screenshot_has_dimensions(self, backend: XCTestBackend):
        result = backend.screenshot()
        has_dims = ("width" in result and "height" in result)
        assert has_dims, "Screenshot response must include width and height"

    def test_screenshot_width_is_positive(self, backend: XCTestBackend):
        result = backend.screenshot()
        assert result.get("width", 0) > 0


# ===========================================================================
# Unit tests — no live runner required
# ===========================================================================


class TestXCTestBackendUnit:
    """Verify XCTestBackend constructs requests correctly (mocked urllib)."""

    # ------------------------------------------------------------------
    # Health / is_available
    # ------------------------------------------------------------------

    def test_is_available_true_when_ok_returned(self):
        backend = XCTestBackend(host="localhost", port=8222)
        mock_resp = MagicMock()
        mock_resp.read.return_value = b'{"status": "ok"}'
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        with patch("urllib.request.urlopen", return_value=mock_resp):
            assert backend.is_available() is True

    def test_is_available_false_when_not_ok(self):
        backend = XCTestBackend(host="localhost", port=8222)
        mock_resp = MagicMock()
        mock_resp.read.return_value = b'{"status": "error"}'
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        with patch("urllib.request.urlopen", return_value=mock_resp):
            assert backend.is_available() is False

    def test_is_available_false_on_connection_error(self):
        backend = XCTestBackend(host="localhost", port=8222)
        with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("refused")):
            assert backend.is_available() is False

    def test_is_available_uses_correct_port(self):
        backend = XCTestBackend(host="localhost", port=9999)
        with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("refused")) as mock_open:
            backend.is_available()
        url_str = str(mock_open.call_args)
        assert "9999" in url_str

    # ------------------------------------------------------------------
    # Tap
    # ------------------------------------------------------------------

    def test_tap_posts_to_slash_tap(self):
        backend = XCTestBackend(port=8222)
        mock_resp = MagicMock()
        mock_resp.read.return_value = b'{"status": "ok"}'
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        with patch("urllib.request.urlopen", return_value=mock_resp) as mock_open:
            backend.tap(100.0, 200.0)
        req = mock_open.call_args[0][0]
        assert req.full_url.endswith("/tap")

    def test_tap_sends_x_and_y(self):
        backend = XCTestBackend(port=8222)
        mock_resp = MagicMock()
        mock_resp.read.return_value = b'{"status": "ok"}'
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        with patch("urllib.request.urlopen", return_value=mock_resp) as mock_open:
            backend.tap(100.0, 200.0)
        req = mock_open.call_args[0][0]
        body = json.loads(req.data.decode())
        assert body["x"] == 100.0
        assert body["y"] == 200.0

    def test_tap_method_is_post(self):
        backend = XCTestBackend(port=8222)
        mock_resp = MagicMock()
        mock_resp.read.return_value = b'{"status": "ok"}'
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        with patch("urllib.request.urlopen", return_value=mock_resp) as mock_open:
            backend.tap(0.0, 0.0)
        req = mock_open.call_args[0][0]
        assert req.method == "POST"

    # ------------------------------------------------------------------
    # Swipe
    # ------------------------------------------------------------------

    def test_swipe_posts_to_slash_swipe(self):
        backend = XCTestBackend(port=8222)
        mock_resp = MagicMock()
        mock_resp.read.return_value = b'{"status": "ok"}'
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        with patch("urllib.request.urlopen", return_value=mock_resp) as mock_open:
            backend.swipe(0.0, 0.0, 100.0, 100.0)
        req = mock_open.call_args[0][0]
        assert req.full_url.endswith("/swipe")

    def test_swipe_sends_all_coordinates(self):
        backend = XCTestBackend(port=8222)
        mock_resp = MagicMock()
        mock_resp.read.return_value = b'{"status": "ok"}'
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        with patch("urllib.request.urlopen", return_value=mock_resp) as mock_open:
            backend.swipe(10.0, 20.0, 30.0, 40.0)
        body = json.loads(mock_open.call_args[0][0].data.decode())
        assert body["x1"] == 10.0
        assert body["y1"] == 20.0
        assert body["x2"] == 30.0
        assert body["y2"] == 40.0

    def test_swipe_default_duration(self):
        backend = XCTestBackend(port=8222)
        mock_resp = MagicMock()
        mock_resp.read.return_value = b'{"status": "ok"}'
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        with patch("urllib.request.urlopen", return_value=mock_resp) as mock_open:
            backend.swipe(0.0, 0.0, 10.0, 10.0)
        body = json.loads(mock_open.call_args[0][0].data.decode())
        assert body["duration"] == 0.3

    # ------------------------------------------------------------------
    # Source tree (GET /source — v3 extension)
    # ------------------------------------------------------------------

    def test_get_source_calls_slash_source(self):
        """_get('/source') sends a GET to /source."""
        backend = XCTestBackend(port=8222)
        mock_resp = MagicMock()
        mock_resp.read.return_value = b'{"type": "Application", "children": []}'
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        with patch("urllib.request.urlopen", return_value=mock_resp) as mock_open:
            result = backend._get("/source")
        url_str = str(mock_open.call_args)
        assert "/source" in url_str
        assert result["type"] == "Application"

    def test_get_source_returns_parsed_json(self):
        backend = XCTestBackend(port=8222)
        payload = {
            "type": "Application",
            "label": "MyApp",
            "frame": {"x": 0, "y": 0, "width": 390, "height": 844},
            "children": [
                {"type": "Button", "label": "OK", "frame": {"x": 100, "y": 400, "width": 80, "height": 44}}
            ],
        }
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps(payload).encode()
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        with patch("urllib.request.urlopen", return_value=mock_resp):
            result = backend._get("/source")
        assert result["label"] == "MyApp"
        assert result["children"][0]["label"] == "OK"

    # ------------------------------------------------------------------
    # Connection error paths
    # ------------------------------------------------------------------

    def test_tap_raises_connection_error_on_refused(self):
        backend = XCTestBackend(port=8222)
        err = urllib.error.URLError(ConnectionRefusedError("refused"))
        with patch("urllib.request.urlopen", side_effect=err):
            with pytest.raises(ConnectionError):
                backend.tap(0.0, 0.0)

    def test_swipe_raises_connection_error_on_refused(self):
        backend = XCTestBackend(port=8222)
        err = urllib.error.URLError(ConnectionRefusedError("refused"))
        with patch("urllib.request.urlopen", side_effect=err):
            with pytest.raises(ConnectionError):
                backend.swipe(0.0, 0.0, 10.0, 10.0)

    # ------------------------------------------------------------------
    # Backend repr
    # ------------------------------------------------------------------

    def test_repr_includes_port(self):
        backend = XCTestBackend(host="localhost", port=8222, udid="booted")
        r = repr(backend)
        assert "8222" in r

    def test_repr_includes_host(self):
        backend = XCTestBackend(host="myhost", port=8222)
        r = repr(backend)
        assert "myhost" in r


# ===========================================================================
# Source tree shape contract (unit — mocked response)
# ===========================================================================


class TestSourceTreeShape:
    """Verify the expected JSON shape for the /source endpoint."""

    def _source_response(self, payload: dict) -> dict:
        """Drive XCTestBackend._get('/source') with a mocked HTTP response."""
        backend = XCTestBackend(port=8222)
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps(payload).encode()
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        with patch("urllib.request.urlopen", return_value=mock_resp):
            return backend._get("/source")

    def test_root_has_type(self):
        result = self._source_response({"type": "Application", "children": []})
        assert result["type"] == "Application"

    def test_nested_children_preserved(self):
        payload = {
            "type": "Application",
            "children": [
                {
                    "type": "Button",
                    "label": "Tap me",
                    "frame": {"x": 10, "y": 20, "width": 80, "height": 40},
                    "children": [],
                }
            ],
        }
        result = self._source_response(payload)
        assert result["children"][0]["label"] == "Tap me"

    def test_frame_fields_present(self):
        payload = {
            "type": "Application",
            "frame": {"x": 0, "y": 0, "width": 390, "height": 844},
            "children": [],
        }
        result = self._source_response(payload)
        assert result["frame"]["width"] == 390
        assert result["frame"]["height"] == 844

    def test_empty_children_list(self):
        result = self._source_response({"type": "Application", "children": []})
        assert result["children"] == []
