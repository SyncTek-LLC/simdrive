"""Tests for tool_load_journey — the agent-first journey loader.

tool_load_journey was added in 1.0.0a7 to replace tool_run_journey on the MCP
surface. It returns parsed journey data (goals, success_criteria, budget, etc.)
so the MCP-host agent can drive simdrive primitives directly without any LLM
call inside simdrive — and without requiring an API key or MCP sampling support.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_FIXTURES = Path(__file__).parent / "fixtures" / "journey_cycle1_smoke"
_JOURNEY_YAML = _FIXTURES / "journey.yaml"
_PERSONA_YAML = _FIXTURES / "persona.yaml"


# ── basic happy path ──────────────────────────────────────────────────────────


def test_load_journey_returns_parsed_data():
    """tool_load_journey returns ok=True and journey dict with expected fields."""
    from simdrive.server import tool_load_journey

    result = tool_load_journey({"path": str(_JOURNEY_YAML)})
    assert result["ok"] is True
    journey = result["journey"]
    assert journey["name"] == "cycle1_smoke"
    assert isinstance(journey["goals"], list)
    assert len(journey["goals"]) >= 1
    assert isinstance(journey["success_criteria"], list)
    assert len(journey["success_criteria"]) >= 1
    assert isinstance(journey["budget"], dict)
    assert "max_steps" in journey["budget"]
    assert result["persona"] is None  # no persona_path supplied


def test_load_journey_with_persona():
    """When persona_path is supplied, tool_load_journey returns persona data."""
    from simdrive.server import tool_load_journey

    result = tool_load_journey({"path": str(_JOURNEY_YAML), "persona_path": str(_PERSONA_YAML)})
    assert result["ok"] is True
    persona = result["persona"]
    assert persona is not None
    assert "slug" in persona
    assert "name" in persona
    assert "role" in persona


def test_load_journey_journey_fields_complete():
    """Journey dict contains all expected fields."""
    from simdrive.server import tool_load_journey

    result = tool_load_journey({"path": str(_JOURNEY_YAML)})
    journey = result["journey"]
    for key in ("name", "persona", "target", "goals", "success_criteria", "budget", "tags"):
        assert key in journey, f"Missing key {key!r} in journey dict"


def test_load_journey_missing_path_raises():
    """tool_load_journey raises KeyError when 'path' argument is absent."""
    from simdrive.server import tool_load_journey

    with pytest.raises(KeyError):
        tool_load_journey({})


def test_load_journey_bad_path_raises():
    """tool_load_journey raises SimdriveError when the file does not exist."""
    from simdrive.server import tool_load_journey
    from simdrive.errors import SimdriveError

    with pytest.raises((SimdriveError, FileNotFoundError, Exception)):
        tool_load_journey({"path": "/nonexistent/journey.yaml"})


# ── no-anthropic guarantee ────────────────────────────────────────────────────


def test_load_journey_no_anthropic_import():
    """Calling tool_load_journey does not trigger an anthropic import.

    This is the critical invariant: tool_load_journey must be usable from any
    MCP client regardless of whether anthropic is installed.
    """
    _SENTINEL = object()
    original = sys.modules.get("anthropic", _SENTINEL)
    sys.modules["anthropic"] = None  # type: ignore[assignment]

    try:
        # Re-import the handler under the mask.
        mods_to_remove = [k for k in sys.modules if k.startswith("simdrive")]
        for mod in mods_to_remove:
            sys.modules.pop(mod, None)

        try:
            from simdrive.server import tool_load_journey
            result = tool_load_journey({"path": str(_JOURNEY_YAML)})
            assert result["ok"] is True
        except ModuleNotFoundError as exc:
            if "anthropic" in str(exc).lower():
                pytest.fail(
                    f"tool_load_journey triggered an anthropic import: {exc}. "
                    "tool_load_journey must be usable without the anthropic package."
                )
            raise  # Other ModuleNotFoundErrors may indicate real missing deps in CI.
    finally:
        # Restore anthropic module state.
        if original is _SENTINEL:
            sys.modules.pop("anthropic", None)
        else:
            sys.modules["anthropic"] = original  # type: ignore[assignment]

        # Clean up freshly imported simdrive modules (partial-init'd without anthropic).
        for k in list(sys.modules):
            if k.startswith("simdrive"):
                sys.modules.pop(k, None)
