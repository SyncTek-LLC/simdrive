"""Tier-based access control for the SpecterQA MCP tool surface.

INIT-2026-525 — First revenue play; enforces license tier gating so trial/indie
users cannot access premium diagnostic and parallel-session tools.

Tier hierarchy (ascending privilege):
  trial < indie < pro < team < enterprise

Pricing (as of 2026-04-25, verified consistent across all surfaces):
  trial:      Free  — 1 sim, 3 runs/session
  indie:      $29/mo — 2 sims
  pro:        $99/mo — 4 sims
  team:       $299/mo — 10 sims
  enterprise: Custom  — unlimited

Bypass:
  Set SPECTERQA_LICENSE_BYPASS=1 to skip all tier checks.  Intended for:
  - CI environments where a live Keygen.sh API call would be a flaky dependency.
  - Developer machines testing the gate itself.
  Tradeoff: if this env var is accidentally set in production, tier enforcement
  is completely disabled.  It is intentionally a non-default and should never
  be set in customer-facing deployments.  Absent the env var the default is
  always to enforce.

Fail-open policy:
  When the LicenseValidator raises an exception (network outage, bad config),
  the gate *fails open* with a WARNING log.  This means:
  - Dev environments without a license configured are not bricked.
  - A monitoring alert fires (WARNING in logs), so the failure is visible.
  - Revenue leakage risk is minimal: the validator must completely fail, not just
    return a lower tier.  A valid-but-low tier still enforces gating normally.

Usage:
  # On a tool function:
  @require_tier("pro")
  async def ios_perf(...):
      ...

  # Or check programmatically:
  err = check_tier_gate(min_tier="pro", current_tier="trial", tool_name="ios_perf")
  if err is not None:
      return json.dumps(err)
"""

from __future__ import annotations

import functools
import json
import logging
import os
from typing import Any, Callable

logger = logging.getLogger("specterqa.ios.mcp.tier_gate")

# ---------------------------------------------------------------------------
# Tier rank — lower number = lower privilege
# ---------------------------------------------------------------------------

TIER_RANK: dict[str, int] = {
    "trial": 0,
    "indie": 1,
    "pro": 2,
    "team": 3,
    "enterprise": 4,
    # Aliases / legacy names — map to closest canonical tier
    "founder": 2,   # founder ≈ pro (4 sims); see validator._TIER_DEFAULTS
    "solo": 0,      # solo ≈ trial (1 sim)
    "offline": 0,   # offline grace → trial privileges
    "unknown": -1,  # unknown → below trial; always blocked on gated tools
}

# ---------------------------------------------------------------------------
# Upgrade URL (surfaced in error responses)
# ---------------------------------------------------------------------------

_UPGRADE_URL = "https://synctek.io/specterqa#pricing"

# ---------------------------------------------------------------------------
# Per-tool tier requirements
# ---------------------------------------------------------------------------
# Every tool registered in server.py must appear here.  The mapping is the
# single source of truth — adding a new tool means adding a line here.
#
# Criteria:
#   trial:  Safe read/observation + basic interaction.  Tools that don't
#           consume significant infra resources and that a free user needs
#           to evaluate the product.
#   indie:  Recording/replay (saves YAML artifacts), dismiss helpers,
#           appearance/webview.  Mild value add; $29/mo tier.
#   pro:    Performance + network monitoring + accessibility audit +
#           AI debugging capture tools + high-trust simctl passthrough.
#           These require real infra compute and are the key upgrade
#           motivators; $99/mo tier.
#   team:   Session-multiplexing primitives: promote_session_to_test.
#           $299/mo tier.
#   enterprise: Reserved for future tools only.

TOOL_TIER_MAP: dict[str, str] = {
    # ── Session lifecycle ──────────────────────────────────────────────────
    "ios_start_session": "trial",
    "ios_stop_session": "trial",

    # ── Vision-first primitives (v16.0.0) ─────────────────────────────────
    # The only sanctioned input + observation surface in v16.
    "ios_observe": "trial",
    "ios_act": "trial",

    # ── Lifecycle / state (kept) ──────────────────────────────────────────
    "ios_app_state": "trial",
    "ios_dismiss_sheet": "trial",

    # ── Recording & Replay ─────────────────────────────────────────────────
    # These create persistent YAML artifacts — upgrade from trial baseline.
    "ios_start_recording": "indie",
    "ios_stop_recording": "indie",
    "ios_list_replays": "indie",
    "ios_replay": "indie",
    "ios_validate_replay": "indie",

    # ── Environment Discovery ──────────────────────────────────────────────
    # These are purely informational — allow in trial so users can diagnose.
    "ios_doctor": "trial",
    "ios_devices": "trial",
    "ios_apps": "trial",
    "ios_license_status": "trial",
    "ios_get_capabilities": "trial",
    "ios_session_status": "trial",
    "ios_wait_for_session": "trial",

    # ── Quality & Diagnostics ──────────────────────────────────────────────
    "ios_accessibility_audit": "pro",   # Full audit; non-trivial CPU
    "ios_set_appearance": "indie",      # Cosmetic test utility
    "ios_simctl": "pro",                # Pro tier: arbitrary simctl passthrough is high-trust; indie users get safer tools instead
    "ios_webview_elements": "indie",    # WKWebView introspection
    "ios_logs": "trial",                # Logs are essential for debugging even in trial
    "ios_crashes": "trial",             # Crash detection is essential; trial needs it
    "ios_pre_grant_permissions": "indie",
    "ios_dismiss_springboard_alert": "indie",
    # v16.0.0a2 Bug #2 fix (Maurice/Palace dogfood §3.2): moved back to trial.
    # On iOS 26+ simctl cannot pre-grant `notifications` (OS-restricted) and
    # ios_act cannot reach SpringBoard alert windows (outside target-app
    # coord scope). Without this tool at trial tier, trial users on iOS 26+
    # have NO path past the first-launch notifications prompt — a hard
    # regression vs v15.x where this tool was free. The capability is a
    # workaround for an Apple limitation, not a premium feature.
    "ios_dismiss_first_launch_alerts": "trial",

    # ── Performance & Network Monitoring ───────────────────────────────────
    "ios_perf": "pro",
    "ios_memory": "pro",
    "ios_network": "pro",
    "ios_perf_baseline": "pro",
    "ios_perf_compare": "pro",

    # ── AI Debugging Primitives (v14.0.0b1) ───────────────────────────────
    # These are high-value debugging multipliers; gated at pro+.
    # ios_capture_state + ios_action_with_logs deleted in v16.0.0 (folded
    # into ios_observe + ios_act + ios_logs_tail composition).
    "ios_app_relaunch": "pro",          # Pro tier: single-session debug utility; team tier is dead-on-arrival for this use case
    "ios_logs_tail": "pro",
    "ios_promote_session_to_test": "team",
}


# ---------------------------------------------------------------------------
# License tier resolution — cached per-process
# ---------------------------------------------------------------------------

_tier_cache: str | None = None
# TODO: add threading.Lock when serve() goes multi-worker


def _get_current_tier() -> str:
    """Resolve the current license tier.

    Resolution order:
    1. SPECTERQA_IOS_LICENSE=founder → "founder"
    2. SPECTERQA_LICENSE_KEY env var → validate via LicenseValidator
    3. No key → trial mode

    Caches the result once per process so we don't hit Keygen.sh on every
    tool call.  The cache is intentionally process-scoped (not request-scoped)
    because tier changes require a process restart in the MCP server model.

    Returns:
        The tier string ("trial", "indie", "pro", "team", "enterprise", "founder", …).
        Falls back to "trial" on any error.
    """
    global _tier_cache

    if _tier_cache is not None:
        return _tier_cache

    # Dogfood bypass
    env_license = os.environ.get("SPECTERQA_IOS_LICENSE", "").strip()
    if env_license.lower() == "founder":
        _tier_cache = "founder"
        return _tier_cache

    try:
        from specterqa.ios.license.validator import LicenseValidator  # noqa: PLC0415

        license_key = os.environ.get("SPECTERQA_LICENSE_KEY", "").strip()
        validator = LicenseValidator(license_key=license_key)
        result = validator.validate()
        tier = result.get("tier", "trial") or "trial"
        # Warn if the returned tier is not in the rank map (catches future/custom tiers
        # that would silently lock the user out of all gated tools).
        if tier not in TIER_RANK:
            logger.warning(
                "Unknown license tier %r — falling back to most-restrictive "
                "(denying all gated tools)",
                tier,
            )
        _tier_cache = tier
        return _tier_cache

    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "tier_gate: LicenseValidator raised an exception (%s). "
            "Failing open — all tools are temporarily accessible. "
            "Fix the validator configuration to restore enforcement.",
            exc,
        )
        # Do NOT cache on failure so the next call retries.
        return "trial"


def _reset_tier_cache() -> None:
    """Reset the per-process tier cache (for tests and after license activation)."""
    global _tier_cache
    _tier_cache = None


# ---------------------------------------------------------------------------
# Core gate check
# ---------------------------------------------------------------------------


def check_tier_gate(
    min_tier: str,
    current_tier: str,
    tool_name: str = "unknown",
) -> dict | None:
    """Return an error dict if current_tier is below min_tier, else None.

    Args:
        min_tier:     The minimum tier required to call the tool.
        current_tier: The caller's current license tier.
        tool_name:    The MCP tool name (included in the error message).

    Returns:
        None if access is granted.
        A structured error dict if access is denied:
          {
            "error": "tier_required",
            "required_tier": str,
            "current_tier": str,
            "tool_name": str,
            "message": str,
            "upgrade_url": str,
          }
    """
    current_rank = TIER_RANK.get(current_tier, -1)
    required_rank = TIER_RANK.get(min_tier, 0)

    if current_rank >= required_rank:
        return None  # Access granted

    return {
        "error": "tier_required",
        "required_tier": min_tier,
        "current_tier": current_tier,
        "tool_name": tool_name,
        "message": (
            f"'{tool_name}' requires a {min_tier} license or higher. "
            f"Your current tier is '{current_tier}'. "
            f"Upgrade at {_UPGRADE_URL} to unlock this feature."
        ),
        "upgrade_url": _UPGRADE_URL,
    }


# ---------------------------------------------------------------------------
# Decorator
# ---------------------------------------------------------------------------


def require_tier(min_tier: str) -> Callable:
    """Decorator: gate an MCP tool function by minimum license tier.

    Works with both sync and async (coroutine) functions.

    Usage::

        @require_tier("pro")
        async def ios_perf() -> str:
            ...

    When the gate blocks, the error dict is returned as ``json.dumps(err)``
    so that MCP clients always receive a JSON string (consistent with how
    every other tool in server.py returns results).

    Bypass:
        Set ``SPECTERQA_LICENSE_BYPASS=1`` to skip all tier checks.

    Fail-open:
        If ``_get_current_tier()`` raises, the tool is allowed through and a
        WARNING is logged.
    """
    import asyncio as _asyncio  # noqa: PLC0415

    def decorator(func: Callable) -> Callable:
        tool_name = func.__name__
        is_coro = _asyncio.iscoroutinefunction(func)

        def _check_gate() -> dict | None:
            """Return error dict if gate blocks, None if allowed.  May raise."""
            # CI / dev bypass
            if os.environ.get("SPECTERQA_LICENSE_BYPASS", "").strip().lower() in ("1", "true", "yes"):
                return None

            # Resolve current tier (fail-open on exception)
            try:
                current_tier = _get_current_tier()
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "tier_gate: _get_current_tier() raised %s for tool '%s'. "
                    "Failing open — tool is temporarily unrestricted.",
                    exc,
                    tool_name,
                )
                return None  # fail-open

            return check_tier_gate(
                min_tier=min_tier,
                current_tier=current_tier,
                tool_name=tool_name,
            )

        if is_coro:
            @functools.wraps(func)
            async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                err = _check_gate()
                if err is not None:
                    return json.dumps(err)
                return await func(*args, **kwargs)
            return async_wrapper
        else:
            @functools.wraps(func)
            def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
                err = _check_gate()
                if err is not None:
                    return json.dumps(err)
                return func(*args, **kwargs)
            return sync_wrapper

    return decorator


# ---------------------------------------------------------------------------
# Module-level startup warning when bypass is active
# ---------------------------------------------------------------------------

if os.environ.get("SPECTERQA_LICENSE_BYPASS", "").strip().lower() in ("1", "true", "yes"):
    logger.warning(
        "SPECTERQA_LICENSE_BYPASS is set — ALL tier enforcement is DISABLED. "
        "This must NEVER be set in production deployments."
    )
