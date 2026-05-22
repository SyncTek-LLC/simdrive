"""F#2: session_start must verify the launched app reached foreground before
returning ``state: "active"``.

Bug reproducer (Example Reader dogfood 2026-05-22): SplashMate built without proper
iCloud entitlement crashed within ~500 ms of launch. ``tool_session_start``
returned ``state: "active"`` for an already-terminated process. The agent
wasted multiple tap/type roundtrips before discovering the crash via separate
``app_state`` and ``crashes`` calls.

Fix:
  1. When ``app_bundle_id`` is provided, after ``sim.launch_app`` succeeds,
     poll ``diagnostics.app_state`` up to 5×300 ms. Break out as soon as
     ``state == "foreground"``.
  2. If the app never reaches foreground (i.e. observed ``not-running`` at
     timeout), return ``state: "launched_then_exited"`` with
     ``crash_report_path`` (most recent .ips for that bundle since session
     start) and a ``recovery:`` hint.
  3. ``verify_launch: bool = True`` opt-out preserves legacy fire-and-forget
     behaviour when False.

Tests:
  - test_session_start_returns_active_when_app_reaches_foreground
      mock app_state to return foreground immediately → state="active",
      no crash_report_path
  - test_session_start_returns_launched_then_exited_when_app_crashes
      mock app_state to return not-running on every poll → state=
      "launched_then_exited", crash_report_path is populated from a mocked
      list_crashes result, recovery message present
  - test_session_start_verify_launch_false_skips_polling
      verify_launch=False → state="active" and app_state was never polled
      (legacy behaviour preserved)
  - test_session_start_verify_launch_no_bundle_skips_polling
      no app_bundle_id at all → polling skipped entirely (nothing to verify)
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest


# ── helpers ──────────────────────────────────────────────────────────────────


def _patch_sim_for_start(monkeypatch, udid: str = "SIM-UDID-VERIFY"):
    """Patch sim.find_device / sim.launch_app so session.start succeeds
    without touching real simctl."""
    import simdrive.sim as sim_mod
    from simdrive.sim import Device

    fake_device = Device(udid=udid, name="iPhone 17 Pro",
                         os_version="26.0", state="Booted")

    monkeypatch.setattr(
        sim_mod, "find_device",
        lambda udid=None, name=None, os_version=None: fake_device,
    )
    monkeypatch.setattr(sim_mod, "first_booted", lambda: fake_device)
    monkeypatch.setattr(sim_mod, "boot", lambda *_a, **_kw: None)
    monkeypatch.setattr(sim_mod, "launch_app", lambda *_a, **_kw: 12345)


def _patch_workroot(monkeypatch, tmp_path: Path):
    import simdrive.session as session_mod
    monkeypatch.setattr(session_mod, "_workroot", lambda: tmp_path)


# ── test 1: happy path — foreground confirmed → state=active ─────────────────


def test_session_start_returns_active_when_app_reaches_foreground(
    tmp_path, monkeypatch,
):
    import simdrive.server as server_mod
    import simdrive.session as session_mod
    import simdrive.diagnostics as diag_mod

    # Clean state isolation.
    session_mod._SESSIONS.clear()

    udid = "SIM-UDID-VERIFY-OK"
    bundle = "com.example.fastapp"

    _patch_sim_for_start(monkeypatch, udid=udid)
    _patch_workroot(monkeypatch, tmp_path)

    # app_state returns foreground on the first poll → loop breaks
    # immediately, no sleep budget consumed.
    poll_calls = {"n": 0}

    def _fake_app_state(udid_arg, bundle_arg):
        poll_calls["n"] += 1
        return {"state": "foreground", "bundle_id": bundle_arg, "pid": 12345}

    monkeypatch.setattr(diag_mod, "app_state", _fake_app_state)

    # Track sleep — assert we don't burn the full settle budget.
    sleep_total = {"s": 0.0}
    monkeypatch.setattr(
        "simdrive.session.time.sleep",
        lambda s: sleep_total.__setitem__("s", sleep_total["s"] + s),
    )

    result = server_mod.tool_session_start({
        "udid": udid,
        "app_bundle_id": bundle,
    })

    assert result["state"] == "active", (
        f"Expected state='active' when app reaches foreground, got: {result}"
    )
    assert "crash_report_path" not in result or result.get("crash_report_path") is None, (
        f"crash_report_path must not be present on success: {result}"
    )
    assert poll_calls["n"] >= 1, "Should have polled at least once"
    # First poll succeeded → no sleeps should fire.
    assert sleep_total["s"] == 0.0, (
        f"Should not sleep when first poll confirms foreground; "
        f"slept {sleep_total['s']}s"
    )


# ── test 2: crash path — never foreground → state=launched_then_exited ───────


def test_session_start_returns_launched_then_exited_when_app_crashes(
    tmp_path, monkeypatch,
):
    import simdrive.server as server_mod
    import simdrive.session as session_mod
    import simdrive.diagnostics as diag_mod

    session_mod._SESSIONS.clear()

    udid = "SIM-UDID-VERIFY-CRASH"
    bundle = "co.synctek.splashMate"

    _patch_sim_for_start(monkeypatch, udid=udid)
    _patch_workroot(monkeypatch, tmp_path)

    # app_state always says not-running → simulates immediate crash.
    poll_calls = {"n": 0}

    def _fake_app_state(udid_arg, bundle_arg):
        poll_calls["n"] += 1
        return {"state": "not-running", "bundle_id": bundle_arg, "pid": None}

    monkeypatch.setattr(diag_mod, "app_state", _fake_app_state)

    # Mock list_crashes to return a recent .ips report for this bundle.
    fake_crash_path = str(tmp_path / "splashMate-2026-05-22.ips")
    monkeypatch.setattr(
        diag_mod, "list_crashes",
        lambda since_ts=0.0, bundle_id=None, max_results=10, reports_dir=None: [
            {
                "path": fake_crash_path,
                "name": "splashMate-2026-05-22.ips",
                "timestamp": "2026-05-22 12:00:00",
                "exception": "EXC_CRASH (SIGABRT)",
                "bundle_id": bundle,
                "mtime": 0.0,
                "backtrace_first_lines": [],
            }
        ],
    )

    # Speed up the test — replace sleep with no-op.
    monkeypatch.setattr("simdrive.session.time.sleep", lambda s: None)

    result = server_mod.tool_session_start({
        "udid": udid,
        "app_bundle_id": bundle,
    })

    assert result["state"] == "launched_then_exited", (
        f"Expected state='launched_then_exited' when app crashes within "
        f"settle window, got: {result}"
    )
    assert result.get("crash_report_path") == fake_crash_path, (
        f"Expected crash_report_path={fake_crash_path!r}, got: {result}"
    )
    assert "recovery" in result and result["recovery"], (
        f"Expected 'recovery' hint to be present and non-empty: {result}"
    )
    # Should mention the crash / settle window in the recovery message.
    rec = result["recovery"].lower()
    assert "crash" in rec or "exit" in rec or "launch" in rec, (
        f"recovery message should describe the launch failure: {result['recovery']!r}"
    )
    # Polled at least the full attempt budget (5 attempts).
    assert poll_calls["n"] >= 2, (
        f"Should retry app_state polling on not-running; only got "
        f"{poll_calls['n']} call(s)"
    )


# ── test 3: opt-out — verify_launch=False skips polling ──────────────────────


def test_session_start_verify_launch_false_skips_polling(
    tmp_path, monkeypatch,
):
    import simdrive.server as server_mod
    import simdrive.session as session_mod
    import simdrive.diagnostics as diag_mod

    session_mod._SESSIONS.clear()

    udid = "SIM-UDID-VERIFY-OPTOUT"
    bundle = "com.example.optout"

    _patch_sim_for_start(monkeypatch, udid=udid)
    _patch_workroot(monkeypatch, tmp_path)

    # If app_state is ever called the test fails — verify_launch=False must
    # skip the poll entirely.
    poll_calls = {"n": 0}

    def _fake_app_state(udid_arg, bundle_arg):
        poll_calls["n"] += 1
        return {"state": "foreground", "bundle_id": bundle_arg, "pid": 1}

    monkeypatch.setattr(diag_mod, "app_state", _fake_app_state)

    result = server_mod.tool_session_start({
        "udid": udid,
        "app_bundle_id": bundle,
        "verify_launch": False,
    })

    assert result["state"] == "active", (
        f"verify_launch=False must return state='active' (legacy behaviour): {result}"
    )
    assert poll_calls["n"] == 0, (
        f"verify_launch=False must NOT call diagnostics.app_state; "
        f"got {poll_calls['n']} call(s)"
    )


# ── test 4: no bundle id at all → polling skipped ────────────────────────────


def test_session_start_verify_launch_no_bundle_skips_polling(
    tmp_path, monkeypatch,
):
    import simdrive.server as server_mod
    import simdrive.session as session_mod
    import simdrive.diagnostics as diag_mod

    session_mod._SESSIONS.clear()

    udid = "SIM-UDID-VERIFY-NOBUNDLE"

    _patch_sim_for_start(monkeypatch, udid=udid)
    _patch_workroot(monkeypatch, tmp_path)

    poll_calls = {"n": 0}

    def _fake_app_state(udid_arg, bundle_arg):
        poll_calls["n"] += 1
        return {"state": "foreground", "bundle_id": bundle_arg, "pid": 1}

    monkeypatch.setattr(diag_mod, "app_state", _fake_app_state)

    result = server_mod.tool_session_start({
        "udid": udid,
        # no app_bundle_id, no verify_launch override
    })

    assert result["state"] == "active", (
        f"No bundle to verify → state='active': {result}"
    )
    assert poll_calls["n"] == 0, (
        "No app_bundle_id → diagnostics.app_state must not be polled; "
        f"got {poll_calls['n']} call(s)"
    )


# ── test 5: response always carries crash_report_path + recovery keys ────────


def test_session_start_response_always_has_crash_keys(tmp_path, monkeypatch):
    """Schema-consistency contract: the keys 'crash_report_path' and
    'recovery' must always be present on the response (None on success)."""
    import simdrive.server as server_mod
    import simdrive.session as session_mod
    import simdrive.diagnostics as diag_mod

    session_mod._SESSIONS.clear()

    udid = "SIM-UDID-VERIFY-SCHEMA"
    bundle = "com.example.schema"

    _patch_sim_for_start(monkeypatch, udid=udid)
    _patch_workroot(monkeypatch, tmp_path)
    monkeypatch.setattr(
        diag_mod, "app_state",
        lambda *_a, **_kw: {"state": "foreground", "bundle_id": bundle, "pid": 1},
    )

    result = server_mod.tool_session_start({
        "udid": udid,
        "app_bundle_id": bundle,
    })

    assert "crash_report_path" in result, (
        f"crash_report_path key missing from success response: {result}"
    )
    assert "recovery" in result, (
        f"recovery key missing from success response: {result}"
    )
    assert result["crash_report_path"] is None
    assert result["recovery"] is None


# ── test 6: single not-running flake does NOT trip the crash verdict ─────────


def test_session_start_single_not_running_then_foreground_returns_active(
    tmp_path, monkeypatch,
):
    """Review feedback fix: ONE transient 'not-running' poll (launchctl
    flake) must NOT be enough to declare launched_then_exited. The crash
    streak resets as soon as foreground appears."""
    import simdrive.server as server_mod
    import simdrive.session as session_mod
    import simdrive.diagnostics as diag_mod

    session_mod._SESSIONS.clear()

    udid = "SIM-UDID-VERIFY-FLAKE"
    bundle = "com.example.flake"

    _patch_sim_for_start(monkeypatch, udid=udid)
    _patch_workroot(monkeypatch, tmp_path)
    monkeypatch.setattr("simdrive.session.time.sleep", lambda s: None)

    # Sequence: not-running, then foreground. One flake → still active.
    states = iter([
        {"state": "not-running", "bundle_id": bundle, "pid": None},
        {"state": "foreground", "bundle_id": bundle, "pid": 7},
    ])
    poll_calls = {"n": 0}

    def _fake_app_state(udid_arg, bundle_arg):
        poll_calls["n"] += 1
        return next(states)

    monkeypatch.setattr(diag_mod, "app_state", _fake_app_state)

    result = server_mod.tool_session_start({
        "udid": udid,
        "app_bundle_id": bundle,
    })

    assert result["state"] == "active", (
        f"Single transient not-running must not declare crash; got: {result}"
    )
    assert result["crash_report_path"] is None
    assert poll_calls["n"] == 2, (
        f"Expected exactly 2 polls (not-running, foreground); got {poll_calls['n']}"
    )


# ── test 7: TWO consecutive not-running polls DO trip the crash verdict ──────


def test_session_start_two_consecutive_not_running_declares_crash(
    tmp_path, monkeypatch,
):
    """Review feedback fix: two consecutive not-running polls = crash."""
    import simdrive.server as server_mod
    import simdrive.session as session_mod
    import simdrive.diagnostics as diag_mod

    session_mod._SESSIONS.clear()

    udid = "SIM-UDID-VERIFY-CRASH2"
    bundle = "com.example.crash2"

    _patch_sim_for_start(monkeypatch, udid=udid)
    _patch_workroot(monkeypatch, tmp_path)
    monkeypatch.setattr("simdrive.session.time.sleep", lambda s: None)

    poll_calls = {"n": 0}

    def _fake_app_state(udid_arg, bundle_arg):
        poll_calls["n"] += 1
        return {"state": "not-running", "bundle_id": bundle_arg, "pid": None}

    monkeypatch.setattr(diag_mod, "app_state", _fake_app_state)
    monkeypatch.setattr(
        diag_mod, "list_crashes",
        lambda since_ts=0.0, bundle_id=None, max_results=10, reports_dir=None: [],
    )

    result = server_mod.tool_session_start({
        "udid": udid,
        "app_bundle_id": bundle,
    })

    assert result["state"] == "launched_then_exited", (
        f"Two not-running polls in a row must declare crash; got: {result}"
    )
    # Should break on the 2nd not-running, not poll all 10 attempts.
    assert poll_calls["n"] == 2, (
        f"Should break on 2nd consecutive not-running; got {poll_calls['n']} polls"
    )


# ── test 8: app_state RAISES mid-poll → caught, retried, no propagation ──────


def test_session_start_app_state_raises_mid_poll_caught_and_retried(
    tmp_path, monkeypatch,
):
    """Review feedback fix: exceptions from app_state must be caught (not
    propagated) and the loop must keep polling. A transient launchctl
    failure followed by foreground means state='active'."""
    import simdrive.server as server_mod
    import simdrive.session as session_mod
    import simdrive.diagnostics as diag_mod

    session_mod._SESSIONS.clear()

    udid = "SIM-UDID-VERIFY-RAISE"
    bundle = "com.example.raise"

    _patch_sim_for_start(monkeypatch, udid=udid)
    _patch_workroot(monkeypatch, tmp_path)
    monkeypatch.setattr("simdrive.session.time.sleep", lambda s: None)

    poll_calls = {"n": 0}

    def _fake_app_state(udid_arg, bundle_arg):
        poll_calls["n"] += 1
        if poll_calls["n"] == 1:
            raise RuntimeError("simulated launchctl ENOENT flake")
        return {"state": "foreground", "bundle_id": bundle_arg, "pid": 42}

    monkeypatch.setattr(diag_mod, "app_state", _fake_app_state)

    result = server_mod.tool_session_start({
        "udid": udid,
        "app_bundle_id": bundle,
    })

    # Must NOT have propagated the RuntimeError; loop continues and
    # observes foreground on the 2nd poll.
    assert result["state"] == "active", (
        f"app_state RuntimeError must be caught + retried, then resolve to "
        f"active on next foreground; got: {result}"
    )
    assert poll_calls["n"] == 2, (
        f"Expected 2 polls (raise, then foreground); got {poll_calls['n']}"
    )


# ── test 9: slow-but-healthy — foreground on attempt 3 of 10 → active ────────


def test_session_start_slow_but_healthy_app_returns_active(
    tmp_path, monkeypatch,
):
    """Review feedback fix: a legit cold-start that takes ~900 ms to show
    up in launchctl list must NOT be declared crashed. With the 3000 ms /
    10-attempt budget, foreground on attempt 3 should resolve cleanly."""
    import simdrive.server as server_mod
    import simdrive.session as session_mod
    import simdrive.diagnostics as diag_mod

    session_mod._SESSIONS.clear()

    udid = "SIM-UDID-VERIFY-SLOW"
    bundle = "com.example.slow"

    _patch_sim_for_start(monkeypatch, udid=udid)
    _patch_workroot(monkeypatch, tmp_path)
    monkeypatch.setattr("simdrive.session.time.sleep", lambda s: None)

    # Polls 1+2: not-running (launchctl hasn't seen it yet).
    # Poll 3: foreground. With 2-consecutive rule + reset on foreground, we
    # would have declared crash on the OLD code (any single not-running was
    # enough). New code keeps the streak at 2 but the next poll resets it.
    # Wait — actually with the 2-consecutive rule, two not-running in a row
    # = crash declared on poll 2. We need to be more careful: simulate ONE
    # not-running, then a non-foreground-but-not-not-running gap, then
    # foreground. We'll use the "unknown" sentinel emitted by exception
    # handling for the gap.
    #
    # Realistic cold-start scenario: launchctl flakes once, then takes a
    # beat to surface, then shows foreground.
    states = iter([
        {"state": "not-running", "bundle_id": bundle, "pid": None},
        # Exception simulates a launchctl glitch — handler turns this into
        # "unknown" which resets the not-running streak.
        RuntimeError("launchctl timeout"),
        {"state": "foreground", "bundle_id": bundle, "pid": 99},
    ])
    poll_calls = {"n": 0}

    def _fake_app_state(udid_arg, bundle_arg):
        poll_calls["n"] += 1
        item = next(states)
        if isinstance(item, Exception):
            raise item
        return item

    monkeypatch.setattr(diag_mod, "app_state", _fake_app_state)

    result = server_mod.tool_session_start({
        "udid": udid,
        "app_bundle_id": bundle,
    })

    assert result["state"] == "active", (
        f"Slow but healthy app (foreground on attempt 3) must be active; "
        f"got: {result}"
    )
    assert poll_calls["n"] == 3, (
        f"Expected 3 polls before foreground; got {poll_calls['n']}"
    )


# ── test 10: env-configurable budget via SIMDRIVE_VERIFY_LAUNCH_BUDGET_MS ─────


def test_session_start_verify_budget_env_var_extends_polling(
    tmp_path, monkeypatch,
):
    """Setting SIMDRIVE_VERIFY_LAUNCH_BUDGET_MS=6000 must yield 20 attempts
    instead of the default 10 (300 ms each)."""
    import simdrive.server as server_mod
    import simdrive.session as session_mod
    import simdrive.diagnostics as diag_mod

    session_mod._SESSIONS.clear()

    udid = "SIM-UDID-VERIFY-ENV-BUDGET"
    bundle = "com.example.envbudget"

    _patch_sim_for_start(monkeypatch, udid=udid)
    _patch_workroot(monkeypatch, tmp_path)
    monkeypatch.setattr("simdrive.session.time.sleep", lambda s: None)
    monkeypatch.setenv("SIMDRIVE_VERIFY_LAUNCH_BUDGET_MS", "6000")

    # Poll order: 19 "unknown" (no crash streak, no foreground) followed by
    # 1 foreground at attempt 20. If env override is ignored we'd cap at 10.
    sequence = (
        [RuntimeError("flake")] * 19
        + [{"state": "foreground", "bundle_id": bundle, "pid": 1}]
    )
    states = iter(sequence)
    poll_calls = {"n": 0}

    def _fake_app_state(udid_arg, bundle_arg):
        poll_calls["n"] += 1
        item = next(states)
        if isinstance(item, Exception):
            raise item
        return item

    monkeypatch.setattr(diag_mod, "app_state", _fake_app_state)

    result = server_mod.tool_session_start({
        "udid": udid,
        "app_bundle_id": bundle,
    })

    assert result["state"] == "active"
    assert poll_calls["n"] == 20, (
        f"6000 ms env budget → 20 attempts; got {poll_calls['n']}"
    )


def test_verify_launch_budget_env_var_is_clamped(monkeypatch):
    """Out-of-range env values are clamped to [500, 15000] ms."""
    from simdrive.session import _verify_launch_attempts

    # Too low — clamp to 500 ms → ceil(500/300) = 2 attempts.
    monkeypatch.setenv("SIMDRIVE_VERIFY_LAUNCH_BUDGET_MS", "10")
    assert _verify_launch_attempts() == 2

    # Too high — clamp to 15000 ms → 50 attempts.
    monkeypatch.setenv("SIMDRIVE_VERIFY_LAUNCH_BUDGET_MS", "999999")
    assert _verify_launch_attempts() == 50

    # Garbage → default 3000 ms → 10 attempts.
    monkeypatch.setenv("SIMDRIVE_VERIFY_LAUNCH_BUDGET_MS", "not-a-number")
    assert _verify_launch_attempts() == 10

    # Unset → default 10.
    monkeypatch.delenv("SIMDRIVE_VERIFY_LAUNCH_BUDGET_MS", raising=False)
    assert _verify_launch_attempts() == 10


# ── test 11: crash-report flush race — retry on initial empty list ───────────


def test_session_start_crash_report_flush_race_retried(tmp_path, monkeypatch):
    """ReportCrash writes .ips asynchronously. If list_crashes returns []
    on the first try but a path on the second (post 250 ms sleep), the
    response must carry the path."""
    import simdrive.server as server_mod
    import simdrive.session as session_mod
    import simdrive.diagnostics as diag_mod

    session_mod._SESSIONS.clear()

    udid = "SIM-UDID-VERIFY-FLUSH"
    bundle = "com.example.flushrace"

    _patch_sim_for_start(monkeypatch, udid=udid)
    _patch_workroot(monkeypatch, tmp_path)
    monkeypatch.setattr("simdrive.session.time.sleep", lambda s: None)

    monkeypatch.setattr(
        diag_mod, "app_state",
        lambda *_a, **_kw: {"state": "not-running", "bundle_id": bundle, "pid": None},
    )

    fake_crash_path = str(tmp_path / "flushrace.ips")
    call_count = {"n": 0}

    def _fake_list_crashes(
        since_ts=0.0, bundle_id=None, max_results=10, reports_dir=None,
    ):
        call_count["n"] += 1
        if call_count["n"] == 1:
            return []
        return [
            {
                "path": fake_crash_path,
                "name": "flushrace.ips",
                "timestamp": "2026-05-22 12:00:00",
                "exception": "EXC_BAD_ACCESS",
                "bundle_id": bundle_id,
                "mtime": 0.0,
                "backtrace_first_lines": [],
            }
        ]

    monkeypatch.setattr(diag_mod, "list_crashes", _fake_list_crashes)

    result = server_mod.tool_session_start({
        "udid": udid,
        "app_bundle_id": bundle,
    })

    assert result["state"] == "launched_then_exited"
    assert result["crash_report_path"] == fake_crash_path, (
        f"Expected crash_report_path={fake_crash_path!r} after flush retry; "
        f"got: {result}"
    )
    assert call_count["n"] == 2, (
        f"Expected list_crashes to be called twice (initial + retry); "
        f"got {call_count['n']}"
    )


# ── test 12: device path threads verify_launch through (foreground = active) ─


def test_session_start_device_path_verifies_launch_when_running(
    tmp_path, monkeypatch,
):
    """target='device' must thread verify_launch through to _start_device.
    When app_state_device returns 'running', state='active'."""
    import simdrive.server as server_mod
    import simdrive.session as session_mod
    import simdrive.diagnostics as diag_mod

    session_mod._SESSIONS.clear()

    udid = "DEV-UDID-VERIFY-OK"
    hardware_udid = "DEV-HW-UDID-VERIFY-OK"
    bundle = "com.example.deviceapp"

    _patch_workroot(monkeypatch, tmp_path)

    # Patch WDA registry + client + launch.
    from simdrive.wda import registry as wda_registry_mod
    monkeypatch.setattr(
        wda_registry_mod, "load",
        lambda u: {
            "host": "127.0.0.1", "port": 8100,
            "hardware_udid": hardware_udid,
            "device_name": "Fake iPhone",
            "os_version": "26.0",
        },
    )

    # Capture the WdaClient calls without hitting real WDA.
    import simdrive.wda.client as wda_client_mod

    class _FakeWda:
        def __init__(self, host, port):
            self.host = host
            self.port = port
            self.opened_session = None
        def status(self):
            return {"value": {"ready": True}}
        def open_session(self, bundle_id):
            self.opened_session = bundle_id

    monkeypatch.setattr(wda_client_mod, "WdaClient", _FakeWda)

    # Patch device.launch_app — record the call but no real devicectl.
    import simdrive.device as device_mod
    launch_calls = {"n": 0}
    def _fake_launch(udid_arg, bundle_arg):
        launch_calls["n"] += 1
        return 1234
    monkeypatch.setattr(device_mod, "launch_app", _fake_launch)

    # Verify the device app-state primitive is used (not the sim one).
    sim_calls = {"n": 0}
    device_calls = {"n": 0}

    def _fake_app_state_sim(*_a, **_kw):
        sim_calls["n"] += 1
        return {"state": "foreground", "bundle_id": bundle, "pid": 1}

    def _fake_app_state_device(udid_arg, bundle_arg):
        device_calls["n"] += 1
        assert udid_arg == hardware_udid, (
            f"Device path must use hardware_udid, got {udid_arg!r}"
        )
        return {"state": "running", "bundle_id": bundle_arg, "pid": 99}

    monkeypatch.setattr(diag_mod, "app_state", _fake_app_state_sim)
    monkeypatch.setattr(diag_mod, "app_state_device", _fake_app_state_device)

    result = server_mod.tool_session_start({
        "udid": udid,
        "target": "device",
        "app_bundle_id": bundle,
    })

    assert result["state"] == "active", (
        f"Device path with running app must return active; got: {result}"
    )
    assert result["target"] == "device"
    assert result["crash_report_path"] is None
    assert result["recovery"] is None
    assert launch_calls["n"] == 1
    assert device_calls["n"] >= 1, (
        f"app_state_device must be called on device path; got {device_calls['n']}"
    )
    assert sim_calls["n"] == 0, (
        f"app_state (sim path) must NOT be called on device path; got {sim_calls['n']}"
    )


# ── test 13: device path — graceful fallback when devicectl can't see procs ──


def test_session_start_device_path_graceful_fallback_when_unavailable(
    tmp_path, monkeypatch,
):
    """If app_state_device raises on every poll (no DDI / no process list),
    return state='active' with a verification-unavailable warning rather
    than mis-declaring a crash."""
    import simdrive.server as server_mod
    import simdrive.session as session_mod
    import simdrive.diagnostics as diag_mod

    session_mod._SESSIONS.clear()

    udid = "DEV-UDID-VERIFY-NODDI"
    hardware_udid = "DEV-HW-UDID-VERIFY-NODDI"
    bundle = "com.example.noddi"

    _patch_workroot(monkeypatch, tmp_path)
    monkeypatch.setattr("simdrive.session.time.sleep", lambda s: None)

    from simdrive.wda import registry as wda_registry_mod
    monkeypatch.setattr(
        wda_registry_mod, "load",
        lambda u: {
            "host": "127.0.0.1", "port": 8100,
            "hardware_udid": hardware_udid,
            "device_name": "Fake iPhone",
            "os_version": "26.0",
        },
    )

    import simdrive.wda.client as wda_client_mod

    class _FakeWda:
        def __init__(self, host, port):
            pass
        def status(self):
            return {"value": {"ready": True}}
        def open_session(self, bundle_id):
            pass

    monkeypatch.setattr(wda_client_mod, "WdaClient", _FakeWda)

    import simdrive.device as device_mod
    monkeypatch.setattr(device_mod, "launch_app", lambda *_a, **_kw: 1)

    # Every poll raises — devicectl process list is unavailable.
    def _always_raise(*_a, **_kw):
        raise RuntimeError("devicectl process list unavailable (DDI not mounted)")
    monkeypatch.setattr(diag_mod, "app_state_device", _always_raise)

    result = server_mod.tool_session_start({
        "udid": udid,
        "target": "device",
        "app_bundle_id": bundle,
    })

    assert result["state"] == "active", (
        f"Device verification unavailable must fall back to active, NOT "
        f"launched_then_exited; got: {result}"
    )
    assert result["recovery"] is not None and "unavailable" in result["recovery"].lower(), (
        f"Expected verification-unavailable warning in recovery; got: {result}"
    )


# ── test 14: device path — two consecutive not-running → launched_then_exited ─


def test_session_start_device_path_declares_crash_on_two_not_running(
    tmp_path, monkeypatch,
):
    """Device path also uses the 2-consecutive-not-running rule."""
    import simdrive.server as server_mod
    import simdrive.session as session_mod
    import simdrive.diagnostics as diag_mod

    session_mod._SESSIONS.clear()

    udid = "DEV-UDID-VERIFY-CRASH"
    hardware_udid = "DEV-HW-UDID-VERIFY-CRASH"
    bundle = "com.example.devcrash"

    _patch_workroot(monkeypatch, tmp_path)
    monkeypatch.setattr("simdrive.session.time.sleep", lambda s: None)

    from simdrive.wda import registry as wda_registry_mod
    monkeypatch.setattr(
        wda_registry_mod, "load",
        lambda u: {
            "host": "127.0.0.1", "port": 8100,
            "hardware_udid": hardware_udid,
            "device_name": "Fake iPhone",
            "os_version": "26.0",
        },
    )

    import simdrive.wda.client as wda_client_mod

    class _FakeWda:
        def __init__(self, host, port):
            pass
        def status(self):
            return {"value": {"ready": True}}
        def open_session(self, bundle_id):
            pass

    monkeypatch.setattr(wda_client_mod, "WdaClient", _FakeWda)

    import simdrive.device as device_mod
    monkeypatch.setattr(device_mod, "launch_app", lambda *_a, **_kw: 1)

    monkeypatch.setattr(
        diag_mod, "app_state_device",
        lambda *_a, **_kw: {"state": "not-running", "bundle_id": bundle, "pid": None},
    )

    result = server_mod.tool_session_start({
        "udid": udid,
        "target": "device",
        "app_bundle_id": bundle,
    })

    assert result["state"] == "launched_then_exited", (
        f"Device path with two not-running polls must declare crash; got: {result}"
    )
    # crash_report_path is None on device (no on-device .ips access).
    assert result["crash_report_path"] is None
    assert result["recovery"] is not None and "device" in result["recovery"].lower()
