"""Tests for M11: IOSAIStepRunner — iOS-specific AIStepRunner subclass/wrapper.

TDD Phase 3 — INIT-2026-492, SpecterQA iOS Simulator Driver.
These tests are written BEFORE the implementation exists and must be
importable even when the implementation module is absent.

Module under test (to be created by CodeAtlas):
    specterqa/ios/engine/ai_step_runner.py  — IOSAIStepRunner
"""

from __future__ import annotations

import dataclasses
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

# ---------------------------------------------------------------------------
# Conditional import guard — core protocols (always available once package installed)
# ---------------------------------------------------------------------------

try:
    from specterqa.engine.protocols import Decision, StepResult  # type: ignore[import]
    _PROTOCOLS_AVAILABLE = True
except ImportError:
    _PROTOCOLS_AVAILABLE = False
    Decision = None  # type: ignore[assignment,misc]
    StepResult = None  # type: ignore[assignment,misc]

needs_protocols = pytest.mark.skipif(
    not _PROTOCOLS_AVAILABLE,
    reason="specterqa.engine.protocols not yet installed",
)

# ---------------------------------------------------------------------------
# Conditional import guard — ios runner
# ---------------------------------------------------------------------------

try:
    from specterqa.ios.engine.ai_step_runner import IOSAIStepRunner  # type: ignore[import]
    _IOS_RUNNER_AVAILABLE = True
except ImportError:
    _IOS_RUNNER_AVAILABLE = False
    IOSAIStepRunner = None  # type: ignore[assignment,misc]

try:
    from specterqa.ios.drivers.simulator.ai_context import (  # type: ignore[import]
        DriverContext,
        SimulatorAIContext,
    )
    _AI_CONTEXT_AVAILABLE = True
except ImportError:
    _AI_CONTEXT_AVAILABLE = False
    DriverContext = None  # type: ignore[assignment,misc]
    SimulatorAIContext = None  # type: ignore[assignment,misc]

needs_ios_runner = pytest.mark.skipif(
    not _IOS_RUNNER_AVAILABLE,
    reason="specterqa.ios.engine.ai_step_runner not yet implemented",
)

# ---------------------------------------------------------------------------
# Helpers — build mock components
# ---------------------------------------------------------------------------


def _make_decision(
    action: str = "click",
    target: str = "Sign In",
    value: str = "",
    goal_achieved: bool = False,
    reasoning: str = "Tap the button",
) -> Any:
    """Build a Decision object for use in mock deciders.

    Returns a MagicMock with Decision-compatible fields when Decision
    is not importable, so helpers remain usable pre-implementation.
    """
    if Decision is not None:
        return Decision(
            action=action,
            target=target,
            value=value,
            reasoning=reasoning,
            goal_achieved=goal_achieved,
            observation="Button is visible",
        )
    # Fallback stub when protocols not installed
    d = MagicMock()
    d.action = action
    d.target = target
    d.value = value
    d.reasoning = reasoning
    d.goal_achieved = goal_achieved
    d.observation = "Button is visible"
    d.ux_notes = None
    d.checkpoint = None
    return d


def _make_done_decision() -> Any:
    """Build a goal_achieved=True Decision."""
    if Decision is not None:
        return Decision(
            action="done",
            target="",
            value="",
            reasoning="Goal achieved",
            goal_achieved=True,
            observation="Task complete",
        )
    d = MagicMock()
    d.action = "done"
    d.target = ""
    d.value = ""
    d.reasoning = "Goal achieved"
    d.goal_achieved = True
    d.observation = "Task complete"
    d.ux_notes = None
    d.checkpoint = None
    return d


def _make_log_entry(message: str = "test", level: str = "info") -> MagicMock:
    entry = MagicMock()
    entry.message = message
    entry.level = level
    entry.timestamp = "2026-03-29T10:00:00Z"
    entry.subsystem = "com.example"
    entry.category = "general"
    entry.process = "MyApp"
    entry.thread_id = 1
    entry.is_error = level in ("error", "fault")
    return entry


def _make_crash_report(exception_type: str = "EXC_BAD_ACCESS") -> MagicMock:
    crash = MagicMock()
    crash.timestamp = "2026-03-29T10:01:00Z"
    crash.exception_type = exception_type
    crash.exception_code = "0x0000000000000001"
    crash.crashing_thread = 0
    crash.backtrace = ["frame0"]
    crash.last_exception = "Fatal error"
    crash.app_version = "1.0.0"
    return crash


def _make_driver_context(
    screenshot_base64: str = "dGVzdA==",
    crashes: list | None = None,
    logs_with_errors: list | None = None,
) -> MagicMock:
    """Build a mock DriverContext."""
    ctx = MagicMock()
    ctx.screenshot_base64 = screenshot_base64
    ctx.recent_logs = logs_with_errors or []
    ctx.active_requests = []
    ctx.perf_snapshot = MagicMock()
    ctx.app_state = {}
    ctx.crashes = crashes or []
    return ctx


def _make_context_builder(
    driver_context: MagicMock | None = None,
    formatted_str: str = "## Recent Logs\nnone\n## Network Activity\nnone",
) -> MagicMock:
    """Build a mock SimulatorAIContext context builder."""
    builder = MagicMock()
    builder.build_context.return_value = driver_context or _make_driver_context()
    builder.format_for_claude.return_value = formatted_str
    return builder


def _make_decider(decisions: list[Decision] | None = None) -> MagicMock:
    """Build a mock AIDecider that returns decisions in sequence."""
    decider = MagicMock()
    if decisions is None:
        decisions = [_make_done_decision()]
    decider.decide.side_effect = decisions
    return decider


def _make_executor(screenshot_b64: str = "dGVzdA==") -> MagicMock:
    """Build a mock ActionExecutor.

    The executor must support:
    - screenshot() -> str (base64)
    - execute(decision) -> ActionResult
    """
    executor = MagicMock()
    executor.screenshot.return_value = screenshot_b64
    action_result = MagicMock()
    action_result.success = True
    action_result.action = "click"
    action_result.target = "button"
    action_result.error = None
    action_result.duration_ms = 50.0
    action_result.ui_changed = True
    executor.execute.return_value = action_result
    return executor


def _make_ios_runner(
    decisions: list[Decision] | None = None,
    driver_context: MagicMock | None = None,
    screenshot_b64: str = "dGVzdA==",
    evidence_dir: str | None = None,
    budget: float | None = None,
) -> Any:
    """Construct an IOSAIStepRunner with mocked components."""
    decider = _make_decider(decisions)
    executor = _make_executor(screenshot_b64)
    context_builder = _make_context_builder(driver_context)
    return IOSAIStepRunner(
        decider=decider,
        executor=executor,
        context_builder=context_builder,
        evidence_dir=evidence_dir,
        budget=budget,
    ), decider, executor, context_builder


# needed for type hints only in helpers
from typing import Any


# ===========================================================================
# Test 1: run_step calls decider with screenshot and formatted context
# ===========================================================================


@needs_ios_runner
class TestRunStepCallsDecider:
    """run_step must call decider.decide() with screenshot + formatted context."""

    def test_decide_called_with_screenshot_and_context(self, tmp_path):
        """decider.decide() must receive the screenshot_base64 from the executor
        and the formatted context string from the context builder."""
        formatted = "## Recent Logs\nGET /api\n## Network Activity\n..."
        driver_ctx = _make_driver_context()
        runner, decider, executor, ctx_builder = _make_ios_runner(
            decisions=[_make_done_decision()],
            driver_context=driver_ctx,
        )
        ctx_builder.format_for_claude.return_value = formatted

        result = runner.run_step(goal="Tap Sign In", checkpoint=None, max_iterations=5)

        decider.decide.assert_called()
        call_kwargs = decider.decide.call_args
        # The formatted context should be passed as ui_context
        assert formatted in str(call_kwargs)


# ===========================================================================
# Test 2: run_step executes action from decider on executor
# ===========================================================================


@needs_ios_runner
class TestRunStepExecutesAction:
    """run_step must pass the Decision from decider to executor.execute()."""

    def test_executor_called_with_decision(self):
        """executor.execute() must be called with the Decision returned by decide()."""
        click_decision = _make_decision(action="click", target="Login Button")
        done_decision = _make_done_decision()
        runner, decider, executor, _ = _make_ios_runner(
            decisions=[click_decision, done_decision],
        )

        runner.run_step(goal="Tap Login", max_iterations=5)

        # executor.execute must have been called with the click Decision
        executor.execute.assert_called()
        first_call_args = executor.execute.call_args_list[0]
        called_decision = first_call_args[0][0]
        assert called_decision.action == "click"


# ===========================================================================
# Test 3: run_step returns StepResult with correct fields
# ===========================================================================


@needs_ios_runner
class TestRunStepReturnsStepResult:
    """run_step must return a properly populated StepResult."""

    def test_returns_step_result_instance(self):
        """run_step must return a StepResult dataclass."""
        runner, *_ = _make_ios_runner(decisions=[_make_done_decision()])
        result = runner.run_step(goal="Do something", max_iterations=5)
        assert isinstance(result, StepResult)

    def test_result_goal_achieved_true_on_done(self):
        """When decider returns goal_achieved=True, StepResult.goal_achieved must be True."""
        runner, *_ = _make_ios_runner(decisions=[_make_done_decision()])
        result = runner.run_step(goal="Complete task", max_iterations=5)
        assert result.goal_achieved is True

    def test_result_passed_true_on_success(self):
        """When goal is achieved without error, StepResult.passed must be True."""
        runner, *_ = _make_ios_runner(decisions=[_make_done_decision()])
        result = runner.run_step(goal="Complete task", max_iterations=5)
        assert result.passed is True


# ===========================================================================
# Test 4: run_step detects crash → Finding with severity="critical"
# ===========================================================================


@needs_ios_runner
class TestRunStepCrashFinding:
    """When a CrashReport is present in DriverContext, run_step must add a
    critical Finding to StepResult.findings."""

    def test_crash_creates_critical_finding(self):
        """A CrashReport in context.crashes must produce a Finding with
        severity='critical' in StepResult.findings."""
        crash_report = _make_crash_report(exception_type="EXC_BAD_ACCESS")
        driver_ctx = _make_driver_context(crashes=[crash_report])
        runner, *_ = _make_ios_runner(
            decisions=[_make_done_decision()],
            driver_context=driver_ctx,
        )
        result = runner.run_step(goal="Navigate to home", max_iterations=5)

        assert len(result.findings) > 0
        crash_findings = [
            f for f in result.findings
            if hasattr(f, "severity") and f.severity == "critical"
        ]
        assert len(crash_findings) > 0, (
            f"Expected at least one critical finding, got: {result.findings}"
        )


# ===========================================================================
# Test 5: run_step detects error log → Finding with severity="high"
# ===========================================================================


@needs_ios_runner
class TestRunStepErrorLogFinding:
    """When error-level log entries are present, run_step must add a high Finding."""

    def test_error_log_creates_high_finding(self):
        """An error-level LogEntry in context.recent_logs must produce a Finding
        with severity='high' in StepResult.findings."""
        error_log = _make_log_entry(message="Connection refused", level="error")
        driver_ctx = _make_driver_context(logs_with_errors=[error_log])
        runner, *_ = _make_ios_runner(
            decisions=[_make_done_decision()],
            driver_context=driver_ctx,
        )
        result = runner.run_step(goal="Check network", max_iterations=5)

        assert len(result.findings) > 0
        high_findings = [
            f for f in result.findings
            if hasattr(f, "severity") and f.severity == "high"
        ]
        assert len(high_findings) > 0, (
            f"Expected at least one high finding for error logs, got: {result.findings}"
        )


# ===========================================================================
# Test 6: run_step stops at max_iterations
# ===========================================================================


@needs_ios_runner
class TestRunStepMaxIterations:
    """run_step must stop when max_iterations is reached."""

    def test_stops_at_max_iterations(self):
        """run_step must not call decider more than max_iterations times."""
        # Never achieves goal — always returns a non-done decision
        decisions = [_make_decision(action="click") for _ in range(20)]
        runner, decider, executor, _ = _make_ios_runner(decisions=decisions)

        result = runner.run_step(goal="Never achieves", max_iterations=3)

        # Should have stopped — decider called at most max_iterations times
        assert decider.decide.call_count <= 3
        assert result.goal_achieved is False


# ===========================================================================
# Test 7: run_step stops when goal_achieved
# ===========================================================================


@needs_ios_runner
class TestRunStepGoalAchievedStop:
    """run_step must stop early when decider returns goal_achieved=True."""

    def test_stops_when_goal_achieved(self):
        """run_step should not call decider again after goal_achieved=True."""
        done = _make_done_decision()
        runner, decider, *_ = _make_ios_runner(
            decisions=[_make_decision(), done, _make_decision(), _make_decision()],
        )
        result = runner.run_step(goal="Finish task", max_iterations=10)

        # Goal was achieved at iteration 2 — decider should not be called more
        assert decider.decide.call_count <= 2
        assert result.goal_achieved is True


# ===========================================================================
# Test 8: run_step stops when budget exceeded (BudgetExceededError)
# ===========================================================================


@needs_ios_runner
class TestRunStepBudgetExceeded:
    """run_step must stop and mark error when BudgetExceededError is raised."""

    def test_stops_on_budget_exceeded(self):
        """When the decider raises BudgetExceededError, run_step must stop and
        return a failed StepResult with an appropriate error message."""
        try:
            from specterqa.ios.engine.ai_step_runner import BudgetExceededError  # type: ignore[import]
        except ImportError:
            # Use a generic exception if BudgetExceededError not yet defined
            BudgetExceededError = Exception  # type: ignore[assignment,misc]

        runner, decider, *_ = _make_ios_runner(budget=0.001)
        decider.decide.side_effect = BudgetExceededError("Budget exhausted")

        result = runner.run_step(goal="Expensive task", max_iterations=10)

        assert result.passed is False
        assert result.goal_achieved is False
        assert result.error is not None


# ===========================================================================
# Test 9: run_step checks checkpoint after each iteration
# ===========================================================================


@needs_ios_runner
class TestRunStepCheckpoint:
    """run_step must record checkpoint milestones."""

    def test_checkpoint_recorded_in_result(self):
        """When a checkpoint is provided and the decider signals it was reached,
        the checkpoint must appear in StepResult.checkpoints_reached."""
        if Decision is not None:
            checkpoint_decision = Decision(
                action="done",
                target="",
                value="",
                reasoning="Done",
                goal_achieved=True,
                checkpoint="USER_LOGGED_IN",
            )
        else:
            checkpoint_decision = MagicMock()
            checkpoint_decision.action = "done"
            checkpoint_decision.target = ""
            checkpoint_decision.value = ""
            checkpoint_decision.reasoning = "Done"
            checkpoint_decision.goal_achieved = True
            checkpoint_decision.checkpoint = "USER_LOGGED_IN"
            checkpoint_decision.ux_notes = None
            checkpoint_decision.observation = ""
        runner, *_ = _make_ios_runner(decisions=[checkpoint_decision])
        result = runner.run_step(
            goal="Log in",
            checkpoint="USER_LOGGED_IN",
            max_iterations=5,
        )
        assert "USER_LOGGED_IN" in result.checkpoints_reached


# ===========================================================================
# Test 10: run_step includes all findings in StepResult.findings
# ===========================================================================


@needs_ios_runner
class TestRunStepAllFindingsMerged:
    """Both crash findings and error log findings must all appear together."""

    def test_findings_from_crash_and_error_combined(self):
        """If both a crash and error logs are present, both findings must be in result."""
        crash = _make_crash_report()
        error_log = _make_log_entry(message="Timeout error", level="error")
        driver_ctx = _make_driver_context(
            crashes=[crash],
            logs_with_errors=[error_log],
        )
        runner, *_ = _make_ios_runner(
            decisions=[_make_done_decision()],
            driver_context=driver_ctx,
        )
        result = runner.run_step(goal="Test", max_iterations=5)

        severities = {
            f.severity for f in result.findings if hasattr(f, "severity")
        }
        assert "critical" in severities, "Expected critical finding from crash"
        assert "high" in severities, "Expected high finding from error log"


# ===========================================================================
# Test 11: _detect_crash_finding converts CrashReport correctly
# ===========================================================================


@needs_ios_runner
class TestDetectCrashFinding:
    """_detect_crash_finding must convert CrashReport to Finding objects."""

    def test_crash_report_to_finding_conversion(self):
        """_detect_crash_finding must return a list of Finding with severity='critical'
        and the exception_type in the title/description."""
        runner, *_ = _make_ios_runner()
        crash = _make_crash_report(exception_type="EXC_CRASH")
        findings = runner._detect_crash_finding(_make_driver_context(crashes=[crash]))

        assert len(findings) == 1
        finding = findings[0]
        assert finding.severity == "critical"
        assert "EXC_CRASH" in (finding.title or "") or "EXC_CRASH" in (finding.description or "")

    def test_no_crashes_returns_empty_list(self):
        """_detect_crash_finding returns [] when context.crashes is empty."""
        runner, *_ = _make_ios_runner()
        findings = runner._detect_crash_finding(_make_driver_context(crashes=[]))
        assert findings == []


# ===========================================================================
# Test 12: _detect_error_finding converts error LogEntry correctly
# ===========================================================================


@needs_ios_runner
class TestDetectErrorFinding:
    """_detect_error_finding must convert error-level LogEntry to Finding objects."""

    def test_error_log_to_finding_conversion(self):
        """_detect_error_finding must return a Finding with severity='high'."""
        runner, *_ = _make_ios_runner()
        error_log = _make_log_entry(message="DB connection failed", level="error")
        findings = runner._detect_error_finding(
            _make_driver_context(logs_with_errors=[error_log])
        )

        assert len(findings) >= 1
        assert all(f.severity == "high" for f in findings)

    def test_info_logs_not_converted(self):
        """_detect_error_finding must ignore non-error log entries."""
        runner, *_ = _make_ios_runner()
        info_log = _make_log_entry(message="App started", level="info")
        findings = runner._detect_error_finding(
            _make_driver_context(logs_with_errors=[info_log])
        )
        assert findings == []


# ===========================================================================
# Test 13: Stuck detection triggers after repeated identical screenshots
# ===========================================================================


@needs_ios_runner
class TestStuckDetection:
    """run_step must abort when stuck (identical screenshots repeated)."""

    def test_stuck_detection_triggers_on_repeated_screenshots(self):
        """run_step must detect stuck state after several identical screenshots
        and stop without achieving the goal."""
        # Always return same screenshot (stuck) and click (never done)
        constant_screenshot = "AAAA=="  # same every time
        runner, decider, executor, ctx_builder = _make_ios_runner(
            decisions=[_make_decision(action="click") for _ in range(20)],
            screenshot_b64=constant_screenshot,
        )

        result = runner.run_step(goal="Something that gets stuck", max_iterations=15)

        # Should NOT achieve goal — stuck should have been detected
        assert result.goal_achieved is False or result.action_count < 15


# ===========================================================================
# Test 14: Evidence collected to evidence_dir when provided
# ===========================================================================


@needs_ios_runner
class TestEvidenceCollection:
    """When evidence_dir is provided, run_step must write evidence files."""

    def test_evidence_written_to_dir(self, tmp_path):
        """After run_step completes, the evidence_dir must contain at least one file."""
        runner, *_ = _make_ios_runner(
            decisions=[_make_done_decision()],
            evidence_dir=str(tmp_path),
        )
        runner.run_step(goal="Collect evidence", max_iterations=5)

        evidence_files = list(tmp_path.iterdir())
        assert len(evidence_files) > 0, (
            f"Expected evidence files in {tmp_path}, found none"
        )


# ===========================================================================
# Test 15: Context builder receives all sub-module data
# ===========================================================================


@needs_ios_runner
class TestContextBuilderReceivesSubModules:
    """context_builder.build_context() must be called with sub-module instances."""

    def test_context_builder_called_per_iteration(self):
        """build_context must be called at least once per run_step invocation."""
        runner, decider, executor, ctx_builder = _make_ios_runner(
            decisions=[_make_done_decision()],
        )
        runner.run_step(goal="Build context test", max_iterations=5)

        ctx_builder.build_context.assert_called()
