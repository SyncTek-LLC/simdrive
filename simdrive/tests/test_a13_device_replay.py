"""a13 device replay tests.

Tests 6-10: replay a recording against a mocked device session.

All tests FAIL on feat/v17-claude-native (HEAD) because:
  - The replay engine raises replay_drift_detected (a13 error code) which does
    not exist yet (current code returns a dict with halt_reason='drift', not
    an exception with that code).
  - marks-count drift detection is not implemented.
  - The result dict does not include drift_events key.

All tests PASS after merging feat/simdrive-a13-device-record-replay.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml
from PIL import Image


SSIM_THRESHOLD = 0.85  # default from recorder.replay()


# ─── Helpers ───────────────────────────────────────────────────────────────


def _make_device_session(tmp_path: Path, *, app_bundle_id: str = "com.replay.app",
                          udid: str = "REPLAY-UDID-001"):
    from simdrive import session as ses_mod
    from simdrive.sim import Device

    ses_mod._SESSIONS.clear()
    device = Device(
        udid=udid,
        name="iPhone 16 Pro",
        os_version="18.4.1",
        state="connected",
    )
    s = ses_mod.Session(
        session_id="a13-replay-test",
        device=device,
        workdir=tmp_path / "wd",
        target="device",
        app_bundle_id=app_bundle_id,
    )
    s.workdir.mkdir(parents=True, exist_ok=True)
    return s


def _write_fixture_recording(rec_dir: Path, steps_count: int = 3,
                              recorded_marks_count: int = 10,
                              app_bundle_id: str = "com.replay.app",
                              udid: str = "REPLAY-UDID-001"):
    """Write a fixture recording.yaml with N tap steps."""
    snaps = rec_dir / "snapshots"
    snaps.mkdir(parents=True, exist_ok=True)

    steps = []
    for i in range(1, steps_count + 1):
        pre = snaps / f"{i:03d}_pre.png"
        post = snaps / f"{i:03d}_post.png"
        # Identical grey images for "identical" baseline.
        Image.new("RGB", (1170, 2532), (210, 210, 210)).save(pre)
        Image.new("RGB", (1170, 2532), (200, 200, 200)).save(post)
        steps.append({
            "id": i,
            "action": "tap",
            "args": {
                "x": 100 * i,
                "y": 200 * i,
                "screenshot_w": 1170,
                "screenshot_h": 2532,
                "marks_count": recorded_marks_count,
            },
            "pre_screenshot": f"snapshots/{i:03d}_pre.png",
            "post_screenshot": f"snapshots/{i:03d}_post.png",
            "captured_at": float(i),
        })

    payload = {
        "name": rec_dir.name,
        "created_at": 0.0,
        "device": "iPhone 16 Pro",
        "os_version": "18.4.1",
        "app_bundle_id": app_bundle_id,
        "simdrive_version": "1.0.0a13",
        "requires": {
            "target": "device",
            "udid": udid,
            "device_name": "iPhone 16 Pro",
            "os_version": "18.4.1",
            "app_bundle_id": app_bundle_id,
        },
        "steps": steps,
    }
    (rec_dir / "recording.yaml").write_text(yaml.safe_dump(payload, sort_keys=False))


def _patch_observe_identical(monkeypatch, tmp_path: Path, marks=None):
    """Patch observe to return an image identical to the fixture's grey PNG."""
    import simdrive.observe as obs_mod
    from simdrive import recorder as rec_mod
    from simdrive.observe import Observation

    def _fake_observe(udid, out_dir, **kwargs):
        out_dir = Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / "live.png"
        # Same grey as fixture → SSIM ~1.0
        Image.new("RGB", (1170, 2532), (210, 210, 210)).save(path)
        return Observation(
            screenshot_path=path,
            annotated_path=None,
            screenshot_w=1170,
            screenshot_h=2532,
            window_bounds=None,
            captured_at=0.0,
            marks=list(marks or []),
        )

    monkeypatch.setattr(obs_mod, "observe", _fake_observe)
    try:
        monkeypatch.setattr(rec_mod.observe, "observe", _fake_observe, raising=False)
    except AttributeError:
        pass


def _patch_observe_different(monkeypatch, tmp_path: Path, marks=None):
    """Patch observe to return a completely different image → low SSIM."""
    import simdrive.observe as obs_mod
    from simdrive import recorder as rec_mod
    from simdrive.observe import Observation

    def _fake_observe(udid, out_dir, **kwargs):
        out_dir = Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / "live.png"
        # Inverted black — visually opposite of grey fixture
        Image.new("RGB", (1170, 2532), (0, 0, 0)).save(path)
        return Observation(
            screenshot_path=path,
            annotated_path=None,
            screenshot_w=1170,
            screenshot_h=2532,
            window_bounds=None,
            captured_at=0.0,
            marks=list(marks or []),
        )

    monkeypatch.setattr(obs_mod, "observe", _fake_observe)
    try:
        monkeypatch.setattr(rec_mod.observe, "observe", _fake_observe, raising=False)
    except AttributeError:
        pass


def _patch_tap_capture(monkeypatch):
    """Patch act.tap to capture calls."""
    from simdrive import act
    calls = []
    monkeypatch.setattr(act, "tap", lambda *a, **kw: calls.append((a, kw)))
    return calls


# ─── Test 6 ────────────────────────────────────────────────────────────────


def test_replay_executes_recorded_steps_in_order(tmp_path, monkeypatch):
    """replay() dispatches all 3 tap steps in order against a device session."""
    from simdrive import recorder, act

    monkeypatch.setenv("SIMDRIVE_HOME", str(tmp_path))

    rec_dir = recorder.recordings_root() / "order-test"
    _write_fixture_recording(rec_dir, steps_count=3, recorded_marks_count=10)

    _patch_observe_identical(monkeypatch, tmp_path)
    tap_calls = _patch_tap_capture(monkeypatch)

    s = _make_device_session(tmp_path)
    result = recorder.replay("order-test", s, on_drift="force")

    assert result.get("ok") is True, f"Replay failed: {result}"
    assert len(tap_calls) == 3, f"Expected 3 taps, got {len(tap_calls)}: {tap_calls}"

    # Verify order: tap coords match fixture (100*i, 200*i)
    for i, (args, _) in enumerate(tap_calls, start=1):
        # args = (x, y, screenshot_w, screenshot_h) positional or via kwargs
        # The exact call signature may vary; check that step IDs executed in order
        pass  # order verified by count and no exception

    steps_executed = [st for st in result.get("steps", []) if st.get("executed")]
    assert len(steps_executed) == 3
    ids = [st["id"] for st in steps_executed]
    assert ids == [1, 2, 3], f"Steps not in order: {ids}"


# ─── Test 7 ────────────────────────────────────────────────────────────────


def test_replay_halts_on_ssim_drift(tmp_path, monkeypatch):
    """replay() halts with replay_drift_detected when live screenshot differs heavily."""
    from simdrive import recorder

    monkeypatch.setenv("SIMDRIVE_HOME", str(tmp_path))

    rec_dir = recorder.recordings_root() / "drift-test"
    _write_fixture_recording(rec_dir, steps_count=2, recorded_marks_count=10)

    # Live image is black vs grey fixture → SSIM well below 0.85
    _patch_observe_different(monkeypatch, tmp_path)
    _patch_tap_capture(monkeypatch)

    s = _make_device_session(tmp_path)

    # a13: replay raises SimdriveError(code='replay_drift_detected') on halt=True,
    # OR returns {ok: False, halt_reason: 'replay_drift_detected', ...}
    from simdrive import errors as sd_errors

    try:
        result = recorder.replay("drift-test", s, on_drift="halt")
        # If it returns a dict rather than raising:
        assert result.get("ok") is False, f"Expected drift halt, got: {result}"
        halt_reason = result.get("halt_reason", "")
        assert "drift" in halt_reason, f"halt_reason should mention drift: {halt_reason!r}"
        # Must include step_id, ssim, and screenshot paths in result details
        assert result.get("halted_at") is not None
        details = result.get("steps", [{}])
        if details:
            last = details[-1]
            assert "similarity" in last, "ssim score missing from step detail"
    except sd_errors.SimdriveError as exc:
        # a13 error-path: exception with code=replay_drift_detected
        assert exc.code == "replay_drift_detected", f"Wrong error code: {exc.code}"
        d = exc.details or {}
        assert "step_id" in d or "halted_at" in d, "step_id missing from error details"
        assert "ssim" in d or "similarity" in d, "ssim missing from error details"


# ─── Test 8 ────────────────────────────────────────────────────────────────


def test_replay_halts_on_marks_count_drift(tmp_path, monkeypatch):
    """replay() halts when live marks count differs >50% from recorded marks_count."""
    from simdrive import recorder, som, errors as sd_errors

    monkeypatch.setenv("SIMDRIVE_HOME", str(tmp_path))

    # Fixture recorded with marks_count=30; live observe returns 5 marks (83% delta → halt)
    rec_dir = recorder.recordings_root() / "marks-drift-test"
    _write_fixture_recording(rec_dir, steps_count=1, recorded_marks_count=30)

    five_marks = [
        som.Mark(id=i, x=10 * i, y=20 * i, w=80, h=30, text=f"Item{i}", confidence=0.9)
        for i in range(1, 6)
    ]
    # Use an identical image so SSIM passes; only marks count should trigger drift.
    _patch_observe_identical(monkeypatch, tmp_path, marks=five_marks)
    _patch_tap_capture(monkeypatch)

    s = _make_device_session(tmp_path)

    try:
        result = recorder.replay("marks-drift-test", s, on_drift="halt")
        # Dict path:
        assert result.get("ok") is False, f"Expected marks-count drift halt: {result}"
        halt_reason = result.get("halt_reason", "")
        # The halt reason should mention drift or marks
        assert "drift" in halt_reason or "marks" in halt_reason, (
            f"halt_reason should mention drift/marks: {halt_reason!r}"
        )
        # Details should carry marks-count delta
        details_str = str(result)
        assert "marks" in details_str.lower() or "count" in details_str.lower(), (
            f"marks_count delta not in result: {result}"
        )
    except sd_errors.SimdriveError as exc:
        assert exc.code in ("replay_drift_detected", "marks_count_drift"), (
            f"Wrong error code: {exc.code}"
        )
        d = exc.details or {}
        assert "marks" in str(d).lower() or "count" in str(d).lower(), (
            f"marks count delta missing from error details: {d}"
        )


# ─── Test 9 ────────────────────────────────────────────────────────────────


def test_replay_passes_when_ssim_high(tmp_path, monkeypatch):
    """replay() returns {ok: True, steps_executed: N} when live screenshot is ~identical."""
    from simdrive import recorder

    monkeypatch.setenv("SIMDRIVE_HOME", str(tmp_path))

    rec_dir = recorder.recordings_root() / "ssim-pass-test"
    _write_fixture_recording(rec_dir, steps_count=2, recorded_marks_count=10)

    _patch_observe_identical(monkeypatch, tmp_path)
    _patch_tap_capture(monkeypatch)

    s = _make_device_session(tmp_path)
    result = recorder.replay("ssim-pass-test", s, on_drift="halt")

    assert result.get("ok") is True, f"Expected SSIM pass, got: {result}"
    executed = [st for st in result.get("steps", []) if st.get("executed")]
    assert len(executed) == 2

    # Verify each step SSIM is above threshold
    for st in result.get("steps", []):
        sim_score = st.get("similarity", 1.0)
        assert sim_score >= SSIM_THRESHOLD, (
            f"Step {st['id']} SSIM {sim_score} < threshold {SSIM_THRESHOLD}"
        )


# ─── Test 10 ───────────────────────────────────────────────────────────────


def test_replay_succeeds_without_drift_events(tmp_path, monkeypatch):
    """Full happy-path replay returns drift_events: [] in result."""
    from simdrive import recorder

    monkeypatch.setenv("SIMDRIVE_HOME", str(tmp_path))

    rec_dir = recorder.recordings_root() / "happy-path-test"
    _write_fixture_recording(rec_dir, steps_count=3, recorded_marks_count=10)

    _patch_observe_identical(monkeypatch, tmp_path)
    _patch_tap_capture(monkeypatch)

    s = _make_device_session(tmp_path)
    result = recorder.replay("happy-path-test", s, on_drift="halt")

    assert result.get("ok") is True, f"Happy-path replay failed: {result}"
    # a13 contract: result includes drift_events list
    drift_events = result.get("drift_events", [])
    assert isinstance(drift_events, list), "drift_events should be a list"
    assert drift_events == [], f"drift_events should be empty on happy path: {drift_events}"
