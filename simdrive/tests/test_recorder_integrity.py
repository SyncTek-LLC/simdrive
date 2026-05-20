"""Recorder integrity tests (INIT-2026-549 / WS-E).

Covers three audit hardenings of ``simdrive.recorder``:

1. ``Recorder.add_step`` drops the step entirely (with a structured warning
   log) when either pre- or post-state capture failed. Previously such steps
   were appended with whatever partial state was captured and replays tripped
   on them with confusing errors.

2. **Invariant** (Hypothesis property test): every step persisted into
   ``recording.yaml`` has non-None ``pre_screenshot`` + ``post_screenshot``
   keys and consistent coordinate-space metadata. Holds regardless of how
   many random calls ``add_step`` receives (mixture of valid + invalid
   captures, varied actions).

3. ``replay()`` SSIM hysteresis: a single noisy frame below the drift
   threshold does NOT halt; the engine recaptures and only halts when two
   consecutive samples are sub-threshold. Each comparison is also emitted at
   DEBUG level so operators can diagnose flaky replays from logs.
"""
from __future__ import annotations

import logging
from pathlib import Path

import pytest
import yaml
from PIL import Image

# Hypothesis is in the test deps (tests/test_packaging_deps.py lists it). Skip
# the property test gracefully when running in stripped environments.
try:
    from hypothesis import given, settings, HealthCheck
    from hypothesis import strategies as st
    HAS_HYPOTHESIS = True
except ImportError:  # pragma: no cover — local-only fallback
    HAS_HYPOTHESIS = False


# ─── Helpers ──────────────────────────────────────────────────────────────


def _make_session(tmp_path: Path, *, target: str = "simulator"):
    from simdrive import session as ses_mod
    from simdrive.sim import Device

    ses_mod._SESSIONS.clear()
    device = Device(
        udid="UDID-INTEGRITY-001",
        name="iPhone Test",
        os_version="18.4",
        state="Booted",
    )
    s = ses_mod.Session(
        session_id="integrity-test",
        device=device,
        workdir=tmp_path / "wd",
        target=target,
        app_bundle_id="com.test.integrity",
    )
    s.workdir.mkdir(parents=True, exist_ok=True)
    return s


def _png(path: Path, size=(64, 64), fill=(200, 200, 200)) -> Path:
    Image.new("RGB", size, fill).save(path)
    return path


def _fake_observe_factory(screenshot_path: Path):
    """Build an observe() stub that always returns the same screenshot."""
    from simdrive.observe import Observation

    def _fake_observe(udid, out_dir, **kwargs):
        return Observation(
            screenshot_path=screenshot_path,
            annotated_path=None,
            screenshot_w=64,
            screenshot_h=64,
            window_bounds=None,
            captured_at=0.0,
            marks=[],
        )

    return _fake_observe


# ─── Audit item 1: partial-capture steps are dropped ─────────────────────


def test_add_step_drops_step_when_post_screenshot_missing(tmp_path, monkeypatch, caplog):
    """A None ``post_screenshot`` simulates a failed post-state capture.

    Previously the step would still append (with broken paths) and break
    replay. The step must now be dropped and a structured warning logged
    containing the action type.
    """
    from simdrive import recorder

    monkeypatch.setenv("SIMDRIVE_HOME", str(tmp_path))

    s = _make_session(tmp_path)
    monkeypatch.setattr(
        "simdrive.observe.observe",
        _fake_observe_factory(_png(tmp_path / "obs.png")),
    )

    rec = recorder.start(s, "drop-on-post-fail")
    pre = _png(tmp_path / "pre.png")

    caplog.clear()
    with caplog.at_level(logging.WARNING, logger="simdrive.recorder"):
        step_id = rec.add_step(
            "tap",
            {"x": 10, "y": 20, "screenshot_w": 64, "screenshot_h": 64},
            pre,
            None,  # <-- failed post-state capture
        )

    assert step_id is None, "Step with missing post must be dropped"
    assert rec.steps == [], "Recording must not contain partial steps"

    drop_records = [
        r for r in caplog.records
        if r.name == "simdrive.recorder"
        and r.message == "recorder.dropped_step_partial_capture"
    ]
    assert drop_records, "Expected a structured drop warning"
    rec_log = drop_records[0]
    assert getattr(rec_log, "action", None) == "tap"
    assert "post_state_missing" in getattr(rec_log, "failure", "")
    assert getattr(rec_log, "timestamp", None) is not None


def test_add_step_drops_step_when_post_screenshot_path_does_not_exist(
    tmp_path, monkeypatch, caplog,
):
    """A non-None path that does not point to a real file is also a failure
    — typical case is when the screenshot tool returned a path but the write
    silently no-op'd. Recorder must NOT copy garbage data into the snapshot
    dir and must NOT append the step."""
    from simdrive import recorder

    monkeypatch.setenv("SIMDRIVE_HOME", str(tmp_path))

    s = _make_session(tmp_path)
    monkeypatch.setattr(
        "simdrive.observe.observe",
        _fake_observe_factory(_png(tmp_path / "obs.png")),
    )

    rec = recorder.start(s, "drop-on-missing-file")
    pre = _png(tmp_path / "pre.png")
    bogus_post = tmp_path / "does-not-exist.png"

    caplog.clear()
    with caplog.at_level(logging.WARNING, logger="simdrive.recorder"):
        step_id = rec.add_step(
            "type_text",
            {"text": "hello"},
            pre,
            bogus_post,
        )

    assert step_id is None
    assert rec.steps == []
    assert not (rec.snapshots_dir / "001_pre.png").exists(), (
        "Pre snapshot must not be copied into the recording when the step is dropped"
    )

    drop_records = [
        r for r in caplog.records
        if r.message == "recorder.dropped_step_partial_capture"
    ]
    assert drop_records, "Expected a structured drop warning"
    assert getattr(drop_records[0], "action", None) == "type_text"


def test_add_step_drops_step_when_post_screenshot_is_empty_file(
    tmp_path, monkeypatch, caplog,
):
    """A zero-byte post screenshot is the simctl-hiccup symptom in
    production — file exists but read is empty. Must be treated as a failed
    capture, not silently propagated into the recording."""
    from simdrive import recorder

    monkeypatch.setenv("SIMDRIVE_HOME", str(tmp_path))

    s = _make_session(tmp_path)
    monkeypatch.setattr(
        "simdrive.observe.observe",
        _fake_observe_factory(_png(tmp_path / "obs.png")),
    )

    rec = recorder.start(s, "drop-on-empty-file")
    pre = _png(tmp_path / "pre.png")
    empty_post = tmp_path / "empty.png"
    empty_post.write_bytes(b"")

    caplog.clear()
    with caplog.at_level(logging.WARNING, logger="simdrive.recorder"):
        step_id = rec.add_step(
            "swipe",
            {"x1": 0, "y1": 0, "x2": 10, "y2": 10, "screenshot_w": 64, "screenshot_h": 64},
            pre,
            empty_post,
        )

    assert step_id is None
    assert rec.steps == []

    drop_records = [
        r for r in caplog.records
        if r.message == "recorder.dropped_step_partial_capture"
    ]
    assert drop_records, "Expected drop warning for empty post screenshot"
    assert "post_state_empty" in getattr(drop_records[0], "failure", "")


def test_add_step_keeps_step_ids_contiguous_after_drop(tmp_path, monkeypatch):
    """A dropped step must NOT consume an id slot — the next successful
    step is still 1-based and contiguous. Without this, replay would see
    step ids like [1, 3] and the diagnostic UX would be miserable."""
    from simdrive import recorder

    monkeypatch.setenv("SIMDRIVE_HOME", str(tmp_path))

    s = _make_session(tmp_path)
    monkeypatch.setattr(
        "simdrive.observe.observe",
        _fake_observe_factory(_png(tmp_path / "obs.png")),
    )

    rec = recorder.start(s, "contiguous-ids")
    pre = _png(tmp_path / "pre.png")
    post = _png(tmp_path / "post.png")

    # Step 1 — succeeds.
    id1 = rec.add_step("tap", {"x": 1, "y": 1}, pre, post)
    # Failed capture — dropped.
    dropped = rec.add_step("tap", {"x": 2, "y": 2}, pre, None)
    # Step 2 (was-id-3 in a buggy implementation) — succeeds.
    id3 = rec.add_step("tap", {"x": 3, "y": 3}, pre, post)

    assert id1 == 1
    assert dropped is None
    assert id3 == 2, "Dropped step should not consume an id"
    assert [s["id"] for s in rec.steps] == [1, 2]


# ─── Audit item 2: persisted-recording invariant (Hypothesis) ───────────


@pytest.mark.skipif(not HAS_HYPOTHESIS, reason="hypothesis not installed")
@settings(
    max_examples=30,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(
    actions_and_validity=st.lists(
        st.tuples(
            st.sampled_from(["tap", "swipe", "type_text", "press_key"]),
            # True → valid capture (both PNGs); False → simulated failure
            # (None post, missing path, or empty file — chosen below).
            st.booleans(),
            st.sampled_from(["none_post", "missing_post", "empty_post", "none_pre"]),
        ),
        min_size=1,
        max_size=8,
    ),
)
def test_persisted_recording_invariant_pre_and_post_always_present(
    tmp_path, monkeypatch, actions_and_validity,
):
    """Property: every persisted ``recording.steps[*]`` entry has non-None
    ``pre_screenshot`` and ``post_screenshot`` keys pointing at files that
    actually exist in the recording dir, AND each step's recorded coord-space
    metadata is consistent within the step (``screenshot_w/h`` if present
    matches the dimensions in args). This must hold regardless of how many
    add_step calls were made with failed captures interleaved.
    """
    from simdrive import recorder

    # Per-example isolation: fresh SIMDRIVE_HOME and recording name. Hypothesis
    # re-invokes the body with the same tmp_path/monkeypatch fixtures, so use
    # exist_ok=True and a unique session subdir keyed on the example.
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    monkeypatch.setenv("SIMDRIVE_HOME", str(home))

    example_key = abs(hash(tuple((a, v, f) for a, v, f in actions_and_validity)))
    s = _make_session(tmp_path / f"session-{example_key}")
    monkeypatch.setattr(
        "simdrive.observe.observe",
        _fake_observe_factory(_png(tmp_path / "obs_invariant.png")),
    )

    rec_name = f"invariant-{abs(hash(tuple((a, v, f) for a, v, f in actions_and_validity)))}"
    rec = recorder.start(s, rec_name)

    base_pre = _png(tmp_path / "valid_pre.png")
    base_post = _png(tmp_path / "valid_post.png")
    empty_file = tmp_path / "empty.png"
    empty_file.write_bytes(b"")

    for action, valid, failure_kind in actions_and_validity:
        # Use a consistent coordinate space within each persisted step so the
        # "same coord-space metadata" invariant has something to compare.
        args = {"screenshot_w": 64, "screenshot_h": 64}
        if action == "tap":
            args.update({"x": 5, "y": 7})
        elif action == "swipe":
            args.update({"x1": 0, "y1": 0, "x2": 10, "y2": 10})
        elif action == "type_text":
            args["text"] = "hi"
        elif action == "press_key":
            args["key"] = "return"

        if valid:
            rec.add_step(action, args, base_pre, base_post)
        else:
            if failure_kind == "none_post":
                rec.add_step(action, args, base_pre, None)
            elif failure_kind == "missing_post":
                rec.add_step(action, args, base_pre, tmp_path / "nope.png")
            elif failure_kind == "empty_post":
                rec.add_step(action, args, base_pre, empty_file)
            elif failure_kind == "none_pre":
                rec.add_step(action, args, None, base_post)

    yaml_path = recorder.stop(s)
    payload = yaml.safe_load(yaml_path.read_text())
    persisted_steps = payload.get("steps", [])

    rec_dir = yaml_path.parent
    for step in persisted_steps:
        # Invariant 1: both screenshot keys present and non-None.
        assert step.get("pre_screenshot"), f"step {step['id']} missing pre_screenshot key"
        assert step.get("post_screenshot"), f"step {step['id']} missing post_screenshot key"

        # Invariant 2: the referenced files exist + are non-empty.
        pre_full = rec_dir / step["pre_screenshot"]
        post_full = rec_dir / step["post_screenshot"]
        assert pre_full.exists() and pre_full.stat().st_size > 0, (
            f"step {step['id']} pre file missing/empty: {pre_full}"
        )
        assert post_full.exists() and post_full.stat().st_size > 0, (
            f"step {step['id']} post file missing/empty: {post_full}"
        )

        # Invariant 3: coord-space metadata in args is consistent (w&h both
        # present or both absent — never one without the other).
        args = step.get("args") or {}
        has_w = "screenshot_w" in args
        has_h = "screenshot_h" in args
        assert has_w == has_h, (
            f"step {step['id']} has mismatched coord-space metadata: "
            f"screenshot_w={has_w}, screenshot_h={has_h}"
        )

    # Invariant 4: step ids are contiguous from 1.
    ids = [step["id"] for step in persisted_steps]
    assert ids == list(range(1, len(ids) + 1)), (
        f"step ids must be contiguous from 1, got {ids}"
    )


# ─── Audit item 3: replay drift hysteresis + DEBUG logging ──────────────


def _write_single_step_recording(rec_dir: Path, *, pre_fill=(200, 200, 200),
                                  post_fill=(180, 180, 180)) -> Path:
    """Lay out a minimal 1-step recording on disk."""
    snaps = rec_dir / "snapshots"
    snaps.mkdir(parents=True, exist_ok=True)
    pre = snaps / "001_pre.png"
    post = snaps / "001_post.png"
    Image.new("RGB", (64, 64), pre_fill).save(pre)
    Image.new("RGB", (64, 64), post_fill).save(post)
    payload = {
        "name": rec_dir.name,
        "created_at": 0.0,
        "device": "iPhone Test",
        "os_version": "18.4",
        "app_bundle_id": None,
        "steps": [
            {
                "id": 1,
                "action": "tap",
                "args": {"x": 10, "y": 10, "screenshot_w": 64, "screenshot_h": 64},
                "pre_screenshot": "snapshots/001_pre.png",
                "post_screenshot": "snapshots/001_post.png",
                "captured_at": 0.0,
            }
        ],
    }
    (rec_dir / "recording.yaml").write_text(yaml.safe_dump(payload, sort_keys=False))
    return rec_dir / "recording.yaml"


def _install_alternating_observe(monkeypatch, paths: list[Path]) -> dict:
    """Patch observe() to return each given screenshot path in sequence.

    Returns the call-count state dict (mutated in place) so callers can
    assert how many recaptures the replay engine triggered.
    """
    from simdrive.observe import Observation
    from simdrive import observe as obs_mod

    state = {"i": 0}

    def _fake_observe(udid, out_dir, **kwargs):
        idx = min(state["i"], len(paths) - 1)
        state["i"] += 1
        return Observation(
            screenshot_path=paths[idx],
            annotated_path=None,
            screenshot_w=64,
            screenshot_h=64,
            window_bounds=None,
            captured_at=0.0,
            marks=[],
        )

    monkeypatch.setattr(obs_mod, "observe", _fake_observe)
    return state


def test_drift_hysteresis_single_subthreshold_then_normal_does_not_halt(
    tmp_path, monkeypatch, caplog,
):
    """One noisy sub-threshold frame followed by a normal frame must NOT
    halt the replay. The recheck samples a fresh screenshot and finds the UI
    stable — the recheck score is above threshold so we proceed."""
    from simdrive import recorder as rec_mod, act

    monkeypatch.setenv("SIMDRIVE_HOME", str(tmp_path))

    rec_dir = tmp_path / "recordings" / "hysteresis-recover"
    _write_single_step_recording(rec_dir, pre_fill=(200, 200, 200))

    # Live frames: first one is wildly different (forces sub-threshold SSIM),
    # second matches the recorded pre — recheck passes → no halt.
    noisy = tmp_path / "noisy.png"
    Image.new("RGB", (64, 64), (10, 10, 10)).save(noisy)
    clean = tmp_path / "clean.png"
    Image.new("RGB", (64, 64), (200, 200, 200)).save(clean)
    _install_alternating_observe(monkeypatch, [noisy, clean])

    monkeypatch.setattr(act, "tap", lambda *a, **kw: (0, 0))

    s = _make_session(tmp_path)

    caplog.clear()
    with caplog.at_level(logging.DEBUG, logger="simdrive.recorder"):
        result = rec_mod.replay(
            "hysteresis-recover",
            s,
            on_drift="halt",
            drift_threshold=0.85,
            halt_on_state_mismatch=False,
        )

    assert result["ok"] is True, f"replay should not halt on single noisy frame: {result}"
    assert result["halt_reason"] is None

    # Two DEBUG ssim_compare records expected (sample=1 then sample=2).
    debug_records = [
        r for r in caplog.records
        if r.name == "simdrive.recorder" and r.message == "replay.ssim_compare"
    ]
    samples = sorted(getattr(r, "sample", None) for r in debug_records)
    assert samples == [1, 2], (
        f"Expected two DEBUG ssim_compare records (sample=1,2), got {samples}"
    )


def test_drift_hysteresis_two_consecutive_subthreshold_frames_halts(
    tmp_path, monkeypatch, caplog,
):
    """Two consecutive sub-threshold frames DO halt — the error message must
    include BOTH scores so a triaging operator can see both samples were
    bad."""
    from simdrive import recorder as rec_mod, act

    monkeypatch.setenv("SIMDRIVE_HOME", str(tmp_path))

    rec_dir = tmp_path / "recordings" / "hysteresis-halt"
    _write_single_step_recording(rec_dir, pre_fill=(200, 200, 200))

    # Both live frames are wildly different from the recorded pre — both
    # samples fall below threshold → halt.
    noisy_a = tmp_path / "noisy_a.png"
    noisy_b = tmp_path / "noisy_b.png"
    Image.new("RGB", (64, 64), (10, 10, 10)).save(noisy_a)
    Image.new("RGB", (64, 64), (15, 15, 15)).save(noisy_b)
    _install_alternating_observe(monkeypatch, [noisy_a, noisy_b])

    monkeypatch.setattr(act, "tap", lambda *a, **kw: (0, 0))

    s = _make_session(tmp_path)

    caplog.clear()
    with caplog.at_level(logging.DEBUG, logger="simdrive.recorder"):
        result = rec_mod.replay(
            "hysteresis-halt",
            s,
            on_drift="halt",
            drift_threshold=0.85,
            halt_on_state_mismatch=False,
        )

    assert result["ok"] is False
    assert result["halt_reason"] == "drift"
    assert result["halted_at"] == 1

    # The failing step's error must mention "2 consecutive sub-threshold
    # samples" and quote both score numbers.
    failing_step = result["steps"][-1]
    err = failing_step["error"]
    assert "2 consecutive sub-threshold samples" in err, err
    # Both scores quoted: the message is "SSIM <a> then <b> < 0.85 …"
    assert " then " in err, f"Error must include both samples: {err}"

    # Two DEBUG samples on the halting step (sample 1 + recheck sample 2).
    debug_records = [
        r for r in caplog.records
        if r.name == "simdrive.recorder" and r.message == "replay.ssim_compare"
    ]
    samples = sorted(getattr(r, "sample", None) for r in debug_records)
    assert samples == [1, 2], (
        f"Halting step must log both samples at DEBUG: got {samples}"
    )


def test_drift_hysteresis_single_subthreshold_when_on_drift_force_still_logs_both(
    tmp_path, monkeypatch, caplog,
):
    """When ``on_drift='force'`` the replay never halts, but the hysteresis
    recapture and DEBUG logs must still happen — operators triaging a flaky
    replay want to see both samples in the logs regardless of halt policy."""
    from simdrive import recorder as rec_mod, act

    monkeypatch.setenv("SIMDRIVE_HOME", str(tmp_path))

    rec_dir = tmp_path / "recordings" / "force-with-hysteresis"
    _write_single_step_recording(rec_dir, pre_fill=(200, 200, 200))

    noisy = tmp_path / "n.png"
    Image.new("RGB", (64, 64), (10, 10, 10)).save(noisy)
    clean = tmp_path / "c.png"
    Image.new("RGB", (64, 64), (200, 200, 200)).save(clean)
    _install_alternating_observe(monkeypatch, [noisy, clean])

    monkeypatch.setattr(act, "tap", lambda *a, **kw: (0, 0))

    s = _make_session(tmp_path)

    caplog.clear()
    with caplog.at_level(logging.DEBUG, logger="simdrive.recorder"):
        result = rec_mod.replay(
            "force-with-hysteresis",
            s,
            on_drift="force",
            drift_threshold=0.85,
            halt_on_state_mismatch=False,
        )

    assert result["ok"] is True

    debug_records = [
        r for r in caplog.records
        if r.name == "simdrive.recorder" and r.message == "replay.ssim_compare"
    ]
    samples = sorted(getattr(r, "sample", None) for r in debug_records)
    assert samples == [1, 2]
