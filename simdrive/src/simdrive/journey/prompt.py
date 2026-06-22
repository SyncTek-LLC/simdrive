"""Prompt assembly for the journey runner — Component 3.

assemble_system_prompt   — stable across all steps of a journey (Claude cache-friendly)
assemble_user_prompt     — per-step: observe payload + recent step history

Design principle: the system prompt is intentionally held stable so every
step re-uses the same cached prefix.  Only the user prompt changes per step.
This keeps Claude API costs low for long journeys (prompt caching re-uses the
system prefix after the first call).
"""
from __future__ import annotations

import json
from typing import Any

from .persona import Persona
from .result import StepRecord
from .schema import Journey


# ── System prompt (stable across journey steps) ──────────────────────────────

_SYSTEM_TEMPLATE = """\
You are an AI agent driving an iOS simulator on behalf of a user persona.
You will receive a screenshot observation and must decide the next action.

## Persona
Name: {persona_name}
Role: {persona_role}
Technical comfort: {technical_comfort}
Patience: {patience}
Goals: {persona_goals}
Frustrations: {persona_frustrations}
Locale: {locale}
{accessibility_block}

## Journey
Name: {journey_name}
Goals:
{journey_goals}

## Instructions
At each step you receive:
- The current screen observation (OCR text, marks with stable_ids, screenshot path)
- The last {history_size} steps you took (tool + rationale)
- The list of success criteria still unmet

You must respond with a JSON object with these fields:
  tool: one of "tap" | "swipe" | "type_text" | "press_key" | "clear_field" | "perform_accessibility_action" | "set_text" | "done" | "fail"
  args: a dict of arguments for the chosen tool (empty dict for "done"/"fail")
  rationale: one sentence explaining WHY you chose this action
  confidence: float 0.0-1.0 for how confident you are this action advances the goal

Use "done" when ALL success criteria are met.
Use "fail" only when the app is in a clearly broken state and no recovery is possible.

Use "perform_accessibility_action" (simulator only) to invoke a VoiceOver custom
action — args {{"name": "<action label>"}}, e.g. {{"name": "Where am I?"}}. These
are invisible to the screenshot; use them when a goal/criterion refers to a
VoiceOver rotor action or a spoken announcement.

Use "set_text" (simulator only) to enter text into a field that "type_text"
can't reach — notably a UIAlertController prompt (e.g. "Go to Page"). args
{{"text": "..."}} (add {{"identifier": "..."}} or {{"label": "..."}} to target a
specific field; otherwise the alert's field is used).

Prefer stable_id over pixel coordinates when stable_ids are visible in the marks.
Be precise with type_text — only type text explicitly required by the goal.
Never guess or hallucinate element positions.
"""

_ACCESSIBILITY_BLOCK_TEMPLATE = """\
Accessibility needs:
  Large text: {large_text}
  VoiceOver: {voice_over}
  Reduce motion: {reduce_motion}
  High contrast: {high_contrast}"""


def assemble_system_prompt(
    journey: Journey,
    persona: Persona,
    history_size: int = 3,
) -> str:
    """Build the stable system prompt for this journey × persona combination.

    Kept deterministic so tests can assert exact output.  No timestamps or
    random tokens — every field is sourced from the validated models.
    """
    accessibility_needs = persona.accessibility_needs
    has_any_accessibility = any([
        accessibility_needs.large_text,
        accessibility_needs.voice_over,
        accessibility_needs.reduce_motion,
        accessibility_needs.high_contrast,
    ])
    if has_any_accessibility:
        accessibility_block = _ACCESSIBILITY_BLOCK_TEMPLATE.format(
            large_text=accessibility_needs.large_text,
            voice_over=accessibility_needs.voice_over,
            reduce_motion=accessibility_needs.reduce_motion,
            high_contrast=accessibility_needs.high_contrast,
        )
    else:
        accessibility_block = ""

    persona_goals = "\n".join(f"- {g}" for g in persona.goals) if persona.goals else "(none)"
    persona_frustrations = (
        "\n".join(f"- {f}" for f in persona.frustrations) if persona.frustrations else "(none)"
    )
    journey_goals = "\n".join(f"{i+1}. {g}" for i, g in enumerate(journey.goals))

    return _SYSTEM_TEMPLATE.format(
        persona_name=persona.name,
        persona_role=persona.role,
        technical_comfort=persona.technical_comfort,
        patience=persona.patience,
        persona_goals=persona_goals,
        persona_frustrations=persona_frustrations,
        locale=persona.locale,
        accessibility_block=accessibility_block,
        journey_name=journey.name,
        journey_goals=journey_goals,
        history_size=history_size,
    )


# ── User prompt (changes every step) ─────────────────────────────────────────

def assemble_user_prompt(
    obs: dict,
    unmet_criteria: list[str],
    recent_steps: list[StepRecord],
    step_idx: int,
    budget_remaining: dict[str, Any],
) -> str:
    """Build the per-step user prompt.

    obs: the raw dict from tool_observe / observe.to_dict()
    unmet_criteria: human-readable descriptions of criteria not yet passed
    recent_steps: last N StepRecord objects (caller slices to history_size)
    step_idx: 1-based step number
    budget_remaining: {"steps": int, "seconds": float, "llm_calls": int}
    """
    lines: list[str] = [f"## Step {step_idx}"]

    # Observation section
    lines.append("\n### Screen observation")
    marks = obs.get("marks", [])
    if marks:
        lines.append(f"Marks visible ({len(marks)} total):")
        for m in marks[:20]:  # cap at 20 to keep prompt concise
            stable_id = m.get("stable_id", "")
            text = m.get("text", "")
            lines.append(f"  stable_id={stable_id!r}  text={text!r}")
    else:
        lines.append("No marks detected on this screen.")

    # OCR text
    # obs["marks"] has text per mark; also surface any top-level text key if present
    ocr_summary = obs.get("recent_logs") or ""
    if ocr_summary:
        lines.append(f"\nRecent logs (last tail):\n{ocr_summary[:500]}")

    # Screenshot path (for vision model)
    screenshot_path = obs.get("screenshot_path", "")
    if screenshot_path:
        lines.append(f"\nScreenshot: {screenshot_path}")

    # Unmet criteria
    lines.append("\n### Unmet success criteria")
    if unmet_criteria:
        for c in unmet_criteria:
            lines.append(f"  - {c}")
    else:
        lines.append("  All criteria currently met — use tool=done to finish.")

    # Recent step history
    if recent_steps:
        lines.append("\n### Recent steps (last 3)")
        for sr in recent_steps:
            args_summary = json.dumps(sr.args, separators=(",", ":"))[:80]
            lines.append(
                f"  Step {sr.step_idx}: {sr.tool}({args_summary}) "
                f"— {sr.rationale[:80]}"
            )

    # Budget remaining
    lines.append(
        f"\n### Budget remaining: "
        f"{budget_remaining.get('steps', '?')} steps, "
        f"{budget_remaining.get('seconds', '?'):.0f}s, "
        f"{budget_remaining.get('llm_calls', '?')} LLM calls"
    )

    lines.append("\nRespond with JSON only.")
    return "\n".join(lines)
