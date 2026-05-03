"""Success-criteria evaluators — Component 3.

One evaluator function per criterion type.  Each takes an observation dict
(from tool_observe / obs.to_dict()), an optional perf snapshot dict, and the
criterion spec; returns a CriterionEval.

Evaluators are pure functions — no side effects, easy to test in isolation.
The runner calls evaluate_all_criteria() at every step.
"""
from __future__ import annotations

import logging
from typing import Optional

from .result import CriterionEval
from .schema import SuccessCriterion

log = logging.getLogger("simdrive.journey.criteria")


# ── Individual evaluators ─────────────────────────────────────────────────────


def eval_text_visible(criterion: SuccessCriterion, obs: dict) -> CriterionEval:
    """Check that criterion.text_visible appears as a substring in any mark's text.

    Matching is case-insensitive substring search across all mark texts in the
    observation, mimicking what a human eye would do.
    """
    target = criterion.text_visible
    if target is None:
        raise ValueError("eval_text_visible called but criterion.text_visible is None — caller must pre-filter")

    marks = obs.get("marks", [])
    # Gather all visible text tokens from the SoM marks.
    all_text = " ".join(m.get("text", "") for m in marks).lower()
    target_lower = target.lower()
    found = target_lower in all_text

    return CriterionEval(
        criterion_type="text_visible",
        passed=found,
        detail=(
            f"text {target!r} {'found' if found else 'not found'} in screen marks"
        ),
        observed_value=all_text[:200] if all_text else None,
    )


def eval_screen_matches(criterion: SuccessCriterion, obs: dict) -> CriterionEval:
    """Check that criterion.screen_matches stable_id appears in the current marks."""
    stable_id = criterion.screen_matches
    if stable_id is None:
        raise ValueError("eval_screen_matches called but criterion.screen_matches is None — caller must pre-filter")

    marks = obs.get("marks", [])
    found = any(m.get("stable_id") == stable_id for m in marks)

    return CriterionEval(
        criterion_type="screen_matches",
        passed=found,
        detail=(
            f"stable_id {stable_id!r} {'present' if found else 'absent'} in current marks"
        ),
        observed_value=[m.get("stable_id") for m in marks] if marks else [],
    )


def eval_perf_under(
    criterion: SuccessCriterion,
    perf_snapshot: Optional[dict],
) -> CriterionEval:
    """Check that current performance is within the specified budget.

    perf_snapshot: the dict returned by tool_perf or perf.snapshot() —
    expected keys: cpu_pct (float), memory_mb (float).
    """
    budget = criterion.perf_under
    if budget is None:
        raise ValueError("eval_perf_under called but criterion.perf_under is None — caller must pre-filter")

    if perf_snapshot is None:
        return CriterionEval(
            criterion_type="perf_under",
            passed=False,
            detail="perf snapshot unavailable — cannot evaluate perf_under criterion",
            observed_value=None,
        )

    violations: list[str] = []
    cpu_limit = budget.get("cpu_pct")
    mem_limit = budget.get("memory_mb")

    cpu_actual = perf_snapshot.get("cpu_pct")
    mem_actual = perf_snapshot.get("memory_mb") or perf_snapshot.get("rss_mb")

    if cpu_limit is not None and cpu_actual is not None:
        if float(cpu_actual) > float(cpu_limit):
            violations.append(
                f"cpu {cpu_actual:.1f}% > limit {cpu_limit:.1f}%"
            )

    if mem_limit is not None and mem_actual is not None:
        if float(mem_actual) > float(mem_limit):
            violations.append(
                f"memory {mem_actual:.1f}MB > limit {mem_limit:.1f}MB"
            )

    passed = len(violations) == 0
    return CriterionEval(
        criterion_type="perf_under",
        passed=passed,
        detail=(
            "all perf metrics within budget"
            if passed
            else "; ".join(violations)
        ),
        observed_value={"cpu_pct": cpu_actual, "memory_mb": mem_actual},
    )


def eval_no_crash(
    criterion: SuccessCriterion,
    crashes_since_start: list[dict],
) -> CriterionEval:
    """Check that no crash reports have been generated since the journey started."""
    if criterion.no_crash is not True:
        raise ValueError("eval_no_crash called but criterion.no_crash is not True — caller must pre-filter")

    crashed = len(crashes_since_start) > 0
    return CriterionEval(
        criterion_type="no_crash",
        passed=not crashed,
        detail=(
            "no crashes detected"
            if not crashed
            else f"{len(crashes_since_start)} crash(es): {crashes_since_start[0].get('path', '?')}"
        ),
        observed_value=crashes_since_start[:3] if crashes_since_start else [],
    )


def eval_cross_device_state_matches(
    criterion: SuccessCriterion,
) -> CriterionEval:
    """Pass-through evaluator for the 1.0 stretch criterion.

    cross_device_state_matches is not implemented in 1.0.  When present, we
    warn in the agent_trace but never fail-closed — that would silently break
    any journey that includes this criterion before the feature ships.
    """
    # Explicitly a pass-through; log a warning so it's discoverable.
    log.warning(
        "cross_device_state_matches criterion is not implemented in SimDrive 1.0 "
        "— treating as passed (pass-through). Will be implemented in 1.1+."
    )
    return CriterionEval(
        criterion_type="cross_device_state_matches",
        passed=True,
        detail="cross_device_state_matches is a 1.0 stretch — pass-through (not evaluated)",
        observed_value=None,
    )


# ── Batch evaluation ──────────────────────────────────────────────────────────


def evaluate_all_criteria(
    criteria: list[SuccessCriterion],
    obs: dict,
    perf_snapshot: Optional[dict] = None,
    crashes_since_start: Optional[list[dict]] = None,
) -> list[CriterionEval]:
    """Evaluate every criterion in the journey's success_criteria list.

    Returns a CriterionEval for each criterion in the same order.
    Never raises — evaluator errors are captured as failed CriterionEval entries.
    """
    results: list[CriterionEval] = []
    crashes = crashes_since_start or []

    for criterion in criteria:
        try:
            if criterion.text_visible is not None:
                results.append(eval_text_visible(criterion, obs))
            elif criterion.screen_matches is not None:
                results.append(eval_screen_matches(criterion, obs))
            elif criterion.perf_under is not None:
                results.append(eval_perf_under(criterion, perf_snapshot))
            elif criterion.no_crash is True:
                results.append(eval_no_crash(criterion, crashes))
            elif criterion.cross_device_state_matches is not None:
                results.append(eval_cross_device_state_matches(criterion))
            else:
                # Should never happen — SuccessCriterion validates at least one field.
                results.append(
                    CriterionEval(
                        criterion_type="unknown",
                        passed=False,
                        detail="criterion has no evaluable fields",
                    )
                )
        except Exception as exc:
            results.append(
                CriterionEval(
                    criterion_type="error",
                    passed=False,
                    detail=f"evaluator raised: {exc}",
                )
            )

    return results


def all_passed(evals: list[CriterionEval]) -> bool:
    """True when every CriterionEval in the list has passed=True."""
    return all(e.passed for e in evals)


def unmet_descriptions(evals: list[CriterionEval]) -> list[str]:
    """Return human-readable descriptions of every unmet criterion."""
    return [f"{e.criterion_type}: {e.detail}" for e in evals if not e.passed]
