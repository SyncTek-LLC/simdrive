"""MCP tool registration audit — v14.0.0a1.

Asserts that:
1. The three tools removed in v14.0.0a1 (ios_start_runner, ios_stop_runner,
   ios_save_replay) are NOT present in the registered tool list.
2. The core session lifecycle tools (ios_start_session, ios_stop_session) ARE present.
3. The total tool count matches the documented value (38 tools).

These are pure unit tests — no live simulator, no network, no MCP transport.
The FastMCP instance is constructed in process and list_tools() is queried directly.

INIT-2026-525 — SpecterQA iOS v14.0.0a1 Phase 1 audit.
"""
from __future__ import annotations

import asyncio
import pytest


# ---------------------------------------------------------------------------
# Fixture — build the MCP server once per module (expensive construction)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def mcp_server():
    """Return the FastMCP instance from create_server()."""
    try:
        from specterqa.ios.mcp.server import create_server
    except ImportError as exc:
        pytest.skip(f"MCP package not available: {exc}")
    return create_server()


@pytest.fixture(scope="module")
def tool_names(mcp_server) -> set[str]:
    """Return the set of registered tool names."""
    tools = asyncio.get_event_loop().run_until_complete(mcp_server.list_tools())
    return {t.name for t in tools}


# ---------------------------------------------------------------------------
# 1. Removed tools must NOT be present
# ---------------------------------------------------------------------------


REMOVED_IN_V14 = [
    "ios_start_runner",
    "ios_stop_runner",
    "ios_save_replay",
]


@pytest.mark.parametrize("tool_name", REMOVED_IN_V14)
def test_removed_tool_absent(tool_names: set[str], tool_name: str):
    """v14.0.0a1 removed three manual-lifecycle tools — they must not appear in the registry."""
    assert tool_name not in tool_names, (
        f"Tool '{tool_name}' was removed in v14.0.0a1 but is still registered. "
        "Remove the @mcp.tool decorator for this function."
    )


# ---------------------------------------------------------------------------
# 2. Core session lifecycle tools must still be present
# ---------------------------------------------------------------------------


REQUIRED_SESSION_TOOLS = [
    "ios_start_session",
    "ios_stop_session",
]


@pytest.mark.parametrize("tool_name", REQUIRED_SESSION_TOOLS)
def test_required_session_tool_present(tool_names: set[str], tool_name: str):
    """Session lifecycle tools must remain registered after the v14 consolidation."""
    assert tool_name in tool_names, (
        f"Required session tool '{tool_name}' is missing from the MCP tool registry."
    )


# ---------------------------------------------------------------------------
# 3. Total tool count matches documentation
# ---------------------------------------------------------------------------


_EXPECTED_TOOL_COUNT = 49  # v16.0.0 adds ios_observe + ios_act (vision-first primitives)


def test_total_tool_count(tool_names: set[str]):
    """Registered tool count must equal the documented 43 (v14.0.0b1 adds 5 AI debugging tools).

    If you add or remove a tool, update _EXPECTED_TOOL_COUNT AND the
    create_server() docstring AND the CLAUDE.md instructions header.
    """
    count = len(tool_names)
    assert count == _EXPECTED_TOOL_COUNT, (
        f"Expected {_EXPECTED_TOOL_COUNT} registered tools, found {count}.\n"
        f"Tools found: {sorted(tool_names)}"
    )


# ---------------------------------------------------------------------------
# 4. Phase 2 tools must be present
# ---------------------------------------------------------------------------


PHASE2_TOOLS = [
    "ios_app_relaunch",
    "ios_logs_tail",
    "ios_capture_state",
    "ios_action_with_logs",
    "ios_promote_session_to_test",
]


@pytest.mark.parametrize("tool_name", PHASE2_TOOLS)
def test_phase2_tool_present(tool_names: set[str], tool_name: str):
    """v14.0.0b1 Phase 2 adds 5 AI debugging tools — all must be registered."""
    assert tool_name in tool_names, (
        f"Phase 2 tool '{tool_name}' is missing from the MCP tool registry."
    )
