#!/usr/bin/env python3
"""SimDrive 1.0 Cycle 1 — scripted smoke test for the journey runner.

Purpose: verify end-to-end wiring of the journey runner WITHOUT making
real Anthropic API calls or needing a live iOS simulator.

Strategy:
  1. Construct a fake LLMClient that returns a scripted [StepDecision(tool="done")]
  2. Patch runner.tool_observe to return a fake observation with "Home" visible
  3. Load the fixture journey + persona YAMLs
  4. Call run_journey() with a MagicMock session
  5. Assert outcome == "passed"

Exits 0 on pass, 1 on failure.
"""
from __future__ import annotations

import sys
import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

# Ensure simdrive package is importable when run from repo root.
_REPO = Path(__file__).parent.parent
_SIMDRIVE_SRC = _REPO / "simdrive" / "src"
if str(_SIMDRIVE_SRC) not in sys.path:
    sys.path.insert(0, str(_SIMDRIVE_SRC))

from specterqa_ios.journey.schema import load_journey
from specterqa_ios.journey.persona import load_persona
from specterqa_ios.journey.runner import run_journey, StepDecision, LLMClient

# ── Fixture paths ─────────────────────────────────────────────────────────────

_FIXTURE_DIR = _REPO / "simdrive" / "tests" / "fixtures" / "journey_cycle1_smoke"
_JOURNEY_PATH = _FIXTURE_DIR / "journey.yaml"
_PERSONA_PATH = _FIXTURE_DIR / "persona.yaml"


# ── Fake LLM client ───────────────────────────────────────────────────────────


class FakeLLMClient:
    """Scripted LLM client — returns 'done' on first call.

    Conforms to the LLMClient Protocol so the runner accepts it directly.
    The runner will:
      1. Call tool_observe (mocked to return obs with "Home" visible).
      2. Evaluate criteria — text_visible: "Home" passes immediately.
      3. Never reach the LLM call because all criteria pass before that step.

    We still implement call() + cost_usd so the Protocol is satisfied in
    cases where an LLM call is needed (e.g. if success criteria don't pass
    on first observe).
    """

    def __init__(self) -> None:
        self._cost: float = 0.0
        self._calls: int = 0
        self._decisions: list[StepDecision] = [
            StepDecision(
                tool="done",
                args={},
                rationale="Smoke: all criteria satisfied",
                confidence=1.0,
            )
        ]

    def call(self, system_prompt: str, user_prompt: str, screenshot_path):
        if self._calls >= len(self._decisions):
            return StepDecision(tool="done", args={}, rationale="fallback done", confidence=1.0)
        decision = self._decisions[self._calls]
        self._calls += 1
        self._cost += 0.001  # nominal fake cost
        return decision

    @property
    def cost_usd(self) -> float:
        return self._cost


# ── Fake observation (contains "Home" text so text_visible criterion passes) ──


def _fake_observe(arguments: dict) -> dict:
    return {
        "ok": True,
        "screenshot_path": None,
        "marks": [
            {"id": 1, "text": "Home", "bbox": [0, 0, 100, 50], "center": [50, 25], "stable_id": "home-1"},
            {"id": 2, "text": "Settings", "bbox": [100, 0, 200, 50], "center": [150, 25], "stable_id": "settings-1"},
        ],
        "device_w": 390,
        "device_h": 844,
        "app_state": "foreground",
    }


def _fake_tool_crashes(arguments: dict) -> dict:
    return {"crashes": []}


def _fake_tool_perf(arguments: dict) -> dict:
    return {"cpu_pct": 1.0, "memory_mb": 50.0}


def _fake_tool_perf_baseline(arguments: dict) -> dict:
    return {"label": "default", "cpu_pct": 1.0, "memory_mb": 50.0}


# ── Main ──────────────────────────────────────────────────────────────────────


def main() -> int:
    print("SimDrive Cycle 1 smoke — loading fixtures...")
    assert _JOURNEY_PATH.exists(), f"journey fixture not found: {_JOURNEY_PATH}"
    assert _PERSONA_PATH.exists(), f"persona fixture not found: {_PERSONA_PATH}"

    journey = load_journey(_JOURNEY_PATH)
    persona = load_persona(_PERSONA_PATH)

    print(f"  journey: {journey.name!r}  persona: {persona.name!r}")

    # Build a MagicMock session with required attributes.
    fake_session = MagicMock()
    fake_session.session_id = "smoke-session-001"
    fake_session.started_at = 0.0

    llm_client = FakeLLMClient()

    # Use a temp dir for artifacts so the smoke run doesn't litter ~/.simdrive.
    with tempfile.TemporaryDirectory(prefix="simdrive_smoke_") as tmpdir:
        artifact_dir = Path(tmpdir) / "smoke_run"

        # Patch the module-level tool_* references in runner so no real sim is needed.
        with (
            patch("specterqa_ios.journey.runner.tool_observe", side_effect=_fake_observe),
            patch("specterqa_ios.journey.runner.tool_crashes", side_effect=_fake_tool_crashes),
            patch("specterqa_ios.journey.runner.tool_perf", side_effect=_fake_tool_perf),
            patch("specterqa_ios.journey.runner.tool_perf_baseline", side_effect=_fake_tool_perf_baseline),
        ):
            result = run_journey(
                journey=journey,
                persona=persona,
                session=fake_session,
                llm_client=llm_client,
                artifact_dir_override=artifact_dir,
                _recorder_module=None,  # disable recorder in smoke
            )

    print(f"  outcome:         {result.outcome}")
    print(f"  steps_executed:  {result.steps_executed}")
    print(f"  llm_calls:       {result.llm_calls}")
    print(f"  llm_cost_usd:    ${result.llm_cost_usd:.4f}")
    print(f"  duration:        {result.duration_seconds:.3f}s")
    for ce in result.success_criteria:
        icon = "PASS" if ce.passed else "FAIL"
        print(f"  criterion [{icon}]: {ce.criterion_type} — {ce.detail}")

    if result.outcome != "passed":
        print(f"\nSMOKE FAILED: outcome={result.outcome!r}  reason={result.failure_reason!r}")
        return 1

    print("\nSMOKE PASSED: outcome=passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
