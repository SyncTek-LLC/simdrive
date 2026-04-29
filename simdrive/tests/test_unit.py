"""Unit tests for simdrive — no live sim required."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from simdrive import server
from simdrive.window import WindowBounds


def test_version_present():
    assert server.__version__ == "0.1.0a1"


def test_tool_count_is_twelve():
    tools = server.list_tools()
    assert len(tools) == 12, f"expected 12 tools, got {len(tools)}: {[t['name'] for t in tools]}"


def test_tool_names_match_spec():
    expected = {
        "session_start", "session_end", "session_status",
        "observe",
        "tap", "swipe", "type_text", "press_key",
        "record_start", "record_stop", "replay",
        "logs",
    }
    got = {t["name"] for t in server.list_tools()}
    assert got == expected, f"missing: {expected - got}, extra: {got - expected}"


def test_every_tool_has_schema_and_handler():
    for t in server._TOOLS:
        assert "name" in t
        assert "description" in t and len(t["description"]) > 10
        assert "inputSchema" in t and t["inputSchema"]["type"] == "object"
        assert callable(t["handler"])


def test_unknown_tool_raises():
    with pytest.raises(ValueError):
        server.call_tool("does_not_exist", {})


def test_pixel_to_screen_corners():
    """Math sanity: corner pixels of the screenshot map to corners of the window."""
    from simdrive.act import _pixels_to_screen
    bounds = WindowBounds(x=1406, y=39, width=456, height=972)
    # Top-left pixel (0, 0) → (1406, 39)
    assert _pixels_to_screen(bounds, 0, 0, 1206, 2622) == (1406, 39)
    # Bottom-right pixel maps to bottom-right window
    sx, sy = _pixels_to_screen(bounds, 1206, 2622, 1206, 2622)
    assert (sx, sy) == (1406 + 456, 39 + 972)


def test_pixel_to_screen_center():
    from simdrive.act import _pixels_to_screen
    bounds = WindowBounds(x=1000, y=100, width=400, height=800)
    # Center of a 1000x2000 screenshot
    sx, sy = _pixels_to_screen(bounds, 500, 1000, 1000, 2000)
    assert (sx, sy) == (1200, 500)


def test_pixel_to_screen_invalid_dims():
    from simdrive.act import _pixels_to_screen, ActError
    bounds = WindowBounds(x=0, y=0, width=10, height=10)
    with pytest.raises(ActError):
        _pixels_to_screen(bounds, 5, 5, 0, 100)


def test_session_status_no_sessions():
    # With no started session, status should still return something coherent
    result = server.tool_session_status({})
    assert "sessions" in result
    assert "version" in result


def test_call_tool_dispatch():
    # session_status without a session_id
    result = server.call_tool("session_status", {})
    assert isinstance(result, dict)
    assert "version" in result


def test_recording_schema_round_trip(tmp_path, monkeypatch):
    """A finalized recording should be readable via yaml.safe_load."""
    import yaml
    from simdrive import recorder, session
    from simdrive.sim import Device

    monkeypatch.setenv("SIMDRIVE_HOME", str(tmp_path))

    fake_session = session.Session(
        session_id="test",
        device=Device(udid="X", name="iPhone Test", os_version="26.3", state="Booted"),
        workdir=tmp_path / "wd",
    )
    fake_session.workdir.mkdir(parents=True, exist_ok=True)

    # Use synthetic 1x1 PNGs so we don't need sim
    from PIL import Image
    pre = tmp_path / "pre.png"
    post = tmp_path / "post.png"
    Image.new("RGB", (10, 10), (255, 0, 0)).save(pre)
    Image.new("RGB", (10, 10), (0, 255, 0)).save(post)

    rec = recorder.start(fake_session, "test_recording")
    rec.add_step("tap", {"x": 100, "y": 200, "screenshot_w": 1206, "screenshot_h": 2622}, pre, post)
    yaml_path = recorder.stop(fake_session)

    payload = yaml.safe_load(yaml_path.read_text())
    assert payload["name"] == "test_recording"
    assert len(payload["steps"]) == 1
    assert payload["steps"][0]["action"] == "tap"
    assert payload["steps"][0]["args"]["x"] == 100


def test_press_key_lists_supported_keys_in_error():
    from simdrive import act
    # Force the cliclick path so we exercise the error message branch.
    # If pyobjc IS available, press_key tries pid backend which silently returns
    # False for unknown keys and falls through to cliclick path.
    with pytest.raises(act.ActError) as exc_info:
        act.press_key("totally-not-a-key")
    msg = str(exc_info.value)
    assert "supported" in msg.lower() or "Supported" in msg


def test_pid_input_capability_reports_state():
    from simdrive import pid_input
    cap = pid_input.capability()
    # Quartz availability is a fact about the install; just ensure the call works.
    assert isinstance(cap.quartz, bool)
    assert cap.sim_pid is None or isinstance(cap.sim_pid, int)


def test_session_status_reports_mode():
    result = server.tool_session_status({})
    assert "mode" in result
    assert result["mode"] in {"background", "foreground"}
    assert "mode_note" in result


def test_pid_input_ascii_keycode_table_covers_alphanumeric():
    from simdrive.pid_input import _build_ascii_keycodes
    table = _build_ascii_keycodes()
    for ch in "abcdefghijklmnopqrstuvwxyz0123456789":
        assert ch in table, f"missing keycode for {ch!r}"
    for ch in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
        assert ch in table, f"missing keycode for {ch!r}"
        assert table[ch][1] is True, f"{ch!r} should require shift"


def test_som_find_by_text():
    from simdrive.som import Mark, find_by_text
    marks = [
        Mark(id=1, x=0, y=0, w=100, h=20, text="Settings", confidence=0.9),
        Mark(id=2, x=0, y=30, w=100, h=20, text="Don't Allow", confidence=0.95),
        Mark(id=3, x=0, y=60, w=100, h=20, text="Allow Once", confidence=0.92),
    ]
    assert find_by_text(marks, "Settings").id == 1
    assert find_by_text(marks, "settings").id == 1  # case-insensitive
    assert find_by_text(marks, "Don't").id == 2  # prefix
    assert find_by_text(marks, "Allow").id in {2, 3}  # both contain
    assert find_by_text(marks, "nothing-here") is None


def test_som_find_by_mark_id():
    from simdrive.som import Mark, find_by_mark_id
    marks = [Mark(id=1, x=0, y=0, w=10, h=10, text="a", confidence=1.0)]
    assert find_by_mark_id(marks, 1).text == "a"
    assert find_by_mark_id(marks, 99) is None


def test_resolve_target_xy_coords(monkeypatch, tmp_path):
    """{x, y} resolves to those literal coords."""
    from simdrive import server, session as ses
    from simdrive.sim import Device
    s = ses.Session(
        session_id="t", device=Device(udid="X", name="iPhone Test", os_version="26.3", state="Booted"),
        workdir=tmp_path,
    )
    x, y, via = server._resolve_target_xy(s, {"x": 100, "y": 200})
    assert (x, y) == (100, 200)
    assert via == "coords"


def test_resolve_target_xy_mark(tmp_path):
    from simdrive import server, session as ses, som
    from simdrive.sim import Device
    s = ses.Session(
        session_id="t", device=Device(udid="X", name="iPhone Test", os_version="26.3", state="Booted"),
        workdir=tmp_path,
        last_marks=[som.Mark(id=5, x=100, y=200, w=40, h=20, text="OK", confidence=0.9)],
    )
    x, y, via = server._resolve_target_xy(s, {"mark": 5})
    assert (x, y) == (120, 210)  # center of bbox
    assert "mark:5" in via


def test_resolve_target_xy_text(tmp_path):
    from simdrive import server, session as ses, som
    from simdrive.sim import Device
    s = ses.Session(
        session_id="t", device=Device(udid="X", name="iPhone Test", os_version="26.3", state="Booted"),
        workdir=tmp_path,
        last_marks=[som.Mark(id=2, x=0, y=0, w=200, h=40, text="Don't Allow", confidence=0.95)],
    )
    x, y, via = server._resolve_target_xy(s, {"text": "Don't Allow"})
    assert (x, y) == (100, 20)
    assert "text:" in via


def test_resolve_target_xy_missing_raises(tmp_path):
    from simdrive import server, session as ses
    from simdrive.sim import Device
    s = ses.Session(
        session_id="t", device=Device(udid="X", name="iPhone Test", os_version="26.3", state="Booted"),
        workdir=tmp_path,
    )
    with pytest.raises(ValueError):
        server._resolve_target_xy(s, {})


def test_resolve_target_xy_mark_not_found(tmp_path):
    from simdrive import server, session as ses
    from simdrive.sim import Device
    s = ses.Session(
        session_id="t", device=Device(udid="X", name="iPhone Test", os_version="26.3", state="Booted"),
        workdir=tmp_path,
        last_marks=[],
    )
    with pytest.raises(ValueError, match="not found"):
        server._resolve_target_xy(s, {"mark": 1})
