"""Journey integration for host-AX: perform_accessibility_action step +
announcement_heard success criterion. Pure-function / patched-tool tests."""
from __future__ import annotations

from unittest.mock import patch

import pytest

from simdrive.journey.criteria import eval_announcement_heard, evaluate_all_criteria
from simdrive.journey.runner import StepDecision, _dispatch_action
from simdrive.journey.schema import SuccessCriterion


# ── announcement_heard criterion ─────────────────────────────────────────────


def _obs_with_announcements(*texts: str) -> dict:
    return {"marks": [], "announcements": [{"text": t} for t in texts]}


def test_announcement_heard_found_substring():
    crit = SuccessCriterion(announcement_heard="page 12")
    ev = eval_announcement_heard(crit, _obs_with_announcements("Section 2, page 12, 34%"))
    assert ev.passed is True
    assert ev.criterion_type == "announcement_heard"


def test_announcement_heard_not_found():
    crit = SuccessCriterion(announcement_heard="page 99")
    ev = eval_announcement_heard(crit, _obs_with_announcements("Section 1, page 1, 5%"))
    assert ev.passed is False


def test_announcement_heard_empty_obs():
    crit = SuccessCriterion(announcement_heard="anything")
    ev = eval_announcement_heard(crit, {"marks": []})  # no announcements key
    assert ev.passed is False


def test_evaluate_all_criteria_dispatches_announcement():
    crits = [SuccessCriterion(announcement_heard="Page 2")]
    evals = evaluate_all_criteria(crits, obs=_obs_with_announcements("Page 2"))
    assert len(evals) == 1
    assert evals[0].criterion_type == "announcement_heard"
    assert evals[0].passed is True


def test_announcement_heard_is_a_valid_sole_criterion():
    # Must not trip the "at least one field" validator.
    crit = SuccessCriterion(announcement_heard="x")
    assert crit.announcement_heard == "x"


# ── perform_accessibility_action step dispatch ───────────────────────────────


def test_dispatch_routes_perform_accessibility_action():
    decision = StepDecision(
        tool="perform_accessibility_action",
        args={"name": "Where am I?"},
        rationale="assert position",
        confidence=0.9,
    )
    with patch(
        "simdrive.journey.runner.tool_perform_accessibility_action",
        return_value={"ok": True},
    ) as fn:
        _dispatch_action(decision, "sess-1")
    fn.assert_called_once()
    passed = fn.call_args[0][0]
    assert passed["name"] == "Where am I?"
    assert passed["session_id"] == "sess-1"


def test_step_decision_accepts_new_tool_literal():
    # Constructing with the new tool names must not raise.
    d = StepDecision(tool="perform_accessibility_action", args={}, rationale="r", confidence=0.5)
    assert d.tool == "perform_accessibility_action"
    d2 = StepDecision(tool="set_text", args={"text": "42"}, rationale="r", confidence=0.5)
    assert d2.tool == "set_text"


def test_dispatch_routes_set_text():
    decision = StepDecision(
        tool="set_text", args={"text": "42"}, rationale="enter page", confidence=0.9
    )
    with patch("simdrive.journey.runner.tool_set_text", return_value={"ok": True}) as fn:
        _dispatch_action(decision, "sess-2")
    fn.assert_called_once()
    passed = fn.call_args[0][0]
    assert passed["text"] == "42"
    assert passed["session_id"] == "sess-2"
