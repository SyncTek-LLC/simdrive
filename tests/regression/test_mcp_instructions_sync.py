"""
Regression: MCP instructions block must not advertise tool names that lack @mcp.tool registration.

This test prevents the class of bug where instructions mention a tool name (e.g. ios_dismiss_keyboard)
but no corresponding @mcp.tool(name=...) decorator exists — causing agents to call a non-existent tool
and receive an InputValidationError / tool-not-found error.

Run:
    pytest tests/regression/test_mcp_instructions_sync.py -v
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent.parent
SERVER_PY = REPO_ROOT / "src" / "specterqa" / "ios" / "mcp" / "server.py"


def _registered_tool_names(src: str) -> set[str]:
    """Return all tool names from @mcp.tool(name=...) decorators."""
    return set(re.findall(r'@mcp\.tool\(\s*\n?\s*name="([^"]+)"', src))


def _instruction_tool_names(src: str) -> set[str]:
    """Return all ios_* tokens mentioned inside the FastMCP instructions= string."""
    # Extract the instructions string literal (between triple-quoted block)
    instructions_match = re.search(
        r'instructions="""(.*?)"""',
        src,
        re.DOTALL,
    )
    if not instructions_match:
        return set()
    instructions_text = instructions_match.group(1)
    # Find all ios_* identifiers (word characters after ios_)
    return set(re.findall(r"\bios_[a-z_]+\b", instructions_text))


class TestMCPInstructionsSync:
    """Every ios_* name in instructions= must have a @mcp.tool(name=...) registration."""

    def test_no_phantom_tools_in_instructions(self):
        src = SERVER_PY.read_text(encoding="utf-8")
        registered = _registered_tool_names(src)
        mentioned = _instruction_tool_names(src)

        assert registered, "No registered tools found — check SERVER_PY path"
        assert mentioned, "No ios_* names found in instructions — check regex"

        phantom = mentioned - registered
        assert not phantom, (
            f"The following tool names appear in the instructions= block but have NO "
            f"@mcp.tool(name=...) registration:\n"
            + "\n".join(f"  - {name}" for name in sorted(phantom))
            + "\n\nFix: either implement the tool or remove it from the instructions block."
        )

    def test_registered_tools_count_matches_instructions_header(self):
        """The 'AVAILABLE TOOLS (N total)' line in instructions must match actual count."""
        src = SERVER_PY.read_text(encoding="utf-8")
        registered = _registered_tool_names(src)
        actual_count = len(registered)

        instructions_match = re.search(
            r'instructions="""(.*?)"""',
            src,
            re.DOTALL,
        )
        assert instructions_match, "Could not find instructions= block"
        instructions_text = instructions_match.group(1)

        count_match = re.search(r"AVAILABLE TOOLS \((\d+) total\)", instructions_text)
        if count_match:
            declared_count = int(count_match.group(1))
            assert declared_count == actual_count, (
                f"Instructions header says 'AVAILABLE TOOLS ({declared_count} total)' "
                f"but actual registered tool count is {actual_count}. "
                f"Update the header to match."
            )
