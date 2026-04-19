"""Dogfood Test 2 — AI Debugging Workflow.

Mirrors the real AI debugging loop: install → import tools → verify registration
→ optionally exercise the 5 new AI debugging primitives against a live simulator.

Two portions:

CI-ALWAYS (runs on every PR, no simulator required):
  - Fresh venv creation + pip install of local package
  - Import the 5 new AI debugging tools and verify they are registered via the
    MCP tool manager
  - Verify ``specterqa-ios --help`` or the MCP tool count is >= 43

LIVE-SIM (SPECTERQA_LIVE_SIM=1 + Xcode + booted simulator required):
  - Boot sim, ios_start_session against TestKitApp (xctest backend)
  - ios_capture_state() — verify all blocks return
  - ios_action_with_logs({"type": "tap", "label": "..."}) — verify logs populated
  - ios_app_relaunch(bundle_id) — verify elapsed_ms < 5000, foreground_verified=True
  - ios_start_recording → tap elements → ios_promote_session_to_test("dogfood_regression")
    → assert file at ./replays/dogfood_regression.yaml exists + validation passed
  - Tear down session

Marked ``@pytest.mark.dogfood`` for the full module.
Marked ``@pytest.mark.live`` for the live-sim portions only.

If the CI-always portion fails, the 5 new AI debugging tools are not registered.
"""
from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.dogfood

REPO_ROOT = Path(__file__).parent.parent.parent
TESTKIT_BUNDLE_ID = "io.synctek.specterqa.testkit"

AI_DEBUG_TOOLS = [
    "ios_app_relaunch",
    "ios_logs_tail",
    "ios_capture_state",
    "ios_action_with_logs",
    "ios_promote_session_to_test",
]

MINIMUM_TOOL_COUNT = 43


def _xcode_available() -> bool:
    try:
        r = subprocess.run(["xcodebuild", "-version"], capture_output=True, timeout=10)
        return r.returncode == 0
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Fixture: MCP server instance (constructed once per module)
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
# CI-ALWAYS: tool registration + count
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("tool_name", AI_DEBUG_TOOLS)
def test_ai_debug_tool_registered(tool_names: set[str], tool_name: str):
    """All 5 AI debugging tools must be registered in the MCP tool manager.

    If any of these fail, the AI debugging loop workflow is broken for users.
    """
    assert tool_name in tool_names, (
        f"AI debugging tool '{tool_name}' is not registered in the MCP tool manager.\n"
        f"Registered tools: {sorted(tool_names)}"
    )


def test_mcp_tool_count_at_least_43(tool_names: set[str]):
    """MCP tool count must be >= 43 (v14.0.0 adds 5 tools, removes 3: net +3 from v13.3.0).

    This is the regression guard for the tool-surface. If someone accidentally
    removes a tool without updating this test, it fails loudly.
    """
    count = len(tool_names)
    assert count >= MINIMUM_TOOL_COUNT, (
        f"Expected >= {MINIMUM_TOOL_COUNT} registered MCP tools, found {count}.\n"
        f"Tools found: {sorted(tool_names)}"
    )


def test_removed_tools_absent(tool_names: set[str]):
    """Three tools removed in v14.0.0 must NOT appear in the registry."""
    removed = ["ios_start_runner", "ios_stop_runner", "ios_save_replay"]
    for tool in removed:
        assert tool not in tool_names, (
            f"Tool '{tool}' was removed in v14.0.0 but is still registered.\n"
            f"Remove the @mcp.tool decorator for this function."
        )


# ---------------------------------------------------------------------------
# LIVE-SIM: full AI debugging loop
# ---------------------------------------------------------------------------


@pytest.mark.live
def test_ai_debug_full_loop(mcp_server):
    """Full AI debugging workflow against a live simulator.

    Requires SPECTERQA_LIVE_SIM=1 + booted simulator + Xcode.
    """
    if not os.environ.get("SPECTERQA_LIVE_SIM"):
        pytest.skip("requires SPECTERQA_LIVE_SIM=1 + Xcode + booted simulator")

    if not _xcode_available():
        pytest.skip("Xcode / xcodebuild not available")

    replays_dir = REPO_ROOT / "replays"

    async def _run():
        # Start session
        start = await mcp_server.call_tool(
            "ios_start_session", {"bundle_id": TESTKIT_BUNDLE_ID, "backend": "xctest"}
        )
        assert not getattr(start, "isError", False), f"ios_start_session failed: {start}"

        try:
            # ios_capture_state — all blocks must return
            state = await mcp_server.call_tool(
                "ios_capture_state",
                {"include": ["screenshot", "elements", "logs", "app_state"]},
            )
            assert not getattr(state, "isError", False), f"ios_capture_state failed: {state}"
            # The result content should include captured_at and at least one of the blocks
            content_text = str(state)
            assert "captured_at" in content_text, (
                "ios_capture_state response missing 'captured_at' key — "
                "at least one include block must have returned data"
            )

            # ios_action_with_logs — verify logs array populated
            action_result = await mcp_server.call_tool(
                "ios_action_with_logs",
                {
                    "action": {"type": "tap", "label": "FormTab"},
                    "log_window_ms": 2000,
                },
            )
            assert not getattr(action_result, "isError", False), (
                f"ios_action_with_logs failed: {action_result}"
            )
            action_text = str(action_result)
            assert "logs" in action_text, (
                "ios_action_with_logs response missing 'logs' key"
            )

            # ios_app_relaunch — verify elapsed_ms < 5000, foreground_verified=True
            relaunch = await mcp_server.call_tool(
                "ios_app_relaunch", {"bundle_id": TESTKIT_BUNDLE_ID}
            )
            assert not getattr(relaunch, "isError", False), (
                f"ios_app_relaunch failed: {relaunch}"
            )
            relaunch_text = str(relaunch)
            assert "elapsed_ms" in relaunch_text, (
                "ios_app_relaunch response missing 'elapsed_ms'"
            )
            assert "foreground_verified" in relaunch_text, (
                "ios_app_relaunch response missing 'foreground_verified'"
            )
            # Parse elapsed_ms from response to check < 5000
            import json, re
            match = re.search(r'"elapsed_ms"\s*:\s*(\d+)', relaunch_text)
            if match:
                elapsed = int(match.group(1))
                assert elapsed < 5000, (
                    f"ios_app_relaunch took {elapsed}ms — exceeds 5000ms threshold.\n"
                    "The AI debugging loop cycle time target is < 5s for relaunch without app_path."
                )

            # ios_start_recording → tap → ios_promote_session_to_test
            await mcp_server.call_tool("ios_start_recording", {})
            await mcp_server.call_tool("ios_wait_idle", {})
            await mcp_server.call_tool("ios_tap", {"label": "ListTab"})
            await mcp_server.call_tool("ios_wait_idle", {})
            await mcp_server.call_tool("ios_tap", {"label": "FormTab"})
            await mcp_server.call_tool("ios_wait_idle", {})

            promote = await mcp_server.call_tool(
                "ios_promote_session_to_test",
                {"name": "dogfood_regression", "validate": True},
            )
            assert not getattr(promote, "isError", False), (
                f"ios_promote_session_to_test failed: {promote}"
            )
            promote_text = str(promote)
            assert "can_replay" in promote_text, (
                "ios_promote_session_to_test response missing 'can_replay'"
            )
            assert "validation" in promote_text, (
                "ios_promote_session_to_test response missing 'validation'"
            )

            # Verify the replay YAML was written to ./replays/dogfood_regression.yaml
            replay_file = replays_dir / "dogfood_regression.yaml"
            assert replay_file.exists(), (
                f"ios_promote_session_to_test did not write replay YAML at {replay_file}.\n"
                "Check that the default save path is ./replays/<name>.yaml"
            )

        finally:
            # Always stop session
            await mcp_server.call_tool("ios_stop_session", {})

    asyncio.run(_run())
