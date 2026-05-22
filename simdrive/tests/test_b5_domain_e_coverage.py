"""Coverage fill-in for new F#3/F#8/F#9/F#13/F#16 production lines.

Targets lines that were added in:
  feat(b5): apps/perf/lint polish [F#3 F#8 F#9 F#13 F#16]

and left uncovered by the existing test suite, causing the --fail-under=90
gate to drop to ~89.2%.

Production code is NOT modified — this file only exercises the new paths.

Scope (modules measured by CI coverage gate):
  simdrive.server   — _compute_ssim (lines 815-874), verify_change block
                      in tool_tap (lines 1169-1173)
  simdrive.recorder — OSError path in _lint_one (lines 843-844)

Run under: pytest -m "not live"
"""
from __future__ import annotations

import io
import struct
import zlib
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
import yaml
from PIL import Image


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _png_bytes(w: int = 4, h: int = 4, color: tuple = (200, 200, 200)) -> bytes:
    """Return minimal valid PNG bytes (RGB, 8-bit) using PIL."""
    buf = io.BytesIO()
    Image.new("RGB", (w, h), color).save(buf, format="PNG")
    return buf.getvalue()


def _write_png(path: Path, w: int = 4, h: int = 4,
               color: tuple = (200, 200, 200)) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_png_bytes(w, h, color))
    return path


def _sim_session(tmp_path: Path, sid: str = "cov-e-sim"):
    """Build and register a minimal simulator session."""
    from simdrive import session
    from simdrive.sim import Device

    session._SESSIONS.pop(sid, None)
    s = session.Session(
        session_id=sid,
        device=Device(udid="UDID-E-SIM", name="iPhone Test",
                      os_version="26.3", state="Booted"),
        workdir=tmp_path / "wd",
        target="simulator",
    )
    s.workdir.mkdir(parents=True, exist_ok=True)
    session._SESSIONS[sid] = s
    return s


# ===========================================================================
# F#8 — _compute_ssim unit tests (server.py 815-874)
# ===========================================================================


class TestComputeSsim:
    """Direct unit tests for _compute_ssim; no simulator required."""

    def test_none_paths_return_1_0(self):
        """_compute_ssim(None, None) must return 1.0 (safe no-change default)."""
        from simdrive.server import _compute_ssim

        result = _compute_ssim(None, None)
        assert result == 1.0, (
            f"_compute_ssim(None, None) must return 1.0; got {result!r}"
        )

    def test_nonexistent_file_returns_1_0(self, tmp_path):
        """Missing file paths must return 1.0 via the exception fallback."""
        from simdrive.server import _compute_ssim

        missing = str(tmp_path / "no_such_file.png")
        result = _compute_ssim(missing, missing)
        assert result == 1.0, (
            f"_compute_ssim with missing files must return 1.0; got {result!r}"
        )

    def test_non_png_file_returns_1_0(self, tmp_path):
        """Non-PNG file (invalid magic bytes) must return 1.0 (can't compare)."""
        from simdrive.server import _compute_ssim

        not_png = tmp_path / "fake.png"
        not_png.write_bytes(b"this is not a PNG file at all, no magic bytes")
        result = _compute_ssim(str(not_png), str(not_png))
        assert result == 1.0, (
            f"Non-PNG bytes must fall through to 1.0; got {result!r}"
        )

    def test_identical_png_returns_near_1_0(self, tmp_path):
        """Two identical real PNG files must produce ssim near 1.0."""
        from simdrive.server import _compute_ssim

        png_path = _write_png(tmp_path / "img.png", w=8, h=8, color=(128, 64, 200))
        result = _compute_ssim(str(png_path), str(png_path))
        # Identical images must be >= 0.99 (floating-point SSIM formula is exact
        # for identical inputs; allow a tiny margin for the clamp).
        assert result >= 0.99, (
            f"Identical PNGs must return ~1.0; got {result!r}"
        )

    def test_different_png_returns_less_than_1_0(self, tmp_path):
        """Two visually different PNG files must produce ssim < 1.0."""
        from simdrive.server import _compute_ssim

        pre = _write_png(tmp_path / "pre.png", w=8, h=8, color=(0, 0, 0))
        post = _write_png(tmp_path / "post.png", w=8, h=8, color=(255, 255, 255))
        result = _compute_ssim(str(pre), str(post))
        assert result < 1.0, (
            f"Different PNGs must return < 1.0; got {result!r}"
        )

    def test_mismatched_dimensions_return_1_0(self, tmp_path):
        """PNGs of different sizes cannot be compared — must return 1.0."""
        from simdrive.server import _compute_ssim

        small = _write_png(tmp_path / "small.png", w=4, h=4)
        large = _write_png(tmp_path / "large.png", w=8, h=8)
        result = _compute_ssim(str(small), str(large))
        assert result == 1.0, (
            f"Dimension-mismatched PNGs must return 1.0; got {result!r}"
        )

    def test_result_is_float_in_0_1_range(self, tmp_path):
        """_compute_ssim return value must always be a float in [0.0, 1.0]."""
        from simdrive.server import _compute_ssim

        pre = _write_png(tmp_path / "pre.png", w=6, h=6, color=(100, 150, 200))
        post = _write_png(tmp_path / "post.png", w=6, h=6, color=(50, 50, 50))
        result = _compute_ssim(str(pre), str(post))
        assert isinstance(result, float), f"Must return float; got {type(result)}"
        assert 0.0 <= result <= 1.0, f"Must be in [0, 1]; got {result!r}"

    def test_empty_path_string_returns_1_0(self):
        """Empty string path must fall through to 1.0 (no crash)."""
        from simdrive.server import _compute_ssim

        result = _compute_ssim("", "")
        assert result == 1.0, f"Empty string paths must return 1.0; got {result!r}"

    def test_rgba_png_returns_valid_float(self, tmp_path):
        """An RGBA PNG (color_type=6) should parse and return a valid float."""
        from simdrive.server import _compute_ssim

        buf = io.BytesIO()
        Image.new("RGBA", (4, 4), (100, 150, 200, 255)).save(buf, format="PNG")
        rgba_path = tmp_path / "rgba.png"
        rgba_path.write_bytes(buf.getvalue())
        result = _compute_ssim(str(rgba_path), str(rgba_path))
        # Identical RGBA images should also return near 1.0 or the no-change default.
        assert isinstance(result, float), f"Must return float; got {type(result)}"
        assert 0.0 <= result <= 1.0


# ===========================================================================
# F#8 — verify_change in tool_tap (server.py 1169-1173)
# ===========================================================================


class TestToolTapVerifyChange:
    """Integration tests for verify_change=True path in tool_tap."""

    def test_verify_change_true_adds_screen_changed_and_ssim_delta(
        self, tmp_path, monkeypatch
    ):
        """verify_change=True must add screen_changed + ssim_delta to response."""
        from simdrive import server, session, act

        png = _write_png(tmp_path / "pre.png", w=4, h=4)
        s = _sim_session(tmp_path, "vc-true-1")
        s.last_screenshot_w = 1206
        s.last_screenshot_h = 2622
        s.last_screenshot_path = str(png)

        monkeypatch.setattr(act, "tap", lambda x, y, sw, sh, udid=None: (x, y))
        monkeypatch.setattr(session, "append_action", lambda s, action: None)
        # Monkeypatch _compute_ssim to return a known value (no real PNG I/O).
        monkeypatch.setattr(server, "_compute_ssim", lambda pre, post: 0.8)

        resp = server.tool_tap({
            "session_id": "vc-true-1",
            "x": 100,
            "y": 200,
            "verify_change": True,
        })

        assert resp.get("ok") is True
        assert "screen_changed" in resp, (
            f"verify_change=True must add 'screen_changed'; keys={list(resp.keys())}"
        )
        assert "ssim_delta" in resp, (
            f"verify_change=True must add 'ssim_delta'; keys={list(resp.keys())}"
        )
        assert isinstance(resp["screen_changed"], bool)
        assert isinstance(resp["ssim_delta"], float)
        # ssim=0.8 → delta=0.2, which is > 0.05 → screen_changed=True
        assert resp["screen_changed"] is True
        assert abs(resp["ssim_delta"] - 0.2) < 0.001

    def test_verify_change_false_omits_screen_changed(
        self, tmp_path, monkeypatch
    ):
        """Default (no verify_change) must NOT add screen_changed to response."""
        from simdrive import server, session, act

        png = _write_png(tmp_path / "pre2.png", w=4, h=4)
        s = _sim_session(tmp_path, "vc-false-1")
        s.last_screenshot_w = 1206
        s.last_screenshot_h = 2622
        s.last_screenshot_path = str(png)

        monkeypatch.setattr(act, "tap", lambda x, y, sw, sh, udid=None: (x, y))
        monkeypatch.setattr(session, "append_action", lambda s, action: None)

        resp = server.tool_tap({
            "session_id": "vc-false-1",
            "x": 100,
            "y": 200,
        })

        assert resp.get("ok") is True
        assert "screen_changed" not in resp
        assert "ssim_delta" not in resp

    def test_verify_change_true_no_change_gives_screen_changed_false(
        self, tmp_path, monkeypatch
    ):
        """When ssim=1.0 (no change), screen_changed must be False."""
        from simdrive import server, session, act

        png = _write_png(tmp_path / "pre3.png", w=4, h=4)
        s = _sim_session(tmp_path, "vc-true-2")
        s.last_screenshot_w = 1206
        s.last_screenshot_h = 2622
        s.last_screenshot_path = str(png)

        monkeypatch.setattr(act, "tap", lambda x, y, sw, sh, udid=None: (x, y))
        monkeypatch.setattr(session, "append_action", lambda s, action: None)
        monkeypatch.setattr(server, "_compute_ssim", lambda pre, post: 1.0)

        resp = server.tool_tap({
            "session_id": "vc-true-2",
            "x": 100,
            "y": 200,
            "verify_change": True,
        })

        assert resp["screen_changed"] is False
        assert resp["ssim_delta"] < 0.05


# ===========================================================================
# F#16 — recorder.py OSError path in _lint_one (lines 843-844)
# ===========================================================================


class TestLintOneOsError:
    """Cover the OSError branch in _lint_one (recorder.py line 843-844)."""

    def test_lint_unreadable_yaml_returns_fail_with_read_error(self, tmp_path):
        """When reading recording.yaml raises OSError, lint must return status='fail'
        with 'read error' in reason.

        This covers recorder.py lines 843-844 (the except OSError branch).
        """
        from simdrive.recorder import lint_recordings, _lint_one
        import simdrive.recorder as rec_mod

        rec_dir = tmp_path / "unreadable"
        rec_dir.mkdir()
        yaml_path = rec_dir / "recording.yaml"
        yaml_path.write_text("name: test\nsteps: []")

        # Patch Path.read_text on the specific file to raise OSError.
        original_read_text = Path.read_text

        def _patched_read_text(self, *args, **kwargs):
            if self == yaml_path:
                raise OSError("permission denied (mock)")
            return original_read_text(self, *args, **kwargs)

        with patch.object(Path, "read_text", _patched_read_text):
            results = lint_recordings(tmp_path)

        assert len(results) == 1
        r = results[0]
        assert r.status == "fail", (
            f"OSError in read must give status='fail'; got {r.status!r}"
        )
        assert "read error" in r.reason, (
            f"Reason must contain 'read error'; got {r.reason!r}"
        )

    def test_lint_one_oserror_category_is_fail(self, tmp_path):
        """OSError path must set category='fail' on the returned LintResult."""
        from simdrive.recorder import _lint_one

        yaml_path = tmp_path / "recording.yaml"
        yaml_path.write_text("name: test\nsteps: []")

        original_read_text = Path.read_text

        def _patched_read_text(self, *args, **kwargs):
            if self == yaml_path:
                raise OSError("no permission")
            return original_read_text(self, *args, **kwargs)

        with patch.object(Path, "read_text", _patched_read_text):
            result = _lint_one(yaml_path)

        assert result.category == "fail", (
            f"OSError path must set category='fail'; got {result.category!r}"
        )


# ===========================================================================
# F#16 — LintResult category field — round-trip via to_dict
# ===========================================================================


class TestLintResultCategoryField:
    """Verify the category field is present and round-trips correctly."""

    def test_to_dict_includes_category_for_ok_recording(self, tmp_path):
        """to_dict() must include 'category' key for an ok recording."""
        import yaml as _yaml
        from simdrive.recorder import lint_recordings

        _GOOD_REQUIRES = {
            "app": {"bundle_id": "com.example.app", "version": "2.4.1",
                    "version_match": "minor"},
            "sim": {"device": "iPhone 17 Pro", "ios_version": "26.3"},
            "initial_state": {
                "foreground": True,
                "text_subset_required": ["Library"],
                "text_subset_forbidden": [],
                "primary_button_label": None,
            },
        }
        rec_dir = tmp_path / "ok_rec"
        rec_dir.mkdir()
        (rec_dir / "recording.yaml").write_text(_yaml.safe_dump({
            "name": "ok_rec",
            "created_at": 0.0,
            "steps": [{"action": "tap"}],
            "requires": _GOOD_REQUIRES,
        }))

        results = lint_recordings(tmp_path)
        assert len(results) == 1
        d = results[0].to_dict()
        assert "category" in d, f"to_dict() must include 'category'; keys={list(d.keys())}"
        assert d["category"] == "ok"

    def test_empty_recording_category_via_to_dict(self, tmp_path):
        """to_dict() on an empty-step recording must return category='empty'."""
        import yaml as _yaml
        from simdrive.recorder import lint_recordings

        rec_dir = tmp_path / "empty_rec"
        rec_dir.mkdir()
        (rec_dir / "recording.yaml").write_text(_yaml.safe_dump({
            "name": "empty_rec",
            "created_at": 0.0,
            "steps": [],
        }))

        results = lint_recordings(tmp_path)
        assert len(results) == 1
        d = results[0].to_dict()
        assert d["category"] == "empty", (
            f"0-step recording must have category='empty'; got {d['category']!r}"
        )
        assert d["status"] == "empty"

    def test_missing_state_contract_category_via_to_dict(self, tmp_path):
        """to_dict() on a recording with steps but no requires block must have
        category='missing_state_contract'."""
        import yaml as _yaml
        from simdrive.recorder import lint_recordings

        rec_dir = tmp_path / "no_contract"
        rec_dir.mkdir()
        (rec_dir / "recording.yaml").write_text(_yaml.safe_dump({
            "name": "no_contract",
            "created_at": 0.0,
            "steps": [{"action": "tap"}],
        }))

        results = lint_recordings(tmp_path)
        assert len(results) == 1
        d = results[0].to_dict()
        assert d["category"] == "missing_state_contract", (
            f"Recording with steps but no requires must have "
            f"category='missing_state_contract'; got {d['category']!r}"
        )
