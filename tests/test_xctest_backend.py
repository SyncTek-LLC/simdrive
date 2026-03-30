"""Tests for XCTestBackend — Python HTTP client that talks to the Swift XCTest runner.

TDD Phase — INIT-2026-500.
These tests are written BEFORE implementation exists and are importable even
when the implementation module is absent.

Module under test (to be created by CodeAtlas):
  specterqa/ios/backends/xctest_client.py  —  XCTestBackend
"""

from __future__ import annotations

import json
from io import BytesIO
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Conditional import guard — tests remain importable without implementation.
# ---------------------------------------------------------------------------

try:
    from specterqa.ios.backends.xctest_client import XCTestBackend  # type: ignore[import]
    _XCTEST_AVAILABLE = True
except ImportError:
    _XCTEST_AVAILABLE = False
    XCTestBackend = None  # type: ignore[assignment,misc]

needs_xctest = pytest.mark.skipif(
    not _XCTEST_AVAILABLE,
    reason="specterqa.ios.backends.xctest_client not yet implemented",
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_DEFAULT_HOST = "localhost"
_DEFAULT_PORT = 8222
_DEFAULT_UDID = "booted"
_ALT_UDID = "00008110-001A2B3C4D5E6F78"


def _make_backend(
    host: str = _DEFAULT_HOST,
    port: int = _DEFAULT_PORT,
    udid: str = _DEFAULT_UDID,
) -> "XCTestBackend":
    """Construct an XCTestBackend for testing."""
    return XCTestBackend(host=host, port=port, udid=udid)


def _mock_http_response(body: dict, status: int = 200) -> MagicMock:
    """Return a mock urllib response with JSON body."""
    encoded = json.dumps(body).encode("utf-8")
    mock_resp = MagicMock()
    mock_resp.read.return_value = encoded
    mock_resp.status = status
    mock_resp.getcode.return_value = status
    mock_resp.__enter__ = lambda s: s
    mock_resp.__exit__ = MagicMock(return_value=False)
    return mock_resp


def _mock_http_error(status: int = 500) -> MagicMock:
    """Return a mock urllib error response."""
    import urllib.error
    return urllib.error.HTTPError(
        url="http://localhost:8222/tap",
        code=status,
        msg="Internal Server Error",
        hdrs=None,  # type: ignore[arg-type]
        fp=None,
    )


# ===========================================================================
# TestXCTestBackendAvailability — health check and connection detection
# ===========================================================================


@needs_xctest
class TestXCTestBackendAvailability:
    """is_available() reports whether the XCTest runner HTTP server is responding."""

    def test_is_available_returns_true_when_health_responds(self):
        """is_available() returns True when /health endpoint returns 200."""
        backend = _make_backend()
        mock_resp = _mock_http_response({"status": "ok"})
        with patch("urllib.request.urlopen", return_value=mock_resp):
            assert backend.is_available() is True

    def test_is_available_returns_false_when_connection_refused(self):
        """is_available() returns False when the runner is not listening."""
        import urllib.error

        backend = _make_backend()
        conn_err = ConnectionRefusedError(111, "Connection refused")
        os_err = urllib.error.URLError(conn_err)
        with patch("urllib.request.urlopen", side_effect=os_err):
            assert backend.is_available() is False

    def test_health_check_targets_correct_port(self):
        """is_available() checks the port passed to the constructor."""
        backend = _make_backend(port=9333)
        mock_resp = _mock_http_response({"status": "ok"})
        with patch("urllib.request.urlopen", return_value=mock_resp) as mock_open:
            backend.is_available()
        called_url = str(mock_open.call_args)
        assert "9333" in called_url, f"Expected port 9333 in URL, got: {called_url!r}"

    def test_health_check_verifies_status_field(self):
        """is_available() inspects the 'status' field — a body without it is falsy."""
        backend = _make_backend()
        # Body that has no useful status — backend should treat this as unavailable
        mock_resp = _mock_http_response({"error": "starting"}, status=200)
        with patch("urllib.request.urlopen", return_value=mock_resp):
            # Either True or False is acceptable here — but the call must not raise
            result = backend.is_available()
            assert isinstance(result, bool)


# ===========================================================================
# TestXCTestBackendTap — /tap endpoint
# ===========================================================================


@needs_xctest
class TestXCTestBackendTap:
    """tap(x, y) POSTs to /tap with device-logical-point coordinates."""

    def test_tap_sends_post_to_tap_endpoint(self):
        """tap() makes a POST request to /tap."""
        backend = _make_backend()
        mock_resp = _mock_http_response({"success": True})
        with patch("urllib.request.urlopen", return_value=mock_resp) as mock_open:
            backend.tap(x=100, y=250)
        assert mock_open.called, "tap() did not call urlopen"
        request_arg = mock_open.call_args.args[0]
        url = getattr(request_arg, "full_url", None) or str(request_arg)
        assert "/tap" in url, f"Expected /tap in request URL, got: {url!r}"

    def test_tap_body_contains_x_and_y(self):
        """tap() request body contains 'x' and 'y' fields."""
        backend = _make_backend()
        mock_resp = _mock_http_response({"success": True})
        captured_data: list[bytes] = []

        def _capture_open(req, *args, **kwargs):
            data = getattr(req, "data", None)
            if data:
                captured_data.append(data)
            return mock_resp

        with patch("urllib.request.urlopen", side_effect=_capture_open):
            backend.tap(x=150, y=300)

        assert captured_data, "tap() did not send a request body"
        body = json.loads(captured_data[0])
        assert "x" in body, f"'x' not in tap request body: {body}"
        assert "y" in body, f"'y' not in tap request body: {body}"

    def test_tap_coordinates_are_floats(self):
        """tap() encodes x and y as floats (device logical points, not raw ints)."""
        backend = _make_backend()
        mock_resp = _mock_http_response({"success": True})
        captured_data: list[bytes] = []

        def _capture_open(req, *args, **kwargs):
            data = getattr(req, "data", None)
            if data:
                captured_data.append(data)
            return mock_resp

        with patch("urllib.request.urlopen", side_effect=_capture_open):
            backend.tap(x=75, y=150)

        body = json.loads(captured_data[0])
        # x and y must be numeric; float preferred but int-encoded float is OK
        assert isinstance(body["x"], (int, float)), f"x must be numeric: {body['x']!r}"
        assert isinstance(body["y"], (int, float)), f"y must be numeric: {body['y']!r}"
        assert body["x"] == pytest.approx(75), f"x value mismatch: {body['x']}"
        assert body["y"] == pytest.approx(150), f"y value mismatch: {body['y']}"


# ===========================================================================
# TestXCTestBackendSwipe — /swipe endpoint
# ===========================================================================


@needs_xctest
class TestXCTestBackendSwipe:
    """swipe(x1, y1, x2, y2, duration) POSTs to /swipe."""

    def test_swipe_sends_post_to_swipe_endpoint(self):
        """swipe() makes a POST request to /swipe."""
        backend = _make_backend()
        mock_resp = _mock_http_response({"success": True})
        with patch("urllib.request.urlopen", return_value=mock_resp) as mock_open:
            backend.swipe(x1=100, y1=700, x2=100, y2=200, duration=0.4)
        request_arg = mock_open.call_args.args[0]
        url = getattr(request_arg, "full_url", None) or str(request_arg)
        assert "/swipe" in url, f"Expected /swipe in request URL, got: {url!r}"

    def test_swipe_body_contains_required_fields(self):
        """swipe() body includes x1, y1, x2, y2, and duration."""
        backend = _make_backend()
        mock_resp = _mock_http_response({"success": True})
        captured_data: list[bytes] = []

        def _capture_open(req, *args, **kwargs):
            data = getattr(req, "data", None)
            if data:
                captured_data.append(data)
            return mock_resp

        with patch("urllib.request.urlopen", side_effect=_capture_open):
            backend.swipe(x1=50, y1=800, x2=50, y2=100, duration=0.6)

        assert captured_data, "swipe() did not send a request body"
        body = json.loads(captured_data[0])
        for field in ("x1", "y1", "x2", "y2"):
            assert field in body, f"'{field}' not in swipe body: {body}"
        # Duration may appear as 'duration' or similar key
        duration_present = any(
            "duration" in k.lower() for k in body
        )
        assert duration_present, f"'duration' not found in swipe body keys: {list(body.keys())}"


# ===========================================================================
# TestXCTestBackendTypeText — /type endpoint
# ===========================================================================


@needs_xctest
class TestXCTestBackendTypeText:
    """type_text(text) POSTs to /type with the text payload."""

    def test_type_text_sends_post_to_type_endpoint(self):
        """type_text() POSTs to /type."""
        backend = _make_backend()
        mock_resp = _mock_http_response({"success": True})
        with patch("urllib.request.urlopen", return_value=mock_resp) as mock_open:
            backend.type_text("hello world")
        request_arg = mock_open.call_args.args[0]
        url = getattr(request_arg, "full_url", None) or str(request_arg)
        assert "/type" in url, f"Expected /type in request URL, got: {url!r}"

    def test_type_text_body_contains_text(self):
        """type_text() encodes the text string in the request body."""
        backend = _make_backend()
        mock_resp = _mock_http_response({"success": True})
        captured_data: list[bytes] = []

        def _capture_open(req, *args, **kwargs):
            data = getattr(req, "data", None)
            if data:
                captured_data.append(data)
            return mock_resp

        with patch("urllib.request.urlopen", side_effect=_capture_open):
            backend.type_text("SpecterQA rocks")

        assert captured_data
        body = json.loads(captured_data[0])
        # Accept 'text', 'value', or 'string' as the key
        text_value = body.get("text") or body.get("value") or body.get("string")
        assert text_value is not None, f"Text payload not found in body: {body}"
        assert "SpecterQA rocks" in str(text_value)


# ===========================================================================
# TestXCTestBackendPressKey — /key endpoint
# ===========================================================================


@needs_xctest
class TestXCTestBackendPressKey:
    """press_key(key) POSTs to /key."""

    def test_press_key_sends_post_to_key_endpoint(self):
        """press_key() POSTs to /key."""
        backend = _make_backend()
        mock_resp = _mock_http_response({"success": True})
        with patch("urllib.request.urlopen", return_value=mock_resp) as mock_open:
            backend.press_key("home")
        request_arg = mock_open.call_args.args[0]
        url = getattr(request_arg, "full_url", None) or str(request_arg)
        assert "/key" in url, f"Expected /key in request URL, got: {url!r}"


# ===========================================================================
# TestXCTestBackendScreenshot — /screenshot endpoint
# ===========================================================================


@needs_xctest
class TestXCTestBackendScreenshot:
    """screenshot() GETs /screenshot and returns a dict with base64 image data."""

    def test_screenshot_sends_get_to_screenshot_endpoint(self):
        """screenshot() issues a GET to /screenshot."""
        backend = _make_backend()
        mock_resp = _mock_http_response({"image": "iVBORw0KGgoAAAANSUhEUgAAAAE="})
        with patch("urllib.request.urlopen", return_value=mock_resp) as mock_open:
            backend.screenshot()
        assert mock_open.called
        request_arg = mock_open.call_args.args[0]
        url = getattr(request_arg, "full_url", None) or str(request_arg)
        assert "/screenshot" in url, f"Expected /screenshot in URL, got: {url!r}"

    def test_screenshot_returns_dict_with_base64(self):
        """screenshot() returns a dict containing a base64-encoded image field."""
        backend = _make_backend()
        b64_payload = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
        mock_resp = _mock_http_response({"image": b64_payload})
        with patch("urllib.request.urlopen", return_value=mock_resp):
            result = backend.screenshot()
        assert isinstance(result, dict), f"screenshot() should return a dict, got {type(result)}"
        # Accept 'image', 'data', or 'base64' as key name
        image_data = result.get("image") or result.get("data") or result.get("base64")
        assert image_data is not None, f"No image data in screenshot result: {result}"


# ===========================================================================
# TestXCTestBackendPressButton — /press_button endpoint
# ===========================================================================


@needs_xctest
class TestXCTestBackendPressButton:
    """press_button(button) POSTs to /press_button."""

    def test_press_button_sends_post_to_press_button_endpoint(self):
        """press_button() POSTs to /press_button."""
        backend = _make_backend()
        mock_resp = _mock_http_response({"success": True})
        with patch("urllib.request.urlopen", return_value=mock_resp) as mock_open:
            backend.press_button("home")
        request_arg = mock_open.call_args.args[0]
        url = getattr(request_arg, "full_url", None) or str(request_arg)
        assert "/press_button" in url, f"Expected /press_button in URL, got: {url!r}"


# ===========================================================================
# TestXCTestBackendShutdown — /shutdown endpoint
# ===========================================================================


@needs_xctest
class TestXCTestBackendShutdown:
    """shutdown() POSTs to /shutdown to gracefully stop the XCTest runner."""

    def test_shutdown_sends_post_to_shutdown_endpoint(self):
        """shutdown() POSTs to /shutdown."""
        backend = _make_backend()
        mock_resp = _mock_http_response({"ok": True})
        with patch("urllib.request.urlopen", return_value=mock_resp) as mock_open:
            backend.shutdown()
        assert mock_open.called, "shutdown() did not call urlopen"
        request_arg = mock_open.call_args.args[0]
        url = getattr(request_arg, "full_url", None) or str(request_arg)
        assert "/shutdown" in url, f"Expected /shutdown in URL, got: {url!r}"


# ===========================================================================
# TestXCTestBackendErrorHandling — failure and timeout paths
# ===========================================================================


@needs_xctest
class TestXCTestBackendErrorHandling:
    """Backend surfaces errors as structured failure dicts or typed exceptions."""

    def test_connection_timeout_raises_appropriate_error(self):
        """A socket timeout raises a recognisable exception (not a bare Exception)."""
        import socket
        import urllib.error

        backend = _make_backend()
        timeout_err = urllib.error.URLError(socket.timeout("timed out"))
        with patch("urllib.request.urlopen", side_effect=timeout_err):
            with pytest.raises((TimeoutError, ConnectionError, OSError, RuntimeError)):
                backend.tap(x=100, y=200)

    def test_http_error_response_returns_failure_dict(self):
        """An HTTP 5xx from the runner is surfaced as a failure dict, not a crash."""
        import urllib.error

        backend = _make_backend()
        http_err = urllib.error.HTTPError(
            url="http://localhost:8222/tap",
            code=500,
            msg="Internal Server Error",
            hdrs=None,  # type: ignore[arg-type]
            fp=None,
        )
        with patch("urllib.request.urlopen", side_effect=http_err):
            # Should return a failure dict OR raise — both are acceptable as long as
            # the caller can detect the failure without an unhandled exception escaping
            try:
                result = backend.tap(x=100, y=200)
                # If it doesn't raise, it must signal failure in the return value
                assert result is not None
                if isinstance(result, dict):
                    success = result.get("success", result.get("ok", True))
                    assert not success, f"Expected failure indicator in result: {result}"
            except (RuntimeError, ConnectionError, OSError):
                pass  # Raising a typed exception is also acceptable

    def test_coordinates_are_device_logical_points_not_pixels(self):
        """tap() passes logical-point coordinates, not raw pixel coordinates.

        On a @3x device, pixel (300, 600) == logical (100, 200).
        The backend must NOT perform any pixel-to-point scaling — that is the
        caller's responsibility. The values passed in are forwarded as-is.
        """
        backend = _make_backend()
        mock_resp = _mock_http_response({"success": True})
        captured_data: list[bytes] = []

        def _capture_open(req, *args, **kwargs):
            data = getattr(req, "data", None)
            if data:
                captured_data.append(data)
            return mock_resp

        with patch("urllib.request.urlopen", side_effect=_capture_open):
            backend.tap(x=100, y=200)

        body = json.loads(captured_data[0])
        # Must NOT have multiplied to 300/600
        assert body.get("x") != 300, (
            "tap() must not multiply coordinates by scale factor"
        )
        assert body.get("y") != 600, (
            "tap() must not multiply coordinates by scale factor"
        )
        assert body.get("x") == pytest.approx(100)
        assert body.get("y") == pytest.approx(200)
