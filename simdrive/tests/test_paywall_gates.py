"""Paywall gate tests — [internal-tracker].5.

Every MCP tool handler must call ``check_entitlement()`` at its entry. When the
entitlement check raises a ``LicenseError`` the tool MUST propagate the error
unchanged so the MCP envelope returned to the agent host carries the structured
``license_required`` payload (pricing URL, command hints, etc.).

Bootstrap commands (``trial``, ``license``, ``auth``) intentionally do NOT call
``check_entitlement()`` — they MUST work pre-license. The dispatcher dispatches
them outside the tool surface, so they are not in ``_TOOLS``.

TDD: written before adding the gate to each tool.
"""
from __future__ import annotations

import asyncio
import inspect
from typing import Any

import pytest


# The full canonical 32-tool registry — sourced from server._TOOLS at runtime.
# A test below pins this count so adding/removing tools without updating the
# gate is caught immediately.
EXPECTED_TOOL_COUNT = 36  # +3: perform_accessibility_action, get_announcements, set_text (host-AX a11y)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _all_tool_names() -> list[str]:
    """Return every tool name exposed via server._TOOLS."""
    from simdrive import server
    return [t["name"] for t in server._TOOLS]


def _invoke(name: str, arguments: dict) -> Any:
    """Invoke a tool by name handling sync + async handlers."""
    from simdrive import server
    handler = next(t["handler"] for t in server._TOOLS if t["name"] == name)
    if inspect.iscoroutinefunction(handler):
        return asyncio.run(handler(arguments))
    return handler(arguments)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def force_license_error(monkeypatch: pytest.MonkeyPatch):
    """Force ``check_entitlement`` to raise a not_found LicenseError.

    Patches the canonical import path used by ``simdrive.license.gate.gate()``.
    """
    from simdrive.license import errors as lic_errors

    def _raise(*_args, **_kwargs):
        raise lic_errors.license_not_found("/tmp/fake-license.json")

    # Patch in the entitlement module so every gate path picks it up.
    import simdrive.license.entitlement as ent
    monkeypatch.setattr(ent, "check_entitlement", _raise)
    return _raise


# ---------------------------------------------------------------------------
# Tool-registry shape
# ---------------------------------------------------------------------------


class TestToolRegistryShape:

    def test_tool_count_pinned_at_32(self) -> None:
        names = _all_tool_names()
        assert len(names) == EXPECTED_TOOL_COUNT, (
            f"Tool surface drifted: expected {EXPECTED_TOOL_COUNT}, got {len(names)}.\n"
            "If you added/removed a tool, update EXPECTED_TOOL_COUNT and ensure "
            "the new tool calls gate() at its entry."
        )


# ---------------------------------------------------------------------------
# Gate enforcement — parametrised across every tool
# ---------------------------------------------------------------------------


# Pinned list so test failure surfaces the offender by name (not "tool #17").
#
# NOTE: ``run_journey`` was removed from the public MCP tool surface — the
# in-process function ``tool_run_journey`` still exists (it carries its own
# license gate; tested by test_license_cli_trial.py) but is no longer exposed
# to MCP clients. The list below mirrors ``simdrive.server._TOOLS`` exactly.
GATED_TOOLS: list[str] = [
    "session_start",
    "session_end",
    "session_status",
    "observe",
    "tap",
    "tap_and_wait_keyboard",
    "swipe",
    "type_text",
    "press_key",
    "record_start",
    "record_stop",
    "replay",
    "list_devices",
    "logs",
    "perf",
    "perf_baseline",
    "perf_compare",
    "memory",
    "doctor",
    "app_state",
    "apps",
    "crashes",
    "dismiss_first_launch_alerts",
    "pre_grant_permissions",
    "set_appearance",
    "dismiss_sheet",
    "list_replays",
    "validate_replay",
    "lint_recordings",
    "migrate_recording",
    "version",
    "clear_field",
    "load_journey",
    "perform_accessibility_action",
    "get_announcements",
    "set_text",
]


class TestGateAppliedToEveryTool:

    def test_pinned_gated_list_matches_registry(self) -> None:
        registry = set(_all_tool_names())
        pinned = set(GATED_TOOLS)
        # version is allowed to be exempt (see below) but should still be present
        # in the registry. Pinned list is what the gate enforces.
        assert pinned <= registry, f"pinned but not in registry: {pinned - registry}"
        # Anything in the registry that is NOT pinned must be added intentionally.
        extras = registry - pinned
        assert not extras, (
            f"new tool(s) not in GATED_TOOLS — add gate() + update this list: {extras}"
        )

    @pytest.mark.parametrize("tool_name", GATED_TOOLS)
    def test_tool_raises_license_error_when_no_license(
        self,
        tool_name: str,
        force_license_error,
    ) -> None:
        """Every tool must propagate LicenseError before doing real work.

        We invoke with arguments designed to fail loudly *after* the gate
        (unknown session_id, missing required fields). The gate fires first,
        so the LicenseError comes out the top — never the downstream error.
        """
        from simdrive.license.errors import LicenseError

        with pytest.raises(LicenseError) as exc_info:
            _invoke(tool_name, {"session_id": "no-such-session"})

        assert exc_info.value.code == "license_not_found", (
            f"tool {tool_name!r} did not surface license_not_found "
            f"(got {exc_info.value.code!r}). It is missing the gate() call "
            "or catches LicenseError internally."
        )


# ---------------------------------------------------------------------------
# Gate behaviour
# ---------------------------------------------------------------------------


class TestGateBehaviour:

    def test_gate_passes_when_check_entitlement_returns(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """``gate()`` must return None (no raise) when check_entitlement succeeds."""
        from simdrive.license import gate as gate_mod
        from simdrive.license.entitlement import Entitlement
        import simdrive.license.entitlement as ent

        ok_ent = Entitlement(
            tier="pro", seats=1, expires_at=2_000_000_000, customer_email="ok@example.com",
        )
        monkeypatch.setattr(ent, "check_entitlement", lambda *a, **kw: ok_ent)
        # Should not raise
        gate_mod.gate()

    def test_gate_re_raises_license_error_unchanged(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from simdrive.license import gate as gate_mod
        from simdrive.license.errors import LicenseError, license_not_found
        import simdrive.license.entitlement as ent

        original = license_not_found("/x")
        def _raise(*_a, **_kw):
            raise original
        monkeypatch.setattr(ent, "check_entitlement", _raise)

        with pytest.raises(LicenseError) as exc:
            gate_mod.gate()
        assert exc.value.code == original.code


# ---------------------------------------------------------------------------
# Bootstrap commands NOT in the tool surface
# ---------------------------------------------------------------------------


class TestRunJourneyGated:
    """``run_journey`` is no longer in the MCP registry but the underlying
    ``tool_run_journey`` async function still exists (consumed by the journey
    CLI subcommands). It must keep its gate.
    """

    def test_tool_run_journey_propagates_license_error(self, force_license_error) -> None:
        from simdrive import server
        from simdrive.license.errors import LicenseError

        with pytest.raises(LicenseError) as exc_info:
            asyncio.run(server.tool_run_journey({"session_id": "no-such"}))
        assert exc_info.value.code == "license_not_found"


class TestBootstrapCommandsExempt:
    """trial / license / auth are CLI subcommands, NOT MCP tools.

    Verifies they are absent from the tool registry — adding them there would
    accidentally apply the paywall to the very commands a user runs to escape
    the paywall.
    """

    @pytest.mark.parametrize("forbidden", ["trial", "license", "auth"])
    def test_bootstrap_command_not_a_tool(self, forbidden: str) -> None:
        assert forbidden not in _all_tool_names(), (
            f"{forbidden!r} must not be an MCP tool — it is a bootstrap CLI "
            "subcommand that has to work without a license."
        )
