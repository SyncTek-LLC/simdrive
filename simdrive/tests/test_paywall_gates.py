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


class TestNoLicenseInstallWorks:
    """INIT-2026-610 (Chairman decision, 2026-08-26) — SimDrive was retired as
    a commercial product. Every public surface says it is free and unsold,
    but before this fix the paywall was never removed from the code: a fresh
    `pip install simdrive`, wired into an MCP client, called any tool and hit
    `LicenseError [license_not_found]` advertising a 14-day trial and a
    pricing page for a product no longer for sale.

    1,870 passing tests never caught this because conftest.py auto-issues a
    session dev-trial license at module load. This test uses the REAL
    ``@pytest.mark.no_license`` opt-out (conftest.py) which deletes that
    session license from disk for the duration of the test — it does NOT
    monkeypatch ``check_entitlement`` (see ``force_license_error`` above) and
    does NOT route through the session-license fixture. That is the point:
    this exercises the exact chokepoint a genuine clean install hits.

    Red-first: before the INIT-2026-610 fix to
    ``entitlement.check_entitlement()``, both tests below fail — the gate
    raises ``LicenseError[license_not_found]`` and the second test's
    assertion on ``ent.tier`` never gets there. After the fix, both pass.
    """

    @pytest.mark.no_license
    def test_tool_call_succeeds_with_no_license_on_disk(self) -> None:
        """A gated tool call must reach real tool logic — not be stopped by
        the paywall gate — when no license.json exists at all.

        ``session_status`` with a bogus session_id is used as the probe: if
        the entitlement gate passes (as it must), execution reaches
        ``session.get()`` and fails with the downstream ``no_session`` error
        instead. Asserting the code is ``no_session`` (and NOT
        ``license_not_found``) is the precise, deterministic proof that the
        gate let the call through.
        """
        from simdrive import errors as core_errors
        from simdrive.license.errors import LicenseError

        with pytest.raises(core_errors.SimdriveError) as exc_info:
            _invoke("session_status", {"session_id": "no-such-session"})

        assert not isinstance(exc_info.value, LicenseError), (
            "session_status raised a LicenseError with no license.json present — "
            "the paywall gate fired on a genuine clean install. This is the "
            "INIT-2026-610 defect: check_entitlement() must return a free "
            "entitlement when no license file exists, not raise."
        )
        assert exc_info.value.code == "no_session", (
            f"expected the downstream no_session error (proving the gate passed), "
            f"got code={exc_info.value.code!r}"
        )

    @pytest.mark.no_license
    def test_check_entitlement_returns_free_unlimited_entitlement(self) -> None:
        """Exercise the real default license path end to end (Path.home() /
        '.simdrive' / 'license.json', as resolved by conftest's fixture HOME)
        — not a monkeypatch, not a passed-in path."""
        from simdrive.license.entitlement import check_entitlement

        ent = check_entitlement()
        assert ent.tier != "trial"
        assert ent.journey_quota_per_month is None, "no-license install must be unlimited"
        assert ent.max_simulators is None, "no-license install must be unlimited"

    @pytest.mark.no_license
    def test_no_advertising_if_a_license_error_still_fires(self) -> None:
        """Even where a LicenseError CAN still legitimately fire (e.g. a
        present-but-malformed license.json), the envelope must carry no
        pricing_url and no trial-signup copy — SimDrive is not for sale."""
        from simdrive.license.errors import license_invalid, license_not_found

        for err in (license_invalid("corrupt"), license_not_found("/tmp/x.json")):
            env = err.to_dict()
            assert "pricing_url" not in env["error"]
            assert "trial_command_hint" not in env["error"]
            assert "auth_command_hint" not in env["error"]
            assert "simdrive.dev/pricing" not in env["error"]["message"]
            assert "trial start" not in env["error"]["message"]


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
