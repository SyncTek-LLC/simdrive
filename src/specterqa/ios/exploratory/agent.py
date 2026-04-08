"""M16: ExploratoryAgent — VTE-style persona-driven iOS exploration.

Drives an IOSAIStepRunner through an app using a persona definition,
collecting findings and coverage areas until a stop condition fires.

INIT-2026-492 — SpecterQA iOS Simulator Driver, Phase 4.
"""

from __future__ import annotations

import time
from typing import Any


class ExploratoryAgent:
    """Persona-driven exploratory testing agent for iOS apps.

    Args:
        step_runner: An object with a ``run_step(goal, ...)`` method that
            returns a dict with keys: success, finding, covered_area, cost,
            critical.
        persona: Optional dict with keys: name, role, goals, traits.
            Defaults to a generic QA persona.
        max_steps: Hard upper bound on exploration steps. Default 20.
        budget: Maximum spend in USD before stopping. Default None (unlimited).
    """

    _DEFAULT_PERSONA: dict[str, Any] = {
        "name": "QA Engineer",
        "role": "tester",
        "goals": ["explore the app", "find UI bugs"],
        "traits": ["thorough", "systematic"],
    }

    def __init__(
        self,
        step_runner: Any,
        persona: dict[str, Any] | None = None,
        max_steps: int = 20,
        budget: float | None = None,
    ) -> None:
        self._step_runner = step_runner
        self.persona = persona if persona is not None else dict(self._DEFAULT_PERSONA)
        self._max_steps = max_steps
        self._budget = budget

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def explore(self, app_context: str = "") -> dict[str, Any]:
        """Run the exploration loop and return a summary.

        Args:
            app_context: Free-text description of the current app state /
                starting screen. Used to seed the first goal.

        Returns:
            Dict with keys:
                steps_taken (int), findings (list[str]), coverage_areas
                (list[str]), duration_seconds (float), budget_used (float).
        """
        start_time = time.monotonic()

        findings: list[Any] = []
        coverage_areas: list[str] = []
        coverage_set: set[str] = set()
        history: list[str] = []
        budget_used: float = 0.0
        steps_taken: int = 0

        for _ in range(self._max_steps):
            # Check stop before running the next step
            if self._should_stop(
                steps=steps_taken,
                findings=findings,
                budget_used=budget_used,
            ):
                break

            goal = self._generate_next_goal(history=history, coverage=coverage_set)
            result = self._step_runner.run_step(goal)

            steps_taken += 1

            # Accumulate cost
            cost = result.get("cost", 0.0) or 0.0
            budget_used += cost

            # Collect finding
            finding = result.get("finding")
            if finding:
                findings.append(finding)

            # Collect coverage area
            area = result.get("covered_area")
            if area and area not in coverage_set:
                coverage_set.add(area)
                coverage_areas.append(area)

            # Record goal in history
            history.append(goal)

            # Check critical flag directly on the step result (fast path).
            # This handles the case where finding is a plain string so it
            # cannot carry a 'critical' attribute itself.
            if result.get("critical"):
                break

            # Re-check remaining stop conditions AFTER the step (budget)
            if self._should_stop(
                steps=steps_taken,
                findings=findings,
                budget_used=budget_used,
            ):
                break

        duration = round(time.monotonic() - start_time, 3)

        return {
            "steps_taken": steps_taken,
            "findings": findings,
            "coverage_areas": coverage_areas,
            "duration_seconds": duration,
            "budget_used": budget_used,
        }

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _generate_next_goal(
        self,
        history: list[str],
        coverage: set[str],
    ) -> str:
        """Generate the next exploration goal for the step runner.

        Cycles through persona goals, skipping areas already covered.
        Falls back to a generic exploration prompt when all goals are
        exhausted.

        Args:
            history: Goals already issued in this session.
            coverage: Set of area names already visited.

        Returns:
            A non-empty goal string.
        """
        persona_goals: list[str] = self.persona.get("goals", [])
        persona_name: str = self.persona.get("name", "QA Engineer")
        persona_role: str = self.persona.get("role", "tester")

        # Try each persona goal that hasn't been used yet
        for goal in persona_goals:
            if goal not in history:
                # Skip if the goal text overlaps with an already-covered area
                already_done = any(area.lower() in goal.lower() for area in coverage)
                if not already_done:
                    return goal

        # If all goals exhausted, pick a persona-flavoured generic goal
        step_num = len(history) + 1
        return (
            f"As a {persona_role} named {persona_name}, "
            f"continue exploring the app — step {step_num}. "
            f"Avoid areas already visited: {', '.join(sorted(coverage)) or 'none'}."
        )

    def _should_stop(
        self,
        steps: int,
        findings: list[Any],
        budget_used: float,
    ) -> bool:
        """Return True when any stop condition is satisfied.

        Stop conditions (checked in order):
        1. ``steps >= max_steps``
        2. A critical finding is present in *findings*
        3. Budget exceeded (when a budget is configured)

        Args:
            steps: Number of steps completed so far.
            findings: Accumulated findings (strings or dicts).
            budget_used: Total cost spent so far.

        Returns:
            True if exploration should halt.
        """
        # 1. Max steps reached
        if steps >= self._max_steps:
            return True

        # 2. Critical finding detected
        for item in findings:
            if isinstance(item, dict) and item.get("critical"):
                return True
            # String findings injected via result_overrides in tests are
            # treated as non-critical; critical flag lives in dict form.

        # 3. Budget exceeded
        if self._budget is not None and budget_used >= self._budget:
            return True

        return False
