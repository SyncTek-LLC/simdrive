"""SpecterQA Computer Use Decider — Claude Computer Use API integration.

Implements the AIDecider protocol using Claude's Computer Use beta API,
enabling Claude to visually interpret iOS Simulator screenshots and decide
the next UI action.

Bundled in specterqa-ios (INIT-2026-493).
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from specterqa.engine.protocols import Decision

logger = logging.getLogger("specterqa.engine.computer_use_decider")

# Default display dimensions matching iPhone 15 Pro retina resolution
_DEFAULT_DISPLAY_WIDTH = 1170
_DEFAULT_DISPLAY_HEIGHT = 2532

# Computer Use beta identifier required by the Anthropic API
_COMPUTER_USE_BETA = "computer-use-2025-01-24"

# Cost per million tokens (USD) for claude-sonnet-4-20250514 — used for
# cost_callback computation when the model is not in the global pricing table.
_FALLBACK_INPUT_COST_PER_M = 3.00
_FALLBACK_OUTPUT_COST_PER_M = 15.00


def _compute_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    """Estimate USD cost for a single API call."""
    try:
        from specterqa.engine.models import PRICING
        pricing = PRICING.get(model)
        if pricing:
            return (input_tokens / 1_000_000) * pricing["input"] + (output_tokens / 1_000_000) * pricing["output"]
    except ImportError:
        pass
    return (input_tokens / 1_000_000) * _FALLBACK_INPUT_COST_PER_M + (output_tokens / 1_000_000) * _FALLBACK_OUTPUT_COST_PER_M


class ComputerUseDecider:
    """AI brain that uses Claude Computer Use to decide iOS Simulator actions.

    Receives a screenshot (base64-encoded PNG) and a natural-language goal,
    sends them to the Claude Computer Use beta API, and maps the response to
    a ``Decision`` object understood by the SpecterQA AI loop.

    Usage::

        decider = ComputerUseDecider(api_key="sk-ant-...", cost_callback=my_fn)
        decision = decider.decide(
            goal="Tap the Sign In button",
            screenshot_base64=base64_png,
        )
    """

    def __init__(
        self,
        api_key: str | None = None,
        model: str = "claude-sonnet-4-20250514",
        cost_callback: Callable[[str, float], None] | None = None,
        display_width: int = _DEFAULT_DISPLAY_WIDTH,
        display_height: int = _DEFAULT_DISPLAY_HEIGHT,
    ) -> None:
        """
        Args:
            api_key: Anthropic API key.  Defaults to ``ANTHROPIC_API_KEY``
                environment variable if not provided.
            model: Claude model ID to use for Computer Use.
            cost_callback: Optional ``(model_name, cost_usd) -> None`` called
                after each successful API call for spend tracking.
            display_width: Screen width in pixels (sent to Claude in tool config).
            display_height: Screen height in pixels (sent to Claude in tool config).
        """
        import anthropic

        self._model = model
        self._cost_callback = cost_callback
        self._display_width = display_width
        self._display_height = display_height

        # Build the Anthropic client — api_key=None falls back to env var.
        self._client = anthropic.Anthropic(api_key=api_key)

    # -- Public API ----------------------------------------------------------

    def decide(
        self,
        goal: str,
        screenshot_base64: str,
        ui_context: str = "",
        force_api: bool = False,
        stuck_context: str | None = None,
        **kwargs: Any,
    ) -> Decision:
        """Ask Claude what to do next given a screenshot and a goal.

        Args:
            goal: Natural-language description of the current step objective.
            screenshot_base64: Base64-encoded PNG screenshot of the simulator.
            ui_context: Optional supplementary context (e.g. AX tree snippet).
            force_api: Ignored — always calls the API (kept for protocol compat).
            stuck_context: If provided, appended to the prompt to hint that the
                agent should try a different approach.

        Returns:
            A ``Decision`` derived from Claude's response.  Never raises on
            API errors — returns a failure ``Decision`` instead.
        """
        messages = self._build_messages(goal, screenshot_base64, ui_context, stuck_context)
        tools = self._build_tools()

        response = self._call_api_with_retry(messages, tools)
        if response is None:
            return Decision(
                action="stuck",
                target="",
                value="",
                reasoning="API call failed after retry",
                goal_achieved=False,
            )

        self._fire_cost_callback(response)
        return self._parse_response(response)

    # -- Message Construction ------------------------------------------------

    def _build_messages(
        self,
        goal: str,
        screenshot_base64: str,
        ui_context: str,
        stuck_context: str | None,
    ) -> list[dict[str, Any]]:
        """Build the messages list for the Computer Use API call."""
        prompt_parts: list[str] = [f"Goal: {goal}"]
        if ui_context:
            prompt_parts.append(f"UI context:\n{ui_context}")
        if stuck_context:
            prompt_parts.append(f"IMPORTANT: {stuck_context}")
        prompt_parts.append(
            "Look at the screenshot and determine the best action to progress "
            "toward the goal.  Use the computer tool to interact with the UI.  "
            "If the goal is already achieved, respond with text only (no tool use)."
        )

        text_block: dict[str, Any] = {"type": "text", "text": "\n\n".join(prompt_parts)}
        image_block: dict[str, Any] = {
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": "image/png",
                "data": screenshot_base64,
            },
        }

        return [{"role": "user", "content": [image_block, text_block]}]

    def _build_tools(self) -> list[dict[str, Any]]:
        """Build the tools list including the computer tool with display config."""
        return [
            {
                "type": "computer_20250124",
                "name": "computer",
                "display_width_px": self._display_width,
                "display_height_px": self._display_height,
                "display_number": 1,
            }
        ]

    # -- API Call ------------------------------------------------------------

    def _call_api_with_retry(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> Any | None:
        """Call the beta.messages.create endpoint, retrying once on transient errors.

        Returns the response object, or None if all attempts fail.
        """
        import anthropic

        for attempt in range(2):
            try:
                response = self._client.beta.messages.create(
                    model=self._model,
                    max_tokens=1024,
                    messages=messages,
                    tools=tools,
                    betas=[_COMPUTER_USE_BETA],
                )
                return response
            except anthropic.APIConnectionError as exc:
                if attempt == 0:
                    logger.warning("ComputerUseDecider: transient connection error, retrying: %s", exc)
                    continue
                logger.error("ComputerUseDecider: connection error after retry: %s", exc)
                return None
            except anthropic.APIError as exc:
                logger.error("ComputerUseDecider: API error: %s", exc)
                return None
            except Exception as exc:
                logger.error("ComputerUseDecider: unexpected error: %s", exc, exc_info=True)
                return None

        return None

    # -- Response Parsing ----------------------------------------------------

    def _parse_response(self, response: Any) -> Decision:
        """Map a Claude Computer Use response to a Decision.

        Precedence:
        1. First ``tool_use`` block → action Decision
        2. Text-only response (no tool_use) → goal_achieved=True
        """
        tool_use_blocks = [b for b in response.content if b.type == "tool_use"]

        if not tool_use_blocks:
            # Claude returned only text — interpret as goal achieved
            text = " ".join(b.text for b in response.content if b.type == "text")
            return Decision(
                action="done",
                target="",
                value="",
                reasoning=text,
                goal_achieved=True,
                observation=text,
            )

        # Use the first tool_use block
        block = tool_use_blocks[0]
        tool_input: dict[str, Any] = block.input if isinstance(block.input, dict) else {}
        action_name: str = tool_input.get("action", "")

        return self._map_tool_action(action_name, tool_input)

    def _map_tool_action(self, action_name: str, tool_input: dict[str, Any]) -> Decision:
        """Convert a Computer Use tool action to a Decision."""
        if action_name in ("left_click", "right_click", "double_click", "middle_click", "click"):
            coordinate = tool_input.get("coordinate", [0, 0])
            x, y = int(coordinate[0]), int(coordinate[1])
            return Decision(
                action="click",
                target=f"{x},{y}",
                value="",
                reasoning=f"Clicking at ({x}, {y})",
                goal_achieved=False,
            )

        if action_name == "type":
            text = tool_input.get("text", "")
            return Decision(
                action="fill",
                target="",
                value=text,
                reasoning=f"Typing: {text!r}",
                goal_achieved=False,
            )

        if action_name == "key":
            key = tool_input.get("text", "")
            return Decision(
                action="keyboard",
                target="",
                value=key,
                reasoning=f"Pressing key: {key}",
                goal_achieved=False,
            )

        if action_name == "scroll":
            coordinate = tool_input.get("coordinate", [0, 0])
            x, y = int(coordinate[0]), int(coordinate[1])
            direction = tool_input.get("direction", "down")
            return Decision(
                action="scroll",
                target=f"{x},{y}",
                value=direction,
                reasoning=f"Scrolling {direction} at ({x}, {y})",
                goal_achieved=False,
            )

        if action_name == "screenshot":
            # Claude wants a screenshot — return a wait and let the loop re-screenshot
            return Decision(
                action="wait",
                target="",
                value="0.5",
                reasoning="Claude requested screenshot",
                goal_achieved=False,
            )

        if action_name in ("mouse_move", "left_click_drag"):
            coordinate = tool_input.get("coordinate", tool_input.get("start_coordinate", [0, 0]))
            x, y = int(coordinate[0]), int(coordinate[1])
            return Decision(
                action="click",
                target=f"{x},{y}",
                value="",
                reasoning=f"Mouse move/drag to ({x}, {y})",
                goal_achieved=False,
            )

        # Unknown action — return stuck
        logger.warning("ComputerUseDecider: unknown tool action '%s'", action_name)
        return Decision(
            action="stuck",
            target="",
            value="",
            reasoning=f"Unknown Computer Use action: {action_name}",
            goal_achieved=False,
        )

    # -- Cost Tracking -------------------------------------------------------

    def _fire_cost_callback(self, response: Any) -> None:
        """Invoke cost_callback(model, cost_usd) after a successful API call."""
        if self._cost_callback is None:
            return
        try:
            usage = response.usage
            cost = _compute_cost(
                self._model,
                usage.input_tokens,
                usage.output_tokens,
            )
            self._cost_callback(self._model, cost)
        except Exception as exc:
            logger.warning("ComputerUseDecider: cost_callback failed: %s", exc)
