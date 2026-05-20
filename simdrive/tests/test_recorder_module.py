"""Unit tests for less-exercised paths in ``simdrive.recorder``.

Targets the migration, lint, partial-recording, requires-block,
semver/version-matching, and pure helper functions. The replay engine
itself has substantial integration coverage in test_recorder_integrity.py
and test_state_contract.py — this file fills in the remaining unit-level
gaps to push recorder.py above the 80% line-coverage floor.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
import yaml
from PIL import Image

from simdrive import recorder, session as ses
from simdrive.recorder import (
    AppRequires, DeviceRequires, InitialStateRequires,
    LintResult, MigrationError, MigrationResult, Recorder, RequiresBlock,
    SimRequires, _block_similarity, _build_requires_block,
    _ios_version_matches, _normalize_masks, _semver_tuple,
    _split_semver_predicate, _version_matches,
)
from simdrive.sim import Device
from simdrive.som import Mark


# ── _normalize_masks ────────────────────────────────────────────────────────


def test_normalize_masks_none_returns_none():
    assert _normalize_masks(None) is None
    assert _normalize_masks([]) is None


def test_normalize_masks_dict_form():
    out = _normalize_masks([{"x": 1, "y": 2, "w": 3, "h": 4}])
    assert out == [(1, 2, 3, 4)]


def test_normalize_masks_tuple_form():
    out = _normalize_masks([(5, 6, 7, 8), [9, 10, 11, 12]])
    assert out == [(5, 6, 7, 8), (9, 10, 11, 12)]


# ── semver helpers ──────────────────────────────────────────────────────────


def test_split_semver_predicate_with_operator():
    assert _split_semver_predicate(">=18.0") == (">=", "18.0")
    assert _split_semver_predicate("<26.4") == ("<", "26.4")
    assert _split_semver_predicate("== 1.0") == ("==", "1.0")


def test_split_semver_predicate_no_operator():
    assert _split_semver_predicate("18.0") == ("==", "18.0")


def test_semver_tuple_pure_numeric():
    assert _semver_tuple("18.0.3") == (18, 0, 3)


def test_semver_tuple_stops_at_nonnumeric():
    # "0-beta" isn't parseable as int, so iteration stops at the first
    # non-numeric chunk — yielding (18,) for "18.0-beta".
    assert _semver_tuple("18.0-beta") == (18,)


def test_semver_tuple_fallback_empty():
    assert _semver_tuple("abc") == (0,)


def test_ios_version_matches_eq():
    assert _ios_version_matches("18.0", "18.0") is True
    assert _ios_version_matches("18.0", "18.0.0") is True


def test_ios_version_matches_ge():
    assert _ios_version_matches(">=18.0", "18.4") is True
    assert _ios_version_matches(">=18.0", "17.4") is False


def test_ios_version_matches_lt():
    assert _ios_version_matches("<18.0", "17.5") is True
    assert _ios_version_matches("<18.0", "18.0") is False


def test_ios_version_matches_gt_and_le():
    assert _ios_version_matches(">18.0", "18.1") is True
    assert _ios_version_matches("<=18.0", "18.0") is True


def test_ios_version_matches_ne():
    assert _ios_version_matches("!=18.0", "18.1") is True
    assert _ios_version_matches("!=18.0", "18.0") is False


def test_version_matches_any():
    assert _version_matches("any", "1.0", "9.9") is True
    assert _version_matches("minor", None, "9.9") is True


def test_version_matches_actual_none():
    assert _version_matches("exact", "1.0", None) is False


def test_version_matches_exact():
    assert _version_matches("exact", "1.0", "1.0") is True
    assert _version_matches("exact", "1.0", "1.0.0") is False


def test_version_matches_major():
    assert _version_matches("major", "1.4", "1.9") is True
    assert _version_matches("major", "1.4", "2.0") is False


def test_version_matches_minor_default():
    assert _version_matches("minor", "1.4", "1.4.99") is True
    assert _version_matches("minor", "1.4", "1.5.0") is False


# ── from_dict variants on requires sub-blocks ───────────────────────────────


def test_app_requires_from_dict_unknown_mode_defaults_to_minor():
    a = AppRequires.from_dict({"bundle_id": "x", "version": "1.0", "version_match": "bogus"})
    assert a.version_match == "minor"


def test_app_requires_from_dict_non_mapping():
    assert AppRequires.from_dict("not a dict").bundle_id is None


def test_sim_requires_from_dict_non_mapping():
    assert SimRequires.from_dict(None).device is None


def test_device_requires_from_dict_round_trip():
    d = DeviceRequires(udid="U", device_name="iPad", os_version="26.4.2", os_major=26)
    out = DeviceRequires.from_dict(d.to_dict())
    assert out.udid == "U"
    assert out.os_major == 26


def test_device_requires_from_dict_non_mapping():
    assert DeviceRequires.from_dict([]).udid is None


def test_initial_state_requires_defaults_when_non_mapping():
    s = InitialStateRequires.from_dict("garbage")
    assert s.foreground is True


def test_requires_block_from_dict_non_mapping_returns_none():
    assert RequiresBlock.from_dict(None) is None
    assert RequiresBlock.from_dict("garbage") is None


def test_requires_block_from_dict_flat_device_format():
    """Flat format with target=device + top-level udid/device_name should promote."""
    block = RequiresBlock.from_dict({
        "target": "device",
        "udid": "U1",
        "device_name": "iPad",
        "os_version": "26.4.2",
        "app_bundle_id": "com.example.App",
    })
    assert block is not None
    assert block.target == "device"
    assert block.device is not None
    assert block.device.udid == "U1"
    assert block.device.os_major == 26
    assert block.app.bundle_id == "com.example.App"


def test_requires_block_from_dict_flat_device_invalid_os_version():
    """A non-int os major shouldn't raise; os_major stays None."""
    block = RequiresBlock.from_dict({
        "target": "device",
        "udid": "U",
        "device_name": "iPad",
        "os_version": "garbage",
    })
    assert block is not None
    assert block.device.os_major is None


def test_requires_block_round_trip():
    rb = RequiresBlock(
        app=AppRequires(bundle_id="com.example.App"),
        sim=SimRequires(device="iPhone 17"),
        initial_state=InitialStateRequires(foreground=True),
        target="simulator",
    )
    d = rb.to_dict()
    rb2 = RequiresBlock.from_dict(d)
    assert rb2 is not None
    assert rb2.target == "simulator"
    assert rb2.app.bundle_id == "com.example.App"


def test_requires_block_round_trip_device():
    rb = RequiresBlock(
        app=AppRequires(bundle_id="com.example.App"),
        sim=SimRequires(),
        initial_state=InitialStateRequires(foreground=False),
        target="device",
        device=DeviceRequires(udid="U", device_name="iPad", os_version="26.4", os_major=26),
    )
    d = rb.to_dict()
    assert "device" in d
    rb2 = RequiresBlock.from_dict(d)
    assert rb2.device.udid == "U"


# ── _build_requires_block ────────────────────────────────────────────────────


def test_build_requires_block_picks_primary_label_from_upper_half():
    """The largest mark in the upper half of the screen becomes the primary label."""
    marks = [
        Mark(id=1, x=0, y=10, w=100, h=20, text="Hello World", confidence=0.95),
        Mark(id=2, x=0, y=500, w=200, h=40, text="Footer", confidence=0.95),
    ]
    block = _build_requires_block(
        marks,
        screen_h=600,
        app_bundle_id="com.example.App",
        app_version="1.0",
        sim_device="iPhone 17",
        sim_ios_version="26.3",
    )
    # Upper-half mark is "Hello World".
    assert block.initial_state.primary_button_label == "Hello World"


def test_build_requires_block_empty_marks():
    block = _build_requires_block(
        [], screen_h=600, app_bundle_id="x", app_version="1.0",
        sim_device="iPhone", sim_ios_version="26.3",
    )
    assert block.initial_state.foreground is False
    assert block.initial_state.text_subset_required == []
    assert block.initial_state.primary_button_label is None


def test_build_requires_block_device_target_populates_device():
    block = _build_requires_block(
        [], screen_h=None,
        app_bundle_id="x", app_version=None,
        sim_device=None, sim_ios_version=None,
        target="device",
        device_udid="U1", device_name="iPad", device_os_version="26.4.2",
    )
    assert block.target == "device"
    assert block.device is not None
    assert block.device.os_major == 26
    assert block.sim.device is None  # Sim block empty for device recordings


def test_build_requires_block_device_invalid_os_version():
    block = _build_requires_block(
        [], screen_h=None,
        app_bundle_id="x", app_version=None,
        sim_device=None, sim_ios_version=None,
        target="device",
        device_udid="U1", device_name="iPad", device_os_version="not-a-version",
    )
    assert block.device.os_major is None


def test_build_requires_block_with_dict_marks():
    """Device path delivers marks as dicts — _build_requires_block must handle them."""
    marks = [
        {"x": 0, "y": 10, "w": 100, "h": 20, "text": "Login", "confidence_band": "high"},
        {"x": 0, "y": 50, "w": 80, "h": 20, "text": "Cancel", "confidence_band": "medium"},
        {"x": 0, "y": 90, "w": 60, "h": 20, "text": "X", "confidence_band": "high"},  # too short
    ]
    block = _build_requires_block(
        marks, screen_h=600,
        app_bundle_id=None, app_version=None,
        sim_device=None, sim_ios_version=None,
        target="device",
    )
    assert "Login" in block.initial_state.text_subset_required
    assert "Cancel" in block.initial_state.text_subset_required
    # 1-char "X" is skipped (len < 2)
    assert "X" not in block.initial_state.text_subset_required


def test_build_requires_block_skips_low_confidence():
    # Use real English words so the dictionary-gated confidence_band promotes
    # the high-confidence mark to "high" instead of being demoted to "low".
    marks = [
        Mark(id=1, x=0, y=10, w=100, h=20, text="xkqjxz", confidence=0.20),  # non-English -> low
        Mark(id=2, x=0, y=10, w=100, h=20, text="Hello", confidence=0.95),
    ]
    block = _build_requires_block(
        marks, screen_h=600,
        app_bundle_id=None, app_version=None,
        sim_device="iPhone", sim_ios_version="26.3",
    )
    assert "xkqjxz" not in block.initial_state.text_subset_required
    assert "Hello" in block.initial_state.text_subset_required


def test_build_requires_block_cap_at_10_required_texts():
    # 15 real English words to ensure each passes the dictionary fence and lands
    # in the high/medium confidence bands.
    english_words = [
        "Login", "Hello", "World", "Settings", "Account",
        "Submit", "Cancel", "Continue", "Search", "Profile",
        "Logout", "Welcome", "Update", "Refresh", "Notify",
    ]
    marks = [
        Mark(id=i, x=0, y=i * 10, w=100, h=20, text=t, confidence=0.95)
        for i, t in enumerate(english_words)
    ]
    block = _build_requires_block(
        marks, screen_h=600,
        app_bundle_id=None, app_version=None,
        sim_device="iPhone", sim_ios_version="26.3",
    )
    assert len(block.initial_state.text_subset_required) == 10


# ── _block_similarity ───────────────────────────────────────────────────────


def test_block_similarity_identical_images(tmp_path):
    img_path = tmp_path / "img.png"
    Image.new("RGB", (64, 64), (200, 100, 50)).save(img_path)
    assert _block_similarity(img_path, img_path) == 1.0


def test_block_similarity_different_images(tmp_path):
    a = tmp_path / "a.png"
    b = tmp_path / "b.png"
    Image.new("RGB", (64, 64), (0, 0, 0)).save(a)
    Image.new("RGB", (64, 64), (255, 255, 255)).save(b)
    score = _block_similarity(a, b)
    # Totally different => score near 0.
    assert score < 0.1


def test_block_similarity_resizes_mismatched_dims(tmp_path):
    a = tmp_path / "a.png"
    b = tmp_path / "b.png"
    Image.new("RGB", (64, 64), (200, 100, 50)).save(a)
    Image.new("RGB", (128, 128), (200, 100, 50)).save(b)
    # Different size, same color => resize then near-identical.
    score = _block_similarity(a, b)
    assert score > 0.9


def test_block_similarity_with_masks(tmp_path):
    a = tmp_path / "a.png"
    b = tmp_path / "b.png"
    img_a = Image.new("RGB", (64, 64), (200, 100, 50))
    img_b = Image.new("RGB", (64, 64), (200, 100, 50))
    # Put a difference in a region we'll mask out
    from PIL import ImageDraw
    d = ImageDraw.Draw(img_b)
    d.rectangle([0, 0, 10, 10], fill=(0, 255, 0))
    img_a.save(a)
    img_b.save(b)
    masked = _block_similarity(a, b, masks=[(0, 0, 10, 10)])
    unmasked = _block_similarity(a, b)
    # Masking out the difference should yield a higher (or equal) score.
    assert masked >= unmasked


# ── recordings_root ────────────────────────────────────────────────────────


def test_recordings_root_uses_env(monkeypatch, tmp_path):
    monkeypatch.setenv("SIMDRIVE_HOME", str(tmp_path))
    assert recorder.recordings_root() == tmp_path / "recordings"


def test_recordings_root_default_home(monkeypatch):
    monkeypatch.delenv("SIMDRIVE_HOME", raising=False)
    assert recorder.recordings_root().name == "recordings"


# ── _check_capture ──────────────────────────────────────────────────────────


def test_check_capture_pre_none():
    assert "pre_state_missing" in recorder._check_capture(None, Path("/tmp/x"))


def test_check_capture_post_missing(tmp_path):
    pre = tmp_path / "pre.png"
    Image.new("RGB", (10, 10)).save(pre)
    msg = recorder._check_capture(pre, tmp_path / "missing.png")
    assert "post_state_missing" in msg


def test_check_capture_empty_file(tmp_path):
    pre = tmp_path / "pre.png"
    post = tmp_path / "post.png"
    Image.new("RGB", (10, 10)).save(pre)
    post.write_bytes(b"")
    msg = recorder._check_capture(pre, post)
    assert "post_state_empty" in msg


def test_check_capture_ok(tmp_path):
    pre = tmp_path / "pre.png"
    post = tmp_path / "post.png"
    Image.new("RGB", (10, 10)).save(pre)
    Image.new("RGB", (10, 10)).save(post)
    assert recorder._check_capture(pre, post) is None


# ── Recorder.add_step ───────────────────────────────────────────────────────


def _fake_session(workdir):
    return ses.Session(
        session_id="t",
        device=Device(udid="U", name="iPhone", os_version="26.3", state="Booted"),
        workdir=workdir,
    )


def test_recorder_add_step_drops_when_pre_missing(tmp_path):
    s = _fake_session(tmp_path)
    rec = Recorder(name="r", session=s, root=tmp_path / "rec")
    post = tmp_path / "post.png"
    Image.new("RGB", (10, 10)).save(post)
    out = rec.add_step("tap", {"x": 1}, None, post)
    assert out is None
    assert rec.steps == []


def test_recorder_add_step_drops_on_copy_failure(tmp_path):
    s = _fake_session(tmp_path)
    rec = Recorder(name="r", session=s, root=tmp_path / "rec")
    pre = tmp_path / "pre.png"
    post = tmp_path / "post.png"
    Image.new("RGB", (10, 10)).save(pre)
    Image.new("RGB", (10, 10)).save(post)
    with patch("simdrive.recorder.shutil.copy2", side_effect=OSError("disk full")):
        out = rec.add_step("tap", {"x": 1}, pre, post)
    assert out is None
    assert rec.steps == []


def test_recorder_add_step_appends_marks_count(tmp_path):
    s = _fake_session(tmp_path)
    rec = Recorder(name="r", session=s, root=tmp_path / "rec")
    pre = tmp_path / "pre.png"
    post = tmp_path / "post.png"
    Image.new("RGB", (10, 10)).save(pre)
    Image.new("RGB", (10, 10)).save(post)
    idx = rec.add_step("tap", {"x": 1}, pre, post, marks_count=5)
    assert idx == 1
    assert rec.steps[0]["marks_count"] == 5


# ── Recorder.finalize + write_partial ────────────────────────────────────────


def test_recorder_finalize_includes_capture_warning(tmp_path):
    s = _fake_session(tmp_path)
    rec = Recorder(name="r", session=s, root=tmp_path / "rec")
    rec.capture_warning = "observe failed at start"
    out = rec.finalize()
    payload = yaml.safe_load(out.read_text())
    assert payload["_capture_warning"] == "observe failed at start"


def test_recorder_finalize_handles_sim_appversion_failure(tmp_path):
    s = _fake_session(tmp_path)
    s.app_bundle_id = "com.example.App"
    rec = Recorder(name="r", session=s, root=tmp_path / "rec")
    with patch("simdrive.recorder.sim.get_app_version", side_effect=RuntimeError("simctl down")):
        out = rec.finalize()
    payload = yaml.safe_load(out.read_text())
    assert payload["app_version"] is None


def test_recorder_finalize_writes_screenshot_size_when_set(tmp_path):
    s = _fake_session(tmp_path)
    s.last_screenshot_w = 1206
    s.last_screenshot_h = 2622
    rec = Recorder(name="r", session=s, root=tmp_path / "rec")
    out = rec.finalize()
    payload = yaml.safe_load(out.read_text())
    assert payload["screenshot_size_pixels"] == [1206, 2622]


def test_recorder_write_partial_creates_partial_yaml(tmp_path):
    s = _fake_session(tmp_path)
    rec = Recorder(name="r", session=s, root=tmp_path / "rec")
    rec.steps = [{"id": 1, "action": "tap", "args": {}}]
    rec.requires_block = RequiresBlock(
        app=AppRequires(bundle_id="x"),
        sim=SimRequires(device="iPhone"),
        initial_state=InitialStateRequires(),
    )
    partial = rec.write_partial()
    payload = yaml.safe_load(partial.read_text())
    assert payload["partial"] is True
    assert payload["partial_steps_captured"] == 1
    assert payload["requires"]["target"] == "simulator"


# ── lint_recordings ─────────────────────────────────────────────────────────


def test_lint_recordings_missing_path_returns_empty(tmp_path):
    out = recorder.lint_recordings(tmp_path / "doesnt-exist")
    assert out == []


def test_lint_recordings_empty_dir(tmp_path):
    assert recorder.lint_recordings(tmp_path) == []


def test_lint_one_yaml_parse_error(tmp_path):
    rec_dir = tmp_path / "rec"
    rec_dir.mkdir()
    yaml_path = rec_dir / "recording.yaml"
    yaml_path.write_text("not: : valid: yaml: at all: :")  # broken
    results = recorder.lint_recordings(tmp_path)
    assert len(results) == 1
    assert results[0].status == "fail"


def test_lint_one_non_mapping(tmp_path):
    rec_dir = tmp_path / "rec"
    rec_dir.mkdir()
    yaml_path = rec_dir / "recording.yaml"
    yaml_path.write_text("- just\n- a\n- list")  # parses as list, not dict
    results = recorder.lint_recordings(tmp_path)
    assert results[0].status == "fail"
    assert "mapping" in results[0].reason


def test_lint_one_missing_requires(tmp_path):
    rec_dir = tmp_path / "rec"
    rec_dir.mkdir()
    (rec_dir / "recording.yaml").write_text(yaml.safe_dump({"name": "r", "steps": []}))
    results = recorder.lint_recordings(tmp_path)
    assert results[0].status == "fail"
    assert "no requires block" in results[0].reason


def test_lint_one_ok(tmp_path):
    rec_dir = tmp_path / "rec"
    rec_dir.mkdir()
    rb = RequiresBlock(
        app=AppRequires(bundle_id="com.example.App"),
        sim=SimRequires(device="iPhone 17"),
        initial_state=InitialStateRequires(
            foreground=True,
            text_subset_required=["Hello", "World"],
        ),
    )
    payload = {"name": "r", "steps": [], "requires": rb.to_dict()}
    (rec_dir / "recording.yaml").write_text(yaml.safe_dump(payload))
    results = recorder.lint_recordings(tmp_path)
    assert results[0].status == "ok"
    assert results[0].text_mark_count == 2
    assert results[0].app_bundle_id == "com.example.App"


# ── migrate_recording ───────────────────────────────────────────────────────


def test_migrate_recording_no_yaml_raises(tmp_path, monkeypatch):
    monkeypatch.setenv("SIMDRIVE_HOME", str(tmp_path))
    with pytest.raises(MigrationError) as exc:
        recorder.migrate_recording("does-not-exist")
    assert "not found" in str(exc.value)


def test_migrate_recording_idempotent_when_requires_present(tmp_path, monkeypatch):
    monkeypatch.setenv("SIMDRIVE_HOME", str(tmp_path))
    rec_dir = tmp_path / "recordings" / "r"
    rec_dir.mkdir(parents=True)
    payload = {
        "name": "r",
        "steps": [{"id": 1, "pre_screenshot": "snap/x.png"}],
        "requires": {"app": {"bundle_id": "x"}},
    }
    (rec_dir / "recording.yaml").write_text(yaml.safe_dump(payload))
    result = recorder.migrate_recording("r")
    assert isinstance(result, MigrationResult)
    assert result.migrated is False
    assert "already migrated" in result.reason


def test_migrate_recording_no_steps_raises(tmp_path, monkeypatch):
    monkeypatch.setenv("SIMDRIVE_HOME", str(tmp_path))
    rec_dir = tmp_path / "recordings" / "r"
    rec_dir.mkdir(parents=True)
    (rec_dir / "recording.yaml").write_text(yaml.safe_dump({"name": "r", "steps": []}))
    with pytest.raises(MigrationError) as exc:
        recorder.migrate_recording("r")
    assert "step-0" in str(exc.value)


def test_migrate_recording_no_pre_screenshot_raises(tmp_path, monkeypatch):
    monkeypatch.setenv("SIMDRIVE_HOME", str(tmp_path))
    rec_dir = tmp_path / "recordings" / "r"
    rec_dir.mkdir(parents=True)
    payload = {"name": "r", "steps": [{"id": 1}]}
    (rec_dir / "recording.yaml").write_text(yaml.safe_dump(payload))
    with pytest.raises(MigrationError) as exc:
        recorder.migrate_recording("r")
    assert "no pre_screenshot" in str(exc.value)


def test_migrate_recording_missing_pre_screenshot_file_raises(tmp_path, monkeypatch):
    monkeypatch.setenv("SIMDRIVE_HOME", str(tmp_path))
    rec_dir = tmp_path / "recordings" / "r"
    rec_dir.mkdir(parents=True)
    payload = {"name": "r", "steps": [{"id": 1, "pre_screenshot": "missing.png"}]}
    (rec_dir / "recording.yaml").write_text(yaml.safe_dump(payload))
    with pytest.raises(MigrationError) as exc:
        recorder.migrate_recording("r")
    assert "missing" in str(exc.value)


def test_migrate_recording_dry_run_does_not_write(tmp_path, monkeypatch):
    monkeypatch.setenv("SIMDRIVE_HOME", str(tmp_path))
    rec_dir = tmp_path / "recordings" / "r"
    rec_dir.mkdir(parents=True)
    pre = rec_dir / "snap.png"
    Image.new("RGB", (100, 200)).save(pre)
    payload = {
        "name": "r",
        "device": "iPhone 17",
        "os_version": "26.3",
        "app_bundle_id": "com.example.App",
        "steps": [{
            "id": 1, "pre_screenshot": "snap.png",
            "args": {"screenshot_h": 200},
        }],
    }
    (rec_dir / "recording.yaml").write_text(yaml.safe_dump(payload))
    with patch("simdrive.recorder.som.detect_marks", return_value=[]):
        result = recorder.migrate_recording("r", dry_run=True)
    assert result.migrated is True
    assert result.dry_run is True
    # File untouched — no requires block on disk
    fresh = yaml.safe_load((rec_dir / "recording.yaml").read_text())
    assert fresh.get("requires") is None


def test_migrate_recording_writes_requires_and_backup(tmp_path, monkeypatch):
    monkeypatch.setenv("SIMDRIVE_HOME", str(tmp_path))
    rec_dir = tmp_path / "recordings" / "r"
    rec_dir.mkdir(parents=True)
    pre = rec_dir / "snap.png"
    Image.new("RGB", (100, 200)).save(pre)
    payload = {
        "name": "r",
        "device": "iPhone 17",
        "os_version": "26.3",
        "app_bundle_id": "com.example.App",
        "steps": [{"id": 1, "pre_screenshot": "snap.png", "args": {}}],
    }
    (rec_dir / "recording.yaml").write_text(yaml.safe_dump(payload))
    marks = [
        Mark(id=1, x=0, y=10, w=80, h=20, text="Hello", confidence=0.95),
        Mark(id=2, x=0, y=40, w=80, h=20, text="World", confidence=0.95),
    ]
    with patch("simdrive.recorder.som.detect_marks", return_value=marks):
        result = recorder.migrate_recording("r")
    assert result.migrated is True
    assert result.backup_path is not None and result.backup_path.exists()
    fresh = yaml.safe_load((rec_dir / "recording.yaml").read_text())
    assert "requires" in fresh
    assert "Hello" in fresh["requires"]["initial_state"]["text_subset_required"]


def test_migrate_recording_pulls_dimensions_from_pil_when_args_missing(tmp_path, monkeypatch):
    """When step args lack screenshot_h, the migrator reads the PNG dimensions directly."""
    monkeypatch.setenv("SIMDRIVE_HOME", str(tmp_path))
    rec_dir = tmp_path / "recordings" / "r"
    rec_dir.mkdir(parents=True)
    pre = rec_dir / "snap.png"
    Image.new("RGB", (50, 100)).save(pre)
    payload = {
        "name": "r",
        "device": "iPhone 17",
        "os_version": "26.3",
        "steps": [{"id": 1, "pre_screenshot": "snap.png", "args": {}}],  # no screenshot_h
    }
    (rec_dir / "recording.yaml").write_text(yaml.safe_dump(payload))
    with patch("simdrive.recorder.som.detect_marks", return_value=[]):
        result = recorder.migrate_recording("r")
    assert result.migrated is True


# ── start / stop / errors ───────────────────────────────────────────────────


def test_stop_when_not_recording_raises(tmp_path):
    s = _fake_session(tmp_path)
    s.recorder = None
    with pytest.raises(Exception):
        recorder.stop(s)


def test_start_when_already_recording_raises(tmp_path, monkeypatch):
    monkeypatch.setenv("SIMDRIVE_HOME", str(tmp_path))
    s = _fake_session(tmp_path)
    # Pretend a recorder is already attached
    existing = Recorder(name="other", session=s, root=tmp_path / "other")
    s.recorder = existing
    with pytest.raises(Exception):
        recorder.start(s, "new-recording")


def test_start_creates_recording_dir_and_attaches_to_session(tmp_path, monkeypatch):
    """start() should create the root dir, attach recorder to the session, and call the contract path."""
    monkeypatch.setenv("SIMDRIVE_HOME", str(tmp_path))
    s = _fake_session(tmp_path)
    s.recorder = None
    # _capture_state_contract for simulator path uses observe.observe — patch it.
    with patch("simdrive.recorder.observe.observe") as mock_observe:
        # Fake observation with no marks => block created but empty.
        mock_obs = mock_observe.return_value
        mock_obs.marks = []
        mock_obs.screenshot_h = 200
        rec = recorder.start(s, "myrec")
    assert rec.name == "myrec"
    assert rec.root.exists()
    assert s.recorder is rec
    s.recorder = None


def test_start_handles_existing_dir_via_timestamp(tmp_path, monkeypatch):
    """start() should not clobber an existing recording dir — it suffixes with a timestamp."""
    monkeypatch.setenv("SIMDRIVE_HOME", str(tmp_path))
    s = _fake_session(tmp_path)
    s.recorder = None
    # Pre-create the conflicting directory.
    (tmp_path / "recordings" / "dup").mkdir(parents=True)
    with patch("simdrive.recorder.observe.observe") as mock_observe:
        mock_obs = mock_observe.return_value
        mock_obs.marks = []
        mock_obs.screenshot_h = 100
        rec = recorder.start(s, "dup")
    # Suffix should differ from the simple name
    assert rec.root.name != "dup"
    assert rec.root.name.startswith("dup-")
    s.recorder = None


def test_stop_finalizes_recording(tmp_path, monkeypatch):
    monkeypatch.setenv("SIMDRIVE_HOME", str(tmp_path))
    s = _fake_session(tmp_path)
    rec = Recorder(name="r", session=s, root=tmp_path / "rec")
    s.recorder = rec
    out = recorder.stop(s)
    assert out.exists()
    assert s.recorder is None


# ── _capture_state_contract (sim path failure) ──────────────────────────────


def test_capture_state_contract_sim_observe_failure_returns_warning(tmp_path):
    s = _fake_session(tmp_path)
    s.target = "simulator"
    with patch("simdrive.recorder.observe.observe", side_effect=RuntimeError("boom")):
        block, warning = recorder._capture_state_contract(s, tmp_path / "wd")
    assert block is None
    assert warning is not None
    assert "Could not capture" in warning


def test_capture_state_contract_device_routes_to_device_helper(tmp_path):
    s = _fake_session(tmp_path)
    s.target = "device"
    # Sentinel return so we know the device helper was called
    with patch("simdrive.recorder._capture_device_state_contract",
               return_value=(None, "device-sentinel")) as mock_dev:
        block, warning = recorder._capture_state_contract(s, tmp_path / "wd")
    assert mock_dev.called
    assert warning == "device-sentinel"


def test_capture_device_state_contract_observe_failure_swallowed(tmp_path):
    s = _fake_session(tmp_path)
    s.target = "device"
    s.wda_client = None
    with patch("simdrive.recorder.observe.observe", side_effect=RuntimeError("offline")):
        block, warning = recorder._capture_device_state_contract(s, tmp_path / "wd")
    assert block is None
    assert "Could not capture device" in warning


def test_capture_device_state_contract_no_wda_uses_observe(tmp_path):
    s = _fake_session(tmp_path)
    s.target = "device"
    s.wda_client = None
    with patch("simdrive.recorder.observe.observe") as mock_observe:
        mock_obs = mock_observe.return_value
        mock_obs.marks = []
        mock_obs.screenshot_h = 800
        block, warning = recorder._capture_device_state_contract(s, tmp_path / "wd")
    assert warning is None
    assert block is not None
    assert block.target == "device"


# ── _current_app_version ────────────────────────────────────────────────────


def test_current_app_version_no_bundle_returns_none(tmp_path):
    s = _fake_session(tmp_path)
    s.app_bundle_id = None
    assert recorder._current_app_version(s) is None


def test_current_app_version_device_returns_none(tmp_path):
    s = _fake_session(tmp_path)
    s.target = "device"
    s.app_bundle_id = "com.example.App"
    assert recorder._current_app_version(s) is None


def test_current_app_version_simctl_failure_returns_none(tmp_path):
    s = _fake_session(tmp_path)
    s.app_bundle_id = "com.example.App"
    with patch("simdrive.recorder.sim.get_app_version", side_effect=RuntimeError("boom")):
        assert recorder._current_app_version(s) is None


def test_current_app_version_simctl_returns_string(tmp_path):
    s = _fake_session(tmp_path)
    s.app_bundle_id = "com.example.App"
    with patch("simdrive.recorder.sim.get_app_version", return_value="2.0"):
        assert recorder._current_app_version(s) == "2.0"


# ── LintResult.to_dict ──────────────────────────────────────────────────────


def test_lint_result_to_dict():
    r = LintResult(path=Path("/x"), status="ok", reason="", text_mark_count=3,
                   app_bundle_id="com.example.App", sim_device="iPhone")
    d = r.to_dict()
    assert d["status"] == "ok"
    assert d["text_mark_count"] == 3
    assert d["path"].endswith("/x")
