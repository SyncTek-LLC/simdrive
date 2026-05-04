"""Journey-specific error code constructors.

All codes here must be merged into the global errors.py by Atlas during
integration. Each constructor mirrors the pattern in errors.py — returns a
SimdriveError with a stable code string and a human-readable message that
ends with "Recovery: ..." for every actionable failure.

New codes introduced (list for Atlas integration):
  journey_schema_invalid
  journey_persona_not_found
  journey_schema_version_unsupported
  journey_device_selector_missing
  persona_schema_invalid
  persona_schema_version_unsupported
  journey_budget_exceeded
  claude_call_failed
  claude_cost_cap_hit
  act_tool_failed
  success_criterion_unevaluable
  ci_no_journeys_matched
  ci_invalid_journey
"""
from __future__ import annotations

from typing import Any

# Import from the global errors module so SimdriveError is the same class
# everywhere — callers can still catch simdrive.errors.SimdriveError.
from simdrive.errors import SimdriveError


# ── Component 1 — Journey schema ────────────────────────────────────────────


def journey_schema_invalid(path: str, reason: str) -> SimdriveError:
    return SimdriveError(
        code="journey_schema_invalid",
        message=(
            f"journey file {path!r} failed schema validation: {reason}. "
            "Recovery: run `simdrive validate --journeys-dir <dir>` to see all errors."
        ),
        details={"path": path, "reason": reason},
    )


def journey_persona_not_found(persona_slug: str, personas_dir: str) -> SimdriveError:
    return SimdriveError(
        code="journey_persona_not_found",
        message=(
            f"persona slug {persona_slug!r} not found in {personas_dir!r}. "
            "Recovery: create `.simdrive/personas/{persona_slug}.yaml` or update the "
            "`persona:` field in your journey file."
        ),
        details={"persona_slug": persona_slug, "personas_dir": personas_dir},
    )


def journey_schema_version_unsupported(version: Any, supported: int = 1) -> SimdriveError:
    return SimdriveError(
        code="journey_schema_version_unsupported",
        message=(
            f"journey schema_version {version!r} is not supported "
            f"(this build supports version {supported}). "
            "Recovery: update `schema_version:` to 1 in your journey YAML."
        ),
        details={"version": version, "supported": supported},
    )


def journey_device_selector_missing(journey_name: str) -> SimdriveError:
    return SimdriveError(
        code="journey_device_selector_missing",
        message=(
            f"journey {journey_name!r} sets `target: device` but omits `device_selector`. "
            "Recovery: add a `device_selector:` block with at least `udid` or `name`."
        ),
        details={"journey_name": journey_name},
    )


# ── Component 2 — Persona schema ────────────────────────────────────────────


def persona_schema_invalid(path: str, reason: str) -> SimdriveError:
    return SimdriveError(
        code="persona_schema_invalid",
        message=(
            f"persona file {path!r} failed schema validation: {reason}. "
            "Recovery: run `simdrive validate --personas-dir <dir>` to see all errors."
        ),
        details={"path": path, "reason": reason},
    )


def persona_schema_version_unsupported(version: Any, supported: int = 1) -> SimdriveError:
    return SimdriveError(
        code="persona_schema_version_unsupported",
        message=(
            f"persona schema_version {version!r} is not supported "
            f"(this build supports version {supported}). "
            "Recovery: update `schema_version:` to 1 in your persona YAML."
        ),
        details={"version": version, "supported": supported},
    )


# ── Component 3 — Runner ────────────────────────────────────────────────────


def journey_budget_exceeded(journey_name: str, steps: int, seconds: float, llm_calls: int) -> SimdriveError:
    return SimdriveError(
        code="journey_budget_exceeded",
        message=(
            f"journey {journey_name!r} exceeded budget after {steps} steps, "
            f"{seconds:.1f}s, {llm_calls} LLM calls. "
            "Recovery: increase `budget.max_steps`/`max_seconds`/`max_llm_calls` in the journey YAML, "
            "or simplify the journey goals."
        ),
        details={"journey_name": journey_name, "steps": steps,
                 "seconds": seconds, "llm_calls": llm_calls},
    )


def claude_call_failed(reason: str, attempt: int) -> SimdriveError:
    return SimdriveError(
        code="claude_call_failed",
        message=(
            f"Claude API call failed (attempt {attempt}): {reason}. "
            "Recovery: check network connectivity and ANTHROPIC_API_KEY environment variable."
        ),
        details={"reason": reason, "attempt": attempt},
    )


def claude_cost_cap_hit(cost_usd: float, cap_usd: float) -> SimdriveError:
    return SimdriveError(
        code="claude_cost_cap_hit",
        message=(
            f"Journey aborted: LLM cost ${cost_usd:.4f} reached the ${cap_usd:.2f} cap. "
            "Recovery: set SIMDRIVE_COST_CAP_USD env var to a higher value, "
            "or reduce budget.max_llm_calls in your journey."
        ),
        details={"cost_usd": cost_usd, "cap_usd": cap_usd},
    )


def act_tool_failed(tool_name: str, inner_code: str, inner_message: str) -> SimdriveError:
    return SimdriveError(
        code="act_tool_failed",
        message=(
            f"Action tool {tool_name!r} failed during journey execution: {inner_message}. "
            "Recovery: check the journey step's target exists on screen (use `observe` to verify)."
        ),
        details={"tool_name": tool_name, "inner_code": inner_code, "inner_message": inner_message},
    )


def success_criterion_unevaluable(criterion_type: str, reason: str) -> SimdriveError:
    return SimdriveError(
        code="success_criterion_unevaluable",
        message=(
            f"Success criterion {criterion_type!r} could not be evaluated: {reason}. "
            "Recovery: ensure the required data (observe output, perf snapshot, etc.) "
            "is available before evaluating this criterion type."
        ),
        details={"criterion_type": criterion_type, "reason": reason},
    )


# ── Component 8 — CI orchestrator ───────────────────────────────────────────


def ci_no_journeys_matched(journeys_dir: str, tag_filter: list[str]) -> SimdriveError:
    return SimdriveError(
        code="ci_no_journeys_matched",
        message=(
            f"No journey files found in {journeys_dir!r} "
            f"matching tags {tag_filter}. "
            "Recovery: run `simdrive validate` to list discovered journeys, "
            "or adjust `--tag` / `--journeys` filter."
        ),
        details={"journeys_dir": journeys_dir, "tag_filter": tag_filter},
    )


def ci_invalid_journey(path: str, reason: str) -> SimdriveError:
    return SimdriveError(
        code="ci_invalid_journey",
        message=(
            f"CI run aborted: journey {path!r} failed validation: {reason}. "
            "Recovery: fix the journey file or remove it from the journeys directory."
        ),
        details={"path": path, "reason": reason},
    )
