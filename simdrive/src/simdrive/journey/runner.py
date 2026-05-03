"""Journey runner core — Component 3.

Public surface:
  LLMClient        — Protocol that the runner calls for each vision decision
  StepDecision     — Parsed response from the LLM (tool + args + rationale + confidence)
  run_journey      — Execute a journey against an active Session; returns RunResult

The LLMClient is a dependency-injection Protocol so tests can substitute a
fake client without any real Claude API call.
"""
from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Optional, Protocol

from .criteria import all_passed, evaluate_all_criteria, unmet_descriptions
from .errors import (
    act_tool_failed,
    claude_cost_cap_hit,
)
from .persona import Persona
from .prompt import assemble_system_prompt, assemble_user_prompt
from .result import CriterionEval, OutcomeType, RunResult, StepRecord
from .schema import Journey

log = logging.getLogger("simdrive.journey.runner")

# Default cost cap — overridable via SIMDRIVE_COST_CAP_USD env var.
_DEFAULT_COST_CAP_USD = 5.0


# ── Module-level server tool references (patchable in tests) ─────────────────
# Defined as module-level callables so `patch("...runner.tool_observe")` works.
# Each falls back to the real server function when called; tests replace them.

def tool_observe(arguments: dict) -> dict:  # pragma: no cover
    from simdrive.server import tool_observe as _fn
    return _fn(arguments)


def tool_tap(arguments: dict) -> dict:  # pragma: no cover
    from simdrive.server import tool_tap as _fn
    return _fn(arguments)


def tool_swipe(arguments: dict) -> dict:  # pragma: no cover
    from simdrive.server import tool_swipe as _fn
    return _fn(arguments)


def tool_type_text(arguments: dict) -> dict:  # pragma: no cover
    from simdrive.server import tool_type_text as _fn
    return _fn(arguments)


def tool_press_key(arguments: dict) -> dict:  # pragma: no cover
    from simdrive.server import tool_press_key as _fn
    return _fn(arguments)


def tool_clear_field(arguments: dict) -> dict:  # pragma: no cover
    from simdrive.server import tool_clear_field as _fn
    return _fn(arguments)


def tool_perf(arguments: dict) -> dict:  # pragma: no cover
    from simdrive.server import tool_perf as _fn
    return _fn(arguments)


def tool_perf_baseline(arguments: dict) -> dict:  # pragma: no cover
    from simdrive.server import tool_perf_baseline as _fn
    return _fn(arguments)


def tool_crashes(arguments: dict) -> dict:  # pragma: no cover
    from simdrive.server import tool_crashes as _fn
    return _fn(arguments)

# Approximate cost per LLM call (vision, claude-3-7 range); used for
# in-flight cost tracking when the real client doesn't report token counts.
_APPROX_COST_PER_CALL_USD = 0.004

# How many recent steps to include in the user prompt context window.
_HISTORY_SIZE = 3


# ── LLM client protocol ───────────────────────────────────────────────────────


@dataclass
class StepDecision:
    """Parsed response from one LLM vision call."""

    tool: Literal["tap", "swipe", "type_text", "press_key", "clear_field", "done", "fail"]
    args: dict
    rationale: str
    confidence: float


class LLMClient(Protocol):
    """Protocol for the vision LLM used by the runner.

    The production implementation calls claude-3-7-sonnet or equivalent.
    Tests substitute a fake client that returns scripted StepDecision objects.
    """

    def call(
        self,
        system_prompt: str,
        user_prompt: str,
        screenshot_path: Optional[str],
    ) -> StepDecision:
        """Make one LLM call and return the parsed action decision."""
        ...

    @property
    def cost_usd(self) -> float:
        """Cumulative cost of all calls made so far."""
        ...


# ── Artifact helpers ──────────────────────────────────────────────────────────


def _runs_root() -> Path:
    base = os.environ.get("SIMDRIVE_HOME") or str(Path.home() / ".simdrive")
    return Path(base) / "runs"


def _artifact_dir(journey_name: str) -> Path:
    ts = int(time.time())
    safe_name = journey_name.replace(" ", "_").lower()
    return _runs_root() / f"{safe_name}-{ts}"


def _write_artifacts(
    artifact_dir: Path,
    result: RunResult,
) -> None:
    """Write summary.json, summary.md, and agent_trace.jsonl to artifact_dir."""
    artifact_dir.mkdir(parents=True, exist_ok=True)

    # summary.json
    summary_path = artifact_dir / "summary.json"
    summary_path.write_text(result.to_json())

    # summary.md — human-friendly one-pager
    md_lines = [
        f"# Journey: {result.journey_name}",
        f"**Outcome:** {result.outcome}",
        f"**Persona:** {result.persona_name}",
        f"**Steps:** {result.steps_executed}",
        f"**Duration:** {result.duration_seconds:.1f}s",
        f"**LLM calls:** {result.llm_calls}",
        f"**Cost:** ${result.llm_cost_usd:.4f}",
        "",
        "## Success Criteria",
    ]
    for ce in result.success_criteria:
        icon = "✓" if ce.passed else "✗"
        md_lines.append(f"- {icon} **{ce.criterion_type}**: {ce.detail}")
    if result.failure_reason:
        md_lines += ["", f"**Failure reason:** {result.failure_reason}"]
    summary_md_path = artifact_dir / "summary.md"
    summary_md_path.write_text("\n".join(md_lines))

    # agent_trace.jsonl — one JSON object per step
    trace_path = artifact_dir / "agent_trace.jsonl"
    with trace_path.open("w") as f:
        for sr in result.steps:
            from dataclasses import asdict
            f.write(json.dumps(asdict(sr)) + "\n")


# ── Dispatch helpers ──────────────────────────────────────────────────────────


def _dispatch_action(decision: StepDecision, session_id: str) -> None:
    """Route the LLM's decision to the appropriate act tool.

    Uses the module-level tool_* references so tests can patch them via
    `patch("simdrive.journey.runner.tool_tap")` etc.
    All tool functions follow the same call signature: tool_*(arguments: dict) -> dict.
    """
    import simdrive.journey.runner as _self  # noqa: PLC0415 — current module

    args = dict(decision.args)
    args["session_id"] = session_id

    tool_map = {
        "tap": _self.tool_tap,
        "swipe": _self.tool_swipe,
        "type_text": _self.tool_type_text,
        "press_key": _self.tool_press_key,
        "clear_field": _self.tool_clear_field,
    }

    tool_fn = tool_map.get(decision.tool)
    if tool_fn is None:
        # done / fail are handled in the main loop before _dispatch_action is called
        raise ValueError(f"unexpected tool in dispatch: {decision.tool!r}")

    try:
        tool_fn(args)
    except Exception as exc:
        from simdrive.errors import SimdriveError  # noqa: PLC0415
        inner_code = exc.code if isinstance(exc, SimdriveError) else "unknown"
        raise act_tool_failed(decision.tool, inner_code, str(exc)) from exc


# ── Main runner ───────────────────────────────────────────────────────────────


_RECORDER_DEFAULT = object()  # sentinel: means "load the real recorder"
_RECORDER_DISABLED = None    # sentinel: means "skip recording entirely"


def run_journey(
    journey: Journey,
    persona: Persona,
    session: Any,  # simdrive.session.Session — typed as Any to avoid hard dep
    llm_client: LLMClient,
    *,
    cost_cap_usd: Optional[float] = None,
    artifact_dir_override: Optional[Path] = None,
    _recorder_module: Any = _RECORDER_DEFAULT,  # injectable for testing
) -> RunResult:
    """Execute a journey against an already-started session.

    Parameters
    ----------
    journey:    Validated Journey model.
    persona:    Validated Persona model.
    session:    Active simdrive.session.Session.
    llm_client: Dependency-injected LLM client (tests use a fake).
    cost_cap_usd: Override for the default $5/run cost cap.
    artifact_dir_override: Override the artifact directory for testing.

    Returns
    -------
    RunResult — complete outcome including success-criteria evals and per-step trace.
    """
    # Cost cap — env var > arg > default.
    _cap = cost_cap_usd
    if _cap is None:
        env_cap = os.environ.get("SIMDRIVE_COST_CAP_USD")
        _cap = float(env_cap) if env_cap else _DEFAULT_COST_CAP_USD

    artifact_dir = artifact_dir_override or _artifact_dir(journey.name)

    # Recorder integration — load the real recorder unless the caller passed None
    # (explicit disable, used in tests).  The sentinel _RECORDER_DEFAULT triggers
    # the import; passing _recorder_module=None skips recording entirely.
    if _recorder_module is _RECORDER_DEFAULT:
        try:
            from simdrive import recorder as _recorder_module  # noqa: PLC0415
        except ImportError:
            _recorder_module = None

    # ── Pre-loop setup ────────────────────────────────────────────────────────

    session_id: str = session.session_id
    started_at = time.time()
    budget = journey.budget

    # Assemble the stable system prompt once — cached by Claude.
    system_prompt = assemble_system_prompt(journey, persona, _HISTORY_SIZE)

    step_records: list[StepRecord] = []
    final_criteria_evals: list[CriterionEval] = []
    outcome: OutcomeType = "error"
    failure_reason: Optional[str] = None
    llm_calls = 0
    replay_id: Optional[str] = None

    # Start recording if the recorder module is available.
    if _recorder_module is not None:
        try:
            _recorder_module.start(session, name=journey.name)
        except Exception as exc:
            log.warning("recorder.start failed (non-fatal): %s", exc)

    # Perf baseline — best-effort; failure is non-fatal.
    perf_baseline: Optional[dict] = None
    try:
        baseline_result = tool_perf_baseline({"session_id": session_id})
        perf_baseline = baseline_result  # noqa: F841 (stored for future compare)
    except Exception as exc:
        log.warning("perf_baseline failed (non-fatal): %s", exc)

    # Session start_at for crash filtering
    session_started_at = getattr(session, "started_at", started_at)

    # ── Agent loop ────────────────────────────────────────────────────────────

    step_idx = 0
    while True:
        elapsed = time.time() - started_at

        # Budget check — step count
        if step_idx >= budget.max_steps:
            outcome = "budget_exceeded"
            failure_reason = (
                f"exceeded max_steps={budget.max_steps}"
            )
            log.warning(
                "journey %r budget exceeded: steps=%d", journey.name, step_idx
            )
            break

        # Budget check — wall clock
        if elapsed >= budget.max_seconds:
            outcome = "budget_exceeded"
            failure_reason = (
                f"exceeded max_seconds={budget.max_seconds} (elapsed={elapsed:.1f}s)"
            )
            break

        # Budget check — LLM calls
        if llm_calls >= budget.max_llm_calls:
            outcome = "budget_exceeded"
            failure_reason = (
                f"exceeded max_llm_calls={budget.max_llm_calls}"
            )
            break

        # Cost cap check
        current_cost = getattr(llm_client, "cost_usd", 0.0)
        if current_cost >= _cap:
            # Surface as error, not budget_exceeded, since the cap is a safety rail.
            outcome = "error"
            failure_reason = f"LLM cost cap ${_cap:.2f} reached"
            raise claude_cost_cap_hit(current_cost, _cap)

        # Observe current screen state.
        try:
            obs_dict = tool_observe({"session_id": session_id})
        except Exception as exc:
            outcome = "error"
            failure_reason = f"observe failed: {exc}"
            break

        # Evaluate success criteria against current observation.
        perf_snapshot: Optional[dict] = None
        try:
            perf_snapshot = tool_perf({"session_id": session_id})
        except Exception:
            pass  # perf_under criteria will mark themselves as failed if snapshot is None

        crashes: list[dict] = []
        try:
            crash_result = tool_crashes({
                "session_id": session_id,
                "since": session_started_at,
            })
            if isinstance(crash_result, dict):
                crashes = crash_result.get("crashes", [])
        except Exception:
            pass

        criteria_evals = evaluate_all_criteria(
            journey.success_criteria,
            obs=obs_dict,
            perf_snapshot=perf_snapshot,
            crashes_since_start=crashes,
        )
        final_criteria_evals = criteria_evals

        # Crash detection — check no_crash criterion outcome
        if crashes:
            outcome = "crashed"
            failure_reason = f"app crashed: {crashes[0].get('path', 'unknown')}"
            break

        # All criteria met?
        if all_passed(criteria_evals):
            outcome = "passed"
            break

        # Build prompt context for this step.
        unmet = unmet_descriptions(criteria_evals)
        budget_remaining = {
            "steps": budget.max_steps - step_idx,
            "seconds": max(0.0, budget.max_seconds - elapsed),
            "llm_calls": budget.max_llm_calls - llm_calls,
        }
        user_prompt = assemble_user_prompt(
            obs=obs_dict,
            unmet_criteria=unmet,
            recent_steps=step_records[-_HISTORY_SIZE:],
            step_idx=step_idx + 1,
            budget_remaining=budget_remaining,
        )

        # LLM vision call.
        screenshot_path = obs_dict.get("screenshot_path")
        step_start = time.time()
        try:
            decision = llm_client.call(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                screenshot_path=screenshot_path,
            )
            llm_calls += 1
        except Exception as exc:
            outcome = "error"
            failure_reason = f"claude_call_failed: {exc}"
            break

        step_elapsed = time.time() - step_start

        # Handle terminal decisions from the LLM.
        if decision.tool == "done":
            outcome = "passed"
            break
        if decision.tool == "fail":
            outcome = "failed"
            failure_reason = decision.rationale
            break

        # Dispatch the chosen action.
        try:
            _dispatch_action(decision, session_id)
        except Exception as exc:
            outcome = "error"
            failure_reason = str(exc)
            step_records.append(
                StepRecord(
                    step_idx=step_idx + 1,
                    tool=decision.tool,
                    args=decision.args,
                    rationale=decision.rationale,
                    confidence=decision.confidence,
                    elapsed_seconds=step_elapsed,
                    success_criteria_snapshot=list(criteria_evals),
                    error=str(exc),
                )
            )
            break

        # Record the step.
        step_records.append(
            StepRecord(
                step_idx=step_idx + 1,
                tool=decision.tool,
                args=decision.args,
                rationale=decision.rationale,
                confidence=decision.confidence,
                elapsed_seconds=step_elapsed,
                success_criteria_snapshot=list(criteria_evals),
            )
        )
        step_idx += 1

    # ── Post-loop teardown ────────────────────────────────────────────────────

    total_elapsed = time.time() - started_at

    # Stop recording and capture replay_id.
    if _recorder_module is not None:
        try:
            yaml_path = _recorder_module.stop(session)
            replay_id = yaml_path.stem if yaml_path else None
        except Exception as exc:
            log.warning("recorder.stop failed (non-fatal): %s", exc)

    # Compute final LLM cost.
    llm_cost = getattr(llm_client, "cost_usd", 0.0)
    if llm_cost == 0.0 and llm_calls > 0:
        # Fallback estimate when client doesn't track cost.
        llm_cost = llm_calls * _APPROX_COST_PER_CALL_USD

    result = RunResult(
        outcome=outcome,
        journey_name=journey.name,
        persona_name=persona.name,
        steps_executed=step_idx,
        llm_calls=llm_calls,
        llm_cost_usd=llm_cost,
        duration_seconds=total_elapsed,
        success_criteria=final_criteria_evals,
        replay_id=replay_id,
        artifact_dir=artifact_dir,
        failure_reason=failure_reason,
        steps=step_records,
    )

    # Write artifacts.
    try:
        _write_artifacts(artifact_dir, result)
    except Exception as exc:
        log.warning("_write_artifacts failed (non-fatal): %s", exc)

    return result
