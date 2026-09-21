"""INIT-2026-641 item 4.6 (D3) — `final_expect` text assertions.

Closes the measured 0.9824 blind spot documented in commit be1b8ba's own
message: two frames differing only by a small indicator (a countdown chip)
score 0.9824 against the 0.85 sim SSIM threshold and pass. SSIM cannot see
a small rendered region; OCR text can. Every fixture here reproduces that
*shape* (near-identical frames, not a full-frame color swap) rather than
relying on the existing full-swap fixtures in
tests/test_replay_outcome_verification.py, which cannot exercise this gap.
"""
from __future__ import annotations

from pathlib import Path

import yaml
from PIL import Image, ImageDraw

from simdrive import recorder as rec_mod


def _make_sim_session(tmp_path: Path, sid: str = "final-expect-sim"):
    from simdrive import session as ses_mod
    from simdrive.sim import Device

    ses_mod._SESSIONS.clear()
    device = Device(udid="SIM-FE-UDID", name="iPhone 17 Pro", os_version="26.1", state="active")
    workdir = tmp_path / "sessions" / sid
    workdir.mkdir(parents=True, exist_ok=True)
    s = ses_mod.Session(
        session_id=sid, device=device, workdir=workdir, target="simulator",
        last_screenshot_w=1206, last_screenshot_h=2622, last_marks=[],
    )
    ses_mod._SESSIONS[sid] = s
    return s


def _near_identical_frames(off_path: Path, armed_path: Path, w: int = 1206, h: int = 2622) -> None:
    """Two frames identical except for a small rendered region in one
    corner (a "countdown chip"), the exact shape of the measured 0.9824
    near-miss, not a full-frame color swap.
    """
    base = Image.new("RGB", (w, h), (80, 120, 160))
    d = ImageDraw.Draw(base)
    for i in range(0, w, 50):
        d.line([(i, 0), (i, h)], fill=(70, 110, 150), width=2)
    for j in range(0, h, 50):
        d.line([(0, j), (w, j)], fill=(70, 110, 150), width=2)
    base.save(off_path)

    armed = base.copy()
    d2 = ImageDraw.Draw(armed)
    d2.rectangle([900, 100, 1100, 160], fill=(255, 0, 0))
    d2.text((910, 115), "00:59", fill=(255, 255, 255))
    armed.save(armed_path)


def _write_recording(rec_dir: Path, *, final_expect=None) -> None:
    """One-step recording whose capture ended ARMED (post = armed.png);
    the recorded pre-frame is the OFF frame."""
    snaps = rec_dir / "snapshots"
    snaps.mkdir(parents=True, exist_ok=True)
    _near_identical_frames(snaps / "001_pre.png", snaps / "001_post.png")
    payload = {
        "name": rec_dir.name,
        "created_at": 0.0,
        "target": "simulator",
        "device": "iPhone 17 Pro",
        "os_version": "26.1",
        "app_bundle_id": "org.example.timer",
        "simdrive_version": "test",
        "steps": [{
            "id": 1,
            "action": "tap",
            "args": {"x": 300, "y": 1900, "screenshot_w": 1206, "screenshot_h": 2622},
            "pre_screenshot": "snapshots/001_pre.png",
            "post_screenshot": "snapshots/001_post.png",
            "captured_at": 1.0,
        }],
    }
    if final_expect is not None:
        payload["final_expect"] = final_expect
    (rec_dir / "recording.yaml").write_text(yaml.safe_dump(payload, sort_keys=False))


def _patch_live_frame(monkeypatch, session, path: Path, marks: list):
    """Every _observe_for_replay call during this replay returns the SAME
    live frame + marks — the app never re-armed the timer during the run.
    """
    def _fake(s):
        return {"screenshot_path": path, "marks_count": len(marks), "marks": list(marks),
                "screenshot_w": 1206, "screenshot_h": 2622}

    monkeypatch.setattr(rec_mod, "_observe_for_replay", _fake, raising=False)


def _patch_tap(monkeypatch):
    from simdrive import act
    monkeypatch.setattr(act, "tap", lambda *a, **kw: None)


def _patch_no_crashes(monkeypatch):
    """Neutral for item 4.6's tests — item 4.7 covers crash detection itself."""
    from simdrive import diagnostics
    monkeypatch.setattr(diagnostics, "list_crashes", lambda **kw: [])


class TestFinalExpectCatchesTheMeasuredNearMiss:
    def test_final_expect_catches_a_near_identical_frame_ssim_would_pass(self, tmp_path, monkeypatch):
        s = _make_sim_session(tmp_path)
        rec_dir = tmp_path / "recordings" / "timer_check"
        monkeypatch.setattr(rec_mod, "recordings_root", lambda: tmp_path / "recordings")
        _write_recording(rec_dir, final_expect=["Timer Armed"])

        # Live frame is the OFF frame (bug: the app never armed), with no
        # "Timer Armed" text mark — but it's SSIM-near-identical to the
        # recorded ARMED post-frame. Precondition, asserted directly (per
        # the test plan), not assumed:
        off_path = tmp_path / "live_off.png"
        armed_path = rec_dir / "snapshots" / "001_post.png"
        _near_identical_frames(off_path, tmp_path / "_unused_armed.png")
        ssim_score = rec_mod._ssim_or_fallback(off_path, armed_path)
        assert ssim_score >= 0.85, (
            f"fixture must reproduce the near-miss shape (SSIM >= 0.85 despite "
            f"a real difference); got {ssim_score}, fixture is not calibrated"
        )

        _patch_live_frame(monkeypatch, s, off_path, marks=[])
        _patch_tap(monkeypatch)
        _patch_no_crashes(monkeypatch)

        result = rec_mod.replay("timer_check", s)

        # Both facts in the same test: SSIM alone would have passed...
        assert result["final_state"]["drifted"] is False, (
            "SSIM alone must score above threshold for this fixture to prove "
            "final_expect earns its keep, not just duplicate an SSIM failure"
        )
        assert result["final_state"]["similarity"] >= 0.85
        # ...and final_expect catches it anyway.
        assert result["ok"] is False
        assert result["halt_reason"] == "final_expect_failed"
        assert result["missing_expectations"] == ["Timer Armed"]

    def test_final_expect_passes_when_the_named_text_is_present(self, tmp_path, monkeypatch):
        s = _make_sim_session(tmp_path)
        rec_dir = tmp_path / "recordings" / "timer_check2"
        monkeypatch.setattr(rec_mod, "recordings_root", lambda: tmp_path / "recordings")
        _write_recording(rec_dir, final_expect=["Timer Armed"])

        armed_path = rec_dir / "snapshots" / "001_post.png"
        marks = [{"id": 1, "text": "Timer Armed 00:59", "bbox": [900, 100, 200, 60],
                  "center": [1000, 130], "confidence_band": "high"}]
        _patch_live_frame(monkeypatch, s, armed_path, marks=marks)
        _patch_tap(monkeypatch)
        _patch_no_crashes(monkeypatch)

        result = rec_mod.replay("timer_check2", s)

        assert result["ok"] is True
        assert result.get("halt_reason") is None
        assert result.get("final_expect_ok") is True

    def test_final_expect_is_optional_and_backward_compatible(self, tmp_path, monkeypatch):
        """A recording with no final_expect key replays exactly as today."""
        s = _make_sim_session(tmp_path)
        rec_dir = tmp_path / "recordings" / "no_expect"
        monkeypatch.setattr(rec_mod, "recordings_root", lambda: tmp_path / "recordings")
        _write_recording(rec_dir, final_expect=None)

        armed_path = rec_dir / "snapshots" / "001_post.png"
        _patch_live_frame(monkeypatch, s, armed_path, marks=[])
        _patch_tap(monkeypatch)
        _patch_no_crashes(monkeypatch)

        result = rec_mod.replay("no_expect", s)

        assert result["ok"] is True
        assert "missing_expectations" not in result
        assert "final_expect_ok" not in result

    def test_final_expect_multiple_assertions_all_must_hold(self, tmp_path, monkeypatch):
        s = _make_sim_session(tmp_path)
        rec_dir = tmp_path / "recordings" / "multi_expect"
        monkeypatch.setattr(rec_mod, "recordings_root", lambda: tmp_path / "recordings")
        _write_recording(rec_dir, final_expect=["Timer Armed", "Countdown Active"])

        armed_path = rec_dir / "snapshots" / "001_post.png"
        marks = [{"id": 1, "text": "Timer Armed", "bbox": [0, 0, 1, 1], "center": [0, 0],
                  "confidence_band": "high"}]
        _patch_live_frame(monkeypatch, s, armed_path, marks=marks)
        _patch_tap(monkeypatch)
        _patch_no_crashes(monkeypatch)

        result = rec_mod.replay("multi_expect", s)

        assert result["ok"] is False
        assert result["halt_reason"] == "final_expect_failed"
        assert result["missing_expectations"] == ["Countdown Active"]
