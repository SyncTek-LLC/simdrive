"""F#2: session_start must verify the launched app reached foreground before
returning ``state: "active"``.

Bug reproducer (Palace dogfood 2026-05-22): SplashMate built without proper
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
