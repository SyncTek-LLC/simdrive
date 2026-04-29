"""Unit tests for simdrive — no live sim required."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from simdrive import server
from simdrive.window import WindowBounds


def test_version_present():
    assert server.__version__ == "0.1.0a2"


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


def test_hid_inject_binary_exists():
    from simdrive import hid_inject
    p = hid_inject._binary_path()
    assert p is not None and p.exists()


def test_hid_inject_available():
    from simdrive import hid_inject
    assert hid_inject.available() is True


def test_session_status_reports_mode():
    result = server.tool_session_status({})
    assert "mode" in result
    assert result["mode"] in {"background", "foreground"}
    assert "mode_note" in result


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
    from simdrive import server, session as ses, errors as err
    from simdrive.sim import Device
    s = ses.Session(
        session_id="t", device=Device(udid="X", name="iPhone Test", os_version="26.3", state="Booted"),
        workdir=tmp_path,
    )
    with pytest.raises(err.SimdriveError) as exc_info:
        server._resolve_target_xy(s, {})
    assert exc_info.value.code == "missing_target"


def test_resolve_target_xy_mark_not_found(tmp_path):
    from simdrive import server, session as ses, errors as err
    from simdrive.sim import Device
    s = ses.Session(
        session_id="t", device=Device(udid="X", name="iPhone Test", os_version="26.3", state="Booted"),
        workdir=tmp_path,
        last_marks=[],
    )
    with pytest.raises(err.SimdriveError) as exc_info:
        server._resolve_target_xy(s, {"mark": 1})
    assert exc_info.value.code == "target_not_found"


def test_simdrive_error_to_dict():
    from simdrive import errors as err
    e = err.no_session("abc")
    d = e.to_dict()
    assert d["ok"] is False
    assert d["error"]["code"] == "no_session"
    assert d["error"]["details"]["session_id"] == "abc"


def test_no_device_error_codes():
    from simdrive import errors as err
    e1 = err.no_device({"udid": "X"})
    assert e1.code == "no_device"
    assert "udid" in e1.details["query"]


def test_multi_session_isolation(tmp_path, monkeypatch):
    """Two sessions running in the same process must keep separate state."""
    monkeypatch.setenv("SIMDRIVE_HOME", str(tmp_path))
    from simdrive import session as ses
    from simdrive.sim import Device

    # Reset module-level session dict
    ses._SESSIONS.clear()

    d1 = Device(udid="UDID-AAA", name="iPhone A", os_version="26.3", state="Booted")
    d2 = Device(udid="UDID-BBB", name="iPhone B", os_version="26.3", state="Booted")

    # Direct construction (bypassing simctl boot calls)
    s1 = ses.Session(session_id="alpha", device=d1, workdir=tmp_path / "alpha")
    s1.last_marks = ["mark-from-A"]
    s2 = ses.Session(session_id="beta", device=d2, workdir=tmp_path / "beta")
    s2.last_marks = ["mark-from-B"]

    ses._SESSIONS["alpha"] = s1
    ses._SESSIONS["beta"] = s2

    assert ses.get("alpha").device.udid == "UDID-AAA"
    assert ses.get("beta").device.udid == "UDID-BBB"
    assert ses.get("alpha").last_marks == ["mark-from-A"]
    assert ses.get("beta").last_marks == ["mark-from-B"]

    ses.end("alpha")
    assert "alpha" not in ses._SESSIONS
    assert "beta" in ses._SESSIONS


def test_pasteboard_helper_call_signature():
    """Smoke-test that set_pasteboard exists with the expected signature."""
    from simdrive import sim
    assert callable(sim.set_pasteboard)


def test_chord_helper_call_signature():
    from simdrive import hid_inject
    assert callable(hid_inject.chord)


def test_mark_stable_id_is_position_text_hash():
    from simdrive.som import Mark
    m1 = Mark(id=1, x=100, y=200, w=80, h=20, text="Borrow", confidence=1.0)
    m2 = Mark(id=99, x=103, y=205, w=80, h=20, text="Borrow", confidence=1.0)
    # Same text, position within the 20px bucket → same stable_id
    assert m1.stable_id == m2.stable_id
    # Different text → different stable_id
    m3 = Mark(id=1, x=100, y=200, w=80, h=20, text="Return", confidence=1.0)
    assert m1.stable_id != m3.stable_id


def test_find_by_stable_id():
    from simdrive.som import Mark, find_by_stable_id
    marks = [
        Mark(id=1, x=0, y=0, w=10, h=10, text="A", confidence=1.0),
        Mark(id=2, x=50, y=50, w=10, h=10, text="B", confidence=1.0),
    ]
    target = marks[1].stable_id
    assert find_by_stable_id(marks, target).text == "B"
    assert find_by_stable_id(marks, "nope") is None


def test_observe_writes_sidecar_json(tmp_path, monkeypatch):
    """observe() should drop a <screenshot>.json next to the PNG."""
    from simdrive import observe
    from PIL import Image
    monkeypatch.setattr(observe, "sim", _stub_sim_for_observe(tmp_path))

    out_dir = tmp_path / "obs"
    obs = observe.observe(udid="X", out_dir=out_dir, annotate=False)
    sidecar = obs.screenshot_path.with_suffix(".json")
    assert sidecar.exists()
    import json as _j
    payload = _j.loads(sidecar.read_text())
    assert "screenshot_path" in payload
    assert "marks" in payload


def _stub_sim_for_observe(tmp_path):
    """Build a stub `sim` namespace that yields a 100x100 PNG without simctl."""
    from PIL import Image
    class StubSim:
        @staticmethod
        def screenshot(udid, dest):
            Image.new("RGB", (100, 100), (200, 200, 200)).save(dest)
            return dest
        @staticmethod
        def get_log_tail(*args, **kwargs):
            return ""
    return StubSim


def test_session_append_action_writes_jsonl(tmp_path):
    from simdrive import session as ses
    from simdrive.sim import Device
    s = ses.Session(
        session_id="aud",
        device=Device(udid="X", name="iPhone Test", os_version="26.3", state="Booted"),
        workdir=tmp_path,
    )
    ses.append_action(s, {"action": "tap", "args": {"x": 10, "y": 20}})
    ses.append_action(s, {"action": "press_key", "args": {"key": "home"}})
    log = (tmp_path / "actions.jsonl").read_text().strip().splitlines()
    assert len(log) == 2
    import json as _j
    assert _j.loads(log[0])["action"] == "tap"
    assert _j.loads(log[1])["action"] == "press_key"
