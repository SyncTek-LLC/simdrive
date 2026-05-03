"""Unit tests for simdrive — no live sim required."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from simdrive import server
from simdrive.window import WindowBounds


def test_version_present():
    assert server.__version__ == "17.0.0a1"


def test_tool_count_is_thirty():
    # 29 pre-existing tools + run_journey (SimDrive 1.0 Cycle 1) = 30
    tools = server.list_tools()
    assert len(tools) == 30, f"expected 30 tools, got {len(tools)}: {[t['name'] for t in tools]}"


def test_tool_names_match_spec():
    expected = {
        "session_start", "session_end", "session_status",
        "observe",
        "tap", "swipe", "type_text", "press_key",
        "record_start", "record_stop", "replay",
        "logs", "list_devices",
        # v0.3.0a1 SpecterQA parity round 1
        "perf", "perf_baseline", "perf_compare", "memory",
        "doctor", "app_state", "apps", "crashes",
        "dismiss_first_launch_alerts", "pre_grant_permissions",
        "set_appearance", "dismiss_sheet", "list_replays", "validate_replay",
        # v0.3.0a3 dogfood fixes
        "version", "clear_field",
        # SimDrive 1.0 Cycle 1
        "run_journey",
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
    assert res.stdout.startswith("specterqa-ios ")
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


# =====================================================================
# v0.3.0a1 — SpecterQA parity (perf / diagnostics / robustness)
# =====================================================================


def _make_session_with_app(tmp_path, sid="s-perf", bundle_id="com.example.App"):
    """Build a Session with a bundle id wired up + register it in the module dict."""
    from simdrive import session as ses
    from simdrive.sim import Device
    ses._SESSIONS.clear()
    s = ses.Session(
        session_id=sid,
        device=Device(udid="UDID-X", name="iPhone Test", os_version="26.3", state="Booted"),
        workdir=tmp_path / "wd",
        app_bundle_id=bundle_id,
    )
    s.workdir.mkdir(parents=True, exist_ok=True)
    ses._SESSIONS[sid] = s
    return s


def _stub_perf_subproc(monkeypatch, *, pid=4242, cpu=12.5, rss_kb=204800, threads=8):
    """Patch perf._run so the perf module sees launchctl + ps output without shelling out."""
    from simdrive import perf as _perf

    launchctl_line = f"{pid}\t0\tUIKitApplication:com.example.App[uuid]"
    ps_line = f"{cpu} {rss_kb}"
    ps_M_lines = "USER PID TT STAT TIME COMMAND\n" + "\n".join(
        f"alice {pid} ?? S 0:00.00 thread{i}" for i in range(threads)
    )

    def fake_run(cmd, timeout=10.0):
        import subprocess as _sp
        out = ""
        if "launchctl" in cmd:
            out = launchctl_line + "\n"
        elif cmd[:1] == ["ps"] and "-M" in cmd:
            out = ps_M_lines + "\n"
        elif cmd[:1] == ["ps"]:
            out = ps_line + "\n"
        return _sp.CompletedProcess(cmd, 0, out, "")

    monkeypatch.setattr(_perf, "_run", fake_run)


def test_perf_returns_cpu_memory_threads(tmp_path, monkeypatch):
    from simdrive import server
    s = _make_session_with_app(tmp_path)
    _stub_perf_subproc(monkeypatch, pid=999, cpu=22.5, rss_kb=153600, threads=12)

    result = server.tool_perf({"session_id": s.session_id})
    assert result["pid"] == 999
    assert result["cpu_pct"] == 22.5
    assert result["memory_rss_mb"] == 150.0  # 153600 / 1024
    assert result["threads"] == 12
    assert "captured_at" in result


def test_perf_baseline_stores_on_session(tmp_path, monkeypatch):
    from simdrive import server
    s = _make_session_with_app(tmp_path)
    _stub_perf_subproc(monkeypatch, pid=111, cpu=10.0, rss_kb=102400, threads=5)

    result = server.tool_perf_baseline({"session_id": s.session_id})
    assert result["label"] == "default"
    assert "default" in s.perf_baselines
    assert s.perf_baselines["default"]["memory_rss_mb"] == 100.0


def test_perf_compare_severity_high_on_memory_jump(tmp_path, monkeypatch):
    from simdrive import server
    s = _make_session_with_app(tmp_path)

    # Baseline snapshot: 100 MB
    _stub_perf_subproc(monkeypatch, pid=1, cpu=10.0, rss_kb=102400, threads=5)
    server.tool_perf_baseline({"session_id": s.session_id})

    # Current snapshot: 165 MB (+65 MB → severity high)
    _stub_perf_subproc(monkeypatch, pid=1, cpu=11.0, rss_kb=168960, threads=6)
    res = server.tool_perf_compare({"session_id": s.session_id})
    assert res["severity"] == "high"
    assert res["delta"]["memory_rss_mb"] >= 60


def test_perf_compare_severity_medium_on_cpu_jump(tmp_path, monkeypatch):
    from simdrive import server
    s = _make_session_with_app(tmp_path)

    _stub_perf_subproc(monkeypatch, pid=1, cpu=5.0, rss_kb=102400, threads=5)
    server.tool_perf_baseline({"session_id": s.session_id})

    # +30% CPU, low memory delta → medium
    _stub_perf_subproc(monkeypatch, pid=1, cpu=35.0, rss_kb=102400, threads=5)
    res = server.tool_perf_compare({"session_id": s.session_id})
    assert res["severity"] == "medium"


def test_memory_returns_unavailable_when_footprint_missing(tmp_path, monkeypatch):
    from simdrive import server, perf as _perf
    s = _make_session_with_app(tmp_path)
    monkeypatch.setattr(_perf.shutil, "which", lambda name: None)

    result = server.tool_memory({"session_id": s.session_id})
    assert result["available"] is False
    assert "footprint" in result["reason"]


def test_doctor_reports_checks(monkeypatch):
    from simdrive import server, diagnostics, hid_inject
    import subprocess as _sp

    def fake_run(cmd, timeout=10.0):
        if cmd[:1] == ["xcode-select"]:
            return _sp.CompletedProcess(cmd, 0, "/Applications/Xcode.app/Contents/Developer", "")
        if "runtimes" in cmd:
            return _sp.CompletedProcess(cmd, 0, '{"runtimes": [{"name":"iOS 26.3"}]}', "")
        if "booted" in cmd:
            return _sp.CompletedProcess(
                cmd, 0,
                '{"devices": {"iOS-26-3": [{"udid":"X","state":"Booted"}]}}', "",
            )
        return _sp.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(diagnostics, "_run", fake_run)
    monkeypatch.setattr(hid_inject, "available", lambda: True)
    monkeypatch.setattr(hid_inject, "_binary_path", lambda: Path("/fake/simdrive-input"))

    result = server.tool_doctor({})
    assert result["ok"] is True
    names = {c["name"] for c in result["checks"]}
    assert {"xcode_select", "simctl_runtimes", "simctl_booted_devices", "hid_helper"} <= names


def test_doctor_marks_failed_check(monkeypatch):
    from simdrive import server, diagnostics, hid_inject
    import subprocess as _sp

    def fake_run(cmd, timeout=10.0):
        if cmd[:1] == ["xcode-select"]:
            return _sp.CompletedProcess(cmd, 0, "/Applications/Xcode.app/Contents/Developer", "")
        if "runtimes" in cmd:
            # No runtimes installed → check fails.
            return _sp.CompletedProcess(cmd, 0, '{"runtimes": []}', "")
        if "booted" in cmd:
            return _sp.CompletedProcess(
                cmd, 0,
                '{"devices": {"iOS-26-3": [{"udid":"X","state":"Booted"}]}}', "",
            )
        return _sp.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(diagnostics, "_run", fake_run)
    monkeypatch.setattr(hid_inject, "available", lambda: True)
    monkeypatch.setattr(hid_inject, "_binary_path", lambda: Path("/fake/simdrive-input"))

    result = server.tool_doctor({})
    assert result["ok"] is False
    runtimes_check = next(c for c in result["checks"] if c["name"] == "simctl_runtimes")
    assert runtimes_check["ok"] is False


def test_apps_parses_listapps_plist(tmp_path, monkeypatch):
    from simdrive import server, diagnostics
    import subprocess as _sp
    import plistlib

    s = _make_session_with_app(tmp_path)
    plist_payload = plistlib.dumps({
        "com.example.App": {
            "CFBundleDisplayName": "Example",
            "CFBundleShortVersionString": "1.2.3",
            "Path": "/Applications/Example.app",
        },
        "com.example.Other": {
            "CFBundleName": "Other",
            "CFBundleVersion": "9.9",
            "Path": "/Applications/Other.app",
        },
    })

    def fake_run(cmd, timeout=15.0):
        return _sp.CompletedProcess(cmd, 0, plist_payload.decode("utf-8"), "")

    monkeypatch.setattr(diagnostics, "_run", fake_run)

    result = server.tool_apps({"session_id": s.session_id})
    bundles = {a["bundle_id"] for a in result["apps"]}
    assert bundles == {"com.example.App", "com.example.Other"}
    example = next(a for a in result["apps"] if a["bundle_id"] == "com.example.App")
    assert example["name"] == "Example"
    assert example["version"] == "1.2.3"


def test_app_state_running(tmp_path, monkeypatch):
    from simdrive import server, diagnostics
    import subprocess as _sp
    s = _make_session_with_app(tmp_path)

    def fake_run(cmd, timeout=10.0):
        return _sp.CompletedProcess(
            cmd, 0,
            "1234\t0\tUIKitApplication:com.example.App[uuid]\n",
            "",
        )

    monkeypatch.setattr(diagnostics, "_run", fake_run)
    result = server.tool_app_state({"session_id": s.session_id})
    assert result["state"] == "foreground"
    assert result["pid"] == 1234


def test_app_state_not_running(tmp_path, monkeypatch):
    from simdrive import server, diagnostics
    import subprocess as _sp
    s = _make_session_with_app(tmp_path)

    monkeypatch.setattr(
        diagnostics, "_run",
        lambda cmd, timeout=10.0: _sp.CompletedProcess(cmd, 0, "999\t0\tcom.other\n", ""),
    )
    result = server.tool_app_state({"session_id": s.session_id})
    assert result["state"] == "not-running"
    assert result["pid"] is None


def test_crashes_filters_by_session_start_time(tmp_path, monkeypatch):
    """Three .ips files with different mtimes; only the post-session-start one surfaces."""
    import os
    import json as _json
    from simdrive import server, diagnostics

    reports = tmp_path / "reports"
    reports.mkdir()
    payload = _json.dumps({"timestamp": "2026-04-29 00:00:00 -0400",
                           "bundleID": "com.example.App"}) + "\n{}"

    p_old1 = reports / "old1.ips"
    p_old2 = reports / "old2.ips"
    p_new = reports / "new.ips"
    for p in (p_old1, p_old2, p_new):
        p.write_text(payload)

    os.utime(p_old1, (100, 100))
    os.utime(p_old2, (200, 200))
    os.utime(p_new, (10_000_000_000, 10_000_000_000))

    monkeypatch.setattr(diagnostics, "_DIAGNOSTIC_REPORTS_DIR", reports)

    s = _make_session_with_app(tmp_path)
    s.started_at = 1_000_000_000  # cutoff between old and new

    result = server.tool_crashes({"session_id": s.session_id})
    names = [c["name"] for c in result["crashes"]]
    assert names == ["new.ips"]


def test_crashes_filters_by_bundle_id(tmp_path, monkeypatch):
    import os
    import json as _json
    from simdrive import server, diagnostics

    reports = tmp_path / "reports"
    reports.mkdir()

    def write_ips(name, bundle):
        p = reports / name
        body = _json.dumps({"timestamp": "x", "bundleID": bundle}) + "\n{}"
        p.write_text(body)
        os.utime(p, (10_000_000_000, 10_000_000_000))
        return p

    write_ips("a.ips", "com.example.App")
    write_ips("b.ips", "com.other.App")
    write_ips("c.ips", "com.example.App")

    monkeypatch.setattr(diagnostics, "_DIAGNOSTIC_REPORTS_DIR", reports)
    s = _make_session_with_app(tmp_path)
    s.started_at = 0

    result = server.tool_crashes({
        "session_id": s.session_id,
        "app_bundle_id": "com.example.App",
        "since_session_start": False,
    })
    names = sorted(c["name"] for c in result["crashes"])
    assert names == ["a.ips", "c.ips"]


def test_dismiss_first_launch_alerts_loops_until_alert_gone(tmp_path, monkeypatch):
    """First observe sees an Allow button, second observe is clean → one tap, attempts=1."""
    from simdrive import server, observe as obs_mod, act, som
    from simdrive.observe import Observation
    from PIL import Image

    s = _make_session_with_app(tmp_path, sid="alert-once")

    fake_screenshot = tmp_path / "fake.png"
    Image.new("RGB", (100, 200), (10, 10, 10)).save(fake_screenshot)
    allow_mark = som.Mark(id=1, x=200, y=400, w=80, h=40, text="Allow", confidence=0.95)

    call_count = {"n": 0}

    def fake_observe(udid, out_dir, **kwargs):
        call_count["n"] += 1
        marks = [allow_mark] if call_count["n"] == 1 else []
        return Observation(
            screenshot_path=fake_screenshot,
            annotated_path=None,
            screenshot_w=100,
            screenshot_h=200,
            window_bounds=None,
            captured_at=0.0,
            marks=marks,
        )

    tap_calls: list[tuple] = []
    monkeypatch.setattr(obs_mod, "observe", fake_observe)
    monkeypatch.setattr(act, "tap",
                        lambda x, y, sw, sh, udid=None: tap_calls.append((x, y)) or (x, y))

    result = server.tool_dismiss_first_launch_alerts({"session_id": s.session_id})
    assert result["dismissed"] == 1
    assert result["attempts"] == 1
    assert len(tap_calls) == 1


def test_dismiss_first_launch_alerts_retries_on_alert_persisting(tmp_path, monkeypatch):
    """Alert visible twice in a row, then gone → retries=1 should fire two taps."""
    from simdrive import server, observe as obs_mod, act, som
    from simdrive.observe import Observation
    from PIL import Image

    s = _make_session_with_app(tmp_path, sid="alert-retry")

    fake_screenshot = tmp_path / "fake.png"
    Image.new("RGB", (100, 200), (10, 10, 10)).save(fake_screenshot)
    allow_mark = som.Mark(id=1, x=200, y=400, w=80, h=40, text="Allow", confidence=0.95)

    call_count = {"n": 0}

    def fake_observe(udid, out_dir, **kwargs):
        call_count["n"] += 1
        marks = [allow_mark] if call_count["n"] <= 2 else []
        return Observation(
            screenshot_path=fake_screenshot,
            annotated_path=None,
            screenshot_w=100,
            screenshot_h=200,
            window_bounds=None,
            captured_at=0.0,
            marks=marks,
        )

    tap_calls: list[tuple] = []
    monkeypatch.setattr(obs_mod, "observe", fake_observe)
    monkeypatch.setattr(act, "tap",
                        lambda x, y, sw, sh, udid=None: tap_calls.append((x, y)) or (x, y))

    result = server.tool_dismiss_first_launch_alerts({
        "session_id": s.session_id,
        "retries": 1,
    })
    assert result["attempts"] == 2
    assert result["dismissed"] == 2
    assert len(tap_calls) == 2


def test_pre_grant_permissions_invokes_simctl_per_perm(tmp_path, monkeypatch):
    from simdrive import server, robustness
    import subprocess as _sp

    s = _make_session_with_app(tmp_path)
    calls: list[list[str]] = []

    def fake_run(cmd, *args, **kwargs):
        calls.append(list(cmd))
        return _sp.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(robustness.subprocess, "run", fake_run)

    result = server.tool_pre_grant_permissions({
        "session_id": s.session_id,
        "permissions": ["camera", "photos"],
    })
    assert result["ok"] is True
    assert result["granted"] == ["camera", "photos"]
    assert len(calls) == 2
    assert calls[0][:5] == ["xcrun", "simctl", "privacy", "UDID-X", "grant"]
    assert calls[0][5] == "camera"
    assert calls[1][5] == "photos"


def test_set_appearance_invokes_simctl_ui(tmp_path, monkeypatch):
    from simdrive import server, robustness
    import subprocess as _sp

    s = _make_session_with_app(tmp_path)
    captured: dict = {}

    def fake_run(cmd, *args, **kwargs):
        captured["cmd"] = list(cmd)
        return _sp.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(robustness.subprocess, "run", fake_run)

    result = server.tool_set_appearance({"session_id": s.session_id, "appearance": "dark"})
    assert result["ok"] is True
    assert result["appearance"] == "dark"
    assert captured["cmd"] == ["xcrun", "simctl", "ui", "UDID-X", "appearance", "dark"]


def test_dismiss_sheet_calls_swipe_with_screenshot_dims(tmp_path, monkeypatch):
    from simdrive import server, act, observe as obs_mod, session as ses
    from simdrive.observe import Observation
    from simdrive.sim import Device
    from PIL import Image

    ses._SESSIONS.clear()
    sid = "sheet"
    pre_path = tmp_path / "pre.png"
    Image.new("RGB", (1000, 2000), (200, 200, 200)).save(pre_path)
    s = ses.Session(
        session_id=sid,
        device=Device(udid="UDID-X", name="iPhone Test", os_version="26.3", state="Booted"),
        workdir=tmp_path / "wd",
        last_screenshot_w=1000,
        last_screenshot_h=2000,
        last_screenshot_path=pre_path,
    )
    s.workdir.mkdir(parents=True, exist_ok=True)
    ses._SESSIONS[sid] = s

    captured = {}

    def fake_swipe(x1, y1, x2, y2, sw, sh, duration_ms, udid=None):
        captured.update({
            "x1": x1, "y1": y1, "x2": x2, "y2": y2,
            "sw": sw, "sh": sh, "duration_ms": duration_ms,
        })

    monkeypatch.setattr(act, "swipe", fake_swipe)

    server.tool_dismiss_sheet({"session_id": sid})
    assert captured["sw"] == 1000
    assert captured["sh"] == 2000
    assert captured["x1"] == 500
    assert captured["x2"] == 500
    assert captured["y1"] == int(2000 * 0.2)
    assert captured["y2"] == int(2000 * 0.7)


def test_list_replays_returns_metadata(tmp_path, monkeypatch):
    import yaml as _yaml
    from simdrive import server, recorder

    monkeypatch.setenv("SIMDRIVE_HOME", str(tmp_path))
    root = recorder.recordings_root()
    root.mkdir(parents=True, exist_ok=True)

    for name in ("alpha", "beta"):
        rec_dir = root / name
        rec_dir.mkdir()
        (rec_dir / "recording.yaml").write_text(_yaml.safe_dump({
            "name": name,
            "created_at": 1.0,
            "simdrive_version": "0.3.0a1",
            "tags": ["smoke"],
            "steps": [{"id": 1, "action": "tap", "args": {}, "pre_screenshot": "x"}],
        }))

    result = server.tool_list_replays({})
    names = sorted(r["name"] for r in result["replays"])
    assert names == ["alpha", "beta"]
    for r in result["replays"]:
        assert r["steps"] == 1
        assert r["simdrive_version"] == "0.3.0a1"
        assert r["tags"] == ["smoke"]


def test_validate_replay_passes_on_good_yaml(tmp_path, monkeypatch):
    import yaml as _yaml
    from simdrive import server, recorder
    from PIL import Image

    monkeypatch.setenv("SIMDRIVE_HOME", str(tmp_path))
    root = recorder.recordings_root()
    rec_dir = root / "good"
    rec_dir.mkdir(parents=True, exist_ok=True)
    snaps = rec_dir / "snapshots"
    snaps.mkdir()
    Image.new("RGB", (10, 10), (255, 0, 0)).save(snaps / "001_pre.png")
    Image.new("RGB", (10, 10), (0, 255, 0)).save(snaps / "001_post.png")

    (rec_dir / "recording.yaml").write_text(_yaml.safe_dump({
        "name": "good",
        "created_at": 1.0,
        "simdrive_version": "0.3.0a1",
        "steps": [{
            "id": 1, "action": "tap", "args": {"x": 1, "y": 1},
            "pre_screenshot": "snapshots/001_pre.png",
            "post_screenshot": "snapshots/001_post.png",
        }],
    }))

    result = server.tool_validate_replay({"name": "good"})
    assert result["ok"] is True
    assert result["errors"] == []
    assert result["step_count"] == 1


def test_validate_replay_flags_missing_screenshot(tmp_path, monkeypatch):
    import yaml as _yaml
    from simdrive import server, recorder

    monkeypatch.setenv("SIMDRIVE_HOME", str(tmp_path))
    root = recorder.recordings_root()
    rec_dir = root / "missing-snap"
    rec_dir.mkdir(parents=True, exist_ok=True)
    (rec_dir / "recording.yaml").write_text(_yaml.safe_dump({
        "name": "missing-snap",
        "created_at": 1.0,
        "steps": [{
            "id": 1, "action": "tap", "args": {},
            "pre_screenshot": "snapshots/missing.png",
        }],
    }))

    result = server.tool_validate_replay({"name": "missing-snap"})
    assert result["ok"] is False
    assert any("missing.png" in e for e in result["errors"])


def test_validate_replay_flags_unsupported_action(tmp_path, monkeypatch):
    import yaml as _yaml
    from simdrive import server, recorder
    from PIL import Image

    monkeypatch.setenv("SIMDRIVE_HOME", str(tmp_path))
    root = recorder.recordings_root()
    rec_dir = root / "bad-action"
    rec_dir.mkdir(parents=True, exist_ok=True)
    snaps = rec_dir / "snapshots"
    snaps.mkdir()
    Image.new("RGB", (10, 10), (255, 0, 0)).save(snaps / "001_pre.png")

    (rec_dir / "recording.yaml").write_text(_yaml.safe_dump({
        "name": "bad-action",
        "created_at": 1.0,
        "steps": [{
            "id": 1, "action": "dance", "args": {},
            "pre_screenshot": "snapshots/001_pre.png",
        }],
    }))

    result = server.tool_validate_replay({"name": "bad-action"})
    assert result["ok"] is False
    assert any("dance" in e for e in result["errors"])


# v0.3.0a2 — closing the two partials

def test_list_devices_emits_last_seen_and_unavailable_reason(tmp_path, monkeypatch):
    """devicectl JSON has lastConnectionDate + pairingState/tunnelState we can
    surface as last_seen and a composed unavailable_reason."""
    from simdrive import server, device

    fake_devs = [
        device.RealDevice(
            udid="UDID-1", name="Maurice's iPad",
            model="iPad Pro", transport="localNetwork", state="available",
            last_seen="2026-04-29T18:07:00.000Z", unavailable_reason=None,
        ),
        device.RealDevice(
            udid="UDID-2", name="Old Tester",
            model="iPhone 13 Pro", transport=None, state="unavailable",
            last_seen=None, unavailable_reason="not paired; tunnel disconnected",
        ),
    ]
    monkeypatch.setattr(device, "list_devices", lambda: fake_devs)
    monkeypatch.setattr(device, "libimobiledevice_available", lambda: (True, []))

    result = server.tool_list_devices({})
    assert result["ok"] is True
    by_udid = {d["udid"]: d for d in result["devices"]}
    assert by_udid["UDID-1"]["last_seen"] == "2026-04-29T18:07:00.000Z"
    assert by_udid["UDID-1"]["unavailable_reason"] is None
    assert by_udid["UDID-2"]["last_seen"] is None
    assert "not paired" in by_udid["UDID-2"]["unavailable_reason"]


def test_unavailable_reason_compose():
    from simdrive.device import _unavailable_reason
    assert _unavailable_reason("available", {}, {}) is None
    assert _unavailable_reason(
        "unavailable",
        {"pairingState": "unpaired", "tunnelState": "disconnected", "transportType": "wired"},
        {},
    ) == "not paired; tunnel disconnected"
    assert _unavailable_reason(
        "unavailable",
        {"pairingState": "paired", "tunnelState": "disconnected"},
        {"developerModeStatus": "disabled"},
    ) == "tunnel disconnected; no transport; developer mode disabled"
    assert _unavailable_reason("unavailable", {"transportType": "wired", "pairingState": "paired", "tunnelState": "connected"}, {}) == "device offline"


def test_get_app_version_parses_listapps(monkeypatch):
    """sim.get_app_version reads CFBundleShortVersionString from listapps output."""
    import plistlib, subprocess as _sp
    from simdrive import sim

    plist_payload = plistlib.dumps({
        "com.example.App": {
            "CFBundleShortVersionString": "3.0.0",
            "CFBundleVersion": "470",
        },
    })

    def fake_simctl(*args, timeout=30.0, capture=True):
        return _sp.CompletedProcess(("xcrun", "simctl") + args, 0, plist_payload.decode("utf-8"), "")

    monkeypatch.setattr(sim, "_simctl", fake_simctl)
    assert sim.get_app_version("UDID", "com.example.App") == "3.0.0"
    assert sim.get_app_version("UDID", "com.unknown") is None


def test_get_app_version_falls_back_to_cfbundleversion(monkeypatch):
    import plistlib, subprocess as _sp
    from simdrive import sim

    plist_payload = plistlib.dumps({
        "com.example.App": {"CFBundleVersion": "9.9"},
    })

    def fake_simctl(*args, timeout=30.0, capture=True):
        return _sp.CompletedProcess(("xcrun", "simctl") + args, 0, plist_payload.decode("utf-8"), "")

    monkeypatch.setattr(sim, "_simctl", fake_simctl)
    assert sim.get_app_version("UDID", "com.example.App") == "9.9"


def test_recording_metadata_includes_app_version(tmp_path, monkeypatch):
    """recorder.finalize() stamps the recording with the app's version when available."""
    import yaml as _yaml
    from simdrive import recorder, session as ses, sim
    from simdrive.sim import Device
    from PIL import Image

    monkeypatch.setenv("SIMDRIVE_HOME", str(tmp_path))
    monkeypatch.setattr(sim, "get_app_version", lambda udid, bundle: "3.0.0 (470)")

    s = ses.Session(
        session_id="appver-test",
        device=Device(udid="X", name="iPhone Test", os_version="26.3", state="Booted"),
        workdir=tmp_path / "wd",
        app_bundle_id="org.thepalaceproject.palace",
    )
    s.workdir.mkdir(parents=True, exist_ok=True)
    s.last_screenshot_w = 1206
    s.last_screenshot_h = 2622

    pre = tmp_path / "pre.png"
    post = tmp_path / "post.png"
    Image.new("RGB", (10, 10), (255, 0, 0)).save(pre)
    Image.new("RGB", (10, 10), (0, 255, 0)).save(post)

    rec = recorder.start(s, "appver-flow")
    rec.add_step("tap", {"x": 1, "y": 2}, pre, post)
    yaml_path = recorder.stop(s)

    payload = _yaml.safe_load(yaml_path.read_text())
    assert payload["app_version"] == "3.0.0 (470)"


def test_recording_app_version_none_when_unavailable(tmp_path, monkeypatch):
    """When sim.get_app_version raises or returns None, recording falls through cleanly."""
    import yaml as _yaml
    from simdrive import recorder, session as ses, sim
    from simdrive.sim import Device
    from PIL import Image

    monkeypatch.setenv("SIMDRIVE_HOME", str(tmp_path))
    def boom(*a, **kw): raise RuntimeError("simctl missing")
    monkeypatch.setattr(sim, "get_app_version", boom)

    s = ses.Session(
        session_id="noapp-test",
        device=Device(udid="X", name="iPhone Test", os_version="26.3", state="Booted"),
        workdir=tmp_path / "wd",
        app_bundle_id="com.example.App",
    )
    s.workdir.mkdir(parents=True, exist_ok=True)

    pre = tmp_path / "pre.png"
    post = tmp_path / "post.png"
    Image.new("RGB", (10, 10), (255, 0, 0)).save(pre)
    Image.new("RGB", (10, 10), (0, 255, 0)).save(post)

    rec = recorder.start(s, "noapp-flow")
    rec.add_step("tap", {"x": 1, "y": 2}, pre, post)
    yaml_path = recorder.stop(s)

    payload = _yaml.safe_load(yaml_path.read_text())
    assert payload["app_version"] is None


# ───────────────── v0.3.0a3 dogfood-feedback round ────────────────── #


def _make_session(tmp_path, sid: str, marks=None):
    """Helper for the v0.3.0a3 tests — minimal Session with a fake screenshot."""
    from simdrive import session as ses
    from simdrive.sim import Device
    from PIL import Image

    ses._SESSIONS.clear()
    pre_path = tmp_path / f"pre-{sid}.png"
    Image.new("RGB", (1206, 2622), (240, 240, 240)).save(pre_path)
    s = ses.Session(
        session_id=sid,
        device=Device(udid="X", name="iPhone Test", os_version="26.3", state="Booted"),
        workdir=tmp_path / f"wd-{sid}",
        last_screenshot_w=1206,
        last_screenshot_h=2622,
        last_screenshot_path=pre_path,
        last_marks=list(marks or []),
    )
    s.workdir.mkdir(parents=True, exist_ok=True)
    ses._SESSIONS[sid] = s
    return s, pre_path


def test_type_text_response_includes_injection_method_and_dispatch_succeeded(tmp_path, monkeypatch):
    """v0.3.0a3 — type_text response surfaces injection_method + dispatch_succeeded."""
    from simdrive import server, act, observe as obs_mod, som
    from simdrive.observe import Observation

    target_mark = som.Mark(id=1, x=100, y=200, w=120, h=40, text="Username", confidence=0.95)
    target_sid = target_mark.stable_id
    s, pre_path = _make_session(tmp_path, "tt-injmethod", marks=[target_mark])

    monkeypatch.setattr(act, "tap", lambda x, y, sw, sh, udid=None: (x, y))
    monkeypatch.setattr(act, "type_text", lambda text, udid=None: None)
    monkeypatch.setattr(act, "_backend", lambda: "hid")

    def fake_observe(udid, out_dir, **kwargs):
        return Observation(
            screenshot_path=pre_path, annotated_path=None,
            screenshot_w=1206, screenshot_h=2622,
            window_bounds=None, captured_at=0.0, marks=[],
        )
    monkeypatch.setattr(obs_mod, "observe", fake_observe)

    result = server.tool_type_text({
        "session_id": "tt-injmethod",
        "text": "hello",
        "tap_first": {"stable_id": target_sid},
    })
    assert result["injection_method"] == "hid"
    assert result["dispatch_succeeded"] is True
    # Legacy fields still present for the cliclick-path debug.
    assert "keyboard_visible" in result
    assert "focused_field" in result


def test_mark_confidence_band_high_for_real_english():
    """A clean OCR of real English text at high confidence stays high-band."""
    from simdrive.som import Mark
    m = Mark(id=1, x=0, y=0, w=10, h=10, text="The Dance Partner", confidence=0.95)
    assert m.confidence_band == "high"
    assert m.confidence == 0.95
    assert m.raw_confidence == 0.95


def test_mark_confidence_band_low_for_misread_gibberish():
    """Stylized cover-art OCR misread at high engine confidence gets clamped."""
    from simdrive.som import Mark
    m = Mark(id=1, x=0, y=0, w=10, h=10, text="Sary of the Canadan liothest", confidence=1.0)
    assert m.confidence_band == "low"
    assert m.confidence <= 0.3
    assert m.raw_confidence == 1.0


def test_mark_confidence_band_handles_short_tokens():
    """Short tokens always pass dictionary check — 'OK' should be high."""
    from simdrive.som import Mark
    m = Mark(id=1, x=0, y=0, w=10, h=10, text="OK", confidence=0.95)
    assert m.confidence_band == "high"


def test_version_tool_returns_loaded_and_disk():
    """tool_version returns the four expected fields."""
    from simdrive import server
    result = server.tool_version({})
    assert result["version"] == server.__version__
    assert "loaded_at" in result
    assert "disk_version" in result
    assert "drift" in result


def test_call_tool_emits_warning_on_version_drift(monkeypatch):
    """When _check_version_drift returns a string, dispatcher injects it."""
    from simdrive import server
    server.session._SESSIONS.clear() if hasattr(server, "session") else None
    monkeypatch.setattr(server, "_check_version_drift", lambda: "drift detected: 0.3.0a2 vs 0.3.0a3")
    result = server.call_tool("version", {})
    assert result["_simdrive_warning"] == "drift detected: 0.3.0a2 vs 0.3.0a3"


def test_call_tool_no_warning_when_versions_match(monkeypatch):
    """Default path: loaded == disk, no warning surfaces."""
    from simdrive import server
    monkeypatch.setattr(server, "_check_version_drift", lambda: None)
    result = server.call_tool("version", {})
    assert "_simdrive_warning" not in result


def test_clear_field_tool_sends_cmd_a_and_delete(tmp_path, monkeypatch):
    """clear_field tool dispatches Cmd-A then delete via HID."""
    from simdrive import server, act, hid_inject

    s, _ = _make_session(tmp_path, "clearfield-1")
    chord_calls: list[tuple] = []
    press_calls: list[tuple] = []
    monkeypatch.setattr(hid_inject, "chord", lambda udid, m, k: chord_calls.append((udid, m, k)))
    monkeypatch.setattr(act, "press_key", lambda key, udid=None: press_calls.append((key, udid)))

    result = server.tool_clear_field({"session_id": "clearfield-1"})
    assert result["ok"] is True
    assert result["cleared"] is True
    assert chord_calls == [("X", "cmd", "a")]
    assert press_calls == [("delete", "X")]


def test_type_text_clear_first_sends_cmd_a_delete_before_typing(tmp_path, monkeypatch):
    """clear_first=True orders chord(cmd,a) → press_key(delete) → type_text(text)."""
    from simdrive import server, act, hid_inject, observe as obs_mod, som
    from simdrive.observe import Observation

    target_mark = som.Mark(id=1, x=100, y=200, w=120, h=40, text="Search", confidence=0.95)
    target_sid = target_mark.stable_id
    s, pre_path = _make_session(tmp_path, "tt-clearfirst", marks=[target_mark])

    call_order: list[str] = []
    monkeypatch.setattr(act, "tap", lambda x, y, sw, sh, udid=None: (x, y))
    monkeypatch.setattr(hid_inject, "chord",
                        lambda udid, m, k: call_order.append(f"chord:{m},{k}"))
    monkeypatch.setattr(act, "press_key",
                        lambda key, udid=None: call_order.append(f"press_key:{key}"))
    monkeypatch.setattr(act, "type_text",
                        lambda text, udid=None: call_order.append(f"type_text:{text}"))
    monkeypatch.setattr(act, "_backend", lambda: "hid")

    def fake_observe(udid, out_dir, **kwargs):
        return Observation(
            screenshot_path=pre_path, annotated_path=None,
            screenshot_w=1206, screenshot_h=2622,
            window_bounds=None, captured_at=0.0, marks=[],
        )
    monkeypatch.setattr(obs_mod, "observe", fake_observe)

    server.tool_type_text({
        "session_id": "tt-clearfirst",
        "text": "new",
        "tap_first": {"stable_id": target_sid},
        "clear_first": True,
    })
    # chord and delete must fire BEFORE type_text("new").
    chord_idx = call_order.index("chord:cmd,a")
    delete_idx = call_order.index("press_key:delete")
    type_idx = call_order.index("type_text:new")
    assert chord_idx < delete_idx < type_idx


def test_find_by_text_resolves_search_via_icon_alias():
    """find_by_text(marks, 'search') resolves to a mark whose text is 'Q/'."""
    from simdrive.som import Mark, find_by_text
    glyph_mark = Mark(id=1, x=10, y=10, w=20, h=20, text="Q/", confidence=0.6)
    other = Mark(id=2, x=100, y=100, w=50, h=20, text="Library", confidence=0.95)
    found = find_by_text([glyph_mark, other], "search")
    assert found is glyph_mark


def test_find_by_text_back_alias():
    """'<' OCR resolves to find_by_text query 'back'."""
    from simdrive.som import Mark, find_by_text
    chevron = Mark(id=1, x=10, y=10, w=20, h=20, text="<", confidence=0.7)
    found = find_by_text([chevron], "back")
    assert found is chevron


def test_find_by_text_returns_none_when_neither_exact_nor_alias_matches():
    """No exact/prefix/substring AND no alias hit → None."""
    from simdrive.som import Mark, find_by_text
    other = Mark(id=1, x=10, y=10, w=20, h=20, text="Foo", confidence=0.7)
    assert find_by_text([other], "search") is None
