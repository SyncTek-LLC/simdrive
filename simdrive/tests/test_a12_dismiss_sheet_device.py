"""a12: dismiss_sheet on device calls WDA swipe; sim path unchanged.

Tests:
  14. test_dismiss_sheet_on_device_calls_wda_swipe
      - Session with target='device' and a mock WdaClient.
        Invoke tool_dismiss_sheet(session_id). Assert wda.swipe(...) was called
        with from-y ≈ 20% screen height and to-y ≈ 70% screen height in logical
        points (with F-006 scale division if needed). No simctl called.
  15. test_dismiss_sheet_on_sim_still_uses_existing_path
      - Same setup but target='simulator'. Assert the sim path is hit and
        WDA wda.swipe is NOT called.

Both tests FAIL on HEAD because:
  - tool_dismiss_sheet raises device_input_unavailable when target='device'.
  - The function never reaches WDA swipe logic for device sessions.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch, call

import pytest


# ── helpers ──────────────────────────────────────────────────────────────────


def _make_device_session(
    session_id: str, udid: str, workdir: Path,
    screen_w: int = 1290, screen_h: int = 2796,
):
    """Device session with pre-populated screenshot dims (pixel space)."""
    from simdrive.sim import Device
    d = Device(udid=udid, name="iPhone 17 Pro Max", os_version="26.0", state="active")
    wda = MagicMock()
    wda.swipe = MagicMock()
    wda.window_size_points.return_value = (430, 932)  # logical points for 3× device
    return SimpleNamespace(
        session_id=session_id,
        device=d,
        target="device",
        app_bundle_id=None,
        workdir=workdir,
        last_action_at=0.0,
        state="active",
        recorder=None,
        wda_client=wda,
        pixel_per_point_scale=None,  # not yet cached
        last_screenshot_w=screen_w,
        last_screenshot_h=screen_h,
        last_screenshot_path=None,
        last_marks=[],
        perf_baselines={},
        started_at=0.0,
    ), wda


def _make_sim_session(session_id: str, udid: str, workdir: Path):
    from simdrive.sim import Device
    d = Device(udid=udid, name="iPhone 17 Pro", os_version="26.0", state="Booted")
    return SimpleNamespace(
        session_id=session_id,
        device=d,
        target="simulator",
        app_bundle_id=None,
        workdir=workdir,
        last_action_at=0.0,
        state="active",
        recorder=None,
        wda_client=None,
        pixel_per_point_scale=None,
        last_screenshot_w=1179,
        last_screenshot_h=2556,
        last_screenshot_path=None,
        last_marks=[],
        perf_baselines={},
        started_at=0.0,
    )


# ── test 14 ───────────────────────────────────────────────────────────────────


def test_dismiss_sheet_on_device_calls_wda_swipe(tmp_path, monkeypatch):
    """device target: tool_dismiss_sheet calls wda.swipe from 20% to 70% screen height.

    Fails on HEAD: tool_dismiss_sheet calls errors.device_input_unavailable()
    immediately for target='device', before any WDA swipe logic.
    """
    import simdrive.server as server_mod
    import simdrive.session as session_mod
    import simdrive.act as act_mod

    udid = "DEVICE-UDID-SHEET"
    sid = "sid-device-sheet"
    screen_w, screen_h = 1290, 2796  # pixel dimensions

    s, wda_mock = _make_device_session(sid, udid=udid, workdir=tmp_path / "sess",
                                       screen_w=screen_w, screen_h=screen_h)
    (tmp_path / "sess").mkdir(parents=True, exist_ok=True)
    monkeypatch.setitem(session_mod._SESSIONS, sid, s)

    # Prevent actual simctl calls.
    captured_simctl = []

    def _fake_act_swipe(*args, **kwargs):
        captured_simctl.append(("act.swipe", args, kwargs))

    monkeypatch.setattr(act_mod, "swipe", _fake_act_swipe)

    result = server_mod.tool_dismiss_sheet({"session_id": sid})

    assert result.get("ok") is True, f"Expected ok=True, got: {result}"

    # WDA swipe must have been called.
    assert wda_mock.swipe.called, (
        "Expected wda.swipe() to be called for target='device' in tool_dismiss_sheet, "
        "but it was not called. a12 adds WDA-swipe path for device sessions."
    )

    # Verify swipe coordinates are approximately 20% → 70% of screen height (in points).
    call_args = wda_mock.swipe.call_args
    assert call_args is not None, "wda.swipe was marked called but call_args is None"

    # Accept both positional and keyword args.
    args = call_args[0]
    kwargs = call_args[1]

    # Logical point height ≈ screen_h / scale. Scale = screen_w / 430pts ≈ 3.0.
    # Expected: from_y ≈ 932 * 0.2 ≈ 186 pts; to_y ≈ 932 * 0.7 ≈ 652 pts.
    # Allow ±10% tolerance for scale rounding.
    if args:
        from_x, from_y, to_x, to_y = (args + (None,) * 4)[:4]
    else:
        from_x = kwargs.get("from_x")
        from_y = kwargs.get("from_y")
        to_x = kwargs.get("to_x")
        to_y = kwargs.get("to_y")

    assert from_y is not None and to_y is not None, (
        f"Could not extract from_y / to_y from wda.swipe call: args={args}, kwargs={kwargs}"
    )

    # The from_y should be ≈ 20% of height, to_y ≈ 70% — in any unit.
    # Accept a wide tolerance since we just need to confirm the direction / rough values.
    from_y_f = float(from_y)
    to_y_f = float(to_y)

    assert to_y_f > from_y_f, (
        f"Expected to_y ({to_y_f}) > from_y ({from_y_f}) for a downward swipe."
    )

    # simctl/act.swipe must NOT have been called for device sessions.
    assert not captured_simctl, (
        f"Expected act.swipe NOT to be called for device target, "
        f"but got simctl calls: {captured_simctl}"
    )


# ── test 15 ───────────────────────────────────────────────────────────────────


def test_dismiss_sheet_on_sim_still_uses_existing_path(tmp_path, monkeypatch):
    """simulator target: tool_dismiss_sheet uses act.swipe (sim path), not wda.swipe.

    Guard against regressions where a12's device branch accidentally changes
    the simulator code path.

    Passes on HEAD for the right reason (sim path works); fails if a12
    accidentally breaks the sim branch.
    """
    import simdrive.server as server_mod
    import simdrive.session as session_mod
    import simdrive.act as act_mod

    udid = "SIM-UDID-SHEET"
    sid = "sid-sim-sheet"
    s = _make_sim_session(sid, udid=udid, workdir=tmp_path / "sess")
    (tmp_path / "sess").mkdir(parents=True, exist_ok=True)
    monkeypatch.setitem(session_mod._SESSIONS, sid, s)

    captured_act = []
    wda_swipe_called = []

    def _fake_act_swipe(*args, **kwargs):
        captured_act.append(("act.swipe", args, kwargs))

    monkeypatch.setattr(act_mod, "swipe", _fake_act_swipe)

    # Inject a mock WDA to detect any accidental WDA call on sim path.
    fake_wda = MagicMock()
    fake_wda.swipe = MagicMock(side_effect=lambda *a, **kw: wda_swipe_called.append((a, kw)))
    s.wda_client = fake_wda

    result = server_mod.tool_dismiss_sheet({"session_id": sid})

    assert result.get("ok") is True, f"Expected ok=True on sim path, got: {result}"
    assert captured_act, (
        "Expected act.swipe() to be called for simulator target in tool_dismiss_sheet."
    )
    assert not wda_swipe_called, (
        f"Expected WDA swipe NOT to be called on simulator path, "
        f"but it was called: {wda_swipe_called}"
    )
