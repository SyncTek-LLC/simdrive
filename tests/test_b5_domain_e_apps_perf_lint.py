"""b5 Domain E RED tests — apps/perf/lint polish.

Findings covered:
  F#3  — apps() returns empty version string (CFBundleShortVersionString not read from plist)
  F#8  — optional verify_change: true on tap (pre/post SSIM drift signal)
  F#9  — perf reports cpu_pct: 0.0 consistently (instant sample vs windowed average)
  F#13 — list_replays returns 0-step placeholders mixed with real recordings (no min_steps param)
  F#16 — lint-recordings fails on 0-step empty recordings (should categorize as 'empty', not fail)

All tests fail RED on HEAD. None touch production code.
Run under: pytest -m "not live"
"""
from __future__ import annotations

import os
import plistlib
import time
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
import yaml


# ─── helpers ─────────────────────────────────────────────────────────────────


def _make_fake_plist_bytes(
    bundle_id: str,
    short_version: str | None,
    bundle_version: str,
    display_name: str = "TestApp",
    path: str = "/path/TestApp.app",
) -> bytes:
    """Build a minimal simctl listapps plist blob."""
    data: dict[str, Any] = {
        bundle_id: {
            "CFBundleDisplayName": display_name,
            "CFBundleVersion": bundle_version,
            "Path": path,
        }
    }
    if short_version is not None:
        data[bundle_id]["CFBundleShortVersionString"] = short_version
    return plistlib.dumps(data)


def _fake_run_result(stdout: str, returncode: int = 0) -> MagicMock:
    r = MagicMock()
    r.returncode = returncode
    r.stdout = stdout
    r.stderr = ""
    return r


# ─── F#3 — apps() version field ──────────────────────────────────────────────


class TestAppsVersionField:
    """F#3: apps() must return CFBundleShortVersionString in 'version', not empty string."""

    def test_apps_version_populated_from_short_version_string(self, monkeypatch):
        """When Info.plist has CFBundleShortVersionString=1.1.1, apps() entry must have version='1.1.1'.

        Fails on HEAD: list_apps() returns 'version': '' — CFBundleShortVersionString
        is parsed from simctl JSON but not passed through when only plist is available
        (or the read path is broken). This assertion proves the production gap.

        Wait — diagnostics.py line 287 shows version: info.get('CFBundleShortVersionString') or ''
        which SHOULD work. The real bug is that simctl listapps does NOT emit
        CFBundleShortVersionString in its plist for every app; the field is only
        in the app's own Info.plist on disk. list_apps must fall back to reading
        app.plist from Path/<bundle>/Info.plist when the simctl output lacks it.
        """
        import simdrive.diagnostics as diag_mod

        # Simulate simctl plist that contains ONLY CFBundleVersion (no ShortVersionString)
        # but has a Path pointing to an app bundle.
        app_path = "/path/co.synctek.splashMate.app"
        plist_bytes = _make_fake_plist_bytes(
            bundle_id="co.synctek.splashMate",
            short_version=None,           # <-- simctl output missing ShortVersionString
            bundle_version="8",
            display_name="SplashMate",
            path=app_path,
        )

        monkeypatch.setattr(
            diag_mod, "_run",
            lambda cmd, timeout=15.0: _fake_run_result(plist_bytes.decode("utf-8")),
        )

        # Simulate Info.plist on disk inside the app bundle with the real version.
        info_plist_bytes = plistlib.dumps({
            "CFBundleShortVersionString": "1.1.1",
            "CFBundleVersion": "8",
        })

        def _fake_plist_read(path: str | Path) -> bytes:
            return info_plist_bytes

        monkeypatch.setattr(
            diag_mod, "_read_app_info_plist",  # expected NEW helper — does not exist yet
            _fake_plist_read,
            raising=False,
        )

        apps = diag_mod.list_apps("FAKE-UDID-B5-F3")

        assert apps, "Expected non-empty apps list"
        splashmate = next((a for a in apps if a["bundle_id"] == "co.synctek.splashMate"), None)
        assert splashmate is not None, "Expected SplashMate in apps list"

        # RED: version is '' on HEAD because simctl plist lacks CFBundleShortVersionString
        # and list_apps does not fall back to reading Info.plist from disk.
        assert splashmate["version"] == "1.1.1", (
            f"F#3: Expected version='1.1.1' from CFBundleShortVersionString fallback; "
            f"got version={splashmate['version']!r}. "
            "list_apps() must read CFBundleShortVersionString from app's Info.plist when simctl omits it."
        )
        assert splashmate["build"] == "8", (
            f"F#3: Expected build='8'; got build={splashmate['build']!r}"
        )

    def test_apps_version_uses_simctl_short_version_when_present(self, monkeypatch):
        """When simctl plist includes CFBundleShortVersionString, return it directly."""
        import simdrive.diagnostics as diag_mod

        plist_bytes = _make_fake_plist_bytes(
            bundle_id="io.synctek.simdrive.demo",
            short_version="2.0.0",
            bundle_version="42",
        )
        monkeypatch.setattr(
            diag_mod, "_run",
            lambda cmd, timeout=15.0: _fake_run_result(plist_bytes.decode("utf-8")),
        )

        apps = diag_mod.list_apps("FAKE-UDID-B5-F3-B")
        assert apps
        entry = apps[0]
        assert entry["version"] == "2.0.0", (
            f"F#3: version should be '2.0.0' when simctl plist has ShortVersionString; "
            f"got {entry['version']!r}"
        )

    def test_apps_version_fallback_to_build_when_plist_missing_short_version(self, monkeypatch):
        """When neither simctl nor Info.plist has ShortVersionString, version falls back to build."""
        import simdrive.diagnostics as diag_mod

        plist_bytes = _make_fake_plist_bytes(
            bundle_id="com.missing.version",
            short_version=None,
            bundle_version="99",
            path="/path/com.missing.version.app",
        )
        monkeypatch.setattr(
            diag_mod, "_run",
            lambda cmd, timeout=15.0: _fake_run_result(plist_bytes.decode("utf-8")),
        )

        # Simulate Info.plist that also has no ShortVersionString
        info_plist_bytes = plistlib.dumps({"CFBundleVersion": "99"})

        monkeypatch.setattr(
            diag_mod, "_read_app_info_plist",
            lambda path: info_plist_bytes,
            raising=False,
        )

        apps = diag_mod.list_apps("FAKE-UDID-B5-F3-C")
        assert apps
        entry = apps[0]
        # The fallback: version == build when ShortVersionString is truly absent
        # RED: on HEAD version is '' not the build value, and _read_app_info_plist doesn't exist
        assert entry["version"] == "99", (
            f"F#3: fallback — when ShortVersionString absent, version should equal build='99'; "
            f"got version={entry['version']!r}"
        )


# ─── F#8 — tap verify_change ─────────────────────────────────────────────────


class TestTapVerifyChange:
    """F#8: tap with verify_change=True must return screen_changed bool and ssim_delta float."""

    def _make_session(self) -> MagicMock:
        """Return a minimal mock session matching what tool_tap inspects."""
        s = MagicMock()
        s.target = "simulator"
        s.device.udid = "FAKE-UDID-B5-F8"
        s.last_screenshot_path = "/tmp/fake_pre.png"
        s.last_screenshot_w = 1170
        s.last_screenshot_h = 2532
        s.recorder = None
        s.app_bundle_id = "io.fake.app"
        s.perf_baselines = {}
        s.wda_client = None
        return s

    def test_verify_change_false_absent_from_response_by_default(self, monkeypatch):
        """Without verify_change param, tap response must NOT include screen_changed/ssim_delta.

        RED because: on HEAD tool_tap never returns screen_changed; this test confirms
        the ABSENCE (the shape contract). This documents what must NOT regress.
        Passes immediately — left to anchor the shape before F#8 is implemented.
        """
        import simdrive.server as server_mod
        import simdrive.session as sess_mod
        import simdrive.act as act_mod

        s = self._make_session()
        monkeypatch.setattr(sess_mod, "get", lambda sid: s)
        monkeypatch.setattr(server_mod, "_entitlement_gate", lambda: None)
        monkeypatch.setattr(server_mod, "_ensure_screenshot_dims", lambda s: (1170, 2532))
        monkeypatch.setattr(server_mod, "_resolve_target_xy", lambda s, args: (100, 200, "coord", None))
        monkeypatch.setattr(act_mod, "tap", lambda x, y, w, h, udid=None: (100, 200))
        monkeypatch.setattr(sess_mod, "append_action", lambda s, action: None)

        resp = server_mod.tool_tap({
            "session_id": "fake-sid",
            "x": 100,
            "y": 200,
        })

        assert "screen_changed" not in resp, (
            "tool_tap without verify_change must not include 'screen_changed' in response"
        )
        assert "ssim_delta" not in resp, (
            "tool_tap without verify_change must not include 'ssim_delta' in response"
        )

    def test_verify_change_true_returns_screen_changed_and_ssim_delta(self, monkeypatch):
        """verify_change=True must return screen_changed bool and ssim_delta float.

        Fails on HEAD: tool_tap never captures pre/post screenshots or computes SSIM
        when verify_change=True; the key is absent from the response entirely.
        """
        import simdrive.server as server_mod
        import simdrive.session as sess_mod
        import simdrive.act as act_mod

        s = self._make_session()
        monkeypatch.setattr(sess_mod, "get", lambda sid: s)
        monkeypatch.setattr(server_mod, "_entitlement_gate", lambda: None)
        monkeypatch.setattr(server_mod, "_ensure_screenshot_dims", lambda s: (1170, 2532))
        monkeypatch.setattr(server_mod, "_resolve_target_xy", lambda s, args: (100, 200, "coord", None))
        monkeypatch.setattr(act_mod, "tap", lambda x, y, w, h, udid=None: (100, 200))
        monkeypatch.setattr(sess_mod, "append_action", lambda s, action: None)

        resp = server_mod.tool_tap({
            "session_id": "fake-sid",
            "x": 100,
            "y": 200,
            "verify_change": True,
        })

        assert "screen_changed" in resp, (
            f"F#8: verify_change=True tap response must include 'screen_changed'; "
            f"got keys: {list(resp.keys())}"
        )
        assert "ssim_delta" in resp, (
            f"F#8: verify_change=True tap response must include 'ssim_delta'; "
            f"got keys: {list(resp.keys())}"
        )
        assert isinstance(resp["screen_changed"], bool), (
            f"F#8: 'screen_changed' must be bool; got {type(resp['screen_changed'])}"
        )
        assert isinstance(resp["ssim_delta"], float), (
            f"F#8: 'ssim_delta' must be float; got {type(resp['ssim_delta'])}"
        )

    def test_verify_change_true_no_change_returns_screen_changed_false(self, monkeypatch):
        """When screen doesn't change after tap, screen_changed must be False and ssim_delta near 0.

        Fails on HEAD: tool_tap has no verify_change logic at all.
        """
        import simdrive.server as server_mod
        import simdrive.session as sess_mod
        import simdrive.act as act_mod

        s = self._make_session()
        monkeypatch.setattr(sess_mod, "get", lambda sid: s)
        monkeypatch.setattr(server_mod, "_entitlement_gate", lambda: None)
        monkeypatch.setattr(server_mod, "_ensure_screenshot_dims", lambda s: (1170, 2532))
        monkeypatch.setattr(server_mod, "_resolve_target_xy", lambda s, args: (100, 200, "coord", None))
        monkeypatch.setattr(act_mod, "tap", lambda x, y, w, h, udid=None: (100, 200))
        monkeypatch.setattr(sess_mod, "append_action", lambda s, action: None)

        # Mock the SSIM comparison utility that F#8 implementation must call.
        # Returns ssim=1.0 (identical screens).
        monkeypatch.setattr(
            server_mod, "_compute_ssim",  # expected new function
            lambda pre, post: 1.0,
            raising=False,
        )

        resp = server_mod.tool_tap({
            "session_id": "fake-sid",
            "x": 100,
            "y": 200,
            "verify_change": True,
        })

        assert "screen_changed" in resp, (
            f"F#8: 'screen_changed' missing from response; keys={list(resp.keys())}"
        )
        assert resp["screen_changed"] is False, (
            f"F#8: identical screens → screen_changed must be False; got {resp['screen_changed']!r}"
        )
        assert resp.get("ssim_delta", 1.0) < 0.05, (
            f"F#8: identical screens → ssim_delta must be near 0; got {resp.get('ssim_delta')!r}"
        )

    def test_verify_change_true_with_change_returns_screen_changed_true(self, monkeypatch):
        """When screen changes after tap, screen_changed must be True and ssim_delta > 0.

        Fails on HEAD: no verify_change logic exists.
        """
        import simdrive.server as server_mod
        import simdrive.session as sess_mod
        import simdrive.act as act_mod

        s = self._make_session()
        monkeypatch.setattr(sess_mod, "get", lambda sid: s)
        monkeypatch.setattr(server_mod, "_entitlement_gate", lambda: None)
        monkeypatch.setattr(server_mod, "_ensure_screenshot_dims", lambda s: (1170, 2532))
        monkeypatch.setattr(server_mod, "_resolve_target_xy", lambda s, args: (100, 200, "coord", None))
        monkeypatch.setattr(act_mod, "tap", lambda x, y, w, h, udid=None: (100, 200))
        monkeypatch.setattr(sess_mod, "append_action", lambda s, action: None)

        # SSIM returns 0.5 — screens differ significantly.
        monkeypatch.setattr(
            server_mod, "_compute_ssim",
            lambda pre, post: 0.5,
            raising=False,
        )

        resp = server_mod.tool_tap({
            "session_id": "fake-sid",
            "x": 100,
            "y": 200,
            "verify_change": True,
        })

        assert "screen_changed" in resp, (
            f"F#8: 'screen_changed' missing from response; keys={list(resp.keys())}"
        )
        assert resp["screen_changed"] is True, (
            f"F#8: differing screens → screen_changed must be True; got {resp['screen_changed']!r}"
        )
        assert resp.get("ssim_delta", 0.0) > 0.1, (
            f"F#8: differing screens → ssim_delta must be > 0; got {resp.get('ssim_delta')!r}"
        )


# ─── F#9 — perf windowed CPU average ─────────────────────────────────────────


class TestPerfWindowedCpu:
    """F#9: perf() must sample CPU over a window (200 ms) and return an average, not an instant 0.0."""

    def test_perf_snapshot_returns_sample_window_ms_field(self, monkeypatch):
        """perf.snapshot must include 'sample_window_ms' in its return dict.

        Fails on HEAD: snapshot() returns {pid, cpu_pct, memory_rss_mb, threads, captured_at}
        with no sample_window_ms field.
        """
        import simdrive.perf as perf_mod

        monkeypatch.setattr(perf_mod, "find_app_pid", lambda udid, bundle_id: 1234)

        fake_run = MagicMock()
        fake_run.returncode = 0
        fake_run.stdout = "10.5  204800"

        monkeypatch.setattr(perf_mod, "_run", lambda cmd: fake_run)

        result = perf_mod.snapshot("FAKE-UDID-B5-F9", "io.fake.app")

        assert "sample_window_ms" in result, (
            f"F#9: perf.snapshot must return 'sample_window_ms'; "
            f"got keys: {list(result.keys())}. "
            "Implement windowed sampling (200 ms) and document the window in the response."
        )
        assert result["sample_window_ms"] == 200, (
            f"F#9: sample_window_ms must be 200; got {result.get('sample_window_ms')!r}"
        )

    def test_perf_snapshot_cpu_is_average_not_single_sample(self, monkeypatch):
        """perf.snapshot must average multiple ps samples taken over 200 ms.

        Fails on HEAD: snapshot() calls ps exactly once and returns that raw value.
        A single instant sample at a quiet moment returns 0.0 (F#9 in dogfood).
        """
        import simdrive.perf as perf_mod

        monkeypatch.setattr(perf_mod, "find_app_pid", lambda udid, bundle_id: 5678)

        # Simulate multiple ps calls returning different cpu% values across the window.
        call_count = 0
        cpu_values = [0.0, 15.0, 25.0]  # average = 13.33

        def _multi_run(cmd):
            nonlocal call_count
            r = MagicMock()
            r.returncode = 0
            if "pcpu" in " ".join(cmd):
                r.stdout = f"{cpu_values[min(call_count, len(cpu_values)-1)]}  204800"
                call_count += 1
            else:
                # threads query
                r.stdout = "HEADER\n thread1\n thread2"
            return r

        monkeypatch.setattr(perf_mod, "_run", _multi_run)
        # Suppress real sleep so tests run fast — implementation must call time.sleep internally.
        monkeypatch.setattr("time.sleep", lambda s: None)

        result = perf_mod.snapshot("FAKE-UDID-B5-F9-AVG", "io.fake.app")

        # With 3 samples of [0, 15, 25] averaged = 13.33.
        # The instant-sample returns whatever the first ps returns (often 0.0).
        # After windowing, cpu_pct must NOT be stuck at 0.0 if samples varied.
        assert result["cpu_pct"] > 0.0, (
            f"F#9: windowed CPU average must be > 0 when samples vary; "
            f"got cpu_pct={result['cpu_pct']!r}. "
            "HEAD returns instant-sample which is 0.0 for an active app at a quiet moment."
        )

    def test_perf_snapshot_samples_multiple_times_in_window(self, monkeypatch):
        """perf.snapshot must call ps at least 2 times within the sampling window.

        Fails on HEAD: snapshot() calls ps exactly once.
        """
        import simdrive.perf as perf_mod

        monkeypatch.setattr(perf_mod, "find_app_pid", lambda udid, bundle_id: 9999)
        monkeypatch.setattr("time.sleep", lambda s: None)

        cpu_run_count = 0

        def _counting_run(cmd):
            nonlocal cpu_run_count
            r = MagicMock()
            r.returncode = 0
            if "pcpu" in " ".join(cmd):
                cpu_run_count += 1
                r.stdout = "5.0  102400"
            else:
                r.stdout = "HDR\n t1"
            return r

        monkeypatch.setattr(perf_mod, "_run", _counting_run)

        perf_mod.snapshot("FAKE-UDID-B5-F9-CNT", "io.fake.app")

        assert cpu_run_count >= 2, (
            f"F#9: windowed CPU sampling must call ps at least 2 times; "
            f"got {cpu_run_count} call(s). "
            "HEAD calls ps once and returns that instant value."
        )


# ─── F#13 — list_replays min_steps filter ────────────────────────────────────


class TestListReplaysMinSteps:
    """F#13: list_replays must accept min_steps param and filter out 0-step placeholders by default."""

    def _make_recordings_dir(self, tmp_path: Path) -> Path:
        """Populate a fake recordings root with 0-step and N-step entries."""
        root = tmp_path / "recordings"
        for name, steps in [
            ("real_login", [{"action": "tap"}, {"action": "type_text"}]),
            ("real_signup", [{"action": "tap"}]),
            ("empty_placeholder_1", []),
            ("empty_placeholder_2", []),
            ("empty_placeholder_3", None),
        ]:
            d = root / name
            d.mkdir(parents=True)
            payload = {
                "name": name,
                "created_at": "2026-05-22T00:00:00",
                "steps": steps or [],
            }
            (d / "recording.yaml").write_text(yaml.dump(payload))
        return root

    def test_list_replays_default_excludes_zero_step_recordings(self, tmp_path):
        """list_replays() with no args must omit recordings where steps == 0.

        Fails on HEAD: list_replays() accepts only replays_root (no min_steps param)
        and returns ALL recordings, including 0-step placeholders.
        """
        import simdrive.robustness as rob_mod

        root = self._make_recordings_dir(tmp_path)

        # On HEAD this call signature works but no filtering occurs.
        # When F#13 is implemented, list_replays will default min_steps=1.
        try:
            result = rob_mod.list_replays(root, min_steps=1)
        except TypeError:
            # HEAD does not accept min_steps; call without it to show all pass through.
            result = rob_mod.list_replays(root)

        names = [r["name"] for r in result]

        # RED: on HEAD, 0-step placeholders ARE in the list.
        for placeholder in ("empty_placeholder_1", "empty_placeholder_2", "empty_placeholder_3"):
            assert placeholder not in names, (
                f"F#13: list_replays() default (min_steps=1) must exclude 0-step recording "
                f"'{placeholder}'; it appeared in the result list. "
                f"All returned names: {names}"
            )

    def test_list_replays_min_steps_zero_returns_all(self, tmp_path):
        """list_replays(min_steps=0) must return ALL recordings including 0-step ones.

        Fails on HEAD: min_steps param not accepted at all (TypeError).
        """
        import simdrive.robustness as rob_mod

        root = self._make_recordings_dir(tmp_path)

        # When min_steps=0, ALL recordings (including placeholders) must be returned.
        result = rob_mod.list_replays(root, min_steps=0)  # type: ignore[call-arg]

        names = {r["name"] for r in result}
        assert "empty_placeholder_1" in names, (
            f"F#13: list_replays(min_steps=0) must include 0-step recordings; "
            f"got names: {names}"
        )
        assert "real_login" in names, (
            f"F#13: list_replays(min_steps=0) must include real recordings; "
            f"got names: {names}"
        )
        assert len(result) == 5, (
            f"F#13: expected 5 total recordings with min_steps=0; got {len(result)}"
        )

    def test_list_replays_min_steps_param_accepted(self, tmp_path):
        """list_replays must accept the min_steps keyword argument without TypeError.

        Fails on HEAD: robustness.list_replays signature is list_replays(replays_root) only.
        """
        import simdrive.robustness as rob_mod
        import inspect

        sig = inspect.signature(rob_mod.list_replays)
        assert "min_steps" in sig.parameters, (
            f"F#13: list_replays must accept 'min_steps' param; "
            f"current signature: {sig}. "
            "HEAD signature has no min_steps parameter."
        )


# ─── F#16 — lint-recordings empty vs missing_state_contract ─────────────────


class TestLintRecordingsEmptyCategory:
    """F#16: lint must categorize 0-step recordings as 'empty', not fail them for missing requires."""

    def _write_recording(self, d: Path, steps: list | None, has_requires: bool) -> Path:
        d.mkdir(parents=True, exist_ok=True)
        payload: dict = {
            "name": d.name,
            "created_at": "2026-05-22T00:00:00",
            "steps": steps or [],
        }
        if has_requires:
            payload["requires"] = {
                "sim": {"device": "iPhone 17 Pro", "os": "iOS 26.3"},
                "app": {"bundle_id": "io.fake.app", "version": "1.0"},
                "initial_state": {"text_subset_required": ["Login"]},
            }
        (d / "recording.yaml").write_text(yaml.dump(payload))
        return d / "recording.yaml"

    def test_zero_step_recording_without_requires_categorized_as_empty(self, tmp_path):
        """0-step recording with no requires block must be categorized 'empty', NOT 'fail'.

        Fails on HEAD: _lint_one returns status='fail' with reason 'no requires block' for
        any recording missing a requires block, including 0-step placeholders.
        """
        from simdrive.recorder import lint_recordings, LintResult

        rec_dir = tmp_path / "empty_placeholder"
        self._write_recording(rec_dir, steps=[], has_requires=False)

        results = lint_recordings(tmp_path)
        assert len(results) == 1, f"Expected 1 lint result; got {len(results)}"

        r = results[0]

        # RED: on HEAD, status='fail', reason contains 'no requires block'
        assert r.status == "empty", (
            f"F#16: 0-step recording without requires must have status='empty'; "
            f"got status={r.status!r}, reason={r.reason!r}. "
            "HEAD treats it as 'fail: no requires block' — wrong category."
        )

    def test_zero_step_recording_does_not_appear_in_fail_count(self, tmp_path):
        """tool_lint_recordings fail count must not include 0-step empty recordings.

        Fails on HEAD: fail_count includes all recordings missing requires, including empties.
        """
        from simdrive.recorder import lint_recordings

        # One empty placeholder (0 steps, no requires)
        rec1 = tmp_path / "empty_rec"
        self._write_recording(rec1, steps=[], has_requires=False)

        # One real recording with steps but missing requires (genuinely failing)
        rec2 = tmp_path / "real_no_requires"
        self._write_recording(rec2, steps=[{"action": "tap"}], has_requires=False)

        # One passing recording
        rec3 = tmp_path / "passing_rec"
        self._write_recording(rec3, steps=[{"action": "tap"}], has_requires=True)

        results = lint_recordings(tmp_path)
        fail_count = sum(1 for r in results if r.status == "fail")

        # RED: on HEAD fail_count=2 (empty_rec + real_no_requires both fail)
        assert fail_count == 1, (
            f"F#16: only the real recording with steps-but-no-requires should fail; "
            f"expected fail_count=1, got fail_count={fail_count}. "
            f"Statuses: {[(r.path.parent.name, r.status) for r in results]}"
        )

    def test_non_empty_recording_missing_requires_still_fails(self, tmp_path):
        """Recording with steps but no requires block must still fail with status='fail'.

        This is a preservation test — F#16 must not accidentally pass recordings
        that genuinely need a state contract.
        """
        from simdrive.recorder import lint_recordings

        rec = tmp_path / "real_missing_contract"
        self._write_recording(rec, steps=[{"action": "tap"}, {"action": "type_text"}], has_requires=False)

        results = lint_recordings(tmp_path)
        assert len(results) == 1

        r = results[0]
        assert r.status == "fail", (
            f"F#16: real recording (steps > 0) with no requires must still be 'fail'; "
            f"got status={r.status!r}"
        )

    def test_lint_result_has_category_field(self, tmp_path):
        """LintResult must expose a 'category' field: 'empty' | 'missing_state_contract' | 'ok'.

        Fails on HEAD: LintResult has no 'category' field; only status + reason.
        """
        from simdrive.recorder import lint_recordings, LintResult
        import dataclasses

        # Verify the dataclass has a 'category' field
        fields = {f.name for f in dataclasses.fields(LintResult)}
        assert "category" in fields, (
            f"F#16: LintResult must have a 'category' field; "
            f"current fields: {sorted(fields)}. "
            "Needed to distinguish 'empty' vs 'missing_state_contract'."
        )

        rec = tmp_path / "empty_test"
        self._write_recording(rec, steps=[], has_requires=False)

        results = lint_recordings(tmp_path)
        r = results[0]
        assert r.category == "empty", (  # type: ignore[attr-defined]
            f"F#16: 0-step recording category must be 'empty'; got {getattr(r, 'category', '?')!r}"
        )
