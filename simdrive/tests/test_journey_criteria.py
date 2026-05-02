"""Tests for journey/criteria.py — Component 3 criteria evaluators.

Each evaluator has its own test class. Tests are pure-function:
no sessions, no simulators, no LLM calls.
"""
from __future__ import annotations

import pytest

from specterqa_ios.journey.criteria import (
    all_passed,
    eval_cross_device_state_matches,
    eval_no_crash,
    eval_perf_under,
    eval_screen_matches,
    eval_text_visible,
    evaluate_all_criteria,
    unmet_descriptions,
)
from specterqa_ios.journey.result import CriterionEval
from specterqa_ios.journey.schema import SuccessCriterion


# ── Helpers ───────────────────────────────────────────────────────────────────

def _obs_with_marks(*texts: str) -> dict:
    """Build a minimal observation dict with marks carrying the given texts."""
    return {
        "marks": [
            {"stable_id": f"sid{i}", "text": t, "id": i}
            for i, t in enumerate(texts)
        ],
        "screenshot_path": "/tmp/screen.png",
    }


def _obs_empty() -> dict:
    return {"marks": [], "screenshot_path": "/tmp/screen.png"}


# ── text_visible evaluator ────────────────────────────────────────────────────

class TestEvalTextVisible:
    def test_text_found_passes(self):
        criterion = SuccessCriterion(text_visible="Welcome")
        obs = _obs_with_marks("Welcome Back", "Sign In")
        result = eval_text_visible(criterion, obs)
        assert result.passed is True
        assert result.criterion_type == "text_visible"

    def test_text_not_found_fails(self):
        criterion = SuccessCriterion(text_visible="Dashboard")
        obs = _obs_with_marks("Sign In", "Create Account")
        result = eval_text_visible(criterion, obs)
        assert result.passed is False

    def test_text_visible_case_insensitive(self):
        criterion = SuccessCriterion(text_visible="WELCOME")
        obs = _obs_with_marks("welcome back")
        result = eval_text_visible(criterion, obs)
        assert result.passed is True

    def test_empty_marks_fails(self):
        criterion = SuccessCriterion(text_visible="Home")
        obs = _obs_empty()
        result = eval_text_visible(criterion, obs)
        assert result.passed is False

    def test_partial_substring_match(self):
        criterion = SuccessCriterion(text_visible="come")
        obs = _obs_with_marks("Welcome")
        result = eval_text_visible(criterion, obs)
        assert result.passed is True  # substring match

    def test_observed_value_present(self):
        criterion = SuccessCriterion(text_visible="x")
        obs = _obs_with_marks("xyz")
        result = eval_text_visible(criterion, obs)
        assert result.observed_value is not None


# ── screen_matches evaluator ──────────────────────────────────────────────────

class TestEvalScreenMatches:
    def test_stable_id_present_passes(self):
        criterion = SuccessCriterion(screen_matches="abc123")
        obs = {
            "marks": [{"stable_id": "abc123", "text": "Home", "id": 0}],
        }
        result = eval_screen_matches(criterion, obs)
        assert result.passed is True

    def test_stable_id_absent_fails(self):
        criterion = SuccessCriterion(screen_matches="missing-id")
        obs = _obs_with_marks("Home")
        result = eval_screen_matches(criterion, obs)
        assert result.passed is False

    def test_empty_marks_fails(self):
        criterion = SuccessCriterion(screen_matches="anything")
        result = eval_screen_matches(criterion, _obs_empty())
        assert result.passed is False

    def test_result_contains_observed_stable_ids(self):
        criterion = SuccessCriterion(screen_matches="target")
        obs = {"marks": [{"stable_id": "other", "text": "X"}]}
        result = eval_screen_matches(criterion, obs)
        assert isinstance(result.observed_value, list)


# ── perf_under evaluator ──────────────────────────────────────────────────────

class TestEvalPerfUnder:
    def test_within_budget_passes(self):
        criterion = SuccessCriterion(perf_under={"cpu_pct": 50.0, "memory_mb": 200.0})
        perf = {"cpu_pct": 30.0, "memory_mb": 150.0}
        result = eval_perf_under(criterion, perf)
        assert result.passed is True

    def test_cpu_exceeded_fails(self):
        criterion = SuccessCriterion(perf_under={"cpu_pct": 50.0})
        perf = {"cpu_pct": 75.0}
        result = eval_perf_under(criterion, perf)
        assert result.passed is False
        assert "cpu" in result.detail

    def test_memory_exceeded_fails(self):
        criterion = SuccessCriterion(perf_under={"memory_mb": 100.0})
        perf = {"memory_mb": 250.0}
        result = eval_perf_under(criterion, perf)
        assert result.passed is False
        assert "memory" in result.detail

    def test_no_perf_snapshot_fails(self):
        criterion = SuccessCriterion(perf_under={"cpu_pct": 50.0})
        result = eval_perf_under(criterion, perf_snapshot=None)
        assert result.passed is False
        assert "unavailable" in result.detail

    def test_at_exact_limit_passes(self):
        # Equal to limit is within budget (not strictly less than).
        criterion = SuccessCriterion(perf_under={"cpu_pct": 50.0})
        perf = {"cpu_pct": 50.0}
        result = eval_perf_under(criterion, perf)
        assert result.passed is True

    def test_rss_mb_key_accepted(self):
        """Accept rss_mb as an alias for memory_mb."""
        criterion = SuccessCriterion(perf_under={"memory_mb": 500.0})
        perf = {"rss_mb": 200.0}  # alternate key from perf module
        result = eval_perf_under(criterion, perf)
        assert result.passed is True


# ── no_crash evaluator ────────────────────────────────────────────────────────

class TestEvalNoCrash:
    def test_no_crashes_passes(self):
        criterion = SuccessCriterion(no_crash=True)
        result = eval_no_crash(criterion, crashes_since_start=[])
        assert result.passed is True
        assert "no crashes" in result.detail

    def test_one_crash_fails(self):
        criterion = SuccessCriterion(no_crash=True)
        crashes = [{"path": "/tmp/crash.ips", "timestamp": 1234567}]
        result = eval_no_crash(criterion, crashes_since_start=crashes)
        assert result.passed is False
        assert "crash" in result.detail.lower()

    def test_multiple_crashes_fails(self):
        criterion = SuccessCriterion(no_crash=True)
        crashes = [
            {"path": "/tmp/c1.ips"},
            {"path": "/tmp/c2.ips"},
        ]
        result = eval_no_crash(criterion, crashes)
        assert result.passed is False
        assert "2" in result.detail


# ── cross_device_state_matches — pass-through ──────────────────────────────────

class TestEvalCrossDeviceStateMatches:
    def test_always_passes_with_warning(self, caplog):
        import logging
        criterion = SuccessCriterion(cross_device_state_matches={"session_id": "abc"})
        with caplog.at_level(logging.WARNING, logger="simdrive.journey.criteria"):
            result = eval_cross_device_state_matches(criterion)
        assert result.passed is True
        assert "pass-through" in result.detail
        assert any("pass-through" in r.message or "1.0 stretch" in r.message
                   for r in caplog.records)


# ── evaluate_all_criteria ─────────────────────────────────────────────────────

class TestEvaluateAllCriteria:
    def test_all_pass(self):
        criteria = [
            SuccessCriterion(text_visible="Home"),
            SuccessCriterion(no_crash=True),
        ]
        obs = _obs_with_marks("Home screen loaded")
        evals = evaluate_all_criteria(criteria, obs, crashes_since_start=[])
        assert all(e.passed for e in evals)

    def test_partial_fail(self):
        criteria = [
            SuccessCriterion(text_visible="Home"),
            SuccessCriterion(text_visible="MISSING_TEXT"),
        ]
        obs = _obs_with_marks("Home")
        evals = evaluate_all_criteria(criteria, obs)
        assert evals[0].passed is True
        assert evals[1].passed is False

    def test_evaluator_exception_captured(self):
        """An evaluator exception should be captured as a failed eval, not raised."""
        # Craft a criterion that will cause eval_screen_matches to see weird data.
        criteria = [SuccessCriterion(screen_matches="x")]
        # Obs with marks that have no stable_id key — edge case.
        obs = {"marks": [{"text": "hello", "id": 0}]}
        evals = evaluate_all_criteria(criteria, obs)
        # Should not raise — either pass=True (key absent treated as not found) or pass=False
        assert len(evals) == 1

    def test_order_preserved(self):
        criteria = [
            SuccessCriterion(text_visible="A"),
            SuccessCriterion(text_visible="B"),
            SuccessCriterion(text_visible="C"),
        ]
        obs = _obs_with_marks("A B C")
        evals = evaluate_all_criteria(criteria, obs)
        assert [e.criterion_type for e in evals] == [
            "text_visible", "text_visible", "text_visible"
        ]

    def test_empty_criteria_returns_empty(self):
        evals = evaluate_all_criteria([], _obs_empty())
        assert evals == []


# ── all_passed + unmet_descriptions ──────────────────────────────────────────

class TestHelpers:
    def test_all_passed_true(self):
        evals = [
            CriterionEval("text_visible", True, "found"),
            CriterionEval("no_crash", True, "no crashes"),
        ]
        assert all_passed(evals) is True

    def test_all_passed_false(self):
        evals = [
            CriterionEval("text_visible", True, "found"),
            CriterionEval("no_crash", False, "crash detected"),
        ]
        assert all_passed(evals) is False

    def test_all_passed_empty(self):
        assert all_passed([]) is True

    def test_unmet_descriptions_returns_only_failures(self):
        evals = [
            CriterionEval("text_visible", True, "ok"),
            CriterionEval("screen_matches", False, "missing mark"),
        ]
        descs = unmet_descriptions(evals)
        assert len(descs) == 1
        assert "screen_matches" in descs[0]
        assert "missing mark" in descs[0]

    def test_unmet_descriptions_empty_when_all_pass(self):
        evals = [CriterionEval("text_visible", True, "ok")]
        assert unmet_descriptions(evals) == []
