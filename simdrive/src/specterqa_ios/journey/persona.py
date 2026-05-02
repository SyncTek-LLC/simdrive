"""Persona YAML schema + validator — Component 2.

Public surface:
  Persona          — Pydantic v2 model for a validated persona file
  load_persona     — load + validate a persona YAML file; raises SimdriveError on any fault

A persona captures who the simulated user is so the runner can inject role,
comfort level, goals, and frustrations into every Claude prompt for realistic
agent behaviour.

Schema version 1 is the only supported version in this build.
"""
from __future__ import annotations

from pathlib import Path
from typing import Literal, Optional

import yaml
from pydantic import BaseModel, Field, field_validator

from .errors import (
    persona_schema_invalid,
    persona_schema_version_unsupported,
)

_SUPPORTED_SCHEMA_VERSION = 1

# Canonical comfort levels — keeps prompts and tests predictable.
TechnicalComfortLevel = Literal["novice", "intermediate", "advanced", "expert"]

# Patience affects how many retries the runner tolerates before declaring fail.
PatienceLevel = Literal["low", "medium", "high"]


class AccessibilityNeeds(BaseModel):
    """Optional accessibility configuration injected into prompt context."""

    large_text: bool = False
    voice_over: bool = False
    reduce_motion: bool = False
    high_contrast: bool = False


class Persona(BaseModel):
    """Validated persona model.

    Construct via load_persona(path) — the loader does YAML parsing and
    schema_version checks before Pydantic validation.
    """

    schema_version: int
    slug: str  # filesystem name (no spaces, URL-safe)
    name: str  # display name used in prompts ("Alice", "Power User Bob")
    role: str  # one-sentence description injected at system prompt time
    technical_comfort: TechnicalComfortLevel = "intermediate"
    patience: PatienceLevel = "medium"
    goals: list[str] = Field(default_factory=list)
    frustrations: list[str] = Field(default_factory=list)
    accessibility_needs: AccessibilityNeeds = Field(default_factory=AccessibilityNeeds)
    locale: str = "en-US"  # BCP-47 locale tag; injected into prompts for L10N tests
    # Freeform notes surfaced in agent_trace.jsonl but not in prompts.
    notes: Optional[str] = None

    @field_validator("schema_version")
    @classmethod
    def _check_schema_version(cls, v: int) -> int:
        if v != _SUPPORTED_SCHEMA_VERSION:
            raise ValueError(f"unsupported schema_version {v!r}")
        return v

    @field_validator("slug")
    @classmethod
    def _slug_valid(cls, v: str) -> str:
        import re
        if not v.strip():
            raise ValueError("slug must not be blank")
        if not re.match(r"^[a-z0-9_-]+$", v):
            raise ValueError(
                f"slug {v!r} must contain only lowercase letters, digits, hyphens, underscores"
            )
        return v

    @field_validator("name")
    @classmethod
    def _name_not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("name must not be blank")
        return v

    @field_validator("role")
    @classmethod
    def _role_not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("role must not be blank")
        return v


def load_persona(path: str | Path) -> Persona:
    """Load and validate a persona YAML file.

    Steps:
    1. Read + yaml.safe_load the file.
    2. Check schema_version == 1.
    3. Pydantic-validate the full document.

    Raises SimdriveError on any validation failure.
    """
    path = Path(path)
    path_str = str(path)

    # Step 1 — raw load
    try:
        raw = yaml.safe_load(path.read_text())
    except Exception as exc:
        raise persona_schema_invalid(path_str, f"YAML parse error: {exc}") from exc

    if not isinstance(raw, dict):
        raise persona_schema_invalid(path_str, "top-level document must be a YAML mapping")

    # Step 2 — schema_version pre-check
    sv = raw.get("schema_version")
    if sv != _SUPPORTED_SCHEMA_VERSION:
        raise persona_schema_version_unsupported(sv)

    # Step 3 — Pydantic validation
    try:
        return Persona.model_validate(raw)
    except Exception as exc:
        raise persona_schema_invalid(path_str, str(exc)) from exc
