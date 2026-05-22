"""F#11: recording must persist `tap_first` focus context on `type_text`.

Repro: a `type_text(text="X", tap_first={text:"Y"})` call was previously stored
as just `{"text": "X"}` — `tap_first` dropped. Multi-field forms could not be
faithfully replayed because the replay had no focus target.

These tests cover three layers:

1. **Record** — server.tool_type_text on an active recorder persists `tap_first`
   (and `clear_first`) into step.args alongside `text`.
2. **Replay** — recorder._execute_step_for_session dispatches the recorded
   `tap_first` as an `act.tap` (sim) or `wda.tap` (device) BEFORE typing.
3. **Round-trip** — nested-dict `tap_first` survives a yaml.safe_dump +
   yaml.safe_load cycle without mutation.
4. **Backward compat** — an old recording (no `tap_first` in step.args) still
   replays cleanly: only `act.type_text` is called, no spurious tap.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml
from PIL import Image


# ─── Helpers ───────────────────────────────────────────────────────────────


def _make_sim_session(tmp_path: Path, sid: str = "f11-sim",
                      udid: str = "SIM-F11-UDID-AAAAA",
                      screenshot_w: int = 1170,
                      screenshot_h: int = 2532,
                      last_marks: list | None = None):
    """Build a minimal simulator Session and register it."""
    from simdrive import session as ses_mod
    from simdrive.sim import Device

    ses_mod._SESSIONS.clear()
    device = Device(udid=udid, name="iPhone 16 Pro", os_version="26.0", state="active")
    workdir = tmp_path / "sessions" / sid
    workdir.mkdir(parents=True, exist_ok=True)
    s = ses_mod.Session(
        session_id=sid,
        device=device,
        workdir=workdir,
        target="simulator",
        last_screenshot_w=screenshot_w,
        last_screenshot_h=screenshot_h,
        last_marks=last_marks or [],
    )
    ses_mod._SESSIONS[sid] = s
    return s


def _fake_observe_factory(tmp_path: Path, marks=None, w=1170, h=2532, colour=(210, 210, 210)):
    """observe() stub: writes a flat-grey PNG and returns an Observation."""
    from simdrive.observe import Observation

    def _fake_observe(udid, out_dir, **kwargs):
        out_dir = Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / f"obs_{len(list(out_dir.iterdir()))}.png"
        Image.new("RGB", (w, h), colour).save(path)
        return Observation(
            screenshot_path=path,
            annotated_path=None,
            screenshot_w=w,
            screenshot_h=h,
            window_bounds=None,
            captured_at=0.0,
            marks=list(marks or []),
        )

    return _fake_observe


# ─── Test 1: Record path persists tap_first ─────────────────────────────────


def test_type_text_recording_persists_tap_first(tmp_path, monkeypatch):
    """server.tool_type_text on an active recorder MUST persist tap_first + clear_first."""
    monkeypatch.setenv("SIMDRIVE_HOME", str(tmp_path))

    from simdrive import act, hid_inject, observe as obs_mod, recorder, server, som

    # Two marks: one is the "you@example.com" placeholder we tap_first, the other
    # is the field-after that we type into (mark resolution happens via text).
    marks = [
        som.Mark(id=1, x=200, y=400, w=300, h=80, text="you@example.com", confidence=0.95),
        som.Mark(id=2, x=200, y=600, w=300, h=80, text="Other", confidence=0.95),
    ]
    monkeypatch.setattr(obs_mod, "observe", _fake_observe_factory(tmp_path, marks=marks))

    # Stub all act/hid dispatch so the test never touches a real simulator.
    monkeypatch.setattr(act, "tap", lambda *a, **kw: None)
    monkeypatch.setattr(act, "type_text", lambda *a, **kw: None)
    monkeypatch.setattr(act, "press_key", lambda *a, **kw: None)
    monkeypatch.setattr(hid_inject, "chord", lambda *a, **kw: None)
    # _backend() is consulted inside act.type_text; we patched act.type_text but the
    # response logic also calls act._backend(). Stub it to a known value.
    monkeypatch.setattr(act, "_backend", lambda: "hid")

    s = _make_sim_session(tmp_path, last_marks=[m.to_dict() for m in marks])
    s.last_screenshot_path = tmp_path / "stub_pre.png"
    Image.new("RGB", (1170, 2532), (210, 210, 210)).save(s.last_screenshot_path)

    recorder.start(s, "tap-first-record")

    server.tool_type_text({
        "session_id": s.session_id,
        "text": "dogfood@synctek.io",
        "tap_first": {"text": "you@example.com"},
        "clear_first": True,
    })

    yaml_path = recorder.stop(s)
    payload = yaml.safe_load(yaml_path.read_text())
    steps = payload.get("steps", [])
    assert len(steps) >= 1, "type_text on active recorder must append at least one step"
    step = next(st for st in steps if st["action"] == "type_text")
    args = step["args"]
    assert args.get("text") == "dogfood@synctek.io"
    assert args.get("tap_first") == {"text": "you@example.com"}, (
        f"tap_first dropped from recording (F#11). Got: {args!r}"
    )
    assert args.get("clear_first") is True, (
        f"clear_first dropped from recording. Got: {args!r}"
    )


# ─── Test 2: Replay dispatches tap_first before type_text ───────────────────


def test_replay_dispatches_tap_first_before_typing(tmp_path, monkeypatch):
    """A recorded step with tap_first MUST tap the resolved target before typing on replay."""
    monkeypatch.setenv("SIMDRIVE_HOME", str(tmp_path))

    from simdrive import act, observe as obs_mod, recorder, som

    # Build a fixture recording with one type_text step that has tap_first set.
    rec_dir = recorder.recordings_root() / "tap-first-replay"
    snaps = rec_dir / "snapshots"
    snaps.mkdir(parents=True, exist_ok=True)
    pre = snaps / "001_pre.png"
    post = snaps / "001_post.png"
    Image.new("RGB", (1170, 2532), (210, 210, 210)).save(pre)
    Image.new("RGB", (1170, 2532), (210, 210, 210)).save(post)

    payload = {
        "name": "tap-first-replay",
        "created_at": 0.0,
        "target": "simulator",
        "device": "iPhone 16 Pro",
        "os_version": "26.0",
        "app_bundle_id": "com.replay.app",
        "simdrive_version": "1.0.0",
        "screenshot_size_pixels": [1170, 2532],
        "tags": [],
        "steps": [
            {
                "id": 1,
                "action": "type_text",
                "args": {
                    "text": "dogfood@synctek.io",
                    "tap_first": {"text": "you@example.com"},
                    "screenshot_w": 1170,
                    "screenshot_h": 2532,
                },
                "pre_screenshot": "snapshots/001_pre.png",
                "post_screenshot": "snapshots/001_post.png",
                "captured_at": 0.0,
            }
        ],
    }
    (rec_dir / "recording.yaml").write_text(yaml.safe_dump(payload, sort_keys=False))

    # Live observe + marks: the "you@example.com" mark is on screen.
    live_marks = [
        som.Mark(id=1, x=200, y=400, w=300, h=80, text="you@example.com", confidence=0.95),
    ]
    monkeypatch.setattr(obs_mod, "observe", _fake_observe_factory(tmp_path, marks=live_marks))

    # Patch _observe_for_replay so it returns the live marks for stable-id/text resolution.
    from simdrive.observe import Observation

    def _fake_obs_for_replay(session):
        out_dir = session.workdir / "replay"
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / f"live_{len(list(out_dir.iterdir()))}.png"
        Image.new("RGB", (1170, 2532), (210, 210, 210)).save(path)
        return {
            "screenshot_path": path,
            "marks": [m.to_dict() for m in live_marks],
            "marks_count": len(live_marks),
            "screenshot_w": 1170,
            "screenshot_h": 2532,
        }

    monkeypatch.setattr(recorder, "_observe_for_replay", _fake_obs_for_replay, raising=False)

    # Capture act.tap + act.type_text calls.
    tap_calls: list[tuple] = []
    type_calls: list[tuple] = []
    monkeypatch.setattr(act, "tap", lambda *a, **kw: tap_calls.append((a, kw)) or (0, 0))
    monkeypatch.setattr(act, "type_text", lambda *a, **kw: type_calls.append((a, kw)))

    s = _make_sim_session(tmp_path, sid="f11-replay")

    # Replay with no state contract (force) so we focus only on dispatch ordering.
    result = recorder.replay("tap-first-replay", s, on_drift="force",
                              halt_on_state_mismatch=False)

    # Replay should succeed and dispatch tap THEN type_text.
    assert result.get("ok") is True, f"Replay failed: {result}"
    assert len(tap_calls) >= 1, (
        f"act.tap should be called for tap_first focus before typing. tap_calls={tap_calls}"
    )
    assert len(type_calls) == 1, f"act.type_text expected once, got {type_calls}"
    # tap_first target {text:"you@example.com"} resolves to mark center (200+150, 400+40)
    # = (350, 440). Just verify the first positional args contain that center, scale-agnostic.
    tap_args = tap_calls[0][0]
    assert tap_args[0] == 350 and tap_args[1] == 440, (
        f"tap_first should resolve to mark center (350, 440), got {tap_args[:2]}"
    )
    # And type_text was called with the recorded text.
    type_args = type_calls[0][0]
    assert type_args[0] == "dogfood@synctek.io", (
        f"type_text args should be the recorded text, got {type_args}"
    )


# ─── Test 3: YAML round-trip preserves nested tap_first dict ────────────────


def test_tap_first_yaml_roundtrip_preserves_nested_dict(tmp_path):
    """A nested-dict tap_first must survive yaml.safe_dump → safe_load unchanged."""
    payload = {
        "name": "rt",
        "steps": [
            {
                "id": 1,
                "action": "type_text",
                "args": {
                    "text": "dogfood@synctek.io",
                    "tap_first": {"text": "you@example.com"},
                    "clear_first": True,
                },
                "pre_screenshot": "snapshots/001_pre.png",
                "post_screenshot": "snapshots/001_post.png",
                "captured_at": 1.0,
            }
        ],
    }
    path = tmp_path / "rt.yaml"
    path.write_text(yaml.safe_dump(payload, sort_keys=False))
    loaded = yaml.safe_load(path.read_text())
    assert loaded == payload, "YAML round-trip mutated the nested tap_first dict"
    # Specifically the nested dict identity:
    assert loaded["steps"][0]["args"]["tap_first"] == {"text": "you@example.com"}


# ─── Test 4: Backward compat — pre-fix recordings without tap_first ─────────


def test_legacy_recording_without_tap_first_still_replays(tmp_path, monkeypatch):
    """A recording with only {text:...} on type_text MUST replay without crashing,
    and MUST NOT spuriously dispatch a tap.
    """
    monkeypatch.setenv("SIMDRIVE_HOME", str(tmp_path))

    from simdrive import act, observe as obs_mod, recorder

    rec_dir = recorder.recordings_root() / "legacy-no-tap-first"
    snaps = rec_dir / "snapshots"
    snaps.mkdir(parents=True, exist_ok=True)
    pre = snaps / "001_pre.png"
    post = snaps / "001_post.png"
    Image.new("RGB", (1170, 2532), (210, 210, 210)).save(pre)
    Image.new("RGB", (1170, 2532), (210, 210, 210)).save(post)

    payload = {
        "name": "legacy-no-tap-first",
        "created_at": 0.0,
        "target": "simulator",
        "device": "iPhone 16 Pro",
        "os_version": "26.0",
        "app_bundle_id": "com.legacy.app",
        "simdrive_version": "1.0.0a3",
        "screenshot_size_pixels": [1170, 2532],
        "tags": [],
        "steps": [
            {
                "id": 1,
                "action": "type_text",
                "args": {
                    "text": "legacy text",
                    "screenshot_w": 1170,
                    "screenshot_h": 2532,
                },
                "pre_screenshot": "snapshots/001_pre.png",
                "post_screenshot": "snapshots/001_post.png",
                "captured_at": 0.0,
            }
        ],
    }
    (rec_dir / "recording.yaml").write_text(yaml.safe_dump(payload, sort_keys=False))

    monkeypatch.setattr(obs_mod, "observe", _fake_observe_factory(tmp_path))

    def _fake_obs_for_replay(session):
        out_dir = session.workdir / "replay"
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / f"live_{len(list(out_dir.iterdir()))}.png"
        Image.new("RGB", (1170, 2532), (210, 210, 210)).save(path)
        return {
            "screenshot_path": path,
            "marks": [],
            "marks_count": 0,
            "screenshot_w": 1170,
            "screenshot_h": 2532,
        }

    monkeypatch.setattr(recorder, "_observe_for_replay", _fake_obs_for_replay, raising=False)

    tap_calls: list[tuple] = []
    type_calls: list[tuple] = []
    monkeypatch.setattr(act, "tap", lambda *a, **kw: tap_calls.append((a, kw)) or (0, 0))
    monkeypatch.setattr(act, "type_text", lambda *a, **kw: type_calls.append((a, kw)))

    s = _make_sim_session(tmp_path, sid="f11-legacy")

    result = recorder.replay(
        "legacy-no-tap-first", s, on_drift="force", halt_on_state_mismatch=False
    )
    assert result.get("ok") is True, f"Legacy replay failed: {result}"
    assert len(tap_calls) == 0, (
        f"Legacy recording (no tap_first) MUST NOT trigger a tap; got {tap_calls}"
    )
    assert len(type_calls) == 1, f"type_text expected once, got {type_calls}"
    assert type_calls[0][0][0] == "legacy text"
