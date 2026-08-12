"""Replay must verify what a recording *did*, not just what it walked past.

Three defects, each reproduced here before being fixed:

1. **Outcome was never asserted.** Replay compared every step's PRE-state and
   returned ok after dispatching the final action without ever looking at the
   result. Any recording whose payload is its last step therefore passed
   unconditionally. Found in the field: a two-step sleep-timer recording
   returned ``ok=True`` at SSIM 0.995 while the menu tap had been swallowed and
   no timer was ever armed.

2. **Swallowed taps.** A synthetic tap on a freshly-presented SwiftUI menu row
   is often ignored by the UI while the tool reports success. Capture hides it
   (agents observe between actions, which costs real time); replay dispatches
   back-to-back and hits it repeatedly.

3. **Unstable OCR in state contracts.** Text rendered inside images (book cover
   art) re-OCRs differently between consecutive reads at full confidence, so a
   contract built from one sample rejects genuinely correct state at replay.

Each test below fails against the pre-fix engine for the stated reason.
"""
from __future__ import annotations

from pathlib import Path

import yaml
from PIL import Image

GREY = (210, 210, 210)
DARK = (12, 12, 12)


# ─── Helpers ───────────────────────────────────────────────────────────────


def _make_sim_session(tmp_path: Path, sid: str = "outcome-sim"):
    from simdrive import session as ses_mod
    from simdrive.sim import Device

    ses_mod._SESSIONS.clear()
    device = Device(udid="SIM-OUTCOME-UDID", name="iPhone 17 Pro",
                    os_version="26.1", state="active")
    workdir = tmp_path / "sessions" / sid
    workdir.mkdir(parents=True, exist_ok=True)
    s = ses_mod.Session(
        session_id=sid,
        device=device,
        workdir=workdir,
        target="simulator",
        last_screenshot_w=1206,
        last_screenshot_h=2622,
        last_marks=[],
    )
    ses_mod._SESSIONS[sid] = s
    return s


def _write_recording(rec_dir: Path, *, steps: int = 2,
                     final_post_colour=GREY,
                     settle_ms: int | None = None) -> None:
    """Recording whose pre-frames are all GREY; the final post-frame is
    ``final_post_colour`` — that is the state the capture ended in."""
    snaps = rec_dir / "snapshots"
    snaps.mkdir(parents=True, exist_ok=True)
    step_list = []
    for i in range(1, steps + 1):
        Image.new("RGB", (1206, 2622), GREY).save(snaps / f"{i:03d}_pre.png")
        post_colour = final_post_colour if i == steps else GREY
        Image.new("RGB", (1206, 2622), post_colour).save(snaps / f"{i:03d}_post.png")
        args: dict = {"x": 300, "y": 1900, "screenshot_w": 1206, "screenshot_h": 2622}
        if settle_ms is not None:
            args["settle_ms"] = settle_ms
        step_list.append({
            "id": i,
            "action": "tap",
            "args": args,
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


def _patch_replay_frames(monkeypatch, colours: list, *, loop_last: bool = True):
    """Feed replay a scripted sequence of live frames.

    ``colours`` is consumed one entry per ``_observe_for_replay`` call; when it
    runs out the last colour repeats (so a test only has to describe the frames
    it cares about).
    """
    from simdrive import recorder as rec_mod

    seen: list = []

    def _fake(session):
        colour = colours[len(seen)] if len(seen) < len(colours) else (
            colours[-1] if loop_last else GREY
        )
        seen.append(colour)
        out_dir = Path(session.workdir) / "replay"
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / f"live_{len(seen):03d}.png"
        Image.new("RGB", (1206, 2622), colour).save(path)
        return {"screenshot_path": path, "marks_count": 0, "marks": [],
                "screenshot_w": 1206, "screenshot_h": 2622}

    monkeypatch.setattr(rec_mod, "_observe_for_replay", _fake, raising=False)
    return seen


def _patch_tap(monkeypatch):
    from simdrive import act
    calls: list = []
    monkeypatch.setattr(act, "tap", lambda *a, **kw: calls.append((a, kw)))
    return calls


# ─── 1. Outcome assertion ──────────────────────────────────────────────────


def test_replay_halts_when_the_final_action_had_no_effect(tmp_path, monkeypatch):
    """The false-green regression.

    Every step's PRE-state matches, so the per-step checks are all green and
    every step reports executed — which is exactly the signal the old engine
    returned ok on. But the screen after the final tap is still the pre-tap
    screen: the action did nothing. Replay must halt on the outcome.
    """
    from simdrive import recorder

    monkeypatch.setenv("SIMDRIVE_HOME", str(tmp_path))
    _write_recording(recorder.recordings_root() / "noeffect", steps=2,
                     final_post_colour=DARK)
    # Live screen is GREY forever: matches every pre-state, never becomes the
    # DARK state the capture ended in.
    _patch_replay_frames(monkeypatch, [GREY])
    _patch_tap(monkeypatch)

    result = recorder.replay("noeffect", _make_sim_session(tmp_path),
                             on_drift="halt", halt_on_state_mismatch=False)

    assert result["ok"] is False, f"final-state mismatch must fail the replay: {result}"
    assert result["halt_reason"] == "outcome_drift", result["halt_reason"]
    assert result["final_state"]["drifted"] is True
    assert result["final_state"]["similarity"] < 0.85
    # The trap: per-step signal looked perfect throughout.
    assert all(st["executed"] for st in result["steps"])
    assert not any(st["drifted"] for st in result["steps"])
    # Operators need both frames to triage.
    assert Path(result["expected_screenshot_path"]).exists()
    assert Path(result["actual_screenshot_path"]).exists()


def test_replay_passes_when_the_final_state_matches_the_capture(tmp_path, monkeypatch):
    """The outcome check must not reject a replay that genuinely worked."""
    from simdrive import recorder

    monkeypatch.setenv("SIMDRIVE_HOME", str(tmp_path))
    _write_recording(recorder.recordings_root() / "works", steps=2,
                     final_post_colour=DARK)
    # Frame 1 + 2: each step's pre-state compare. Frame 3: the outcome check,
    # by which point the screen is the DARK state the capture ended in.
    _patch_replay_frames(monkeypatch, [GREY, GREY, DARK])
    _patch_tap(monkeypatch)

    result = recorder.replay("works", _make_sim_session(tmp_path),
                             on_drift="halt", halt_on_state_mismatch=False)

    assert result["ok"] is True, result
    assert result["final_state"]["drifted"] is False
    assert result["final_state"]["similarity"] >= 0.85


def test_outcome_drift_only_warns_when_on_drift_is_not_halt(tmp_path, monkeypatch):
    """on_drift="warn" keeps the run green but still reports the mismatch, so a
    smoke-tier journey surfaces the outcome without failing the board."""
    from simdrive import recorder

    monkeypatch.setenv("SIMDRIVE_HOME", str(tmp_path))
    _write_recording(recorder.recordings_root() / "warned", steps=1,
                     final_post_colour=DARK)
    _patch_replay_frames(monkeypatch, [GREY])
    _patch_tap(monkeypatch)

    result = recorder.replay("warned", _make_sim_session(tmp_path),
                             on_drift="warn", halt_on_state_mismatch=False)

    assert result["ok"] is True
    assert result["final_state"]["drifted"] is True
    assert any(e.get("kind") == "outcome_drift" for e in result["drift_events"])


def test_recording_without_post_screenshots_still_replays(tmp_path, monkeypatch):
    """Backward compat: a recording with no post-frame on disk degrades to the
    old behaviour rather than failing."""
    from simdrive import recorder

    monkeypatch.setenv("SIMDRIVE_HOME", str(tmp_path))
    rec_dir = recorder.recordings_root() / "legacy"
    _write_recording(rec_dir, steps=1)
    (rec_dir / "snapshots" / "001_post.png").unlink()
    _patch_replay_frames(monkeypatch, [GREY])
    _patch_tap(monkeypatch)

    result = recorder.replay("legacy", _make_sim_session(tmp_path),
                             on_drift="halt", halt_on_state_mismatch=False)

    assert result["ok"] is True
    assert "final_state" not in result


# ─── 2. Swallowed taps ─────────────────────────────────────────────────────


def test_a_tap_that_changed_nothing_is_dispatched_again_when_asked(tmp_path, monkeypatch):
    """The swallowed-menu-row case: screen identical before and after the tap."""
    from simdrive import recorder

    monkeypatch.setenv("SIMDRIVE_HOME", str(tmp_path))
    # The capture DID change the screen at this step (post is DARK), so replay
    # has a documented effect to chase.
    _write_recording(recorder.recordings_root() / "swallowed", steps=1,
                     final_post_colour=DARK)
    _patch_replay_frames(monkeypatch, [GREY])  # live never changes
    taps = _patch_tap(monkeypatch)
    monkeypatch.setattr(recorder.time, "sleep", lambda s: None)

    result = recorder.replay("swallowed", _make_sim_session(tmp_path),
                             on_drift="warn", halt_on_state_mismatch=False,
                             retry_noop_taps=True)

    assert len(taps) == 2, f"expected one retry, got {len(taps)} dispatches"
    assert result["noop_retries"], "the retry must be reported, not silent"
    assert result["noop_retries"][0]["step_id"] == 1
    assert result["steps"][0]["noop_retry"]["reason"] == "screen_unchanged_after_tap"


def test_taps_are_never_repeated_unless_explicitly_enabled(tmp_path, monkeypatch):
    """Default must be no retry.

    A tap whose effect is a network round-trip — Borrow, Return, Sign in — looks
    identical to a swallowed one for as long as the request is in flight.
    Re-sending it by default would double-submit, so the swallowed-tap recovery
    stays opt-in and the outcome assertion is what catches the failure.
    """
    from simdrive import recorder

    monkeypatch.setenv("SIMDRIVE_HOME", str(tmp_path))
    _write_recording(recorder.recordings_root() / "no-double-submit", steps=1,
                     final_post_colour=DARK)
    _patch_replay_frames(monkeypatch, [GREY])  # the tap appears to do nothing
    taps = _patch_tap(monkeypatch)

    result = recorder.replay("no-double-submit", _make_sim_session(tmp_path),
                             on_drift="halt", halt_on_state_mismatch=False)

    assert len(taps) == 1, "a tap must not be repeated without opting in"
    assert "noop_retries" not in result
    # ...and the swallowed tap still cannot pass silently.
    assert result["ok"] is False
    assert result["halt_reason"] == "outcome_drift"


def test_a_recording_can_opt_into_retries_itself(tmp_path, monkeypatch):
    """Menu-driven journeys enable it in the recording, not at every call site."""
    from simdrive import recorder

    monkeypatch.setenv("SIMDRIVE_HOME", str(tmp_path))
    rec_dir = recorder.recordings_root() / "policy-opt-in"
    _write_recording(rec_dir, steps=1, final_post_colour=DARK)
    payload = yaml.safe_load((rec_dir / "recording.yaml").read_text())
    payload["replay_policy"] = {"retry_noop_taps": True}
    (rec_dir / "recording.yaml").write_text(yaml.safe_dump(payload, sort_keys=False))
    _patch_replay_frames(monkeypatch, [GREY])
    taps = _patch_tap(monkeypatch)
    monkeypatch.setattr(recorder.time, "sleep", lambda s: None)

    recorder.replay("policy-opt-in", _make_sim_session(tmp_path),
                    on_drift="warn", halt_on_state_mismatch=False)

    assert len(taps) == 2, "replay_policy in the recording must enable the retry"


def test_a_slow_action_is_given_grace_before_being_repeated(tmp_path, monkeypatch):
    """A tap still in flight must not be re-sent — that is the double-submit."""
    from simdrive import recorder

    monkeypatch.setenv("SIMDRIVE_HOME", str(tmp_path))
    _write_recording(recorder.recordings_root() / "slow", steps=1,
                     final_post_colour=DARK)
    # Unchanged right after the tap, changed once the grace period elapses.
    _patch_replay_frames(monkeypatch, [GREY, GREY, DARK])
    taps = _patch_tap(monkeypatch)
    slept: list = []
    monkeypatch.setattr(recorder.time, "sleep", lambda s: slept.append(s))

    recorder.replay("slow", _make_sim_session(tmp_path),
                    on_drift="warn", halt_on_state_mismatch=False,
                    retry_noop_taps=True)

    assert recorder._NOOP_TAP_GRACE_SEC in slept, "the grace re-check must happen"
    assert len(taps) == 1, "an action that landed during grace must not be repeated"


def test_a_step_that_changed_nothing_at_capture_is_never_retried(tmp_path, monkeypatch):
    """No recorded effect means there is nothing missing to chase."""
    from simdrive import recorder

    monkeypatch.setenv("SIMDRIVE_HOME", str(tmp_path))
    # pre == post in the recording: the capture saw no change either.
    _write_recording(recorder.recordings_root() / "no-effect-recorded", steps=1,
                     final_post_colour=GREY)
    _patch_replay_frames(monkeypatch, [GREY])
    taps = _patch_tap(monkeypatch)

    recorder.replay("no-effect-recorded", _make_sim_session(tmp_path),
                    on_drift="warn", halt_on_state_mismatch=False,
                    retry_noop_taps=True)

    assert len(taps) == 1, "nothing changed at capture, so nothing is missing"


def test_a_tap_that_changed_the_screen_is_not_dispatched_again(tmp_path, monkeypatch):
    """Retry must be confined to provable no-ops — a working tap fires once."""
    from simdrive import recorder

    monkeypatch.setenv("SIMDRIVE_HOME", str(tmp_path))
    _write_recording(recorder.recordings_root() / "worked", steps=1,
                     final_post_colour=DARK)
    # frame 1: pre-state compare (GREY) → frame 2: post-tap check (DARK, changed)
    _patch_replay_frames(monkeypatch, [GREY, DARK])
    taps = _patch_tap(monkeypatch)

    result = recorder.replay("worked", _make_sim_session(tmp_path),
                             on_drift="halt", halt_on_state_mismatch=False,
                             retry_noop_taps=True)

    assert len(taps) == 1, f"a tap that worked must not be repeated: {len(taps)}"
    assert "noop_retries" not in result
    assert result["ok"] is True


def test_non_tap_actions_are_never_auto_retried(tmp_path, monkeypatch):
    """Re-sending a swipe or a keystroke is not obviously idempotent, so the
    retry is deliberately tap-only."""
    from simdrive import act, recorder

    monkeypatch.setenv("SIMDRIVE_HOME", str(tmp_path))
    rec_dir = recorder.recordings_root() / "swipe-noop"
    _write_recording(rec_dir, steps=1)
    payload = yaml.safe_load((rec_dir / "recording.yaml").read_text())
    payload["steps"][0]["action"] = "swipe"
    payload["steps"][0]["args"] = {"x1": 100, "y1": 900, "x2": 100, "y2": 300,
                                   "screenshot_w": 1206, "screenshot_h": 2622}
    (rec_dir / "recording.yaml").write_text(yaml.safe_dump(payload, sort_keys=False))

    swipes: list = []
    monkeypatch.setattr(act, "swipe", lambda *a, **kw: swipes.append((a, kw)))
    _patch_replay_frames(monkeypatch, [GREY])  # unchanged screen

    recorder.replay("swipe-noop", _make_sim_session(tmp_path),
                    on_drift="warn", halt_on_state_mismatch=False,
                    retry_noop_taps=True)

    assert len(swipes) == 1, "swipes must not be auto-retried"


# ─── 3. Pacing ─────────────────────────────────────────────────────────────


def test_replay_honours_the_recorded_settle(tmp_path, monkeypatch):
    """Replay paced faster than capture is what surfaces the swallowed-tap race;
    the recorded settle has to be replayed too."""
    from simdrive import recorder

    monkeypatch.setenv("SIMDRIVE_HOME", str(tmp_path))
    _write_recording(recorder.recordings_root() / "paced", steps=2, settle_ms=250)
    _patch_replay_frames(monkeypatch, [GREY])
    _patch_tap(monkeypatch)

    slept: list = []
    monkeypatch.setattr(recorder.time, "sleep", lambda s: slept.append(s))

    recorder.replay("paced", _make_sim_session(tmp_path),
                    on_drift="halt", halt_on_state_mismatch=False)

    assert slept.count(0.25) >= 2, f"each step's settle must be honoured: {slept}"


def test_a_malformed_settle_is_ignored_rather_than_crashing_the_replay(tmp_path, monkeypatch):
    """A hand-edited recording must not be able to take the engine down."""
    from simdrive import recorder

    monkeypatch.setenv("SIMDRIVE_HOME", str(tmp_path))
    rec_dir = recorder.recordings_root() / "bad-settle"
    _write_recording(rec_dir, steps=1)
    payload = yaml.safe_load((rec_dir / "recording.yaml").read_text())
    payload["steps"][0]["args"]["settle_ms"] = "soon"
    (rec_dir / "recording.yaml").write_text(yaml.safe_dump(payload, sort_keys=False))
    _patch_replay_frames(monkeypatch, [GREY])
    _patch_tap(monkeypatch)
    slept: list = []
    monkeypatch.setattr(recorder.time, "sleep", lambda s: slept.append(s))

    result = recorder.replay("bad-settle", _make_sim_session(tmp_path),
                             on_drift="warn", halt_on_state_mismatch=False)

    assert result["steps"][0]["executed"] is True
    assert slept == []


def test_retry_is_skipped_when_the_recorded_frames_are_gone(tmp_path, monkeypatch):
    """Without the capture's frames there is no way to know an effect is missing,
    so the retry must decline rather than guess."""
    from simdrive import recorder

    monkeypatch.setenv("SIMDRIVE_HOME", str(tmp_path))
    rec_dir = recorder.recordings_root() / "frames-gone"
    _write_recording(rec_dir, steps=1, final_post_colour=DARK)
    (rec_dir / "snapshots" / "001_post.png").unlink()
    _patch_replay_frames(monkeypatch, [GREY])
    taps = _patch_tap(monkeypatch)

    recorder.replay("frames-gone", _make_sim_session(tmp_path),
                    on_drift="warn", halt_on_state_mismatch=False,
                    retry_noop_taps=True)

    assert len(taps) == 1, "no recorded oracle means no retry"


def test_retries_are_reported_even_when_a_later_step_blows_up(tmp_path, monkeypatch):
    """Whoever triages the crash needs to know a tap had already been repeated."""
    from simdrive import act, recorder

    monkeypatch.setenv("SIMDRIVE_HOME", str(tmp_path))
    rec_dir = recorder.recordings_root() / "retry-then-error"
    _write_recording(rec_dir, steps=2, final_post_colour=DARK)
    # Give step 1 a recorded effect so the retry has something to chase.
    Image.new("RGB", (1206, 2622), DARK).save(rec_dir / "snapshots" / "001_post.png")
    _patch_replay_frames(monkeypatch, [GREY])
    monkeypatch.setattr(recorder.time, "sleep", lambda s: None)

    calls: list = []

    def _tap(*a, **kw):
        calls.append((a, kw))
        if len(calls) > 2:            # step 1 taps twice (tap + retry), step 2 dies
            raise RuntimeError("HID went away")

    monkeypatch.setattr(act, "tap", _tap)

    result = recorder.replay("retry-then-error", _make_sim_session(tmp_path),
                             on_drift="warn", halt_on_state_mismatch=False,
                             retry_noop_taps=True)

    assert result["halt_reason"] == "execute_error"
    assert result["noop_retries"], "a retry that already happened must still be reported"
    assert result["noop_retries"][0]["step_id"] == 1


def test_recordings_without_a_settle_do_not_sleep(tmp_path, monkeypatch):
    """Recordings captured before settle_ms was persisted keep their old cadence."""
    from simdrive import recorder

    monkeypatch.setenv("SIMDRIVE_HOME", str(tmp_path))
    _write_recording(recorder.recordings_root() / "unpaced", steps=2)
    _patch_replay_frames(monkeypatch, [GREY])
    _patch_tap(monkeypatch)

    slept: list = []
    monkeypatch.setattr(recorder.time, "sleep", lambda s: slept.append(s))

    recorder.replay("unpaced", _make_sim_session(tmp_path),
                    on_drift="halt", halt_on_state_mismatch=False)

    assert slept == [], f"nothing to honour, so nothing should sleep: {slept}"
