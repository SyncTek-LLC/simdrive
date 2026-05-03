"""Anthropic-SDK-backed LLM client for the journey runner.

Conforms to the LLMClient Protocol defined in runner.py. Wraps
``anthropic.Anthropic`` and parses the response into a ``StepDecision``.

Cost tracking is cumulative across calls (``cost_usd`` property). The SDK
exposes ``response.usage.input_tokens`` and ``response.usage.output_tokens``
from every message response; we price them at the claude-opus-4-7 rates used
at model selection time. If usage is unavailable we fall back to the
``_APPROX_COST_PER_CALL_USD`` estimate defined in runner.py.

Model: ``claude-opus-4-7`` (most capable, per BusinessAtlas memory).
"""
from __future__ import annotations

import json
import logging
import os
from typing import Optional

import anthropic

from .runner import StepDecision

log = logging.getLogger("simdrive.journey.claude_client")

# claude-opus-4-7 pricing (USD per 1M tokens, as of 2026-05).
# Source: Anthropic pricing page. Update when pricing changes.
_INPUT_COST_PER_M = 15.0   # $ / 1M input tokens
_OUTPUT_COST_PER_M = 75.0  # $ / 1M output tokens

_MODEL = "claude-opus-4-7"

# Fallback cost estimate when usage is unavailable.
_FALLBACK_COST_PER_CALL_USD = 0.004


def _compute_cost(input_tokens: int, output_tokens: int) -> float:
    return (
        input_tokens * _INPUT_COST_PER_M / 1_000_000
        + output_tokens * _OUTPUT_COST_PER_M / 1_000_000
    )


def _parse_decision(text: str) -> StepDecision:
    """Parse the model's JSON response into a StepDecision.

    The model is prompted to respond with a JSON object with keys:
      tool, args, rationale, confidence

    Falls back to a ``fail`` decision when the response cannot be parsed.
    """
    try:
        data = json.loads(text)
        return StepDecision(
            tool=data["tool"],
            args=data.get("args", {}),
            rationale=data.get("rationale", ""),
            confidence=float(data.get("confidence", 0.5)),
        )
    except Exception as exc:
        log.warning("Failed to parse LLM response as JSON (%s); defaulting to fail. Raw: %r", exc, text[:200])
        return StepDecision(
            tool="fail",
            args={},
            rationale=f"LLM response parse error: {exc}",
            confidence=0.0,
        )


class ClaudeLLMClient:
    """Anthropic-SDK-backed implementation of the LLMClient Protocol.

    Parameters
    ----------
    api_key:
        Anthropic API key. Defaults to ``ANTHROPIC_API_KEY`` env var (the
        SDK's own default behaviour). Explicitly passing ``None`` lets the SDK
        auto-discover the key.
    model:
        Model ID. Defaults to ``claude-opus-4-7``.
    max_tokens:
        Max tokens for each response. Defaults to 1024 (sufficient for a
        JSON action decision).
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: str = _MODEL,
        max_tokens: int = 1024,
    ) -> None:
        self._client = anthropic.Anthropic(
            api_key=api_key or os.environ.get("ANTHROPIC_API_KEY"),
        )
        self._model = model
        self._max_tokens = max_tokens
        self._cost_usd: float = 0.0
        self._call_count: int = 0

    # ── LLMClient Protocol ────────────────────────────────────────────────────

    def call(
        self,
        system_prompt: str,
        user_prompt: str,
        screenshot_path: Optional[str],
    ) -> StepDecision:
        """Make one LLM vision call and return the parsed action decision.

        If ``screenshot_path`` is provided the image is loaded and sent as a
        vision block before the text prompt. The model is instructed to return
        a single JSON object.
        """
        messages: list[dict] = []

        # Build user message — vision block (if screenshot) + text
        user_content: list[dict] = []
        if screenshot_path:
            try:
                import base64
                with open(screenshot_path, "rb") as fh:
                    img_b64 = base64.standard_b64encode(fh.read()).decode()
                # Determine media type from extension
                ext = screenshot_path.rsplit(".", 1)[-1].lower()
                media_type = "image/jpeg" if ext in ("jpg", "jpeg") else "image/png"
                user_content.append({
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": media_type,
                        "data": img_b64,
                    },
                })
            except Exception as exc:
                log.warning("Could not load screenshot %r: %s", screenshot_path, exc)

        user_content.append({"type": "text", "text": user_prompt})
        messages.append({"role": "user", "content": user_content})

        try:
            response = self._client.messages.create(
                model=self._model,
                max_tokens=self._max_tokens,
                system=system_prompt,
                messages=messages,
            )
        except Exception as exc:
            log.error("Anthropic API call failed: %s", exc)
            # Raise so the runner can catch and wrap as claude_call_failed
            raise

        # Track cost
        self._call_count += 1
        try:
            usage = response.usage
            call_cost = _compute_cost(usage.input_tokens, usage.output_tokens)
        except Exception:
            call_cost = _FALLBACK_COST_PER_CALL_USD
        self._cost_usd += call_cost

        # Extract text content
        text = ""
        for block in response.content:
            if hasattr(block, "text"):
                text = block.text
                break

        return _parse_decision(text)

    @property
    def cost_usd(self) -> float:
        """Cumulative USD cost of all calls made on this client instance."""
        return self._cost_usd
