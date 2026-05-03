"""Benchmark: full journey loop throughput with mocked LLM.

Measures the Python-layer cost of one run_journey() invocation (N steps)
with all real I/O mocked out. Captures the overhead from the new observability
log calls, metrics recording, and tracing spans.

Run with:
    pytest simdrive/tests/perf/bench_journey_throughput.py -v

Regression gate: 2× baseline from bench_baselines.json.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional
from unittest.mock import MagicMock, patch

import pytest

import json
from pathlib import Path as _Path

_BASELINES_PATH = _Path(__file__).parent / "bench_baselines.json"


def _check_regression(metric_name: str, measured_p95_ms: float) -> None:
    """Fail if measured p95 exceeds 2× the baseline."""
    if not _BASELINES_PATH.exists():
        return
    baselines = json.loads(_BASELINES_PATH.read_text())
    if metric_name not in baselines:
        return
    baseline_p95 = baselines[metric_name]["p95_ms"]
    limit = baseline_p95 * 2.0
    assert measured_p95_ms <= limit, (
        f"REGRESSION: {metric_name} p95={measured_p95_ms:.1f}ms "
        f"exceeds 2× baseline ({baseline_p95:.1f}ms → limit {limit:.1f}ms)."
    )

N_ITERATIONS = 100
STEPS_PER_JOURNEY = 5  # short journey so total runtime is bounded


@dataclass
class _StepDecision:
    tool: str
    args: dict
    rationale: str
    confidence: float


class _MeasuredLLM:
    """LLM client that returns scripted tap decisions and tracks calls."""

    def __init__(self, steps: int) -> None:
        self._remaining = steps
        self.cost_usd = 0.0

    def call(self, system_prompt: str, user_prompt: str, screenshot_path: Optional[str]) -> _StepDecision:
        self._remaining -= 1
        self.cost_usd += 0.001
        if self._remaining <= 0:
            return _StepDecision(tool="done", args={}, rationale="done", confidence=1.0)
        return _StepDecision(
            tool="tap",
            args={"x": 100, "y": 200, "screenshot_w": 390, "screenshot_h": 844},
            rationale="tap",
            confidence=0.9,
        )


def _p95(data: list[float]) -> float:
    sorted_data = sorted(data)
    idx = max(0, int(0.95 * len(sorted_data)) - 1)
    return sorted_data[idx]


@pytest.mark.perf
def test_journey_loop_step_p95(tmp_path: Path) -> None:
    """Journey loop step p95 must not exceed 2× baseline."""
    from simdrive.journey.runner import run_journey
    from simdrive.journey.schema import Budget, Journey, SuccessCriterion
    from simdrive.journey.persona import Persona

    journey = Journey(
        schema_version=1,
        name="bench-journey",
        persona="bench-user",
        target="simulator",
        goals=["tap the button"],
        success_criteria=[SuccessCriterion(text_visible="Done")],
        budget=Budget(max_steps=STEPS_PER_JOURNEY, max_seconds=300, max_llm_calls=STEPS_PER_JOURNEY + 5),
    )
    persona = Persona(
        schema_version=1,
        slug="bench-user",
        name="Bench User",
        role="performance tester",
        technical_comfort="expert",
        patience="high",
        goals=["test performance"],
    )

    obs = {"text": "", "marks": [], "screenshot_path": "/tmp/bench.png"}
    perf = {"cpu_pct": 5.0, "memory_mb": 100.0}
    crashes = {"crashes": []}

    latencies: list[float] = []

    for i in range(N_ITERATIONS):
        session = MagicMock()
        session.session_id = f"bench-sess-{i}"
        session.started_at = time.time() - 1
        session.recorder = None
        llm = _MeasuredLLM(STEPS_PER_JOURNEY)

        with (
            patch("simdrive.journey.runner.tool_observe", return_value=obs),
            patch("simdrive.journey.runner.tool_perf", return_value=perf),
            patch("simdrive.journey.runner.tool_crashes", return_value=crashes),
            patch("simdrive.journey.runner.tool_perf_baseline", return_value=perf),
            patch("simdrive.journey.runner.tool_tap", return_value={"ok": True}),
        ):
            t0 = time.perf_counter()
            result = run_journey(
                journey, persona, session, llm,
                artifact_dir_override=tmp_path / f"run-{i}",
                _recorder_module=None,
            )
            elapsed_ms = (time.perf_counter() - t0) * 1000.0
        latencies.append(elapsed_ms)

    total_steps = N_ITERATIONS * STEPS_PER_JOURNEY
    per_step_latencies = [ms / STEPS_PER_JOURNEY for ms in latencies]
    p95_step = _p95(per_step_latencies)
    p50_step = _p95(per_step_latencies[:len(per_step_latencies)//2])

    print(
        f"\njourney_loop_step: n={total_steps} steps, "
        f"p50={p50_step:.2f}ms/step, p95={p95_step:.2f}ms/step"
    )
    _check_regression("journey_loop_step", p95_step)

    # Hard cap: mocked step overhead should never exceed 50ms
    assert p95_step < 50.0, f"journey step p95={p95_step:.1f}ms exceeds absolute cap of 50ms"
