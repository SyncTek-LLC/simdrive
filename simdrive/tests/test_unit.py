"""Unit tests for simdrive — no live sim required."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from simdrive import server
from simdrive.window import WindowBounds


def test_version_present():
    assert server.__version__ == "0.2.0a2"


def test_tool_count_is_thirteen():
    tools = server.list_tools()
    assert len(tools) == 13, f"expected 13 tools, got {len(tools)}: {[t['name'] for t in tools]}"


def test_tool_names_match_spec():
    expected = {
        "session_start", "session_end", "session_status",
        "observe",
        "tap", "swipe", "type_text", "press_key",
        "record_start", "record_stop", "replay",
        "logs", "list_devices",
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
    """{x, y} resolves to those literal coords; matched_mark is None for raw coords."""
    from simdrive import server, session as ses
    from simdrive.sim import Device
    s = ses.Session(
        session_id="t", device=Device(udid="X", name="iPhone Test", os_version="26.3", state="Booted"),
        workdir=tmp_path,
    )
    x, y, via, matched = server._resolve_target_xy(s, {"x": 100, "y": 200})
    assert (x, y) == (100, 200)
    assert via == "coords"
    assert matched is None


def test_resolve_target_xy_mark(tmp_path):
    from simdrive import server, session as ses, som
    from simdrive.sim import Device
    target = som.Mark(id=5, x=100, y=200, w=40, h=20, text="OK", confidence=0.9)
    s = ses.Session(
        session_id="t", device=Device(udid="X", name="iPhone Test", os_version="26.3", state="Booted"),
        workdir=tmp_path,
        last_marks=[target],
    )
    x, y, via, matched = server._resolve_target_xy(s, {"mark": 5})
    assert (x, y) == (120, 210)  # center of bbox
    assert "mark:5" in via
    assert matched is target


def test_resolve_target_xy_text(tmp_path):
    from simdrive import server, session as ses, som
    from simdrive.sim import Device
    target = som.Mark(id=2, x=0, y=0, w=200, h=40, text="Don't Allow", confidence=0.95)
    s = ses.Session(
        session_id="t", device=Device(udid="X", name="iPhone Test", os_version="26.3", state="Booted"),
        workdir=tmp_path,
        last_marks=[target],
    )
    x, y, via, matched = server._resolve_target_xy(s, {"text": "Don't Allow"})
    assert (x, y) == (100, 20)
    assert "text:" in via
    assert matched is target


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


def test_device_input_tools_raise_on_device_target(tmp_path):
    """tap/swipe/type_text/press_key must surface device_input_unavailable when target=device."""
    from simdrive import server, session as ses, errors as err
    from simdrive.sim import Device
    ses._SESSIONS.clear()

    sid = "dev-only"
    s = ses.Session(
        session_id=sid,
        device=Device(udid="X", name="iPad", os_version="iPadOS 17", state="active"),
        workdir=tmp_path,
        target="device",
    )
    ses._SESSIONS[sid] = s

    for fn, args in (
        (server.tool_tap, {"session_id": sid, "x": 100, "y": 100}),
        (server.tool_swipe, {"session_id": sid, "x1": 0, "y1": 0, "x2": 1, "y2": 1}),
        (server.tool_type_text, {"session_id": sid, "text": "hi"}),
        (server.tool_press_key, {"session_id": sid, "key": "home"}),
    ):
        with pytest.raises(err.SimdriveError) as exc:
            fn(args)
        assert exc.value.code == "device_input_unavailable", (
            f"{fn.__name__} should raise device_input_unavailable, got {exc.value.code}"
        )


def test_session_start_invalid_target_raises():
    from simdrive import server, errors as err
    with pytest.raises(err.SimdriveError) as exc:
        server.tool_session_start({"target": "android"})
    assert exc.value.code == "invalid_argument"


def test_device_module_imports_and_exposes_helpers():
    from simdrive import device
    assert callable(device.list_devices)
    assert callable(device.find_device)
    assert callable(device.screenshot)
    assert callable(device.get_log_tail)
    ok, missing = device.libimobiledevice_available()
    # macOS test host either has it or it's expected to fail gracefully.
    assert isinstance(ok, bool)
    assert isinstance(missing, list)


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


# ----------------- v0.2.0a2 dogfood-feedback fixes ----------------- #


def test_observe_annotate_false_preserves_last_marks(tmp_path, monkeypatch):
    """observe(annotate=false) returns marks=[] but must NOT wipe the session's mark cache."""
    from simdrive import server, session as ses, observe as obs_mod, som
    from simdrive.sim import Device

    ses._SESSIONS.clear()
    sid = "preserve"
    cached_mark = som.Mark(id=1, x=10, y=20, w=80, h=20, text="OK", confidence=0.95)
    s = ses.Session(
        session_id=sid,
        device=Device(udid="X", name="iPhone Test", os_version="26.3", state="Booted"),
        workdir=tmp_path,
        last_screenshot_w=1206,
        last_screenshot_h=2622,
        last_marks=[cached_mark],
    )
    ses._SESSIONS[sid] = s

    # Stub observe.observe to return an Observation with empty marks.
    from simdrive.observe import Observation
    fake_screenshot = tmp_path / "fake.png"
    from PIL import Image
    Image.new("RGB", (100, 200), (10, 10, 10)).save(fake_screenshot)

    def fake_observe(udid, out_dir, **kwargs):
        return Observation(
            screenshot_path=fake_screenshot,
            annotated_path=None,
            screenshot_w=100,
            screenshot_h=200,
            window_bounds=None,
            captured_at=0.0,
            marks=[],
        )

    monkeypatch.setattr(obs_mod, "observe", fake_observe)

    server.tool_observe({"session_id": sid, "annotate": False})

    # Mark cache must still hold the original — the screen state did update.
    assert s.last_marks == [cached_mark]
    assert s.last_screenshot_w == 100
    assert s.last_screenshot_h == 200


def test_recording_includes_stable_id_when_resolved_via_stable_id(tmp_path, monkeypatch):
    """tool_tap with {stable_id: ...} must record stable_id + text alongside pixel coords."""
    from simdrive import server, session as ses, recorder as rec_mod, act, observe as obs_mod, som
    from simdrive.sim import Device
    from simdrive.observe import Observation
    from PIL import Image

    monkeypatch.setenv("SIMDRIVE_HOME", str(tmp_path))
    ses._SESSIONS.clear()

    target_mark = som.Mark(id=3, x=100, y=200, w=80, h=20, text="Borrow", confidence=0.95)
    target_sid = target_mark.stable_id

    sid = "rec-stable"
    pre_path = tmp_path / "pre.png"
    Image.new("RGB", (1206, 2622), (250, 250, 250)).save(pre_path)
    s = ses.Session(
        session_id=sid,
        device=Device(udid="X", name="iPhone Test", os_version="26.3", state="Booted"),
        workdir=tmp_path / "wd",
        last_screenshot_w=1206,
        last_screenshot_h=2622,
        last_screenshot_path=pre_path,
        last_marks=[target_mark],
    )
    s.workdir.mkdir(parents=True, exist_ok=True)
    ses._SESSIONS[sid] = s

    # Stub act.tap so we don't drive a real sim.
    monkeypatch.setattr(act, "tap", lambda x, y, sw, sh, udid=None: (x, y))

    # Stub the post-step observe (called inside _record_act_step).
    post_path = tmp_path / "post.png"
    Image.new("RGB", (1206, 2622), (200, 200, 200)).save(post_path)

    def fake_observe(udid, out_dir, **kwargs):
        return Observation(
            screenshot_path=post_path,
            annotated_path=None,
            screenshot_w=1206,
            screenshot_h=2622,
            window_bounds=None,
            captured_at=0.0,
            marks=[],
        )

    monkeypatch.setattr(obs_mod, "observe", fake_observe)

    rec_mod.start(s, "stable-id-recording")
    server.tool_tap({"session_id": sid, "stable_id": target_sid})

    assert s.recorder is not None
    last_step = s.recorder.steps[-1]
    assert last_step["action"] == "tap"
    args = last_step["args"]
    assert "x" in args and "y" in args
    assert args.get("stable_id") == target_sid
    assert args.get("text") == "Borrow"


def _write_replay_recording(rec_dir, step_args):
    """Helper: write a single-step recording.yaml under rec_dir for replay tests."""
    import yaml as _yaml
    from PIL import Image
    snaps = rec_dir / "snapshots"
    snaps.mkdir(parents=True, exist_ok=True)
    pre = snaps / "001_pre.png"
    post = snaps / "001_post.png"
    Image.new("RGB", (1206, 2622), (240, 240, 240)).save(pre)
    Image.new("RGB", (1206, 2622), (200, 200, 200)).save(post)
    payload = {
        "name": rec_dir.name,
        "created_at": 0.0,
        "device": "iPhone Test",
        "os_version": "26.3",
        "app_bundle_id": None,
        "steps": [
            {
                "id": 1,
                "action": "tap",
                "args": step_args,
                "pre_screenshot": "snapshots/001_pre.png",
                "post_screenshot": "snapshots/001_post.png",
                "captured_at": 0.0,
            }
        ],
    }
    (rec_dir / "recording.yaml").write_text(_yaml.safe_dump(payload, sort_keys=False))


def test_replay_prefers_stable_id_with_pixel_fallback(tmp_path, monkeypatch):
    """Replay re-resolves stable_id against the live observe and taps the live center."""
    from simdrive import recorder as rec_mod, session as ses, act, observe as obs_mod, som
    from simdrive.sim import Device
    from simdrive.observe import Observation
    from PIL import Image

    monkeypatch.setenv("SIMDRIVE_HOME", str(tmp_path))
    ses._SESSIONS.clear()

    rec_name = "stable-replay"
    rec_dir = tmp_path / "recordings" / rec_name
    _write_replay_recording(rec_dir, {
        "x": 999, "y": 999,
        "screenshot_w": 1206, "screenshot_h": 2622,
        "stable_id": "abc",
        "text": "Borrow",
    })

    s = ses.Session(
        session_id="t",
        device=Device(udid="X", name="iPhone Test", os_version="26.3", state="Booted"),
        workdir=tmp_path / "wd",
    )
    s.workdir.mkdir(parents=True, exist_ok=True)

    # Live screenshot for the SSIM compare.
    live_path = tmp_path / "live.png"
    Image.new("RGB", (1206, 2622), (240, 240, 240)).save(live_path)

    # Stub som.find_by_stable_id to return a Mark at (100, 200) center for "abc".
    fake_mark = som.Mark(id=1, x=80, y=190, w=40, h=20, text="Borrow", confidence=0.95)
    # Adjust so center is (100, 200): center_x = 80 + 40//2 = 100, center_y = 190 + 20//2 = 200.
    assert fake_mark.center == (100, 200)

    def fake_observe(udid, out_dir, **kwargs):
        return Observation(
            screenshot_path=live_path,
            annotated_path=None,
            screenshot_w=1206,
            screenshot_h=2622,
            window_bounds=None,
            captured_at=0.0,
            marks=[fake_mark],
        )

    monkeypatch.setattr(obs_mod, "observe", fake_observe)
    # Force find_by_stable_id to match "abc" → fake_mark, regardless of hash equality.
    monkeypatch.setattr(som, "find_by_stable_id",
                        lambda marks, sid: fake_mark if sid == "abc" else None)

    captured = {}

    def fake_tap(px, py, sw, sh, udid=None):
        captured["px"] = px
        captured["py"] = py
        return px, py

    monkeypatch.setattr(act, "tap", fake_tap)

    result = rec_mod.replay(rec_name, s, on_drift="force")
    assert result["ok"] is True
    assert captured == {"px": 100, "py": 200}


def test_replay_falls_back_to_pixels_when_stable_id_missing(tmp_path, monkeypatch):
    """If the live observation lacks the stable_id, replay falls back to recorded pixels."""
    from simdrive import recorder as rec_mod, session as ses, act, observe as obs_mod, som
    from simdrive.sim import Device
    from simdrive.observe import Observation
    from PIL import Image

    monkeypatch.setenv("SIMDRIVE_HOME", str(tmp_path))
    ses._SESSIONS.clear()

    rec_name = "stable-fallback"
    rec_dir = tmp_path / "recordings" / rec_name
    _write_replay_recording(rec_dir, {
        "x": 999, "y": 999,
        "screenshot_w": 1206, "screenshot_h": 2622,
        "stable_id": "abc",
        "text": "Borrow",
    })

    s = ses.Session(
        session_id="t",
        device=Device(udid="X", name="iPhone Test", os_version="26.3", state="Booted"),
        workdir=tmp_path / "wd",
    )
    s.workdir.mkdir(parents=True, exist_ok=True)

    live_path = tmp_path / "live.png"
    Image.new("RGB", (1206, 2622), (240, 240, 240)).save(live_path)

    def fake_observe(udid, out_dir, **kwargs):
        return Observation(
            screenshot_path=live_path,
            annotated_path=None,
            screenshot_w=1206,
            screenshot_h=2622,
            window_bounds=None,
            captured_at=0.0,
            marks=[],  # nothing to match against
        )

    monkeypatch.setattr(obs_mod, "observe", fake_observe)
    monkeypatch.setattr(som, "find_by_stable_id", lambda marks, sid: None)

    captured = {}

    def fake_tap(px, py, sw, sh, udid=None):
        captured["px"] = px
        captured["py"] = py
        return px, py

    monkeypatch.setattr(act, "tap", fake_tap)

    result = rec_mod.replay(rec_name, s, on_drift="force")
    assert result["ok"] is True
    assert captured == {"px": 999, "py": 999}


def test_type_text_response_shape(tmp_path, monkeypatch):
    """type_text returns ok/chars/keyboard_visible/focused_field; tap_first stable_id surfaces as focused_field."""
    from simdrive import server, session as ses, act, observe as obs_mod, som
    from simdrive.sim import Device
    from simdrive.observe import Observation
    from PIL import Image

    ses._SESSIONS.clear()
    sid = "tt-shape"

    target_mark = som.Mark(id=1, x=100, y=200, w=120, h=40, text="Username", confidence=0.95)
    target_sid = target_mark.stable_id

    pre_path = tmp_path / "pre.png"
    Image.new("RGB", (1206, 2622), (240, 240, 240)).save(pre_path)
    s = ses.Session(
        session_id=sid,
        device=Device(udid="X", name="iPhone Test", os_version="26.3", state="Booted"),
        workdir=tmp_path / "wd",
        last_screenshot_w=1206,
        last_screenshot_h=2622,
        last_screenshot_path=pre_path,
        last_marks=[target_mark],
    )
    s.workdir.mkdir(parents=True, exist_ok=True)
    ses._SESSIONS[sid] = s

    monkeypatch.setattr(act, "tap", lambda x, y, sw, sh, udid=None: (x, y))
    monkeypatch.setattr(act, "type_text", lambda text, udid=None: None)

    # Post-type observe returns marks indicating a keyboard: a "return" key plus a single-char
    # mark in the bottom half of the 2622-tall screenshot.
    return_mark = som.Mark(id=10, x=900, y=2300, w=80, h=40, text="return", confidence=0.95)
    a_mark = som.Mark(id=11, x=300, y=2400, w=20, h=40, text="A", confidence=0.95)

    def fake_observe(udid, out_dir, **kwargs):
        return Observation(
            screenshot_path=pre_path,
            annotated_path=None,
            screenshot_w=1206,
            screenshot_h=2622,
            window_bounds=None,
            captured_at=0.0,
            marks=[return_mark, a_mark],
        )

    monkeypatch.setattr(obs_mod, "observe", fake_observe)

    result = server.tool_type_text({
        "session_id": sid,
        "text": "hi",
        "tap_first": {"stable_id": target_sid},
    })
    assert result["ok"] is True
    assert result["chars"] == 2
    assert result["keyboard_visible"] is True
    assert result["focused_field"] == target_sid


def test_type_text_response_no_focus_when_no_tap_first(tmp_path, monkeypatch):
    """Without tap_first, focused_field is None even if keyboard is visible."""
    from simdrive import server, session as ses, act, observe as obs_mod, som
    from simdrive.sim import Device
    from simdrive.observe import Observation
    from PIL import Image

    ses._SESSIONS.clear()
    sid = "tt-nofocus"

    pre_path = tmp_path / "pre.png"
    Image.new("RGB", (1206, 2622), (240, 240, 240)).save(pre_path)
    s = ses.Session(
        session_id=sid,
        device=Device(udid="X", name="iPhone Test", os_version="26.3", state="Booted"),
        workdir=tmp_path / "wd",
        last_screenshot_w=1206,
        last_screenshot_h=2622,
        last_screenshot_path=pre_path,
        last_marks=[],
    )
    s.workdir.mkdir(parents=True, exist_ok=True)
    ses._SESSIONS[sid] = s

    monkeypatch.setattr(act, "type_text", lambda text, udid=None: None)

    return_mark = som.Mark(id=10, x=900, y=2300, w=80, h=40, text="return", confidence=0.95)
    a_mark = som.Mark(id=11, x=300, y=2400, w=20, h=40, text="A", confidence=0.95)

    def fake_observe(udid, out_dir, **kwargs):
        return Observation(
            screenshot_path=pre_path,
            annotated_path=None,
            screenshot_w=1206,
            screenshot_h=2622,
            window_bounds=None,
            captured_at=0.0,
            marks=[return_mark, a_mark],
        )

    monkeypatch.setattr(obs_mod, "observe", fake_observe)

    result = server.tool_type_text({"session_id": sid, "text": "hi"})
    assert result["ok"] is True
    assert result["chars"] == 2
    assert result["focused_field"] is None


# ----------------- v0.2.0a2 dogfood-feedback round 2 ----------------- #


def _make_grayscale_png(path, size, fill=200, top_band_height=0, top_band_fill=50):
    """Helper: write a grayscale PNG; optional top band differs from the body."""
    from PIL import Image, ImageDraw
    im = Image.new("L", size, fill)
    if top_band_height > 0:
        draw = ImageDraw.Draw(im)
        draw.rectangle([0, 0, size[0], top_band_height], fill=top_band_fill)
    im.save(path)


def test_ssim_mask_excludes_status_bar_region(tmp_path):
    """Two images differing only in a top band: similarity rises with the band masked."""
    from simdrive import recorder as rec_mod
    a = tmp_path / "a.png"
    b = tmp_path / "b.png"
    # Use a 200px band on a 600-tall image (~33%) at strong contrast so the
    # bare-SSIM score lands well below 0.85 — matches the real-world regime
    # where the iOS status-bar area pulls full-screen SSIM into the 0.6s.
    _make_grayscale_png(a, (320, 600), fill=200, top_band_height=200, top_band_fill=10)
    _make_grayscale_png(b, (320, 600), fill=200, top_band_height=200, top_band_fill=240)

    bare_ssim = rec_mod._ssim_or_fallback(a, b)
    masked_ssim = rec_mod._ssim_or_fallback(a, b, masks=[(0, 0, 320, 200)])
    assert bare_ssim < 0.85
    assert masked_ssim >= 0.95

    bare_block = rec_mod._block_similarity(a, b)
    masked_block = rec_mod._block_similarity(a, b, masks=[(0, 0, 320, 200)])
    assert bare_block < 0.85
    assert masked_block >= 0.95


def test_replay_uses_yaml_ssim_masks_when_caller_doesnt_pass(tmp_path, monkeypatch):
    """A recording.yaml with ssim_masks set is used when caller passes no mask_regions."""
    import yaml as _yaml
    from simdrive import recorder as rec_mod, session as ses, act, observe as obs_mod
    from simdrive.sim import Device
    from simdrive.observe import Observation

    monkeypatch.setenv("SIMDRIVE_HOME", str(tmp_path))
    ses._SESSIONS.clear()

    rec_name = "yaml-mask"
    rec_dir = tmp_path / "recordings" / rec_name
    snaps = rec_dir / "snapshots"
    snaps.mkdir(parents=True, exist_ok=True)

    pre = snaps / "001_pre.png"
    post = snaps / "001_post.png"
    _make_grayscale_png(pre, (320, 600), fill=200, top_band_height=60, top_band_fill=10)
    _make_grayscale_png(post, (320, 600), fill=180)

    payload = {
        "name": rec_name,
        "created_at": 0.0,
        "device": "iPhone Test",
        "os_version": "26.3",
        "app_bundle_id": None,
        "ssim_masks": [{"x": 0, "y": 0, "w": 320, "h": 60, "label": "status-bar"}],
        "steps": [
            {
                "id": 1,
                "action": "tap",
                "args": {"x": 100, "y": 100, "screenshot_w": 320, "screenshot_h": 600},
                "pre_screenshot": "snapshots/001_pre.png",
                "post_screenshot": "snapshots/001_post.png",
                "captured_at": 0.0,
            }
        ],
    }
    (rec_dir / "recording.yaml").write_text(_yaml.safe_dump(payload, sort_keys=False))

    s = ses.Session(
        session_id="t",
        device=Device(udid="X", name="iPhone Test", os_version="26.3", state="Booted"),
        workdir=tmp_path / "wd",
    )
    s.workdir.mkdir(parents=True, exist_ok=True)

    live = tmp_path / "live.png"
    _make_grayscale_png(live, (320, 600), fill=200, top_band_height=60, top_band_fill=240)

    def fake_observe(udid, out_dir, **kwargs):
        return Observation(
            screenshot_path=live,
            annotated_path=None,
            screenshot_w=320,
            screenshot_h=600,
            window_bounds=None,
            captured_at=0.0,
            marks=[],
        )

    monkeypatch.setattr(obs_mod, "observe", fake_observe)
    monkeypatch.setattr(act, "tap", lambda x, y, sw, sh, udid=None: (x, y))

    result = rec_mod.replay(rec_name, s, on_drift="halt", drift_threshold=0.9)
    assert result["ok"] is True, f"replay halted unexpectedly: {result}"
    assert result["halt_reason"] is None


def test_mark_stable_id_loose_is_coarser_bucket():
    from simdrive.som import Mark
    # Centers 30px apart, both falling inside the same 60px loose bucket but
    # different 20px tight buckets. Centers: m1=(75,75), m2=(105,105).
    # Tight buckets (cx//20, cy//20): (3,3) vs (5,5) → differ.
    # Loose buckets (cx//60, cy//60): (1,1) vs (1,1) → match.
    m1 = Mark(id=1, x=35, y=65, w=80, h=20, text="Borrow", confidence=1.0)
    m2 = Mark(id=2, x=65, y=95, w=80, h=20, text="Borrow", confidence=1.0)
    assert m1.center == (75, 75)
    assert m2.center == (105, 105)
    assert m1.stable_id != m2.stable_id
    assert m1.stable_id_loose == m2.stable_id_loose


def test_resolve_target_xy_accepts_stable_id_loose(tmp_path):
    from simdrive import server, session as ses, som
    from simdrive.sim import Device
    target = som.Mark(id=5, x=100, y=200, w=40, h=20, text="OK", confidence=0.9)
    s = ses.Session(
        session_id="t",
        device=Device(udid="X", name="iPhone Test", os_version="26.3", state="Booted"),
        workdir=tmp_path,
        last_marks=[target],
    )
    x, y, via, matched = server._resolve_target_xy(s, {"stable_id_loose": target.stable_id_loose})
    assert (x, y) == target.center
    assert "stable_id_loose" in via
    assert matched is target


def test_replay_falls_back_to_stable_id_loose_when_tight_misses(tmp_path, monkeypatch):
    """Recording carries stable_id_loose; live observe lacks tight match but has loose."""
    from simdrive import recorder as rec_mod, session as ses, act, observe as obs_mod, som
    from simdrive.sim import Device
    from simdrive.observe import Observation
    from PIL import Image

    monkeypatch.setenv("SIMDRIVE_HOME", str(tmp_path))
    ses._SESSIONS.clear()

    rec_name = "loose-fallback"
    rec_dir = tmp_path / "recordings" / rec_name
    _write_replay_recording(rec_dir, {
        "x": 999, "y": 999,
        "screenshot_w": 1206, "screenshot_h": 2622,
        "stable_id": "tight-miss",
        "stable_id_loose": "loose-hit",
        "text": "Borrow",
    })

    s = ses.Session(
        session_id="t",
        device=Device(udid="X", name="iPhone Test", os_version="26.3", state="Booted"),
        workdir=tmp_path / "wd",
    )
    s.workdir.mkdir(parents=True, exist_ok=True)

    live_path = tmp_path / "live.png"
    Image.new("RGB", (1206, 2622), (240, 240, 240)).save(live_path)

    fake_mark = som.Mark(id=1, x=80, y=190, w=40, h=20, text="Borrow", confidence=0.95)

    def fake_observe(udid, out_dir, **kwargs):
        return Observation(
            screenshot_path=live_path,
            annotated_path=None,
            screenshot_w=1206,
            screenshot_h=2622,
            window_bounds=None,
            captured_at=0.0,
            marks=[fake_mark],
        )

    monkeypatch.setattr(obs_mod, "observe", fake_observe)
    monkeypatch.setattr(som, "find_by_stable_id", lambda marks, sid: None)
    monkeypatch.setattr(som, "find_by_stable_id_loose",
                        lambda marks, sid: fake_mark if sid == "loose-hit" else None)

    captured = {}

    def fake_tap(px, py, sw, sh, udid=None):
        captured["px"] = px
        captured["py"] = py
        return px, py

    monkeypatch.setattr(act, "tap", fake_tap)

    result = rec_mod.replay(rec_name, s, on_drift="force")
    assert result["ok"] is True
    assert captured == {"px": 100, "py": 200}


def test_tap_response_includes_step_id_when_recording(tmp_path, monkeypatch):
    from simdrive import server, session as ses, recorder as rec_mod, act, observe as obs_mod, som
    from simdrive.sim import Device
    from simdrive.observe import Observation
    from PIL import Image

    monkeypatch.setenv("SIMDRIVE_HOME", str(tmp_path))
    ses._SESSIONS.clear()

    target_mark = som.Mark(id=3, x=100, y=200, w=80, h=20, text="OK", confidence=0.95)
    sid = "rec-step-id"
    pre_path = tmp_path / "pre.png"
    Image.new("RGB", (1206, 2622), (250, 250, 250)).save(pre_path)
    s = ses.Session(
        session_id=sid,
        device=Device(udid="X", name="iPhone Test", os_version="26.3", state="Booted"),
        workdir=tmp_path / "wd",
        last_screenshot_w=1206,
        last_screenshot_h=2622,
        last_screenshot_path=pre_path,
        last_marks=[target_mark],
    )
    s.workdir.mkdir(parents=True, exist_ok=True)
    ses._SESSIONS[sid] = s

    monkeypatch.setattr(act, "tap", lambda x, y, sw, sh, udid=None: (x, y))
    post_path = tmp_path / "post.png"
    Image.new("RGB", (1206, 2622), (200, 200, 200)).save(post_path)

    def fake_observe(udid, out_dir, **kwargs):
        return Observation(
            screenshot_path=post_path,
            annotated_path=None,
            screenshot_w=1206,
            screenshot_h=2622,
            window_bounds=None,
            captured_at=0.0,
            marks=[],
        )

    monkeypatch.setattr(obs_mod, "observe", fake_observe)

    rec_mod.start(s, "step-id-rec")
    res1 = server.tool_tap({"session_id": sid, "stable_id": target_mark.stable_id})
    assert res1["step_id"] == 1
    res2 = server.tool_tap({"session_id": sid, "stable_id": target_mark.stable_id})
    assert res2["step_id"] == 2

    # Without recorder, no step_id key.
    rec_mod.stop(s)
    res3 = server.tool_tap({"session_id": sid, "stable_id": target_mark.stable_id})
    assert "step_id" not in res3


def test_list_devices_emits_hid_supported_and_note(monkeypatch):
    from simdrive import server
    from simdrive import device as dev_mod

    fake_dev = dev_mod.RealDevice(
        udid="UDID-1234",
        name="iPhone QA",
        model="iPhone 17 Pro",
        transport="wired",
        state="available",
    )
    monkeypatch.setattr(dev_mod, "list_devices", lambda: [fake_dev])
    monkeypatch.setattr(dev_mod, "libimobiledevice_available", lambda: (True, []))

    result = server.tool_list_devices({})
    assert result["ok"] is True
    assert "hid_note" in result and "WDA" in result["hid_note"]
    assert len(result["devices"]) == 1
    d = result["devices"][0]
    assert d["hid_supported"] is False
    assert d["udid"] == "UDID-1234"


def test_recording_metadata_includes_version_and_session(tmp_path, monkeypatch):
    """Round-trip a recording; simdrive_version, created_by_session, tags must be present."""
    import yaml
    from simdrive import recorder, session as ses
    from simdrive.sim import Device
    from PIL import Image

    monkeypatch.setenv("SIMDRIVE_HOME", str(tmp_path))
    fake_session = ses.Session(
        session_id="meta-sid",
        device=Device(udid="X", name="iPhone Test", os_version="26.3", state="Booted"),
        workdir=tmp_path / "wd",
        last_screenshot_w=1206,
        last_screenshot_h=2622,
    )
    fake_session.workdir.mkdir(parents=True, exist_ok=True)

    pre = tmp_path / "pre.png"
    post = tmp_path / "post.png"
    Image.new("RGB", (10, 10), (255, 0, 0)).save(pre)
    Image.new("RGB", (10, 10), (0, 255, 0)).save(post)

    rec = recorder.start(fake_session, "meta-recording")
    rec.add_step("tap", {"x": 1, "y": 2, "screenshot_w": 1206, "screenshot_h": 2622}, pre, post)
    yaml_path = recorder.stop(fake_session)

    payload = yaml.safe_load(yaml_path.read_text())
    from simdrive import __version__
    assert payload["simdrive_version"] == __version__
    assert payload["created_by_session"] == "meta-sid"
    assert payload["tags"] == []
    assert payload["screenshot_size_pixels"] == [1206, 2622]


def test_recording_accepts_tags(tmp_path, monkeypatch):
    import yaml
    from simdrive import recorder, session as ses
    from simdrive.sim import Device
    from PIL import Image

    monkeypatch.setenv("SIMDRIVE_HOME", str(tmp_path))
    fake_session = ses.Session(
        session_id="tags-sid",
        device=Device(udid="X", name="iPhone Test", os_version="26.3", state="Booted"),
        workdir=tmp_path / "wd",
    )
    fake_session.workdir.mkdir(parents=True, exist_ok=True)

    pre = tmp_path / "pre.png"
    post = tmp_path / "post.png"
    Image.new("RGB", (10, 10), (255, 0, 0)).save(pre)
    Image.new("RGB", (10, 10), (0, 255, 0)).save(post)

    rec = recorder.start(fake_session, "tagged-recording", tags=["foo", "bar"])
    rec.add_step("tap", {"x": 1, "y": 2, "screenshot_w": 100, "screenshot_h": 100}, pre, post)
    yaml_path = recorder.stop(fake_session)

    payload = yaml.safe_load(yaml_path.read_text())
    assert payload["tags"] == ["foo", "bar"]


def test_replay_response_includes_halt_reason_threshold_steps_planned(tmp_path, monkeypatch):
    """A 3-step recording where step 2 drifts: response must carry halt context."""
    import yaml as _yaml
    from simdrive import recorder as rec_mod, session as ses, act, observe as obs_mod
    from simdrive.sim import Device
    from simdrive.observe import Observation
    from PIL import Image

    monkeypatch.setenv("SIMDRIVE_HOME", str(tmp_path))
    ses._SESSIONS.clear()

    rec_name = "halt-context"
    rec_dir = tmp_path / "recordings" / rec_name
    snaps = rec_dir / "snapshots"
    snaps.mkdir(parents=True, exist_ok=True)

    # Step 1 + 3 pre snapshots match the live screen; step 2's pre snapshot is wildly different.
    same_a = snaps / "001_pre.png"
    same_b = snaps / "001_post.png"
    drift_a = snaps / "002_pre.png"
    drift_b = snaps / "002_post.png"
    same_c = snaps / "003_pre.png"
    same_d = snaps / "003_post.png"
    Image.new("RGB", (320, 600), (200, 200, 200)).save(same_a)
    Image.new("RGB", (320, 600), (200, 200, 200)).save(same_b)
    Image.new("RGB", (320, 600), (10, 10, 10)).save(drift_a)
    Image.new("RGB", (320, 600), (10, 10, 10)).save(drift_b)
    Image.new("RGB", (320, 600), (200, 200, 200)).save(same_c)
    Image.new("RGB", (320, 600), (200, 200, 200)).save(same_d)

    def step(idx):
        return {
            "id": idx,
            "action": "tap",
            "args": {"x": 1, "y": 2, "screenshot_w": 320, "screenshot_h": 600},
            "pre_screenshot": f"snapshots/{idx:03d}_pre.png",
            "post_screenshot": f"snapshots/{idx:03d}_post.png",
            "captured_at": 0.0,
        }

    payload = {
        "name": rec_name,
        "created_at": 0.0,
        "device": "iPhone Test",
        "os_version": "26.3",
        "app_bundle_id": None,
        "steps": [step(1), step(2), step(3)],
    }
    (rec_dir / "recording.yaml").write_text(_yaml.safe_dump(payload, sort_keys=False))

    s = ses.Session(
        session_id="t",
        device=Device(udid="X", name="iPhone Test", os_version="26.3", state="Booted"),
        workdir=tmp_path / "wd",
    )
    s.workdir.mkdir(parents=True, exist_ok=True)

    live = tmp_path / "live.png"
    Image.new("RGB", (320, 600), (200, 200, 200)).save(live)

    def fake_observe(udid, out_dir, **kwargs):
        return Observation(
            screenshot_path=live,
            annotated_path=None,
            screenshot_w=320,
            screenshot_h=600,
            window_bounds=None,
            captured_at=0.0,
            marks=[],
        )

    monkeypatch.setattr(obs_mod, "observe", fake_observe)
    monkeypatch.setattr(act, "tap", lambda x, y, sw, sh, udid=None: (x, y))

    threshold = 0.85
    result = rec_mod.replay(rec_name, s, on_drift="halt", drift_threshold=threshold)
    assert result["ok"] is False
    assert result["halt_reason"] == "drift"
    assert result["threshold"] == threshold
    assert result["steps_planned"] == 3
    assert result["halted_at"] == 2


def _cli_subprocess_env():
    """Make `python -m simdrive.server` resolvable when running from a source checkout."""
    import os
    import simdrive
    pkg_root = Path(simdrive.__file__).resolve().parent.parent  # .../src
    env = os.environ.copy()
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = str(pkg_root) + (os.pathsep + existing if existing else "")
    return env


def test_simdrive_cli_version_flag():
    import subprocess
    import sys
    from simdrive import __version__
    res = subprocess.run(
        [sys.executable, "-m", "simdrive.server", "--version"],
        capture_output=True, text=True, timeout=10.0,
        env=_cli_subprocess_env(),
    )
    assert res.returncode == 0, f"stdout={res.stdout!r} stderr={res.stderr!r}"
    assert res.stdout.startswith("simdrive ")
    assert __version__ in res.stdout


def test_simdrive_cli_help_flag():
    import subprocess
    import sys
    res = subprocess.run(
        [sys.executable, "-m", "simdrive.server", "--help"],
        capture_output=True, text=True, timeout=10.0,
        env=_cli_subprocess_env(),
    )
    assert res.returncode == 0, f"stdout={res.stdout!r} stderr={res.stderr!r}"
    assert "MCP server" in res.stdout
