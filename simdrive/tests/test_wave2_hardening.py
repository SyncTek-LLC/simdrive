"""Wave 2 integration tests for the hardening sprint.

Covers the wiring between Wave 1 sub-branch modules and the central
MCP dispatch in ``simdrive.server``:

- defense-in-depth quota check fires *before* the tool handler runs
- HID failures in clear_first / clear_field surface as typed
  exceptions instead of being silently swallowed
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from simdrive import errors, server, session
from simdrive.cloud.errors import QuotaExceededError
from simdrive.cloud.middleware.quotas import LocalQuotaSnapshot


# ── quota wire-up ───────────────────────────────────────────────────────────


def test_call_tool_no_session_id_skips_quota_check() -> None:
    """Pre-session tools (no session_id in args) must not be blocked by quota."""
    result = server.call_tool("version", {})
    assert isinstance(result, dict)
    assert "version" in result


def test_call_tool_unknown_session_id_skips_quota_check() -> None:
    """An invalid session_id must not throw QuotaExceededError — the underlying
    handler is responsible for raising no_session, not the quota gate."""
    # Use a tool that does session lookup so the underlying handler raises.
    with pytest.raises(errors.SimdriveError) as exc_info:
        server.call_tool("session_status", {"session_id": "definitely-not-a-real-session"})
    # The raised error must be no_session (from session.get), NOT a quota error.
    assert exc_info.value.code == "no_session"


def test_call_tool_raises_quota_exceeded_before_handler_runs() -> None:
    """A session with an exhausted local snapshot must trip QuotaExceededError
    inside the dispatcher, never reaching the tool body."""
    sid = "test-wave2-quota-exhausted"
    over_limit = LocalQuotaSnapshot(tier="free", runs_used=50, runs_limit=50)
    fake_session = SimpleNamespace(
        session_id=sid,
        quota_snapshot=over_limit,
    )
    session._SESSIONS[sid] = fake_session  # type: ignore[assignment]
    try:
        with pytest.raises(QuotaExceededError) as exc_info:
            server.call_tool("session_status", {"session_id": sid})
        assert exc_info.value.code == "cloud_quota_exceeded"
        assert "session_status" in str(exc_info.value)
    finally:
        session._SESSIONS.pop(sid, None)


def test_call_tool_under_quota_proceeds() -> None:
    """A session with a snapshot that's NOT over limit must dispatch normally."""
    sid = "test-wave2-quota-ok"
    under_limit = LocalQuotaSnapshot(tier="pro", runs_used=10, runs_limit=1000)
    fake_session = SimpleNamespace(
        session_id=sid,
        quota_snapshot=under_limit,
    )
    session._SESSIONS[sid] = fake_session  # type: ignore[assignment]
    try:
        # session_status will fail because fake_session isn't a real Session,
        # but it should fail with something other than QuotaExceededError.
        with pytest.raises(Exception) as exc_info:
            server.call_tool("session_status", {"session_id": sid})
        assert not isinstance(exc_info.value, QuotaExceededError)
    finally:
        session._SESSIONS.pop(sid, None)


# ── HID exception surfacing ─────────────────────────────────────────────────


def test_type_text_clear_first_surfaces_hid_chord_failure() -> None:
    """When hid_inject.chord raises during clear_first, the failure must
    propagate as HIDUnavailableError — never silently swallowed."""
    sid = "test-wave2-hid-chord-fail"
    fake_device = SimpleNamespace(udid="DEADBEEF-FAKE-FAKE-FAKE-DEADBEEFCAFE")
    fake_session = SimpleNamespace(
        session_id=sid,
        device=fake_device,
        target="simulator",
        last_screenshot_w=440,
        last_screenshot_h=956,
        last_screenshot_path=None,
        last_marks=[],
        last_action_at=0.0,
        workdir=None,
        recorder=None,
        wda_client=None,
        pixel_per_point_scale=1.0,
    )
    session._SESSIONS[sid] = fake_session  # type: ignore[assignment]
    try:
        with patch("simdrive.hid_inject.chord", side_effect=RuntimeError("bin missing")):
            with pytest.raises(errors.HIDUnavailableError) as exc_info:
                server.call_tool("type_text", {
                    "session_id": sid,
                    "text": "hello",
                    "clear_first": True,
                })
        assert "cmd-a chord failed" in str(exc_info.value)
    finally:
        session._SESSIONS.pop(sid, None)


def test_type_text_clear_first_surfaces_delete_keypress_failure() -> None:
    """When act.press_key('delete') raises after a successful chord, the
    failure must propagate as KeyboardNotReadyError."""
    sid = "test-wave2-hid-delete-fail"
    fake_device = SimpleNamespace(udid="DEADBEEF-FAKE-FAKE-FAKE-DEADBEEFCAFE")
    fake_session = SimpleNamespace(
        session_id=sid,
        device=fake_device,
        target="simulator",
        last_screenshot_w=440,
        last_screenshot_h=956,
        last_screenshot_path=None,
        last_marks=[],
        last_action_at=0.0,
        workdir=None,
        recorder=None,
        wda_client=None,
        pixel_per_point_scale=1.0,
    )
    session._SESSIONS[sid] = fake_session  # type: ignore[assignment]
    try:
        with patch("simdrive.hid_inject.chord", return_value=None), \
             patch("simdrive.act.press_key", side_effect=RuntimeError("hid input dead")):
            with pytest.raises(errors.KeyboardNotReadyError) as exc_info:
                server.call_tool("type_text", {
                    "session_id": sid,
                    "text": "hello",
                    "clear_first": True,
                })
        assert "delete keypress failed" in str(exc_info.value)
    finally:
        session._SESSIONS.pop(sid, None)
