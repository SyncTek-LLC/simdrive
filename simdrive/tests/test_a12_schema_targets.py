"""a12: every MCP tool description has a target marker (sim only) or (sim + device).

Tests:
  5. test_every_mcp_tool_has_target_marker
     - Load the MCP tool registry (_TOOLS). For each tool description, assert it
       contains either '(sim only)' or '(sim + device)'. Lists offending tools.
  6. test_dismiss_sheet_now_marked_sim_and_device
     - dismiss_sheet description contains '(sim + device)' after a12.
  7. test_record_start_still_marked_sim_only
     - record_start description contains '(sim only)'.

All 3 tests FAIL on HEAD because:
  - No tool descriptions currently contain '(sim only)' or '(sim + device)' markers.
  - dismiss_sheet is marked as sim-only in the code (raises device_input_unavailable).
  - record_start has no marker either.
"""
from __future__ import annotations

import pytest


# ── test 5 ────────────────────────────────────────────────────────────────────


def test_every_mcp_tool_has_target_marker():
    """Every tool description must contain '(sim only)' or '(sim + device)'.

    Fails on HEAD: no tool in _TOOLS has either marker in its description.
    """
    import simdrive.server as server_mod

    tools = server_mod._TOOLS
    assert tools, "Expected _TOOLS to be non-empty"

    missing = []
    for tool in tools:
        name = tool.get("name", "<unnamed>")
        description = tool.get("description", "")
        has_sim_only = "(sim only)" in description
        has_sim_device = "(sim + device)" in description
        if not (has_sim_only or has_sim_device):
            missing.append(name)

    assert not missing, (
        f"The following {len(missing)} tool(s) are missing a target marker "
        f"('(sim only)' or '(sim + device)') in their description:\n"
        + "\n".join(f"  - {name}" for name in missing)
        + "\n\na12 requires every tool to declare its device-target support."
    )


# ── test 6 ────────────────────────────────────────────────────────────────────


def test_dismiss_sheet_now_marked_sim_and_device():
    """dismiss_sheet description must contain '(sim + device)' after a12.

    a12 adds device support to dismiss_sheet via WDA swipe, so the marker
    must be updated from '(sim only)' to '(sim + device)'.

    Fails on HEAD: dismiss_sheet description has no target marker at all.
    """
    import simdrive.server as server_mod

    tools_by_name = {t["name"]: t for t in server_mod._TOOLS}
    assert "dismiss_sheet" in tools_by_name, "dismiss_sheet must be in _TOOLS"

    description = tools_by_name["dismiss_sheet"].get("description", "")
    assert "(sim + device)" in description, (
        f"dismiss_sheet description must contain '(sim + device)' after a12, "
        f"but got: {description!r}"
    )


# ── test 7 ────────────────────────────────────────────────────────────────────


def test_record_start_still_marked_sim_only():
    """record_start description must contain '(sim only)'.

    Recording is a simulator-only feature (simctl video record). This marker
    must be present and must NOT accidentally become '(sim + device)'.

    Fails on HEAD: record_start description has no target marker at all.
    """
    import simdrive.server as server_mod

    tools_by_name = {t["name"]: t for t in server_mod._TOOLS}
    assert "record_start" in tools_by_name, "record_start must be in _TOOLS"

    description = tools_by_name["record_start"].get("description", "")
    assert "(sim only)" in description, (
        f"record_start description must contain '(sim only)', "
        f"but got: {description!r}"
    )
    assert "(sim + device)" not in description, (
        "record_start must NOT be marked '(sim + device)' — recording is sim-only."
    )
