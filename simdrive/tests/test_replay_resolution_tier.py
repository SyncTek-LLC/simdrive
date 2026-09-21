"""INIT-2026-641 test plan section 3a (CEO board review condition) — per-step
resolution-tier visibility in replay output.

The guarantee this closes: a multi-step replay that quietly slides from AX to
OCR partway through, with no crash and no outcome failure (OCR still finds
what it needs), must still let a human reading the replay's returned output —
without re-running the sequence live, without reading source — tell which
tier produced each step's marks. A single top-level "ocr was used somewhere"
field cannot answer "which step"; this must be per-step.

Uses the same ``_write_recording`` / ``_observe_for_replay`` monkeypatch
convention as ``test_replay_outcome_verification.py``.
"""
from __future__ import annotations

from pathlib import Path

import yaml
from PIL import Image

GREY = (210, 210, 210)


def _make_sim_session(tmp_path: Path, sid: str = "restier-sim"):
    from simdrive import session as ses_mod
    from simdrive.sim import Device

    ses_mod._SESSIONS.clear()
    device = Device(udid="SIM-RESTIER-UDID", name="iPhone 17 Pro",
                    os_version="26.1", state="active")
    workdir = tmp_path / "sessions" / sid
    workdir.mkdir(parents=True, exist_ok=True)
    s = ses_mod.Session(
        session_id=sid, device=device, workdir=workdir, target="simulator",
        last_screenshot_w=1206, last_screenshot_h=2622, last_marks=[],
    )
    ses_mod._SESSIONS[sid] = s
    return s


def _write_recording(rec_dir: Path, *, steps: int = 5) -> None:
    """A recording where every pre/post frame is identical GREY, so no SSIM
    or outcome drift ever fires — isolating this test to resolution-tier
    visibility, not the (separately tested) drift/outcome machinery.
    """
    snaps = rec_dir / "snapshots"
    snaps.mkdir(parents=True, exist_ok=True)
    step_list = []
    for i in range(1, steps + 1):
        Image.new("RGB", (1206, 2622), GREY).save(snaps / f"{i:03d}_pre.png")
        Image.new("RGB", (1206, 2622), GREY).save(snaps / f"{i:03d}_post.png")
        step_list.append({
            "id": i,
            "action": "tap",
            "args": {"x": 300, "y": 1900, "screenshot_w": 1206, "screenshot_h": 2622},
            "pre_screenshot": f"snapshots/{i:03d}_pre.png",
            "post_screenshot": f"snapshots/{i:03d}_post.png",
            "captured_at": float(i),
        })
    (rec_dir / "recording.yaml").write_text(yaml.safe_dump({
        "name": rec_dir.name,
        "created_at": 0.0,
        "target": "simulator",
        "device": "iPhone 17 Pro",
        "os_version": "26.1",
        "app_bundle_id": "org.example.app",
        "simdrive_version": "test",
        "steps": step_list,
    }, sort_keys=False))


def _patch_replay_resolution_sequence(monkeypatch, tiers: list[str]):
    """Script `_observe_for_replay` to return GREY frames tagged with a
    scripted `resolution_method` per call, one entry from `tiers` per call
    (repeats the last entry once exhausted).
    """
    from simdrive import recorder as rec_mod

    seen: list[str] = []

    def _fake(session):
        tier = tiers[len(seen)] if len(seen) < len(tiers) else tiers[-1]
        seen.append(tier)
        out_dir = Path(session.workdir) / "replay"
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / f"live_{len(seen):03d}.png"
        Image.new("RGB", (1206, 2622), GREY).save(path)
        return {
            "screenshot_path": path, "marks_count": 0, "marks": [],
            "screenshot_w": 1206, "screenshot_h": 2622,
            "resolution_method": tier,
        }

    monkeypatch.setattr(rec_mod, "_observe_for_replay", _fake, raising=False)
    return seen


def _patch_tap(monkeypatch):
    from simdrive import act
    monkeypatch.setattr(act, "tap", lambda *a, **kw: None)


def test_replay_output_logs_resolution_tier_per_step(tmp_path, monkeypatch):
    """AX for steps 1-2, then a quiet slide to OCR for steps 3-5 — no crash,
    no outcome failure (frames never change). The per-step record must name
    the tier for EVERY step, including the clean AX ones, matching what that
    step's observe call actually returned — not a single summary field.
    """
    from simdrive import recorder

    monkeypatch.setenv("SIMDRIVE_HOME", str(tmp_path))
    _write_recording(recorder.recordings_root() / "tier-slide", steps=5)
    _patch_replay_resolution_sequence(
        monkeypatch, ["ax", "ax", "ocr", "ocr", "ocr"],
    )
    _patch_tap(monkeypatch)

    result = recorder.replay(
        "tier-slide", _make_sim_session(tmp_path),
        on_drift="halt", halt_on_state_mismatch=False,
    )

    assert result["ok"] is True, result
    steps = result["steps"]
    assert len(steps) == 5
    # Present on every step, not only degraded ones.
    assert all("resolution_method" in st for st in steps)
    observed_tiers = [st["resolution_method"] for st in steps]
    assert observed_tiers == ["ax", "ax", "ocr", "ocr", "ocr"], observed_tiers


def test_replay_output_resolution_tier_survives_a_reactivate_cycle(tmp_path, monkeypatch):
    """A step that hit the reactivate-and-retry path must report the tier it
    actually resolved on after recovery ('ax_reactivated'), distinguishable
    both from a clean 'ax' step and from a step where the retry budget was
    genuinely exhausted ('ocr').
    """
    from simdrive import recorder

    monkeypatch.setenv("SIMDRIVE_HOME", str(tmp_path))
    _write_recording(recorder.recordings_root() / "tier-reactivate", steps=3)
    _patch_replay_resolution_sequence(
        monkeypatch, ["ax", "ax_reactivated", "ocr"],
    )
    _patch_tap(monkeypatch)

    result = recorder.replay(
        "tier-reactivate", _make_sim_session(tmp_path),
        on_drift="halt", halt_on_state_mismatch=False,
    )

    assert result["ok"] is True, result
    tiers = [st["resolution_method"] for st in result["steps"]]
    assert tiers == ["ax", "ax_reactivated", "ocr"]
    # The reactivated step is distinguishable from both its clean-AX
    # neighbor and the fully-exhausted-to-OCR step.
    assert tiers[0] != tiers[1] != tiers[2]
