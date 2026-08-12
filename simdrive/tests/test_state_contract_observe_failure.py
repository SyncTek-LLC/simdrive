"""A state contract that cannot be checked must not be reported as met.

`_verify_state_contract` reads the live screen to compare it against the
recording's `requires:` block. If that read fails — simulator wedged, device
unplugged, screenshot service dead — there are two possible behaviours:

  * treat the contract as satisfied and start replaying into an unknown screen;
  * treat it as broken and halt.

It halts, and these tests pin that. The distinction matters because the failure
mode of the first option is silent: every subsequent step compares against
whatever happens to be on screen, and a replay that verified nothing reports the
same `ok` as one that verified everything — the same class of defect this
module's outcome assertion exists to prevent.

When the contract has ALSO failed for a concrete reason (wrong app version, say),
the observe failure is demoted to a warning: the operator already has a specific
answer, and "we also couldn't take a screenshot" is noise next to it.
"""
from __future__ import annotations

from pathlib import Path

import yaml
from PIL import Image


def _make_sim_session(tmp_path: Path, *, app_bundle_id: str = "org.example.app"):
    from simdrive import session as ses_mod
    from simdrive.sim import Device

    ses_mod._SESSIONS.clear()
    workdir = tmp_path / "sessions" / "contract-observe"
    workdir.mkdir(parents=True, exist_ok=True)
    s = ses_mod.Session(
        session_id="contract-observe",
        device=Device(udid="SIM-CONTRACT-OBS", name="iPhone 17 Pro",
                      os_version="26.1", state="active"),
        workdir=workdir,
        target="simulator",
        app_bundle_id=app_bundle_id,
        last_screenshot_w=1206,
        last_screenshot_h=2622,
    )
    ses_mod._SESSIONS[s.session_id] = s
    return s


def _write_recording(rec_dir: Path, *, app_version: str = "493") -> None:
    snaps = rec_dir / "snapshots"
    snaps.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (1206, 2622), (210, 210, 210)).save(snaps / "001_pre.png")
    Image.new("RGB", (1206, 2622), (210, 210, 210)).save(snaps / "001_post.png")
    (rec_dir / "recording.yaml").write_text(yaml.safe_dump({
        "name": rec_dir.name,
        "created_at": 0.0,
        "target": "simulator",
        "device": "iPhone 17 Pro",
        "os_version": "26.1",
        "app_bundle_id": "org.example.app",
        "simdrive_version": "test",
        "requires": {
            "target": "simulator",
            "app": {"bundle_id": "org.example.app", "version": app_version,
                    "version_match": "exact"},
            "sim": {"device": "iPhone 17 Pro", "ios_version": "26.1"},
            "initial_state": {
                "foreground": True,
                "text_subset_required": ["Private Down Under"],
                "text_subset_forbidden": [],
                "primary_button_label": None,
            },
        },
        "steps": [{
            "id": 1,
            "action": "tap",
            "args": {"x": 300, "y": 1900, "screenshot_w": 1206, "screenshot_h": 2622},
            "pre_screenshot": "snapshots/001_pre.png",
            "post_screenshot": "snapshots/001_post.png",
            "captured_at": 1.0,
        }],
    }, sort_keys=False))


def test_replay_halts_when_the_live_state_cannot_be_observed(tmp_path, monkeypatch):
    """No screenshot means no verification — which must fail, not pass."""
    from simdrive import act, recorder

    monkeypatch.setenv("SIMDRIVE_HOME", str(tmp_path))
    _write_recording(recorder.recordings_root() / "blind")

    def _boom(session, workdir):
        raise RuntimeError("simctl screenshot failed: device not booted")

    monkeypatch.setattr(recorder, "_observe_live_marks", _boom, raising=False)
    monkeypatch.setattr(recorder, "_current_app_version", lambda s: "493",
                        raising=False)
    taps: list = []
    monkeypatch.setattr(act, "tap", lambda *a, **kw: taps.append(a))

    result = recorder.replay("blind", _make_sim_session(tmp_path), on_drift="halt")

    assert result["ok"] is False
    assert result["halt_reason"] == "state_contract_mismatch"
    assert result["halted_at"] == 0, "nothing may execute against an unverified screen"
    assert taps == [], "no step may be dispatched"
    assert any("observe failed" in r for r in result["reasons"]), result["reasons"]
    assert "reachable" in result["remedy"]


def test_an_observe_failure_is_advisory_when_the_contract_already_failed(tmp_path, monkeypatch):
    """A concrete mismatch is the useful answer; the screenshot failure rides
    along as a warning rather than displacing it."""
    from simdrive import act, recorder

    monkeypatch.setenv("SIMDRIVE_HOME", str(tmp_path))
    # Contract demands app version 493; the live app reports 500.
    _write_recording(recorder.recordings_root() / "blind-and-wrong", app_version="493")

    def _boom(session, workdir):
        raise RuntimeError("simctl screenshot failed: device not booted")

    monkeypatch.setattr(recorder, "_observe_live_marks", _boom, raising=False)
    monkeypatch.setattr(recorder, "_current_app_version", lambda s: "500",
                        raising=False)
    monkeypatch.setattr(act, "tap", lambda *a, **kw: None)

    result = recorder.replay("blind-and-wrong", _make_sim_session(tmp_path),
                             on_drift="halt")

    assert result["ok"] is False
    assert result["halt_reason"] == "state_contract_mismatch"
    reasons = " ".join(result["reasons"])
    assert "version" in reasons.lower(), f"the real cause must lead: {result['reasons']}"
    # The observe failure is still surfaced, just not as the headline.
    warning = result.get("_simdrive_warning", "")
    assert "observe failed" in warning or any(
        "observe failed" in w for w in (result.get("warnings") or [])
    ), result


def test_a_contract_that_can_be_checked_still_passes(tmp_path, monkeypatch):
    """Guard against fixing the blind case by failing everything."""
    from simdrive import act, recorder
    from simdrive.som import Mark

    monkeypatch.setenv("SIMDRIVE_HOME", str(tmp_path))
    _write_recording(recorder.recordings_root() / "sighted")

    monkeypatch.setattr(
        recorder, "_observe_live_marks",
        lambda session, workdir: [Mark(id=1, x=100, y=200, w=400, h=60,
                                       text="Private Down Under", confidence=1.0)],
        raising=False,
    )
    monkeypatch.setattr(recorder, "_current_app_version", lambda s: "493",
                        raising=False)
    monkeypatch.setattr(act, "tap", lambda *a, **kw: None)

    def _fake_replay_obs(session):
        out = Path(session.workdir) / "replay"
        out.mkdir(parents=True, exist_ok=True)
        p = out / "live.png"
        Image.new("RGB", (1206, 2622), (210, 210, 210)).save(p)
        return {"screenshot_path": p, "marks_count": 1, "marks": [],
                "screenshot_w": 1206, "screenshot_h": 2622}

    monkeypatch.setattr(recorder, "_observe_for_replay", _fake_replay_obs,
                        raising=False)

    result = recorder.replay("sighted", _make_sim_session(tmp_path), on_drift="halt")

    assert result["ok"] is True, result
    assert result["halt_reason"] is None
