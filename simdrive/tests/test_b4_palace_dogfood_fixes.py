"""Regression tests pinning the three b3 Palace-dogfood bug fixes.

Palace iOS 3.1.0 / iPhone 16 Pro sim / iOS 26 surfaced these in
2026-05-22 dogfood. Each test asserts the *fixed* behavior so a future
regression fires here before reaching dogfood.

F-B3-009  `clear_field` must emit a recording step
F-B3-010  `tap_and_wait_keyboard` must serialize as `tap_and_wait_keyboard`,
          not the underlying primitive `tap`
F-B3-011  `type_text` must surface a `keyboard_visible_reason` whenever
          dispatch_succeeded is true but keyboard_visible is false, so
          agents do not retry into a successfully-committed field
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest


_FAKE_UDID = "31471BBD-0000-B4FIX-PALACE-DOGFOODB3"


def _make_sim_session(tmp_path: Path, sid: str = "b4-fix"):
    from simdrive import session as session_mod
    from simdrive.sim import Device

    d = Device(udid=_FAKE_UDID, name="Test Sim", os_version="26.0", state="active")
    workdir = tmp_path / "sessions" / sid
    workdir.mkdir(parents=True, exist_ok=True)
    s = session_mod.Session(
        session_id=sid,
        device=d,
        workdir=workdir,
        target="simulator",
        last_screenshot_w=1206,
        last_screenshot_h=2622,
    )
    session_mod._SESSIONS[sid] = s
    return s


def _attach_fake_recorder(s):
    """Attach a minimal Recorder-shaped stub so handlers can record steps.

    Handlers call _record_act_step which calls s.recorder.add_step(...). The
    fake records the action + args and returns the step id. upgrade_step_action
    patches the just-recorded entry in place.
    """
    rec = MagicMock()
    rec.steps = []  # list of step dicts
    rec.name = "b4-fix-recording"
    rec.session = s

    def _add_step(action, args, pre_screenshot, post_screenshot=None, marks_count=None, **kw):
        idx = len(rec.steps) + 1
        rec.steps.append({"id": idx, "action": action, "args": args})
        return idx

    def _upgrade(step_id, new_action):
        for step in rec.steps:
            if step.get("id") == step_id:
                step["action"] = new_action
                return True
        return False

    rec.add_step.side_effect = _add_step
    rec.upgrade_step_action.side_effect = _upgrade
    s.recorder = rec
    # tool_tap reads s.last_screenshot_path to know recording is "active"
    s.last_screenshot_path = s.workdir / "pre.png"
    s.last_screenshot_path.write_bytes(b"\x89PNG\r\n\x1a\n")
    return rec


def _stub_observation(tmp_path):
    """Build a minimal Observation that the recorder's post-capture path
    will accept without hitting a live simulator."""
    from simdrive import observe as observe_mod
    return observe_mod.Observation(
        screenshot_path=tmp_path / "post.png",
        annotated_path=None,
        screenshot_w=1206, screenshot_h=2622,
        window_bounds=None,
        captured_at=0.0,
        marks=[],
        recent_logs=None,
    )


@pytest.fixture(autouse=True)
def _cleanup_sessions():
    from simdrive import session as session_mod
    before = set(session_mod._SESSIONS.keys())
    yield
    for k in list(session_mod._SESSIONS.keys()):
        if k not in before:
            session_mod._SESSIONS.pop(k, None)


# ── F-B3-009: clear_field must record a step ───────────────────────────────


def test_clear_field_records_a_step(tmp_path):
    """clear_field on sim path with an active recorder must emit a step."""
    s = _make_sim_session(tmp_path)
    rec = _attach_fake_recorder(s)
    from simdrive import server, observe as observe_mod
    with patch("simdrive.hid_inject.chord", return_value=None), \
         patch.object(server.act, "press_key", return_value=None), \
         patch.object(observe_mod, "observe", return_value=_stub_observation(tmp_path)):
        result = server.tool_clear_field({"session_id": s.session_id})
    assert result["ok"] is True
    assert result["cleared"] is True
    # Pre-fix this list was empty (b3 Palace bug). Post-fix it has the step.
    assert len(rec.steps) == 1
    assert rec.steps[0]["action"] == "clear_field"


def test_clear_field_failure_does_not_pollute_recording(tmp_path):
    """When HID dispatch fails, the recording should NOT contain a fake
    clear_field step — the step would lie about what happened."""
    s = _make_sim_session(tmp_path)
    rec = _attach_fake_recorder(s)
    from simdrive import server, observe as observe_mod
    with patch("simdrive.hid_inject.chord", side_effect=RuntimeError("hid dead")), \
         patch.object(server.act, "press_key"), \
         patch.object(observe_mod, "observe", return_value=_stub_observation(tmp_path)):
        result = server.tool_clear_field({"session_id": s.session_id})
    assert result["ok"] is False
    assert result["cleared"] is False
    # No step recorded for a failed clear.
    assert rec.steps == []


# ── F-B3-010: tap_and_wait_keyboard recording semantic ─────────────────────


def test_tap_and_wait_keyboard_records_with_composite_action_name(tmp_path):
    """The just-recorded step must have action='tap_and_wait_keyboard', NOT 'tap'.

    Before the b4 fix, Recorder.add_step was called by the underlying tool_tap
    with action='tap'. The composite tool then runs, sleeps, observes — but
    the recording.yaml entry was still 'tap', stripping the keyboard-wait
    semantic on replay (F-B3-010).
    """
    s = _make_sim_session(tmp_path)
    rec = _attach_fake_recorder(s)
    s.last_marks = [
        {"id": 1, "stable_id": "s-email", "stable_id_loose": "sl-email",
         "text": "Email", "center": (100, 200), "bbox": (50, 180, 100, 40),
         "confidence_band": "high"},
    ]

    fake_observe_result = {"marks": [], "target": "simulator"}
    from simdrive import server, observe as observe_mod
    with patch.object(server.act, "tap", return_value=(0, 0)), \
         patch.object(server, "tool_observe", return_value=fake_observe_result), \
         patch.object(observe_mod, "observe", return_value=_stub_observation(tmp_path)), \
         patch.object(server.time, "sleep"):
        result = server.tool_tap_and_wait_keyboard({
            "session_id": s.session_id,
            "stable_id": "s-email",
        })

    # Verify exactly one step recorded with the composite action name.
    assert len(rec.steps) == 1
    assert rec.steps[0]["action"] == "tap_and_wait_keyboard", (
        f"Expected the upgraded action name; got {rec.steps[0]['action']!r}. "
        "F-B3-010 regression — the recorder still serializes the composite as "
        "the underlying primitive."
    )
    # Confirm upgrade_step_action was actually called (not just that the
    # initial action happened to match).
    rec.upgrade_step_action.assert_called_with(rec.steps[0]["id"], "tap_and_wait_keyboard")
    # Sanity: the response still carries post_state from the observe.
    assert result["post_state"] == fake_observe_result


def test_tap_and_wait_keyboard_no_recorder_is_safe(tmp_path):
    """When no recorder is active, the upgrade call must be a no-op (not crash)."""
    s = _make_sim_session(tmp_path)
    assert s.recorder is None  # no recorder attached
    from simdrive import server
    with patch.object(server.act, "tap", return_value=(0, 0)), \
         patch.object(server, "tool_observe", return_value={"marks": []}), \
         patch.object(server.time, "sleep"):
        result = server.tool_tap_and_wait_keyboard({
            "session_id": s.session_id, "x": 100, "y": 200,
        })
    assert result["post_state"] == {"marks": []}


# ── F-B3-011: type_text keyboard_visible_reason on false-negative ──────────


def test_type_text_emits_keyboard_visible_reason_when_dispatched_but_collapsed(tmp_path):
    """When dispatch_succeeded=True but the post-type observe finds no
    keyboard chrome (the Palace instant-search scenario), the response must
    include a `keyboard_visible_reason` so the agent does NOT retry the type.
    """
    s = _make_sim_session(tmp_path)
    from simdrive import observe as observe_mod
    from simdrive import server, som

    # Mock a post_obs with NO keyboard chrome marks — mirrors Palace's case
    # where the search field auto-committed and dismissed the keyboard.
    no_kb_obs = _stub_observation(tmp_path)

    with patch.object(server.act, "type_text", return_value=None), \
         patch.object(server.act, "_backend", return_value="hid"), \
         patch.object(observe_mod, "observe", return_value=no_kb_obs):
        result = server.tool_type_text({
            "session_id": s.session_id,
            "text": "freedom",
        })

    assert result["dispatch_succeeded"] is True
    assert result["keyboard_visible"] is False
    assert "keyboard_visible_reason" in result
    reason = result["keyboard_visible_reason"]
    assert "dispatch_succeeded=true" in reason
    assert "do NOT retry" in reason


def test_type_text_no_reason_when_keyboard_visible_true(tmp_path):
    """When the heuristic correctly detects the keyboard, no reason field
    is emitted (it's noise on the happy path)."""
    s = _make_sim_session(tmp_path)
    from simdrive import observe as observe_mod
    from simdrive import server, som

    # Mock a post_obs WITH a keyboard-chrome mark — the heuristic should fire.
    kb_mark = som.Mark(
        id=99, x=600, y=2400, w=40, h=40,
        text="return", confidence=0.95, raw_confidence=0.95,
    )
    obs = _stub_observation(tmp_path)
    obs.marks = [kb_mark]

    with patch.object(server.act, "type_text", return_value=None), \
         patch.object(server.act, "_backend", return_value="hid"), \
         patch.object(observe_mod, "observe", return_value=obs):
        result = server.tool_type_text({
            "session_id": s.session_id,
            "text": "hello",
        })

    assert result["dispatch_succeeded"] is True
    assert result["keyboard_visible"] is True
    assert "keyboard_visible_reason" not in result
