"""Tests for the a9.0 recording state contract.

Covers:
  - RequiresBlock schema (round-trip, defaults, malformed input)
  - Auto-populate at record_start (observation → requires block)
  - Replay-time verification (halt on mismatch, warn on missing block)
  - robustness.validate_replay tolerates `requires:` key

The state contract is the headline a9.0 feature in response to Palace's
2026-05-11 dogfood report: a replay against a divergent app state silently
executed 23 taps at SSIM 0.014. We verify state at replay step -1 and halt
on mismatch with a structured error.
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml as _yaml
from PIL import Image


# ──────────────────────────────────────────────────────────────────────────
# Step 1 — schema round-trip
# ──────────────────────────────────────────────────────────────────────────


def test_requires_block_round_trips_through_dict():
    from simdrive.recorder import (
        AppRequires,
        InitialStateRequires,
        RequiresBlock,
        SimRequires,
    )

    block = RequiresBlock(
        app=AppRequires(bundle_id="com.example.app", version="2.4.1", version_match="minor"),
        sim=SimRequires(device="iPhone 17 Pro", ios_version=">=18.0"),
        initial_state=InitialStateRequires(
            foreground=True,
            text_subset_required=["Library", "Books"],
            text_subset_forbidden=["Don't Allow"],
            primary_button_label="Add Library",
        ),
    )
    d = block.to_dict()
    assert d["app"]["bundle_id"] == "com.example.app"
    assert d["app"]["version_match"] == "minor"
    assert d["sim"]["ios_version"] == ">=18.0"
    assert d["initial_state"]["primary_button_label"] == "Add Library"

    restored = RequiresBlock.from_dict(d)
    assert restored is not None
    assert restored.to_dict() == d


def test_requires_block_defaults_when_fields_missing():
    from simdrive.recorder import RequiresBlock

    minimal = {"app": {}, "sim": {}, "initial_state": {}}
    block = RequiresBlock.from_dict(minimal)
    assert block is not None
    assert block.app.bundle_id is None
    assert block.app.version_match == "minor"
    assert block.sim.device is None
    assert block.initial_state.foreground is True
    assert block.initial_state.text_subset_required == []
    assert block.initial_state.text_subset_forbidden == []
    assert block.initial_state.primary_button_label is None


def test_requires_block_from_dict_returns_none_on_malformed_input():
    from simdrive.recorder import RequiresBlock

    assert RequiresBlock.from_dict(None) is None
    assert RequiresBlock.from_dict("not a dict") is None
    assert RequiresBlock.from_dict([]) is None
    assert RequiresBlock.from_dict(42) is None


# ──────────────────────────────────────────────────────────────────────────
# Step 2 — capture-time auto-populate
# ──────────────────────────────────────────────────────────────────────────


def _fake_marks(monkeypatch, marks):
    """Stub observe.observe() to return a canned Observation with `marks`."""
    from simdrive import observe as obs_mod
    from simdrive.observe import Observation

    def fake_observe(udid, out_dir, **kwargs):
        out_dir = Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / "live.png"
        Image.new("RGB", (1206, 2622), (240, 240, 240)).save(path)
        return Observation(
            screenshot_path=path,
            annotated_path=None,
            screenshot_w=1206,
            screenshot_h=2622,
            window_bounds=None,
            captured_at=0.0,
            marks=marks,
        )

    monkeypatch.setattr(obs_mod, "observe", fake_observe)


def _make_session(tmp_path, *, app_bundle_id=None):
    from simdrive import session as ses
    from simdrive.sim import Device

    ses._SESSIONS.clear()
    s = ses.Session(
        session_id="rsc-test",
        device=Device(udid="UDID", name="iPhone 17 Pro", os_version="26.3", state="Booted"),
        workdir=tmp_path / "wd",
        app_bundle_id=app_bundle_id,
    )
    s.workdir.mkdir(parents=True, exist_ok=True)
    return s


def test_record_start_captures_requires_block_from_observe(tmp_path, monkeypatch):
    from simdrive import recorder, som

    monkeypatch.setenv("SIMDRIVE_HOME", str(tmp_path))

    marks = [
        som.Mark(id=1, x=40, y=80, w=400, h=200, text="Library", confidence=0.95),
        som.Mark(id=2, x=40, y=300, w=200, h=40, text="Books", confidence=0.92),
        som.Mark(id=3, x=40, y=400, w=80, h=40, text="X", confidence=0.99),  # too short
    ]
    _fake_marks(monkeypatch, marks)

    s = _make_session(tmp_path, app_bundle_id="com.example.app")
    rec = recorder.start(s, "with-state")
    assert rec.requires_block is not None
    assert rec.requires_block.app.bundle_id == "com.example.app"
    assert rec.requires_block.sim.device == "iPhone 17 Pro"
    assert rec.requires_block.sim.ios_version == "26.3"
    assert rec.requires_block.initial_state.foreground is True
    # Required texts include only marks with len>=2; "X" is filtered out.
    assert "Library" in rec.requires_block.initial_state.text_subset_required
    assert "Books" in rec.requires_block.initial_state.text_subset_required
    assert "X" not in rec.requires_block.initial_state.text_subset_required
    # primary_button_label: largest bbox in upper half (y < 2622/2 = 1311). Both marks qualify;
    # mark id=1 has the largest area (400*200=80000) vs id=2 (200*40=8000).
    assert rec.requires_block.initial_state.primary_button_label == "Library"


def test_record_start_empty_marks_yields_foreground_false(tmp_path, monkeypatch):
    from simdrive import recorder

    monkeypatch.setenv("SIMDRIVE_HOME", str(tmp_path))
    _fake_marks(monkeypatch, [])

    s = _make_session(tmp_path)
    rec = recorder.start(s, "no-marks")
    assert rec.requires_block is not None
    assert rec.requires_block.initial_state.foreground is False
    assert rec.requires_block.initial_state.text_subset_required == []
    assert rec.requires_block.initial_state.primary_button_label is None


def test_record_start_observe_failure_degrades_gracefully(tmp_path, monkeypatch):
    """If observe raises, recording still starts; requires_block is None."""
    from simdrive import observe as obs_mod, recorder

    monkeypatch.setenv("SIMDRIVE_HOME", str(tmp_path))

    def bad_observe(*a, **kw):
        raise RuntimeError("observe blew up")

    monkeypatch.setattr(obs_mod, "observe", bad_observe)
    s = _make_session(tmp_path)
    rec = recorder.start(s, "bad-observe")
    assert rec.requires_block is None
    # Capture warning was emitted (stored on the recorder for later replay).
    assert "observe blew up" in (rec.capture_warning or "")


def test_finalize_writes_requires_block_to_yaml(tmp_path, monkeypatch):
    from simdrive import recorder, som

    monkeypatch.setenv("SIMDRIVE_HOME", str(tmp_path))
    marks = [som.Mark(id=1, x=40, y=80, w=400, h=200, text="Library", confidence=0.95)]
    _fake_marks(monkeypatch, marks)

    s = _make_session(tmp_path, app_bundle_id="com.example.app")
    rec = recorder.start(s, "yaml-roundtrip")
    yaml_path = recorder.stop(s)

    payload = _yaml.safe_load(yaml_path.read_text())
    assert "requires" in payload
    assert payload["requires"]["app"]["bundle_id"] == "com.example.app"
    assert payload["requires"]["sim"]["device"] == "iPhone 17 Pro"


def test_finalize_omits_requires_when_none(tmp_path, monkeypatch):
    from simdrive import observe as obs_mod, recorder

    monkeypatch.setenv("SIMDRIVE_HOME", str(tmp_path))
    monkeypatch.setattr(obs_mod, "observe",
                        lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("nope")))

    s = _make_session(tmp_path)
    recorder.start(s, "no-requires")
    yaml_path = recorder.stop(s)

    payload = _yaml.safe_load(yaml_path.read_text())
    assert "requires" not in payload


# ──────────────────────────────────────────────────────────────────────────
# Step 3 — replay-time verifier
# ──────────────────────────────────────────────────────────────────────────


def _write_recording_with_requires(rec_dir, requires):
    """Single-step tap recording with an explicit requires block."""
    snaps = rec_dir / "snapshots"
    snaps.mkdir(parents=True, exist_ok=True)
    pre = snaps / "001_pre.png"
    post = snaps / "001_post.png"
    Image.new("RGB", (1206, 2622), (240, 240, 240)).save(pre)
    Image.new("RGB", (1206, 2622), (200, 200, 200)).save(post)
    payload = {
        "name": rec_dir.name,
        "created_at": 0.0,
        "device": "iPhone 17 Pro",
        "os_version": "26.3",
        "app_bundle_id": "com.example.app",
        "app_version": "2.4.1",
        "steps": [{
            "id": 1, "action": "tap",
            "args": {"x": 100, "y": 200, "screenshot_w": 1206, "screenshot_h": 2622},
            "pre_screenshot": "snapshots/001_pre.png",
            "post_screenshot": "snapshots/001_post.png",
            "captured_at": 0.0,
        }],
    }
    if requires is not None:
        payload["requires"] = requires
    (rec_dir / "recording.yaml").write_text(_yaml.safe_dump(payload, sort_keys=False))


def _replay_session(tmp_path, *, app_bundle_id="com.example.app", device="iPhone 17 Pro",
                   os_version="26.3"):
    from simdrive import session as ses
    from simdrive.sim import Device

    ses._SESSIONS.clear()
    s = ses.Session(
        session_id="rt",
        device=Device(udid="UDID", name=device, os_version=os_version, state="Booted"),
        workdir=tmp_path / "wd",
        app_bundle_id=app_bundle_id,
    )
    s.workdir.mkdir(parents=True, exist_ok=True)
    return s


def _patch_replay_observe(monkeypatch, marks):
    """Patch BOTH the bound import (`recorder.observe`) and the source module so
    the verifier and the per-step similarity loop see the same fake."""
    from simdrive import observe as obs_mod, recorder
    from simdrive.observe import Observation

    def fake_observe(udid, out_dir, **kwargs):
        out_dir = Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / "live.png"
        Image.new("RGB", (1206, 2622), (240, 240, 240)).save(path)
        return Observation(
            screenshot_path=path,
            annotated_path=None,
            screenshot_w=1206,
            screenshot_h=2622,
            window_bounds=None,
            captured_at=0.0,
            marks=marks,
        )

    monkeypatch.setattr(obs_mod, "observe", fake_observe)
    monkeypatch.setattr(recorder.observe, "observe", fake_observe, raising=False)


def _patch_tap_noop(monkeypatch):
    from simdrive import act
    monkeypatch.setattr(act, "tap", lambda *a, **kw: None)


def test_replay_passes_when_state_matches(tmp_path, monkeypatch):
    from simdrive import recorder, som

    monkeypatch.setenv("SIMDRIVE_HOME", str(tmp_path))
    rec_dir = recorder.recordings_root() / "match"
    _write_recording_with_requires(rec_dir, {
        "app": {"bundle_id": "com.example.app", "version": "2.4.1", "version_match": "minor"},
        "sim": {"device": "iPhone 17 Pro", "ios_version": "26.3"},
        "initial_state": {
            "foreground": True,
            "text_subset_required": ["Library"],
            "text_subset_forbidden": ["Don't Allow"],
            "primary_button_label": "Add Library",
        },
    })

    marks = [som.Mark(id=1, x=40, y=80, w=400, h=200, text="Add Library", confidence=0.95)]
    _patch_replay_observe(monkeypatch, marks)
    _patch_tap_noop(monkeypatch)

    s = _replay_session(tmp_path)
    result = recorder.replay("match", s, on_drift="force")
    assert result["ok"] is True
    assert result.get("halt_reason") != "state_contract_mismatch"


def test_replay_halts_on_forbidden_text(tmp_path, monkeypatch):
    """The headline scenario — replay sees 'Don't Allow' alert and halts before step 1."""
    from simdrive import act, recorder, som

    monkeypatch.setenv("SIMDRIVE_HOME", str(tmp_path))
    rec_dir = recorder.recordings_root() / "forbidden"
    _write_recording_with_requires(rec_dir, {
        "app": {"bundle_id": "com.example.app", "version": "2.4.1", "version_match": "minor"},
        "sim": {"device": "iPhone 17 Pro", "ios_version": "26.3"},
        "initial_state": {
            "foreground": True,
            "text_subset_required": ["Library"],
            "text_subset_forbidden": ["Don't Allow"],
            "primary_button_label": "Add Library",
        },
    })

    marks = [
        som.Mark(id=1, x=40, y=80, w=400, h=80, text="Allow Access", confidence=0.95),
        som.Mark(id=2, x=40, y=200, w=200, h=60, text="Don't Allow", confidence=0.95),
    ]
    _patch_replay_observe(monkeypatch, marks)

    tap_calls = []
    monkeypatch.setattr(act, "tap", lambda *a, **kw: tap_calls.append(a))

    s = _replay_session(tmp_path)
    result = recorder.replay("forbidden", s, on_drift="force")
    assert result["ok"] is False
    assert result["halt_reason"] == "state_contract_mismatch"
    assert result["halted_at"] == 0
    assert tap_calls == []  # no taps executed
    assert "expected" in result
    assert "actual" in result
    assert "remedy" in result
    # Alert-shaped remedy
    assert "permission alert" in result["remedy"].lower() or "pre-grant" in result["remedy"].lower()


def test_replay_halts_on_missing_required_text(tmp_path, monkeypatch):
    from simdrive import act, recorder, som

    monkeypatch.setenv("SIMDRIVE_HOME", str(tmp_path))
    rec_dir = recorder.recordings_root() / "missing-required"
    _write_recording_with_requires(rec_dir, {
        "app": {},
        "sim": {},
        "initial_state": {
            "text_subset_required": ["Library", "Settings"],
        },
    })

    marks = [som.Mark(id=1, x=40, y=80, w=400, h=80, text="Library", confidence=0.95)]
    _patch_replay_observe(monkeypatch, marks)
    tap_calls = []
    monkeypatch.setattr(act, "tap", lambda *a, **kw: tap_calls.append(a))

    s = _replay_session(tmp_path)
    result = recorder.replay("missing-required", s, on_drift="force")
    assert result["ok"] is False
    assert result["halt_reason"] == "state_contract_mismatch"
    assert tap_calls == []


def test_replay_passes_with_version_match_any(tmp_path, monkeypatch):
    from simdrive import recorder, som

    monkeypatch.setenv("SIMDRIVE_HOME", str(tmp_path))
    rec_dir = recorder.recordings_root() / "any-ver"
    _write_recording_with_requires(rec_dir, {
        "app": {"bundle_id": "com.example.app", "version": "1.0.0", "version_match": "any"},
        "sim": {},
        "initial_state": {"text_subset_required": ["Hi"]},
    })

    marks = [som.Mark(id=1, x=40, y=80, w=400, h=80, text="Hi there", confidence=0.95)]
    _patch_replay_observe(monkeypatch, marks)
    _patch_tap_noop(monkeypatch)

    s = _replay_session(tmp_path, app_bundle_id="com.example.app")
    # Override session app_version expectation by passing a different live bundle/version
    result = recorder.replay("any-ver", s, on_drift="force")
    assert result["ok"] is True


def test_replay_halts_on_version_minor_mismatch(tmp_path, monkeypatch):
    """version_match=minor: requires same major.minor; 2.4.1 vs 2.5.0 mismatches."""
    from simdrive import act, recorder, som

    monkeypatch.setenv("SIMDRIVE_HOME", str(tmp_path))
    rec_dir = recorder.recordings_root() / "minor-mismatch"
    _write_recording_with_requires(rec_dir, {
        "app": {"bundle_id": "com.example.app", "version": "2.4.1", "version_match": "minor"},
        "sim": {},
        "initial_state": {},
    })

    # Stub the version lookup so the live "current version" is 2.5.0.
    monkeypatch.setattr(recorder, "_current_app_version",
                        lambda session: "2.5.0", raising=False)
    marks = [som.Mark(id=1, x=40, y=80, w=400, h=80, text="Hi", confidence=0.95)]
    _patch_replay_observe(monkeypatch, marks)
    tap_calls = []
    monkeypatch.setattr(act, "tap", lambda *a, **kw: tap_calls.append(a))

    s = _replay_session(tmp_path, app_bundle_id="com.example.app")
    result = recorder.replay("minor-mismatch", s, on_drift="force")
    assert result["ok"] is False
    assert result["halt_reason"] == "state_contract_mismatch"
    assert tap_calls == []


def test_replay_passes_with_version_minor_same(tmp_path, monkeypatch):
    """version_match=minor: 2.4.1 vs 2.4.9 passes (same major.minor)."""
    from simdrive import recorder, som

    monkeypatch.setenv("SIMDRIVE_HOME", str(tmp_path))
    rec_dir = recorder.recordings_root() / "minor-pass"
    _write_recording_with_requires(rec_dir, {
        "app": {"bundle_id": "com.example.app", "version": "2.4.1", "version_match": "minor"},
        "sim": {},
        "initial_state": {},
    })

    monkeypatch.setattr(recorder, "_current_app_version",
                        lambda session: "2.4.9", raising=False)
    marks = [som.Mark(id=1, x=40, y=80, w=400, h=80, text="Hi", confidence=0.95)]
    _patch_replay_observe(monkeypatch, marks)
    _patch_tap_noop(monkeypatch)

    s = _replay_session(tmp_path, app_bundle_id="com.example.app")
    result = recorder.replay("minor-pass", s, on_drift="force")
    assert result["ok"] is True


def test_replay_warns_on_recording_without_requires(tmp_path, monkeypatch):
    """Old recordings without a requires block should warn but proceed."""
    from simdrive import recorder, som

    monkeypatch.setenv("SIMDRIVE_HOME", str(tmp_path))
    rec_dir = recorder.recordings_root() / "no-block"
    _write_recording_with_requires(rec_dir, None)  # no requires key

    marks = [som.Mark(id=1, x=40, y=80, w=400, h=80, text="anything", confidence=0.95)]
    _patch_replay_observe(monkeypatch, marks)
    _patch_tap_noop(monkeypatch)

    s = _replay_session(tmp_path)
    result = recorder.replay("no-block", s, on_drift="force")
    assert result["ok"] is True
    assert "_simdrive_warning" in result
    assert "requires" in result["_simdrive_warning"].lower()


def test_replay_halt_on_state_mismatch_false_proceeds_with_warning(tmp_path, monkeypatch):
    from simdrive import recorder, som

    monkeypatch.setenv("SIMDRIVE_HOME", str(tmp_path))
    rec_dir = recorder.recordings_root() / "warn-only"
    _write_recording_with_requires(rec_dir, {
        "app": {}, "sim": {},
        "initial_state": {"text_subset_forbidden": ["Don't Allow"]},
    })

    marks = [som.Mark(id=1, x=40, y=80, w=200, h=60, text="Don't Allow", confidence=0.95)]
    _patch_replay_observe(monkeypatch, marks)
    _patch_tap_noop(monkeypatch)

    s = _replay_session(tmp_path)
    result = recorder.replay("warn-only", s, on_drift="force", halt_on_state_mismatch=False)
    assert result["ok"] is True
    assert "_simdrive_warning" in result
    assert "state_contract_mismatch" in result["_simdrive_warning"]


# ──────────────────────────────────────────────────────────────────────────
# Step 4 — robustness.validate_replay tolerates requires:
# ──────────────────────────────────────────────────────────────────────────


def test_validate_replay_accepts_requires_key(tmp_path, monkeypatch):
    from simdrive import recorder, server

    monkeypatch.setenv("SIMDRIVE_HOME", str(tmp_path))
    rec_dir = recorder.recordings_root() / "with-requires"
    rec_dir.mkdir(parents=True, exist_ok=True)
    snaps = rec_dir / "snapshots"
    snaps.mkdir()
    Image.new("RGB", (10, 10), (255, 0, 0)).save(snaps / "001_pre.png")
    Image.new("RGB", (10, 10), (0, 255, 0)).save(snaps / "001_post.png")

    (rec_dir / "recording.yaml").write_text(_yaml.safe_dump({
        "name": "with-requires",
        "created_at": 1.0,
        "simdrive_version": "1.0.0a9",
        "requires": {
            "app": {"bundle_id": "com.example.app"},
            "sim": {"device": "iPhone 17 Pro"},
            "initial_state": {"text_subset_required": ["Hi"]},
        },
        "steps": [{
            "id": 1, "action": "tap", "args": {"x": 1, "y": 1},
            "pre_screenshot": "snapshots/001_pre.png",
            "post_screenshot": "snapshots/001_post.png",
        }],
    }))

    result = server.tool_validate_replay({"name": "with-requires"})
    assert result["ok"] is True
    assert result["errors"] == []
