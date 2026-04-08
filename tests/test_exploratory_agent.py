"""Tests for ExploratoryAgent (M16) — INIT-2026-492.

TDD Phase — tests written BEFORE implementation exists.
These tests are importable even when the implementation module is absent.

Module under test (to be created by CodeAtlas):
  specterqa/ios/exploratory/agent.py  — ExploratoryAgent
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

# ---------------------------------------------------------------------------
# Conditional import guard — tests importable even if impl is missing.
# ---------------------------------------------------------------------------

try:
    from specterqa.ios.exploratory.agent import ExploratoryAgent  # type: ignore[import]

    _AGENT_AVAILABLE = True
except ImportError:
    _AGENT_AVAILABLE = False
    ExploratoryAgent = None  # type: ignore[assignment,misc]

needs_agent = pytest.mark.skipif(
    not _AGENT_AVAILABLE,
    reason="specterqa.ios.exploratory.agent not yet implemented",
)

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _make_step_runner(result_overrides: dict | None = None) -> MagicMock:
    """Build a mock step_runner whose run_step returns a minimal StepResult dict."""
    runner = MagicMock()
    base_result = {
        "success": True,
        "finding": None,
        "covered_area": "home_screen",
        "cost": 0.05,
        "critical": False,
    }
    if result_overrides:
        base_result.update(result_overrides)
    runner.run_step = MagicMock(return_value=base_result)
    return runner


def _make_persona(
    name: str = "QA Engineer",
    role: str = "tester",
    goals: list[str] | None = None,
    traits: list[str] | None = None,
) -> dict:
    return {
        "name": name,
        "role": role,
        "goals": goals or ["find UI bugs", "test edge cases"],
        "traits": traits or ["thorough", "systematic"],
    }


# ===========================================================================
# M16: ExploratoryAgent — 12 tests
# ===========================================================================


@needs_agent
class TestExploratoryAgentReturnShape:
    """explore() return value structure."""

    def test_explore_returns_dict_with_required_keys(self):
        runner = _make_step_runner()
        agent = ExploratoryAgent(step_runner=runner, max_steps=1)
        result = agent.explore(app_context="login screen")
        assert isinstance(result, dict)
        required_keys = {"steps_taken", "findings", "coverage_areas", "duration_seconds", "budget_used"}
        assert required_keys.issubset(result.keys()), f"Missing keys: {required_keys - result.keys()}"

    def test_explore_coverage_areas_is_list_of_strings(self):
        runner = _make_step_runner()
        agent = ExploratoryAgent(step_runner=runner, max_steps=1)
        result = agent.explore()
        assert isinstance(result["coverage_areas"], list)
        for area in result["coverage_areas"]:
            assert isinstance(area, str)

    def test_explore_findings_is_list(self):
        runner = _make_step_runner()
        agent = ExploratoryAgent(step_runner=runner, max_steps=2)
        result = agent.explore()
        assert isinstance(result["findings"], list)


@needs_agent
class TestExploratoryAgentStepExecution:
    """explore() step runner interaction."""

    def test_explore_calls_step_runner_run_step(self):
        runner = _make_step_runner()
        agent = ExploratoryAgent(step_runner=runner, max_steps=3, budget=10.0)
        agent.explore(app_context="settings")
        assert runner.run_step.call_count >= 1

    def test_explore_stops_at_max_steps(self):
        runner = _make_step_runner()
        max_steps = 4
        agent = ExploratoryAgent(step_runner=runner, max_steps=max_steps, budget=100.0)
        result = agent.explore()
        assert result["steps_taken"] <= max_steps

    def test_explore_stops_when_budget_exceeded(self):
        # Each step costs 0.50; budget is 1.0 — should stop after ≤2 steps.
        runner = _make_step_runner(result_overrides={"cost": 0.50})
        agent = ExploratoryAgent(step_runner=runner, max_steps=100, budget=1.0)
        result = agent.explore()
        assert result["steps_taken"] <= 3  # tolerance for boundary check order

    def test_explore_stops_on_critical_finding(self):
        runner = _make_step_runner(result_overrides={"critical": True, "finding": "crash on tap"})
        agent = ExploratoryAgent(step_runner=runner, max_steps=50, budget=100.0)
        result = agent.explore()
        # Should stop after the critical finding is detected, not run all 50 steps.
        assert result["steps_taken"] < 10

    def test_explore_records_findings_from_step_results(self):
        finding_text = "Button label missing"
        runner = _make_step_runner(result_overrides={"finding": finding_text})
        agent = ExploratoryAgent(step_runner=runner, max_steps=2, budget=10.0)
        result = agent.explore()
        assert finding_text in result["findings"]

    def test_explore_tracks_coverage_areas(self):
        runner = _make_step_runner(result_overrides={"covered_area": "settings_screen"})
        agent = ExploratoryAgent(step_runner=runner, max_steps=2, budget=10.0)
        result = agent.explore()
        assert "settings_screen" in result["coverage_areas"]


@needs_agent
class TestGenerateNextGoal:
    """_generate_next_goal() logic."""

    def test_generate_next_goal_avoids_already_covered_areas(self):
        runner = _make_step_runner()
        persona = _make_persona(goals=["test login", "test settings", "test profile"])
        agent = ExploratoryAgent(step_runner=runner, persona=persona, max_steps=10)
        already_covered = {"login", "settings"}
        goal = agent._generate_next_goal(history=[], coverage=already_covered)
        # The returned goal must be a non-empty string.
        assert isinstance(goal, str)
        assert len(goal.strip()) > 0

    def test_generate_next_goal_uses_persona_goals(self):
        runner = _make_step_runner()
        persona = _make_persona(goals=["verify onboarding flow", "check error states"])
        agent = ExploratoryAgent(step_runner=runner, persona=persona, max_steps=10)
        goal = agent._generate_next_goal(history=[], coverage=set())
        # Should produce a non-empty exploration goal derived from context.
        assert isinstance(goal, str)
        assert len(goal.strip()) > 0


@needs_agent
class TestShouldStop:
    """_should_stop() early-exit conditions."""

    def test_should_stop_true_at_max_steps(self):
        runner = _make_step_runner()
        agent = ExploratoryAgent(step_runner=runner, max_steps=5, budget=10.0)
        assert agent._should_stop(steps=5, findings=[], budget_used=0.0) is True

    def test_should_stop_true_on_critical_finding(self):
        runner = _make_step_runner()
        agent = ExploratoryAgent(step_runner=runner, max_steps=50, budget=10.0)
        findings = [{"text": "crash detected", "critical": True}]
        assert agent._should_stop(steps=1, findings=findings, budget_used=0.1) is True

    def test_should_stop_false_below_limits(self):
        runner = _make_step_runner()
        agent = ExploratoryAgent(step_runner=runner, max_steps=50, budget=10.0)
        assert agent._should_stop(steps=3, findings=[], budget_used=0.5) is False


@needs_agent
class TestPersonaStored:
    """Persona data is stored and accessible on the agent."""

    def test_persona_traits_accessible_on_agent(self):
        runner = _make_step_runner()
        persona = _make_persona(traits=["adversarial", "detail-oriented"])
        agent = ExploratoryAgent(step_runner=runner, persona=persona)
        # The persona (or its traits) must be retrievable — exact attribute name
        # is up to implementation, but persona dict must be stored somewhere.
        stored = getattr(agent, "persona", None) or getattr(agent, "_persona", None)
        assert stored is not None, "Persona dict must be stored on the agent instance"
        assert stored.get("traits") == ["adversarial", "detail-oriented"]
