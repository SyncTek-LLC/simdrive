"""INIT-2026-641 item 4.3 (D2) — tap's verify_change becomes discoverable
and defaults to true, returning a nested `post_state` (not flattened keys).

Before this item: `verify_change` was read via
`arguments.get("verify_change", False)` inside tool_tap but never declared
in tap's inputSchema (see tests/test_mcp_schema_handler_sync.py), so no
agent could discover it existed, and even when passed explicitly it
returned flat `screen_changed`/`ssim_delta` top-level keys. The OCR/marks
pass that already ran to compute the pre/post SSIM was thrown away instead
of refreshing `s.last_marks`.
"""
from __future__ import annotations

import io
from pathlib import Path
from unittest.mock import patch

import pytest
from PIL import Image

from simdrive import act, observe as observe_mod, server, session, som
from simdrive.sim import Device


def _png_bytes(w: int = 4, h: int = 4, color: tuple = (200, 200, 200)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (w, h), color).save(buf, format="PNG")
    return buf.getvalue()


def _write_png(path: Path, w: int = 4, h: int = 4, color: tuple = (200, 200, 200)) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_png_bytes(w, h, color))
    return path


def _sim_session(tmp_path: Path, sid: str) -> session.Session:
    session._SESSIONS.pop(sid, None)
    s = session.Session(
        session_id=sid,
        device=Device(udid=f"UDID-{sid}", name="iPhone Test", os_version="26.3", state="Booted"),
        workdir=tmp_path / "wd",
        target="simulator",
    )
    s.workdir.mkdir(parents=True, exist_ok=True)
    session._SESSIONS[sid] = s
    return s


def _fake_marks(n: int) -> list:
    return [
        som.Mark(id=i + 1, x=10, y=10 * (i + 1), w=40, h=20, text=f"Item {i}", confidence=0.9)
        for i in range(n)
    ]


def _patch_observe_returns(marks: list, tmp_path: Path):
    """Patch observe.observe so any internal call returns a fresh Observation
    with the given marks and a real (tiny) screenshot on disk, without
    touching sim.screenshot/Vision OCR.
    """
    real_observe = observe_mod.observe

    def fake_observe(udid, out_dir, annotate=True, **kwargs):
        out_dir = Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / "post.png"
        _write_png(path)
        annotated_path = None
        if annotate:
            annotated_path = out_dir / "post-som.png"
            _write_png(annotated_path)
        return observe_mod.Observation(
            screenshot_path=path,
            annotated_path=annotated_path,
            screenshot_w=100,
            screenshot_h=200,
            window_bounds=None,
            captured_at=1234.0,
            marks=list(marks),
        )

    return fake_observe


class TestTapDefaultVerifyChange:
    def test_tap_defaults_verify_change_true_and_returns_post_state(self, tmp_path, monkeypatch):
        """No verify_change key at all -> response carries `post_state`, not
        flat `screen_changed`/`ssim_delta` keys. Fails today two ways: the
        default is False, and even when explicitly True, current code
        returns flat top-level keys, not nested.
        """
        png = _write_png(tmp_path / "pre.png")
        s = _sim_session(tmp_path, "vc-default-1")
        s.last_screenshot_w = 100
        s.last_screenshot_h = 200
        s.last_screenshot_path = str(png)

        monkeypatch.setattr(act, "tap", lambda x, y, sw, sh, udid=None: (x, y))
        monkeypatch.setattr(session, "append_action", lambda s, action: None)
        monkeypatch.setattr(server, "_compute_ssim", lambda pre, post: 0.8)
        monkeypatch.setattr(
            server.observe, "observe", _patch_observe_returns(_fake_marks(3), tmp_path)
        )

        resp = server.tool_tap({"session_id": "vc-default-1", "x": 100, "y": 200})

        assert resp["ok"] is True
        assert "post_state" in resp, f"verify_change must default true; keys={list(resp.keys())}"
        assert "screen_changed" not in resp, "post_state must be nested, not flattened"
        assert "ssim_delta" not in resp, "post_state must be nested, not flattened"
        post_state = resp["post_state"]
        assert set(post_state.keys()) == {"marks", "screen_changed", "ssim_delta", "screenshot_path"}
        assert post_state["screen_changed"] is True
        assert abs(post_state["ssim_delta"] - 0.2) < 0.001
        assert post_state["marks"], "post_state.marks must be populated from the reused OCR pass"
        # post_state.marks is always the compact shape.
        for m in post_state["marks"]:
            assert set(m.keys()) <= {
                "id", "stable_id", "text", "center", "bbox", "confidence_band",
                "english_like", "source",
            }

    def test_tap_verify_change_false_opts_out(self, tmp_path, monkeypatch):
        """verify_change is schema-visible and settable to False — a default
        an agent can't see or turn off is not a real opt-out.
        """
        png = _write_png(tmp_path / "pre2.png")
        s = _sim_session(tmp_path, "vc-false-1")
        s.last_screenshot_w = 100
        s.last_screenshot_h = 200
        s.last_screenshot_path = str(png)

        monkeypatch.setattr(act, "tap", lambda x, y, sw, sh, udid=None: (x, y))
        monkeypatch.setattr(session, "append_action", lambda s, action: None)

        resp = server.tool_tap({"session_id": "vc-false-1", "x": 100, "y": 200, "verify_change": False})
        assert resp["ok"] is True
        assert "post_state" not in resp

    def test_verify_change_declared_in_tap_schema(self):
        tap_tool = next(t for t in server._TOOLS if t["name"] == "tap")
        assert "verify_change" in server._schema_declared_params(tap_tool)

    def test_post_state_marks_refresh_last_marks_for_followup_tap(self, tmp_path, monkeypatch):
        """D2's own acceptance test: tap once, pull a stable_id from
        post_state.marks, tap again with that id and no intervening
        tool_observe — it must resolve, not hit target_not_found.
        """
        png = _write_png(tmp_path / "pre3.png")
        s = _sim_session(tmp_path, "vc-refresh-1")
        s.last_screenshot_w = 100
        s.last_screenshot_h = 200
        s.last_screenshot_path = str(png)
        s.last_marks = [m.to_dict() for m in _fake_marks(1)]  # stale, pre-tap marks

        monkeypatch.setattr(act, "tap", lambda x, y, sw, sh, udid=None: (x, y))
        monkeypatch.setattr(session, "append_action", lambda s, action: None)
        monkeypatch.setattr(server, "_compute_ssim", lambda pre, post: 0.5)
        fresh_marks = _fake_marks(2)
        # Give the fresh marks distinguishable text from the stale ones so a
        # test bug (still resolving against the stale cache) would be caught.
        fresh_marks[0].text = "Fresh Target"
        monkeypatch.setattr(
            server.observe, "observe", _patch_observe_returns(fresh_marks, tmp_path)
        )

        resp = server.tool_tap({"session_id": "vc-refresh-1", "x": 5, "y": 5})
        fresh_stable_id = resp["post_state"]["marks"][0]["stable_id"]

        # No intervening tool_observe call.
        resp2 = server.tool_tap({"session_id": "vc-refresh-1", "stable_id": fresh_stable_id})
        assert resp2["ok"] is True
        assert resp2["tapped_mark"]["text"] == "Fresh Target"

    def test_verify_change_reuses_ocr_pass_without_drawing_annotation(self, tmp_path, monkeypatch):
        """Item C (lazy annotation): the internal observe used to build
        post_state must not draw the annotated PNG — nothing in post_state
        ever surfaces an annotated_path, so drawing one is pure waste.
        """
        png = _write_png(tmp_path / "pre4.png")
        s = _sim_session(tmp_path, "vc-lazy-1")
        s.last_screenshot_w = 100
        s.last_screenshot_h = 200
        s.last_screenshot_path = str(png)

        monkeypatch.setattr(act, "tap", lambda x, y, sw, sh, udid=None: (x, y))
        monkeypatch.setattr(session, "append_action", lambda s, action: None)
        monkeypatch.setattr(server, "_compute_ssim", lambda pre, post: 1.0)

        draw_calls = []
        real_observe_observe = observe_mod.observe

        def spying_observe(udid, out_dir, annotate=True, **kwargs):
            draw_calls.append(annotate)
            return _patch_observe_returns(_fake_marks(1), tmp_path)(udid, out_dir, annotate=annotate, **kwargs)

        monkeypatch.setattr(server.observe, "observe", spying_observe)

        server.tool_tap({"session_id": "vc-lazy-1", "x": 1, "y": 1})

        assert draw_calls, "expected the verify_change fallback to call observe.observe at least once"
        assert all(a is False for a in draw_calls), (
            f"tap's verify_change post-observe must pass annotate=False (nothing in "
            f"post_state ever reads annotated_path); got annotate values {draw_calls}"
        )
