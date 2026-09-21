"""Dogfood gap 3 — animation was discarded rather than measured.

simdrive had no frame capture at all: record_start stores per-step pre/post
stills, and the recorder deliberately applies hysteresis so a transient
animation cannot trip replay drift. Correct for replay determinism, and it left
four campaign targets unsettleable — a Listen/Cancel button flicker, a
mini-to-full player morph, a skeleton that may never resolve, and a five-minute
freeze that had to be proved by hand with `ps` CPU% and log silence.

The governing constraint is that raw frames must NOT go back to the agent: 120
screenshots is an unreadable, budget-destroying payload. So capture_motion
returns quantified motion — a delta series, a state count, a transition count, a
settling time — plus a handful of representative frames.

Everything here tests the analysis, which is pure: frames are synthetic PNGs on
disk. Capture itself (simctl recordVideo + ffmpeg) is exercised by the live
tests at the bottom, which are marked and skipped without a booted simulator.
"""
from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from simdrive import motion


# ── synthetic frame helpers ─────────────────────────────────────────────────


def _frame(path: Path, blocks: list[tuple[tuple[int, int, int, int], int]],
           size: tuple[int, int] = (200, 400), bg: int = 255) -> Path:
    """Write a grayscale PNG: `bg` background with `blocks` of ((x,y,w,h), level)."""
    from PIL import Image, ImageDraw
    im = Image.new("L", size, bg)
    draw = ImageDraw.Draw(im)
    for (x, y, w, h), level in blocks:
        draw.rectangle([x, y, x + w, y + h], fill=level)
    path.parent.mkdir(parents=True, exist_ok=True)
    im.save(path)
    return path


def _sequence(tmp_path: Path, pattern: list[str]) -> list[Path]:
    """Build a frame sequence from a pattern of single-letter state names.

    Each distinct letter renders a visually distinct frame. The varying block is
    deliberately large (~48% of the frame) so every delta sits well clear of the
    motion threshold — a fixture parked on the threshold tests the fixture, not
    the code.
    """
    levels = {"A": 0, "B": 128, "C": 200, "D": 64}
    out = []
    for i, ch in enumerate(pattern):
        out.append(_frame(tmp_path / f"f_{i:04d}.png", [((20, 20, 160, 240), levels[ch])]))
    return out


def _times(n: int, fps: float) -> list[float]:
    return [i * 1000.0 / fps for i in range(n)]


# ── delta series ────────────────────────────────────────────────────────────


def test_identical_frames_produce_zero_delta(tmp_path):
    frames = _sequence(tmp_path, ["A", "A", "A"])
    res = motion.analyze_frames(frames, _times(3, 10))
    assert res["delta_series"] == [0.0, 0.0]
    assert res["max_delta"] == 0.0


def test_delta_series_has_one_entry_fewer_than_frames(tmp_path):
    """Each entry is a difference *against the previous frame*."""
    frames = _sequence(tmp_path, ["A", "B", "A", "B"])
    res = motion.analyze_frames(frames, _times(4, 10))
    assert len(res["delta_series"]) == 3


def test_delta_is_normalised_and_larger_for_a_bigger_change(tmp_path):
    small = [_frame(tmp_path / "s0.png", [((20, 20, 10, 10), 0)]),
             _frame(tmp_path / "s1.png", [((20, 20, 10, 10), 255)])]
    big = [_frame(tmp_path / "b0.png", [((0, 0, 200, 400), 0)]),
           _frame(tmp_path / "b1.png", [((0, 0, 200, 400), 255)])]
    d_small = motion.analyze_frames(small, _times(2, 10))["delta_series"][0]
    d_big = motion.analyze_frames(big, _times(2, 10))["delta_series"][0]
    assert 0.0 < d_small < d_big <= 1.0


def test_roi_excludes_change_outside_it(tmp_path):
    """Whole-screen deltas are dominated by the status-bar clock; the ROI is the
    whole point. A change entirely outside the ROI must read as no motion."""
    a = _frame(tmp_path / "a.png", [((0, 0, 200, 10), 0)])
    b = _frame(tmp_path / "b.png", [((0, 0, 200, 10), 255)])
    roi = (0, 100, 100, 100)
    res = motion.analyze_frames([a, b], _times(2, 10), roi=roi)
    assert res["delta_series"] == [0.0]
    # ...and without the ROI the same pair is clearly moving.
    assert motion.analyze_frames([a, b], _times(2, 10))["delta_series"][0] > 0.0


def test_roi_is_scaled_when_frames_are_not_screenshot_sized(tmp_path):
    """ROI arrives in screenshot pixels; decoded frames are downscaled for speed,
    so an unscaled ROI would crop the wrong region (or fall off the frame)."""
    a = _frame(tmp_path / "a.png", [((100, 200, 50, 50), 0)], size=(200, 400))
    b = _frame(tmp_path / "b.png", [((100, 200, 50, 50), 255)], size=(200, 400))
    # Reference (screenshot) space is 2x the frame space.
    res = motion.analyze_frames([a, b], _times(2, 10),
                                roi=(200, 400, 100, 100), reference_size=(400, 800))
    assert res["delta_series"][0] > 0.0
    assert res["roi_in_frame"] == [100, 200, 50, 50]


def test_roi_outside_the_frame_is_rejected(tmp_path):
    a = _frame(tmp_path / "a.png", [((0, 0, 10, 10), 0)], size=(200, 400))
    with pytest.raises(motion.MotionError):
        motion.analyze_frames([a, a], _times(2, 10), roi=(500, 500, 50, 50))


def test_mask_regions_blank_a_noisy_area(tmp_path):
    """Shares the replay mask concept: blank the clock instead of chasing it."""
    a = _frame(tmp_path / "a.png", [((0, 0, 200, 20), 0)])
    b = _frame(tmp_path / "b.png", [((0, 0, 200, 20), 255)])
    res = motion.analyze_frames([a, b], _times(2, 10), mask_regions=[(0, 0, 200, 20)])
    assert res["delta_series"] == [0.0]


# ── state clustering ────────────────────────────────────────────────────────


def test_distinct_states_counts_perceptually_distinct_screens(tmp_path):
    frames = _sequence(tmp_path, ["A", "A", "B", "B", "C", "C"])
    res = motion.analyze_frames(frames, _times(6, 10))
    assert res["distinct_states"] == 3


def test_repeated_visits_to_a_state_do_not_inflate_the_count(tmp_path):
    """A flicker is *few* states and *many* transitions; conflating the two would
    make the flicker signature unreadable."""
    frames = _sequence(tmp_path, ["A", "B", "A", "B", "A", "B"])
    res = motion.analyze_frames(frames, _times(6, 10))
    assert res["distinct_states"] == 2
    assert res["transitions"] == 5


def test_a_static_screen_is_one_state_with_no_transitions(tmp_path):
    frames = _sequence(tmp_path, ["A"] * 8)
    res = motion.analyze_frames(frames, _times(8, 10))
    assert res["distinct_states"] == 1
    assert res["transitions"] == 0


def test_state_sequence_is_reported_for_auditability(tmp_path):
    frames = _sequence(tmp_path, ["A", "B", "A"])
    res = motion.analyze_frames(frames, _times(3, 10))
    assert res["state_sequence"] == [0, 1, 0]


# ── settling ────────────────────────────────────────────────────────────────


def test_settled_at_ms_is_when_motion_stopped(tmp_path):
    # Moves for the first 3 frames, then holds still for 500 ms at 10 fps.
    frames = _sequence(tmp_path, ["A", "B", "C", "C", "C", "C", "C", "C"])
    res = motion.analyze_frames(frames, _times(8, 10), quiet_ms=300)
    assert res["settled_at_ms"] == pytest.approx(200.0, abs=1.0)


def test_never_settling_reports_null(tmp_path):
    """A stuck skeleton is a *settling* property, not a state check."""
    frames = _sequence(tmp_path, ["A", "B"] * 6)
    res = motion.analyze_frames(frames, _times(12, 10), quiet_ms=300)
    assert res["settled_at_ms"] is None


def test_a_screen_still_from_the_start_settles_at_zero(tmp_path):
    frames = _sequence(tmp_path, ["A"] * 8)
    res = motion.analyze_frames(frames, _times(8, 10), quiet_ms=300)
    assert res["settled_at_ms"] == 0.0


def test_a_late_twitch_pushes_the_settle_time_out(tmp_path):
    quiet_then_twitch = _sequence(tmp_path, ["A", "A", "A", "A", "B", "B", "B", "B"])
    res = motion.analyze_frames(quiet_then_twitch, _times(8, 10), quiet_ms=300)
    assert res["settled_at_ms"] == pytest.approx(400.0, abs=1.0)


def test_quiet_tail_shorter_than_quiet_ms_does_not_count_as_settled(tmp_path):
    """Two still frames at 10 fps is 100 ms — not enough to call it settled."""
    frames = _sequence(tmp_path, ["A", "B", "C", "D", "D"])
    res = motion.analyze_frames(frames, _times(5, 10), quiet_ms=300)
    assert res["settled_at_ms"] is None


def test_capture_that_stopped_producing_frames_settles_at_that_point(tmp_path):
    """simctl recordVideo is change-driven: a still display emits no frames, so a
    clip far shorter than the requested window proves stillness after it ends.
    Measured on iOS 26 — a 2000 ms recording of a static screen yielded a 67 ms clip."""
    frames = _sequence(tmp_path, ["A", "B"])
    res = motion.analyze_frames(frames, _times(2, 10), quiet_ms=300,
                                requested_duration_ms=5000.0)
    assert res["settled_at_ms"] == pytest.approx(100.0, abs=1.0)
    assert any("stopped producing frames" in w for w in res["warnings"])


# ── representative frames ───────────────────────────────────────────────────


def test_one_representative_frame_per_state_not_one_per_frame(tmp_path):
    """The payload budget is the design constraint: 120 frames is unreadable."""
    frames = _sequence(tmp_path, ["A", "A", "B", "B", "A", "C"])
    out = tmp_path / "reps"
    res = motion.analyze_frames(frames, _times(6, 10), representative_dir=out)
    reps = res["representative_frames"]
    assert len(reps) == 3 == res["distinct_states"]
    assert all(Path(r["path"]).exists() for r in reps)
    assert [r["state"] for r in reps] == [0, 1, 2]
    assert reps[1]["at_ms"] == pytest.approx(200.0, abs=1.0)


def test_representative_frames_are_capped(tmp_path):
    frames = _sequence(tmp_path, ["A", "B", "C", "D"] * 3)
    out = tmp_path / "reps"
    res = motion.analyze_frames(frames, _times(12, 10), representative_dir=out,
                                max_representative_frames=2)
    assert len(res["representative_frames"]) == 2
    assert res["distinct_states"] == 4, "capping the images must not fake the state count"


def test_no_frames_saved_when_no_directory_is_given(tmp_path):
    frames = _sequence(tmp_path, ["A", "B"])
    res = motion.analyze_frames(frames, _times(2, 10))
    assert res["representative_frames"] == []


# ── degenerate input ────────────────────────────────────────────────────────


def test_empty_frame_list_is_a_structured_error(tmp_path):
    with pytest.raises(motion.MotionError):
        motion.analyze_frames([], [])


def test_single_frame_has_no_deltas_but_still_reports_one_state(tmp_path):
    frames = _sequence(tmp_path, ["A"])
    res = motion.analyze_frames(frames, _times(1, 10))
    assert res["delta_series"] == []
    assert res["distinct_states"] == 1
    assert res["transitions"] == 0


def test_max_gap_ms_surfaces_sampling_dropouts(tmp_path):
    """A frame-rate collapse mid-morph is a finding; an even series must not hide it."""
    frames = _sequence(tmp_path, ["A", "B", "C"])
    res = motion.analyze_frames(frames, [0.0, 33.0, 400.0])
    assert res["max_gap_ms"] == pytest.approx(367.0, abs=1.0)


# ── flicker verdict ─────────────────────────────────────────────────────────


def test_alternating_between_two_states_is_flicker(tmp_path):
    """The motivating case: a button alternating Listen/Cancel several times a second."""
    frames = _sequence(tmp_path, ["A", "B"] * 6)
    verdict = motion.flicker_verdict(
        motion.analyze_frames(frames, _times(12, 10)), duration_ms=1200)
    assert verdict["flickering"] is True
    assert verdict["states"] == 2
    assert verdict["transitions"] == 11


def test_a_one_way_state_change_is_not_flicker(tmp_path):
    """Tapping a button and having the label change once is correct behaviour."""
    frames = _sequence(tmp_path, ["A"] * 6 + ["B"] * 6)
    verdict = motion.flicker_verdict(
        motion.analyze_frames(frames, _times(12, 10)), duration_ms=1200)
    assert verdict["flickering"] is False


def test_a_monotone_progression_through_many_states_is_not_flicker(tmp_path):
    """A wizard stepping A->B->C->D has transitions but never revisits a state."""
    frames = _sequence(tmp_path, ["A", "A", "B", "B", "C", "C", "D", "D"])
    verdict = motion.flicker_verdict(
        motion.analyze_frames(frames, _times(8, 10)), duration_ms=800)
    assert verdict["flickering"] is False
    assert verdict["transitions"] == 3


def test_a_single_there_and_back_is_not_flicker(tmp_path):
    """A -> B -> A once is a state change and a revert, not a flicker."""
    frames = _sequence(tmp_path, ["A", "A", "B", "B", "A", "A"])
    verdict = motion.flicker_verdict(
        motion.analyze_frames(frames, _times(6, 10)), duration_ms=600)
    assert verdict["flickering"] is False


def test_flicker_period_is_a_full_cycle_not_a_half(tmp_path):
    """At 10 fps alternating every frame, one A->B->A cycle takes 200 ms."""
    frames = _sequence(tmp_path, ["A", "B"] * 6)
    verdict = motion.flicker_verdict(
        motion.analyze_frames(frames, _times(12, 10)), duration_ms=1200)
    assert verdict["period_ms"] == pytest.approx(200.0, abs=20.0)


def test_static_screen_has_no_period(tmp_path):
    frames = _sequence(tmp_path, ["A"] * 6)
    verdict = motion.flicker_verdict(
        motion.analyze_frames(frames, _times(6, 10)), duration_ms=600)
    assert verdict["flickering"] is False
    assert verdict["period_ms"] is None


# ── capture plumbing ────────────────────────────────────────────────────────


def test_ffmpeg_absent_gives_a_recovery_message_not_an_import_error():
    with patch("simdrive.motion.shutil.which", return_value=None):
        with pytest.raises(motion.MotionError) as exc:
            motion.decode_frames(Path("/tmp/x.mp4"), fps=30, out_dir=Path("/tmp/y"))
    assert "ffmpeg" in str(exc.value).lower()
    assert "brew install ffmpeg" in str(exc.value)


def test_auto_source_falls_back_to_screenshot_sampling_without_ffmpeg():
    with patch("simdrive.motion.shutil.which", return_value=None):
        assert motion.resolve_source("auto") == "screenshots"


def test_auto_source_prefers_video_when_ffmpeg_is_present():
    with patch("simdrive.motion.shutil.which", return_value="/opt/homebrew/bin/ffmpeg"):
        assert motion.resolve_source("auto") == "video"


def test_explicit_video_source_without_ffmpeg_is_an_error():
    with patch("simdrive.motion.shutil.which", return_value=None):
        with pytest.raises(motion.MotionError):
            motion.resolve_source("video")


def test_screenshot_sampler_reports_its_real_rate(tmp_path):
    """~1 fps is what simctl screenshot actually sustains; claiming the requested
    fps would let an agent read a flicker verdict off five samples."""
    shots: list[float] = []

    def _fake_screenshot(udid, dest):
        _frame(dest, [((20, 20, 60, 40), 0 if len(shots) % 2 else 255)])
        shots.append(0.0)
        return dest

    with patch("simdrive.motion.sim.screenshot", side_effect=_fake_screenshot):
        paths, times = motion.sample_screenshots("UDID", tmp_path, duration_ms=300, fps=1000)
    assert len(paths) == len(times) >= 2
    # Stamped before the capture call, so the first frame sits at ~0 rather than
    # carrying the ~1 s simctl screenshot latency. Near-zero, not exactly zero:
    # this is a wall clock, and an exact-equality assertion here flakes.
    assert times[0] < 5.0
    assert times[-1] > times[0]


def test_effective_fps_is_measured_not_assumed(tmp_path):
    frames = _sequence(tmp_path, ["A", "B", "C"])
    res = motion.analyze_frames(frames, [0.0, 500.0, 1000.0])
    assert res["effective_fps"] == pytest.approx(2.0, abs=0.01)


# ── MCP tool wiring ─────────────────────────────────────────────────────────


def _make_sim_session(tmp_path: Path):
    from simdrive.sim import Device
    d = Device(udid="SIM-MOTION", name="iPhone 17 Pro", os_version="26.0", state="Booted")
    return SimpleNamespace(
        session_id="sid-motion",
        device=d,
        target="simulator",
        app_bundle_id="com.example.app",
        workdir=tmp_path,
        last_action_at=0.0,
        last_screenshot_w=200,
        last_screenshot_h=400,
        last_screenshot_path=None,
        last_marks=[],
        recorder=None,
    )


def _stub_capture(monkeypatch, result: dict):
    seen: dict = {}

    def _fake(udid, out_dir, duration_ms, fps=30, roi=None, mask_regions=None,
              source="auto", reference_size=None, **kw):
        seen.update({"udid": udid, "duration_ms": duration_ms, "fps": fps,
                     "roi": roi, "source": source, "reference_size": reference_size})
        return dict(result)

    monkeypatch.setattr("simdrive.motion.capture_motion", _fake)
    return seen


_BASE_RESULT = {
    "source": "video", "frames": 10, "fps": 30, "effective_fps": 29.9,
    "requested_duration_ms": 1000, "captured_duration_ms": 1000,
    "roi": None, "roi_in_frame": None, "frame_size": [200, 400],
    "delta_series": [0.0] * 9, "mean_delta": 0.0, "max_delta": 0.0,
    "distinct_states": 1, "state_sequence": [0] * 10, "transitions": 0,
    "settled_at_ms": 0.0, "max_gap_ms": 33.0, "representative_frames": [],
    "warnings": [], "note": "",
}


def test_tool_capture_motion_passes_roi_and_duration_through(tmp_path, monkeypatch):
    import simdrive.server as server_mod
    import simdrive.session as session_mod

    s = _make_sim_session(tmp_path)
    monkeypatch.setitem(session_mod._SESSIONS, s.session_id, s)
    seen = _stub_capture(monkeypatch, _BASE_RESULT)

    out = server_mod.tool_capture_motion({
        "session_id": s.session_id, "duration_ms": 2000, "fps": 20,
        "roi": [10, 20, 30, 40],
    })
    assert out["ok"] is True
    assert seen["duration_ms"] == 2000
    assert seen["fps"] == 20
    assert seen["roi"] == (10, 20, 30, 40)
    # The ROI is in screenshot pixels, so the analysis needs the screenshot size.
    assert seen["reference_size"] == (200, 400)


def test_tool_capture_motion_never_returns_a_frame_per_sample(tmp_path, monkeypatch):
    """The design constraint, enforced: numbers go back, not 120 images."""
    import simdrive.server as server_mod
    import simdrive.session as session_mod

    s = _make_sim_session(tmp_path)
    monkeypatch.setitem(session_mod._SESSIONS, s.session_id, s)
    result = dict(_BASE_RESULT, frames=120, distinct_states=3,
                  representative_frames=[{"state": i, "at_ms": 0.0, "path": "/p"} for i in range(3)])
    _stub_capture(monkeypatch, result)

    out = server_mod.tool_capture_motion({"session_id": s.session_id, "duration_ms": 4000})
    assert out["frames"] == 120
    assert len(out["representative_frames"]) == 3


def test_tool_capture_motion_rejects_a_malformed_roi(tmp_path, monkeypatch):
    import simdrive.server as server_mod
    import simdrive.session as session_mod

    s = _make_sim_session(tmp_path)
    monkeypatch.setitem(session_mod._SESSIONS, s.session_id, s)
    with pytest.raises(Exception) as exc:
        server_mod.tool_capture_motion({"session_id": s.session_id, "roi": [1, 2, 3]})
    assert "roi" in str(exc.value).lower()


def test_tool_capture_motion_clamps_an_absurd_duration(tmp_path, monkeypatch):
    import simdrive.server as server_mod
    import simdrive.session as session_mod

    s = _make_sim_session(tmp_path)
    monkeypatch.setitem(session_mod._SESSIONS, s.session_id, s)
    with pytest.raises(Exception):
        server_mod.tool_capture_motion({"session_id": s.session_id, "duration_ms": 10 ** 7})


def test_tool_detect_flicker_refuses_a_verdict_it_cannot_support(tmp_path, monkeypatch):
    """At ~1 fps you cannot resolve a several-Hz flicker. Saying so beats guessing."""
    import simdrive.server as server_mod
    import simdrive.session as session_mod

    s = _make_sim_session(tmp_path)
    monkeypatch.setitem(session_mod._SESSIONS, s.session_id, s)
    _stub_capture(monkeypatch, dict(_BASE_RESULT, source="screenshots", effective_fps=1.1))

    out = server_mod.tool_detect_flicker({
        "session_id": s.session_id, "roi": [0, 0, 10, 10], "duration_ms": 2000})
    assert out["ok"] is False
    assert out["error"]["code"] == "insufficient_frame_rate"


def test_tool_detect_flicker_returns_the_verdict_shape(tmp_path, monkeypatch):
    import simdrive.server as server_mod
    import simdrive.session as session_mod

    s = _make_sim_session(tmp_path)
    monkeypatch.setitem(session_mod._SESSIONS, s.session_id, s)
    _stub_capture(monkeypatch, dict(
        _BASE_RESULT, effective_fps=30.0, distinct_states=2, transitions=11,
        state_sequence=[0, 1] * 6, delta_series=[0.4] * 11,
    ))

    out = server_mod.tool_detect_flicker({
        "session_id": s.session_id, "roi": [0, 0, 10, 10], "duration_ms": 1200})
    assert out["ok"] is True
    assert set(["flickering", "transitions", "period_ms", "states"]) <= set(out)
    assert out["flickering"] is True


def test_tool_detect_flicker_requires_an_roi(tmp_path, monkeypatch):
    """Whole-screen flicker detection is dominated by the clock; an ROI is not optional."""
    import simdrive.server as server_mod
    import simdrive.session as session_mod

    s = _make_sim_session(tmp_path)
    monkeypatch.setitem(session_mod._SESSIONS, s.session_id, s)
    with pytest.raises(Exception) as exc:
        server_mod.tool_detect_flicker({"session_id": s.session_id, "duration_ms": 1000})
    assert "roi" in str(exc.value).lower()


def test_tool_liveness_probe_taps_before_sampling(tmp_path, monkeypatch):
    """A liveness verdict with no stimulus is just 'nothing happened'."""
    import simdrive.server as server_mod
    import simdrive.session as session_mod

    s = _make_sim_session(tmp_path)
    monkeypatch.setitem(session_mod._SESSIONS, s.session_id, s)
    taps: list[tuple] = []
    monkeypatch.setattr("simdrive.act.tap",
                        lambda x, y, sw, sh, udid=None: taps.append((x, y)) or (x, y))
    monkeypatch.setattr("simdrive.perf.snapshot",
                        lambda udid, bundle: {"pid": 1, "cpu_pct": 0.2})
    _stub_capture(monkeypatch, dict(_BASE_RESULT, max_delta=0.0))

    out = server_mod.tool_liveness_probe({"session_id": s.session_id, "seconds": 2})
    assert taps, "liveness_probe must inject a touch"
    assert out["responsive"] is False
    assert out["cpu_pct"] == 0.2


def test_tool_liveness_probe_reports_a_responsive_ui(tmp_path, monkeypatch):
    import simdrive.server as server_mod
    import simdrive.session as session_mod

    s = _make_sim_session(tmp_path)
    monkeypatch.setitem(session_mod._SESSIONS, s.session_id, s)
    monkeypatch.setattr("simdrive.act.tap", lambda x, y, sw, sh, udid=None: (x, y))
    monkeypatch.setattr("simdrive.perf.snapshot",
                        lambda udid, bundle: {"pid": 1, "cpu_pct": 41.0})
    _stub_capture(monkeypatch, dict(_BASE_RESULT, max_delta=0.35, transitions=2,
                                    distinct_states=2))

    out = server_mod.tool_liveness_probe({"session_id": s.session_id, "seconds": 2})
    assert out["responsive"] is True
    assert out["max_delta"] == 0.35


def test_tool_liveness_probe_taps_a_caller_supplied_point(tmp_path, monkeypatch):
    import simdrive.server as server_mod
    import simdrive.session as session_mod

    s = _make_sim_session(tmp_path)
    monkeypatch.setitem(session_mod._SESSIONS, s.session_id, s)
    taps: list[tuple] = []
    monkeypatch.setattr("simdrive.act.tap",
                        lambda x, y, sw, sh, udid=None: taps.append((x, y)) or (x, y))
    monkeypatch.setattr("simdrive.perf.snapshot", lambda udid, bundle: {"pid": None, "cpu_pct": 0.0})
    _stub_capture(monkeypatch, _BASE_RESULT)

    server_mod.tool_liveness_probe({"session_id": s.session_id, "seconds": 1, "x": 42, "y": 99})
    assert taps == [(42, 99)]


def test_motion_tools_are_registered_and_documented():
    import simdrive.server as server_mod
    names = {t["name"] for t in server_mod._TOOLS}
    assert {"capture_motion", "detect_flicker", "liveness_probe"} <= names
    spec = next(t for t in server_mod._TOOLS if t["name"] == "capture_motion")
    desc = spec["description"].lower()
    assert "roi" in desc
    assert "ffmpeg" in desc, "the decode dependency must be discoverable before the call fails"


def test_motion_capture_does_not_touch_replay_drift_semantics():
    """Replay's animation-suppression hysteresis is correct and load-bearing;
    motion capture is an additional channel, not a change to drift."""
    from simdrive import recorder
    import inspect
    src = inspect.getsource(recorder)
    assert "import motion" not in src and "from .motion" not in src


# ── live capture (needs a booted simulator + ffmpeg) ────────────────────────


def _booted_udid() -> str | None:
    try:
        from simdrive import sim as sim_mod
        d = sim_mod.first_booted()
        return d.udid if d else None
    except Exception:
        return None


@pytest.mark.live
def test_live_capture_returns_quantified_motion(tmp_path):
    udid = _booted_udid()
    if not udid:
        pytest.skip("no booted simulator")
    if not subprocess.run(["which", "ffmpeg"], capture_output=True).returncode == 0:
        pytest.skip("ffmpeg not installed")
    res = motion.capture_motion(udid, tmp_path, duration_ms=1500, fps=15)
    assert res["frames"] >= 1
    assert len(res["delta_series"]) == max(0, res["frames"] - 1)
    assert res["distinct_states"] >= 1
    assert 0.0 <= res["max_delta"] <= 1.0


def test_a_slow_but_real_oscillation_is_still_flicker():
    """Regression from a live measurement: driving a genuine 2-state oscillation on
    a booted sim produced 5 transitions between 2 states over 6 s (0.83/s). An
    earlier 1.0/s rate floor vetoed it — the only thing between a real
    oscillation and a correct verdict. Revisits, not rate, is the discriminator."""
    analysis = {"transitions": 5, "distinct_states": 2,
                "state_sequence": [0] * 27 + [1] * 12 + [0] * 15 + [1] * 12 + [0] * 13 + [1] * 13,
                "captured_duration_ms": 6000.0}
    verdict = motion.flicker_verdict(analysis, duration_ms=6000)
    assert verdict["flickering"] is True
    assert verdict["revisits"] == 4
    assert verdict["transitions_per_second"] == pytest.approx(0.83, abs=0.01)


def test_the_rate_floor_still_excludes_a_glacial_two_state_drift():
    """Two states swapping four times across two minutes is not a flicker."""
    analysis = {"transitions": 4, "distinct_states": 2,
                "state_sequence": [0, 1, 0, 1, 0], "captured_duration_ms": 120000.0}
    verdict = motion.flicker_verdict(analysis, duration_ms=120000)
    assert verdict["flickering"] is False


# ── tool-surface edge cases ─────────────────────────────────────────────────


def test_motion_tools_refuse_a_device_session(tmp_path, monkeypatch):
    """recordVideo and screenshot polling here are simctl-only; a real device
    session must get a clear refusal, not a failure deep in a subprocess."""
    import simdrive.server as server_mod
    import simdrive.session as session_mod
    from simdrive.sim import Device

    d = Device(udid="DEV", name="iPhone", os_version="26.0", state="available")
    s = SimpleNamespace(session_id="sid-dev-motion", device=d, target="device",
                        app_bundle_id="com.example.app", workdir=tmp_path,
                        last_action_at=0.0, last_screenshot_w=0, last_screenshot_h=0)
    monkeypatch.setitem(session_mod._SESSIONS, s.session_id, s)
    calls = [
        (server_mod.tool_capture_motion, {"session_id": s.session_id}),
        (server_mod.tool_detect_flicker, {"session_id": s.session_id, "roi": [0, 0, 5, 5]}),
        (server_mod.tool_liveness_probe, {"session_id": s.session_id}),
    ]
    for tool, args in calls:
        with pytest.raises(Exception) as exc:
            tool(args)
        assert "simulator-only" in str(exc.value)


def test_roi_may_be_given_as_an_object(tmp_path, monkeypatch):
    """Matches replay's mask_regions, which accepts both forms."""
    import simdrive.server as server_mod
    import simdrive.session as session_mod

    s = _make_sim_session(tmp_path)
    monkeypatch.setitem(session_mod._SESSIONS, s.session_id, s)
    seen = _stub_capture(monkeypatch, _BASE_RESULT)
    server_mod.tool_capture_motion({
        "session_id": s.session_id, "roi": {"x": 1, "y": 2, "w": 3, "h": 4}})
    assert seen["roi"] == (1, 2, 3, 4)


@pytest.mark.parametrize("bad_roi", [
    {"x": 1, "y": 2},                 # object missing w/h
    ["a", "b", "c", "d"],             # non-integer entries
    "0,0,10,10",                      # a string
])
def test_malformed_roi_forms_are_rejected(tmp_path, monkeypatch, bad_roi):
    import simdrive.server as server_mod
    import simdrive.session as session_mod

    s = _make_sim_session(tmp_path)
    monkeypatch.setitem(session_mod._SESSIONS, s.session_id, s)
    with pytest.raises(Exception) as exc:
        server_mod.tool_capture_motion({"session_id": s.session_id, "roi": bad_roi})
    assert "roi" in str(exc.value).lower()


@pytest.mark.parametrize("fps", [0, 61])
def test_fps_outside_the_supported_range_is_rejected(tmp_path, monkeypatch, fps):
    import simdrive.server as server_mod
    import simdrive.session as session_mod

    s = _make_sim_session(tmp_path)
    monkeypatch.setitem(session_mod._SESSIONS, s.session_id, s)
    with pytest.raises(Exception) as exc:
        server_mod.tool_capture_motion({"session_id": s.session_id, "fps": fps})
    assert "fps" in str(exc.value)


def test_capture_failure_becomes_a_structured_error(tmp_path, monkeypatch):
    """A missing ffmpeg with source='video' must arrive as a coded error carrying
    the recovery hint, not as a raw RuntimeError."""
    import simdrive.server as server_mod
    import simdrive.session as session_mod
    from simdrive import errors as err_mod

    s = _make_sim_session(tmp_path)
    monkeypatch.setitem(session_mod._SESSIONS, s.session_id, s)

    def _boom(*a, **kw):
        raise motion.MotionError("ffmpeg is required ... brew install ffmpeg")

    monkeypatch.setattr("simdrive.motion.capture_motion", _boom)
    with pytest.raises(err_mod.SimdriveError) as exc:
        server_mod.tool_capture_motion({"session_id": s.session_id})
    assert exc.value.code == "motion_capture_failed"
    assert "brew install ffmpeg" in exc.value.message


def test_detect_flicker_rejects_an_absurd_duration(tmp_path, monkeypatch):
    import simdrive.server as server_mod
    import simdrive.session as session_mod

    s = _make_sim_session(tmp_path)
    monkeypatch.setitem(session_mod._SESSIONS, s.session_id, s)
    with pytest.raises(Exception) as exc:
        server_mod.tool_detect_flicker({
            "session_id": s.session_id, "roi": [0, 0, 5, 5], "duration_ms": 10 ** 7})
    assert "duration_ms" in str(exc.value)


@pytest.mark.parametrize("seconds", [0, 61])
def test_liveness_probe_rejects_an_out_of_range_window(tmp_path, monkeypatch, seconds):
    import simdrive.server as server_mod
    import simdrive.session as session_mod

    s = _make_sim_session(tmp_path)
    monkeypatch.setitem(session_mod._SESSIONS, s.session_id, s)
    with pytest.raises(Exception) as exc:
        server_mod.tool_liveness_probe({"session_id": s.session_id, "seconds": seconds})
    assert "seconds" in str(exc.value)


def test_liveness_probe_still_reports_when_the_tap_itself_fails(tmp_path, monkeypatch):
    """A UI so wedged that HID injection errors is exactly when the verdict matters;
    the probe must record the tap failure and still sample the screen."""
    import simdrive.server as server_mod
    import simdrive.session as session_mod

    s = _make_sim_session(tmp_path)
    monkeypatch.setitem(session_mod._SESSIONS, s.session_id, s)

    def _boom(*a, **kw):
        raise RuntimeError("hid inject timed out")

    monkeypatch.setattr("simdrive.act.tap", _boom)
    monkeypatch.setattr("simdrive.perf.snapshot", lambda udid, bundle: {"cpu_pct": 0.0})
    _stub_capture(monkeypatch, dict(_BASE_RESULT, max_delta=0.0))

    out = server_mod.tool_liveness_probe({"session_id": s.session_id, "seconds": 1})
    assert out["ok"] is True
    assert out["tap_error"] == "hid inject timed out"
    assert out["responsive"] is False


def test_liveness_probe_distinguishes_a_spin_from_a_deadlock(tmp_path, monkeypatch):
    """Frozen at 98% CPU and frozen at idle are different bugs; the campaign
    established its freeze precisely by pairing UI stillness with CPU%."""
    import simdrive.server as server_mod
    import simdrive.session as session_mod

    s = _make_sim_session(tmp_path)
    monkeypatch.setitem(session_mod._SESSIONS, s.session_id, s)
    monkeypatch.setattr("simdrive.act.tap", lambda x, y, sw, sh, udid=None: (x, y))
    monkeypatch.setattr("simdrive.perf.snapshot", lambda udid, bundle: {"cpu_pct": 98.4})
    _stub_capture(monkeypatch, dict(_BASE_RESULT, max_delta=0.0))

    out = server_mod.tool_liveness_probe({"session_id": s.session_id, "seconds": 1})
    assert out["responsive"] is False
    assert "spin" in out["verdict"]
    assert "deadlock" in out["verdict"]


def test_liveness_probe_survives_an_unavailable_perf_snapshot(tmp_path, monkeypatch):
    """CPU is corroboration, not a dependency — losing it must not lose the verdict."""
    import simdrive.server as server_mod
    import simdrive.session as session_mod

    s = _make_sim_session(tmp_path)
    monkeypatch.setitem(session_mod._SESSIONS, s.session_id, s)
    monkeypatch.setattr("simdrive.act.tap", lambda x, y, sw, sh, udid=None: (x, y))

    def _boom(*a, **kw):
        raise RuntimeError("no pid")

    monkeypatch.setattr("simdrive.perf.snapshot", _boom)
    _stub_capture(monkeypatch, dict(_BASE_RESULT, max_delta=0.0))

    out = server_mod.tool_liveness_probe({"session_id": s.session_id, "seconds": 1})
    assert out["ok"] is True
    assert out["cpu_pct"] is None
    assert "hang" in out["verdict"]
