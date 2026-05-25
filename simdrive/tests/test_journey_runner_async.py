"""TDD tests for the async contract of run_journey — [internal-tracker].

The MCP sampling refactor converts run_journey from a synchronous function
to an async coroutine so it can await MCPSamplingLLMClient.call(). These
tests pin the new async contract.

ALL tests in this file must FAIL until engineering:
  1. Makes LLMClient.call an async def
  2. Makes run_journey an async def (coroutine function)
  3. Updates the runner loop to `await llm_client.call(...)`

We use asyncio.run() in test bodies to avoid requiring pytest-asyncio.
"""
from __future__ import annotations

import asyncio
import inspect
import time
from pathlib import Path
from typing import Optional
from unittest.mock import MagicMock, patch

import pytest

from simdrive.journey.persona import Persona
from simdrive.journey.runner import LLMClient, StepDecision, run_journey
from simdrive.journey.schema import Budget, Journey, SuccessCriterion


# ---------------------------------------------------------------------------
# Shared fixtures (mirrors style in test_journey_runner.py)
# ---------------------------------------------------------------------------


def _make_journey(
    *,
    name: str = "async-test-journey",
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
        name="Async Test User",
        role="A test persona for async runner tests",
    )


def _make_session(session_id: str = "async-test-session-001") -> MagicMock:
    s = MagicMock()
    s.session_id = session_id
    s.started_at = time.time()
    return s


_OBS_WITH_HOME = {
    "marks": [{"stable_id": "sid001", "text": "Home", "id": 0}],
    "screenshot_path": "/tmp/obs.png",
}

_OBS_EMPTY = {
    "marks": [],
    "screenshot_path": "/tmp/obs_empty.png",
}


# ---------------------------------------------------------------------------
# FakeAsyncLLMClient — async-compatible fake that matches the new Protocol
# ---------------------------------------------------------------------------


class FakeAsyncLLMClient:
    """Scripted async LLM client — conforms to the NEW async LLMClient Protocol.

    call() is an async def — this is the critical change the refactor requires.
    All existing behaviour (scripted decisions, cost tracking) is preserved.
    """

    def __init__(self, decisions: list[StepDecision]):
        self._decisions = list(decisions)
        self._idx = 0
        self._cost = 0.0

    async def call(
        self,
        system_prompt: str,
        user_prompt: str,
        screenshot_path: Optional[str],
    ) -> StepDecision:
        if self._idx >= len(self._decisions):
            raise RuntimeError("FakeAsyncLLMClient ran out of scripted decisions")
        d = self._decisions[self._idx]
        self._idx += 1
        self._cost += 0.004
        return d

    @property
    def cost_usd(self) -> float:
        return self._cost


# ---------------------------------------------------------------------------
# Test 1: run_journey is a coroutine function
# ---------------------------------------------------------------------------


class TestRunJourneyIsAsync:
    def test_run_journey_is_coroutine_function(self):
        """inspect.iscoroutinefunction(run_journey) must be True after refactor.

        This test fails until run_journey is converted to `async def run_journey(...)`.
        """
        assert inspect.iscoroutinefunction(run_journey), (
            "run_journey must be an async def (coroutine function) after the "
            "MCP sampling refactor.  Currently it is a sync function.  "
            "engineering: convert `def run_journey(...)` to `async def run_journey(...)`."
        )

    def test_run_journey_returns_awaitable(self):
        """Calling run_journey(...) without await must return a coroutine object,
        not a RunResult.

        This test fails until run_journey is async.
        """
        journey = _make_journey()
        persona = _make_persona()
        session = _make_session()
        client = FakeAsyncLLMClient([
            StepDecision(tool="done", args={}, rationale="ok", confidence=1.0),
        ])

        # Don't await — just check the return type is a coroutine
        coro = run_journey(journey, persona, session, client, _recorder_module=None)
        try:
            assert inspect.iscoroutine(coro), (
                f"run_journey(...) must return a coroutine; got {type(coro).__name__}"
            )
        finally:
            # Clean up the unawaited coroutine to prevent ResourceWarning.
            if inspect.iscoroutine(coro):
                coro.close()


# ---------------------------------------------------------------------------
# Test 2: LLMClient Protocol call is async
# ---------------------------------------------------------------------------


class TestLLMClientProtocolIsAsync:
    def test_llmclient_protocol_call_is_async(self):
        """The LLMClient Protocol's call method must be declared as async def.

        This pins the Protocol contract so mypy can enforce that all
        implementations (ClaudeLLMClient, MCPSamplingLLMClient, FakeAsyncLLMClient)
        define an async call().

        We inspect LLMClient.call directly — for Protocols with a body (even
        an ellipsis body), inspect.iscoroutinefunction works when the method is
        declared as async def.  On Python 3.9 Protocol.__protocol_attrs__ does
        not exist; we skip that attribute lookup.
        """
        is_async = inspect.iscoroutinefunction(LLMClient.call)
        assert is_async, (
            "LLMClient.call must be declared as `async def call(...)` in the Protocol. "
            "This allows mypy to enforce that all implementations are async. "
            "Currently it is a sync def — engineering: change to `async def call(...)`."
        )


# ---------------------------------------------------------------------------
# Test 3: async run_journey with FakeAsyncLLMClient
# ---------------------------------------------------------------------------


class TestRunJourneyAsyncWithFakeClient:
    """End-to-end tests for the async run_journey using FakeAsyncLLMClient."""

    def test_run_journey_async_with_fake_async_client_passed(self, tmp_path: Path):
        """Async run_journey with a fake async client that says 'done' → 'passed'.

        This mirrors test_llm_done_decision_gives_passed from test_journey_runner.py
        but uses the async client + asyncio.run().
        """
        journey = _make_journey()
        persona = _make_persona()
        session = _make_session()
        client = FakeAsyncLLMClient([
            StepDecision(tool="done", args={}, rationale="criteria met", confidence=1.0),
        ])

        with (
            patch("simdrive.journey.runner.tool_observe", return_value=_OBS_WITH_HOME),
            patch("simdrive.journey.runner.tool_perf", return_value={"cpu_pct": 10.0}),
            patch("simdrive.journey.runner.tool_crashes", return_value={"crashes": []}),
            patch("simdrive.journey.runner.tool_perf_baseline", return_value={}),
        ):
            result = asyncio.run(
                run_journey(
                    journey, persona, session, client,
                    artifact_dir_override=tmp_path / "async_run1",
                    _recorder_module=None,
                )
            )

        assert result.outcome == "passed"
        assert result.journey_name == "async-test-journey"

    def test_run_journey_async_with_fake_async_client_failed(self, tmp_path: Path):
        """Async run_journey with a fake async client that says 'fail' → 'failed'."""
        journey = _make_journey(
            success_criteria=[SuccessCriterion(text_visible="NEVER_VISIBLE")]
        )
        persona = _make_persona()
        session = _make_session()
        client = FakeAsyncLLMClient([
            StepDecision(tool="fail", args={}, rationale="app is broken", confidence=0.9),
        ])

        with (
            patch("simdrive.journey.runner.tool_observe", return_value=_OBS_EMPTY),
            patch("simdrive.journey.runner.tool_perf", return_value={}),
            patch("simdrive.journey.runner.tool_crashes", return_value={"crashes": []}),
            patch("simdrive.journey.runner.tool_perf_baseline", return_value={}),
        ):
            result = asyncio.run(
                run_journey(
                    journey, persona, session, client,
                    artifact_dir_override=tmp_path / "async_run_fail",
                    _recorder_module=None,
                )
            )

        assert result.outcome == "failed"
        assert "app is broken" in (result.failure_reason or "")

    def test_run_journey_async_budget_exceeded(self, tmp_path: Path):
        """Async run_journey respects max_steps budget."""
        journey = _make_journey(
            success_criteria=[SuccessCriterion(text_visible="NEVER_VISIBLE")],
            budget=Budget(max_steps=2, max_seconds=300, max_llm_calls=100),
        )
        persona = _make_persona()
        session = _make_session()
        client = FakeAsyncLLMClient([
            StepDecision(tool="tap", args={"x": 100, "y": 200}, rationale="try", confidence=0.8),
            StepDecision(tool="tap", args={"x": 100, "y": 200}, rationale="try", confidence=0.8),
            StepDecision(tool="tap", args={"x": 100, "y": 200}, rationale="try", confidence=0.8),
        ])

        with (
            patch("simdrive.journey.runner.tool_observe", return_value=_OBS_EMPTY),
            patch("simdrive.journey.runner.tool_perf", return_value={}),
            patch("simdrive.journey.runner.tool_crashes", return_value={"crashes": []}),
            patch("simdrive.journey.runner.tool_perf_baseline", return_value={}),
            patch("simdrive.journey.runner.tool_tap", return_value={"ok": True}),
        ):
            result = asyncio.run(
                run_journey(
                    journey, persona, session, client,
                    artifact_dir_override=tmp_path / "async_budget",
                    _recorder_module=None,
                )
            )

        assert result.outcome == "budget_exceeded"

    def test_run_journey_async_returns_run_result(self, tmp_path: Path):
        """Async run_journey returns a RunResult (not a coroutine or None)."""
        from simdrive.journey.result import RunResult

        journey = _make_journey()
        persona = _make_persona()
        session = _make_session()
        client = FakeAsyncLLMClient([
            StepDecision(tool="done", args={}, rationale="done", confidence=1.0),
        ])

        with (
            patch("simdrive.journey.runner.tool_observe", return_value=_OBS_WITH_HOME),
            patch("simdrive.journey.runner.tool_perf", return_value={}),
            patch("simdrive.journey.runner.tool_crashes", return_value={"crashes": []}),
            patch("simdrive.journey.runner.tool_perf_baseline", return_value={}),
        ):
            result = asyncio.run(
                run_journey(
                    journey, persona, session, client,
                    artifact_dir_override=tmp_path / "async_return_type",
                    _recorder_module=None,
                )
            )

        assert isinstance(result, RunResult), (
            f"run_journey must return a RunResult, got {type(result).__name__}"
        )
