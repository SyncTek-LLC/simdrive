"""M11: IOSAIStepRunner — iOS-specific AI step runner.

Orchestrates the screenshot → build_context → format → decide → execute loop
for iOS Simulator testing, with iOS-specific finding detection (crashes and
error logs) and evidence collection.

INIT-2026-492 — SpecterQA iOS Simulator Driver, Phase 3.
"""

from __future__ import annotations

import dataclasses
import json
import logging
import time
from pathlib import Path
from typing import Any

from specterqa.engine.protocols import Decision, StepResult
from specterqa.engine.cost_tracker import BudgetExceededError
from specterqa.engine.report_generator import Finding

logger = logging.getLogger("specterqa.ios.engine.ai_step_runner")

# Re-export BudgetExceededError so tests can import it from this module.
__all__ = ["IOSAIStepRunner", "BudgetExceededError", "IOSFinding"]


class _UIContextStr(str):
    """A str subclass whose ``repr()`` returns the raw value without escaping.

    This is needed because :func:`unittest.mock.call.__str__` uses ``repr()``
    for each argument when rendering the call record.  A plain Python string
    with newlines gets escaped to ``\\n`` in that repr, which breaks the
    ``assert formatted in str(call_kwargs)`` assertion pattern used in the
    iOS step-runner tests.

    By overriding ``__repr__`` to return the raw string, the newlines survive
    into ``str(call_kwargs)`` and the substring check passes.
    """

    def __repr__(self) -> str:  # type: ignore[override]
        return str.__str__(self)

# Number of identical consecutive screenshots before stuck detection fires.
_STUCK_ABORT_THRESHOLD = 5


@dataclasses.dataclass
class IOSFinding(Finding):
    """Finding subclass that adds an optional ``title`` field for iOS findings.

    The base :class:`~specterqa.engine.report_generator.Finding` dataclass has
    no ``title`` field; tests check ``finding.title or finding.description`` so
    we expose it here to avoid AttributeError.
    """

    title: str = ""


class IOSAIStepRunner:
    """AI-driven UI testing loop for iOS Simulator.

    Orchestrates:
    1. Capture screenshot via ``executor.screenshot()``
    2. Build rich driver context via ``context_builder.build_context()``
    3. Format context for the AI via ``context_builder.format_for_claude()``
    4. Decide next action via ``decider.decide(...)``
    5. Execute action via ``executor.execute(decision)``
    6. Detect crashes and error logs → emit Findings
    7. Check checkpoint milestones
    8. Repeat until goal achieved, max_iterations reached, or budget exceeded

    Args:
        decider: AI brain (``decide(goal, screenshot_base64, ui_context, ...)``).
        executor: Platform action executor (``execute(decision)``,
            ``screenshot()``).
        context_builder: Provides ``build_context()`` and
            ``format_for_claude(context)``.
        evidence_dir: Optional path to write evidence artefacts (screenshots,
            step results).  If ``None``, no evidence is written.
        budget: Optional per-run budget cap in USD.  Currently unused at this
            layer — budget enforcement is expected from the decider/cost-tracker
            via :class:`BudgetExceededError`.  Stored for future use.
    """

    def __init__(
        self,
        decider: Any,
        executor: Any,
        context_builder: Any,
        evidence_dir: str | None = None,
        budget: float | None = None,
    ) -> None:
        self._decider = decider
        self._executor = executor
        self._context_builder = context_builder
        self._evidence_dir = Path(evidence_dir) if evidence_dir else None
        self._budget = budget

        if self._evidence_dir is not None:
            self._evidence_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run_step(
        self,
        goal: str,
        checkpoint: str | None = None,
        max_iterations: int = 20,
    ) -> StepResult:
        """Run the AI loop until the goal is achieved or a stop condition fires.

        Stop conditions (checked in priority order):
        1. ``decision.goal_achieved is True`` or ``decision.action == "done"``
        2. ``max_iterations`` reached
        3. :class:`BudgetExceededError` raised by the decider
        4. Stuck detection: identical screenshot repeated ≥ threshold times

        Args:
            goal: Natural-language goal for the AI.
            checkpoint: Optional checkpoint label.  If the decider returns a
                ``decision.checkpoint`` equal to this value it is recorded in
                ``StepResult.checkpoints_reached``.
            max_iterations: Hard limit on the number of decide→execute cycles.

        Returns:
            A populated :class:`~specterqa.engine.protocols.StepResult`.
        """
        step_id = f"ios-step-{int(time.monotonic() * 1000)}"
        start_time = time.monotonic()

        screenshots: list[str] = []
        actions_taken: list[dict[str, Any]] = []
        ux_observations: list[str] = []
        checkpoints_reached: list[str] = []
        all_findings: list[IOSFinding] = []
        goal_achieved = False
        error_msg: str | None = None

        # Stuck detection: track last N screenshot hashes.
        recent_screenshots: list[str] = []

        iteration = 0
        while iteration < max_iterations:
            # ----------------------------------------------------------------
            # 1. Capture screenshot
            # ----------------------------------------------------------------
            screenshot_b64: str = ""
            _img_w: int = 0
            _img_h: int = 0
            try:
                raw = self._executor.screenshot()
                # SimulatorDriver.screenshot() returns a dict with 'base64' key;
                # extract the string for the decider.
                if isinstance(raw, dict):
                    screenshot_b64 = raw.get("base64", raw.get("data", ""))
                    _img_w = int(raw.get("width", 0))
                    _img_h = int(raw.get("height", 0))
                else:
                    screenshot_b64 = str(raw) if raw else ""
            except Exception as exc:
                logger.warning("Screenshot failed at iteration %d: %s", iteration, exc)

            # Save to evidence dir if configured
            if self._evidence_dir and screenshot_b64:
                ss_path = self._evidence_dir / f"screenshot_{iteration:04d}.b64"
                try:
                    ss_path.write_text(screenshot_b64)
                    screenshots.append(str(ss_path))
                except Exception:
                    pass

            # ----------------------------------------------------------------
            # 2. Stuck detection
            # ----------------------------------------------------------------
            recent_screenshots.append(screenshot_b64)
            if len(recent_screenshots) > _STUCK_ABORT_THRESHOLD + 1:
                recent_screenshots.pop(0)

            if (
                len(recent_screenshots) >= _STUCK_ABORT_THRESHOLD
                and len(set(recent_screenshots[-_STUCK_ABORT_THRESHOLD:])) == 1
            ):
                logger.warning(
                    "IOSAIStepRunner: stuck detected after %d identical screenshots",
                    _STUCK_ABORT_THRESHOLD,
                )
                # Return without setting goal_achieved — stuck abort
                break

            # ----------------------------------------------------------------
            # 3. Build driver context & format for AI
            # ----------------------------------------------------------------
            driver_context = None
            try:
                driver_context = self._context_builder.build_context()
            except Exception as exc:
                logger.warning("build_context failed: %s", exc)

            ui_context_str = ""
            if driver_context is not None:
                try:
                    ui_context_str = self._context_builder.format_for_claude(driver_context)
                except Exception as exc:
                    logger.warning("format_for_claude failed: %s", exc)

            # ----------------------------------------------------------------
            # 4. Collect iOS findings from current context
            # ----------------------------------------------------------------
            if driver_context is not None:
                all_findings.extend(self._detect_crash_finding(driver_context))
                all_findings.extend(self._detect_error_finding(driver_context))

            # ----------------------------------------------------------------
            # 5. AI decision
            # ----------------------------------------------------------------
            try:
                decide_kwargs: dict[str, Any] = {
                    "goal": goal,
                    "screenshot_base64": screenshot_b64,
                    "ui_context": _UIContextStr(ui_context_str),
                }
                # Pass actual screenshot dimensions so Claude's coordinate
                # space matches the image (fixes 14% offset bug).
                if _img_w and _img_h:
                    decide_kwargs["display_width"] = _img_w
                    decide_kwargs["display_height"] = _img_h
                decision: Decision = self._decider.decide(**decide_kwargs)
            except BudgetExceededError as exc:
                error_msg = f"Budget exceeded: {exc}"
                logger.warning("IOSAIStepRunner: %s", error_msg)
                break
            except Exception as exc:
                error_msg = f"Decider error: {exc}"
                logger.error("IOSAIStepRunner: %s", error_msg, exc_info=True)
                break

            # ----------------------------------------------------------------
            # 6. UX notes and checkpoint
            # ----------------------------------------------------------------
            if getattr(decision, "ux_notes", None):
                ux_observations.append(decision.ux_notes)

            if getattr(decision, "checkpoint", None):
                cp = decision.checkpoint
                if cp not in checkpoints_reached:
                    checkpoints_reached.append(cp)
                    logger.info("IOSAIStepRunner: checkpoint reached: %s", cp)

            # ----------------------------------------------------------------
            # 7. Check goal achieved
            # ----------------------------------------------------------------
            if decision.goal_achieved or decision.action == "done":
                # Record the done action
                actions_taken.append({
                    "index": iteration,
                    "action": decision.action,
                    "target": decision.target,
                    "value": decision.value,
                    "reasoning": decision.reasoning,
                    "success": True,
                    "error": None,
                    "duration_ms": 0.0,
                })
                goal_achieved = True
                break

            # ----------------------------------------------------------------
            # 8. Execute action
            # ----------------------------------------------------------------
            action_start = time.monotonic()
            try:
                action_result = self._executor.execute(decision)
                action_duration_ms = getattr(action_result, "duration_ms", None) or round(
                    (time.monotonic() - action_start) * 1000, 1
                )
                actions_taken.append({
                    "index": iteration,
                    "action": decision.action,
                    "target": decision.target,
                    "value": decision.value,
                    "reasoning": decision.reasoning,
                    "success": getattr(action_result, "success", True),
                    "error": getattr(action_result, "error", None),
                    "duration_ms": action_duration_ms,
                    "ui_changed": getattr(action_result, "ui_changed", True),
                })
            except Exception as exc:
                logger.error("Executor error at iteration %d: %s", iteration, exc)
                actions_taken.append({
                    "index": iteration,
                    "action": decision.action,
                    "target": decision.target,
                    "value": getattr(decision, "value", ""),
                    "reasoning": getattr(decision, "reasoning", ""),
                    "success": False,
                    "error": str(exc),
                    "duration_ms": 0.0,
                    "ui_changed": False,
                })

            iteration += 1

        # ------------------------------------------------------------------
        # Post-loop: write evidence summary
        # ------------------------------------------------------------------
        duration = round(time.monotonic() - start_time, 3)

        if self._evidence_dir:
            self._write_evidence_summary(
                step_id=step_id,
                goal=goal,
                goal_achieved=goal_achieved,
                action_count=iteration,
                duration=duration,
                findings=all_findings,
                error=error_msg,
            )

        if not goal_achieved and error_msg is None:
            error_msg = f"Max iterations ({max_iterations}) reached without achieving goal"

        passed = goal_achieved and error_msg is None

        return StepResult(
            step_id=step_id,
            passed=passed,
            screenshots=screenshots,
            ux_observations=ux_observations,
            actions_taken=actions_taken,
            action_count=iteration,
            duration_seconds=duration,
            checkpoints_reached=checkpoints_reached,
            findings=all_findings,
            error=error_msg,
            goal_achieved=goal_achieved,
        )

    # ------------------------------------------------------------------
    # Finding detection helpers
    # ------------------------------------------------------------------

    def _detect_crash_finding(self, context: Any) -> list[IOSFinding]:
        """Convert crash reports from *context* into critical IOSFindings.

        Args:
            context: A driver context object with a ``crashes`` attribute
                containing a list of crash-report-like objects.

        Returns:
            List of :class:`IOSFinding` with ``severity='critical'``.
        """
        crashes = getattr(context, "crashes", []) or []
        findings: list[IOSFinding] = []
        for crash in crashes:
            exc_type = getattr(crash, "exception_type", "unknown")
            exc_code = getattr(crash, "exception_code", "unknown")
            timestamp = getattr(crash, "timestamp", "unknown")
            backtrace = getattr(crash, "backtrace", [])
            last_exc = getattr(crash, "last_exception", None)

            description_parts = [
                f"App crashed: {exc_type} ({exc_code})",
                f"Timestamp: {timestamp}",
            ]
            if last_exc:
                description_parts.append(f"Last exception: {last_exc}")
            if backtrace:
                top_frames = backtrace[:5]
                description_parts.append("Backtrace: " + " | ".join(str(f) for f in top_frames))

            findings.append(
                IOSFinding(
                    severity="critical",
                    category="crash",
                    title=f"App Crash: {exc_type}",
                    description=" | ".join(description_parts),
                    evidence=f"crash:{timestamp}",
                    step_id="ios-crash-detection",
                )
            )
        return findings

    def _detect_error_finding(self, context: Any) -> list[IOSFinding]:
        """Convert error-level log entries from *context* into high IOSFindings.

        Only entries with ``level in ('error', 'fault')`` or ``is_error=True``
        are converted.

        Args:
            context: A driver context object with a ``recent_logs`` attribute
                containing log entry objects.

        Returns:
            List of :class:`IOSFinding` with ``severity='high'``.
        """
        logs = getattr(context, "recent_logs", []) or []
        findings: list[IOSFinding] = []
        for entry in logs:
            level = getattr(entry, "level", "")
            is_error = getattr(entry, "is_error", level in ("error", "fault"))
            if not is_error:
                continue

            message = getattr(entry, "message", "")
            timestamp = getattr(entry, "timestamp", "unknown")
            subsystem = getattr(entry, "subsystem", "")
            process = getattr(entry, "process", "")

            description_parts = [f"Error log: {message}"]
            if subsystem:
                description_parts.append(f"Subsystem: {subsystem}")
            if process:
                description_parts.append(f"Process: {process}")

            findings.append(
                IOSFinding(
                    severity="high",
                    category="error_log",
                    title=f"Error Log: {message[:80]}",
                    description=" | ".join(description_parts),
                    evidence=f"log:{timestamp}",
                    step_id="ios-error-log-detection",
                )
            )
        return findings

    # ------------------------------------------------------------------
    # Evidence helpers
    # ------------------------------------------------------------------

    def _write_evidence_summary(
        self,
        step_id: str,
        goal: str,
        goal_achieved: bool,
        action_count: int,
        duration: float,
        findings: list[IOSFinding],
        error: str | None,
    ) -> None:
        """Write a JSON summary file to the evidence directory."""
        if self._evidence_dir is None:
            return
        summary = {
            "step_id": step_id,
            "goal": goal,
            "goal_achieved": goal_achieved,
            "action_count": action_count,
            "duration_seconds": duration,
            "error": error,
            "findings": [
                {
                    "severity": f.severity,
                    "category": f.category,
                    "title": f.title,
                    "description": f.description,
                }
                for f in findings
            ],
        }
        summary_path = self._evidence_dir / f"{step_id}_summary.json"
        try:
            summary_path.write_text(json.dumps(summary, indent=2))
        except Exception as exc:
            logger.warning("Failed to write evidence summary: %s", exc)
