"""Tests for SimDrive F#1: MCP server self-restart on version drift.

When the on-disk wheel version differs from the loaded version, the MCP
server should:

  1. Still return the current tool result so the caller is not lost.
  2. Annotate the result with both ``_simdrive_warning`` (existing) and a
     new ``_simdrive_action`` field set to ``"restarted"``.
  3. Schedule a background re-exec (``os.execv``) so the next tool call
     lands on the new on-disk code.
  4. Honor the ``SIMDRIVE_NO_AUTO_RESTART`` env-var opt-out: when set to
     a truthy value, the server warns but does NOT schedule a re-exec
     and does NOT add the ``_simdrive_action`` field.

The tests monkeypatch the re-exec trigger so no real process replacement
happens during pytest.
"""
from __future__ import annotations

import asyncio

import pytest

from simdrive import server


# ---------------------------------------------------------------------------
# Helpers — capture re-exec scheduling without actually calling os.execv.
# ---------------------------------------------------------------------------


class _ExecRecorder:
    """Stand-in for the scheduler; tracks invocations instead of forking."""

    def __init__(self) -> None:
        self.calls = 0

    def __call__(self, *args, **kwargs) -> None:  # noqa: D401 — simple recorder
        self.calls += 1


@pytest.fixture
def patched_drift(monkeypatch):
    """Force drift to be detected and capture re-exec scheduling."""
    monkeypatch.setattr(
        server, "_check_version_drift", lambda: "Auto-restarting to pick up disk version 9.9.9."
    )
    rec = _ExecRecorder()
    monkeypatch.setattr(server, "_schedule_self_restart", rec)
    # Reset the "already-scheduled" latch so each test starts fresh.
    monkeypatch.setattr(server, "_RESTART_SCHEDULED", False, raising=False)
    return rec


# ---------------------------------------------------------------------------
# call_tool (sync) path
# ---------------------------------------------------------------------------


def test_call_tool_on_drift_emits_action_restarted_and_schedules(patched_drift, monkeypatch):
    """Drift detected => result has _simdrive_action='restarted' + scheduler fired."""
    monkeypatch.delenv("SIMDRIVE_NO_AUTO_RESTART", raising=False)
    result = server.call_tool("version", {})
    assert result.get("_simdrive_action") == "restarted"
    assert result.get("_simdrive_warning") is not None
    assert patched_drift.calls == 1


def test_call_tool_opt_out_env_disables_restart(monkeypatch):
    """SIMDRIVE_NO_AUTO_RESTART=1 => warning only, no action, no scheduler call."""
    monkeypatch.setattr(
        server, "_check_version_drift", lambda: "Auto-restarting to pick up disk version 9.9.9."
    )
    rec = _ExecRecorder()
    monkeypatch.setattr(server, "_schedule_self_restart", rec)
    monkeypatch.setattr(server, "_RESTART_SCHEDULED", False, raising=False)
    monkeypatch.setenv("SIMDRIVE_NO_AUTO_RESTART", "1")

    result = server.call_tool("version", {})
    assert result.get("_simdrive_warning") is not None
    assert "_simdrive_action" not in result
    assert rec.calls == 0


def test_call_tool_no_drift_no_action_no_scheduler(monkeypatch):
    """When versions match, neither warning nor action nor scheduler fires."""
    monkeypatch.setattr(server, "_check_version_drift", lambda: None)
    rec = _ExecRecorder()
    monkeypatch.setattr(server, "_schedule_self_restart", rec)
    monkeypatch.setattr(server, "_RESTART_SCHEDULED", False, raising=False)
    monkeypatch.delenv("SIMDRIVE_NO_AUTO_RESTART", raising=False)

    result = server.call_tool("version", {})
    assert "_simdrive_warning" not in result
    assert "_simdrive_action" not in result
    assert rec.calls == 0


def test_call_tool_restart_only_scheduled_once_across_calls(monkeypatch):
    """A second drifted call should NOT double-schedule the restart."""
    monkeypatch.setattr(
        server, "_check_version_drift", lambda: "Auto-restarting to pick up disk version 9.9.9."
    )
    rec = _ExecRecorder()
    monkeypatch.setattr(server, "_schedule_self_restart", rec)
    monkeypatch.setattr(server, "_RESTART_SCHEDULED", False, raising=False)
    monkeypatch.delenv("SIMDRIVE_NO_AUTO_RESTART", raising=False)

    server.call_tool("version", {})
    server.call_tool("version", {})
    assert rec.calls == 1


# ---------------------------------------------------------------------------
# call_tool_async (MCP server hot path)
# ---------------------------------------------------------------------------


def test_call_tool_async_on_drift_emits_action_restarted(patched_drift, monkeypatch):
    monkeypatch.delenv("SIMDRIVE_NO_AUTO_RESTART", raising=False)

    async def _go():
        return await server.call_tool_async("version", {})

    result = asyncio.run(_go())
    assert result.get("_simdrive_action") == "restarted"
    assert result.get("_simdrive_warning") is not None
    assert patched_drift.calls == 1


def test_call_tool_async_opt_out_env_disables_restart(monkeypatch):
    monkeypatch.setattr(
        server, "_check_version_drift", lambda: "Auto-restarting to pick up disk version 9.9.9."
    )
    rec = _ExecRecorder()
    monkeypatch.setattr(server, "_schedule_self_restart", rec)
    monkeypatch.setattr(server, "_RESTART_SCHEDULED", False, raising=False)
    monkeypatch.setenv("SIMDRIVE_NO_AUTO_RESTART", "1")

    async def _go():
        return await server.call_tool_async("version", {})

    result = asyncio.run(_go())
    assert result.get("_simdrive_warning") is not None
    assert "_simdrive_action" not in result
    assert rec.calls == 0


# ---------------------------------------------------------------------------
# Warning text — should mention auto-restart and the disk version.
# ---------------------------------------------------------------------------


def test_check_version_drift_warning_mentions_auto_restart(monkeypatch):
    """The warning text should communicate the new auto-restart behaviour."""
    monkeypatch.setattr(server, "_disk_version", lambda: "9.9.9")
    msg = server._check_version_drift()
    assert msg is not None
    assert "9.9.9" in msg
    # Mention either "Auto-restart" or "auto-restart" so the caller knows the
    # server will handle the upgrade on the next tool call.
    assert "auto-restart" in msg.lower() or "restarting" in msg.lower()


# ---------------------------------------------------------------------------
# _schedule_self_restart helper — exists and is callable.
# ---------------------------------------------------------------------------


def test_schedule_self_restart_helper_exists():
    """The module exposes a _schedule_self_restart hook tests can patch."""
    assert hasattr(server, "_schedule_self_restart")
    assert callable(server._schedule_self_restart)
