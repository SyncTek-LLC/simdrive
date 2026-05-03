"""Tests for journey/ci.py — Component 8 CI orchestrator.

All tests use injected fake session and LLM client factories so no real
simulator is needed. JUnit XML well-formedness is verified with xml.etree.
"""
from __future__ import annotations

import json
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Optional
from unittest.mock import MagicMock, patch

import pytest
import yaml

from simdrive.errors import SimdriveError
from simdrive.journey.ci import (
    CIRunOptions,
    CIRunSummary,
    emit_junit_xml,
    run_ci,
)
from simdrive.journey.result import CriterionEval, RunResult
from simdrive.journey.runner import StepDecision


# ── Fixture helpers ───────────────────────────────────────────────────────────

def _write_journey(
    journeys_dir: Path,
    name: str,
    persona_slug: str = "test-user",
    success_text: str = "Home",
    tags: Optional[list[str]] = None,
) -> Path:
    data = {
        "schema_version": 1,
        "name": name,
        "persona": persona_slug,
        "target": "simulator",
        "goals": [f"Complete {name}"],
        "success_criteria": [{"text_visible": success_text}],
        "tags": tags or [],
    }
    p = journeys_dir / f"{name}.yaml"
    p.write_text(yaml.dump(data))
    return p


def _write_persona(personas_dir: Path, slug: str = "test-user") -> Path:
    data = {
        "schema_version": 1,
        "slug": slug,
        "name": "Test User",
        "role": "Test automation persona",
    }
    p = personas_dir / f"{slug}.yaml"
    p.write_text(yaml.dump(data))
    return p


def _make_passed_result(journey_name: str, artifact_dir: Optional[Path] = None) -> RunResult:
    return RunResult(
        outcome="passed",
        journey_name=journey_name,
        persona_name="Test User",
        steps_executed=1,
        llm_calls=1,
        llm_cost_usd=0.004,
        duration_seconds=0.5,
        success_criteria=[CriterionEval("text_visible", True, "found Home")],
        artifact_dir=artifact_dir,
    )


def _make_failed_result(journey_name: str) -> RunResult:
    return RunResult(
        outcome="failed",
        journey_name=journey_name,
        persona_name="Test User",
        steps_executed=2,
        llm_calls=2,
        llm_cost_usd=0.008,
        duration_seconds=1.2,
        failure_reason="criteria not met after 2 steps",
        success_criteria=[CriterionEval("text_visible", False, "Home not found")],
    )


class FakeLLMClient:
    def __init__(self, decision: StepDecision):
        self._decision = decision
        self._cost = 0.0

    def call(self, system_prompt, user_prompt, screenshot_path):
        self._cost += 0.004
        return self._decision

    @property
    def cost_usd(self) -> float:
        return self._cost


# ── emit_junit_xml tests ──────────────────────────────────────────────────────

class TestEmitJunitXml:
    def test_all_pass_produces_valid_xml(self, tmp_path):
        results = [
            _make_passed_result("journey-a"),
            _make_passed_result("journey-b"),
        ]
        out = tmp_path / "junit.xml"
        emit_junit_xml(results, out)
        assert out.exists()

        tree = ET.parse(str(out))
        root = tree.getroot()
        assert root.tag == "testsuite"
        assert root.attrib["tests"] == "2"
        assert root.attrib["failures"] == "0"
        assert root.attrib["errors"] == "0"

    def test_one_failure_produces_failure_element(self, tmp_path):
        results = [
            _make_passed_result("pass-j"),
            _make_failed_result("fail-j"),
        ]
        out = tmp_path / "junit.xml"
        emit_junit_xml(results, out)

        tree = ET.parse(str(out))
        root = tree.getroot()
        testcases = list(root.iter("testcase"))
        assert len(testcases) == 2

        failures = [tc for tc in testcases if tc.find("failure") is not None]
        assert len(failures) == 1
        failure_tc = failures[0]
        assert failure_tc.attrib["name"] == "fail-j"

    def test_failure_element_contains_reason(self, tmp_path):
        results = [_make_failed_result("j")]
        out = tmp_path / "junit.xml"
        emit_junit_xml(results, out)

        tree = ET.parse(str(out))
        root = tree.getroot()
        failure = root.find(".//failure")
        assert failure is not None
        assert "criteria not met" in (failure.text or "")

    def test_system_out_included_when_trace_exists(self, tmp_path):
        artifact_dir = tmp_path / "artifacts"
        artifact_dir.mkdir()
        trace_path = artifact_dir / "agent_trace.jsonl"
        trace_path.write_text('{"step_idx":1,"tool":"tap"}\n')

        result = _make_passed_result("traced-j", artifact_dir=artifact_dir)
        out = tmp_path / "junit.xml"
        emit_junit_xml([result], out)

        tree = ET.parse(str(out))
        sys_out = tree.getroot().find(".//system-out")
        assert sys_out is not None
        assert "tap" in (sys_out.text or "")

    def test_xml_well_formed(self, tmp_path):
        """XML must parse without errors."""
        results = [
            _make_passed_result("a"),
            _make_failed_result("b"),
        ]
        out = tmp_path / "junit.xml"
        emit_junit_xml(results, out)
        # If this doesn't raise, the XML is well-formed.
        ET.parse(str(out))

    def test_empty_results(self, tmp_path):
        """An empty result list should still produce valid XML."""
        out = tmp_path / "junit.xml"
        emit_junit_xml([], out)
        tree = ET.parse(str(out))
        root = tree.getroot()
        assert root.attrib["tests"] == "0"


# ── run_ci tests ──────────────────────────────────────────────────────────────

class TestRunCISingleJourneyPass:
    def test_single_journey_pass(self, tmp_path):
        journeys_dir = tmp_path / "journeys"
        journeys_dir.mkdir()
        personas_dir = tmp_path / "personas"
        personas_dir.mkdir()
        _write_journey(journeys_dir, "j1")
        _write_persona(personas_dir)

        passed_result = _make_passed_result("j1", artifact_dir=tmp_path / "run_j1")
        (tmp_path / "run_j1").mkdir()

        def fake_run_journey(journey, persona, session, llm_client, **kwargs):
            return passed_result

        fake_session = MagicMock()
        fake_session.session_id = "sess-001"

        options = CIRunOptions(
            journeys_dir=str(journeys_dir),
            personas_dir=str(personas_dir),
            junit_path=str(tmp_path / "junit.xml"),
            ci_summary_path=str(tmp_path / "ci_summary.json"),
            session_factory=lambda j: fake_session,
            llm_client_factory=lambda: FakeLLMClient(
                StepDecision(tool="done", args={}, rationale="ok", confidence=1.0)
            ),
        )

        with patch("simdrive.journey.ci.run_journey", side_effect=fake_run_journey):
            summary = run_ci(options)

        assert summary.total == 1
        assert summary.passed == 1
        assert summary.failed == 0
        assert summary.exit_code == 0


class TestRunCIMultipleJourneys:
    def test_three_pass_one_fail(self, tmp_path):
        journeys_dir = tmp_path / "journeys"
        journeys_dir.mkdir()
        personas_dir = tmp_path / "personas"
        personas_dir.mkdir()

        for name in ["j1", "j2", "j3", "j4"]:
            _write_journey(journeys_dir, name)
        _write_persona(personas_dir)

        call_count = {"n": 0}

        def fake_run_journey(journey, persona, session, llm_client, **kwargs):
            call_count["n"] += 1
            if journey.name == "j4":
                return _make_failed_result("j4")
            return _make_passed_result(journey.name)

        fake_session = MagicMock()
        fake_session.session_id = "sess-x"

        options = CIRunOptions(
            journeys_dir=str(journeys_dir),
            personas_dir=str(personas_dir),
            junit_path=str(tmp_path / "junit.xml"),
            ci_summary_path=str(tmp_path / "ci_summary.json"),
            session_factory=lambda j: fake_session,
            llm_client_factory=lambda: FakeLLMClient(
                StepDecision(tool="done", args={}, rationale="ok", confidence=1.0)
            ),
        )

        with patch("simdrive.journey.ci.run_journey", side_effect=fake_run_journey):
            summary = run_ci(options)

        assert summary.total == 4
        assert summary.passed == 3
        assert summary.failed == 1
        assert "j4" in summary.failed_journey_names
        assert summary.exit_code == 1


class TestRunCIPartialFailure:
    def test_bail_on_first_failure(self, tmp_path):
        journeys_dir = tmp_path / "journeys"
        journeys_dir.mkdir()
        personas_dir = tmp_path / "personas"
        personas_dir.mkdir()

        for name in ["j1-fail", "j2", "j3"]:
            _write_journey(journeys_dir, name)
        _write_persona(personas_dir)

        executed = []

        def fake_run_journey(journey, persona, session, llm_client, **kwargs):
            executed.append(journey.name)
            if journey.name == "j1-fail":
                return _make_failed_result("j1-fail")
            return _make_passed_result(journey.name)

        fake_session = MagicMock()
        fake_session.session_id = "sess-bail"

        options = CIRunOptions(
            journeys_dir=str(journeys_dir),
            personas_dir=str(personas_dir),
            junit_path=str(tmp_path / "junit.xml"),
            ci_summary_path=str(tmp_path / "ci_summary.json"),
            bail_on_first_failure=True,
            session_factory=lambda j: fake_session,
            llm_client_factory=lambda: FakeLLMClient(
                StepDecision(tool="done", args={}, rationale="ok", confidence=1.0)
            ),
        )

        with patch("simdrive.journey.ci.run_journey", side_effect=fake_run_journey):
            summary = run_ci(options)

        # Only the first journey should have run (bail_on_first_failure=True)
        assert len(executed) == 1
        assert summary.failed == 1


class TestRunCIJunitOutput:
    def test_junit_file_created(self, tmp_path):
        journeys_dir = tmp_path / "journeys"
        journeys_dir.mkdir()
        personas_dir = tmp_path / "personas"
        personas_dir.mkdir()
        _write_journey(journeys_dir, "smoke-test")
        _write_persona(personas_dir)

        junit_path = tmp_path / "junit.xml"

        def fake_run_journey(journey, persona, session, llm_client, **kwargs):
            return _make_passed_result("smoke-test")

        fake_session = MagicMock()
        fake_session.session_id = "sess-j"

        options = CIRunOptions(
            journeys_dir=str(journeys_dir),
            personas_dir=str(personas_dir),
            junit_path=str(junit_path),
            ci_summary_path=str(tmp_path / "ci_summary.json"),
            session_factory=lambda j: fake_session,
            llm_client_factory=lambda: FakeLLMClient(
                StepDecision(tool="done", args={}, rationale="ok", confidence=1.0)
            ),
        )

        with patch("simdrive.journey.ci.run_journey", side_effect=fake_run_journey):
            summary = run_ci(options)

        assert junit_path.exists()
        # Verify it's well-formed XML
        tree = ET.parse(str(junit_path))
        root = tree.getroot()
        assert root.tag == "testsuite"
        assert root.attrib["tests"] == "1"

    def test_ci_summary_json_written(self, tmp_path):
        journeys_dir = tmp_path / "journeys"
        journeys_dir.mkdir()
        personas_dir = tmp_path / "personas"
        personas_dir.mkdir()
        _write_journey(journeys_dir, "s1")
        _write_persona(personas_dir)

        ci_summary_path = tmp_path / "ci_summary.json"

        def fake_run_journey(journey, persona, session, llm_client, **kwargs):
            return _make_passed_result("s1")

        fake_session = MagicMock()
        fake_session.session_id = "sess-k"

        options = CIRunOptions(
            journeys_dir=str(journeys_dir),
            personas_dir=str(personas_dir),
            junit_path=str(tmp_path / "junit.xml"),
            ci_summary_path=str(ci_summary_path),
            session_factory=lambda j: fake_session,
            llm_client_factory=lambda: FakeLLMClient(
                StepDecision(tool="done", args={}, rationale="ok", confidence=1.0)
            ),
        )

        with patch("simdrive.journey.ci.run_journey", side_effect=fake_run_journey):
            run_ci(options)

        assert ci_summary_path.exists()
        data = json.loads(ci_summary_path.read_text())
        assert data["total"] == 1
        assert data["passed"] == 1


class TestRunCINoJourneysMatched:
    def test_empty_dir_raises(self, tmp_path):
        journeys_dir = tmp_path / "journeys"
        journeys_dir.mkdir()
        personas_dir = tmp_path / "personas"
        personas_dir.mkdir()
        # No journey files written

        options = CIRunOptions(
            journeys_dir=str(journeys_dir),
            personas_dir=str(personas_dir),
        )

        with pytest.raises(SimdriveError) as exc_info:
            run_ci(options)

        assert exc_info.value.code == "ci_no_journeys_matched"


class TestRunCIInvalidJourney:
    def test_invalid_journey_raises_ci_invalid(self, tmp_path):
        journeys_dir = tmp_path / "journeys"
        journeys_dir.mkdir()
        personas_dir = tmp_path / "personas"
        personas_dir.mkdir()
        _write_persona(personas_dir)

        # Write an invalid journey (wrong schema version)
        bad = journeys_dir / "bad.yaml"
        bad.write_text(yaml.dump({
            "schema_version": 99,
            "name": "bad",
            "persona": "test-user",
            "goals": ["x"],
            "success_criteria": [{"text_visible": "x"}],
        }))

        options = CIRunOptions(
            journeys_dir=str(journeys_dir),
            personas_dir=str(personas_dir),
        )

        with pytest.raises(SimdriveError) as exc_info:
            run_ci(options)

        assert exc_info.value.code == "ci_invalid_journey"


class TestCIRunSummaryExitCodes:
    def test_all_pass_exit_0(self):
        summary = CIRunSummary(
            total=2, passed=2, failed=0, errors=0,
            total_llm_cost_usd=0.01, total_duration_seconds=1.0,
            failed_journey_names=[], results=[],
        )
        assert summary.exit_code == 0

    def test_one_failure_exit_1(self):
        summary = CIRunSummary(
            total=2, passed=1, failed=1, errors=0,
            total_llm_cost_usd=0.01, total_duration_seconds=1.0,
            failed_journey_names=["j"], results=[],
        )
        assert summary.exit_code == 1

    def test_error_exit_2(self):
        summary = CIRunSummary(
            total=1, passed=0, failed=0, errors=1,
            total_llm_cost_usd=0.0, total_duration_seconds=0.5,
            failed_journey_names=["j"], results=[],
        )
        assert summary.exit_code == 2
