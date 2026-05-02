"""Tests for journey/runner.py and journey/prompt.py — Component 3.

All tests use a fake LLMClient — no real Claude API calls.
No real simulators required.

Tests cover:
  - Prompt assembly determinism (prompt.py)
  - Budget enforcement (max_steps, max_seconds, max_llm_calls)
  - Happy-path run: LLM → done after criteria met
  - All success-criteria met → outcome=passed
  - LLM returns "done" → outcome=passed
  - LLM returns "fail" → outcome=failed
  - Crash detected → outcome=crashed
  - Act tool failure → outcome=error
  - Cost cap enforcement
  - RunResult serialisation
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional
from unittest.mock import MagicMock, patch, call

import pytest

from specterqa_ios.journey.persona import Persona, AccessibilityNeeds
from specterqa_ios.journey.prompt import assemble_system_prompt, assemble_user_prompt
from specterqa_ios.journey.result import RunResult, StepRecord, CriterionEval
from specterqa_ios.journey.runner import LLMClient, StepDecision, run_journey
from specterqa_ios.journey.schema import Budget, Journey, SuccessCriterion


# ── Fake helpers ──────────────────────────────────────────────────────────────


def _make_journey(
    *,
    name: str = "test-journey",
    goals: Optional[list[str]] = None,
    success_criteria: Optional[list[SuccessCriterion]] = None,
    budget: Optional[Budget] = None,
) -> Journey:
    return Journey(
        schema_version=1,
        name=name,
        persona="test-user",
        target="simulator",
        goals=goals or ["Navigate to home"],
        success_criteria=success_criteria or [SuccessCriterion(text_visible="Home")],
        budget=budget or Budget(max_steps=10, max_seconds=60, max_llm_calls=10),
    )


def _make_persona(slug: str = "test-user") -> Persona:
    return Persona(
        schema_version=1,
        slug=slug,
        name="Test User",
        role="A test automation persona",
    )


def _make_session(session_id: str = "test-session-001") -> MagicMock:
    s = MagicMock()
    s.session_id = session_id
    s.started_at = time.time()
    return s


class FakeLLMClient:
    """Scripted LLM client that returns a predetermined sequence of decisions."""

    def __init__(self, decisions: list[StepDecision]):
        self._decisions = list(decisions)
        self._idx = 0
        self._cost = 0.0

    def call(
        self,
        system_prompt: str,
        user_prompt: str,
        screenshot_path: Optional[str],
    ) -> StepDecision:
        if self._idx >= len(self._decisions):
            raise RuntimeError("FakeLLMClient ran out of scripted decisions")
        d = self._decisions[self._idx]
        self._idx += 1
        self._cost += 0.004
        return d

    @property
    def cost_usd(self) -> float:
        return self._cost


# Fake observation dict — mimics tool_observe output with criteria-passing text.
_OBS_WITH_HOME = {
    "marks": [{"stable_id": "sid001", "text": "Home", "id": 0}],
    "screenshot_path": "/tmp/obs.png",
}

_OBS_EMPTY = {
    "marks": [],
    "screenshot_path": "/tmp/obs_empty.png",
}


# ── Prompt assembly tests (prompt.py) ─────────────────────────────────────────

class TestAssembleSystemPrompt:
    def test_deterministic_output(self):
        """Same inputs always produce the same system prompt."""
        journey = _make_journey()
        persona = _make_persona()
        p1 = assemble_system_prompt(journey, persona)
        p2 = assemble_system_prompt(journey, persona)
        assert p1 == p2

    def test_contains_persona_name(self):
        journey = _make_journey()
        persona = _make_persona()
        prompt = assemble_system_prompt(journey, persona)
        assert "Test User" in prompt

    def test_contains_persona_role(self):
        journey = _make_journey()
        persona = _make_persona()
        prompt = assemble_system_prompt(journey, persona)
        assert "test automation persona" in prompt

    def test_contains_journey_name(self):
        journey = _make_journey(name="onboarding-flow")
        persona = _make_persona()
        prompt = assemble_system_prompt(journey, persona)
        assert "onboarding-flow" in prompt

    def test_contains_journey_goals(self):
        journey = _make_journey(goals=["Tap the sign-in button"])
        persona = _make_persona()
        prompt = assemble_system_prompt(journey, persona)
        assert "Tap the sign-in button" in prompt

    def test_accessibility_block_included_when_needs_set(self):
        persona = Persona(
            schema_version=1,
            slug="accessible-user",
            name="Alice",
            role="Accessibility tester",
            accessibility_needs=AccessibilityNeeds(large_text=True),
        )
        journey = _make_journey()
        prompt = assemble_system_prompt(journey, persona)
        assert "Large text" in prompt
        assert "True" in prompt

    def test_no_accessibility_block_when_no_needs(self):
        persona = _make_persona()  # all defaults = False
        journey = _make_journey()
        prompt = assemble_system_prompt(journey, persona)
        # When no accessibility needs, the block should be absent or empty.
        assert "Large text:" not in prompt

    def test_locale_in_prompt(self):
        persona = Persona(
            schema_version=1,
            slug="fr-user",
            name="Francoise",
            role="French user",
            locale="fr-FR",
        )
        journey = _make_journey()
        prompt = assemble_system_prompt(journey, persona)
        assert "fr-FR" in prompt

    def test_persona_goals_and_frustrations_in_prompt(self):
        persona = Persona(
            schema_version=1,
            slug="u",
            name="U",
            role="A user",
            goals=["Find my books"],
            frustrations=["Slow load times"],
        )
        journey = _make_journey()
        prompt = assemble_system_prompt(journey, persona)
        assert "Find my books" in prompt
        assert "Slow load times" in prompt


class TestAssembleUserPrompt:
    def test_contains_step_number(self):
        result = assemble_user_prompt(
            obs=_OBS_WITH_HOME,
            unmet_criteria=["text_visible: Home not found"],
            recent_steps=[],
            step_idx=3,
            budget_remaining={"steps": 7, "seconds": 45.0, "llm_calls": 7},
        )
        assert "Step 3" in result

    def test_contains_unmet_criteria(self):
        result = assemble_user_prompt(
            obs=_OBS_EMPTY,
            unmet_criteria=["text_visible: Welcome not found"],
            recent_steps=[],
            step_idx=1,
            budget_remaining={"steps": 10, "seconds": 60.0, "llm_calls": 10},
        )
        assert "Welcome not found" in result

    def test_no_unmet_means_done_hint(self):
        result = assemble_user_prompt(
            obs=_OBS_WITH_HOME,
            unmet_criteria=[],
            recent_steps=[],
            step_idx=1,
            budget_remaining={"steps": 10, "seconds": 60.0, "llm_calls": 10},
        )
        assert "done" in result.lower()

    def test_contains_marks_text(self):
        result = assemble_user_prompt(
            obs=_OBS_WITH_HOME,
            unmet_criteria=[],
            recent_steps=[],
            step_idx=1,
            budget_remaining={"steps": 10, "seconds": 60.0, "llm_calls": 10},
        )
        assert "Home" in result

    def test_budget_remaining_shown(self):
        result = assemble_user_prompt(
            obs=_OBS_EMPTY,
            unmet_criteria=[],
            recent_steps=[],
            step_idx=2,
            budget_remaining={"steps": 5, "seconds": 30.0, "llm_calls": 8},
        )
        assert "5" in result  # steps remaining


# ── Runner tests ───────────────────────────────────────────────────────────────

class TestRunJourneyPassed:
    def test_llm_done_decision_gives_passed(self, tmp_path):
        journey = _make_journey()
        persona = _make_persona()
        session = _make_session()

        # LLM immediately says done.
        client = FakeLLMClient([
            StepDecision(tool="done", args={}, rationale="criteria met", confidence=1.0),
        ])

        with (
            patch("specterqa_ios.journey.runner.tool_observe", return_value=_OBS_WITH_HOME),
            patch("specterqa_ios.journey.runner.tool_perf", return_value={"cpu_pct": 10.0}),
            patch("specterqa_ios.journey.runner.tool_crashes", return_value={"crashes": []}),
            patch("specterqa_ios.journey.runner.tool_perf_baseline", return_value={}),
        ):
            result = run_journey(
                journey, persona, session, client,
                artifact_dir_override=tmp_path / "run1",
                _recorder_module=None,
            )

        assert result.outcome == "passed"
        assert result.journey_name == "test-journey"
        assert result.persona_name == "Test User"

    def test_criteria_met_gives_passed_without_done(self, tmp_path):
        """When all criteria are already met at step 0, outcome=passed before any LLM call."""
        journey = _make_journey(
            success_criteria=[SuccessCriterion(text_visible="Home")]
        )
        persona = _make_persona()
        session = _make_session()
        client = FakeLLMClient([])  # Should never be called

        with (
            patch("specterqa_ios.journey.runner.tool_observe", return_value=_OBS_WITH_HOME),
            patch("specterqa_ios.journey.runner.tool_perf", return_value={"cpu_pct": 5.0}),
            patch("specterqa_ios.journey.runner.tool_crashes", return_value={"crashes": []}),
            patch("specterqa_ios.journey.runner.tool_perf_baseline", return_value={}),
        ):
            result = run_journey(
                journey, persona, session, client,
                artifact_dir_override=tmp_path / "run2",
                _recorder_module=None,
            )

        assert result.outcome == "passed"
        assert result.llm_calls == 0


class TestRunJourneyFailed:
    def test_llm_fail_decision_gives_failed(self, tmp_path):
        journey = _make_journey(
            success_criteria=[SuccessCriterion(text_visible="NEVER_VISIBLE")]
        )
        persona = _make_persona()
        session = _make_session()
        client = FakeLLMClient([
            StepDecision(tool="fail", args={}, rationale="app is broken", confidence=0.9),
        ])

        with (
            patch("specterqa_ios.journey.runner.tool_observe", return_value=_OBS_EMPTY),
            patch("specterqa_ios.journey.runner.tool_perf", return_value={}),
            patch("specterqa_ios.journey.runner.tool_crashes", return_value={"crashes": []}),
            patch("specterqa_ios.journey.runner.tool_perf_baseline", return_value={}),
        ):
            result = run_journey(
                journey, persona, session, client,
                artifact_dir_override=tmp_path / "run_fail",
                _recorder_module=None,
            )

        assert result.outcome == "failed"
        assert "app is broken" in (result.failure_reason or "")


class TestRunJourneyBudget:
    def test_max_steps_exceeded(self, tmp_path):
        journey = _make_journey(
            success_criteria=[SuccessCriterion(text_visible="NEVER_VISIBLE")],
            budget=Budget(max_steps=2, max_seconds=300, max_llm_calls=100),
        )
        persona = _make_persona()
        session = _make_session()
        # Provide enough decisions (each tap advances step_idx by 1)
        client = FakeLLMClient([
            StepDecision(tool="tap", args={"x": 100, "y": 200}, rationale="try", confidence=0.8),
            StepDecision(tool="tap", args={"x": 100, "y": 200}, rationale="try", confidence=0.8),
            StepDecision(tool="tap", args={"x": 100, "y": 200}, rationale="try", confidence=0.8),
        ])

        with (
            patch("specterqa_ios.journey.runner.tool_observe", return_value=_OBS_EMPTY),
            patch("specterqa_ios.journey.runner.tool_perf", return_value={}),
            patch("specterqa_ios.journey.runner.tool_crashes", return_value={"crashes": []}),
            patch("specterqa_ios.journey.runner.tool_perf_baseline", return_value={}),
            patch("specterqa_ios.journey.runner.tool_tap", return_value={"ok": True}),
        ):
            result = run_journey(
                journey, persona, session, client,
                artifact_dir_override=tmp_path / "run_budget",
                _recorder_module=None,
            )

        assert result.outcome == "budget_exceeded"

    def test_max_llm_calls_exceeded(self, tmp_path):
        journey = _make_journey(
            success_criteria=[SuccessCriterion(text_visible="NEVER_VISIBLE")],
            budget=Budget(max_steps=100, max_seconds=300, max_llm_calls=1),
        )
        persona = _make_persona()
        session = _make_session()
        # 2 decisions but max_llm_calls=1 → stops after 1 call
        client = FakeLLMClient([
            StepDecision(tool="tap", args={"x": 10, "y": 10}, rationale="r", confidence=0.5),
            StepDecision(tool="tap", args={"x": 10, "y": 10}, rationale="r", confidence=0.5),
        ])

        with (
            patch("specterqa_ios.journey.runner.tool_observe", return_value=_OBS_EMPTY),
            patch("specterqa_ios.journey.runner.tool_perf", return_value={}),
            patch("specterqa_ios.journey.runner.tool_crashes", return_value={"crashes": []}),
            patch("specterqa_ios.journey.runner.tool_perf_baseline", return_value={}),
            patch("specterqa_ios.journey.runner.tool_tap", return_value={"ok": True}),
        ):
            result = run_journey(
                journey, persona, session, client,
                artifact_dir_override=tmp_path / "run_llm_budget",
                _recorder_module=None,
            )

        assert result.outcome == "budget_exceeded"


class TestRunJourneyCrash:
    def test_crash_detected_gives_crashed(self, tmp_path):
        journey = _make_journey(
            success_criteria=[SuccessCriterion(no_crash=True)]
        )
        persona = _make_persona()
        session = _make_session()
        client = FakeLLMClient([])

        crashes = [{"path": "/tmp/crash.ips", "timestamp": 9999}]

        with (
            patch("specterqa_ios.journey.runner.tool_observe", return_value=_OBS_EMPTY),
            patch("specterqa_ios.journey.runner.tool_perf", return_value={}),
            patch("specterqa_ios.journey.runner.tool_crashes", return_value={"crashes": crashes}),
            patch("specterqa_ios.journey.runner.tool_perf_baseline", return_value={}),
        ):
            result = run_journey(
                journey, persona, session, client,
                artifact_dir_override=tmp_path / "run_crash",
                _recorder_module=None,
            )

        assert result.outcome == "crashed"


class TestRunJourneyActToolFailed:
    def test_act_tool_failure_gives_error(self, tmp_path):
        journey = _make_journey(
            success_criteria=[SuccessCriterion(text_visible="NEVER_VISIBLE")],
        )
        persona = _make_persona()
        session = _make_session()
        client = FakeLLMClient([
            StepDecision(tool="tap", args={"x": 50, "y": 50}, rationale="tap", confidence=0.9),
        ])

        from specterqa_ios.errors import SimdriveError

        def _fake_tap_fail(args: dict):
            raise SimdriveError(code="target_not_found", message="no match", details={})

        with (
            patch("specterqa_ios.journey.runner.tool_observe", return_value=_OBS_EMPTY),
            patch("specterqa_ios.journey.runner.tool_perf", return_value={}),
            patch("specterqa_ios.journey.runner.tool_crashes", return_value={"crashes": []}),
            patch("specterqa_ios.journey.runner.tool_perf_baseline", return_value={}),
            patch("specterqa_ios.journey.runner.tool_tap", side_effect=_fake_tap_fail),
        ):
            result = run_journey(
                journey, persona, session, client,
                artifact_dir_override=tmp_path / "run_act_fail",
                _recorder_module=None,
            )

        assert result.outcome == "error"


class TestRunJourneyMetrics:
    def test_llm_calls_counted(self, tmp_path):
        journey = _make_journey(
            success_criteria=[SuccessCriterion(text_visible="NEVER_VISIBLE")],
            budget=Budget(max_steps=3, max_seconds=60, max_llm_calls=20),
        )
        persona = _make_persona()
        session = _make_session()
        # 3 taps, then budget_exceeded kicks in at step_idx==3
        client = FakeLLMClient([
            StepDecision(tool="tap", args={"x": 1, "y": 1}, rationale="r", confidence=0.5),
            StepDecision(tool="tap", args={"x": 1, "y": 1}, rationale="r", confidence=0.5),
            StepDecision(tool="tap", args={"x": 1, "y": 1}, rationale="r", confidence=0.5),
        ])

        with (
            patch("specterqa_ios.journey.runner.tool_observe", return_value=_OBS_EMPTY),
            patch("specterqa_ios.journey.runner.tool_perf", return_value={}),
            patch("specterqa_ios.journey.runner.tool_crashes", return_value={"crashes": []}),
            patch("specterqa_ios.journey.runner.tool_perf_baseline", return_value={}),
            patch("specterqa_ios.journey.runner.tool_tap", return_value={"ok": True}),
        ):
            result = run_journey(
                journey, persona, session, client,
                artifact_dir_override=tmp_path / "run_metrics",
                _recorder_module=None,
            )

        assert result.llm_calls == 3
        assert result.duration_seconds > 0
        assert result.outcome == "budget_exceeded"

    def test_artifact_dir_written(self, tmp_path):
        journey = _make_journey()
        persona = _make_persona()
        session = _make_session()
        client = FakeLLMClient([
            StepDecision(tool="done", args={}, rationale="ok", confidence=1.0),
        ])

        artifact_dir = tmp_path / "my_artifacts"

        with (
            patch("specterqa_ios.journey.runner.tool_observe", return_value=_OBS_WITH_HOME),
            patch("specterqa_ios.journey.runner.tool_perf", return_value={}),
            patch("specterqa_ios.journey.runner.tool_crashes", return_value={"crashes": []}),
            patch("specterqa_ios.journey.runner.tool_perf_baseline", return_value={}),
        ):
            result = run_journey(
                journey, persona, session, client,
                artifact_dir_override=artifact_dir,
                _recorder_module=None,
            )

        assert artifact_dir.exists()
        assert (artifact_dir / "summary.json").exists()
        assert (artifact_dir / "summary.md").exists()
        assert (artifact_dir / "agent_trace.jsonl").exists()


class TestRunResultSerialisation:
    def test_to_dict_json_safe(self, tmp_path):
        """RunResult.to_dict() must be JSON-serialisable."""
        import json

        journey = _make_journey()
        persona = _make_persona()
        session = _make_session()
        client = FakeLLMClient([
            StepDecision(tool="done", args={}, rationale="done", confidence=1.0),
        ])

        with (
            patch("specterqa_ios.journey.runner.tool_observe", return_value=_OBS_WITH_HOME),
            patch("specterqa_ios.journey.runner.tool_perf", return_value={}),
            patch("specterqa_ios.journey.runner.tool_crashes", return_value={"crashes": []}),
            patch("specterqa_ios.journey.runner.tool_perf_baseline", return_value={}),
        ):
            result = run_journey(
                journey, persona, session, client,
                artifact_dir_override=tmp_path / "serial_test",
                _recorder_module=None,
            )

        d = result.to_dict()
        json_str = json.dumps(d)  # must not raise
        assert "passed" in json_str

    def test_passed_property(self, tmp_path):
        journey = _make_journey()
        persona = _make_persona()
        session = _make_session()
        client = FakeLLMClient([
            StepDecision(tool="done", args={}, rationale="done", confidence=1.0),
        ])

        with (
            patch("specterqa_ios.journey.runner.tool_observe", return_value=_OBS_WITH_HOME),
            patch("specterqa_ios.journey.runner.tool_perf", return_value={}),
            patch("specterqa_ios.journey.runner.tool_crashes", return_value={"crashes": []}),
            patch("specterqa_ios.journey.runner.tool_perf_baseline", return_value={}),
        ):
            result = run_journey(
                journey, persona, session, client,
                artifact_dir_override=tmp_path / "pass_prop",
                _recorder_module=None,
            )

        assert result.passed is True
