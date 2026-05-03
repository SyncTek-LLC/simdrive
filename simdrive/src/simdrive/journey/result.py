"""RunResult and CriterionEval dataclasses — Component 3 output types.

These are the stable output contracts for run_journey() and the CI
orchestrator. Keep them serialisable to JSON for artifact writing.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Literal, Optional


OutcomeType = Literal["passed", "failed", "budget_exceeded", "crashed", "error"]


@dataclass
class CriterionEval:
    """Result of evaluating one SuccessCriterion entry."""

    criterion_type: str  # "text_visible" | "screen_matches" | "perf_under" | "no_crash" | "cross_device_state_matches"
    passed: bool
    detail: str  # human-readable explanation — always present
    # Raw observed value compared against; None for boolean criteria.
    observed_value: Optional[Any] = None


@dataclass
class StepRecord:
    """One executed step in the journey — appended to agent_trace.jsonl."""

    step_idx: int
    tool: str
    args: dict
    rationale: str
    confidence: float
    elapsed_seconds: float
    # Outcome after executing this step's action
    success_criteria_snapshot: list[CriterionEval] = field(default_factory=list)
    error: Optional[str] = None


@dataclass
class RunResult:
    """Complete outcome of run_journey().

    Mirrors summary.json in the artifact directory.  All fields are
    primitive-or-list so json.dumps(asdict(result)) is always valid.
    """

    outcome: OutcomeType
    journey_name: str
    persona_name: str
    steps_executed: int
    llm_calls: int
    llm_cost_usd: float
    duration_seconds: float
    success_criteria: list[CriterionEval] = field(default_factory=list)
    replay_id: Optional[str] = None
    artifact_dir: Optional[Path] = None
    # Human-readable failure description when outcome != "passed"
    failure_reason: Optional[str] = None
    # Per-step trace — also written as agent_trace.jsonl
    steps: list[StepRecord] = field(default_factory=list)

    def to_dict(self) -> dict:
        """Serialise to a JSON-safe dict (Path → str, unknown types → repr str)."""
        d = asdict(self)
        if d.get("artifact_dir") is not None:
            d["artifact_dir"] = str(self.artifact_dir)
        # Sanitize observed_value in criteria evals — may contain arbitrary objects
        # from perf snapshots or other tools that aren't JSON-native.
        for ce in d.get("success_criteria", []):
            if ce.get("observed_value") is not None:
                try:
                    import json as _json
                    _json.dumps(ce["observed_value"])
                except (TypeError, ValueError):
                    ce["observed_value"] = repr(ce["observed_value"])
        for step in d.get("steps", []):
            for ce in step.get("success_criteria_snapshot", []):
                if ce.get("observed_value") is not None:
                    try:
                        import json as _json
                        _json.dumps(ce["observed_value"])
                    except (TypeError, ValueError):
                        ce["observed_value"] = repr(ce["observed_value"])
        return d

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent)

    @property
    def passed(self) -> bool:
        return self.outcome == "passed"
