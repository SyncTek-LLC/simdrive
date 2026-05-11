"""Tests for a9.1 — `simdrive lint-recordings` + `simdrive migrate-recording`.

`lint_recordings(path)`:
  - Walks `path` for recording.yaml files.
  - Reports each as ok / fail-no-requires / fail-malformed-requires / fail-parse.

`migrate_recording(name)`:
  - Reads recordings_root/<name>/recording.yaml.
  - If `requires:` already present and not --force, no-op.
  - Else OCRs the step-0 pre_screenshot, builds a RequiresBlock, writes the YAML
    back with a `.pre-migrate.bak` sibling so a botched migration is recoverable.
"""
from __future__ import annotations

import json as _json
from pathlib import Path

import pytest
import yaml as _yaml
from PIL import Image


# ──────────────────────────────────────────────────────────────────────────
# Fixture helpers
# ──────────────────────────────────────────────────────────────────────────


_GOOD_REQUIRES = {
    "app": {"bundle_id": "com.example.app", "version": "2.4.1", "version_match": "minor"},
    "sim": {"device": "iPhone 17 Pro", "ios_version": "26.3"},
    "initial_state": {
        "foreground": True,
        "text_subset_required": ["Library", "Books"],
        "text_subset_forbidden": [],
        "primary_button_label": "Add Library",
    },
}


def _write_recording(dir_: Path, *, requires=None, with_step=True,
                     bundle_id="com.example.app", device="iPhone 17 Pro",
                     os_version="26.3", app_version="2.4.1"):
    """Drop a minimal recording.yaml + optional pre_screenshot under dir_."""
    dir_.mkdir(parents=True, exist_ok=True)
    snaps = dir_ / "snapshots"
    snaps.mkdir(parents=True, exist_ok=True)

    steps = []
    if with_step:
        pre = snaps / "001_pre.png"
        Image.new("RGB", (1206, 2622), (240, 240, 240)).save(pre)
        post = snaps / "001_post.png"
        Image.new("RGB", (1206, 2622), (200, 200, 200)).save(post)
        steps.append({
            "id": 1, "action": "tap",
            "args": {"x": 100, "y": 200, "screenshot_w": 1206, "screenshot_h": 2622},
            "pre_screenshot": "snapshots/001_pre.png",
            "post_screenshot": "snapshots/001_post.png",
            "captured_at": 0.0,
        })

    payload = {
        "name": dir_.name,
        "created_at": 0.0,
        "device": device,
        "os_version": os_version,
        "app_bundle_id": bundle_id,
        "app_version": app_version,
        "steps": steps,
    }
    if requires is not None:
        payload["requires"] = requires
    (dir_ / "recording.yaml").write_text(_yaml.safe_dump(payload, sort_keys=False))
    return dir_ / "recording.yaml"


# ──────────────────────────────────────────────────────────────────────────
# lint_recordings()
# ──────────────────────────────────────────────────────────────────────────


def test_lint_reports_ok_when_requires_present_and_well_formed(tmp_path):
    from simdrive.recorder import lint_recordings

    _write_recording(tmp_path / "good", requires=_GOOD_REQUIRES)

    results = lint_recordings(tmp_path)
    assert len(results) == 1
    r = results[0]
    assert r.status == "ok"
    assert r.path.name == "recording.yaml"
    assert r.text_mark_count == 2
    assert r.app_bundle_id == "com.example.app"
    assert r.sim_device == "iPhone 17 Pro"


def test_lint_reports_fail_when_requires_missing(tmp_path):
    from simdrive.recorder import lint_recordings

    _write_recording(tmp_path / "missing", requires=None)

    results = lint_recordings(tmp_path)
    assert len(results) == 1
    r = results[0]
    assert r.status == "fail"
    assert "no requires block" in r.reason


def test_lint_reports_fail_on_malformed_requires(tmp_path):
    """Top-level `requires:` is present but not a dict — from_dict returns None."""
    from simdrive.recorder import lint_recordings

    _write_recording(tmp_path / "malformed", requires="not-a-dict")

    results = lint_recordings(tmp_path)
    assert len(results) == 1
    assert results[0].status == "fail"
    assert "malformed" in results[0].reason.lower()


def test_lint_reports_fail_on_unparseable_yaml(tmp_path):
    """Non-YAML garbage in recording.yaml surfaces as a fail with a parse reason."""
    from simdrive.recorder import lint_recordings

    rec_dir = tmp_path / "broken"
    rec_dir.mkdir()
    (rec_dir / "recording.yaml").write_text("this: is: not: valid: yaml: [\n")

    results = lint_recordings(tmp_path)
    assert len(results) == 1
    assert results[0].status == "fail"
    assert "parse" in results[0].reason.lower() or "yaml" in results[0].reason.lower()


def test_lint_walks_subdirectories(tmp_path):
    from simdrive.recorder import lint_recordings

    _write_recording(tmp_path / "a", requires=_GOOD_REQUIRES)
    _write_recording(tmp_path / "b", requires=None)
    _write_recording(tmp_path / "nested" / "c", requires=_GOOD_REQUIRES)

    results = lint_recordings(tmp_path)
    assert len(results) == 3
    statuses = sorted(r.status for r in results)
    assert statuses == ["fail", "ok", "ok"]


def test_lint_empty_dir_returns_no_results(tmp_path):
    from simdrive.recorder import lint_recordings

    results = lint_recordings(tmp_path)
    assert results == []


# ──────────────────────────────────────────────────────────────────────────
# lint-recordings CLI
# ──────────────────────────────────────────────────────────────────────────


def test_cli_lint_recordings_exits_zero_when_all_ok(tmp_path, capsys, monkeypatch):
    from simdrive.server import _cmd_lint_recordings

    monkeypatch.setenv("SIMDRIVE_HOME", str(tmp_path))
    _write_recording(tmp_path / "recordings" / "good", requires=_GOOD_REQUIRES)

    with pytest.raises(SystemExit) as exc:
        _cmd_lint_recordings([])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert "[OK]" in out


def test_cli_lint_recordings_exits_nonzero_when_any_fail(tmp_path, capsys, monkeypatch):
    from simdrive.server import _cmd_lint_recordings

    monkeypatch.setenv("SIMDRIVE_HOME", str(tmp_path))
    _write_recording(tmp_path / "recordings" / "bad", requires=None)

    with pytest.raises(SystemExit) as exc:
        _cmd_lint_recordings([])
    assert exc.value.code == 1
    out = capsys.readouterr().out
    assert "[FAIL]" in out


def test_cli_lint_recordings_quiet_suppresses_ok_lines(tmp_path, capsys, monkeypatch):
    from simdrive.server import _cmd_lint_recordings

    _write_recording(tmp_path / "good", requires=_GOOD_REQUIRES)
    _write_recording(tmp_path / "bad", requires=None)

    with pytest.raises(SystemExit) as exc:
        _cmd_lint_recordings(["--path", str(tmp_path), "--quiet"])
    assert exc.value.code == 1
    out = capsys.readouterr().out
    assert "[OK]" not in out
    assert "[FAIL]" in out


def test_cli_lint_recordings_json_shape(tmp_path, capsys, monkeypatch):
    from simdrive.server import _cmd_lint_recordings

    _write_recording(tmp_path / "good", requires=_GOOD_REQUIRES)
    _write_recording(tmp_path / "bad", requires=None)

    with pytest.raises(SystemExit):
        _cmd_lint_recordings(["--path", str(tmp_path), "--json"])
    out = capsys.readouterr().out
    payload = _json.loads(out)
    assert payload["ok"] == 1
    assert payload["fail"] == 1
    assert len(payload["results"]) == 2
    for r in payload["results"]:
        assert set(r.keys()) >= {"path", "status"}


# ──────────────────────────────────────────────────────────────────────────
# migrate_recording()
# ──────────────────────────────────────────────────────────────────────────


def _patch_detect_marks(monkeypatch, marks):
    """Stub som.detect_marks() to return canned marks regardless of image path."""
    from simdrive import som
    monkeypatch.setattr(som, "detect_marks", lambda _path: list(marks))
    # _capture_state_contract uses observe.observe; migrate_recording uses
    # som.detect_marks directly, so only the latter needs stubbing here.


def test_migrate_recording_populates_requires_from_pre_screenshot(tmp_path, monkeypatch):
    from simdrive import recorder, som

    monkeypatch.setenv("SIMDRIVE_HOME", str(tmp_path))
    rec_dir = recorder.recordings_root() / "needs-migrate"
    _write_recording(rec_dir, requires=None, bundle_id="com.example.app")

    marks = [
        som.Mark(id=1, x=40, y=80, w=400, h=200, text="Library", confidence=0.95),
        som.Mark(id=2, x=40, y=300, w=200, h=40, text="Books", confidence=0.92),
        som.Mark(id=3, x=40, y=400, w=80, h=40, text="X", confidence=0.99),  # too short
    ]
    _patch_detect_marks(monkeypatch, marks)

    result = recorder.migrate_recording("needs-migrate")
    assert result.migrated is True

    payload = _yaml.safe_load((rec_dir / "recording.yaml").read_text())
    assert "requires" in payload
    assert payload["requires"]["app"]["bundle_id"] == "com.example.app"
    assert payload["requires"]["sim"]["device"] == "iPhone 17 Pro"
    assert payload["requires"]["initial_state"]["primary_button_label"] == "Library"
    assert "Library" in payload["requires"]["initial_state"]["text_subset_required"]
    assert "X" not in payload["requires"]["initial_state"]["text_subset_required"]

    # Backup file written
    assert (rec_dir / "recording.yaml.pre-migrate.bak").exists()


def test_migrate_recording_noop_when_already_has_requires(tmp_path, monkeypatch):
    from simdrive import recorder

    monkeypatch.setenv("SIMDRIVE_HOME", str(tmp_path))
    rec_dir = recorder.recordings_root() / "already-migrated"
    _write_recording(rec_dir, requires=_GOOD_REQUIRES)

    result = recorder.migrate_recording("already-migrated")
    assert result.migrated is False
    assert "already" in result.reason.lower()
    # No backup written on no-op
    assert not (rec_dir / "recording.yaml.pre-migrate.bak").exists()


def test_migrate_recording_force_overwrites_existing_requires(tmp_path, monkeypatch):
    from simdrive import recorder, som

    monkeypatch.setenv("SIMDRIVE_HOME", str(tmp_path))
    rec_dir = recorder.recordings_root() / "force-me"
    stale = {**_GOOD_REQUIRES,
             "initial_state": {**_GOOD_REQUIRES["initial_state"],
                               "primary_button_label": "Stale Label"}}
    _write_recording(rec_dir, requires=stale)

    marks = [som.Mark(id=1, x=40, y=80, w=400, h=200, text="Fresh", confidence=0.95)]
    _patch_detect_marks(monkeypatch, marks)

    result = recorder.migrate_recording("force-me", force=True)
    assert result.migrated is True
    payload = _yaml.safe_load((rec_dir / "recording.yaml").read_text())
    assert payload["requires"]["initial_state"]["primary_button_label"] == "Fresh"


def test_migrate_recording_fails_on_zero_step_recording(tmp_path, monkeypatch):
    from simdrive import recorder

    monkeypatch.setenv("SIMDRIVE_HOME", str(tmp_path))
    rec_dir = recorder.recordings_root() / "empty"
    _write_recording(rec_dir, requires=None, with_step=False)

    with pytest.raises(recorder.MigrationError) as exc:
        recorder.migrate_recording("empty")
    assert "no step-0" in str(exc.value).lower() or "no screenshot" in str(exc.value).lower()


def test_migrate_recording_dry_run_does_not_write(tmp_path, monkeypatch):
    from simdrive import recorder, som

    monkeypatch.setenv("SIMDRIVE_HOME", str(tmp_path))
    rec_dir = recorder.recordings_root() / "dry"
    _write_recording(rec_dir, requires=None)

    marks = [som.Mark(id=1, x=40, y=80, w=400, h=200, text="Library", confidence=0.95)]
    _patch_detect_marks(monkeypatch, marks)

    result = recorder.migrate_recording("dry", dry_run=True)
    assert result.migrated is True   # would have migrated
    assert result.dry_run is True

    payload = _yaml.safe_load((rec_dir / "recording.yaml").read_text())
    assert "requires" not in payload
    assert not (rec_dir / "recording.yaml.pre-migrate.bak").exists()


# ──────────────────────────────────────────────────────────────────────────
# migrate-recording CLI
# ──────────────────────────────────────────────────────────────────────────


def test_cli_migrate_recording_exits_zero_on_success(tmp_path, monkeypatch, capsys):
    from simdrive import recorder, som
    from simdrive.server import _cmd_migrate_recording

    monkeypatch.setenv("SIMDRIVE_HOME", str(tmp_path))
    rec_dir = recorder.recordings_root() / "cli-good"
    _write_recording(rec_dir, requires=None)

    marks = [som.Mark(id=1, x=40, y=80, w=400, h=200, text="Library", confidence=0.95)]
    _patch_detect_marks(monkeypatch, marks)

    with pytest.raises(SystemExit) as exc:
        _cmd_migrate_recording(["cli-good"])
    assert exc.value.code == 0


def test_cli_migrate_recording_exits_one_on_zero_steps(tmp_path, monkeypatch):
    from simdrive import recorder
    from simdrive.server import _cmd_migrate_recording

    monkeypatch.setenv("SIMDRIVE_HOME", str(tmp_path))
    rec_dir = recorder.recordings_root() / "cli-empty"
    _write_recording(rec_dir, requires=None, with_step=False)

    with pytest.raises(SystemExit) as exc:
        _cmd_migrate_recording(["cli-empty"])
    assert exc.value.code == 1


