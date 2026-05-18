"""a13 state contract tests.

Tests 11-15: replay-time verification of the a13 device requires block.

The a13 requires block uses a nested schema:
  requires:
    target: "simulator" | "device"
    app:
      bundle_id: <str>
      version_match: minor
    device:                       # only for target="device"
      udid: <str>
      device_name: <str>
      os_version: <str>
      os_major: <int>
    sim:                          # for sim recordings
      device: <str>
      ios_version: <str>
    initial_state:
      foreground: <bool>
      ...

Verification rules (a13 CodeAtlas implementation):
  - target mismatch → halt (replay_state_contract_failed in reasons)
  - app.bundle_id mismatch → halt (existing a9 behavior)
  - device.os_major mismatch → halt (new a13 behavior)
  - device.os_version minor diff (same major) → WARNING, proceed
  - device.udid mismatch → stored in block, verified if matching block.device.udid
    against session.device.udid (a13 implementation — halts when UDID differs)

These tests FAIL on feat/v17-claude-native (HEAD) because:
  - The requires block has no target/device fields.
  - target mismatch is not detected.
  - bundle_id check uses a9.0 foreground-only observe path (sees False, not bundle mismatch).
  - os_major mismatch is not detected.
  - Minor OS diff still triggers state_contract_mismatch (no warn+proceed logic).
  - UDID is not verified.

All tests PASS after merging feat/simdrive-a13-device-record-replay.
"""
from __future__ import annotations

import logging
from pathlib import Path

import pytest
import yaml
from PIL import Image


# ─── Helpers ───────────────────────────────────────────────────────────────


def _make_session(tmp_path: Path, *,
                   target: str = "device",
                   udid: str = "DEVICE-UDID-001",
                   device_name: str = "iPhone 16 Pro",
                   os_version: str = "18.4.1",
                   app_bundle_id: str = "com.foo.app"):
    from simdrive import session as ses_mod
    from simdrive.sim import Device

    ses_mod._SESSIONS.clear()
    device = Device(
        udid=udid,
        name=device_name,
        os_version=os_version,
        state="connected" if target == "device" else "Booted",
    )
    s = ses_mod.Session(
        session_id="a13-sc-test",
        device=device,
        workdir=tmp_path / "wd",
        target=target,
        app_bundle_id=app_bundle_id,
    )
    s.workdir.mkdir(parents=True, exist_ok=True)
    return s


def _write_recording(rec_dir: Path, *,
                     target: str = "device",
                     udid: str = "DEVICE-UDID-001",
                     device_name: str = "iPhone 16 Pro",
                     os_version: str = "18.4.1",
                     os_major: int = 18,
                     app_bundle_id: str = "com.foo.app"):
    """Write a single-step recording with an a13-style nested requires block."""
    snaps = rec_dir / "snapshots"
    snaps.mkdir(parents=True, exist_ok=True)
    pre = snaps / "001_pre.png"
    post = snaps / "001_post.png"
    Image.new("RGB", (1170, 2532), (210, 210, 210)).save(pre)
    Image.new("RGB", (1170, 2532), (200, 200, 200)).save(post)

    # a13 nested requires block (matches CodeAtlas DeviceRequires schema)
    payload = {
        "name": rec_dir.name,
        "created_at": 0.0,
        "target": target,
        "device": device_name,
        "os_version": os_version,
        "app_bundle_id": app_bundle_id,
        "simdrive_version": "1.0.0a13",
        "requires": {
            "target": target,
            "app": {
                "bundle_id": app_bundle_id,
                "version": None,
                "version_match": "minor",
            },
            "sim": {"device": None, "ios_version": None},
            "device": {
                "udid": udid,
                "device_name": device_name,
                "os_version": os_version,
                "os_major": os_major,
            },
            "initial_state": {
                # foreground=False: don't require live marks so the state contract
                # check passes without a real observe call (returns empty marks → False).
                "foreground": False,
                "text_subset_required": [],
                "text_subset_forbidden": [],
                "primary_button_label": None,
            },
        },
        "steps": [{
            "id": 1,
            "action": "tap",
            "args": {"x": 100, "y": 200, "screenshot_w": 1170, "screenshot_h": 2532},
            "pre_screenshot": "snapshots/001_pre.png",
            "post_screenshot": "snapshots/001_post.png",
            "captured_at": 0.0,
        }],
    }
    (rec_dir / "recording.yaml").write_text(yaml.safe_dump(payload, sort_keys=False))


def _patch_observe_noop(monkeypatch, tmp_path: Path):
    """Patch observe + a13 internal helpers to return empty marks (no WDA required)."""
    import simdrive.observe as obs_mod
    from simdrive import recorder as rec_mod
    from simdrive.observe import Observation

    def _fake_observe(udid, out_dir, **kwargs):
        out_dir = Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / "live.png"
        Image.new("RGB", (1170, 2532), (210, 210, 210)).save(path)
        return Observation(
            screenshot_path=path,
            annotated_path=None,
            screenshot_w=1170,
            screenshot_h=2532,
            window_bounds=None,
            captured_at=0.0,
            marks=[],
        )

    monkeypatch.setattr(obs_mod, "observe", _fake_observe)
    try:
        monkeypatch.setattr(rec_mod.observe, "observe", _fake_observe, raising=False)
    except AttributeError:
        pass

    # a13: _observe_live_marks is called by _verify_state_contract for initial_state checks.
    # Return empty list to bypass WDA requirement in device sessions.
    try:
        monkeypatch.setattr(rec_mod, "_observe_live_marks", lambda session, workdir: [],
                            raising=False)
    except AttributeError:
        pass


def _patch_tap_noop(monkeypatch):
    from simdrive import act
    monkeypatch.setattr(act, "tap", lambda *a, **kw: None)


def _assert_contract_failed(result: dict, *expected_fields: str):
    """Assert replay result indicates a state contract failure."""
    assert result.get("ok") is False, (
        f"Expected state-contract failure, got ok=True: {result}"
    )
    halt_reason = result.get("halt_reason", "")
    assert "contract" in halt_reason or "mismatch" in halt_reason or "state" in halt_reason, (
        f"halt_reason should indicate state contract failure: {halt_reason!r}"
    )
    result_str = str(result)
    for field in expected_fields:
        assert field in result_str, (
            f"Expected field {field!r} to appear in failure details: {result_str[:500]}"
        )


# ─── Test 11 ───────────────────────────────────────────────────────────────


def test_replay_halts_when_target_mismatches(tmp_path, monkeypatch):
    """Recording requires target='device' but replay session is simulator → halt."""
    from simdrive import recorder, errors as sd_errors

    monkeypatch.setenv("SIMDRIVE_HOME", str(tmp_path))

    rec_dir = recorder.recordings_root() / "target-mismatch"
    _write_recording(rec_dir, target="device")

    _patch_observe_noop(monkeypatch, tmp_path)
    _patch_tap_noop(monkeypatch)

    # Session is simulator — target mismatch with recording
    s = _make_session(tmp_path, target="simulator")

    try:
        result = recorder.replay("target-mismatch", s, on_drift="halt")
        _assert_contract_failed(result, "target")
        assert result.get("halted_at") == 0, "Should halt before any step (halted_at=0)"
    except sd_errors.SimdriveError as exc:
        assert exc.code in ("replay_state_contract_failed", "state_contract_mismatch"), (
            f"Wrong error code: {exc.code}"
        )
        details_str = str(exc.details or {})
        assert "target" in details_str, f"'target' missing from error details: {details_str}"


# ─── Test 12 ───────────────────────────────────────────────────────────────


def test_replay_halts_when_app_bundle_mismatches(tmp_path, monkeypatch):
    """Recording requires app.bundle_id='com.foo.app' but session runs 'com.bar.app' → halt."""
    from simdrive import recorder, errors as sd_errors

    monkeypatch.setenv("SIMDRIVE_HOME", str(tmp_path))

    rec_dir = recorder.recordings_root() / "bundle-mismatch"
    _write_recording(rec_dir, app_bundle_id="com.foo.app")

    _patch_observe_noop(monkeypatch, tmp_path)
    _patch_tap_noop(monkeypatch)

    # Session has different bundle ID
    s = _make_session(tmp_path, app_bundle_id="com.bar.app")

    try:
        result = recorder.replay("bundle-mismatch", s, on_drift="halt")
        # May fail due to bundle or observe — either way it should indicate mismatch
        assert result.get("ok") is False, (
            f"Expected failure on app_bundle_id mismatch: {result}"
        )
        # Verify the halt is related to state contract, not SSIM drift
        halt_reason = result.get("halt_reason", "")
        assert "mismatch" in halt_reason or "contract" in halt_reason or "drift" not in halt_reason, (
            f"Expected state contract failure, not SSIM drift: {halt_reason!r}"
        )
    except sd_errors.SimdriveError as exc:
        assert exc.code in ("replay_state_contract_failed", "state_contract_mismatch"), (
            f"Wrong error code: {exc.code}"
        )


# ─── Test 13 ───────────────────────────────────────────────────────────────


def test_replay_halts_on_major_os_version_mismatch(tmp_path, monkeypatch):
    """Recording requires device.os_major=26, live device is 27 → halt."""
    from simdrive import recorder, errors as sd_errors

    monkeypatch.setenv("SIMDRIVE_HOME", str(tmp_path))

    rec_dir = recorder.recordings_root() / "os-major-mismatch"
    # Recording captured on iOS 26.4.2 (os_major=26)
    _write_recording(rec_dir, os_version="26.4.2", os_major=26)

    _patch_observe_noop(monkeypatch, tmp_path)
    _patch_tap_noop(monkeypatch)

    # Live device is running iOS 27.0.0 (major mismatch)
    s = _make_session(tmp_path, os_version="27.0.0")

    try:
        result = recorder.replay("os-major-mismatch", s, on_drift="halt")
        assert result.get("ok") is False, (
            f"Expected major OS version mismatch halt: {result}"
        )
        # Should halt before step 1
        assert result.get("halted_at") == 0, (
            f"Expected halted_at=0, got: {result.get('halted_at')}"
        )
    except sd_errors.SimdriveError as exc:
        assert exc.code in ("replay_state_contract_failed", "state_contract_mismatch"), (
            f"Wrong error code: {exc.code}"
        )


# ─── Test 14 ───────────────────────────────────────────────────────────────


def test_replay_warns_on_minor_os_version_diff(tmp_path, monkeypatch, caplog):
    """Recording os_version='26.4.2', live='26.4.3' (same major=26) → WARNING + proceed."""
    from simdrive import recorder

    monkeypatch.setenv("SIMDRIVE_HOME", str(tmp_path))

    rec_dir = recorder.recordings_root() / "os-minor-diff"
    # Recording captured on iOS 26.4.2
    _write_recording(rec_dir, os_version="26.4.2", os_major=26)

    _patch_observe_noop(monkeypatch, tmp_path)
    _patch_tap_noop(monkeypatch)

    # Live device is 26.4.3 — same major, different patch
    s = _make_session(tmp_path, os_version="26.4.3")

    with caplog.at_level(logging.WARNING):
        try:
            result = recorder.replay("os-minor-diff", s, on_drift="force")
        except Exception as exc:
            pytest.fail(f"Minor OS version diff should warn, not raise: {exc}")

    # Should proceed (not fail)
    assert result.get("ok") is True, (
        f"Minor OS diff should not fail replay: {result}"
    )
    # A warning must have been emitted (via logging OR surfaced in the result dict)
    # a13 implementation: warning may appear in _simdrive_warning, result warnings list,
    # or Python logging depending on implementation choice.
    result_str = str(result)
    warning_in_log = any(
        "os" in r.message.lower() or "version" in r.message.lower() or "minor" in r.message.lower()
        for r in caplog.records
        if r.levelno >= logging.WARNING
    )
    warning_in_result = (
        "_simdrive_warning" in result
        or "warnings" in result
        or "minor" in result_str.lower()
        or "os_version" in result_str.lower()
    )
    assert warning_in_log or warning_in_result, (
        f"Expected a WARNING about minor OS version diff. "
        f"log records (WARNING+): {[r.message for r in caplog.records if r.levelno >= logging.WARNING]}, "
        f"result: {result}"
    )


# ─── Test 15 ───────────────────────────────────────────────────────────────


def test_replay_halts_when_udid_mismatches(tmp_path, monkeypatch):
    """Recording captured on DEVICE-UDID-001, session has different UDID → halt.

    The a13 requires.device.udid field stores the capture-time UDID. When
    replaying against a different device, the state contract check should halt.
    This enforces that recordings are played back on the same physical device
    (or a deliberately matched replacement).
    """
    from simdrive import recorder, errors as sd_errors

    monkeypatch.setenv("SIMDRIVE_HOME", str(tmp_path))

    rec_dir = recorder.recordings_root() / "udid-mismatch"
    _write_recording(rec_dir, udid="DEVICE-UDID-001")

    _patch_observe_noop(monkeypatch, tmp_path)
    _patch_tap_noop(monkeypatch)

    # Session uses a different UDID — completely different physical device
    s = _make_session(tmp_path, udid="DEVICE-UDID-999")

    try:
        result = recorder.replay("udid-mismatch", s, on_drift="halt")
        # a13: UDID mismatch should halt before any step
        assert result.get("ok") is False, (
            f"Expected UDID mismatch to halt replay: {result}"
        )
        assert result.get("halted_at") == 0, (
            f"Expected halted_at=0 for UDID mismatch: {result}"
        )
        # The failure details must reference udid
        result_str = str(result)
        assert "udid" in result_str.lower(), (
            f"'udid' should appear in failure details: {result_str[:500]}"
        )
    except sd_errors.SimdriveError as exc:
        assert exc.code in ("replay_state_contract_failed", "state_contract_mismatch"), (
            f"Wrong error code: {exc.code}"
        )
        details_str = str(exc.details or {}).lower()
        assert "udid" in details_str, f"udid missing from error details: {details_str}"
