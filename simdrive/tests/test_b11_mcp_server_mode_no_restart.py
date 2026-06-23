"""b11 FIX 1 — auto-restart must be suppressed while serving as an MCP server.

Background: when simdrive runs as an MCP stdio server and detects that the
on-disk wheel is newer than the loaded code, the pre-b11 behaviour scheduled an
``os.execv`` self-restart. Re-execing the process while the MCP client holds an
initialized stdio session DESYNCS the transport — every subsequent JSON-RPC
request fails ``MCP error -32602: Invalid request parameters``, unrecoverable
without a client ``/mcp`` reconnect. That is strictly worse than the drift it
was trying to cure.

The fix gates auto-restart on ``_MCP_SERVER_MODE`` (set True only inside
``_serve_async``, the one code path that owns the stdio transport):

  - In MCP-server mode → NEVER restart; surface a clear, actionable warning
    (no ``_simdrive_action``, no scheduled re-exec).
  - Outside MCP-server mode (CLI smokes, embedders, pytest) → the safe
    self-restart behaviour is preserved.

These tests force upgrade-drift and assert the scheduler is never called when
the server is in MCP-server mode, and that it still fires when it is not.
"""
from __future__ import annotations

import asyncio

import pytest

from simdrive import server


class _ExecRecorder:
    """Records re-exec scheduling instead of replacing the process."""

    def __init__(self) -> None:
        self.calls = 0

    def __call__(self, *args, **kwargs) -> None:
        self.calls += 1


@pytest.fixture
def force_upgrade_drift(monkeypatch):
    """Make _maybe_handle_drift see a strict upgrade and capture scheduling."""
    monkeypatch.setattr(server, "_disk_version", lambda: "9.9.9")
    monkeypatch.setattr(server, "_is_upgrade", lambda disk, loaded: True)
    monkeypatch.setattr(server, "_LOADED_VERSION", "1.0.0", raising=False)
    rec = _ExecRecorder()
    monkeypatch.setattr(server, "_schedule_self_restart", rec)
    monkeypatch.setattr(server, "_RESTART_SCHEDULED", False, raising=False)
    monkeypatch.delenv("SIMDRIVE_NO_AUTO_RESTART", raising=False)
    monkeypatch.delenv(server._RESTART_COUNT_ENV, raising=False)
    return rec


@pytest.fixture
def mcp_server_mode(monkeypatch):
    """Flip the MCP-server-mode flag for the duration of one test."""
    monkeypatch.setattr(server, "_MCP_SERVER_MODE", True, raising=False)


# ---------------------------------------------------------------------------
# In MCP-server mode: NO restart, but a clear warning.
# ---------------------------------------------------------------------------


def test_mcp_server_mode_does_not_schedule_restart(force_upgrade_drift, mcp_server_mode):
    """Drift in MCP-server mode => warning only, NO action, NO scheduler call."""
    result = server.call_tool("version", {})
    assert "_simdrive_action" not in result
    assert force_upgrade_drift.calls == 0
    warning = result.get("_simdrive_warning")
    assert warning is not None
    # The warning must be actionable: name the transport hazard + the fix.
    assert "-32602" in warning
    assert "/mcp" in warning.lower()
    assert "9.9.9" in warning


def test_mcp_server_mode_async_does_not_schedule_restart(force_upgrade_drift, mcp_server_mode):
    """Same suppression on the async (MCP hot-path) dispatcher."""

    async def _go():
        return await server.call_tool_async("version", {})

    result = asyncio.run(_go())
    assert "_simdrive_action" not in result
    assert force_upgrade_drift.calls == 0
    assert "-32602" in result.get("_simdrive_warning", "")


def test_check_version_drift_mcp_mode_message_is_actionable(monkeypatch, mcp_server_mode):
    """The drift string itself (in MCP mode) names the desync + recovery."""
    monkeypatch.setattr(server, "_disk_version", lambda: "9.9.9")
    monkeypatch.setattr(server, "_LOADED_VERSION", "1.0.0", raising=False)
    msg = server._check_version_drift()
    assert msg is not None
    assert "9.9.9" in msg
    assert "-32602" in msg
    assert "mcp-server mode" in msg.lower()
    assert "/mcp" in msg.lower()
    # Must NOT promise an auto-restart it will never perform.
    assert "auto-restarting to pick up" not in msg.lower()


# ---------------------------------------------------------------------------
# Outside MCP-server mode: safe self-restart preserved.
# ---------------------------------------------------------------------------


def test_non_mcp_mode_still_schedules_restart(force_upgrade_drift, monkeypatch):
    """When NOT in MCP-server mode, upgrade-drift still schedules a re-exec."""
    monkeypatch.setattr(server, "_MCP_SERVER_MODE", False, raising=False)
    result = server.call_tool("version", {})
    assert result.get("_simdrive_action") == "restarting"
    assert force_upgrade_drift.calls == 1


def test_in_mcp_server_mode_helper_reflects_flag(monkeypatch):
    """_in_mcp_server_mode() mirrors the module flag."""
    monkeypatch.setattr(server, "_MCP_SERVER_MODE", True, raising=False)
    assert server._in_mcp_server_mode() is True
    monkeypatch.setattr(server, "_MCP_SERVER_MODE", False, raising=False)
    assert server._in_mcp_server_mode() is False
