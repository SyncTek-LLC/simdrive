"""MCP sampling-based LLM client for the journey runner.

Uses the connected MCP client's LLM via `session.create_message(...)` instead
of bringing our own anthropic.Anthropic instance. simdrive becomes pure tools;
the driving agent (Claude Code, Cline, etc.) supplies its own LLM and
credentials.

CRITICAL: this module MUST NOT import `anthropic`. It only depends on the
mcp SDK types (TextContent, ImageContent, SamplingMessage, ModelPreferences,
CreateMessageResult).

[internal-tracker].
"""
from __future__ import annotations

import base64
import json
import logging
from pathlib import Path
from typing import Any, Optional

import mcp.types as mtypes

from .runner import StepDecision

log = logging.getLogger("simdrive.journey.mcp_sampling_client")


def _parse_decision(text: str) -> StepDecision:
    """Parse the model's JSON response into a StepDecision.

    Falls back to a 'fail' decision containing 'parse error' in the rationale
    when the response cannot be parsed (matches the contract enforced by tests).
    """
    try:
        data = json.loads(text)
        return StepDecision(
            tool=data["tool"],
            args=data.get("args", {}),
            rationale=data.get("rationale", ""),
            confidence=float(data.get("confidence", 0.5)),
        )
    except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        log.warning("Sampling response unparseable, falling back to fail: %s", exc)
        return StepDecision(
            tool="fail",
            args={},
            rationale=f"LLM response parse error: {exc}",
            confidence=0.0,
        )


class MCPSamplingLLMClient:
    """LLMClient that delegates to the connected MCP client via sampling.

    Compatible with the LLMClient Protocol in runner.py. Used by tool_run_journey
    so simdrive does not require an Anthropic API key — the agent driving the
    MCP session uses its own credentials.

    Parameters
    ----------
    session:
        An mcp.server.session.ServerSession instance acquired from
        ``server.request_context.session`` inside an async tool handler.
    """

    def __init__(self, session: Any) -> None:
        self._session = session
        self._cost = 0.0  # sampling cost is tracked by the client host; we report 0

    async def call(
        self,
        system_prompt: str,
        user_prompt: str,
        screenshot_path: Optional[str],
    ) -> StepDecision:
        """Make one LLM call via MCP sampling and return the parsed StepDecision.

        Builds a SamplingMessage with text content (and optional image content
        when screenshot_path is provided).  Delegates model selection to the
        MCP client via ModelPreferences with high intelligencePriority so the
        client picks a capable reasoning model for journey decisions.
        """
        # Build the content block(s) for the user SamplingMessage.
        # mcp.types.SamplingMessage.content accepts either a single content
        # block OR a list — we use a list when there is a screenshot so both
        # the image and the text are included in a single message.
        content_blocks: list = [
            mtypes.TextContent(type="text", text=user_prompt),
        ]

        if screenshot_path:
            p = Path(screenshot_path)
            if p.exists():
                try:
                    img_b64 = base64.b64encode(p.read_bytes()).decode("ascii")
                    ext = p.suffix.lower().lstrip(".")
                    mime = "image/jpeg" if ext in ("jpg", "jpeg") else f"image/{ext or 'png'}"
                    content_blocks.append(
                        mtypes.ImageContent(type="image", data=img_b64, mimeType=mime)
                    )
                except Exception as exc:
                    log.warning("Could not load screenshot %r for MCP sampling: %s", screenshot_path, exc)

        # SamplingMessage accepts a single block or a list — pass list directly
        # when there are multiple content blocks (text + image).
        message_content: Any = content_blocks if len(content_blocks) > 1 else content_blocks[0]

        messages = [
            mtypes.SamplingMessage(
                role="user",
                content=message_content,
            ),
        ]

        # Request a capable model — journey decisions require multi-step reasoning.
        prefs = mtypes.ModelPreferences(
            intelligencePriority=0.9,
            speedPriority=0.3,
            costPriority=0.2,
        )

        result = await self._session.create_message(
            messages=messages,
            max_tokens=2048,
            system_prompt=system_prompt,
            model_preferences=prefs,
        )

        # CreateMessageResult.content is a single SamplingContent block
        # (TextContent | ImageContent | AudioContent).  We duck-type on the
        # `type` attribute rather than isinstance so test SimpleNamespace fakes
        # work without being real mcp.types instances.
        content = result.content
        if getattr(content, "type", None) == "text" and hasattr(content, "text"):
            text = content.text
        else:
            log.warning(
                "MCP sampling returned non-text content (%s); falling back to fail",
                getattr(content, "type", type(content).__name__),
            )
            return StepDecision(
                tool="fail",
                args={},
                rationale=(
                    f"LLM response parse error: non-text sampling response type "
                    f"'{getattr(content, 'type', type(content).__name__)}'"
                ),
                confidence=0.0,
            )

        return _parse_decision(text)

    @property
    def cost_usd(self) -> float:
        """Sampling-based clients don't track cost — the client host bills its user."""
        return self._cost
