"""SpecterQA AI Step Runner -- Generic AI-driven UI testing loop.

Bundled in specterqa-ios — sourced from specterqa.engine (upstream unpublished).

Platform-agnostic: consumers inject their own AIDecider (the AI brain) and
ActionExecutor (platform-specific actions).
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from specterqa.engine.protocols import (
    ActionExecutor,
    ActionResult,
    AIDecider,
    Decision,
    StepResult,
)

logger = logging.getLogger("specterqa.engine.ai_step_runner")

_DEFAULT_MAX_ACTIONS = 30
_DEFAULT_MAX_DURATION_SECONDS = 180
_DEFAULT_SETTLE_SECONDS = 0.3
_DEFAULT_STUCK_WARN_THRESHOLD = 5
_DEFAULT_STUCK_ABORT_THRESHOLD = 10
_DEFAULT_ACTION_REPEAT_THRESHOLD = 3
_DEFAULT_CONSECUTIVE_STUCK_LIMIT = 3
_DEFAULT_HASH_HISTORY_SIZE = 10
_DEFAULT_MAX_VERIFICATION_FAILURES = 2


class AIStepRunner:
    """Generic AI-driven UI testing loop.

    Orchestrates the screenshot->decide->act cycle with configurable stuck
    detection, evidence collection, and timing enforcement.
    """

    def __init__(
        self,
        screenshot_fn: Callable[[str, int, str], str | None],
        decider: AIDecider,
        executor: ActionExecutor,
        evidence_dir: Path,
        ui_context_fn: Callable[[], str] | None = None,
        hash_fn: Callable[[], str] | None = None,
        cost_callback: Callable[[str, float], None] | None = None,
        on_escalation: Callable[[dict[str, Any]], None] | None = None,
        settle_seconds: float = _DEFAULT_SETTLE_SECONDS,
        stuck_warn_threshold: int = _DEFAULT_STUCK_WARN_THRESHOLD,
        stuck_abort_threshold: int = _DEFAULT_STUCK_ABORT_THRESHOLD,
        action_repeat_threshold: int = _DEFAULT_ACTION_REPEAT_THRESHOLD,
        consecutive_stuck_limit: int = _DEFAULT_CONSECUTIVE_STUCK_LIMIT,
        hash_history_size: int = _DEFAULT_HASH_HISTORY_SIZE,
    ) -> None:
        self._screenshot_fn = screenshot_fn
        self._decider = decider
        self._executor = executor
        self._evidence_dir = Path(evidence_dir)
        self._ui_context_fn = ui_context_fn
        self._hash_fn = hash_fn
        self._cost_callback = cost_callback
        self._on_escalation = on_escalation
        self._settle_seconds = settle_seconds
        self._stuck_warn_threshold = stuck_warn_threshold
        self._stuck_abort_threshold = stuck_abort_threshold
        self._action_repeat_threshold = action_repeat_threshold
        self._consecutive_stuck_limit = consecutive_stuck_limit
        self._hash_history_size = hash_history_size

    def execute_step(
        self,
        step: dict[str, Any],
        captured_vars: dict[str, Any] | None = None,
    ) -> StepResult:
        """Run the AI loop for a single step."""
        step_id = step.get("id", "unknown")
        goal = step.get("goal", "")
        max_actions = step.get("max_actions", _DEFAULT_MAX_ACTIONS)
        max_duration = step.get("max_duration_seconds", _DEFAULT_MAX_DURATION_SECONDS)

        warn_threshold = step.get("stuck_warn_threshold", self._stuck_warn_threshold)
        abort_threshold = step.get("stuck_abort_threshold", self._stuck_abort_threshold)
        repeat_threshold = step.get("action_repeat_threshold", self._action_repeat_threshold)

        logger.info("AI step %s: goal=%s, max_actions=%d, max_duration=%ds", step_id, goal[:80], max_actions, max_duration)

        screenshots: list[str] = []
        ux_observations: list[str] = []
        actions_taken: list[dict[str, Any]] = []
        checkpoints_reached: list[str] = []
        findings: list[Any] = []
        goal_achieved = False
        error_msg: str | None = None

        recent_hashes: list[str] = []
        consecutive_same_hash = 0
        recent_actions: list[tuple[str, str]] = []
        consecutive_stuck_decisions = 0
        verification_failures = 0

        start_time = time.monotonic()
        action_idx = 0

        while action_idx < max_actions:
            elapsed = time.monotonic() - start_time
            if elapsed > max_duration:
                error_msg = f"Step timed out after {elapsed:.0f}s (limit: {max_duration}s)"
                logger.warning("AI step %s: %s", step_id, error_msg)
                break

            ss_path = self._screenshot_fn(step_id, action_idx, "before")
            if ss_path:
                screenshots.append(ss_path)

            force_api = False
            stuck_context: str | None = None

            if self._hash_fn is not None:
                current_hash = self._hash_fn()
                if recent_hashes and current_hash == recent_hashes[-1]:
                    consecutive_same_hash += 1
                else:
                    consecutive_same_hash = 0

                recent_hashes.append(current_hash)
                if len(recent_hashes) > self._hash_history_size:
                    recent_hashes.pop(0)

                if consecutive_same_hash >= warn_threshold:
                    force_api = True
                    stuck_context = (
                        f"WARNING: The UI has not changed for the last "
                        f"{consecutive_same_hash} actions.  You may be stuck. "
                        f"Try a COMPLETELY DIFFERENT approach."
                    )
                    if self._on_escalation:
                        self._on_escalation({"step_id": step_id, "action_idx": action_idx, "consecutive_same_hash": consecutive_same_hash, "level": "warn"})

                if consecutive_same_hash >= abort_threshold:
                    error_msg = f"App stuck: no UI change for {consecutive_same_hash} consecutive actions"
                    if self._on_escalation:
                        self._on_escalation({"step_id": step_id, "action_idx": action_idx, "consecutive_same_hash": consecutive_same_hash, "level": "abort"})
                    break

            if not force_api and len(recent_actions) >= repeat_threshold:
                last_n = recent_actions[-repeat_threshold:]
                if len(set(last_n)) == 1:
                    force_api = True
                    stuck_context = (
                        f"WARNING: You have repeated the exact same action "
                        f"'{last_n[0][0]}' on '{last_n[0][1]}' "
                        f"{repeat_threshold} times. Try something DIFFERENT."
                    )

            ui_context = ""
            if self._ui_context_fn is not None:
                try:
                    ui_context = self._ui_context_fn()
                except Exception as exc:
                    logger.warning("AI step %s: ui_context_fn failed: %s", step_id, exc)

            screenshot_b64 = ""
            if ss_path:
                screenshot_b64 = self._read_screenshot_b64(ss_path)

            try:
                decision: Decision = self._decider.decide(
                    goal=goal,
                    screenshot_base64=screenshot_b64,
                    ui_context=ui_context,
                    force_api=force_api,
                    stuck_context=stuck_context,
                )
            except Exception as exc:
                error_msg = f"Decider error: {exc}"
                logger.error("AI step %s: %s", step_id, error_msg, exc_info=True)
                break

            if decision.ux_notes:
                ux_observations.append(decision.ux_notes)

            if decision.checkpoint and decision.checkpoint not in checkpoints_reached:
                checkpoints_reached.append(decision.checkpoint)

            if decision.goal_achieved or decision.action == "done":
                actions_taken.append(self._action_record(action_idx, decision, success=True))
                final_ss = self._screenshot_fn(step_id, action_idx, "goal-achieved")
                if final_ss:
                    screenshots.append(final_ss)

                success_criteria = step.get("success_criteria", [])
                if success_criteria and final_ss:
                    verification_b64 = self._read_screenshot_b64(final_ss)
                    if verification_b64:
                        verification_goal = (
                            "VERIFICATION: You just claimed the goal was achieved. "
                            "Look at the current screenshot and verify EACH of "
                            "these success criteria:\n"
                            + "\n".join(f"- {c}" for c in success_criteria)
                            + "\n\nSet goal_achieved to true ONLY if ALL criteria are confirmed."
                        )
                        try:
                            v_decision: Decision = self._decider.decide(
                                goal=verification_goal,
                                screenshot_base64=verification_b64,
                                ui_context="",
                                force_api=True,
                                stuck_context=None,
                            )
                            if v_decision.goal_achieved:
                                goal_achieved = True
                                break
                            else:
                                verification_failures += 1
                                failure_reason = v_decision.reasoning or v_decision.observation or "Criteria not confirmed"
                                findings.append({"type": "verification_failure", "step_id": step_id, "action_idx": action_idx, "reason": failure_reason, "attempt": verification_failures})
                                if verification_failures >= _DEFAULT_MAX_VERIFICATION_FAILURES:
                                    error_msg = f"Verification failed {verification_failures} times: {failure_reason}"
                                    goal_achieved = False
                                    break
                                goal = step.get("goal", "") + f"\n\nPREVIOUS VERIFICATION FAILED: {failure_reason}"
                                goal_achieved = False
                                action_idx += 1
                                continue
                        except Exception as exc:
                            logger.warning("AI step %s: verification call failed: %s", step_id, exc)
                            goal_achieved = True
                            break
                else:
                    goal_achieved = True
                    break

            if decision.action == "stuck":
                consecutive_stuck_decisions += 1
                actions_taken.append(self._action_record(action_idx, decision, success=True))
                if consecutive_stuck_decisions >= self._consecutive_stuck_limit:
                    error_msg = f"Agent reported stuck {consecutive_stuck_decisions} consecutive times"
                    break
                action_idx += 1
                continue
            else:
                consecutive_stuck_decisions = 0

            action_start = time.monotonic()
            try:
                result: ActionResult = self._executor.execute(decision)
            except Exception as exc:
                result = ActionResult(success=False, action=decision.action, target=decision.target, error=str(exc))
                logger.error("AI step %s: executor error: %s", step_id, exc, exc_info=True)

            action_duration_ms = result.duration_ms or round((time.monotonic() - action_start) * 1000, 1)
            actions_taken.append({
                "index": action_idx,
                "action": decision.action,
                "target": decision.target,
                "value": decision.value,
                "reasoning": decision.reasoning,
                "success": result.success,
                "error": result.error,
                "duration_ms": action_duration_ms,
                "ui_changed": result.ui_changed,
            })

            if self._cost_callback is not None:
                try:
                    self._cost_callback(decision.action, 0.0)
                except Exception:
                    pass

            recent_actions.append((decision.action, decision.target[:50]))
            if len(recent_actions) > self._hash_history_size:
                recent_actions.pop(0)

            time.sleep(self._settle_seconds)
            action_idx += 1

        if not goal_achieved:
            final_ss = self._screenshot_fn(step_id, action_idx, "final")
            if final_ss:
                screenshots.append(final_ss)

        if not goal_achieved and error_msg is None:
            error_msg = f"Max actions ({max_actions}) reached without achieving goal"

        duration = round(time.monotonic() - start_time, 2)
        passed = goal_achieved and error_msg is None

        return StepResult(
            step_id=step_id,
            passed=passed,
            screenshots=screenshots,
            ux_observations=ux_observations,
            actions_taken=actions_taken,
            action_count=action_idx,
            duration_seconds=duration,
            checkpoints_reached=checkpoints_reached,
            findings=findings,
            error=error_msg,
            goal_achieved=goal_achieved,
        )

    @staticmethod
    def _action_record(
        index: int,
        decision: Decision,
        *,
        success: bool,
        duration_ms: float = 0.0,
        error: str | None = None,
        ui_changed: bool = False,
    ) -> dict[str, Any]:
        return {
            "index": index,
            "action": decision.action,
            "target": decision.target,
            "value": decision.value,
            "reasoning": decision.reasoning,
            "success": success,
            "error": error,
            "duration_ms": duration_ms,
            "ui_changed": ui_changed,
        }

    @staticmethod
    def _read_screenshot_b64(filepath: str) -> str:
        import base64
        try:
            with open(filepath, "rb") as f:
                return base64.b64encode(f.read()).decode("utf-8")
        except Exception as exc:
            logger.warning("Failed to read screenshot %s: %s", filepath, exc)
            return ""
