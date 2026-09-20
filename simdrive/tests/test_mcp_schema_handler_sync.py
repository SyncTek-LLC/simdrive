"""INIT-2026-641 item 4.2 (D4) — schema-to-handler sync guard.

Closes the defect class that let observe's `compact`/`confidence_floor`/
`mark_limit`/`capture_observability` and tap's `verify_change` stay
implemented, wired, and tested, while being absent from the advertised MCP
`inputSchema` — undiscoverable to any agent reading the tool description.

Note (per the test plan): `test_mcp_instructions_sync.py`, referenced in the
architecture doc as prior art "guarding the retired tree," does not exist
anywhere in this tree (confirmed by `grep -rln inputSchema tests/` before
writing this file). This is new construction, not a fix to an existing test.
"""
from __future__ import annotations

import pytest

from simdrive import server


@pytest.mark.parametrize(
    "tool",
    server._TOOLS,
    ids=[t["name"] for t in server._TOOLS],
)
def test_every_tool_handler_param_is_declared_in_schema(tool):
    """Every key a tool's handler reads off `arguments` must be advertised
    in that tool's `inputSchema.properties`, with no silent exemption.

    Before item 4.2 lands, this must fail for BOTH `observe` (missing
    `compact`, `confidence_floor`, `mark_limit`, `capture_observability`)
    and `tap` (missing `verify_change`) in the same run — proving the
    checker is general-purpose, not hand-fit to one tool.
    """
    undeclared = server._schema_handler_undeclared(tool)
    assert not undeclared, (
        f"tool {tool['name']!r} handler reads {sorted(undeclared)} not "
        f"present in its inputSchema.properties"
    )


def test_schema_sync_detects_a_newly_introduced_drift():
    """Meta-test: the checker itself must be able to fail, not just always
    pass because its regex never matches anything. Uses a small synthetic
    handler + schema pair, not a real server.py handler, so this test does
    not depend on any other item in this initiative landing or not.
    """

    def _synthetic_handler(arguments: dict) -> dict:
        session_id = arguments["session_id"]
        # 'secret_extra' is read but never declared below — this is the
        # drift the checker must catch.
        extra = arguments.get("secret_extra", None)
        return {"session_id": session_id, "extra": extra}

    drifted_tool = {
        "name": "synthetic_drifted_tool",
        "inputSchema": {
            "type": "object",
            "properties": {"session_id": {"type": "string"}},
        },
        "handler": _synthetic_handler,
    }
    undeclared = server._schema_handler_undeclared(drifted_tool)
    assert undeclared == {"secret_extra"}

    # Companion: fixing the schema (adding the missing property) makes the
    # same checker report clean — proving it is not permanently red either.
    in_sync_tool = {
        "name": "synthetic_drifted_tool",
        "inputSchema": {
            "type": "object",
            "properties": {
                "session_id": {"type": "string"},
                "secret_extra": {"type": "string"},
            },
        },
        "handler": _synthetic_handler,
    }
    assert server._schema_handler_undeclared(in_sync_tool) == set()


def test_schema_sync_exempt_allowlist_is_empty():
    """The exempt allowlist starts empty and every fix in this initiative
    closes a gap by declaring the parameter, not by exempting it. A
    non-empty allowlist here would mean a tool has an accepted-but-hidden
    parameter with no test enforcing it stays declared — exactly the
    silent-drift shape this whole guard exists to prevent.
    """
    assert server._SCHEMA_SYNC_EXEMPT == {}


def test_observe_schema_declares_the_economy_mode_knobs():
    """Directed regression pin for item 4.2's own named defect: observe's
    compact/confidence_floor/mark_limit/capture_observability parameters are
    implemented and tested in observe.py but were absent from the advertised
    inputSchema (server.py, observe tool). Confirmed by reading server.py
    directly before this fix: the inputSchema.properties for observe listed
    only session_id, annotate, capture_logs, log_lines, log_predicate,
    include_screenshot_b64.
    """
    observe_tool = next(t for t in server._TOOLS if t["name"] == "observe")
    declared = server._schema_declared_params(observe_tool)
    for name in ("compact", "confidence_floor", "mark_limit", "capture_observability"):
        assert name in declared, f"observe's inputSchema is missing {name!r}"
