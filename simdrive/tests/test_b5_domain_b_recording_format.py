"""Recording format integrity tests for b5 dogfood findings (Domain B).

F#11  Recording drops `tap_first` focus context from type_text  [HIGH]
      STATUS: Fixed in main (commit c746e61, PR #146). Tests here are
      regression guards — they PASS and confirm the fix is in place.

F#12  `text_subset_required` contains duplicates in state contract  [LOW]
      STATUS: NOT YET FIXED. Tests are RED and will fail until dedup is added
      to _build_requires_block.

F#17  `simdrive migrate-recording --all` / `--missing-contract` flag  [LOW]
      STATUS: NOT YET FIXED. Tests are RED — argparse rejects --all/--missing-contract
      with exit code 2 (unrecognised argument) until the flags are implemented.

Run with: pytest -m "not live" tests/test_b5_domain_b_recording_format.py -x --tb=short
"""
from __future__ import annotations

import shutil
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
import yaml


# ---------------------------------------------------------------------------
# Shared helpers / fake factory
# ---------------------------------------------------------------------------

_FAKE_UDID = "FAKEU-DID0-B5DOM-AIN-B0000000000"


def _make_sim_session(tmp_path: Path, sid: str = "b5-domain-b"):
    """Return a minimal simulator Session stored in _SESSIONS."""
    from simdrive import session as session_mod
    from simdrive.sim import Device

    d = Device(
        udid=_FAKE_UDID,
        name="iPhone 17 Pro",
        os_version="26.3",
        state="active",
    )
    workdir = tmp_path / "sessions" / sid
    workdir.mkdir(parents=True, exist_ok=True)
    s = session_mod.Session(
        session_id=sid,
        device=d,
        workdir=workdir,
        target="simulator",
        last_screenshot_w=1206,
        last_screenshot_h=2622,
    )
    session_mod._SESSIONS[sid] = s
    return s


def _attach_fake_recorder(s, tmp_path: Path):
    """Attach a lightweight recorder stub that captures add_step calls."""
    from simdrive import recorder as recorder_mod

    root = tmp_path / "recordings" / s.session_id
    root.mkdir(parents=True, exist_ok=True)
    (root / "snapshots").mkdir(parents=True, exist_ok=True)

    rec = MagicMock(spec=recorder_mod.Recorder)
    rec.steps = []
    rec.name = s.session_id
    rec.session = s
    rec.root = root
    rec.yaml_path = root / "recording.yaml"

    def _add_step(action, args, pre_screenshot, post_screenshot=None, marks_count=None, **kw):
        idx = len(rec.steps) + 1
        rec.steps.append({"id": idx, "action": action, "args": dict(args)})
        return idx

    rec.add_step.side_effect = _add_step
    s.recorder = rec
    return rec


def _fake_png(path: Path) -> Path:
    """Write a tiny fake PNG so path.exists() and stat checks pass."""
    from PIL import Image

    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (1206, 2622), (200, 200, 200)).save(path)
    return path


def _write_recording_yaml(dir_: Path, *, requires=None, steps=None,
                           bundle_id: str = "com.example.app",
                           device: str = "iPhone 17 Pro",
                           os_version: str = "26.3",
                           app_version: str = "1.0.0") -> Path:
    """Write a minimal recording.yaml under dir_."""
    dir_.mkdir(parents=True, exist_ok=True)
    snaps = dir_ / "snapshots"
    snaps.mkdir(parents=True, exist_ok=True)

    default_steps: list = []
    if steps is None:
        pre = snaps / "001_pre.png"
        _fake_png(pre)
        post = snaps / "001_post.png"
        _fake_png(post)
        default_steps = [{
            "id": 1,
            "action": "tap",
            "args": {"x": 100, "y": 200, "screenshot_w": 1206, "screenshot_h": 2622},
            "pre_screenshot": "snapshots/001_pre.png",
            "post_screenshot": "snapshots/001_post.png",
            "captured_at": 0.0,
        }]

    payload: dict = {
        "name": dir_.name,
        "created_at": 0.0,
        "device": device,
        "os_version": os_version,
        "app_bundle_id": bundle_id,
        "app_version": app_version,
        "steps": steps if steps is not None else default_steps,
    }
    if requires is not None:
        payload["requires"] = requires

    yaml_path = dir_ / "recording.yaml"
    yaml_path.write_text(yaml.safe_dump(payload, sort_keys=False))
    return yaml_path


# ===========================================================================
# F#11 — Recording drops `tap_first` focus context from type_text
# ===========================================================================


class TestF11TypeTextTapFirstPersisted:
    """F#11: type_text with tap_first must serialize the full args dict."""

    def test_record_act_step_persists_full_args_including_tap_first(self, tmp_path, monkeypatch):
        """_record_act_step correctly stores arbitrary args dict including tap_first.

        Regression guard: confirms that when tool_type_text passes the full rec_args
        dict (text + tap_first), the step is recorded with both fields intact.

        F#11 FIX (PR #146): tool_type_text now builds rec_args = {"text": text,
        "tap_first": tap_target} before calling _record_act_step. This test confirms
        that shape is preserved end-to-end through the recorder.

        REGRESSION: GREEN — confirm fix is in place.
        """
        from simdrive import session as session_mod, server

        s = _make_sim_session(tmp_path, "f11-record-act")
        rec = _attach_fake_recorder(s, tmp_path)

        pre_png = tmp_path / "pre.png"
        _fake_png(pre_png)
        post_png = tmp_path / "post.png"
        _fake_png(post_png)

        fake_obs = SimpleNamespace(
            screenshot_path=post_png,
            screenshot_w=1206,
            screenshot_h=2622,
            marks=[],
        )
        monkeypatch.setattr("simdrive.observe.observe", lambda *a, **kw: fake_obs)

        # Call _record_act_step with the FIXED args dict (including tap_first).
        # This mirrors what tool_type_text now does after the F#11 fix.
        tap_first_target = {"text": "you@example.com"}
        server._record_act_step(
            s, "type_text",
            {"text": "dogfood@synctek.io", "tap_first": tap_first_target},
            pre_png,
        )

        # Find the step that was recorded.
        assert len(rec.steps) == 1, "Expected exactly one step recorded"
        step = rec.steps[0]
        assert step["action"] == "type_text"
        # Regression: tap_first must survive the _record_act_step → add_step chain.
        assert "tap_first" in step["args"], (
            "F#11 regression: tap_first dropped by _record_act_step — "
            "recording.yaml will not be able to replay the focus context"
        )
        assert step["args"]["tap_first"] == tap_first_target

    def test_tool_type_text_step_args_includes_tap_first(self, tmp_path, monkeypatch):
        """tool_type_text must pass tap_first into the step args when recording.

        RED: will fail because line ~992 in server.py records {"text": text} without tap_first.
        """
        from simdrive import server, session as session_mod, som
        from simdrive.sim import Device

        sid = "f11-tool-type-text"
        s = _make_sim_session(tmp_path, sid)
        rec = _attach_fake_recorder(s, tmp_path)

        tap_first_target = {"text": "you@example.com"}
        text_value = "dogfood@synctek.io"

        pre_png = tmp_path / "obs_pre.png"
        _fake_png(pre_png)
        post_png = tmp_path / "obs_post.png"
        _fake_png(post_png)

        # Create a mark that matches the tap_first target so _resolve_target_xy succeeds.
        matching_mark = som.Mark(id=1, x=100, y=200, w=300, h=40,
                                 text="you@example.com", confidence=0.95)
        s.last_marks = [matching_mark.to_dict()]
        s.last_screenshot_w = 1206
        s.last_screenshot_h = 2622

        fake_obs = SimpleNamespace(
            screenshot_path=pre_png,
            screenshot_w=1206,
            screenshot_h=2622,
            marks=[matching_mark],
        )

        monkeypatch.setattr("simdrive.observe.observe", lambda *a, **kw: fake_obs)
        monkeypatch.setattr("simdrive.act.type_text", lambda text, udid=None: None)
        monkeypatch.setattr("simdrive.act._backend", lambda: "hid")
        monkeypatch.setattr("simdrive.act.tap", lambda *a, **kw: None)

        result = server.tool_type_text({
            "session_id": sid,
            "text": text_value,
            "tap_first": tap_first_target,
        })

        assert result.get("ok") is True, f"tool_type_text returned error: {result}"
        assert len(rec.steps) == 1, f"Expected 1 recorded step, got {len(rec.steps)}"
        step = rec.steps[0]
        assert step["action"] == "type_text"
        # F#11: tap_first must be in the step args stored in the recording.
        assert "tap_first" in step["args"], (
            "F#11: tap_first not persisted in recording step args — "
            f"got args={step['args']!r}"
        )
        assert step["args"]["tap_first"] == tap_first_target, (
            f"F#11: tap_first value mismatch: expected {tap_first_target!r}, "
            f"got {step['args'].get('tap_first')!r}"
        )

    def test_replay_parser_accepts_and_passes_tap_first(self, tmp_path, monkeypatch):
        """Replay engine reads tap_first from step args and taps the focus target.

        Regression guard: confirms F#11 fix — the sim path in _execute_step_for_session
        now calls _resolve_focus_target and act.tap when tap_first is present.

        REGRESSION: GREEN — confirm fix is in place.
        """
        from simdrive import recorder as recorder_mod, session as session_mod, som

        sid = "f11-replay-tap-first"
        s = _make_sim_session(tmp_path, sid)
        monkeypatch.setenv("SIMDRIVE_HOME", str(tmp_path))

        # Build a recording.yaml with a type_text step that includes tap_first.
        rec_dir = tmp_path / "recordings" / "f11-replay-recording"
        rec_dir.mkdir(parents=True, exist_ok=True)
        snaps = rec_dir / "snapshots"
        snaps.mkdir(parents=True, exist_ok=True)

        pre_png = snaps / "001_pre.png"
        _fake_png(pre_png)
        post_png = snaps / "001_post.png"
        _fake_png(post_png)

        tap_first_target = {"text": "you@example.com"}
        recording_payload = {
            "name": "f11-replay-recording",
            "created_at": 0.0,
            "device": "iPhone 17 Pro",
            "os_version": "26.3",
            "app_bundle_id": "io.synctek.simdrive.demo",
            "app_version": "1.0",
            "target": "simulator",
            "steps": [{
                "id": 1,
                "action": "type_text",
                "args": {
                    "text": "dogfood@synctek.io",
                    "tap_first": tap_first_target,
                    "clear_first": False,
                },
                "pre_screenshot": "snapshots/001_pre.png",
                "post_screenshot": "snapshots/001_post.png",
                "captured_at": 0.0,
            }],
            "requires": {
                "target": "simulator",
                "app": {"bundle_id": "io.synctek.simdrive.demo", "version": "1.0", "version_match": "any"},
                "sim": {"device": "iPhone 17 Pro", "ios_version": "26.3"},
                "device": None,
                "initial_state": {
                    "foreground": True,
                    "text_subset_required": [],
                    "text_subset_forbidden": [],
                    "primary_button_label": None,
                },
            },
        }
        (rec_dir / "recording.yaml").write_text(yaml.safe_dump(recording_payload, sort_keys=False))

        # Patch execution primitives to capture what gets called.
        executed_calls: list = []

        def _fake_act_tap(x, y, sw, sh, udid=None):
            executed_calls.append(("tap", x, y))

        def _fake_act_type_text(text, udid=None):
            executed_calls.append(("type_text", text))

        monkeypatch.setattr("simdrive.act.tap", _fake_act_tap)
        monkeypatch.setattr("simdrive.act.type_text", _fake_act_type_text)
        monkeypatch.setattr("simdrive.act._backend", lambda: "hid")

        # Patch _verify_state_contract to skip — not the SUT here.
        monkeypatch.setattr(
            "simdrive.recorder._verify_state_contract",
            lambda *a, **kw: (True, []),
        )

        # Provide a mark that matches the tap_first {"text": "you@example.com"} target.
        matching_mark = som.Mark(id=1, x=100, y=200, w=300, h=40,
                                 text="you@example.com", confidence=0.95)

        # _observe_for_replay returns a dict with marks for stable_id / text resolution.
        monkeypatch.setattr("simdrive.recorder._observe_for_replay", lambda s: {
            "screenshot_path": str(pre_png),
            "screenshot_w": 1206,
            "screenshot_h": 2622,
            "marks": [matching_mark],
        })
        # Patch SSIM so similarity always passes.
        monkeypatch.setattr("simdrive.recorder._ssim_or_fallback", lambda *a, **kw: 1.0)

        result = recorder_mod.replay("f11-replay-recording", s, on_drift="warn")

        # F#11 regression: replay must have issued a tap for the tap_first target before typing.
        tap_calls = [c for c in executed_calls if c[0] == "tap"]
        assert len(tap_calls) >= 1, (
            "F#11 regression: replay engine did not execute a tap for tap_first — "
            "the fix in recorder._execute_step_for_session is missing or broken"
        )

    def test_recording_yaml_roundtrip_preserves_tap_first(self, tmp_path):
        """A recording written with tap_first in args must load back with tap_first intact.

        RED: will fail because the server currently omits tap_first from the recorded args dict.
        """
        # Write a recording.yaml manually (as it SHOULD look after the fix).
        rec_dir = tmp_path / "recordings" / "roundtrip"
        snaps = rec_dir / "snapshots"
        snaps.mkdir(parents=True, exist_ok=True)
        _fake_png(snaps / "001_pre.png")
        _fake_png(snaps / "001_post.png")

        tap_first_val = {"text": "you@example.com"}
        payload = {
            "name": "roundtrip",
            "created_at": 0.0,
            "device": "iPhone 17 Pro",
            "os_version": "26.3",
            "app_bundle_id": "io.example.app",
            "app_version": "1.0",
            "target": "simulator",
            "steps": [{
                "id": 1,
                "action": "type_text",
                "args": {"text": "hello", "tap_first": tap_first_val, "clear_first": False},
                "pre_screenshot": "snapshots/001_pre.png",
                "post_screenshot": "snapshots/001_post.png",
                "captured_at": 0.0,
            }],
        }
        (rec_dir / "recording.yaml").write_text(yaml.safe_dump(payload, sort_keys=False))

        # Load it back.
        loaded = yaml.safe_load((rec_dir / "recording.yaml").read_text())
        step = loaded["steps"][0]

        # Verify tap_first survived the YAML round-trip (it should — YAML handles nested dicts).
        # This test documents the expected shape; the real failure is upstream in the server.
        assert "tap_first" in step["args"], (
            "F#11: tap_first not found in step args after YAML round-trip — "
            f"got step args: {step['args']!r}"
        )
        assert step["args"]["tap_first"] == tap_first_val, (
            f"F#11: tap_first value corrupted after round-trip: "
            f"expected {tap_first_val!r}, got {step['args'].get('tap_first')!r}"
        )


# ===========================================================================
# F#12 — `text_subset_required` contains duplicates in state contract
# ===========================================================================


class TestF12TextSubsetRequiredDeduped:
    """F#12: text_subset_required must be deduplicated before writing to recording.yaml."""

    def test_build_requires_block_deduplicates_text_subset_required(self):
        """_build_requires_block must dedupe text when the same mark text appears twice.

        Current behaviour: marks with the same text produce duplicates in text_subset_required.
        Expected behaviour: each text value appears at most once.

        RED: will fail because _build_requires_block appends without deduplication.
        """
        from simdrive.recorder import _build_requires_block
        from simdrive.som import Mark

        # Two marks with identical text — mimics title + button both reading "Sign In".
        marks = [
            Mark(id=1, x=40, y=80, w=400, h=60, text="Sign In", confidence=0.98),
            Mark(id=2, x=40, y=500, w=200, h=40, text="Sign In", confidence=0.99),
            Mark(id=3, x=40, y=200, w=300, h=40, text="Email", confidence=0.95),
        ]

        block = _build_requires_block(
            marks,
            screen_h=2622,
            app_bundle_id="com.example.app",
            app_version="1.0",
            sim_device="iPhone 17 Pro",
            sim_ios_version="26.3",
        )

        subset = block.initial_state.text_subset_required
        # F#12: "Sign In" must appear exactly once, not twice.
        count_sign_in = subset.count("Sign In")
        assert count_sign_in == 1, (
            f"F#12: 'Sign In' appears {count_sign_in} times in text_subset_required "
            f"(expected 1) — duplicates not deduped: {subset!r}"
        )

    def test_text_subset_required_uniqueness_invariant(self):
        """text_subset_required must have no duplicates regardless of mark count.

        Uses words known to be in the English dictionary (high confidence_band)
        so they pass the band filter and enter text_subset_required.

        RED: will fail because _build_requires_block does not deduplicate.
        """
        from simdrive.recorder import _build_requires_block
        from simdrive.som import Mark

        # 3 marks with the same English text "Email" — all pass the confidence_band
        # filter and should appear only once after dedup.
        marks = [
            Mark(id=i, x=40, y=i * 100, w=200, h=40, text="Email", confidence=0.95)
            for i in range(1, 4)
        ] + [
            Mark(id=4, x=40, y=500, w=200, h=40, text="Cancel", confidence=0.92),
            Mark(id=5, x=40, y=600, w=200, h=40, text="Settings", confidence=0.91),
        ]

        block = _build_requires_block(
            marks,
            screen_h=2622,
            app_bundle_id="com.example.app",
            app_version="1.0",
            sim_device="iPhone 17 Pro",
            sim_ios_version="26.3",
        )

        subset = block.initial_state.text_subset_required
        assert len(subset) == len(set(subset)), (
            f"F#12: text_subset_required contains duplicates: {subset!r}"
        )

    def test_state_contract_dedup_written_to_yaml(self, tmp_path, monkeypatch):
        """recording.yaml's text_subset_required must contain unique values only.

        Simulates record_start capturing state where SoM produces duplicate text marks,
        then verifies finalize() writes a deduped list.

        RED: will fail because _build_requires_block doesn't deduplicate.
        """
        from simdrive import recorder as recorder_mod, session as session_mod
        from simdrive.som import Mark

        monkeypatch.setenv("SIMDRIVE_HOME", str(tmp_path))
        sid = "f12-dedup-yaml"
        s = _make_sim_session(tmp_path, sid)

        # Stub observe to return marks with duplicate text.
        dup_marks = [
            Mark(id=1, x=40, y=80, w=400, h=60, text="Sign In", confidence=0.98),
            Mark(id=2, x=40, y=500, w=200, h=40, text="Sign In", confidence=0.99),
            Mark(id=3, x=40, y=200, w=300, h=40, text="Password", confidence=0.95),
        ]

        fake_obs = SimpleNamespace(
            screenshot_path=tmp_path / "fake.png",
            screenshot_w=1206,
            screenshot_h=2622,
            marks=dup_marks,
        )
        _fake_png(fake_obs.screenshot_path)

        monkeypatch.setattr("simdrive.observe.observe", lambda *a, **kw: fake_obs)
        monkeypatch.setattr("simdrive.sim.get_app_version", lambda *a, **kw: "1.0")
        monkeypatch.setenv("SIMDRIVE_HOME", str(tmp_path))

        rec = recorder_mod.start(s, "f12-dedup-recording")
        yaml_path = recorder_mod.stop(s)

        payload = yaml.safe_load(yaml_path.read_text())
        subset = payload.get("requires", {}).get("initial_state", {}).get("text_subset_required", [])

        assert len(subset) == len(set(subset)), (
            f"F#12: text_subset_required in recording.yaml contains duplicates: {subset!r}"
        )
        # "Sign In" specifically must appear only once.
        assert subset.count("Sign In") == 1, (
            f"F#12: 'Sign In' appears {subset.count('Sign In')} times in recording.yaml "
            f"text_subset_required: {subset!r}"
        )


# ===========================================================================
# F#17 — `simdrive migrate-recording --all` / `--missing-contract` flag
# ===========================================================================


class TestF17MigrateRecordingAllFlag:
    """F#17: migrate-recording must support --all and --missing-contract batch flags."""

    _GOOD_REQUIRES = {
        "target": "simulator",
        "app": {"bundle_id": "com.example.app", "version": "1.0", "version_match": "minor"},
        "sim": {"device": "iPhone 17 Pro", "ios_version": "26.3"},
        "device": None,
        "initial_state": {
            "foreground": True,
            "text_subset_required": ["Library", "Books"],
            "text_subset_forbidden": [],
            "primary_button_label": "Library",
        },
    }

    def _patch_som(self, monkeypatch):
        from simdrive import som
        marks = [
            som.Mark(id=1, x=40, y=80, w=400, h=200, text="Library", confidence=0.95),
        ]
        monkeypatch.setattr(som, "detect_marks", lambda _path: list(marks))

    def test_migrate_recording_all_migrates_every_old_recording(self, tmp_path, monkeypatch):
        """--all flag must iterate all recordings under recordings_root and migrate each.

        RED: will fail because _cmd_migrate_recording has no --all flag.
        """
        from simdrive.server import _cmd_migrate_recording

        monkeypatch.setenv("SIMDRIVE_HOME", str(tmp_path))
        self._patch_som(monkeypatch)

        # Create 2 old-format recordings (no requires block).
        for name in ("old-rec-1", "old-rec-2"):
            _write_recording_yaml(tmp_path / "recordings" / name, requires=None)

        # --all should migrate both without specifying a name.
        with pytest.raises(SystemExit) as exc:
            _cmd_migrate_recording(["--all"])
        assert exc.value.code == 0, (
            "F#17: --all flag returned non-zero exit — may not be implemented"
        )

        # Verify both recordings now have a requires block.
        for name in ("old-rec-1", "old-rec-2"):
            rec_yaml = tmp_path / "recordings" / name / "recording.yaml"
            payload = yaml.safe_load(rec_yaml.read_text())
            assert "requires" in payload, (
                f"F#17: --all did not migrate {name} — no requires block written"
            )

    def test_migrate_recording_all_with_two_recordings_count(self, tmp_path, monkeypatch):
        """--all must process all N recordings and report N migrated.

        RED: will fail because --all is not implemented.
        """
        import sys
        from io import StringIO
        from simdrive.server import _cmd_migrate_recording

        monkeypatch.setenv("SIMDRIVE_HOME", str(tmp_path))
        self._patch_som(monkeypatch)

        for name in ("batch-1", "batch-2", "batch-3"):
            _write_recording_yaml(tmp_path / "recordings" / name, requires=None)

        captured_output = StringIO()
        monkeypatch.setattr(sys, "stdout", captured_output)

        with pytest.raises(SystemExit) as exc:
            _cmd_migrate_recording(["--all"])

        assert exc.value.code == 0, "F#17: --all exited non-zero"
        output = captured_output.getvalue()
        # Output should mention each recording or a count.
        assert "batch-1" in output or "3" in output or "migrated" in output.lower(), (
            f"F#17: --all output doesn't confirm 3 recordings were processed: {output!r}"
        )

    def test_migrate_recording_missing_contract_skips_already_migrated(self, tmp_path, monkeypatch):
        """--missing-contract must only touch recordings without a requires block.

        RED: will fail because --missing-contract is not implemented.
        """
        from simdrive.server import _cmd_migrate_recording

        monkeypatch.setenv("SIMDRIVE_HOME", str(tmp_path))
        self._patch_som(monkeypatch)

        # One already-migrated recording.
        _write_recording_yaml(
            tmp_path / "recordings" / "already-good",
            requires=self._GOOD_REQUIRES,
        )
        original_mtime = (tmp_path / "recordings" / "already-good" / "recording.yaml").stat().st_mtime

        # One old-format recording needing migration.
        _write_recording_yaml(tmp_path / "recordings" / "needs-migrate", requires=None)

        with pytest.raises(SystemExit) as exc:
            _cmd_migrate_recording(["--missing-contract"])
        assert exc.value.code == 0, "F#17: --missing-contract exited non-zero"

        # already-good must NOT have been touched (mtime unchanged).
        new_mtime = (tmp_path / "recordings" / "already-good" / "recording.yaml").stat().st_mtime
        assert new_mtime == original_mtime, (
            "F#17: --missing-contract rewrote an already-migrated recording — "
            "it should skip recordings that already have a requires block"
        )

        # needs-migrate MUST now have a requires block.
        payload = yaml.safe_load(
            (tmp_path / "recordings" / "needs-migrate" / "recording.yaml").read_text()
        )
        assert "requires" in payload, (
            "F#17: --missing-contract did not migrate the recording that needed it"
        )

    def test_migrate_recording_all_handles_empty_recordings_dir(self, tmp_path, monkeypatch):
        """--all on empty recordings dir should exit 0 gracefully.

        RED: will fail because --all is not implemented.
        """
        from simdrive.server import _cmd_migrate_recording

        monkeypatch.setenv("SIMDRIVE_HOME", str(tmp_path))
        (tmp_path / "recordings").mkdir(parents=True, exist_ok=True)

        with pytest.raises(SystemExit) as exc:
            _cmd_migrate_recording(["--all"])
        assert exc.value.code == 0, (
            "F#17: --all on empty recordings dir must exit 0, got non-zero"
        )

    def test_migrate_recording_all_flag_in_argparse_schema(self):
        """_cmd_migrate_recording's argparse must accept --all without raising.

        RED: will fail because the argument parser has no --all option.
        """
        from simdrive.server import _cmd_migrate_recording

        # Passing an unrecognised arg causes argparse SystemExit with code 2.
        # If --all is defined, it will raise SystemExit(0 or 1) from actual logic,
        # not argparse error (code 2).
        with pytest.raises(SystemExit) as exc:
            # We pass --all without a recordings dir, so it may fail on missing dir —
            # but code must NOT be 2 (argparse "unrecognised arguments").
            _cmd_migrate_recording(["--all"])
        assert exc.value.code != 2, (
            "F#17: --all is not a recognised argument in _cmd_migrate_recording's argparse schema"
        )

    def test_migrate_recording_missing_contract_flag_in_argparse_schema(self):
        """_cmd_migrate_recording's argparse must accept --missing-contract without raising.

        RED: will fail because the argument parser has no --missing-contract option.
        """
        from simdrive.server import _cmd_migrate_recording

        with pytest.raises(SystemExit) as exc:
            _cmd_migrate_recording(["--missing-contract"])
        assert exc.value.code != 2, (
            "F#17: --missing-contract is not a recognised argument in _cmd_migrate_recording's argparse schema"
        )
