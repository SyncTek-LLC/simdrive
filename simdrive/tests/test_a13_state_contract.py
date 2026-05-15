"""a13 state contract tests.

Tests 11-15: replay-time verification of the a13 device requires block
(target, udid, device_name, os_version, app_bundle_id).

The a13 requires block has a different shape from the a9.0 RequiresBlock:
  - Flat keys: target, udid, device_name, os_version, app_bundle_id
  - NOT nested under app/sim/initial_state

These tests FAIL on feat/v17-claude-native (HEAD) because:
  - The replay engine uses the a9.0 RequiresBlock schema (app/sim/initial_state)
    and does not check target/udid.
  - replay_state_contract_failed error code does not exist.
  - OS version major mismatch is not detected in the existing verifier.
  - Minor OS version diff does not emit a WARNING and proceed.

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
                     app_bundle_id: str = "com.foo.app"):
    """Write a single-step recording with an a13-style requires block."""
    snaps = rec_dir / "snapshots"
    snaps.mkdir(parents=True, exist_ok=True)
    pre = snaps / "001_pre.png"
    post = snaps / "001_post.png"
    Image.new("RGB", (1170, 2532), (210, 210, 210)).save(pre)
    Image.new("RGB", (1170, 2532), (200, 200, 200)).save(post)

    payload = {
        "name": rec_dir.name,
        "created_at": 0.0,
        "device": device_name,
        "os_version": os_version,
        "app_bundle_id": app_bundle_id,
        "simdrive_version": "1.0.0a13",
        "requires": {
            "target": target,
            "udid": udid,
            "device_name": device_name,
            "os_version": os_version,
            "app_bundle_id": app_bundle_id,
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
    """Patch observe to return a matching screenshot (no SSIM drift)."""
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


def _patch_tap_noop(monkeypatch):
    from simdrive import act
    monkeypatch.setattr(act, "tap", lambda *a, **kw: None)


def _assert_contract_failed(result: dict, expected_field: str):
    """Assert that the replay result indicates a state contract failure mentioning field."""
    assert result.get("ok") is False, f"Expected state-contract failure, got ok=True: {result}"
    halt_reason = result.get("halt_reason", "")
    assert "contract" in halt_reason or "mismatch" in halt_reason or "state" in halt_reason, (
        f"halt_reason should indicate state contract failure: {halt_reason!r}"
    )
    # The field should appear somewhere in the result (reasons, details, or expected/actual)
    result_str = str(result)
    assert expected_field in result_str, (
        f"Expected field {expected_field!r} to appear in failure details: {result_str[:500]}"
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

    # Session is simulator (target mismatch)
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
    """Recording requires com.foo.app but session runs com.bar.app → halt."""
    from simdrive import recorder, errors as sd_errors

    monkeypatch.setenv("SIMDRIVE_HOME", str(tmp_path))

    rec_dir = recorder.recordings_root() / "bundle-mismatch"
    _write_recording(rec_dir, app_bundle_id="com.foo.app")

    _patch_observe_noop(monkeypatch, tmp_path)
    _patch_tap_noop(monkeypatch)

    s = _make_session(tmp_path, app_bundle_id="com.bar.app")

    try:
        result = recorder.replay("bundle-mismatch", s, on_drift="halt")
        _assert_contract_failed(result, "app_bundle_id")
    except sd_errors.SimdriveError as exc:
        assert exc.code in ("replay_state_contract_failed", "state_contract_mismatch"), (
            f"Wrong error code: {exc.code}"
        )
        details_str = str(exc.details or {})
        assert "app_bundle_id" in details_str or "bundle" in details_str, (
            f"app_bundle_id missing from error details: {details_str}"
        )


# ─── Test 13 ───────────────────────────────────────────────────────────────


def test_replay_halts_on_major_os_version_mismatch(tmp_path, monkeypatch):
    """Recording requires os_version='26.4.2', live device is '27.0.0' → halt."""
    from simdrive import recorder, errors as sd_errors

    monkeypatch.setenv("SIMDRIVE_HOME", str(tmp_path))

    rec_dir = recorder.recordings_root() / "os-major-mismatch"
    _write_recording(rec_dir, os_version="26.4.2")

    _patch_observe_noop(monkeypatch, tmp_path)
    _patch_tap_noop(monkeypatch)

    s = _make_session(tmp_path, os_version="27.0.0")

    try:
        result = recorder.replay("os-major-mismatch", s, on_drift="halt")
        _assert_contract_failed(result, "os_version")
    except sd_errors.SimdriveError as exc:
        assert exc.code in ("replay_state_contract_failed", "state_contract_mismatch"), (
            f"Wrong error code: {exc.code}"
        )


# ─── Test 14 ───────────────────────────────────────────────────────────────


def test_replay_warns_on_minor_os_version_diff(tmp_path, monkeypatch, caplog):
    """Recording os_version='26.4.2', live='26.4.3' → WARNING emitted, replay proceeds."""
    from simdrive import recorder

    monkeypatch.setenv("SIMDRIVE_HOME", str(tmp_path))

    rec_dir = recorder.recordings_root() / "os-minor-diff"
    _write_recording(rec_dir, os_version="26.4.2")

    _patch_observe_noop(monkeypatch, tmp_path)
    _patch_tap_noop(monkeypatch)

    s = _make_session(tmp_path, os_version="26.4.3")

    with caplog.at_level(logging.WARNING):
        try:
            result = recorder.replay("os-minor-diff", s, on_drift="force")
        except Exception as exc:
            # If it raises, that's the failure mode we're testing against — should NOT raise.
            pytest.fail(f"Minor OS version diff should warn, not raise: {exc}")

    # Should NOT be a failure
    assert result.get("ok") is True, (
        f"Minor OS diff should not fail replay: {result}"
    )
    # A warning must have been emitted (either via Python logging or in result)
    warning_in_log = any(
        "os" in r.message.lower() or "version" in r.message.lower()
        for r in caplog.records
        if r.levelno >= logging.WARNING
    )
    warning_in_result = "warn" in str(result).lower() or "_simdrive_warning" in result
    assert warning_in_log or warning_in_result, (
        f"Expected a WARNING about minor OS version diff. "
        f"log records: {[r.message for r in caplog.records]}, "
        f"result: {result}"
    )


# ─── Test 15 ───────────────────────────────────────────────────────────────


def test_replay_halts_when_udid_mismatches(tmp_path, monkeypatch):
    """Recording requires udid='DEVICE-UDID-001', session has different UDID → halt."""
    from simdrive import recorder, errors as sd_errors

    monkeypatch.setenv("SIMDRIVE_HOME", str(tmp_path))

    rec_dir = recorder.recordings_root() / "udid-mismatch"
    _write_recording(rec_dir, udid="DEVICE-UDID-001")

    _patch_observe_noop(monkeypatch, tmp_path)
    _patch_tap_noop(monkeypatch)

    # Session uses a different UDID
    s = _make_session(tmp_path, udid="DEVICE-UDID-999")

    try:
        result = recorder.replay("udid-mismatch", s, on_drift="halt")
        _assert_contract_failed(result, "udid")
    except sd_errors.SimdriveError as exc:
        assert exc.code in ("replay_state_contract_failed", "state_contract_mismatch"), (
            f"Wrong error code: {exc.code}"
        )
        details_str = str(exc.details or {})
        assert "udid" in details_str, f"udid missing from error details: {details_str}"
