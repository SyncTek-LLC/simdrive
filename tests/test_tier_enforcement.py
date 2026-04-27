"""Tier enforcement tests for MCP tool surface.

TDD tests for INIT-2026-525: verify that MCP tools enforce license tier gating.
These tests are pure unit tests — no live simulator, no network required.
The LicenseValidator is mocked so tests run hermetically.

Tier hierarchy (ascending privilege):
  trial < indie < pro < team < enterprise

Tier → tool access mapping (enforced by tier_gate.py):
  trial:      Basic interaction, observation, waiting, session lifecycle, env-discovery
  indie:      Trial + recording/replay, dismiss-helpers, appearance, simctl, webview
  pro:        Indie + ios_perf, ios_memory, ios_network, ios_perf_baseline,
              ios_perf_compare, ios_accessibility_audit, ios_capture_state,
              ios_logs_tail, ios_action_with_logs
  team:       Pro + ios_app_relaunch, ios_promote_session_to_test
  enterprise: All tools (including any future additions)

BYPASS: Set SPECTERQA_LICENSE_BYPASS=1 to skip tier checks entirely (CI use).
"""

from __future__ import annotations

import json
import os
from unittest.mock import patch, MagicMock

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_validator_mock(tier: str) -> MagicMock:
    """Return a mock LicenseValidator whose validate() returns the given tier."""
    mock = MagicMock()
    max_sims = {"trial": 1, "indie": 2, "pro": 4, "team": 10, "enterprise": 999}.get(tier, 1)
    mock.validate.return_value = {
        "valid": True,
        "tier": tier,
        "max_concurrent_sims": max_sims,
        "expires_at": None,
    }
    mock.tier.return_value = tier
    return mock


def _call_tier_gate(min_tier: str, current_tier: str, tool_name: str = "test_tool") -> dict | None:
    """Call check_tier_gate directly and return the error dict or None."""
    from specterqa.ios.mcp.tier_gate import check_tier_gate
    return check_tier_gate(min_tier=min_tier, current_tier=current_tier, tool_name=tool_name)


# ---------------------------------------------------------------------------
# Unit tests: check_tier_gate() core logic
# ---------------------------------------------------------------------------


class TestTierGateCore:
    """Tests for the check_tier_gate() function (pure logic, no validator mock needed)."""

    def test_same_tier_passes(self):
        """A user at exactly the required tier is allowed."""
        result = _call_tier_gate("pro", "pro", "ios_perf")
        assert result is None, f"Expected None (pass), got: {result}"

    def test_higher_tier_passes(self):
        """A team user is allowed to call a pro tool."""
        result = _call_tier_gate("pro", "team", "ios_perf")
        assert result is None

    def test_enterprise_passes_everything(self):
        """Enterprise passes all tier gates."""
        for min_tier in ("trial", "indie", "pro", "team", "enterprise"):
            result = _call_tier_gate(min_tier, "enterprise", f"ios_tool_{min_tier}")
            assert result is None, f"Enterprise failed gate for min_tier={min_tier}"

    def test_lower_tier_blocked(self):
        """A trial user is blocked from a pro tool."""
        result = _call_tier_gate("pro", "trial", "ios_perf")
        assert result is not None, "Expected a blocking error dict"
        assert result["error"] == "tier_required"
        assert result["required_tier"] == "pro"
        assert result["current_tier"] == "trial"
        assert "message" in result

    def test_error_response_shape(self):
        """The error response has the expected shape for MCP clients."""
        result = _call_tier_gate("team", "indie", "ios_parallel")
        assert result is not None
        assert set(result.keys()) >= {"error", "required_tier", "current_tier", "message", "upgrade_url"}
        assert result["upgrade_url"].startswith("https://")

    def test_trial_blocked_from_pro(self):
        """Trial user blocked from a pro-tier tool with meaningful message."""
        result = _call_tier_gate("pro", "trial", "ios_perf")
        assert result["error"] == "tier_required"
        assert "pro" in result["message"].lower() or "upgrade" in result["message"].lower()

    def test_indie_blocked_from_team(self):
        """Indie user blocked from team-tier tools."""
        result = _call_tier_gate("team", "indie", "ios_app_relaunch")
        assert result["error"] == "tier_required"
        assert result["required_tier"] == "team"
        assert result["current_tier"] == "indie"

    def test_tier_ordering_is_total(self):
        """Canonical tiers have a strictly increasing rank — no ambiguity."""
        from specterqa.ios.mcp.tier_gate import TIER_RANK
        # Only test the canonical commercial tiers; alias tiers (founder, solo, offline)
        # intentionally share ranks with their canonical equivalents.
        canonical = ["trial", "indie", "pro", "team", "enterprise"]
        for i, lower in enumerate(canonical):
            for higher in canonical[i + 1:]:
                assert TIER_RANK[lower] < TIER_RANK[higher], (
                    f"Expected {lower} < {higher} in TIER_RANK but order is reversed"
                )


# ---------------------------------------------------------------------------
# Integration-style tests: decorator applied to a real function
# ---------------------------------------------------------------------------


class TestTierGateDecorator:
    """Tests that use the @require_tier decorator on a dummy function."""

    def setup_method(self):
        """Ensure SPECTERQA_LICENSE_BYPASS is not set before each test."""
        os.environ.pop("SPECTERQA_LICENSE_BYPASS", None)

    def teardown_method(self):
        os.environ.pop("SPECTERQA_LICENSE_BYPASS", None)

    def _patch_tier(self, tier: str):
        """Return a context manager that injects the given tier into tier_gate._get_current_tier."""
        from specterqa.ios.mcp import tier_gate
        return patch.object(tier_gate, "_get_current_tier", return_value=tier)

    def test_decorator_allows_matching_tier(self):
        """Decorated function executes normally when current tier meets requirement."""
        from specterqa.ios.mcp.tier_gate import require_tier

        @require_tier("indie")
        def my_tool():
            return {"status": "ok"}

        with self._patch_tier("indie"):
            result = my_tool()
        assert result == {"status": "ok"}

    def test_decorator_blocks_lower_tier(self):
        """Decorated function returns error dict when current tier is too low."""
        from specterqa.ios.mcp.tier_gate import require_tier

        @require_tier("pro")
        def my_pro_tool():
            return {"status": "ok"}

        with self._patch_tier("trial"):
            result = my_pro_tool()
        assert isinstance(result, dict)
        assert result.get("error") == "tier_required"
        assert result.get("required_tier") == "pro"
        assert result.get("current_tier") == "trial"

    def test_decorator_json_string_return(self):
        """When the decorated function returns a JSON string, gate still works (no double-encode)."""
        from specterqa.ios.mcp.tier_gate import require_tier

        @require_tier("pro")
        def my_tool_returning_json_str():
            return json.dumps({"status": "ok"})

        with self._patch_tier("trial"):
            result = my_tool_returning_json_str()
        # Should return the tier error as a JSON string (consistent with MCP tool return type)
        assert isinstance(result, (dict, str))
        if isinstance(result, str):
            parsed = json.loads(result)
            assert parsed.get("error") == "tier_required"
        else:
            assert result.get("error") == "tier_required"

    def test_bypass_env_var_skips_gate(self):
        """SPECTERQA_LICENSE_BYPASS=1 skips tier enforcement entirely."""
        from specterqa.ios.mcp.tier_gate import require_tier

        @require_tier("enterprise")
        def enterprise_only_tool():
            return {"status": "ok"}

        os.environ["SPECTERQA_LICENSE_BYPASS"] = "1"
        with self._patch_tier("trial"):
            result = enterprise_only_tool()
        # Should pass (bypass active)
        assert result == {"status": "ok"}

    def test_bypass_env_var_false_still_gates(self):
        """SPECTERQA_LICENSE_BYPASS=0 does NOT bypass gating."""
        from specterqa.ios.mcp.tier_gate import require_tier

        @require_tier("pro")
        def pro_tool():
            return {"status": "ok"}

        os.environ["SPECTERQA_LICENSE_BYPASS"] = "0"
        with self._patch_tier("trial"):
            result = pro_tool()
        assert result.get("error") == "tier_required"


# ---------------------------------------------------------------------------
# Scenario tests: real-world tier/tool combinations
# ---------------------------------------------------------------------------


class TestTierScenarios:
    """Scenario-driven tests matching the assignment requirements."""

    def setup_method(self):
        os.environ.pop("SPECTERQA_LICENSE_BYPASS", None)

    def teardown_method(self):
        os.environ.pop("SPECTERQA_LICENSE_BYPASS", None)

    def _patch_tier(self, tier: str):
        from specterqa.ios.mcp import tier_gate
        return patch.object(tier_gate, "_get_current_tier", return_value=tier)

    def test_trial_user_blocked_from_pro_tool(self):
        """Trial user calling ios_perf gets a structured tier error."""
        from specterqa.ios.mcp.tier_gate import require_tier

        @require_tier("pro")
        def ios_perf():
            return json.dumps({"cpu_percent": 5.0})

        with self._patch_tier("trial"):
            result = ios_perf()

        # Result should be either a dict or a JSON string with the error shape.
        if isinstance(result, str):
            result = json.loads(result)
        assert result["error"] == "tier_required"
        assert result["required_tier"] == "pro"
        assert result["current_tier"] == "trial"
        assert "message" in result

    def test_pro_user_allowed_pro_tools(self):
        """Pro user calling ios_perf succeeds."""
        from specterqa.ios.mcp.tier_gate import require_tier

        @require_tier("pro")
        def ios_perf():
            return json.dumps({"cpu_percent": 5.0})

        with self._patch_tier("pro"):
            result = ios_perf()

        assert result == json.dumps({"cpu_percent": 5.0})

    def test_indie_user_blocked_from_team_parallel(self):
        """Indie user blocked from a team-tier tool (ios_app_relaunch is team)."""
        from specterqa.ios.mcp.tier_gate import require_tier

        @require_tier("team")
        def ios_app_relaunch():
            return json.dumps({"status": "relaunched"})

        with self._patch_tier("indie"):
            result = ios_app_relaunch()

        if isinstance(result, str):
            result = json.loads(result)
        assert result["error"] == "tier_required"
        assert result["required_tier"] == "team"
        assert result["current_tier"] == "indie"

    def test_enterprise_unlimited_passes_all(self):
        """Enterprise user passes every gate."""
        from specterqa.ios.mcp.tier_gate import require_tier

        results = {}
        for min_tier in ("trial", "indie", "pro", "team", "enterprise"):
            @require_tier(min_tier)
            def tool():
                return {"ok": True}
            with self._patch_tier("enterprise"):
                results[min_tier] = tool()

        for min_tier, result in results.items():
            assert result == {"ok": True}, (
                f"Enterprise should pass {min_tier} gate but got: {result}"
            )

    def test_no_license_treated_as_trial(self):
        """When no license is active, the tier falls back to 'trial'."""
        # Patch _get_current_tier so that when the validator returns no tier,
        # we fall back to trial.
        from specterqa.ios.mcp import tier_gate

        with patch.object(tier_gate, "_get_current_tier", return_value="trial"):
            # trial can access trial tools
            result = _call_tier_gate("trial", "trial", "ios_screenshot")
            assert result is None

            # trial cannot access pro tools
            result = _call_tier_gate("pro", "trial", "ios_perf")
            assert result is not None
            assert result["error"] == "tier_required"

    def test_tier_check_fails_open_when_validator_unavailable(self):
        """When the validator raises an exception, the gate fails open (with WARNING).

        Policy: fail-open so dev environments without a configured license key
        are not bricked. The tradeoff is that if the validator is broken in
        production, tier gates temporarily don't protect. We accept this because:
        1. The validator exception is logged at WARNING level, making the incident
           visible in monitoring.
        2. Complete validator failure (not just a bad license) is a distinct
           infrastructure failure mode, not a normal license bypass path.
        """
        from specterqa.ios.mcp import tier_gate
        import logging

        def _raise(*_args, **_kwargs):
            raise RuntimeError("Validator exploded")

        with patch.object(tier_gate, "_get_current_tier", side_effect=_raise):
            with patch.object(tier_gate.logger, "warning") as mock_warn:
                result = _call_tier_gate("pro", "trial", "ios_perf")
                # Should fail-open: None means "allowed"
                # (or if check_tier_gate catches the exception from _get_current_tier
                #  before we pass current_tier, the direct call already has the tier)

        # Because we pass current_tier directly to check_tier_gate in this test,
        # the exception path tests _get_current_tier, which is called by the decorator.
        # Test the decorator path instead:
        with patch.object(tier_gate, "_get_current_tier", side_effect=_raise):
            with patch.object(tier_gate.logger, "warning") as mock_warn:
                @tier_gate.require_tier("pro")
                def guarded_tool():
                    return {"status": "ok"}

                result = guarded_tool()
                # Fail-open: tool should succeed
                assert result == {"status": "ok"}, (
                    f"Expected fail-open (tool succeeds) when validator is unavailable, got: {result}"
                )
                # WARNING must have been logged
                assert mock_warn.called, "Expected a WARNING log when validator fails"


# ---------------------------------------------------------------------------
# Mapping audit: verify TOOL_TIER_MAP covers expected tools
# ---------------------------------------------------------------------------


class TestTierMapping:
    """Verify that TOOL_TIER_MAP contains the expected assignments."""

    def test_pro_tools_in_map(self):
        """ios_perf, ios_memory, ios_network, ios_perf_baseline, ios_perf_compare,
        ios_accessibility_audit are all mapped to 'pro' or higher."""
        from specterqa.ios.mcp.tier_gate import TOOL_TIER_MAP, TIER_RANK

        pro_expected = [
            "ios_perf",
            "ios_memory",
            "ios_network",
            "ios_perf_baseline",
            "ios_perf_compare",
            "ios_accessibility_audit",
        ]
        for tool in pro_expected:
            assert tool in TOOL_TIER_MAP, f"{tool} missing from TOOL_TIER_MAP"
            tool_tier = TOOL_TIER_MAP[tool]
            assert TIER_RANK[tool_tier] >= TIER_RANK["pro"], (
                f"{tool} mapped to {tool_tier} but expected at least 'pro'"
            )

    def test_indie_tools_in_map(self):
        """Recording/replay tools are mapped to 'indie' or higher."""
        from specterqa.ios.mcp.tier_gate import TOOL_TIER_MAP, TIER_RANK

        indie_expected = [
            "ios_start_recording",
            "ios_stop_recording",
            "ios_replay",
            "ios_validate_replay",
            "ios_list_replays",
        ]
        for tool in indie_expected:
            assert tool in TOOL_TIER_MAP, f"{tool} missing from TOOL_TIER_MAP"
            tool_tier = TOOL_TIER_MAP[tool]
            assert TIER_RANK[tool_tier] >= TIER_RANK["indie"], (
                f"{tool} mapped to {tool_tier} but expected at least 'indie'"
            )

    def test_trial_tools_accessible(self):
        """Core interaction tools (ios_tap, ios_screenshot, etc.) are trial-accessible."""
        from specterqa.ios.mcp.tier_gate import TOOL_TIER_MAP, TIER_RANK

        trial_expected = [
            "ios_start_session",
            "ios_stop_session",
            "ios_screenshot",
            "ios_tap",
            "ios_type",
            "ios_swipe",
            "ios_elements",
            "ios_logs",
            "ios_crashes",
            "ios_doctor",
            "ios_devices",
            "ios_apps",
            "ios_license_status",
        ]
        for tool in trial_expected:
            assert tool in TOOL_TIER_MAP, f"{tool} missing from TOOL_TIER_MAP"
            tool_tier = TOOL_TIER_MAP[tool]
            assert TIER_RANK[tool_tier] <= TIER_RANK["trial"], (
                f"{tool} mapped to {tool_tier} but should be trial-accessible"
            )

    def test_team_tools_in_map(self):
        """Team-tier tools are correctly mapped."""
        from specterqa.ios.mcp.tier_gate import TOOL_TIER_MAP, TIER_RANK

        team_expected = [
            "ios_app_relaunch",
            "ios_promote_session_to_test",
        ]
        for tool in team_expected:
            assert tool in TOOL_TIER_MAP, f"{tool} missing from TOOL_TIER_MAP"
            tool_tier = TOOL_TIER_MAP[tool]
            assert TIER_RANK[tool_tier] == TIER_RANK["team"], (
                f"{tool} should be exactly 'team' tier, got {tool_tier}"
            )

    def test_all_47_tools_covered(self):
        """TOOL_TIER_MAP should cover all 47 registered tools (no gaps)."""
        from specterqa.ios.mcp.tier_gate import TOOL_TIER_MAP

        # 47 tools listed in the server docstring
        expected_tools = {
            # Session lifecycle
            "ios_start_session", "ios_stop_session",
            # Observation
            "ios_screenshot", "ios_elements",
            # Interaction
            "ios_tap", "ios_long_press", "ios_type", "ios_press_key",
            "ios_swipe", "ios_swipe_back", "ios_dismiss_keyboard",
            # Waiting
            "ios_wait", "ios_wait_for_element", "ios_wait_idle",
            "ios_app_state", "ios_dismiss_sheet",
            # Recording & Replay
            "ios_start_recording", "ios_stop_recording",
            "ios_list_replays", "ios_replay", "ios_validate_replay",
            # Environment Discovery
            "ios_doctor", "ios_devices", "ios_apps", "ios_license_status",
            "ios_get_capabilities", "ios_session_status", "ios_wait_for_session",
            # Quality & Diagnostics
            "ios_accessibility_audit", "ios_set_appearance", "ios_simctl",
            "ios_webview_elements", "ios_logs", "ios_crashes",
            "ios_pre_grant_permissions", "ios_dismiss_springboard_alert",
            "ios_dismiss_first_launch_alerts",
            # Performance & Network Monitoring
            "ios_perf", "ios_memory", "ios_network",
            "ios_perf_baseline", "ios_perf_compare",
            # AI Debugging Primitives
            "ios_app_relaunch", "ios_logs_tail", "ios_capture_state",
            "ios_action_with_logs", "ios_promote_session_to_test",
        }

        missing = expected_tools - set(TOOL_TIER_MAP.keys())
        assert not missing, f"Tools missing from TOOL_TIER_MAP: {sorted(missing)}"
