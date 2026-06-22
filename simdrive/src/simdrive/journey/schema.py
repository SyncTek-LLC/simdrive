"""Journey YAML schema + validator — Component 1.

Public surface:
  Journey          — Pydantic v2 model for a validated journey file
  load_journey     — load + validate a journey YAML file; raises SimdriveError on any fault

Schema version 1 is the only supported version in this build.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Literal, Optional

import yaml
from pydantic import BaseModel, Field, field_validator, model_validator

from .errors import (
    journey_device_selector_missing,
    journey_persona_not_found,
    journey_schema_invalid,
    journey_schema_version_unsupported,
)

# The only schema_version this build recognises.
_SUPPORTED_SCHEMA_VERSION = 1


class SuccessCriterion(BaseModel):
    """One pass/fail assertion checked at each step of the runner loop."""

    text_visible: Optional[str] = None
    screen_matches: Optional[str] = None  # stable_id
    perf_under: Optional[dict[str, float]] = None  # keys: cpu_pct, memory_mb
    no_crash: Optional[bool] = None
    # 1.0 stretch — pass-through when not configured (never fail-closed)
    cross_device_state_matches: Optional[dict[str, Any]] = None
    # Host-AX: a VoiceOver announcement (substring, case-insensitive) the app
    # must have posted during the journey (simulator sessions only).
    announcement_heard: Optional[str] = None

    @model_validator(mode="after")
    def _at_least_one_criterion(self) -> "SuccessCriterion":
        # Every criterion entry must set at least one field.
        if all(
            v is None
            for v in (
                self.text_visible,
                self.screen_matches,
                self.perf_under,
                self.no_crash,
                self.cross_device_state_matches,
                self.announcement_heard,
            )
        ):
            raise ValueError(
                "SuccessCriterion must set at least one field (text_visible, "
                "screen_matches, perf_under, no_crash, cross_device_state_matches, "
                "announcement_heard)"
            )
        return self


class DeviceSelector(BaseModel):
    """Optional device targeting — required when target='device'."""

    udid: Optional[str] = None
    name: Optional[str] = None
    os_version: Optional[str] = None

    @model_validator(mode="after")
    def _udid_or_name_required(self) -> "DeviceSelector":
        if not self.udid and not self.name:
            raise ValueError("DeviceSelector requires at least `udid` or `name`")
        return self


class Preconditions(BaseModel):
    """Optional preconditions that must be met before the runner begins."""

    app_installed: Optional[str] = None  # bundle_id
    app_state: Optional[Literal["foreground", "background", "not_running"]] = None
    custom: Optional[list[str]] = None  # freeform strings surfaced in agent_trace


class Budget(BaseModel):
    """Runner resource limits — defaults match the spec."""

    max_steps: int = Field(default=30, ge=1)
    max_seconds: int = Field(default=180, ge=1)
    max_llm_calls: int = Field(default=40, ge=1)


class Journey(BaseModel):
    """Validated journey model.

    Construct via load_journey(path) rather than directly — the loader does
    YAML parsing, schema_version checks, and persona-slug cross-referencing.
    """

    schema_version: int
    name: str
    persona: str  # slug → .simdrive/personas/<slug>.yaml
    target: Literal["simulator", "device"] = "simulator"
    device_selector: Optional[DeviceSelector] = None
    preconditions: Optional[Preconditions] = None
    goals: list[str] = Field(min_length=1)
    success_criteria: list[SuccessCriterion] = Field(min_length=1)
    budget: Budget = Field(default_factory=Budget)
    replay_id: Optional[str] = None
    tags: list[str] = Field(default_factory=list)
    app_bundle_id: Optional[str] = None  # override the session's default bundle

    @field_validator("schema_version")
    @classmethod
    def _check_schema_version(cls, v: int) -> int:
        if v != _SUPPORTED_SCHEMA_VERSION:
            raise ValueError(f"unsupported schema_version {v!r}")
        return v

    @field_validator("name")
    @classmethod
    def _name_not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("name must not be blank")
        return v

    @field_validator("persona")
    @classmethod
    def _persona_not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("persona must not be blank")
        return v


def load_journey(
    path: str | Path,
    personas_dir: str | Path | None = None,
) -> Journey:
    """Load and validate a journey YAML file.

    Steps:
    1. Read + yaml.safe_load the file.
    2. Check schema_version == 1 (before full Pydantic parse so the error is clear).
    3. Pydantic-validate the full document.
    4. Cross-ref persona slug exists in personas_dir (when provided).
    5. Enforce device_selector required when target=device.

    Raises SimdriveError on any validation failure so callers never need to
    catch Pydantic's ValidationError directly.
    """
    path = Path(path)
    path_str = str(path)

    # Step 1 — raw load
    try:
        raw = yaml.safe_load(path.read_text())
    except Exception as exc:
        raise journey_schema_invalid(path_str, f"YAML parse error: {exc}") from exc

    if not isinstance(raw, dict):
        raise journey_schema_invalid(path_str, "top-level document must be a YAML mapping")

    # Step 2 — schema_version pre-check for a friendlier error message
    sv = raw.get("schema_version")
    if sv != _SUPPORTED_SCHEMA_VERSION:
        raise journey_schema_version_unsupported(sv)

    # Step 3 — Pydantic validation
    try:
        journey = Journey.model_validate(raw)
    except Exception as exc:
        raise journey_schema_invalid(path_str, str(exc)) from exc

    # Step 4 — persona cross-reference
    if personas_dir is not None:
        personas_dir = Path(personas_dir)
        persona_path = personas_dir / f"{journey.persona}.yaml"
        if not persona_path.exists():
            raise journey_persona_not_found(journey.persona, str(personas_dir))

    # Step 5 — device_selector required when target=device
    if journey.target == "device" and journey.device_selector is None:
        raise journey_device_selector_missing(journey.name)

    return journey
