"""simdrive ci orchestrator — Component 8.

Discovers all journey YAMLs in a directory, runs each against a fresh session,
aggregates results, and emits:
  - JUnit XML  (--junit path, default .simdrive/runs/junit.xml)
  - Corpus dir (--corpus-out path, default .simdrive/runs/corpus/)
  - ci_summary.json

Exit codes:
  0 — all journeys passed
  1 — one or more journeys failed
  2 — internal error (invalid journey, no journeys found, session error)

Public surface:
  CIRunOptions    — configuration dataclass for run_ci
  CIRunSummary    — aggregated result of a CI run
  run_ci          — execute the full CI pass; returns CIRunSummary
  emit_junit_xml  — write JUnit XML for a list of RunResults
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

from .errors import ci_no_journeys_matched
from .loader import iter_journeys, load_persona_for_journey
from .result import RunResult
from .runner import run_journey  # module-level import so tests can patch ci.run_journey
from .schema import Journey

log = logging.getLogger("simdrive.journey.ci")

_DEFAULT_JOURNEYS_DIR = ".simdrive/journeys"
_DEFAULT_PERSONAS_DIR = ".simdrive/personas"
_DEFAULT_JUNIT_PATH = ".simdrive/runs/junit.xml"
_DEFAULT_CORPUS_DIR = ".simdrive/runs/corpus"
_DEFAULT_CI_SUMMARY_PATH = ".simdrive/runs/ci_summary.json"


@dataclass
class CIRunOptions:
    """Configuration for run_ci."""

    journeys_dir: str = _DEFAULT_JOURNEYS_DIR
    personas_dir: str = _DEFAULT_PERSONAS_DIR
    tag_filter: list[str] = field(default_factory=list)
    slug_filter: list[str] = field(default_factory=list)
    bail_on_first_failure: bool = False
    junit_path: str = _DEFAULT_JUNIT_PATH
    corpus_dir: str = _DEFAULT_CORPUS_DIR
    ci_summary_path: str = _DEFAULT_CI_SUMMARY_PATH
    # Override for testing — inject a fake session factory and LLM client factory
    session_factory: Optional[Callable[..., Any]] = None
    llm_client_factory: Optional[Callable[[], Any]] = None


@dataclass
class CIRunSummary:
    """Aggregated result from a full CI pass."""

    total: int
    passed: int
    failed: int
    errors: int
    total_llm_cost_usd: float
    total_duration_seconds: float
    failed_journey_names: list[str]
    results: list[RunResult]
    junit_xml_path: Optional[str] = None
    ci_summary_path: Optional[str] = None

    @property
    def exit_code(self) -> int:
        """0 = all pass, 1 = failures, 2 = errors."""
        if self.errors > 0:
            return 2
        if self.failed > 0:
            return 1
        return 0

    def to_dict(self) -> dict:
        return {
            "total": self.total,
            "passed": self.passed,
            "failed": self.failed,
            "errors": self.errors,
            "total_llm_cost_usd": self.total_llm_cost_usd,
            "total_duration_seconds": self.total_duration_seconds,
            "failed_journey_names": self.failed_journey_names,
            "junit_xml_path": self.junit_xml_path,
            "ci_summary_path": self.ci_summary_path,
        }


def emit_junit_xml(results: list[RunResult], output_path: str | Path) -> Path:
    """Write a JUnit XML file from a list of RunResults.

    Format: one <testsuite> with one <testcase> per journey.
    Failed journeys get a <failure> child element.
    agent_trace.jsonl content is included in <system-out> when available.
    """
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    total = len(results)
    failures = sum(1 for r in results if r.outcome in ("failed", "budget_exceeded", "crashed"))
    errors = sum(1 for r in results if r.outcome == "error")

    suite = ET.Element(
        "testsuite",
        attrib={
            "name": "simdrive-journeys",
            "tests": str(total),
            "failures": str(failures),
            "errors": str(errors),
            "time": f"{sum(r.duration_seconds for r in results):.3f}",
        },
    )

    for result in results:
        tc = ET.SubElement(
            suite,
            "testcase",
            attrib={
                "name": result.journey_name,
                "classname": "simdrive.journey",
                "time": f"{result.duration_seconds:.3f}",
            },
        )

        if result.outcome != "passed":
            failure_type = (
                "org.simdrive.JourneyError"
                if result.outcome == "error"
                else "org.simdrive.JourneyFailure"
            )
            failure_elem = ET.SubElement(
                tc,
                "failure",
                attrib={
                    "message": result.failure_reason or result.outcome,
                    "type": failure_type,
                },
            )
            failure_elem.text = (
                f"outcome={result.outcome}\n"
                f"steps={result.steps_executed}\n"
                f"llm_calls={result.llm_calls}\n"
                f"cost=${result.llm_cost_usd:.4f}\n"
                f"reason={result.failure_reason or ''}"
            )

        # Include agent_trace.jsonl content in <system-out> when available.
        trace_content = ""
        if result.artifact_dir is not None:
            trace_path = Path(result.artifact_dir) / "agent_trace.jsonl"
            if trace_path.exists():
                try:
                    trace_content = trace_path.read_text()[:4096]  # cap at 4 KB
                except Exception:
                    pass
        if trace_content:
            sys_out = ET.SubElement(tc, "system-out")
            sys_out.text = trace_content

    tree = ET.ElementTree(suite)
    ET.indent(tree, space="  ")
    tree.write(str(output_path), xml_declaration=True, encoding="utf-8")
    return output_path


def run_ci(options: CIRunOptions | None = None) -> CIRunSummary:
    """Discover and execute all matching journeys; return a CIRunSummary.

    Steps:
    1. Discover journey paths from options.journeys_dir.
    2. Validate all (bail with exit-code 2 on any invalid).
    3. For each journey: start a session, run, collect result.
    4. Emit JUnit XML + ci_summary.json.
    5. Return CIRunSummary.

    The session_factory and llm_client_factory fields on CIRunOptions allow
    full injection for unit tests — no real simulator needed.
    """
    if options is None:
        options = CIRunOptions()

    journeys_dir = options.journeys_dir
    personas_dir = options.personas_dir

    # ── Step 1+2: discover + validate all journeys ────────────────────────────

    journey_list: list[Journey] = []
    try:
        for j in iter_journeys(
            journeys_dir=journeys_dir,
            personas_dir=personas_dir,
            tag_filter=options.tag_filter or None,
            slug_filter=options.slug_filter or None,
        ):
            journey_list.append(j)
    except Exception:
        # ci_invalid_journey or ci_no_journeys_matched — re-raise so caller gets exit code 2
        raise

    if not journey_list:
        raise ci_no_journeys_matched(journeys_dir, options.tag_filter)

    # ── Step 3: run each journey ──────────────────────────────────────────────

    results: list[RunResult] = []
    ci_start = time.time()

    for journey in journey_list:
        # Load persona
        try:
            persona = load_persona_for_journey(journey, personas_dir)
        except Exception as exc:
            log.error("Failed to load persona for journey %r: %s", journey.name, exc)
            # Create an error RunResult so CI continues (or bails if bail_on_first_failure)
            err_result = RunResult(
                outcome="error",
                journey_name=journey.name,
                persona_name=journey.persona,
                steps_executed=0,
                llm_calls=0,
                llm_cost_usd=0.0,
                duration_seconds=0.0,
                failure_reason=f"persona load failed: {exc}",
            )
            results.append(err_result)
            if options.bail_on_first_failure:
                break
            continue

        # Session factory — injectable for tests, otherwise use the real session module.
        session = None
        try:
            if options.session_factory is not None:
                session = options.session_factory(journey)
            else:
                from simdrive import session as session_module  # noqa: PLC0415
                session = session_module.start(
                    target=journey.target,
                    app_bundle_id=journey.app_bundle_id,
                )
        except Exception as exc:
            err_result = RunResult(
                outcome="error",
                journey_name=journey.name,
                persona_name=persona.name,
                steps_executed=0,
                llm_calls=0,
                llm_cost_usd=0.0,
                duration_seconds=0.0,
                failure_reason=f"session start failed: {exc}",
            )
            results.append(err_result)
            if options.bail_on_first_failure:
                break
            continue

        # LLM client — injectable for tests.
        if options.llm_client_factory is not None:
            llm_client = options.llm_client_factory()
        else:
            # Production: the real Claude client.
            # Import lazily so tests don't need the anthropic SDK installed.
            try:
                from simdrive.journey.claude_client import ClaudeLLMClient  # noqa: PLC0415
                llm_client = ClaudeLLMClient()
            except ImportError:
                log.error("ClaudeLLMClient not available — run `pip install anthropic`")
                err_result = RunResult(
                    outcome="error",
                    journey_name=journey.name,
                    persona_name=persona.name,
                    steps_executed=0,
                    llm_calls=0,
                    llm_cost_usd=0.0,
                    duration_seconds=0.0,
                    failure_reason="ClaudeLLMClient not available",
                )
                results.append(err_result)
                if options.bail_on_first_failure:
                    break
                continue

        # Run the journey — run_journey is async after INIT-2026-544.
        try:
            result = asyncio.run(run_journey(journey, persona, session, llm_client))
        except Exception as exc:
            result = RunResult(
                outcome="error",
                journey_name=journey.name,
                persona_name=persona.name,
                steps_executed=0,
                llm_calls=0,
                llm_cost_usd=0.0,
                duration_seconds=0.0,
                failure_reason=str(exc),
            )

        # End the session (best effort).
        if session is not None:
            try:
                if options.session_factory is None:
                    from simdrive import session as session_module  # noqa: PLC0415
                    session_module.end(session.session_id)
                elif hasattr(session, "_end"):
                    session._end()
            except Exception as exc:
                log.warning("session.end failed (non-fatal): %s", exc)

        results.append(result)

        if options.bail_on_first_failure and result.outcome != "passed":
            break

    # ── Step 4: write artifacts ───────────────────────────────────────────────

    total_elapsed = time.time() - ci_start
    junit_path_str: Optional[str] = None
    summary_path_str: Optional[str] = None

    try:
        junit_path_str = str(emit_junit_xml(results, options.junit_path))
    except Exception as exc:
        log.warning("emit_junit_xml failed (non-fatal): %s", exc)

    passed_count = sum(1 for r in results if r.outcome == "passed")
    failed_count = sum(1 for r in results if r.outcome in ("failed", "budget_exceeded", "crashed"))
    error_count = sum(1 for r in results if r.outcome == "error")

    summary = CIRunSummary(
        total=len(results),
        passed=passed_count,
        failed=failed_count,
        errors=error_count,
        total_llm_cost_usd=sum(r.llm_cost_usd for r in results),
        total_duration_seconds=total_elapsed,
        failed_journey_names=[r.journey_name for r in results if r.outcome != "passed"],
        results=results,
        junit_xml_path=junit_path_str,
        ci_summary_path=summary_path_str,
    )

    # Write ci_summary.json.
    try:
        ci_summary_path = Path(options.ci_summary_path)
        ci_summary_path.parent.mkdir(parents=True, exist_ok=True)
        ci_summary_path.write_text(json.dumps(summary.to_dict(), indent=2))
        summary.ci_summary_path = str(ci_summary_path)
    except Exception as exc:
        log.warning("ci_summary.json write failed (non-fatal): %s", exc)

    return summary
