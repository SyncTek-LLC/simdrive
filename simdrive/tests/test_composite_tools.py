"""Tests for the PR C composite/atomic tool additions (1.0.0b3 polish sprint).

Covers four small additions to the MCP surface:
* ``tool_tap`` echoes ``tapped_mark`` when a mark target resolved
* ``tool_tap`` honors ``settle_ms`` post-action sleep
* ``tool_swipe`` honors ``settle_ms``
* ``tool_dismiss_sheet`` accepts ``direction`` ('down' default, 'up')
* New ``tool_tap_and_wait_keyboard`` composite tool

All tests mock at the act/observe boundary — no live simulator.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


_FAKE_UDID = "31471BBD-0000-COMP-OS17-COMPOSITETOOLS"


def _make_sim_session(tmp_path: Path, sid: str = "comp-sim") -> object:
    from simdrive import session as session_mod
    from simdrive.sim import Device

    d = Device(udid=_FAKE_UDID, name="Test Sim", os_version="26.0", state="active")
    workdir = tmp_path / "sessions" / sid
    workdir.mkdir(parents=True, exist_ok=True)
    s = session_mod.Session(
        session_id=sid,
        device=d,
        workdir=workdir,
        target="simulator",
        last_screenshot_w=440,
        last_screenshot_h=956,
    )
    session_mod._SESSIONS[sid] = s
    return s


@pytest.fixture(autouse=True)
def _cleanup_sessions():
    from simdrive import session as session_mod
    before = set(session_mod._SESSIONS.keys())
    yield
    for k in list(session_mod._SESSIONS.keys()):
        if k not in before:
            session_mod._SESSIONS.pop(k, None)


# ── tapped_mark echo ────────────────────────────────────────────────────────


def test_tool_tap_echoes_tapped_mark_when_mark_resolved(tmp_path):
    """When tap resolves a target via a mark (not raw x/y), the response must
    include `tapped_mark` so the agent can correlate input with audit logs."""
    s = _make_sim_session(tmp_path)
    s.last_marks = [
        {"id": 1, "stable_id": "s-login", "stable_id_loose": "sl-login",
         "text": "Log in", "center": (100, 200), "bbox": (50, 180, 100, 40),
         "confidence_band": "high"},
    ]

    from simdrive import server
    with patch.object(server.act, "tap", return_value=(0, 0)):
        result = server.tool_tap({
            "session_id": s.session_id,
            "stable_id": "s-login",
        })

    assert "tapped_mark" in result
    assert result["tapped_mark"]["stable_id"] == "s-login"
    assert result["tapped_mark"]["text"] == "Log in"


def test_tool_tap_no_tapped_mark_for_raw_xy(tmp_path):
    """A raw {x, y} tap (no mark target) must NOT include `tapped_mark`."""
    s = _make_sim_session(tmp_path)
    from simdrive import server
    with patch.object(server.act, "tap", return_value=(0, 0)):
        result = server.tool_tap({"session_id": s.session_id, "x": 100, "y": 200})
    assert "tapped_mark" not in result


# ── settle_ms ───────────────────────────────────────────────────────────────


def test_tool_tap_settle_ms_sleeps_post_action(tmp_path):
    """settle_ms > 0 must trigger a sleep of (settle_ms / 1000) seconds."""
    s = _make_sim_session(tmp_path)
    from simdrive import server
    with patch.object(server.act, "tap", return_value=(0, 0)), \
         patch.object(server.time, "sleep") as mock_sleep:
        server.tool_tap({
            "session_id": s.session_id, "x": 100, "y": 200, "settle_ms": 250,
        })
    # 250ms = 0.25s. Allow other sleep calls (none expected on this path) but
    # one of them must be the settle.
    settle_calls = [c for c in mock_sleep.call_args_list if c.args == (0.25,)]
    assert len(settle_calls) == 1, f"expected 1 sleep(0.25), got {mock_sleep.call_args_list}"


def test_tool_tap_settle_ms_zero_does_not_sleep(tmp_path):
    s = _make_sim_session(tmp_path)
    from simdrive import server
    with patch.object(server.act, "tap", return_value=(0, 0)), \
         patch.object(server.time, "sleep") as mock_sleep:
        server.tool_tap({"session_id": s.session_id, "x": 100, "y": 200})
    # Default settle_ms=0 -> no sleep call from settle path.
    assert not any(c.args == (0.0,) for c in mock_sleep.call_args_list)


def test_tool_swipe_settle_ms_sleeps(tmp_path):
    s = _make_sim_session(tmp_path)
    from simdrive import server
    with patch.object(server.act, "swipe"), \
         patch.object(server.time, "sleep") as mock_sleep:
        server.tool_swipe({
            "session_id": s.session_id,
            "x1": 100, "y1": 200, "x2": 100, "y2": 400,
            "settle_ms": 400,
        })
    settle_calls = [c for c in mock_sleep.call_args_list if c.args == (0.4,)]
    assert len(settle_calls) == 1


# ── dismiss_sheet direction ────────────────────────────────────────────────


def test_tool_dismiss_sheet_default_swipes_down(tmp_path):
    """Default direction='down' must swipe from 20% -> 70% (top -> bottom)."""
    s = _make_sim_session(tmp_path)
    from simdrive import server
    with patch.object(server.act, "swipe") as mock_swipe:
        result = server.tool_dismiss_sheet({"session_id": s.session_id})
    assert result["direction"] == "down"
    mock_swipe.assert_called_once()
    # swipe(x1, y1, x2, y2, sw, sh, duration_ms, udid=...)
    args = mock_swipe.call_args[0]
    assert args[1] < args[3], f"expected y1 < y2 for 'down', got y1={args[1]} y2={args[3]}"


def test_tool_dismiss_sheet_up_swipes_up(tmp_path):
    """direction='up' must invert: swipe from 70% -> 20% (bottom -> top)."""
    s = _make_sim_session(tmp_path)
    from simdrive import server
    with patch.object(server.act, "swipe") as mock_swipe:
        result = server.tool_dismiss_sheet({
            "session_id": s.session_id, "direction": "up",
        })
    assert result["direction"] == "up"
    args = mock_swipe.call_args[0]
    assert args[1] > args[3], f"expected y1 > y2 for 'up', got y1={args[1]} y2={args[3]}"


def test_tool_dismiss_sheet_invalid_direction_raises(tmp_path):
    s = _make_sim_session(tmp_path)
    from simdrive import server, errors
    with pytest.raises(errors.SimdriveError) as exc_info:
        server.tool_dismiss_sheet({
            "session_id": s.session_id, "direction": "sideways",
        })
    assert exc_info.value.code == "invalid_argument"


# ── tap_and_wait_keyboard composite ────────────────────────────────────────


def test_tool_tap_and_wait_keyboard_atomic_composite(tmp_path):
    """The composite must: call tap, sleep _KEYBOARD_SETTLE_SEC, then observe.
    Result merges tap fields with a `post_state` containing the observation."""
    s = _make_sim_session(tmp_path)
    s.last_marks = [
        {"id": 1, "stable_id": "s-email", "stable_id_loose": "sl-email",
         "text": "Email", "center": (100, 200), "bbox": (50, 180, 100, 40),
         "confidence_band": "high"},
    ]

    fake_observe_result = {
        "marks": [{"id": 99, "text": "delete", "confidence_band": "high"}],
        "screenshot_path": "/tmp/post.png",
        "target": "simulator",
    }

    from simdrive import server
    with patch.object(server.act, "tap", return_value=(0, 0)), \
         patch.object(server, "tool_observe", return_value=fake_observe_result) as mock_obs, \
         patch.object(server.time, "sleep") as mock_sleep:
        result = server.tool_tap_and_wait_keyboard({
            "session_id": s.session_id,
            "stable_id": "s-email",
        })

    # 1. Tap happened (via tapped_mark presence)
    assert result["tapped_mark"]["stable_id"] == "s-email"
    # 2. Settle sleep happened with the documented constant
    sleep_calls = [c.args[0] for c in mock_sleep.call_args_list]
    assert server._KEYBOARD_SETTLE_SEC in sleep_calls
    # 3. Observe followed
    mock_obs.assert_called_once()
    obs_args = mock_obs.call_args[0][0]
    assert obs_args["session_id"] == s.session_id
    assert obs_args["annotate"] is True
    # 4. post_state echoes the observe payload
    assert result["post_state"] == fake_observe_result


def test_tool_tap_and_wait_keyboard_annotate_pass_through(tmp_path):
    """annotate=False should propagate to the post-tap observe call."""
    s = _make_sim_session(tmp_path)
    from simdrive import server
    with patch.object(server.act, "tap", return_value=(0, 0)), \
         patch.object(server, "tool_observe", return_value={}) as mock_obs, \
         patch.object(server.time, "sleep"):
        server.tool_tap_and_wait_keyboard({
            "session_id": s.session_id, "x": 100, "y": 200, "annotate": False,
        })
    obs_args = mock_obs.call_args[0][0]
    assert obs_args["annotate"] is False
