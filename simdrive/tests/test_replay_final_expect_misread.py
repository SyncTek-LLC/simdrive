"""FU-2026-070 — `final_expect` must tell an OCR misread apart from a genuine
replay failure.

Before this, a misread and a real assertion failure produced structurally
identical results (`halt_reason="final_expect_failed"` + a bare
`missing_expectations` list), so an engineer could not tell "the fix did not
work" from "the text recognizer stumbled". The state-contract code already
met the same OCR instability ("JAMES PATTERSON" read as "JAMES PATERSON" at
full confidence, see `_STATE_CONTRACT_SAMPLES`); these tests pin the
equivalent mitigation and the evidence a failure must now carry.
"""
from __future__ import annotations

from pathlib import Path

import yaml
from PIL import Image

from simdrive import recorder as rec_mod
from simdrive import server
from simdrive.som import Mark


def _make_sim_session(tmp_path: Path, sid: str = "fe-misread-sim"):
    from simdrive import session as ses_mod
    from simdrive.sim import Device

    ses_mod._SESSIONS.clear()
    device = Device(udid="SIM-FE-MISREAD", name="iPhone 17 Pro", os_version="26.1", state="active")
    workdir = tmp_path / "sessions" / sid
    workdir.mkdir(parents=True, exist_ok=True)
    s = ses_mod.Session(
        session_id=sid, device=device, workdir=workdir, target="simulator",
        last_screenshot_w=1206, last_screenshot_h=2622, last_marks=[],
    )
    ses_mod._SESSIONS[sid] = s
    return s


def _write_stepless_recording(rec_dir: Path, final_expect: list) -> None:
    """No steps, so the only observations replay takes are final_expect's own
    reads — each fake observation below maps 1:1 to one final_expect sample."""
    rec_dir.mkdir(parents=True, exist_ok=True)
    (rec_dir / "recording.yaml").write_text(yaml.safe_dump({
        "name": rec_dir.name, "created_at": 0.0, "target": "simulator",
        "device": "iPhone 17 Pro", "os_version": "26.1",
        "simdrive_version": "test", "steps": [], "final_expect": final_expect,
    }, sort_keys=False))


def _ocr_mark(text: str, raw_confidence: float = 0.98) -> Mark:
    return Mark(id=1, x=100, y=400, w=600, h=60, text=text,
                confidence=raw_confidence, raw_confidence=raw_confidence)


def _ax_mark(text: str) -> Mark:
    return Mark(id=1, x=100, y=400, w=600, h=60, text=text, confidence=1.0,
                source="ax", role="AXStaticText")


def _patch_observations(monkeypatch, tmp_path: Path, reads: list, *, method: str = "ocr"):
    """Each _observe_for_replay call returns the next entry of `reads` (the
    last one repeats), with its own screenshot on disk."""
    calls = {"n": 0}

    def _fake(session):
        i = min(calls["n"], len(reads) - 1)
        calls["n"] += 1
        path = tmp_path / f"live-{calls['n']}.png"
        Image.new("RGB", (1206, 2622), (200, 200, 200)).save(path)
        marks = reads[i]
        return {"screenshot_path": path, "marks_count": len(marks), "marks": list(marks),
                "screenshot_w": 1206, "screenshot_h": 2622, "resolution_method": method}

    monkeypatch.setattr(rec_mod, "_observe_for_replay", _fake, raising=False)
    return calls


def _replay(tmp_path, monkeypatch, name: str, final_expect: list):
    s = _make_sim_session(tmp_path)
    monkeypatch.setattr(rec_mod, "recordings_root", lambda: tmp_path / "recordings")
    _write_stepless_recording(tmp_path / "recordings" / name, final_expect)
    return rec_mod.replay(name, s)


class TestFinalExpectSeparatesMisreadFromRealFailure:
    def test_near_miss_ocr_read_is_reported_as_possible_misread(self, tmp_path, monkeypatch):
        """The measured instability: the screen shows JAMES PATTERSON, OCR
        reads JAMES PATERSON on both samples at 0.98. That must NOT be
        reported the same way as text that is genuinely absent."""
        _patch_observations(monkeypatch, tmp_path, [[_ocr_mark("JAMES PATERSON", 0.98)]])

        result = _replay(tmp_path, monkeypatch, "misread", ["JAMES PATTERSON"])

        assert result["ok"] is False, "an unconfirmed assertion must never pass silently"
        assert result["halt_reason"] == "final_expect_ocr_uncertain"
        fe = result["final_expect"]
        assert fe["failure_kind"] == "possible_ocr_misread"
        assert fe["confidence"] == "low"
        ev = fe["evidence"][0]
        assert ev["expected"] == "JAMES PATTERSON"
        assert ev["failure_kind"] == "possible_ocr_misread"
        assert ev["nearest_text"] == "JAMES PATERSON"
        assert ev["nearest_similarity"] >= 0.9
        assert ev["nearest_source"] == "ocr"
        assert ev["nearest_ocr_confidence"] == 0.98
        assert ev["ax_checked"] is False
        # Both reads were taken and both screenshots are cited for a human.
        assert fe["samples"] == 2
        assert len(fe["screenshot_paths"]) == 2
        assert all(Path(p).exists() for p in fe["screenshot_paths"])
        assert result["missing_expectations"] == ["JAMES PATTERSON"]

    def test_absent_text_on_ocr_is_a_genuine_failure(self, tmp_path, monkeypatch):
        _patch_observations(monkeypatch, tmp_path, [[_ocr_mark("Timer Off")]])

        result = _replay(tmp_path, monkeypatch, "absent", ["Timer Armed"])

        assert result["ok"] is False
        assert result["halt_reason"] == "final_expect_failed"
        fe = result["final_expect"]
        assert fe["failure_kind"] == "assertion_failed"
        # OCR-only, two reads agree there is nothing close: a real failure,
        # but not ground truth, so not "high".
        assert fe["confidence"] == "medium"
        assert fe["evidence"][0]["basis"] == "ocr"
        assert fe["evidence"][0]["nearest_text"] == "Timer Off"

    def test_ax_ground_truth_absence_is_high_confidence(self, tmp_path, monkeypatch):
        """AX labels are ground truth, not recognized text: an AX label that
        is merely close to the expectation is a real difference, not a misread."""
        _patch_observations(monkeypatch, tmp_path, [[_ax_mark("Timer Aimed")]], method="ax")

        result = _replay(tmp_path, monkeypatch, "ax_absent", ["Timer Armed"])

        assert result["halt_reason"] == "final_expect_failed"
        fe = result["final_expect"]
        assert fe["failure_kind"] == "assertion_failed"
        assert fe["confidence"] == "high"
        ev = fe["evidence"][0]
        assert ev["basis"] == "ax"
        assert ev["ax_checked"] is True
        assert ev["nearest_source"] == "ax"

    def test_misread_on_first_read_recovered_by_second_read_passes(self, tmp_path, monkeypatch):
        """Same two-sample mitigation as the state contract: text that one of
        two reads saw verbatim was on screen."""
        calls = _patch_observations(monkeypatch, tmp_path, [
            [_ocr_mark("JAMES PATERSON")],
            [_ocr_mark("JAMES PATTERSON")],
        ])

        result = _replay(tmp_path, monkeypatch, "recovered", ["JAMES PATTERSON"])

        assert result["ok"] is True
        assert result["final_expect_ok"] is True
        assert result["final_expect"]["recovered_on_resample"] == ["JAMES PATTERSON"]
        assert calls["n"] == 2

    def test_passing_first_read_costs_no_second_observation(self, tmp_path, monkeypatch):
        calls = _patch_observations(monkeypatch, tmp_path, [[_ocr_mark("Timer Armed 00:59")]])

        result = _replay(tmp_path, monkeypatch, "first_pass", ["Timer Armed"])

        assert result["ok"] is True
        assert calls["n"] == 1

    def test_mixed_failure_reports_the_genuine_one_as_the_headline(self, tmp_path, monkeypatch):
        """One misread-looking miss plus one genuinely absent string: the run
        is a real failure, and each item keeps its own classification."""
        _patch_observations(monkeypatch, tmp_path, [[_ocr_mark("JAMES PATERSON")]])

        result = _replay(tmp_path, monkeypatch, "mixed", ["JAMES PATTERSON", "Borrowed"])

        assert result["halt_reason"] == "final_expect_failed"
        kinds = {e["expected"]: e["failure_kind"] for e in result["final_expect"]["evidence"]}
        assert kinds == {"JAMES PATTERSON": "possible_ocr_misread", "Borrowed": "assertion_failed"}


class TestTextSimilarity:
    def test_window_match_inside_a_longer_read(self):
        assert rec_mod._text_similarity("PATTERSON", "by JAMES PATERSON (2024)") >= 0.9

    def test_unrelated_text_scores_low(self):
        assert rec_mod._text_similarity("Timer Armed", "Countdown Active") < 0.6

    def test_empty_inputs(self):
        assert rec_mod._text_similarity("", "anything") == 0.0
        assert rec_mod._text_similarity("x", "") == 0.0


class TestCliSummarySurfacesTheVerdict:
    def test_uncertain_final_expect_is_spelled_out(self):
        result = {
            "ok": False, "halt_reason": "final_expect_ocr_uncertain",
            "final_expect": {
                "ok": False, "failure_kind": "possible_ocr_misread", "confidence": "low",
                "screenshot_paths": ["/tmp/a.png", "/tmp/b.png"],
                "evidence": [{"expected": "JAMES PATTERSON", "failure_kind": "possible_ocr_misread",
                              "confidence": "low", "nearest_text": "JAMES PATERSON",
                              "nearest_similarity": 0.97, "nearest_ocr_confidence": 0.98}],
            },
        }
        text = server._format_replay_summary("misread", result)
        assert "FAIL" in text
        assert "final_expect_ocr_uncertain" in text
        assert "possible_ocr_misread" in text
        assert "'JAMES PATTERSON'" in text and "'JAMES PATERSON'" in text
        assert "/tmp/a.png" in text

    def test_crash_is_spelled_out(self):
        result = {
            "ok": False, "halt_reason": "crash_detected", "crash_after_step": 10,
            "crashes": [{"path": "/x/TestKitApp.ips", "exception": "309"}],
        }
        text = server._format_replay_summary("crashy", result)
        assert "crash_detected" in text
        assert "after step 10" in text
        assert "/x/TestKitApp.ips" in text

    def test_pass_is_one_line(self):
        assert server._format_replay_summary("ok", {"ok": True, "halt_reason": None}) == (
            "replay ok: PASS (halt_reason=None)"
        )
