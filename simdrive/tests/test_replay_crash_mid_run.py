"""FU-2026-071 — the replay crash cross-check against how a real crash behaves.

The original cross-check (INIT-2026-641 item 4.7) ran only after the last
step, against a mocked `list_crashes` that returned the crash immediately.
Driving a real crash (TestKitApp's SIMDRIVE_CRASH_ON_TAP trigger, see
tests/test_live_replay_crash_injection.py) showed two things the mock hid:

  * A crash at step 10 of 15 changes the screen, so step 11's pre-check
    halts with halt_reason="drift" and returns early — the crash check at the
    end of replay() never ran. The crash was reported as drift.
  * The simulator's ReportCrash writes the `.ips` ~15 s after the process
    dies (measured 2026-10-05: captureTime 23:04:25, file written 23:04:40),
    so an immediate lookup finds nothing even when it does run.

These hermetic tests pin both fixes. The live test proves them end to end.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest
import yaml
from PIL import Image

from simdrive import diagnostics
from simdrive import recorder as rec_mod

GREY = (210, 210, 210)
BLACK = (0, 0, 0)
BUNDLE = "io.synctek.specterqa.testkit"
# Captured at import, before conftest's autouse fixture stubs it per test.
_REAL_APP_DIED = rec_mod._app_died_during_replay


def _make_sim_session(tmp_path: Path, sid: str = "crash-mid-run"):
    from simdrive import session as ses_mod
    from simdrive.sim import Device

    ses_mod._SESSIONS.clear()
    device = Device(udid="SIM-CRASH-MID", name="iPhone 17 Pro", os_version="26.1", state="active")
    workdir = tmp_path / "sessions" / sid
    workdir.mkdir(parents=True, exist_ok=True)
    s = ses_mod.Session(
        session_id=sid, device=device, workdir=workdir, target="simulator",
        last_screenshot_w=1206, last_screenshot_h=2622, last_marks=[],
        app_bundle_id=BUNDLE,
    )
    ses_mod._SESSIONS[sid] = s
    return s


def _write_recording(rec_dir: Path, steps: int) -> None:
    snaps = rec_dir / "snapshots"
    snaps.mkdir(parents=True, exist_ok=True)
    step_list = []
    for i in range(1, steps + 1):
        Image.new("RGB", (1206, 2622), GREY).save(snaps / f"{i:03d}_pre.png")
        Image.new("RGB", (1206, 2622), GREY).save(snaps / f"{i:03d}_post.png")
        step_list.append({
            "id": i, "action": "tap",
            "args": {"x": 600, "y": 250, "screenshot_w": 1206, "screenshot_h": 2622},
            "pre_screenshot": f"snapshots/{i:03d}_pre.png",
            "post_screenshot": f"snapshots/{i:03d}_post.png",
            "captured_at": float(i),
        })
    (rec_dir / "recording.yaml").write_text(yaml.safe_dump({
        "name": rec_dir.name, "created_at": 0.0, "target": "simulator",
        "device": "iPhone 17 Pro", "os_version": "26.1",
        "app_bundle_id": BUNDLE, "simdrive_version": "test", "steps": step_list,
    }, sort_keys=False))


class _App:
    """A fake app that dies on the `crash_at`-th tap: from then on the live
    screen is the (black) home screen, and — like ReportCrash — the crash
    report only becomes visible after `report_delay_calls` further lookups."""

    def __init__(self, crash_at: int, report_delay_calls: int = 0, writes_report: bool = True):
        self.crash_at = crash_at
        self.taps = 0
        self.crashed_at_ts: float | None = None
        self.report_delay_calls = report_delay_calls
        self.writes_report = writes_report
        self.lookups = 0

    def tap(self, *a, **kw):
        if self.crashed_at_ts is not None:
            raise RuntimeError("tap after crash should not be needed by these tests")
        self.taps += 1
        if self.taps == self.crash_at:
            self.crashed_at_ts = time.time()

    def observe(self, session):
        out_dir = Path(session.workdir) / "replay"
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / f"live-{time.time_ns()}.png"
        Image.new("RGB", (1206, 2622), BLACK if self.crashed_at_ts else GREY).save(path)
        return {"screenshot_path": path, "marks_count": 0, "marks": [],
                "screenshot_w": 1206, "screenshot_h": 2622}

    def list_crashes(self, since_ts=0.0, bundle_id=None, max_results=10, **kw):
        self.lookups += 1
        if self.crashed_at_ts is None or not self.writes_report:
            return []
        if self.lookups <= self.report_delay_calls:
            return []
        return [{
            "path": "/fake/TestKitApp.ips", "name": "TestKitApp.ips",
            "timestamp": "", "exception": "EXC_BREAKPOINT", "bundle_id": BUNDLE,
            "mtime": self.crashed_at_ts + 15.0,
            "captured_at": self.crashed_at_ts,
            "backtrace_first_lines": [],
        }]


@pytest.fixture
def app(monkeypatch, tmp_path):
    def _install(**kw):
        a = _App(**kw)
        from simdrive import act
        monkeypatch.setattr(act, "tap", a.tap)
        monkeypatch.setattr(rec_mod, "_observe_for_replay", a.observe, raising=False)
        monkeypatch.setattr(diagnostics, "list_crashes", a.list_crashes)
        monkeypatch.setattr(rec_mod, "_CRASH_REPORT_POLL_S", 0.0)
        monkeypatch.setattr(rec_mod, "recordings_root", lambda: tmp_path / "recordings")
        return a
    return _install


class TestCrashMidReplayIsReportedAsACrash:
    def test_crash_at_step_10_of_15_is_crash_detected_not_drift(self, tmp_path, monkeypatch, app):
        app(crash_at=10)
        monkeypatch.setattr(rec_mod, "_app_died_during_replay", lambda s: True)
        s = _make_sim_session(tmp_path)
        _write_recording(tmp_path / "recordings" / "crash10", steps=15)

        result = rec_mod.replay("crash10", s)

        assert result["ok"] is False
        assert result["halt_reason"] == "crash_detected"
        # What the replay would otherwise have said, kept for the reader.
        assert result["halt_reason_before_crash_check"] == "drift"
        assert result["halted_at"] == 11
        assert result["crash_after_step"] == 10
        assert result["crashes"][0]["bundle_id"] == BUNDLE
        assert sum(1 for st in result["steps"] if st["executed"]) == 10

    def test_report_written_late_is_waited_for_when_the_app_died(self, tmp_path, monkeypatch, app):
        a = app(crash_at=3, report_delay_calls=4)
        monkeypatch.setattr(rec_mod, "_app_died_during_replay", lambda s: True)
        s = _make_sim_session(tmp_path)
        _write_recording(tmp_path / "recordings" / "late_report", steps=5)

        result = rec_mod.replay("late_report", s)

        assert result["halt_reason"] == "crash_detected"
        assert a.lookups == 5  # 1 immediate + 4 polls before the report appeared

    def test_app_gone_without_any_report_is_app_exited(self, tmp_path, monkeypatch, app):
        app(crash_at=2, writes_report=False)
        monkeypatch.setattr(rec_mod, "_app_died_during_replay", lambda s: True)
        monkeypatch.setattr(rec_mod, "_CRASH_REPORT_FLUSH_WAIT_S", 0.05)
        s = _make_sim_session(tmp_path)
        _write_recording(tmp_path / "recordings" / "exited", steps=4)

        result = rec_mod.replay("exited", s)

        assert result["ok"] is False
        assert result["halt_reason"] == "app_exited"
        assert result["halt_reason_before_crash_check"] == "drift"
        assert "crashes" not in result

    def test_live_app_on_a_failing_run_costs_one_lookup_and_no_wait(self, tmp_path, monkeypatch, app):
        a = app(crash_at=3, writes_report=False)
        monkeypatch.setattr(rec_mod, "_app_died_during_replay", lambda s: False)
        s = _make_sim_session(tmp_path)
        _write_recording(tmp_path / "recordings" / "plain_drift", steps=4)

        result = rec_mod.replay("plain_drift", s)

        assert result["halt_reason"] == "drift"
        assert a.lookups == 1

    def test_passing_run_never_probes_liveness(self, tmp_path, monkeypatch, app):
        app(crash_at=99)
        probed = []
        monkeypatch.setattr(rec_mod, "_app_died_during_replay", lambda s: probed.append(1) or True)
        s = _make_sim_session(tmp_path)
        _write_recording(tmp_path / "recordings" / "clean", steps=3)

        result = rec_mod.replay("clean", s)

        assert result["ok"] is True
        assert probed == []

    def test_execute_error_after_crash_reports_the_crash(self, tmp_path, monkeypatch, app):
        a = app(crash_at=2)
        monkeypatch.setattr(rec_mod, "_app_died_during_replay", lambda s: True)

        def _boom(step, session, live_obs=None):
            a.tap()
            if a.crashed_at_ts:
                raise RuntimeError("HID inject failed: app not running")
        monkeypatch.setattr(rec_mod, "_execute_step_for_session", _boom)
        s = _make_sim_session(tmp_path)
        _write_recording(tmp_path / "recordings" / "exec_err", steps=4)

        result = rec_mod.replay("exec_err", s, on_drift="warn")

        assert result["halt_reason"] == "crash_detected"
        assert result["halt_reason_before_crash_check"] == "execute_error"
        assert result["crash_after_step"] == 2


class TestCrashReportCaptureTime:
    def test_list_crashes_reports_capture_time_not_write_time(self, tmp_path):
        """The header `timestamp` is when ReportCrash WROTE the file (~15 s
        after the crash); the body's `captureTime` is the crash itself, and is
        what step attribution must use."""
        header = {"app_name": "TestKitApp", "timestamp": "2026-10-05 23:04:40.00 -0400",
                  "bundleID": BUNDLE, "bug_type": "309"}
        body = {"captureTime": "2026-10-05 23:04:25.0388 -0400", "procName": "TestKitApp"}
        p = tmp_path / "TestKitApp-2026-10-05-230440.ips"
        p.write_text(json.dumps(header) + "\n" + json.dumps(body, indent=2))

        [crash] = diagnostics.list_crashes(bundle_id=BUNDLE, reports_dir=tmp_path)

        from datetime import datetime
        expected = datetime.strptime("2026-10-05 23:04:25.0388 -0400", "%Y-%m-%d %H:%M:%S.%f %z")
        assert crash["captured_at"] == pytest.approx(expected.timestamp())

    def test_unparseable_capture_time_is_none(self, tmp_path):
        p = tmp_path / "x.ips"
        p.write_text(json.dumps({"bundleID": BUNDLE}) + "\n{not json")
        [crash] = diagnostics.list_crashes(bundle_id=BUNDLE, reports_dir=tmp_path)
        assert crash["captured_at"] is None


class TestAppDiedProbe:
    """The real `_app_died_during_replay` (conftest stubs it for every other
    hermetic test)."""

    def _session(self, tmp_path, target="simulator"):
        s = _make_sim_session(tmp_path)
        s.target = target
        return s

    def _states(self, monkeypatch, states):
        seq = iter(states)
        calls = []

        def _fake(udid, bundle_id):
            calls.append(1)
            nxt = next(seq)
            if isinstance(nxt, Exception):
                raise nxt
            return nxt
        monkeypatch.setattr(diagnostics, "app_state", _fake)
        monkeypatch.setattr(rec_mod, "_APP_STATE_RECHECK_S", 0.0)
        return calls

    def test_two_not_running_reads_means_dead(self, tmp_path, monkeypatch):
        self._states(monkeypatch, [{"state": "not-running"}, {"state": "not-running"}])
        assert _REAL_APP_DIED(self._session(tmp_path)) is True

    def test_one_flap_is_not_dead(self, tmp_path, monkeypatch):
        self._states(monkeypatch, [{"state": "not-running"}, {"state": "foreground"}])
        assert _REAL_APP_DIED(self._session(tmp_path)) is False

    def test_simctl_failure_is_not_dead(self, tmp_path, monkeypatch):
        calls = self._states(monkeypatch, [{"state": "not-running", "detail": "Unable to lookup"}])
        assert _REAL_APP_DIED(self._session(tmp_path)) is False
        assert len(calls) == 1

    def test_exception_is_not_dead(self, tmp_path, monkeypatch):
        self._states(monkeypatch, [RuntimeError("simctl gone")])
        assert _REAL_APP_DIED(self._session(tmp_path)) is False

    def test_device_session_never_probes(self, tmp_path, monkeypatch):
        calls = self._states(monkeypatch, [])
        assert _REAL_APP_DIED(self._session(tmp_path, target="device")) is False
        assert calls == []
