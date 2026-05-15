"""a12: session_start(replace_existing=True) ends old session and starts fresh one.

Tests:
  12. test_session_start_replace_existing_ends_old_and_starts_new
      - Have session A active for udid X. Invoke tool_session_start with
        replace_existing=True for the same udid. Assert session A was ended
        (session.end called), a new session B was created with a different
        session_id, and B is the one returned.
  13. test_session_start_without_replace_errors_when_active
      - Same setup but replace_existing=False. Assert raises a clear
        "session already active" SimdriveError (or returns an error dict).

Both tests FAIL on HEAD because:
  - tool_session_start / session.start do not accept replace_existing.
  - There is no conflict detection for same-udid sessions.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch, call

import pytest


# ── helpers ──────────────────────────────────────────────────────────────────


def _make_sim_session(session_id: str, udid: str, workdir: Path):
    from simdrive.sim import Device
    d = Device(udid=udid, name="iPhone 17 Pro", os_version="26.0", state="booted")
    return SimpleNamespace(
        session_id=session_id,
        device=d,
        target="simulator",
        app_bundle_id=None,
        workdir=workdir,
        last_action_at=0.0,
        state="active",
        recorder=None,
        perf_baselines={},
        started_at=0.0,
        wda_client=None,
        pixel_per_point_scale=None,
        last_screenshot_w=0,
        last_screenshot_h=0,
        last_screenshot_path=None,
        last_marks=[],
    )


# ── test 12 ───────────────────────────────────────────────────────────────────


def test_session_start_replace_existing_ends_old_and_starts_new(tmp_path, monkeypatch):
    """replace_existing=True ends session A and starts session B for the same udid.

    Fails on HEAD: tool_session_start does not accept replace_existing parameter.
    session.start does not check for existing sessions for the same udid.
    """
    import simdrive.server as server_mod
    import simdrive.session as session_mod
    import simdrive.sim as sim_mod
    from simdrive.errors import SimdriveError

    udid = "SIM-UDID-REPLACE"
    session_a_id = "session-A-existing"

    # Plant session A as an existing active session for this udid.
    session_a = _make_sim_session(session_a_id, udid=udid, workdir=tmp_path / "a")
    (tmp_path / "a").mkdir(parents=True, exist_ok=True)
    monkeypatch.setitem(session_mod._SESSIONS, session_a_id, session_a)

    # Track whether session.end was called for session A.
    ended_sessions = []
    original_end = session_mod.end

    def _fake_end(sid, terminate_app=True):
        ended_sessions.append(sid)
        # Actually remove from _SESSIONS to mimic real behaviour.
        session_mod._SESSIONS.pop(sid, None)

    monkeypatch.setattr(session_mod, "end", _fake_end)

    # Mock sim.find_device to return a valid device for the udid.
    from simdrive.sim import Device
    fake_device = Device(udid=udid, name="iPhone 17 Pro", os_version="26.0", state="Booted")

    monkeypatch.setattr(sim_mod, "find_device", lambda udid=None, name=None, os_version=None: fake_device)
    monkeypatch.setattr(sim_mod, "first_booted", lambda: fake_device)

    # Mock workdir creation.
    import secrets
    new_sid = "session-B-new"
    call_count = [0]
    original_token = secrets.token_urlsafe

    def _fake_token(nbytes=None):
        call_count[0] += 1
        return new_sid

    monkeypatch.setattr("simdrive.session.secrets.token_urlsafe", _fake_token)

    # Ensure the workdir for session B can be created.
    (tmp_path / "sessions" / new_sid).mkdir(parents=True, exist_ok=True)

    def _fake_workroot():
        return tmp_path

    monkeypatch.setattr(session_mod, "_workroot", _fake_workroot)

    # tool_session_start with replace_existing=True.
    result = server_mod.tool_session_start({
        "udid": udid,
        "replace_existing": True,
    })

    # Session A must have been ended.
    assert session_a_id in ended_sessions, (
        f"Expected session A ({session_a_id}) to be ended via session.end(), "
        f"but ended_sessions={ended_sessions}. "
        "a12 replace_existing=True must call session.end() on the existing session."
    )

    # A new session_id must be returned (not session A's id).
    returned_sid = result.get("session_id")
    assert returned_sid is not None, f"Expected session_id in result, got: {result}"
    assert returned_sid != session_a_id, (
        f"Expected a new session_id (not {session_a_id!r}), "
        f"but got {returned_sid!r}."
    )


# ── test 13 ───────────────────────────────────────────────────────────────────


def test_session_start_without_replace_errors_when_active(tmp_path, monkeypatch):
    """replace_existing=False (default) raises when a session for the same udid exists.

    Fails on HEAD: no conflict detection exists — a second session would silently
    start for the same device without any error.
    """
    import simdrive.server as server_mod
    import simdrive.session as session_mod
    import simdrive.sim as sim_mod
    from simdrive.errors import SimdriveError

    udid = "SIM-UDID-NO-REPLACE"
    session_a_id = "session-A-blocking"

    # Plant session A as existing.
    session_a = _make_sim_session(session_a_id, udid=udid, workdir=tmp_path / "a")
    (tmp_path / "a").mkdir(parents=True, exist_ok=True)
    monkeypatch.setitem(session_mod._SESSIONS, session_a_id, session_a)

    # tool_session_start with replace_existing=False (or omitted).
    with pytest.raises((SimdriveError, Exception)) as exc_info:
        server_mod.tool_session_start({
            "udid": udid,
            "replace_existing": False,
        })

    exc_msg = str(exc_info.value).lower()
    assert any(kw in exc_msg for kw in ("already", "active", "session", "exists")), (
        f"Expected error message to mention 'already active' or similar, "
        f"but got: {str(exc_info.value)!r}. "
        "a12 must raise a clear SimdriveError when replace_existing=False "
        "and a session for the same udid is already active."
    )
