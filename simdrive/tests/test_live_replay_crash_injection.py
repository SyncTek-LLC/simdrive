"""FU-2026-071 — live crash injection: a REAL app crash mid-replay, caught.

Every other crash cross-check test mocks ``list_crashes``. This one records a
real 15-tap journey in TestKitApp, relaunches the app with
``SIMDRIVE_CRASH_ON_TAP=10`` (TestKitApp calls ``fatalError`` on the 10th tap),
replays the journey, and requires replay to report the crash — read from the
real ``.ips`` ReportCrash wrote — as having followed step 10.

Marked ``live``; skipped in normal runs. Needs a booted simulator you own
(never borrow another session's) with TestKitApp installed:

    U=$(xcrun simctl create simdrive-crash-live \\
        com.apple.CoreSimulator.SimDeviceType.iPhone-17-Pro \\
        com.apple.CoreSimulator.SimRuntime.iOS-26-3)
    xcrun simctl boot "$U" && xcrun simctl bootstatus "$U" -b
    ./TestKitApp/build.sh "$U"
    SIMDRIVE_LIVE_UDID="$U" python3.11 -m pytest \\
        simdrive/tests/test_live_replay_crash_injection.py -m live -v -s

``scripts/live_crash_injection.sh`` does all of that (and is what the
``live-crash-injection`` CI job runs).
"""
from __future__ import annotations

import json
import os
import pwd
import subprocess
import time
from pathlib import Path

import pytest

from simdrive import diagnostics, server, sim

pytestmark = pytest.mark.live

TESTKIT = "io.synctek.specterqa.testkit"
CRASH_ENV = "SIMCTL_CHILD_SIMDRIVE_CRASH_ON_TAP"
STEPS = 15
CRASH_AT = 10
# Top-centre of the navigation bar: a tap there changes nothing on screen, so
# the recorded journey is 15 identical, replayable no-op taps.
NEUTRAL_X, NEUTRAL_Y = 600, 250


@pytest.fixture
def udid() -> str:
    u = os.environ.get("SIMDRIVE_LIVE_UDID")
    if not u:
        pytest.skip("set SIMDRIVE_LIVE_UDID to a booted simulator dedicated to this test")
    res = sim._simctl("get_app_container", u, TESTKIT, timeout=15.0)
    if res.returncode != 0:
        pytest.skip(f"TestKitApp not installed on {u}: run ./TestKitApp/build.sh {u}")
    return u


@pytest.fixture
def home(tmp_path, monkeypatch) -> Path:
    monkeypatch.setenv("SIMDRIVE_HOME", str(tmp_path))
    monkeypatch.delenv(CRASH_ENV, raising=False)
    # conftest points HOME at a temp dir before simdrive is imported, so
    # diagnostics' module-level reports dir would be an empty temp folder.
    # The simulator's ReportCrash writes to the REAL user's DiagnosticReports.
    real_home = Path(pwd.getpwuid(os.getuid()).pw_dir)
    monkeypatch.setattr(diagnostics, "_DIAGNOSTIC_REPORTS_DIR",
                        real_home / "Library" / "Logs" / "DiagnosticReports")
    return tmp_path


def _terminate(udid: str) -> None:
    """Best effort: on a freshly booted CI simulator `simctl terminate` of an
    app that is not running has been seen to hang past 10 s."""
    try:
        sim._simctl("terminate", udid, TESTKIT, timeout=30.0)
    except subprocess.TimeoutExpired:
        pass
    time.sleep(0.5)


def _fresh_session(udid: str, crash_on_tap: int | None) -> str:
    """Relaunch TestKitApp (simctl launch inherits SIMCTL_CHILD_* from this
    process's environment) and open a session on it."""
    _terminate(udid)
    if crash_on_tap is not None:
        os.environ[CRASH_ENV] = str(crash_on_tap)
    try:
        res = server.tool_session_start(
            {"udid": udid, "app_bundle_id": TESTKIT, "replace_existing": True}
        )
    finally:
        os.environ.pop(CRASH_ENV, None)
    assert res["state"] == "active", res
    time.sleep(2.0)  # first frame + the injector attaching on the next runloop turn
    return res["session_id"]


def test_real_crash_at_step_10_of_15_is_caught(udid, home):
    name = "fu071-crash-at-10"

    # 1. Record 15 no-op taps on a healthy app.
    sid = _fresh_session(udid, crash_on_tap=None)
    server.tool_record_start({"session_id": sid, "name": name})
    for _ in range(STEPS):
        server.tool_tap({"session_id": sid, "x": NEUTRAL_X, "y": NEUTRAL_Y})
        time.sleep(0.3)
    stop = server.tool_record_stop({"session_id": sid})
    assert stop["steps"] == STEPS, stop
    server.tool_session_end({"session_id": sid})

    # 2. Control: the same recording replays clean when nothing crashes, so
    #    the crash below is the only thing that can fail the run.
    sid = _fresh_session(udid, crash_on_tap=None)
    control = server.tool_replay({"session_id": sid, "name": name})
    server.tool_session_end({"session_id": sid})
    assert control["ok"] is True, json.dumps(control, default=str)[:2000]
    assert sum(1 for s in control["steps"] if s["executed"]) == STEPS

    # 3. Relaunch armed to crash on the 10th tap and replay.
    sid = _fresh_session(udid, crash_on_tap=CRASH_AT)
    replay_started = time.time()
    result = server.tool_replay({"session_id": sid, "name": name})
    try:
        server.tool_session_end({"session_id": sid})
    except Exception:
        pass
    print("\nLIVE REPLAY RESULT:\n" + server._format_replay_summary(name, result))
    print(json.dumps({k: result.get(k) for k in (
        "ok", "halt_reason", "halt_reason_before_crash_check", "halted_at",
        "crash_after_step", "crashes")}, default=str, indent=2))

    assert result["ok"] is False
    assert result["halt_reason"] == "crash_detected", json.dumps(result, default=str)[:3000]
    assert result["crash_after_step"] == CRASH_AT
    assert sum(1 for s in result["steps"] if s["executed"]) == CRASH_AT
    crash = result["crashes"][0]
    assert crash["bundle_id"] == TESTKIT
    assert crash["captured_at"] is not None and crash["captured_at"] >= replay_started
    ips = Path(crash["path"])
    assert ips.exists()
    assert "CrashInjector.TapCounter.tapped()" in ips.read_text(errors="replace"), (
        "the crash report must be TestKitApp's injected fatalError, not some other crash"
    )
    # And it is the same report the public `crashes` lookup returns.
    assert any(c["path"] == crash["path"] for c in diagnostics.list_crashes(
        since_ts=replay_started, bundle_id=TESTKIT))
