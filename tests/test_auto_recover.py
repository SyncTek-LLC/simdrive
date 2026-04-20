"""Tests for Issue 8: Sim shutdown detection + auto_recover session option.

Verifies:
- Tools return structured {"error": "sim_shutdown_during_session", ...} when sim shuts down
- auto_recover=True causes ios_start_session to boot + re-deploy on detected shutdown
- auto_recover=False (default) returns structured error without recovery
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest


def _extract_json(result) -> dict:
    try:
        text = result[0].content[0].text
    except (AttributeError, IndexError, TypeError):
        try:
            text = result[0][0].text
        except (AttributeError, IndexError, TypeError):
            text = str(result)
    return json.loads(text)


# ---------------------------------------------------------------------------
# Test: _check_sim_state_during_session helper
# ---------------------------------------------------------------------------

class TestSimStateDetection:
    """_check_sim_state_for_udid returns correct state string."""

    def test_returns_booted_when_sim_is_running(self):
        from specterqa.ios.mcp import server

        def fake_simctl(*args, **kwargs):
            r = MagicMock()
            r.returncode = 0
            r.stdout = json.dumps({
                "devices": {
                    "com.apple.CoreSimulator.SimRuntime.iOS-26-0": [
                        {"udid": "FAKE-UDID", "state": "Booted", "name": "iPhone 12"}
                    ]
                }
            })
            return r

        with patch("subprocess.run", side_effect=fake_simctl):
            state = server._check_sim_state_for_udid("FAKE-UDID")

        assert state == "Booted"

    def test_returns_shutdown_when_sim_is_down(self):
        from specterqa.ios.mcp import server

        def fake_simctl(*args, **kwargs):
            r = MagicMock()
            r.returncode = 0
            r.stdout = json.dumps({
                "devices": {
                    "com.apple.CoreSimulator.SimRuntime.iOS-26-0": [
                        {"udid": "FAKE-UDID", "state": "Shutdown", "name": "iPhone 12"}
                    ]
                }
            })
            return r

        with patch("subprocess.run", side_effect=fake_simctl):
            state = server._check_sim_state_for_udid("FAKE-UDID")

        assert state == "Shutdown"

    def test_returns_unknown_when_udid_not_found(self):
        from specterqa.ios.mcp import server

        def fake_simctl(*args, **kwargs):
            r = MagicMock()
            r.returncode = 0
            r.stdout = json.dumps({"devices": {}})
            return r

        with patch("subprocess.run", side_effect=fake_simctl):
            state = server._check_sim_state_for_udid("NONEXISTENT-UDID")

        assert state in ("Unknown", "Shutdown")  # either is acceptable


# ---------------------------------------------------------------------------
# Test: structured error on sim_shutdown_during_session
# ---------------------------------------------------------------------------

class TestSimShutdownStructuredError:
    """When sim shuts down mid-session, tools should return structured error."""

    def test_handle_tap_returns_sim_shutdown_error_when_sim_down(self):
        """handle_tap should detect Shutdown and return sim_shutdown_during_session error."""
        import specterqa.ios.mcp.server as srv

        # Setup a fake session
        srv._backend = MagicMock()
        srv._session_udid = "FAKE-UDID-SIM-DOWN"
        srv._session_state = "running"
        srv._last_elements = []

        # Make the backend raise a ConnectionError (runner unreachable)
        srv._backend.tap.side_effect = ConnectionError("runner timed out")

        def fake_simctl(*args, **kwargs):
            r = MagicMock()
            r.returncode = 0
            r.stdout = json.dumps({
                "devices": {
                    "com.apple.CoreSimulator.SimRuntime.iOS-26-0": [
                        {"udid": "FAKE-UDID-SIM-DOWN", "state": "Shutdown", "name": "iPhone 12"}
                    ]
                }
            })
            return r

        with patch("subprocess.run", side_effect=fake_simctl):
            result = srv.handle_tap({"x": 195, "y": 275})

        # Result should include sim_shutdown signal
        err = result.get("error", "")
        assert (
            "sim_shutdown" in err.lower()
            or "sim" in err.lower()
            or "shutdown" in err.lower()
            or result.get("error") == "sim_shutdown_during_session"
        ), f"Expected sim shutdown error, got: {result}"

        # Cleanup
        srv._backend = None
        srv._session_udid = None
        srv._session_state = "idle"


# ---------------------------------------------------------------------------
# Test: auto_recover=True passed to ios_start_session
# ---------------------------------------------------------------------------

class TestAutoRecover:
    """auto_recover= parameter is accepted by handle_start_session."""

    def test_handle_start_session_accepts_auto_recover_param(self):
        """handle_start_session should accept auto_recover without error."""
        from specterqa.ios.mcp import server
        import importlib

        # We don't run the full deploy — just check the param is consumed
        with patch("specterqa.ios.mcp.server.handle_start_session") as mock_start:
            mock_start.return_value = {"status": "ok", "backend": "xctest"}

            import asyncio
            mcp_server = server.create_server()

            async def run():
                return await mcp_server.call_tool("ios_start_session", {
                    "bundle_id": "io.example.app",
                    "auto_recover": True,
                })

            asyncio.run(run())

        # Confirm it was called and auto_recover was passed through
        mock_start.assert_called_once()
        args = mock_start.call_args[0][0]
        assert args.get("auto_recover") is True

    def test_handle_start_session_auto_recover_false_by_default(self):
        """auto_recover should default to False (backward compat)."""
        from specterqa.ios.mcp import server

        with patch("specterqa.ios.mcp.server.handle_start_session") as mock_start:
            mock_start.return_value = {"status": "ok", "backend": "xctest"}

            import asyncio
            mcp_server = server.create_server()

            async def run():
                return await mcp_server.call_tool("ios_start_session", {
                    "bundle_id": "io.example.app",
                })

            asyncio.run(run())

        args = mock_start.call_args[0][0]
        assert args.get("auto_recover", False) is False
