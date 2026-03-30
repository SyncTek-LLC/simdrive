"""SpecterQA Report Generator — Produces run report artifacts.

Bundled in specterqa-ios — sourced from specterqa.engine (upstream unpublished).
"""

from __future__ import annotations

import dataclasses
from typing import Any


@dataclasses.dataclass
class Finding:
    """A finding from a run -- represents an issue or observation."""

    severity: str  # block, critical, high, medium, low
    category: str  # server_error, api_contract, performance, behavior, ux, security
    description: str
    evidence: str  # path to screenshot or relevant data
    step_id: str


@dataclasses.dataclass
class StepReport:
    """Report for a single step in a run."""

    step_id: str
    description: str
    mode: str  # api, browser, ios_simulator
    passed: bool
    duration_seconds: float
    error: str | None = None
    notes: str = ""
    action_count: int = 0
    screenshots: list[str] = dataclasses.field(default_factory=list)
    ux_observations: list[str] = dataclasses.field(default_factory=list)
    actions_taken: list[dict[str, Any]] = dataclasses.field(default_factory=list)
    model_routing: dict[str, int] = dataclasses.field(default_factory=dict)
    performance_ms: float | None = None


@dataclasses.dataclass
class RunResult:
    """Complete result of a SpecterQA run."""

    run_id: str
    scenario_name: str
    scenario_id: str
    product_name: str
    persona_name: str
    persona_role: str
    viewport_name: str
    viewport_size: tuple[int, int]
    mock_level: str
    passed: bool
    start_time: str
    end_time: str
    duration_seconds: float
    step_reports: list[StepReport]
    findings: list[Finding]
    cost_usd: float
    cost_summary: dict[str, Any]
