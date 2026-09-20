"""INIT-2026-641 item 4.4 (D2) — type_text gains the same verify_change
mechanism tap has (item 4.3): default-true, nested `post_state` with a
fresh compact marks array, screen_changed, ssim_delta, screenshot_path.

Before this item: type_text had no verify_change mechanism at all. Its
pre-type screenshot capture was gated behind `if s.recorder`, so the harder
case (no recorder attached) captured no pre-type frame to compare against.
Both device and simulator paths already ran a post-type observe.observe()
call to derive the keyboard_visible heuristic and already refreshed
s.last_marks from it — this item reuses that pass rather than adding a
second one.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from simdrive import observe as observe_mod
from simdrive import server, session as session_mod, som
from simdrive.sim import Device

# Module-level imports (not lazy, function-local ones) are deliberate here:
# test_tool_load_journey.py::test_load_journey_no_anthropic_import purges
# every `simdrive.*` entry from sys.modules in its own finally block without
# restoring them, so a *later* lazy `from simdrive import session` (evaluated
# at test-runtime, after that purge already ran) re-imports a disjoint fresh
# copy of the session module, with its own empty _SESSIONS dict, out of sync
# with the one server.py's already-imported reference actually reads from.
# Binding session_mod here at collection time (before any test body runs)
# avoids that trap, matching the working pattern in test_tap_verify_change.py.

_FAKE_UDID = "31471BBD-0000-B4FIX-TYPE-TEXT-VC"


def _make_sim_session(tmp_path: Path, sid: str = "vc-type-1"):
    session_mod._SESSIONS.pop(sid, None)
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


def _obs(tmp_path: Path, name: str, marks: list, annotate: bool = False) -> observe_mod.Observation:
    path = tmp_path / f"{name}.png"
    path.write_bytes(b"\x89PNG\r\n\x1a\n")
    return observe_mod.Observation(
        screenshot_path=path,
        annotated_path=(tmp_path / f"{name}-som.png") if annotate else None,
        screenshot_w=1206,
        screenshot_h=2622,
        window_bounds=None,
        captured_at=0.0,
        marks=list(marks),
        recent_logs=None,
    )


class TestTypeTextVerifyChange:
    def test_type_text_returns_post_state_by_default_with_no_recorder(self, tmp_path):
        """The harder case per the impl plan: s.recorder is None, so nothing
        else in the handler was already going to capture a pre-type frame.
        """
        s = _make_sim_session(tmp_path)
        assert s.recorder is None

        pre = _obs(tmp_path, "pre", marks=[])
        post = _obs(tmp_path, "post", marks=[
            som.Mark(id=1, x=10, y=10, w=40, h=20, text="Search", confidence=0.9),
        ])
        calls = iter([pre, post])

        with patch.object(server.act, "type_text", return_value=None), \
             patch.object(server.act, "_backend", return_value="hid"), \
             patch.object(observe_mod, "observe", side_effect=lambda *a, **kw: next(calls)):
            result = server.tool_type_text({"session_id": s.session_id, "text": "hello"})

        assert result["ok"] is True
        assert "post_state" in result
        post_state = result["post_state"]
        assert set(post_state.keys()) == {"marks", "screen_changed", "ssim_delta", "screenshot_path"}
        assert post_state["marks"], "post_state.marks must be populated from the reused post-type OCR pass"

    def test_type_text_verify_change_false_omits_post_state(self, tmp_path):
        s = _make_sim_session(tmp_path)
        pre = _obs(tmp_path, "pre2", marks=[])
        post = _obs(tmp_path, "post2", marks=[])
        calls = iter([pre, post])

        with patch.object(server.act, "type_text", return_value=None), \
             patch.object(server.act, "_backend", return_value="hid"), \
             patch.object(observe_mod, "observe", side_effect=lambda *a, **kw: next(calls)):
            result = server.tool_type_text({
                "session_id": s.session_id, "text": "hello", "verify_change": False,
            })

        assert result["ok"] is True
        assert "post_state" not in result

    def test_verify_change_declared_in_type_text_schema(self):
        tool = next(t for t in server._TOOLS if t["name"] == "type_text")
        assert "verify_change" in server._schema_declared_params(tool)

    def test_type_text_last_marks_refreshed_for_followup_tap(self, tmp_path):
        """Both type_text paths already refresh s.last_marks from the
        post-type observe (confirmed directly in the impl plan); this pins
        that a follow-up tap(mark:/stable_id:) resolves the fresh marks
        surfaced in post_state, not a stale cache.
        """
        s = _make_sim_session(tmp_path)
        pre = _obs(tmp_path, "pre3", marks=[])
        fresh_mark = som.Mark(id=1, x=10, y=10, w=40, h=20, text="Go", confidence=0.9)
        post = _obs(tmp_path, "post3", marks=[fresh_mark])
        calls = iter([pre, post])

        with patch.object(server.act, "type_text", return_value=None), \
             patch.object(server.act, "_backend", return_value="hid"), \
             patch.object(observe_mod, "observe", side_effect=lambda *a, **kw: next(calls)), \
             patch.object(server.act, "tap", return_value=(0, 0)), \
             patch.object(server.session, "append_action", lambda s, action: None), \
             patch.object(server, "_compute_ssim", lambda pre, post: 1.0):
            result = server.tool_type_text({"session_id": s.session_id, "text": "hello"})
            stable_id = result["post_state"]["marks"][0]["stable_id"]
            resp2 = server.tool_tap({"session_id": s.session_id, "stable_id": stable_id, "verify_change": False})

        assert resp2["ok"] is True
        assert resp2["tapped_mark"]["text"] == "Go"

    def test_type_text_pre_and_post_observe_are_lazy_annotate_false(self, tmp_path):
        """Item C: neither of type_text's two internal observe() calls should
        draw the annotated PNG — no response field ever surfaces it."""
        s = _make_sim_session(tmp_path)
        pre = _obs(tmp_path, "pre4", marks=[])
        post = _obs(tmp_path, "post4", marks=[])
        seen_annotate = []

        def fake_observe(udid, out_dir, annotate=True, **kwargs):
            seen_annotate.append(annotate)
            return pre if len(seen_annotate) == 1 else post

        with patch.object(server.act, "type_text", return_value=None), \
             patch.object(server.act, "_backend", return_value="hid"), \
             patch.object(observe_mod, "observe", side_effect=fake_observe):
            server.tool_type_text({"session_id": s.session_id, "text": "hello"})

        assert len(seen_annotate) == 2
        assert all(a is False for a in seen_annotate), (
            f"type_text's internal observe() calls must pass annotate=False; got {seen_annotate}"
        )
