"""Tests for WDADriver — WebDriverAgent HTTP client.

Covers:
  - is_available() classmethod (probes /status)
  - create_session() — POST /session, window size fetch
  - tap / double_tap / long_press / swipe — W3C Actions payloads
  - type_text / press_key — simctl primary, WDA fallback
  - screenshot — delegates to simctl, resizes, returns base64
  - execute() — unified ActionExecutor protocol (all action types)
  - WDAError raised on HTTP errors and missing session

All tests use stdlib mocking only — no network, no simulator required.

INIT-2026-493 — SpecterQA WDA touch backend.
"""

from __future__ import annotations

import base64
import io
import json
from unittest.mock import MagicMock, patch

import pytest
from PIL import Image

from specterqa.ios.wda_driver import WDADriver, WDAError, WDA_BASE


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_driver(**kwargs) -> WDADriver:
    """Return a WDADriver with a pre-seeded session for convenience."""
    d = WDADriver(**kwargs)
    d._session_id = "test-session-abc"
    d._device_width = 393.0
    d._device_height = 852.0
    d._display_width = 1024
    d._display_height = 2226
    return d


def _urlopen_response(body: dict, status: int = 200) -> MagicMock:
    """Build a mock context-manager response for urllib.urlopen."""
    raw = json.dumps(body).encode()
    resp = MagicMock()
    resp.read.return_value = raw
    resp.status = status
    resp.__enter__ = lambda s: s
    resp.__exit__ = MagicMock(return_value=False)
    return resp


def _tiny_png_b64() -> bytes:
    """Return raw bytes of a 1×1 white PNG."""
    buf = io.BytesIO()
    img = Image.new("RGB", (4, 8), color=(255, 255, 255))
    img.save(buf, "PNG")
    return buf.getvalue()


# ---------------------------------------------------------------------------
# is_available
# ---------------------------------------------------------------------------


class TestIsAvailable:
    def test_returns_true_when_wda_ready(self):
        resp = _urlopen_response({"value": {"ready": True}})
        with patch("urllib.request.urlopen", return_value=resp):
            assert WDADriver.is_available() is True

    def test_returns_false_when_ready_is_false(self):
        resp = _urlopen_response({"value": {"ready": False}})
        with patch("urllib.request.urlopen", return_value=resp):
            assert WDADriver.is_available() is False

    def test_returns_false_on_connection_error(self):
        with patch("urllib.request.urlopen", side_effect=ConnectionRefusedError):
            assert WDADriver.is_available() is False

    def test_returns_false_on_timeout(self):
        import socket

        with patch("urllib.request.urlopen", side_effect=socket.timeout):
            assert WDADriver.is_available() is False

    def test_probes_correct_url(self):
        # Use OSError (a specific exception the narrow except now catches) so
        # is_available() returns False without propagating.
        with patch("urllib.request.urlopen", side_effect=OSError) as mock_open:
            WDADriver.is_available(wda_url="http://localhost:8100")
        mock_open.assert_called_once()
        call_arg = mock_open.call_args[0][0]
        assert "localhost:8100/status" in call_arg

    def test_custom_wda_url(self):
        resp = _urlopen_response({"value": {"ready": True}})
        with patch("urllib.request.urlopen", return_value=resp) as mock_open:
            WDADriver.is_available(wda_url="http://192.168.1.10:8100")
        url_arg = mock_open.call_args[0][0]
        assert "192.168.1.10:8100" in url_arg


# ---------------------------------------------------------------------------
# __init__ and defaults
# ---------------------------------------------------------------------------


class TestInit:
    def test_defaults(self):
        d = WDADriver()
        assert d.udid == "booted"
        assert d.wda_url == WDA_BASE.rstrip("/")
        assert d.verbose is False
        assert d._session_id is None

    def test_trailing_slash_stripped(self):
        d = WDADriver(wda_url="http://localhost:8100/")
        assert not d.wda_url.endswith("/")

    def test_custom_args(self):
        d = WDADriver(udid="ABC-123", wda_url="http://10.0.0.1:8100", verbose=True)
        assert d.udid == "ABC-123"
        assert "10.0.0.1" in d.wda_url
        assert d.verbose is True


# ---------------------------------------------------------------------------
# _request
# ---------------------------------------------------------------------------


class TestRequest:
    def test_get_parses_json(self):
        d = WDADriver()
        resp = _urlopen_response({"value": "ok"})
        with patch("urllib.request.urlopen", return_value=resp):
            result = d._request("GET", "/health")
        assert result == {"value": "ok"}

    def test_post_sends_json_body(self):
        d = WDADriver()
        resp = _urlopen_response({"sessionId": "s1"})
        with patch("urllib.request.urlopen", return_value=resp) as mock_open:
            d._request("POST", "/session", {"capabilities": {}})
        req = mock_open.call_args[0][0]
        assert req.data == b'{"capabilities": {}}'
        assert req.get_header("Content-type") == "application/json"

    def test_raises_wda_error_on_http_error(self):
        import urllib.error

        d = WDADriver()
        exc = urllib.error.HTTPError(
            url="http://localhost:8100/session",
            code=500,
            msg="Internal Server Error",
            hdrs=None,
            fp=None,
        )
        with patch("urllib.request.urlopen", side_effect=exc):
            with pytest.raises(WDAError, match="500"):
                d._request("POST", "/session", {})

    def test_raises_wda_error_on_url_error(self):
        import urllib.error

        d = WDADriver()
        with patch(
            "urllib.request.urlopen",
            side_effect=urllib.error.URLError("Connection refused"),
        ):
            with pytest.raises(WDAError, match="connection failed"):
                d._request("GET", "/status")


# ---------------------------------------------------------------------------
# create_session
# ---------------------------------------------------------------------------


class TestCreateSession:
    def _mock_responses(self, session_resp: dict, size_resp: dict):
        """Return a side_effect list for two sequential urlopen calls."""
        return [
            _urlopen_response(session_resp),
            _urlopen_response(size_resp),
        ]

    def test_stores_session_id(self):
        d = WDADriver()
        responses = self._mock_responses(
            {"sessionId": "ses-001", "value": {}},
            {"value": {"width": 393, "height": 852}},
        )
        with patch("urllib.request.urlopen", side_effect=responses):
            session_id = d.create_session("com.example.App")
        assert session_id == "ses-001"
        assert d._session_id == "ses-001"

    def test_session_id_from_value_dict(self):
        """WDA sometimes nests sessionId inside value."""
        d = WDADriver()
        responses = self._mock_responses(
            {"value": {"sessionId": "ses-nested"}},
            {"value": {"width": 390, "height": 844}},
        )
        with patch("urllib.request.urlopen", side_effect=responses):
            session_id = d.create_session("com.example.App")
        assert session_id == "ses-nested"

    def test_stores_device_dimensions(self):
        d = WDADriver()
        responses = self._mock_responses(
            {"sessionId": "s1"},
            {"value": {"width": 430, "height": 932}},
        )
        with patch("urllib.request.urlopen", side_effect=responses):
            d.create_session("com.example.App")
        assert d._device_width == 430.0
        assert d._device_height == 932.0

    def test_fallback_dimensions_on_size_error(self):
        import urllib.error

        d = WDADriver()
        session_resp = _urlopen_response({"sessionId": "s1"})
        size_exc = urllib.error.URLError("timeout")
        with patch("urllib.request.urlopen", side_effect=[session_resp, size_exc]):
            d.create_session("com.example.App")
        assert d._device_width == 393.0
        assert d._device_height == 852.0

    def test_raises_when_no_session_id_returned(self):
        d = WDADriver()
        resp = _urlopen_response({"value": {}})  # no sessionId
        with patch("urllib.request.urlopen", return_value=resp):
            with pytest.raises(WDAError, match="sessionId"):
                d.create_session("com.example.App")


# ---------------------------------------------------------------------------
# Coordinate conversion
# ---------------------------------------------------------------------------


class TestImgToDevice:
    def test_identity_when_dimensions_match(self):
        d = _make_driver()
        d._device_width = 1024.0
        d._device_height = 2226.0
        assert d._img_to_device(512, 1113) == (512.0, 1113.0)

    def test_scales_correctly(self):
        d = _make_driver()
        # display 1024×2226, device 393×852
        dx, dy = d._img_to_device(1024, 2226)
        assert abs(dx - 393.0) < 0.01
        assert abs(dy - 852.0) < 0.01

    def test_origin_maps_to_origin(self):
        d = _make_driver()
        assert d._img_to_device(0, 0) == (0.0, 0.0)

    def test_midpoint(self):
        d = _make_driver()
        dx, dy = d._img_to_device(512, 1113)
        assert abs(dx - 393 * 512 / 1024) < 0.01
        assert abs(dy - 852 * 1113 / 2226) < 0.01


# ---------------------------------------------------------------------------
# tap
# ---------------------------------------------------------------------------


class TestTap:
    def test_sends_w3c_pointer_action(self):
        d = _make_driver()
        resp = _urlopen_response({"value": None})
        with patch("urllib.request.urlopen", return_value=resp) as mock_open:
            with patch("time.sleep"):
                d.tap(512, 1113)
        req = mock_open.call_args[0][0]
        payload = json.loads(req.data)
        actions = payload["actions"]
        assert len(actions) == 1
        action = actions[0]
        assert action["type"] == "pointer"
        assert action["parameters"]["pointerType"] == "touch"
        types = [a["type"] for a in action["actions"]]
        assert "pointerDown" in types
        assert "pointerUp" in types

    def test_converts_coordinates(self):
        d = _make_driver()
        resp = _urlopen_response({"value": None})
        with patch("urllib.request.urlopen", return_value=resp) as mock_open:
            with patch("time.sleep"):
                d.tap(512, 1113)  # centre of 1024×2226
        payload = json.loads(mock_open.call_args[0][0].data)
        move = payload["actions"][0]["actions"][0]
        assert move["type"] == "pointerMove"
        assert move["x"] == int(393 * 512 / 1024)
        assert move["y"] == int(852 * 1113 / 2226)

    def test_requires_session(self):
        d = WDADriver()  # no session
        with pytest.raises(WDAError, match="session"):
            d.tap(0, 0)

    def test_posts_to_actions_endpoint(self):
        d = _make_driver()
        resp = _urlopen_response({"value": None})
        with patch("urllib.request.urlopen", return_value=resp) as mock_open:
            with patch("time.sleep"):
                d.tap(100, 200)
        url = mock_open.call_args[0][0].full_url
        assert "/session/test-session-abc/actions" in url


# ---------------------------------------------------------------------------
# double_tap
# ---------------------------------------------------------------------------


class TestDoubleTap:
    def test_sends_two_down_up_pairs(self):
        d = _make_driver()
        resp = _urlopen_response({"value": None})
        with patch("urllib.request.urlopen", return_value=resp) as mock_open:
            with patch("time.sleep"):
                d.double_tap(200, 400)
        payload = json.loads(mock_open.call_args[0][0].data)
        sub_actions = payload["actions"][0]["actions"]
        down_count = sum(1 for a in sub_actions if a["type"] == "pointerDown")
        up_count = sum(1 for a in sub_actions if a["type"] == "pointerUp")
        assert down_count == 2
        assert up_count == 2

    def test_requires_session(self):
        d = WDADriver()
        with pytest.raises(WDAError, match="session"):
            d.double_tap(0, 0)


# ---------------------------------------------------------------------------
# long_press
# ---------------------------------------------------------------------------


class TestLongPress:
    def test_pause_duration_in_milliseconds(self):
        d = _make_driver()
        resp = _urlopen_response({"value": None})
        with patch("urllib.request.urlopen", return_value=resp) as mock_open:
            with patch("time.sleep"):
                d.long_press(100, 200, duration=2.0)
        payload = json.loads(mock_open.call_args[0][0].data)
        sub_actions = payload["actions"][0]["actions"]
        pause = next(a for a in sub_actions if a["type"] == "pause")
        assert pause["duration"] >= 2000  # ≥ 2000 ms

    def test_minimum_hold_500ms(self):
        d = _make_driver()
        resp = _urlopen_response({"value": None})
        with patch("urllib.request.urlopen", return_value=resp) as mock_open:
            with patch("time.sleep"):
                d.long_press(100, 200, duration=0.1)  # too short
        payload = json.loads(mock_open.call_args[0][0].data)
        sub_actions = payload["actions"][0]["actions"]
        pause = next(a for a in sub_actions if a["type"] == "pause")
        assert pause["duration"] >= 500

    def test_requires_session(self):
        d = WDADriver()
        with pytest.raises(WDAError, match="session"):
            d.long_press(0, 0)


# ---------------------------------------------------------------------------
# swipe
# ---------------------------------------------------------------------------


class TestSwipe:
    def test_sends_move_down_move_up_sequence(self):
        d = _make_driver()
        resp = _urlopen_response({"value": None})
        with patch("urllib.request.urlopen", return_value=resp) as mock_open:
            with patch("time.sleep"):
                d.swipe(100, 800, 100, 200, duration=0.5)
        payload = json.loads(mock_open.call_args[0][0].data)
        types = [a["type"] for a in payload["actions"][0]["actions"]]
        assert types == ["pointerMove", "pointerDown", "pointerMove", "pointerUp"]

    def test_duration_encoded_in_milliseconds(self):
        d = _make_driver()
        resp = _urlopen_response({"value": None})
        with patch("urllib.request.urlopen", return_value=resp) as mock_open:
            with patch("time.sleep"):
                d.swipe(0, 0, 100, 100, duration=0.6)
        payload = json.loads(mock_open.call_args[0][0].data)
        end_move = payload["actions"][0]["actions"][2]
        assert end_move["duration"] == 600

    def test_requires_session(self):
        d = WDADriver()
        with pytest.raises(WDAError, match="session"):
            d.swipe(0, 0, 0, 100)


# ---------------------------------------------------------------------------
# type_text
# ---------------------------------------------------------------------------


class TestTypeText:
    def test_uses_simctl_primary(self):
        d = _make_driver()
        mock_result = MagicMock()
        mock_result.returncode = 0
        with patch("subprocess.run", return_value=mock_result) as mock_run:
            with patch("time.sleep"):
                d.type_text("hello")
        cmd = mock_run.call_args[0][0]
        assert "xcrun" in cmd
        assert "keyboard" in cmd
        assert "hello" in cmd

    def test_falls_back_to_wda_keys_on_simctl_failure(self):
        d = _make_driver()
        simctl_fail = MagicMock()
        simctl_fail.returncode = 1
        wda_resp = _urlopen_response({"value": None})
        with patch("subprocess.run", return_value=simctl_fail):
            with patch("urllib.request.urlopen", return_value=wda_resp) as mock_open:
                with patch("time.sleep"):
                    d.type_text("world")
        url = mock_open.call_args[0][0].full_url
        assert "/keys" in url
        payload = json.loads(mock_open.call_args[0][0].data)
        assert payload["value"] == list("world")

    def test_text_as_list_of_chars_in_wda_fallback(self):
        d = _make_driver()
        simctl_fail = MagicMock(returncode=1)
        wda_resp = _urlopen_response({"value": None})
        with patch("subprocess.run", return_value=simctl_fail):
            with patch("urllib.request.urlopen", return_value=wda_resp) as mock_open:
                with patch("time.sleep"):
                    d.type_text("ab")
        payload = json.loads(mock_open.call_args[0][0].data)
        assert payload["value"] == ["a", "b"]


# ---------------------------------------------------------------------------
# press_key
# ---------------------------------------------------------------------------


class TestPressKey:
    def test_calls_simctl(self):
        d = _make_driver()
        mock_result = MagicMock(returncode=0)
        with patch("subprocess.run", return_value=mock_result) as mock_run:
            with patch("time.sleep"):
                d.press_key("return")
        cmd = mock_run.call_args[0][0]
        assert "xcrun" in cmd
        assert "return" in cmd

    def test_falls_back_to_wda_on_simctl_failure(self):
        d = _make_driver()
        simctl_fail = MagicMock(returncode=1)
        wda_resp = _urlopen_response({"value": None})
        with patch("subprocess.run", return_value=simctl_fail):
            with patch("urllib.request.urlopen", return_value=wda_resp) as mock_open:
                with patch("time.sleep"):
                    d.press_key("escape")
        url = mock_open.call_args[0][0].full_url
        assert "/keys" in url


# ---------------------------------------------------------------------------
# screenshot
# ---------------------------------------------------------------------------


class TestScreenshot:
    def _png_bytes(self, width: int = 1179, height: int = 2556) -> bytes:
        buf = io.BytesIO()
        Image.new("RGB", (width, height), color=(0, 128, 255)).save(buf, "PNG")
        return buf.getvalue()

    def test_returns_base64_and_dimensions(self, tmp_path):
        d = WDADriver(udid="booted")
        d._screenshot_dir = str(tmp_path)
        png_data = self._png_bytes(1179, 2556)

        mock_run = MagicMock()
        mock_run.return_value = MagicMock()

        def _fake_run(cmd, **kwargs):
            # Write a fake PNG to the path argument
            path = cmd[-1]
            with open(path, "wb") as fh:
                fh.write(png_data)
            return MagicMock()

        with patch("subprocess.run", side_effect=_fake_run):
            b64, w, h = d.screenshot(resize_width=1024)

        assert isinstance(b64, str)
        decoded = base64.b64decode(b64)
        img = Image.open(io.BytesIO(decoded))
        assert img.width == 1024
        assert w == 1024

    def test_updates_display_dimensions(self, tmp_path):
        d = WDADriver(udid="booted")
        d._screenshot_dir = str(tmp_path)
        png_data = self._png_bytes(1179, 2556)

        def _fake_run(cmd, **kwargs):
            path = cmd[-1]
            with open(path, "wb") as fh:
                fh.write(png_data)
            return MagicMock()

        with patch("subprocess.run", side_effect=_fake_run):
            d.screenshot(resize_width=1024)

        assert d._display_width == 1024
        assert d._display_height > 0

    def test_calls_simctl_screenshot(self, tmp_path):
        d = WDADriver(udid="booted")
        d._screenshot_dir = str(tmp_path)
        png_data = self._png_bytes()
        calls_seen: list = []

        def _fake_run(cmd, **kwargs):
            calls_seen.append(cmd)
            if "screenshot" in cmd:
                path = cmd[-1]
                with open(path, "wb") as fh:
                    fh.write(png_data)
            return MagicMock()

        with patch("subprocess.run", side_effect=_fake_run):
            d.screenshot()

        screenshot_cmds = [c for c in calls_seen if "screenshot" in c]
        assert len(screenshot_cmds) == 1
        assert "xcrun" in screenshot_cmds[0]
        assert "simctl" in screenshot_cmds[0]


# ---------------------------------------------------------------------------
# execute — ActionExecutor protocol
# ---------------------------------------------------------------------------


class TestExecute:
    def _driver(self, tmp_path) -> WDADriver:
        d = _make_driver()
        d._screenshot_dir = str(tmp_path)
        return d

    def _png_bytes(self) -> bytes:
        buf = io.BytesIO()
        Image.new("RGB", (1179, 2556)).save(buf, "PNG")
        return buf.getvalue()

    def test_screenshot_action(self, tmp_path):
        d = self._driver(tmp_path)
        png = self._png_bytes()

        def _fake_run(cmd, **kwargs):
            if "screenshot" in cmd:
                path = cmd[-1]
                with open(path, "wb") as fh:
                    fh.write(png)
            return MagicMock()

        with patch("subprocess.run", side_effect=_fake_run):
            result = d.execute({"action": "screenshot"})
        assert result["type"] == "image"
        assert "base64" in result

    def test_left_click_action(self, tmp_path):
        d = self._driver(tmp_path)
        wda_resp = _urlopen_response({"value": None})
        with patch("urllib.request.urlopen", return_value=wda_resp):
            with patch("time.sleep"):
                result = d.execute({"action": "left_click", "coordinate": [100, 200]})
        assert result["type"] == "text"
        assert "100" in result["text"]
        assert "200" in result["text"]

    def test_click_alias(self, tmp_path):
        d = self._driver(tmp_path)
        wda_resp = _urlopen_response({"value": None})
        with patch("urllib.request.urlopen", return_value=wda_resp):
            with patch("time.sleep"):
                result = d.execute({"action": "click", "coordinate": [50, 50]})
        assert result["type"] == "text"

    def test_double_click_action(self, tmp_path):
        d = self._driver(tmp_path)
        wda_resp = _urlopen_response({"value": None})
        with patch("urllib.request.urlopen", return_value=wda_resp):
            with patch("time.sleep"):
                result = d.execute({"action": "double_click", "coordinate": [200, 400]})
        assert "Double-tapped" in result["text"]

    def test_long_press_via_right_click(self, tmp_path):
        d = self._driver(tmp_path)
        wda_resp = _urlopen_response({"value": None})
        with patch("urllib.request.urlopen", return_value=wda_resp):
            with patch("time.sleep"):
                result = d.execute({"action": "right_click", "coordinate": [100, 100], "duration": 2.0})
        assert "Long-pressed" in result["text"]

    def test_type_action(self, tmp_path):
        d = self._driver(tmp_path)
        mock_result = MagicMock(returncode=0)
        with patch("subprocess.run", return_value=mock_result):
            with patch("time.sleep"):
                result = d.execute({"action": "type", "text": "hello world"})
        assert result["type"] == "text"
        assert "hello world" in result["text"]

    def test_key_action(self, tmp_path):
        d = self._driver(tmp_path)
        mock_result = MagicMock(returncode=0)
        with patch("subprocess.run", return_value=mock_result):
            with patch("time.sleep"):
                result = d.execute({"action": "key", "key": "return"})
        assert "return" in result["text"]

    def test_scroll_down(self, tmp_path):
        d = self._driver(tmp_path)
        wda_resp = _urlopen_response({"value": None})
        with patch("urllib.request.urlopen", return_value=wda_resp):
            with patch("time.sleep"):
                result = d.execute(
                    {
                        "action": "scroll",
                        "coordinate": [512, 1113],
                        "direction": "down",
                        "amount": 3,
                    }
                )
        assert "down" in result["text"]

    def test_scroll_up(self, tmp_path):
        d = self._driver(tmp_path)
        wda_resp = _urlopen_response({"value": None})
        with patch("urllib.request.urlopen", return_value=wda_resp):
            with patch("time.sleep"):
                result = d.execute(
                    {
                        "action": "scroll",
                        "direction": "up",
                        "amount": 2,
                    }
                )
        assert "up" in result["text"]

    def test_scroll_left_right(self, tmp_path):
        d = self._driver(tmp_path)
        wda_resp = _urlopen_response({"value": None})
        for direction in ("left", "right"):
            with patch("urllib.request.urlopen", return_value=wda_resp):
                with patch("time.sleep"):
                    result = d.execute({"action": "scroll", "direction": direction})
            assert direction in result["text"]

    def test_left_click_drag(self, tmp_path):
        d = self._driver(tmp_path)
        wda_resp = _urlopen_response({"value": None})
        with patch("urllib.request.urlopen", return_value=wda_resp):
            with patch("time.sleep"):
                result = d.execute(
                    {
                        "action": "left_click_drag",
                        "start_coordinate": [100, 100],
                        "coordinate": [400, 400],
                    }
                )
        assert "Dragged" in result["text"]

    def test_wait_action(self, tmp_path):
        d = self._driver(tmp_path)
        with patch("time.sleep") as mock_sleep:
            result = d.execute({"action": "wait", "duration": 2.5})
        mock_sleep.assert_called_with(2.5)
        assert "Waited" in result["text"]

    def test_unknown_action(self, tmp_path):
        d = self._driver(tmp_path)
        result = d.execute({"action": "unsupported_xyz"})
        assert result["type"] == "text"
        assert "Unknown" in result["text"]


# ---------------------------------------------------------------------------
# launch_app
# ---------------------------------------------------------------------------


class TestLaunchApp:
    def test_uses_wda_when_session_available(self):
        d = _make_driver()
        wda_resp = _urlopen_response({"value": None})
        with patch("urllib.request.urlopen", return_value=wda_resp) as mock_open:
            with patch("subprocess.run"):
                with patch("time.sleep"):
                    d.launch_app("com.example.App")
        url = mock_open.call_args[0][0].full_url
        assert "wda/apps/launch" in url

    def test_falls_back_to_simctl_on_wda_error(self):
        d = _make_driver()
        import urllib.error

        wda_exc = urllib.error.URLError("refused")
        simctl_result = MagicMock(returncode=0)
        with patch("urllib.request.urlopen", side_effect=wda_exc):
            with patch("subprocess.run", return_value=simctl_result) as mock_run:
                with patch("time.sleep"):
                    d.launch_app("com.example.App")
        cmds = [call_args[0][0] for call_args in mock_run.call_args_list]
        simctl_calls = [c for c in cmds if "simctl" in c]
        assert len(simctl_calls) >= 1

    def test_uses_simctl_when_no_session(self):
        d = WDADriver()  # no session
        simctl_result = MagicMock(returncode=0)
        with patch("subprocess.run", return_value=simctl_result) as mock_run:
            with patch("time.sleep"):
                d.launch_app("com.example.App")
        cmds = [c[0][0] for c in mock_run.call_args_list]
        assert any("simctl" in c for c in cmds)


# ---------------------------------------------------------------------------
# device_info
# ---------------------------------------------------------------------------


class TestDeviceInfo:
    def test_returns_booted_device(self):
        d = WDADriver()
        simctl_out = json.dumps(
            {
                "devices": {
                    "com.apple.CoreSimulator.SimRuntime.iOS-18-0": [
                        {
                            "udid": "AABBCCDD-1234-5678-ABCD-000000000001",
                            "name": "iPhone 16 Pro",
                            "state": "Booted",
                        }
                    ]
                }
            }
        )
        mock_result = MagicMock(returncode=0, stdout=simctl_out)
        with patch("subprocess.run", return_value=mock_result):
            info = d.device_info()
        assert info is not None
        assert info["name"] == "iPhone 16 Pro"

    def test_resolves_booted_udid(self):
        d = WDADriver(udid="booted")
        simctl_out = json.dumps(
            {"devices": {"runtime": [{"udid": "REAL-UDID-XYZ", "name": "iPhone 15", "state": "Booted"}]}}
        )
        with patch("subprocess.run", return_value=MagicMock(returncode=0, stdout=simctl_out)):
            d.device_info()
        assert d.udid == "REAL-UDID-XYZ"

    def test_returns_none_when_no_booted_device(self):
        d = WDADriver()
        simctl_out = json.dumps({"devices": {}})
        with patch("subprocess.run", return_value=MagicMock(returncode=0, stdout=simctl_out)):
            result = d.device_info()
        assert result is None


# ---------------------------------------------------------------------------
# _require_session
# ---------------------------------------------------------------------------


class TestRequireSession:
    def test_returns_session_id_when_set(self):
        d = _make_driver()
        assert d._require_session() == "test-session-abc"

    def test_raises_when_no_session(self):
        d = WDADriver()
        with pytest.raises(WDAError, match="session"):
            d._require_session()


# ---------------------------------------------------------------------------
# repr
# ---------------------------------------------------------------------------


class TestRepr:
    def test_repr_includes_key_fields(self):
        d = _make_driver()
        r = repr(d)
        assert "WDADriver" in r
        assert "booted" in r
        assert "test-session-abc" in r
