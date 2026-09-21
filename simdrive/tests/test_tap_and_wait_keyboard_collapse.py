"""INIT-2026-641 item 4.5 (D2) — collapse tap_and_wait_keyboard into a thin
wrapper over tap's new default-verify_change behavior (item 4.3), while
preserving its existing response contract exactly: full annotate=True
marks under `post_state`, not tap's new compact/capped shape.

A refactor that "should" be a no-op is precisely where a silent regression
hides, so this gets its own directed tests rather than a trust-the-
description pass, per the test plan's section 1d.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from simdrive import observe as observe_mod
from simdrive import server, session as session_mod

_FAKE_UDID = "31471BBD-0000-641-TAWK-COLLAPSE"


def _make_sim_session(tmp_path: Path, sid: str = "tawk-1"):
    from simdrive.sim import Device

    session_mod._SESSIONS.pop(sid, None)
    d = Device(udid=_FAKE_UDID, name="Test Sim", os_version="26.0", state="active")
    workdir = tmp_path / "sessions" / sid
    workdir.mkdir(parents=True, exist_ok=True)
    s = session_mod.Session(
        session_id=sid, device=d, workdir=workdir, target="simulator",
        last_screenshot_w=1206, last_screenshot_h=2622,
    )
    session_mod._SESSIONS[sid] = s
    return s


def _attach_fake_recorder(s):
    rec = MagicMock()
    rec.steps = []

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
    s.last_screenshot_path = s.workdir / "pre.png"
    s.last_screenshot_path.write_bytes(b"\x89PNG\r\n\x1a\n")
    return rec


def test_tap_and_wait_keyboard_response_shape_is_unchanged_after_the_collapse(tmp_path):
    """The final `post_state` must be the FULL tool_observe response (rich,
    non-compact marks), not tap's new compact/capped post_state leaking
    through. A compact 6/7-key mark dict is a different, smaller shape than
    this tool's existing full mark dict, and an agent reading e.g.
    raw_confidence off a mark in the response would silently start getting
    KeyError/None instead of a clear break if this regressed.
    """
    s = _make_sim_session(tmp_path)
    full_observe_response = {
        "screenshot_path": "/tmp/x.png",
        "marks": [
            {"id": 1, "stable_id": "abc", "stable_id_loose": "abcd", "bbox": [0, 0, 1, 1],
             "center": [0, 0], "text": "delete", "confidence": 0.9, "raw_confidence": 0.9,
             "confidence_band": "high", "english_like": True, "alternates": []},
        ],
        "target": "simulator",
    }
    with patch.object(server.act, "tap", return_value=(0, 0)), \
         patch.object(server, "tool_observe", return_value=full_observe_response), \
         patch.object(server.session, "append_action", lambda s, action: None), \
         patch.object(server, "_compute_ssim", lambda pre, post: 1.0), \
         patch.object(server.time, "sleep"):
        result = server.tool_tap_and_wait_keyboard({"session_id": s.session_id, "x": 1, "y": 1})

    assert result["post_state"] == full_observe_response
    # Guard against the compact shape leaking in alongside/instead of the
    # full one: the rich mark dict's own diagnostic keys must be present.
    mark = result["post_state"]["marks"][0]
    assert "raw_confidence" in mark
    assert "alternates" in mark


def test_tap_and_wait_keyboard_still_honours_its_keyboard_settle(tmp_path):
    """The wrapper must still apply _KEYBOARD_SETTLE_SEC before observing —
    routed through tap's own settle_ms handling (item 4.5's change), not a
    bare time.sleep call at the wrapper level, and not silently lost when a
    caller passes no settle_ms of their own.
    """
    s = _make_sim_session(tmp_path)
    captured_tap_args = {}

    def fake_tool_tap(arguments):
        captured_tap_args.update(arguments)
        return {"ok": True, "pixel_x": 1, "pixel_y": 1}

    with patch.object(server, "tool_tap", side_effect=fake_tool_tap), \
         patch.object(server, "tool_observe", return_value={"marks": []}), \
         patch.object(server.time, "sleep") as mock_sleep:
        server.tool_tap_and_wait_keyboard({"session_id": s.session_id, "x": 1, "y": 1})

    assert captured_tap_args.get("settle_ms") == int(server._KEYBOARD_SETTLE_SEC * 1000), (
        f"expected tap to be called with the keyboard settle in ms; got "
        f"settle_ms={captured_tap_args.get('settle_ms')!r}"
    )
    # No bare time.sleep(_KEYBOARD_SETTLE_SEC) at the wrapper level anymore —
    # the settle now happens inside the (mocked-out) tool_tap call itself.
    mock_sleep.assert_not_called()

    # A caller-supplied settle_ms must not silently lose the keyboard wait:
    # the effective settle passed to tap must be at least the keyboard settle.
    captured_tap_args.clear()
    with patch.object(server, "tool_tap", side_effect=fake_tool_tap), \
         patch.object(server, "tool_observe", return_value={"marks": []}), \
         patch.object(server.time, "sleep"):
        server.tool_tap_and_wait_keyboard({
            "session_id": s.session_id, "x": 1, "y": 1, "settle_ms": 50,
        })
    assert captured_tap_args.get("settle_ms") >= int(server._KEYBOARD_SETTLE_SEC * 1000)


def test_tap_and_wait_keyboard_still_calls_upgrade_step_action(tmp_path):
    """The wrapper must still call upgrade_step_action(step_id,
    'tap_and_wait_keyboard') when a recorder is attached, preserving replay
    semantics exactly as today. A response that looks identical could still
    silently drop this side effect, so assert it directly.
    """
    s = _make_sim_session(tmp_path)
    rec = _attach_fake_recorder(s)
    s.last_marks = [
        {"id": 1, "stable_id": "s-email", "stable_id_loose": "sl-email",
         "text": "Email", "center": [100, 200], "bbox": [50, 180, 100, 40],
         "confidence_band": "high"},
    ]

    with patch.object(server.act, "tap", return_value=(0, 0)), \
         patch.object(server, "tool_observe", return_value={"marks": [], "target": "simulator"}), \
         patch.object(observe_mod, "observe", return_value=None), \
         patch.object(server, "_compute_ssim", lambda pre, post: 1.0), \
         patch.object(server.time, "sleep"):
        # observe.observe would be called by the underlying tool_tap's
        # verify_change fallback; give it a harmless stub via _record_act_step
        # instead by exercising the recorder path (post_obs comes from
        # observe.observe inside _record_act_step, which we don't want to hit
        # real Vision OCR for, so patch it to a minimal Observation).
        from simdrive.observe import Observation
        fake_obs = Observation(
            screenshot_path=s.workdir / "post.png", annotated_path=None,
            screenshot_w=1206, screenshot_h=2622, window_bounds=None,
            captured_at=0.0, marks=[],
        )
        with patch.object(observe_mod, "observe", return_value=fake_obs):
            result = server.tool_tap_and_wait_keyboard({
                "session_id": s.session_id, "stable_id": "s-email",
            })

    assert len(rec.steps) == 1
    assert rec.steps[0]["action"] == "tap_and_wait_keyboard"
    rec.upgrade_step_action.assert_called_with(rec.steps[0]["id"], "tap_and_wait_keyboard")


def test_plain_tap_now_returns_compact_post_state_not_a_regression(tmp_path):
    """The flip side, named explicitly per the test plan so a reviewer does
    not mistake 'plain tap grew a new key' for the collapse regressing
    something: a plain tap call (not tap_and_wait_keyboard) now returns a
    compact post_state by default (item 4.3) — intended new behavior, a
    distinct contract from tap_and_wait_keyboard's full-annotate one.
    """
    s = _make_sim_session(tmp_path)
    with patch.object(server.act, "tap", return_value=(0, 0)), \
         patch.object(server.session, "append_action", lambda s, action: None), \
         patch.object(server, "_compute_ssim", lambda pre, post: 1.0), \
         patch.object(observe_mod, "observe") as mock_observe:
        from simdrive.observe import Observation
        mock_observe.return_value = Observation(
            screenshot_path=s.workdir / "post.png", annotated_path=None,
            screenshot_w=1206, screenshot_h=2622, window_bounds=None,
            captured_at=0.0, marks=[],
        )
        result = server.tool_tap({"session_id": s.session_id, "x": 1, "y": 1})

    assert "post_state" in result
    assert set(result["post_state"].keys()) == {"marks", "screen_changed", "ssim_delta", "screenshot_path"}
