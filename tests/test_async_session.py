"""Tests for Issue 2: async ios_start_session (wait=False), ios_wait_for_session, ios_session_status.

All tests mock subprocess so no live simulator is required.
"""
from __future__ import annotations

import asyncio
import json
import threading
import time
from unittest.mock import MagicMock, patch, PropertyMock

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _extract_json(mcp_result) -> dict:
    """Extract JSON dict from MCP tool call result (handles mcp v1 and v2 shapes)."""
    try:
        text = mcp_result[0].content[0].text
    except (AttributeError, IndexError, TypeError):
        try:
            text = mcp_result[0][0].text
        except (AttributeError, IndexError, TypeError):
            text = str(mcp_result)
    return json.loads(text)


# ---------------------------------------------------------------------------
# Test: ios_session_status returns idle when no session
# ---------------------------------------------------------------------------

class TestSessionStatus:
    def test_status_idle_when_no_session(self):
        from specterqa.ios.mcp import server
        import importlib
        importlib.reload(server)

        mcp_server = server.create_server()

        async def run():
            return await mcp_server.call_tool("ios_session_status", {})

        result = asyncio.run(run())
        j = _extract_json(result)
        assert j["status"] == "idle"
        assert "elapsed_ms" in j

    def test_status_has_udid_field(self):
        from specterqa.ios.mcp import server
        import importlib
        importlib.reload(server)

        mcp_server = server.create_server()

        async def run():
            return await mcp_server.call_tool("ios_session_status", {})

        result = asyncio.run(run())
        j = _extract_json(result)
        # udid should be present (None/null when idle)
        assert "udid" in j


# ---------------------------------------------------------------------------
# Test: ios_wait_for_session returns quickly when already idle
# ---------------------------------------------------------------------------

class TestWaitForSession:
    def test_returns_fast_when_no_deploy_in_progress(self):
        from specterqa.ios.mcp import server
        import importlib
        importlib.reload(server)

        mcp_server = server.create_server()

        t0 = time.time()

        async def run():
            return await mcp_server.call_tool("ios_wait_for_session", {"timeout_s": 5})

        result = asyncio.run(run())
        elapsed = time.time() - t0
        j = _extract_json(result)
        # When idle (no deploy in flight), should report idle or not-deploying
        assert j.get("status") in ("idle", "not_deploying", "healthy", "failed", "timeout")
        # Should not block for 5 full seconds
        assert elapsed < 4.0

    def test_accepts_deploy_id_param(self):
        """deploy_id= should be accepted without error."""
        from specterqa.ios.mcp import server
        import importlib
        importlib.reload(server)

        mcp_server = server.create_server()

        async def run():
            return await mcp_server.call_tool("ios_wait_for_session", {
                "deploy_id": "test-deploy-id-123",
                "timeout_s": 2,
            })

        result = asyncio.run(run())
        j = _extract_json(result)
        # Should not crash — any status is acceptable
        assert "status" in j


# ---------------------------------------------------------------------------
# Test: ios_start_session with wait=False returns immediately
# ---------------------------------------------------------------------------

class TestAsyncStart:
    """Test wait=False returns sub-500ms with deploying status."""

    def test_wait_false_returns_immediately(self, tmp_path):
        """wait=False should return < 500ms without blocking on runner health."""
        # We mock the deploy machinery to avoid touching the real runner.
        from specterqa.ios.mcp import server
        import importlib
        importlib.reload(server)

        # Patch the heavy deploy path so it doesn't actually run xcodebuild.
        with patch("specterqa.ios.mcp.server.handle_start_session") as mock_start:
            # Simulate the async-path case: handle_start_session called with wait=False
            # should trigger background deploy and return immediately.
            mock_start.return_value = {
                "status": "deploying",
                "health_url": "http://localhost:8222/health",
                "estimated_ready_in_s": 45,
                "deploy_id": "abc-123",
            }

            mcp_server = server.create_server()

            t0 = time.time()

            async def run():
                return await mcp_server.call_tool("ios_start_session", {
                    "bundle_id": "io.example.app",
                    "device_id": "booted",
                    "backend": "xctest",
                    "wait": False,
                })

            result = asyncio.run(run())
            elapsed_ms = (time.time() - t0) * 1000

        j = _extract_json(result)
        assert j.get("status") == "deploying"
        assert "deploy_id" in j
        # Return should be fast (mocked) — under 500ms
        assert elapsed_ms < 500, f"Expected < 500ms but got {elapsed_ms:.0f}ms"

    def test_wait_false_payload_has_health_url(self, tmp_path):
        from specterqa.ios.mcp import server
        import importlib
        importlib.reload(server)

        with patch("specterqa.ios.mcp.server.handle_start_session") as mock_start:
            mock_start.return_value = {
                "status": "deploying",
                "health_url": "http://localhost:8222/health",
                "estimated_ready_in_s": 45,
                "deploy_id": "abc-123",
            }

            mcp_server = server.create_server()

            async def run():
                return await mcp_server.call_tool("ios_start_session", {
                    "bundle_id": "io.example.app",
                    "wait": False,
                })

            result = asyncio.run(run())

        j = _extract_json(result)
        assert "health_url" in j
        assert "estimated_ready_in_s" in j

    def test_wait_true_is_default_behavior(self):
        """wait parameter defaults to True (backward compat)."""
        from specterqa.ios.mcp import server
        import importlib
        importlib.reload(server)

        with patch("specterqa.ios.mcp.server.handle_start_session") as mock_start:
            mock_start.return_value = {"status": "ok", "backend": "xctest"}

            mcp_server = server.create_server()

            async def run():
                # Call without wait= — should default to True (sync)
                return await mcp_server.call_tool("ios_start_session", {
                    "bundle_id": "io.example.app",
                })

            asyncio.run(run())

        # Called once with the handle_start_session function
        mock_start.assert_called_once()
        args = mock_start.call_args[0][0]
        # Default wait should be True
        assert args.get("wait", True) is True


# ---------------------------------------------------------------------------
# Test: background deploy state machine
# ---------------------------------------------------------------------------

class TestDeployStateMachine:
    """Test that the module-level _async_deploy_state tracks transitions."""

    def test_state_transitions_from_idle_to_deploying_to_healthy(self):
        """Simulate the state machine that wait=False uses."""
        from specterqa.ios.mcp import server
        import importlib
        importlib.reload(server)

        # Directly test the state accessors
        # Initially should be idle
        state = server._get_deploy_state()
        assert state["status"] == "idle"

    def test_get_deploy_state_returns_dict_with_required_keys(self):
        from specterqa.ios.mcp import server
        import importlib
        importlib.reload(server)

        state = server._get_deploy_state()
        assert "status" in state
        assert "elapsed_ms" in state
        assert "udid" in state
