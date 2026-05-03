"""Edge-case tests for journey/runner.py — Component 9.

Covers:
  - Budget exhaustion at exactly the limit (max_steps, max_seconds, max_llm_calls)
  - LLM returning unparseable JSON (via exception from llm_client.call)
  - Mid-journey crash detection
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Optional
from unittest.mock import MagicMock, patch


from simdrive.journey.persona import Persona, AccessibilityNeeds
from simdrive.journey.runner import StepDecision, run_journey
from simdrive.journey.schema import Budget, Journey, SuccessCriterion


# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_journey(
    *,
    name: str = "edge-test-journey",
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
        success_criteria=success_criteria
        or [SuccessCriterion(text_visible="Home")],
        budget=budget or Budget(),
    )


def _make_persona() -> Persona:
    return Persona(
        schema_version=1,
        slug="test-user",
        name="Test User",
        role="tester",
        technical_comfort="expert",
        patience="high",
        goals=["test the app"],
        frustrations=[],
        accessibility_needs=AccessibilityNeeds(),
    )


def _make_session(session_id: str = "sess-001") -> MagicMock:
    session = MagicMock()
    session.session_id = session_id
    session.started_at = time.time() - 1
    session.recorder = None
    return session


class _ScriptedLLM:
    """LLM client that returns a scripted list of decisions."""

    def __init__(self, decisions: list[StepDecision]) -> None:
        self._decisions = list(decisions)
        self._idx = 0
        self.cost_usd = 0.0

    def call(
        self,
        system_prompt: str,
        user_prompt: str,
        screenshot_path: Optional[str],
    ) -> StepDecision:
        if self._idx >= len(self._decisions):
            return StepDecision(tool="done", args={}, rationale="no more steps", confidence=1.0)
        dec = self._decisions[self._idx]
        self._idx += 1
        self.cost_usd += 0.001
        return dec


class _RaisingLLM:
    """LLM client that raises on call — simulates network/parse failure."""

    def __init__(self, exc: Exception) -> None:
        self._exc = exc
        self.cost_usd = 0.0

    def call(self, *args, **kwargs) -> StepDecision:
        raise self._exc


# ── Standard observe/crashes mocks ────────────────────────────────────────────


def _patch_tools_no_criteria_met():
    """Context: observe returns no marks + no text → criteria never met."""
    obs = {"text": "", "marks": [], "screenshot_path": "/tmp/obs.png"}
    perf = {"cpu_pct": 5.0, "memory_mb": 100.0}
    crashes = {"crashes": []}
    return (
        patch("simdrive.journey.runner.tool_observe", return_value=obs),
        patch("simdrive.journey.runner.tool_perf", return_value=perf),
        patch("simdrive.journey.runner.tool_crashes", return_value=crashes),
        patch("simdrive.journey.runner.tool_perf_baseline", return_value=perf),
    )


# ── Tests ─────────────────────────────────────────────────────────────────────


class TestBudgetExhaustion:
    def test_exactly_at_max_steps_limit(self, tmp_path: Path) -> None:
        """Runner stops at exactly max_steps, outcome=budget_exceeded."""
        budget = Budget(max_steps=3, max_seconds=300, max_llm_calls=100)
        journey = _make_journey(budget=budget)
        persona = _make_persona()
        session = _make_session()

        # LLM always returns a tap (never done) → forces budget exhaustion
        tap_decision = StepDecision(
            tool="tap", args={"x": 100, "y": 200, "screenshot_w": 390, "screenshot_h": 844},
            rationale="tap", confidence=0.9
        )
        llm = _ScriptedLLM([tap_decision] * 10)

        patcher_obs, patcher_perf, patcher_crashes, patcher_baseline = _patch_tools_no_criteria_met()
        with patcher_obs, patcher_perf, patcher_crashes, patcher_baseline:
            with patch("simdrive.journey.runner.tool_tap", return_value={"ok": True}):
                result = run_journey(
                    journey, persona, session, llm,
                    artifact_dir_override=tmp_path,
                    _recorder_module=None,
                )

        assert result.outcome == "budget_exceeded"
        assert result.steps_executed == 3

    def test_exactly_at_max_llm_calls_limit(self, tmp_path: Path) -> None:
        """Runner stops at exactly max_llm_calls, outcome=budget_exceeded."""
        budget = Budget(max_steps=100, max_seconds=300, max_llm_calls=2)
        journey = _make_journey(budget=budget)
        persona = _make_persona()
        session = _make_session()

        tap_decision = StepDecision(
            tool="tap", args={"x": 100, "y": 200, "screenshot_w": 390, "screenshot_h": 844},
            rationale="tap", confidence=0.9
        )
        llm = _ScriptedLLM([tap_decision] * 10)

        patcher_obs, patcher_perf, patcher_crashes, patcher_baseline = _patch_tools_no_criteria_met()
        with patcher_obs, patcher_perf, patcher_crashes, patcher_baseline:
            with patch("simdrive.journey.runner.tool_tap", return_value={"ok": True}):
                result = run_journey(
                    journey, persona, session, llm,
                    artifact_dir_override=tmp_path,
                    _recorder_module=None,
                )

        assert result.outcome == "budget_exceeded"
        assert result.llm_calls == 2


class TestLLMUnparseableJSON:
    def test_llm_raises_json_parse_error(self, tmp_path: Path) -> None:
        """When LLM raises (simulating unparseable JSON), outcome=error."""
        journey = _make_journey()
        persona = _make_persona()
        session = _make_session()

        exc = ValueError("Unparseable JSON from LLM: {unterminated}")
        llm = _RaisingLLM(exc)

        patcher_obs, patcher_perf, patcher_crashes, patcher_baseline = _patch_tools_no_criteria_met()
        with patcher_obs, patcher_perf, patcher_crashes, patcher_baseline:
            result = run_journey(
                journey, persona, session, llm,
                artifact_dir_override=tmp_path,
                _recorder_module=None,
            )

        assert result.outcome == "error"
        assert result.failure_reason is not None
        assert "claude_call_failed" in result.failure_reason or "Unparseable" in result.failure_reason

    def test_llm_raises_network_error(self, tmp_path: Path) -> None:
        """Network-level errors are also surfaced as outcome=error."""
        journey = _make_journey()
        persona = _make_persona()
        session = _make_session()

        exc = ConnectionError("Network unreachable")
        llm = _RaisingLLM(exc)

        patcher_obs, patcher_perf, patcher_crashes, patcher_baseline = _patch_tools_no_criteria_met()
        with patcher_obs, patcher_perf, patcher_crashes, patcher_baseline:
            result = run_journey(
                journey, persona, session, llm,
                artifact_dir_override=tmp_path,
                _recorder_module=None,
            )

        assert result.outcome == "error"


class TestMidJourneyCrashDetection:
    def test_crash_detected_mid_journey(self, tmp_path: Path) -> None:
        """When tool_crashes returns a crash entry, outcome=crashed."""
        journey = _make_journey()
        persona = _make_persona()
        session = _make_session()

        # LLM would tap but crash is detected first
        tap_decision = StepDecision(
            tool="tap", args={"x": 50, "y": 100, "screenshot_w": 390, "screenshot_h": 844},
            rationale="tap", confidence=0.9,
        )
        llm = _ScriptedLLM([tap_decision] * 5)

        obs = {"text": "", "marks": [], "screenshot_path": "/tmp/obs.png"}
        perf = {"cpu_pct": 5.0, "memory_mb": 100.0}
        crashes_with_crash = {
            "crashes": [{"path": "/var/mobile/Containers/Data/...app_crash.ips"}]
        }

        with (
            patch("simdrive.journey.runner.tool_observe", return_value=obs),
            patch("simdrive.journey.runner.tool_perf", return_value=perf),
            patch("simdrive.journey.runner.tool_crashes", return_value=crashes_with_crash),
            patch("simdrive.journey.runner.tool_perf_baseline", return_value=perf),
        ):
            result = run_journey(
                journey, persona, session, llm,
                artifact_dir_override=tmp_path,
                _recorder_module=None,
            )

        assert result.outcome == "crashed"
        assert result.failure_reason is not None
        assert "crashed" in result.failure_reason or "crash" in result.failure_reason.lower()

    def test_crash_failure_reason_includes_path(self, tmp_path: Path) -> None:
        """The crash path should appear in the failure_reason."""
        journey = _make_journey()
        persona = _make_persona()
        session = _make_session()
        llm = _ScriptedLLM([])

        obs = {"text": "", "marks": [], "screenshot_path": "/tmp/obs.png"}
        perf = {"cpu_pct": 5.0, "memory_mb": 100.0}
        crash_path = "/var/mobile/Containers/Data/crash_report.ips"
        crashes_with_crash = {"crashes": [{"path": crash_path}]}

        with (
            patch("simdrive.journey.runner.tool_observe", return_value=obs),
            patch("simdrive.journey.runner.tool_perf", return_value=perf),
            patch("simdrive.journey.runner.tool_crashes", return_value=crashes_with_crash),
            patch("simdrive.journey.runner.tool_perf_baseline", return_value=perf),
        ):
            result = run_journey(
                journey, persona, session, llm,
                artifact_dir_override=tmp_path,
                _recorder_module=None,
            )

        assert result.outcome == "crashed"
        # The crash path or "unknown" should appear in failure_reason
        assert crash_path in result.failure_reason or "crash" in result.failure_reason.lower()
