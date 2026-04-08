"""SpecterQA Computer Use Runner — iOS Simulator AI-driven test runner.

Orchestrates the full iOS Simulator test lifecycle using Claude Computer Use
as the AI decision engine.  Manages simulator boot/teardown, evidence
collection, and budget enforcement.

Bundled in specterqa-ios (INIT-2026-493).
"""

from __future__ import annotations

import logging
import os
import uuid
from pathlib import Path
from typing import Any

from specterqa.engine.protocols import StepResult

logger = logging.getLogger("specterqa.engine.computer_use_runner")


class ComputerUseRunner:
    """Orchestrates AI-driven iOS Simulator test execution.

    Uses ``ComputerUseDecider`` (Claude Computer Use) as the AI brain and
    ``AIStepRunner`` for the screenshot→decide→act loop.

    Usage::

        runner = ComputerUseRunner(
            bundle_id="com.example.myapp",
            evidence_dir=Path("/tmp/evidence"),
        )
        runner.start()
        results = runner.run_scenario(scenario_dict)
        runner.stop()
    """

    def __init__(
        self,
        bundle_id: str,
        evidence_dir: Path | str | None = None,
        app_path: str | None = None,
        device_name: str | None = None,
        budget: float | None = None,
        api_key: str | None = None,
        model: str = "claude-sonnet-4-20250514",
    ) -> None:
        """
        Args:
            bundle_id: iOS app bundle identifier.
            evidence_dir: Root directory for evidence storage.  A run-specific
                subdirectory is created inside it.
            app_path: Path to the ``.app`` bundle to install.  Optional if the
                app is already installed on the simulator.
            device_name: Preferred simulator device name (e.g. "iPhone 15 Pro").
            budget: Per-run cost budget in USD.  Steps are skipped once exceeded.
            api_key: Anthropic API key.  Falls back to ``ANTHROPIC_API_KEY`` env.
            model: Claude model ID for Computer Use.
        """
        self._bundle_id = bundle_id
        self._app_path = app_path
        self._device_name = device_name or "iPhone 15 Pro"
        self._budget = budget
        self._api_key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        self._model = model

        # Resolve run-specific evidence directory
        run_id = f"run_{uuid.uuid4().hex[:8]}"
        if evidence_dir is not None:
            self._evidence_dir = Path(evidence_dir) / run_id
        else:
            self._evidence_dir = Path("/tmp/specterqa_evidence") / run_id

        # Runtime state — populated in start()
        self._sim_runner: Any = None
        self._decider: Any = None
        self._executor: Any = None
        self._ai_runner_cls: Any = None
        self._budget_exceeded: bool = False

    # -- Lifecycle -----------------------------------------------------------

    def start(self) -> None:
        """Boot the simulator and initialise all AI components.

        Imports are deferred so that module-level import of this class does not
        require the full dependency tree to be installed (supports mock-based
        testing).
        """
        from specterqa.engine.simulator_runner import SimulatorRunner
        from specterqa.engine.computer_use_decider import ComputerUseDecider
        from specterqa.engine.sim_action_executor import SimActionExecutor
        from specterqa.engine.ai_step_runner import AIStepRunner

        self._evidence_dir.mkdir(parents=True, exist_ok=True)

        # Boot simulator
        self._sim_runner = SimulatorRunner(
            bundle_id=self._bundle_id,
            evidence_dir=self._evidence_dir,
            app_path=self._app_path,
            device_name=self._device_name,
        )
        self._sim_runner.start()

        # Wire up AI decider
        cost_callback = self._make_cost_callback()
        self._decider = ComputerUseDecider(
            api_key=self._api_key,
            model=self._model,
            cost_callback=cost_callback,
        )

        # Wire up executor
        self._executor = SimActionExecutor(
            runner=self._sim_runner,
            evidence_dir=self._evidence_dir,
        )

        # Keep a reference to the runner class for step execution
        self._ai_runner_cls = AIStepRunner

        logger.info("ComputerUseRunner started: bundle_id=%s device=%s", self._bundle_id, self._device_name)

    def stop(self) -> None:
        """Shut down the iOS Simulator and release resources."""
        if self._sim_runner is not None:
            try:
                self._sim_runner.stop()
            except Exception as exc:
                logger.warning("ComputerUseRunner: error during stop: %s", exc)
        logger.info("ComputerUseRunner stopped")

    # -- Scenario Execution --------------------------------------------------

    def run_scenario(self, scenario: dict[str, Any]) -> list[StepResult]:
        """Execute all steps in a scenario and return per-step results.

        Args:
            scenario: Scenario dict with a ``steps`` list.  Each step is a
                dict with at least ``id`` and ``goal`` keys.

        Returns:
            A list of :class:`StepResult` objects — one per step, in order.
            Steps are skipped (returned as failed) when the budget is exceeded.
        """
        steps = scenario.get("steps", [])
        results: list[StepResult] = []

        for step in steps:
            step_id = step.get("id", "unknown")

            if self._budget_exceeded:
                results.append(self._make_skipped_result(step_id, "Budget exceeded — step skipped"))
                continue

            result = self._execute_step(step)
            results.append(result)

        return results

    # -- Internal Helpers ----------------------------------------------------

    def _execute_step(self, step: dict[str, Any]) -> StepResult:
        """Execute a single step using AIStepRunner."""
        from specterqa.engine.cost_tracker import BudgetExceededError

        step_id = step.get("id", "unknown")

        # Build the AIStepRunner for this step
        ai_runner = self._ai_runner_cls(
            screenshot_fn=self._make_screenshot_fn(),
            decider=self._decider,
            executor=self._executor,
            evidence_dir=self._evidence_dir,
        )

        try:
            result = ai_runner.execute_step(step)
            return result
        except BudgetExceededError as exc:
            logger.warning("ComputerUseRunner: budget exceeded at step %s: %s", step_id, exc)
            self._budget_exceeded = True
            return self._make_skipped_result(step_id, f"Budget exceeded: {exc}")
        except Exception as exc:
            logger.error("ComputerUseRunner: step %s failed: %s", step_id, exc, exc_info=True)
            return StepResult(
                step_id=step_id,
                passed=False,
                screenshots=[],
                ux_observations=[],
                actions_taken=[],
                action_count=0,
                duration_seconds=0.0,
                checkpoints_reached=[],
                findings=[],
                error=str(exc),
                goal_achieved=False,
            )

    def _make_screenshot_fn(self):
        """Return a screenshot function bound to the SimulatorRunner."""

        def take_screenshot(step_id: str, action_idx: int, label: str) -> str | None:
            if self._sim_runner is None:
                return None
            return self._sim_runner._take_screenshot(step_id, action_idx, label)

        return take_screenshot

    def _make_cost_callback(self):
        """Return a cost callback that enforces the per-run budget."""
        budget = self._budget

        if budget is None:
            return None

        # Mutable state via closure
        spent = [0.0]
        exceeded = [False]
        runner_ref = self

        def cost_callback(model: str, cost_usd: float) -> None:
            from specterqa.engine.cost_tracker import BudgetExceededError

            spent[0] += cost_usd
            if not exceeded[0] and spent[0] > budget:
                exceeded[0] = True
                runner_ref._budget_exceeded = True
                raise BudgetExceededError(f"Run budget exceeded: ${spent[0]:.4f} > ${budget:.2f}")

        return cost_callback

    @staticmethod
    def _make_skipped_result(step_id: str, reason: str) -> StepResult:
        """Build a failed StepResult for a skipped step."""
        return StepResult(
            step_id=step_id,
            passed=False,
            screenshots=[],
            ux_observations=[],
            actions_taken=[],
            action_count=0,
            duration_seconds=0.0,
            checkpoints_reached=[],
            findings=[],
            error=reason,
            goal_achieved=False,
        )
