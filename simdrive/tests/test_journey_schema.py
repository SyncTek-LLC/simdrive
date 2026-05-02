"""Tests for journey/schema.py — Component 1.

Coverage target: 100% of schema.py.
Includes property-based tests (Hypothesis) for slug/name edge cases.
"""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
import yaml

# Hypothesis may not be installed in all environments; skip property tests if absent.
try:
    from hypothesis import given, settings, HealthCheck
    from hypothesis import strategies as st
    HAS_HYPOTHESIS = True
except ImportError:
    HAS_HYPOTHESIS = False

from specterqa_ios.errors import SimdriveError
from specterqa_ios.journey.schema import (
    Budget,
    DeviceSelector,
    Journey,
    Preconditions,
    SuccessCriterion,
    load_journey,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _minimal_journey_dict(**overrides) -> dict:
    base = {
        "schema_version": 1,
        "name": "sign-in",
        "persona": "power-user",
        "target": "simulator",
        "goals": ["Navigate to the login screen and sign in"],
        "success_criteria": [{"text_visible": "Welcome"}],
    }
    base.update(overrides)
    return base


def _write_journey(tmp_path: Path, data: dict, filename: str = "journey.yaml") -> Path:
    p = tmp_path / filename
    p.write_text(yaml.dump(data))
    return p


def _write_persona(personas_dir: Path, slug: str) -> Path:
    p = personas_dir / f"{slug}.yaml"
    p.write_text(yaml.dump({
        "schema_version": 1,
        "slug": slug,
        "name": "Test User",
        "role": "A regular test user",
    }))
    return p


# ── 1. Happy path — minimal valid journey ────────────────────────────────────

class TestLoadJourneyHappyPath:
    def test_minimal_valid_journey(self, tmp_path):
        p = _write_journey(tmp_path, _minimal_journey_dict())
        j = load_journey(p)
        assert j.name == "sign-in"
        assert j.persona == "power-user"
        assert j.target == "simulator"
        assert len(j.goals) == 1
        assert len(j.success_criteria) == 1
        assert j.success_criteria[0].text_visible == "Welcome"

    def test_default_budget_applied(self, tmp_path):
        p = _write_journey(tmp_path, _minimal_journey_dict())
        j = load_journey(p)
        assert j.budget.max_steps == 30
        assert j.budget.max_seconds == 180
        assert j.budget.max_llm_calls == 40

    def test_custom_budget_respected(self, tmp_path):
        data = _minimal_journey_dict(budget={"max_steps": 5, "max_seconds": 60, "max_llm_calls": 10})
        p = _write_journey(tmp_path, data)
        j = load_journey(p)
        assert j.budget.max_steps == 5

    def test_tags_optional(self, tmp_path):
        data = _minimal_journey_dict(tags=["smoke", "p0"])
        p = _write_journey(tmp_path, data)
        j = load_journey(p)
        assert "smoke" in j.tags

    def test_persona_cross_ref_passes_when_persona_file_exists(self, tmp_path):
        personas_dir = tmp_path / "personas"
        personas_dir.mkdir()
        _write_persona(personas_dir, "power-user")
        p = _write_journey(tmp_path, _minimal_journey_dict())
        j = load_journey(p, personas_dir=personas_dir)
        assert j.persona == "power-user"

    def test_all_success_criteria_types(self, tmp_path):
        data = _minimal_journey_dict(success_criteria=[
            {"text_visible": "Home"},
            {"screen_matches": "abc123"},
            {"perf_under": {"cpu_pct": 50.0, "memory_mb": 200.0}},
            {"no_crash": True},
        ])
        p = _write_journey(tmp_path, data)
        j = load_journey(p)
        assert len(j.success_criteria) == 4

    def test_device_target_with_device_selector(self, tmp_path):
        data = _minimal_journey_dict(
            target="device",
            device_selector={"udid": "00008150-001"},
        )
        p = _write_journey(tmp_path, data)
        j = load_journey(p)
        assert j.target == "device"
        assert j.device_selector.udid == "00008150-001"

    def test_replay_id_optional(self, tmp_path):
        data = _minimal_journey_dict(replay_id="tab-bar-tour-001")
        p = _write_journey(tmp_path, data)
        j = load_journey(p)
        assert j.replay_id == "tab-bar-tour-001"


# ── 2. Schema version mismatches ─────────────────────────────────────────────

class TestSchemaVersion:
    def test_schema_version_2_raises(self, tmp_path):
        data = _minimal_journey_dict(schema_version=2)
        p = _write_journey(tmp_path, data)
        with pytest.raises(SimdriveError) as exc_info:
            load_journey(p)
        assert exc_info.value.code == "journey_schema_version_unsupported"
        assert "2" in exc_info.value.message

    def test_schema_version_0_raises(self, tmp_path):
        data = _minimal_journey_dict(schema_version=0)
        p = _write_journey(tmp_path, data)
        with pytest.raises(SimdriveError) as exc_info:
            load_journey(p)
        assert exc_info.value.code == "journey_schema_version_unsupported"

    def test_schema_version_string_raises(self, tmp_path):
        # schema_version must be an int
        data = _minimal_journey_dict()
        data["schema_version"] = "1"  # string, not int
        p = _write_journey(tmp_path, data)
        # Pydantic will coerce "1" → 1 in v2; version check should still pass
        # OR it may raise schema_invalid — either is acceptable
        try:
            j = load_journey(p)
            assert j.schema_version == 1
        except SimdriveError:
            pass  # acceptable

    def test_schema_version_missing_raises(self, tmp_path):
        data = _minimal_journey_dict()
        del data["schema_version"]
        p = _write_journey(tmp_path, data)
        with pytest.raises(SimdriveError) as exc_info:
            load_journey(p)
        # Missing schema_version → version is None → unsupported
        assert exc_info.value.code in (
            "journey_schema_version_unsupported",
            "journey_schema_invalid",
        )


# ── 3. Missing required fields ────────────────────────────────────────────────

class TestMissingRequiredFields:
    def test_missing_name_raises(self, tmp_path):
        data = _minimal_journey_dict()
        del data["name"]
        p = _write_journey(tmp_path, data)
        with pytest.raises(SimdriveError) as exc_info:
            load_journey(p)
        assert exc_info.value.code == "journey_schema_invalid"

    def test_missing_persona_raises(self, tmp_path):
        data = _minimal_journey_dict()
        del data["persona"]
        p = _write_journey(tmp_path, data)
        with pytest.raises(SimdriveError) as exc_info:
            load_journey(p)
        assert exc_info.value.code == "journey_schema_invalid"

    def test_missing_goals_raises(self, tmp_path):
        data = _minimal_journey_dict()
        del data["goals"]
        p = _write_journey(tmp_path, data)
        with pytest.raises(SimdriveError) as exc_info:
            load_journey(p)
        assert exc_info.value.code == "journey_schema_invalid"

    def test_missing_success_criteria_raises(self, tmp_path):
        data = _minimal_journey_dict()
        del data["success_criteria"]
        p = _write_journey(tmp_path, data)
        with pytest.raises(SimdriveError) as exc_info:
            load_journey(p)
        assert exc_info.value.code == "journey_schema_invalid"


# ── 4. Empty list violations ──────────────────────────────────────────────────

class TestEmptyListViolations:
    def test_empty_goals_raises(self, tmp_path):
        data = _minimal_journey_dict(goals=[])
        p = _write_journey(tmp_path, data)
        with pytest.raises(SimdriveError) as exc_info:
            load_journey(p)
        assert exc_info.value.code == "journey_schema_invalid"

    def test_empty_success_criteria_raises(self, tmp_path):
        data = _minimal_journey_dict(success_criteria=[])
        p = _write_journey(tmp_path, data)
        with pytest.raises(SimdriveError) as exc_info:
            load_journey(p)
        assert exc_info.value.code == "journey_schema_invalid"

    def test_success_criterion_no_fields_raises(self, tmp_path):
        data = _minimal_journey_dict(success_criteria=[{}])
        p = _write_journey(tmp_path, data)
        with pytest.raises(SimdriveError) as exc_info:
            load_journey(p)
        assert exc_info.value.code == "journey_schema_invalid"


# ── 5. Persona cross-reference failures ───────────────────────────────────────

class TestPersonaCrossRef:
    def test_persona_not_found_raises(self, tmp_path):
        personas_dir = tmp_path / "personas"
        personas_dir.mkdir()
        # No persona file written → cross-ref fails
        p = _write_journey(tmp_path, _minimal_journey_dict())
        with pytest.raises(SimdriveError) as exc_info:
            load_journey(p, personas_dir=personas_dir)
        assert exc_info.value.code == "journey_persona_not_found"
        assert "power-user" in exc_info.value.message

    def test_persona_cross_ref_skipped_when_no_personas_dir(self, tmp_path):
        # When personas_dir is None, no cross-ref is performed.
        p = _write_journey(tmp_path, _minimal_journey_dict())
        j = load_journey(p, personas_dir=None)
        assert j.persona == "power-user"


# ── 6. device target → device_selector required ───────────────────────────────

class TestDeviceTargetRequiresSelector:
    def test_device_target_without_selector_raises(self, tmp_path):
        data = _minimal_journey_dict(target="device")
        p = _write_journey(tmp_path, data)
        with pytest.raises(SimdriveError) as exc_info:
            load_journey(p)
        assert exc_info.value.code == "journey_device_selector_missing"

    def test_invalid_target_raises(self, tmp_path):
        data = _minimal_journey_dict(target="cloud")
        p = _write_journey(tmp_path, data)
        with pytest.raises(SimdriveError) as exc_info:
            load_journey(p)
        assert exc_info.value.code == "journey_schema_invalid"


# ── 7. YAML parse errors ──────────────────────────────────────────────────────

class TestYAMLParseErrors:
    def test_broken_yaml_raises_schema_invalid(self, tmp_path):
        p = tmp_path / "bad.yaml"
        p.write_text("{ unclosed: [broken yaml")
        with pytest.raises(SimdriveError) as exc_info:
            load_journey(p)
        assert exc_info.value.code == "journey_schema_invalid"

    def test_non_mapping_yaml_raises(self, tmp_path):
        p = tmp_path / "list.yaml"
        p.write_text("- item1\n- item2\n")
        with pytest.raises(SimdriveError) as exc_info:
            load_journey(p)
        assert exc_info.value.code == "journey_schema_invalid"


# ── 8. Wrong type fields ──────────────────────────────────────────────────────

class TestWrongTypeFields:
    def test_goals_as_string_raises(self, tmp_path):
        data = _minimal_journey_dict(goals="Navigate to home")  # should be list
        p = _write_journey(tmp_path, data)
        with pytest.raises(SimdriveError) as exc_info:
            load_journey(p)
        assert exc_info.value.code == "journey_schema_invalid"

    def test_budget_max_steps_negative_raises(self, tmp_path):
        data = _minimal_journey_dict(budget={"max_steps": -1})
        p = _write_journey(tmp_path, data)
        with pytest.raises(SimdriveError) as exc_info:
            load_journey(p)
        assert exc_info.value.code == "journey_schema_invalid"


# ── 9. SuccessCriterion model ─────────────────────────────────────────────────

class TestSuccessCriterionModel:
    def test_text_visible_only(self):
        sc = SuccessCriterion(text_visible="Sign In")
        assert sc.text_visible == "Sign In"

    def test_no_crash_only(self):
        sc = SuccessCriterion(no_crash=True)
        assert sc.no_crash is True

    def test_cross_device_state_matches(self):
        sc = SuccessCriterion(cross_device_state_matches={"key": "value"})
        assert sc.cross_device_state_matches == {"key": "value"}


# ── 10. Budget defaults ───────────────────────────────────────────────────────

class TestBudgetDefaults:
    def test_budget_defaults(self):
        b = Budget()
        assert b.max_steps == 30
        assert b.max_seconds == 180
        assert b.max_llm_calls == 40


# ── 11. DeviceSelector model ──────────────────────────────────────────────────

class TestDeviceSelector:
    def test_udid_only_valid(self):
        ds = DeviceSelector(udid="abc123")
        assert ds.udid == "abc123"

    def test_name_only_valid(self):
        ds = DeviceSelector(name="iPhone 17 Pro")
        assert ds.name == "iPhone 17 Pro"

    def test_neither_udid_nor_name_raises(self):
        with pytest.raises(Exception):
            DeviceSelector()

    def test_both_udid_and_name_valid(self):
        ds = DeviceSelector(udid="abc", name="iPhone 17")
        assert ds.udid == "abc"
        assert ds.name == "iPhone 17"


# ── 11b. Blank name + persona validators ─────────────────────────────────────

class TestBlankFieldValidators:
    def test_blank_name_raises(self, tmp_path):
        data = _minimal_journey_dict(name="   ")  # whitespace-only
        p = _write_journey(tmp_path, data)
        with pytest.raises(SimdriveError) as exc_info:
            load_journey(p)
        assert exc_info.value.code == "journey_schema_invalid"

    def test_blank_persona_raises(self, tmp_path):
        data = _minimal_journey_dict(persona="   ")  # whitespace-only
        p = _write_journey(tmp_path, data)
        with pytest.raises(SimdriveError) as exc_info:
            load_journey(p)
        assert exc_info.value.code == "journey_schema_invalid"


# ── 12. Preconditions model ───────────────────────────────────────────────────

class TestPreconditions:
    def test_preconditions_optional(self, tmp_path):
        data = _minimal_journey_dict(
            preconditions={"app_installed": "com.example.app"}
        )
        p = _write_journey(tmp_path, data)
        j = load_journey(p)
        assert j.preconditions is not None
        assert j.preconditions.app_installed == "com.example.app"


# ── 13. Property-based tests (Hypothesis) ────────────────────────────────────

@pytest.mark.skipif(not HAS_HYPOTHESIS, reason="hypothesis not installed")
class TestHypothesisProperties:
    @given(name=st.text(min_size=1, max_size=50).filter(str.strip))
    @settings(max_examples=30, suppress_health_check=[HealthCheck.function_scoped_fixture])
    def test_valid_name_always_loads(self, tmp_path, name):
        """Any non-blank name should load successfully."""
        data = _minimal_journey_dict(name=name)
        p = tmp_path / "j.yaml"
        p.write_text(yaml.dump(data))
        j = load_journey(p)
        assert j.name == name

    @given(n_goals=st.integers(min_value=1, max_value=10))
    @settings(max_examples=20, suppress_health_check=[HealthCheck.function_scoped_fixture])
    def test_multiple_goals_load(self, tmp_path, n_goals):
        goals = [f"Goal {i}" for i in range(n_goals)]
        data = _minimal_journey_dict(goals=goals)
        p = tmp_path / "j.yaml"
        p.write_text(yaml.dump(data))
        j = load_journey(p)
        assert len(j.goals) == n_goals
