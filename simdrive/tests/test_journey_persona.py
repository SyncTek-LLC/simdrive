"""Tests for journey/persona.py — Component 2.

Coverage target: 100% of persona.py.
"""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
import yaml

try:
    from hypothesis import given, settings, HealthCheck
    from hypothesis import strategies as st
    HAS_HYPOTHESIS = True
except ImportError:
    HAS_HYPOTHESIS = False

from specterqa_ios.errors import SimdriveError
from specterqa_ios.journey.persona import (
    AccessibilityNeeds,
    Persona,
    load_persona,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _minimal_persona_dict(**overrides) -> dict:
    base = {
        "schema_version": 1,
        "slug": "power-user",
        "name": "Alice",
        "role": "A power user who knows the app well",
    }
    base.update(overrides)
    return base


def _write_persona(tmp_path: Path, data: dict, filename: str = "persona.yaml") -> Path:
    p = tmp_path / filename
    p.write_text(yaml.dump(data))
    return p


# ── 1. Happy path ─────────────────────────────────────────────────────────────

class TestLoadPersonaHappyPath:
    def test_minimal_valid_persona(self, tmp_path):
        p = _write_persona(tmp_path, _minimal_persona_dict())
        persona = load_persona(p)
        assert persona.slug == "power-user"
        assert persona.name == "Alice"
        assert persona.role == "A power user who knows the app well"

    def test_defaults_applied(self, tmp_path):
        p = _write_persona(tmp_path, _minimal_persona_dict())
        persona = load_persona(p)
        assert persona.technical_comfort == "intermediate"
        assert persona.patience == "medium"
        assert persona.locale == "en-US"
        assert persona.goals == []
        assert persona.frustrations == []

    def test_full_persona_with_all_fields(self, tmp_path):
        data = _minimal_persona_dict(
            technical_comfort="advanced",
            patience="low",
            goals=["Find a book quickly", "Complete checkout"],
            frustrations=["Slow loading screens"],
            locale="fr-FR",
            notes="This persona is for performance testing",
            accessibility_needs={
                "large_text": True,
                "voice_over": False,
                "reduce_motion": True,
                "high_contrast": False,
            },
        )
        p = _write_persona(tmp_path, data)
        persona = load_persona(p)
        assert persona.technical_comfort == "advanced"
        assert persona.patience == "low"
        assert persona.locale == "fr-FR"
        assert len(persona.goals) == 2
        assert persona.accessibility_needs.large_text is True
        assert persona.accessibility_needs.reduce_motion is True

    def test_slug_with_hyphens_valid(self, tmp_path):
        data = _minimal_persona_dict(slug="new-user-beginner")
        p = _write_persona(tmp_path, data)
        persona = load_persona(p)
        assert persona.slug == "new-user-beginner"

    def test_slug_with_underscores_valid(self, tmp_path):
        data = _minimal_persona_dict(slug="power_user_v2")
        p = _write_persona(tmp_path, data)
        persona = load_persona(p)
        assert persona.slug == "power_user_v2"


# ── 2. Schema version mismatch ────────────────────────────────────────────────

class TestSchemaVersion:
    def test_schema_version_2_raises(self, tmp_path):
        data = _minimal_persona_dict(schema_version=2)
        p = _write_persona(tmp_path, data)
        with pytest.raises(SimdriveError) as exc_info:
            load_persona(p)
        assert exc_info.value.code == "persona_schema_version_unsupported"

    def test_schema_version_0_raises(self, tmp_path):
        data = _minimal_persona_dict(schema_version=0)
        p = _write_persona(tmp_path, data)
        with pytest.raises(SimdriveError) as exc_info:
            load_persona(p)
        assert exc_info.value.code == "persona_schema_version_unsupported"

    def test_schema_version_missing_raises(self, tmp_path):
        data = _minimal_persona_dict()
        del data["schema_version"]
        p = _write_persona(tmp_path, data)
        with pytest.raises(SimdriveError) as exc_info:
            load_persona(p)
        assert exc_info.value.code in (
            "persona_schema_version_unsupported",
            "persona_schema_invalid",
        )


# ── 3. Missing required fields ────────────────────────────────────────────────

class TestMissingRequiredFields:
    def test_missing_slug_raises(self, tmp_path):
        data = _minimal_persona_dict()
        del data["slug"]
        p = _write_persona(tmp_path, data)
        with pytest.raises(SimdriveError) as exc_info:
            load_persona(p)
        assert exc_info.value.code == "persona_schema_invalid"

    def test_missing_name_raises(self, tmp_path):
        data = _minimal_persona_dict()
        del data["name"]
        p = _write_persona(tmp_path, data)
        with pytest.raises(SimdriveError) as exc_info:
            load_persona(p)
        assert exc_info.value.code == "persona_schema_invalid"

    def test_missing_role_raises(self, tmp_path):
        data = _minimal_persona_dict()
        del data["role"]
        p = _write_persona(tmp_path, data)
        with pytest.raises(SimdriveError) as exc_info:
            load_persona(p)
        assert exc_info.value.code == "persona_schema_invalid"


# ── 4. Invalid slug formats ───────────────────────────────────────────────────

class TestSlugValidation:
    def test_slug_with_spaces_raises(self, tmp_path):
        data = _minimal_persona_dict(slug="power user")
        p = _write_persona(tmp_path, data)
        with pytest.raises(SimdriveError) as exc_info:
            load_persona(p)
        assert exc_info.value.code == "persona_schema_invalid"

    def test_slug_with_uppercase_raises(self, tmp_path):
        data = _minimal_persona_dict(slug="PowerUser")
        p = _write_persona(tmp_path, data)
        with pytest.raises(SimdriveError) as exc_info:
            load_persona(p)
        assert exc_info.value.code == "persona_schema_invalid"

    def test_slug_with_special_chars_raises(self, tmp_path):
        data = _minimal_persona_dict(slug="user@v2")
        p = _write_persona(tmp_path, data)
        with pytest.raises(SimdriveError) as exc_info:
            load_persona(p)
        assert exc_info.value.code == "persona_schema_invalid"

    def test_empty_slug_raises(self, tmp_path):
        data = _minimal_persona_dict(slug="")
        p = _write_persona(tmp_path, data)
        with pytest.raises(SimdriveError) as exc_info:
            load_persona(p)
        assert exc_info.value.code == "persona_schema_invalid"


# ── 5. Wrong type fields ──────────────────────────────────────────────────────

class TestWrongTypeFields:
    def test_invalid_technical_comfort_raises(self, tmp_path):
        data = _minimal_persona_dict(technical_comfort="wizard")
        p = _write_persona(tmp_path, data)
        with pytest.raises(SimdriveError) as exc_info:
            load_persona(p)
        assert exc_info.value.code == "persona_schema_invalid"

    def test_invalid_patience_raises(self, tmp_path):
        data = _minimal_persona_dict(patience="very_low")
        p = _write_persona(tmp_path, data)
        with pytest.raises(SimdriveError) as exc_info:
            load_persona(p)
        assert exc_info.value.code == "persona_schema_invalid"

    def test_goals_as_string_raises(self, tmp_path):
        data = _minimal_persona_dict(goals="find a book")  # should be list
        p = _write_persona(tmp_path, data)
        with pytest.raises(SimdriveError) as exc_info:
            load_persona(p)
        assert exc_info.value.code == "persona_schema_invalid"


# ── 6. YAML parse errors ──────────────────────────────────────────────────────

class TestYAMLParseErrors:
    def test_broken_yaml_raises_schema_invalid(self, tmp_path):
        p = tmp_path / "bad.yaml"
        p.write_text("{ unclosed: [broken")
        with pytest.raises(SimdriveError) as exc_info:
            load_persona(p)
        assert exc_info.value.code == "persona_schema_invalid"

    def test_non_mapping_raises(self, tmp_path):
        p = tmp_path / "list.yaml"
        p.write_text("- item1\n- item2\n")
        with pytest.raises(SimdriveError) as exc_info:
            load_persona(p)
        assert exc_info.value.code == "persona_schema_invalid"


# ── 7. AccessibilityNeeds model ───────────────────────────────────────────────

class TestAccessibilityNeeds:
    def test_all_defaults_false(self):
        an = AccessibilityNeeds()
        assert an.large_text is False
        assert an.voice_over is False
        assert an.reduce_motion is False
        assert an.high_contrast is False

    def test_can_set_all_true(self):
        an = AccessibilityNeeds(
            large_text=True, voice_over=True,
            reduce_motion=True, high_contrast=True,
        )
        assert all([an.large_text, an.voice_over, an.reduce_motion, an.high_contrast])


# ── 7b. Blank field validators ────────────────────────────────────────────────

class TestBlankFieldValidators:
    def test_blank_name_raises(self, tmp_path):
        data = _minimal_persona_dict(name="   ")  # whitespace-only
        p = _write_persona(tmp_path, data)
        with pytest.raises(SimdriveError) as exc_info:
            load_persona(p)
        assert exc_info.value.code == "persona_schema_invalid"

    def test_blank_role_raises(self, tmp_path):
        data = _minimal_persona_dict(role="   ")  # whitespace-only
        p = _write_persona(tmp_path, data)
        with pytest.raises(SimdriveError) as exc_info:
            load_persona(p)
        assert exc_info.value.code == "persona_schema_invalid"


# ── 8. Property-based tests (Hypothesis) ─────────────────────────────────────

@pytest.mark.skipif(not HAS_HYPOTHESIS, reason="hypothesis not installed")
class TestHypothesisProperties:
    _VALID_SLUG_ALPHABET = "abcdefghijklmnopqrstuvwxyz0123456789-_"

    @given(
        slug=st.text(
            alphabet=_VALID_SLUG_ALPHABET,
            min_size=1,
            max_size=40,
        )
    )
    @settings(max_examples=30, suppress_health_check=[HealthCheck.function_scoped_fixture])
    def test_valid_slug_always_loads(self, tmp_path, slug):
        """Any slug using only valid chars should load without raising."""
        data = _minimal_persona_dict(slug=slug)
        p = tmp_path / "p.yaml"
        p.write_text(yaml.dump(data))
        persona = load_persona(p)
        assert persona.slug == slug

    @given(name=st.text(min_size=1, max_size=60).filter(str.strip))
    @settings(max_examples=20, suppress_health_check=[HealthCheck.function_scoped_fixture])
    def test_any_nonempty_name_loads(self, tmp_path, name):
        data = _minimal_persona_dict(name=name)
        p = tmp_path / "p.yaml"
        p.write_text(yaml.dump(data))
        persona = load_persona(p)
        assert persona.name == name
