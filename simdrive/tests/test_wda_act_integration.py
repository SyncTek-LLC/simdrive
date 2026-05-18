"""Integration tests: act dispatch routing for target=device via WdaClient.

Verifies that server.py tool_tap / tool_swipe / tool_type_text / tool_press_key /
tool_clear_field route through WdaClient when session.target == "device", and
that the MCP return shape is identical to the simulator path.

All WDA HTTP calls are mocked at the WdaClient level via monkeypatching
registry.load() and WdaClient methods — no real device or network needed.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# ── session factory ───────────────────────────────────────────────────────────


def _make_device_session(tmp_path: Path, udid: str = "TEST-UDID-0001") -> "session_mod.Session":
    """Create an in-memory Session with target='device' and a registered WDA entry."""
    from simdrive import session as session_mod
    from simdrive.sim import Device

    d = Device(udid=udid, name="Moes Max", os_version="26.3.1", state="active")
    workdir = tmp_path / "sessions" / "testsession"
    workdir.mkdir(parents=True, exist_ok=True)
    s = session_mod.Session(
        session_id="testsession",
        device=d,
        workdir=workdir,
        target="device",
        last_screenshot_w=390,
        last_screenshot_h=844,
    )
    session_mod._SESSIONS["testsession"] = s
    return s


@pytest.fixture(autouse=True)
def cleanup_sessions():
    """Remove test sessions from global registry after each test."""
    from simdrive import session as session_mod
    yield
    session_mod._SESSIONS.pop("testsession", None)


# ── WdaClient mock factory ────────────────────────────────────────────────────


_ONE_PX_PNG = bytes.fromhex(
    "89504e470d0a1a0a"
    "0000000d49484452"
    "00000001"
    "00000001"
    "08060000001f15c489"
    "0000000a49444154"
    "789c6260000000020001"
    "e221bc33"
    "0000000049454e44ae426082"
)


def _mock_wda_client() -> MagicMock:
    """Return a MagicMock that looks like a WdaClient.

    screenshot_any returns valid 1×1 PNG bytes so device-path post-observe calls
    (tool_observe → wda.screenshot_any()) don't crash on write_bytes.
    """
    client = MagicMock()
    client.tap.return_value = None
    client.swipe.return_value = None
    client.type_text.return_value = None
    client.press_key.return_value = None
    client.clear_field.return_value = None
    # a12 — device post-type observe calls tool_observe which calls screenshot_any;
    # provide real bytes so write_bytes() does not raise TypeError.
    client.screenshot_any.return_value = _ONE_PX_PNG
    # source() is called by annotate_device_screenshot; return empty XML so it
    # short-circuits to marks=[] without error.
    client.source.return_value = ""
    return client


# ── tool_tap ──────────────────────────────────────────────────────────────────


def test_tool_tap_device_routes_to_wda(tmp_path):
    """tool_tap with target=device must call WdaClient.tap and return ok=True."""
    s = _make_device_session(tmp_path)
    mock_client = _mock_wda_client()

    registry_entry = {
        "host": "localhost", "port": 8100,
        "wda_bundle_id": "com.facebook.WebDriverAgentRunner.xctrunner",
        "signing_identity": "Apple Development: Test",
        "team_id": "E52N8732YT",
    }

    with patch("simdrive.wda.registry.load", return_value=registry_entry), \
         patch("simdrive.wda.client.WdaClient") as mock_cls:
        mock_cls.return_value = mock_client

        from simdrive.server import tool_tap
        result = tool_tap({
            "session_id": "testsession",
            "x": 100,
            "y": 200,
        })

    assert result["ok"] is True
    assert "pixel_x" in result
    assert "pixel_y" in result
    assert "screenshot_size_pixels" in result
    mock_client.tap.assert_called_once()


def test_tool_tap_device_return_shape_matches_simulator(tmp_path):
    """Device tap must return the same fields as the simulator path."""
    s = _make_device_session(tmp_path)
    mock_client = _mock_wda_client()
    registry_entry = {"host": "localhost", "port": 8100}

    with patch("simdrive.wda.registry.load", return_value=registry_entry), \
         patch("simdrive.wda.client.WdaClient") as mock_cls:
        mock_cls.return_value = mock_client

        from simdrive.server import tool_tap
        result = tool_tap({"session_id": "testsession", "x": 50, "y": 80})

    # Must have the same required fields as simulator path
    for field in ("ok", "pixel_x", "pixel_y", "screenshot_size_pixels", "resolved_via"):
        assert field in result, f"Missing field {field!r} in device tool_tap response"


# ── tool_swipe ────────────────────────────────────────────────────────────────


def test_tool_swipe_device_routes_to_wda(tmp_path):
    s = _make_device_session(tmp_path)
    mock_client = _mock_wda_client()
    registry_entry = {"host": "localhost", "port": 8100}

    with patch("simdrive.wda.registry.load", return_value=registry_entry), \
         patch("simdrive.wda.client.WdaClient") as mock_cls:
        mock_cls.return_value = mock_client

        from simdrive.server import tool_swipe
        result = tool_swipe({
            "session_id": "testsession",
            "x1": 100, "y1": 200,
            "x2": 100, "y2": 400,
            "duration_ms": 300,
        })

    assert result["ok"] is True
    mock_client.swipe.assert_called_once()


def test_tool_swipe_device_return_shape(tmp_path):
    s = _make_device_session(tmp_path)
    mock_client = _mock_wda_client()
    registry_entry = {"host": "localhost", "port": 8100}

    with patch("simdrive.wda.registry.load", return_value=registry_entry), \
         patch("simdrive.wda.client.WdaClient") as mock_cls:
        mock_cls.return_value = mock_client

        from simdrive.server import tool_swipe
        result = tool_swipe({
            "session_id": "testsession",
            "x1": 10, "y1": 20, "x2": 30, "y2": 40,
        })

    assert "ok" in result
    assert "resolved_via" in result


# ── tool_type_text ────────────────────────────────────────────────────────────


def test_tool_type_text_device_routes_to_wda(tmp_path):
    s = _make_device_session(tmp_path)
    mock_client = _mock_wda_client()
    registry_entry = {"host": "localhost", "port": 8100}

    # a12 — device type_text post-observe calls tool_observe (device code path),
    # not observe.observe.  Patch annotate_device_screenshot to return empty marks.
    with patch("simdrive.wda.registry.load", return_value=registry_entry), \
         patch("simdrive.wda.client.WdaClient") as mock_cls, \
         patch("simdrive.wda.som_device.annotate_device_screenshot", return_value=([], None)):
        mock_cls.return_value = mock_client

        from simdrive.server import tool_type_text
        result = tool_type_text({"session_id": "testsession", "text": "hello"})

    assert result["ok"] is True
    assert result["chars"] == 5
    # injection_method must be present (device path uses "wda")
    assert "injection_method" in result
    assert result["injection_method"] == "wda"
    mock_client.type_text.assert_called_once_with("hello")


def test_tool_type_text_device_return_shape(tmp_path):
    """Device type_text must match simulator return shape."""
    s = _make_device_session(tmp_path)
    mock_client = _mock_wda_client()
    registry_entry = {"host": "localhost", "port": 8100}

    # a12 — device type_text post-observe calls tool_observe (device code path).
    with patch("simdrive.wda.registry.load", return_value=registry_entry), \
         patch("simdrive.wda.client.WdaClient") as mock_cls, \
         patch("simdrive.wda.som_device.annotate_device_screenshot", return_value=([], None)):
        mock_cls.return_value = mock_client
        from simdrive.server import tool_type_text
        result = tool_type_text({"session_id": "testsession", "text": "abc"})

    for field in ("ok", "chars", "injection_method", "dispatch_succeeded",
                  "keyboard_visible", "focused_field"):
        assert field in result, f"Missing field {field!r} in device type_text response"


# ── tool_press_key ────────────────────────────────────────────────────────────


def test_tool_press_key_device_routes_to_wda(tmp_path):
    s = _make_device_session(tmp_path)
    mock_client = _mock_wda_client()
    registry_entry = {"host": "localhost", "port": 8100}

    with patch("simdrive.wda.registry.load", return_value=registry_entry), \
         patch("simdrive.wda.client.WdaClient") as mock_cls:
        mock_cls.return_value = mock_client

        from simdrive.server import tool_press_key
        result = tool_press_key({"session_id": "testsession", "key": "home"})

    assert result["ok"] is True
    assert result["key"] == "home"
    mock_client.press_key.assert_called_once_with("home")


def test_tool_press_key_device_return_shape(tmp_path):
    s = _make_device_session(tmp_path)
    mock_client = _mock_wda_client()
    registry_entry = {"host": "localhost", "port": 8100}

    with patch("simdrive.wda.registry.load", return_value=registry_entry), \
         patch("simdrive.wda.client.WdaClient") as mock_cls:
        mock_cls.return_value = mock_client
        from simdrive.server import tool_press_key
        result = tool_press_key({"session_id": "testsession", "key": "volumeUp"})

    assert "ok" in result
    assert "key" in result


# ── tool_clear_field ──────────────────────────────────────────────────────────


def test_tool_clear_field_device_routes_to_wda(tmp_path):
    s = _make_device_session(tmp_path)
    mock_client = _mock_wda_client()
    registry_entry = {"host": "localhost", "port": 8100}

    with patch("simdrive.wda.registry.load", return_value=registry_entry), \
         patch("simdrive.wda.client.WdaClient") as mock_cls:
        mock_cls.return_value = mock_client

        from simdrive.server import tool_clear_field
        result = tool_clear_field({"session_id": "testsession"})

    assert result["ok"] is True
    mock_client.clear_field.assert_called_once()


# ── simulator path unaffected ─────────────────────────────────────────────────


def test_tool_tap_simulator_path_unchanged(tmp_path):
    """Simulator sessions must NOT use WdaClient — the existing HID/cliclick path."""
    from simdrive import session as session_mod
    from simdrive.sim import Device

    d = Device(udid="SIM-UDID", name="iPhone 16 Pro", os_version="18.0", state="active")
    workdir = tmp_path / "sim_session"
    workdir.mkdir(parents=True, exist_ok=True)
    s = session_mod.Session(
        session_id="sim_session",
        device=d,
        workdir=workdir,
        target="simulator",
        last_screenshot_w=390,
        last_screenshot_h=844,
    )
    session_mod._SESSIONS["sim_session"] = s

    wda_client_called = []

    def _fake_wda_client(*args, **kwargs):
        wda_client_called.append(True)
        return _mock_wda_client()

    with patch("simdrive.act.hid_inject") as mock_hid, \
         patch("simdrive.wda.client.WdaClient", side_effect=_fake_wda_client):
        mock_hid.available.return_value = False  # force cliclick path
        with patch("simdrive.act._run_cliclick"):
            with patch("simdrive.act.get_bounds") as mock_bounds, \
                 patch("simdrive.act.activate"):
                mock_bounds.return_value = MagicMock(x=0, y=0, width=390, height=844)
                from simdrive.server import tool_tap
                result = tool_tap({"session_id": "sim_session", "x": 50, "y": 80})

    assert result["ok"] is True
    # WdaClient must NOT have been instantiated for simulator sessions
    assert not wda_client_called, "WdaClient should not be used for simulator sessions"

    session_mod._SESSIONS.pop("sim_session", None)


# ── registry missing raises graceful error ────────────────────────────────────


def test_tool_tap_device_no_registry_raises(tmp_path):
    """If no registry entry exists for the device, tap must raise a clear error."""
    from simdrive.errors import SimdriveError
    s = _make_device_session(tmp_path)

    with patch("simdrive.wda.registry.load", return_value=None):
        from simdrive.server import tool_tap
        with pytest.raises(SimdriveError) as exc:
            tool_tap({"session_id": "testsession", "x": 50, "y": 80})
    assert "Recovery:" in exc.value.message
