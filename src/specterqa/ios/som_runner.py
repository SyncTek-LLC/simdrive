"""SoM Test Runner — Set-of-Mark powered iOS test execution.

Architecture:
  1. Element tree provides exact element positions
  2. SoM annotator draws numbered labels on screenshot
  3. Claude picks an element NUMBER (not coordinates)
  4. Backend taps the exact element center (pixel-perfect)

This achieves near-100% tap accuracy by separating:
  - Semantic understanding (Claude) — WHAT to tap
  - Precise positioning (element tree) — WHERE it is

Backend: XCTest runner — our Swift runner deployed on a cloned simulator.
No cursor movement, no window focus.

Research: SoM prompting improves UI agent accuracy from ~50% to ~90%+
by eliminating coordinate prediction entirely.

[internal-tracker] — SpecterQA SoM test runner.
[internal-tracker] — XCTest runner integration, non-blocking mode.
[internal-tracker] — Remove WDA fallback from SoM pipeline.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("specterqa.ios.som_runner")

# Number of recent actions to include as context for Claude.
_HISTORY_WINDOW = 3

# Pause after each action to let the UI settle.
_ACTION_SETTLE_S = 0.5

# Guard 3: maximum consecutive scroll actions before giving up.
MAX_CONSECUTIVE_SCROLLS = 5

# System prompt for the Claude element-selection model.
_SYSTEM_PROMPT = """\
You are an iOS UI testing agent. You see an annotated screenshot with numbered
elements overlaid in red. Each element has a number in a red badge.

Respond in this EXACT format (one line each, no extra text):
ACTION: tap|scroll|type|wait|done|back
ELEMENT: <number>   (only for tap — the red badge number to tap)
DIRECTION: up|down|left|right   (only for scroll)
TEXT: <text>   (only for type)
REASONING: <brief explanation of why this action achieves the goal>

Rules:
- Use "done" when the goal has been achieved.
- Use "back" to perform the iOS back-swipe gesture.
- Use "scroll" when the target element is not visible.
- ELEMENT must be a valid number visible on the screenshot.
- If uncertain, pick the most likely element and explain in REASONING.
"""


class SoMRunnerError(Exception):
    """Raised when the SoM runner encounters an unrecoverable error."""


class SoMRunner:
    """Execute test journeys using the SoM pipeline.

    Args:
        api_key: Anthropic API key.  Falls back to ANTHROPIC_API_KEY env var.
        model: Claude model for element selection.
        verbose: Print debug info to stdout.
        evidence_dir: Path to save annotated screenshots and run results.
        headless: When True (default), boot the cloned simulator without opening
            Simulator.app (non-blocking; user can keep coding).
        runner_url: Base URL of the XCTest runner (default http://localhost:8222).
        app_path: Path to .app bundle to install on the cloned simulator.
            Passed through to TestSession so the clone has the app.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: str = "claude-sonnet-4-20250514",
        verbose: bool = False,
        evidence_dir: Optional[str] = None,
        headless: bool = False,
        runner_url: str = "http://localhost:8222",
        app_path: Optional[str] = None,
    ) -> None:
        self.runner_url = runner_url
        self.api_key = api_key or os.environ.get("ANTHROPIC_API_KEY", "")
        self.model = model
        self.verbose = verbose
        self.evidence_dir = Path(evidence_dir) if evidence_dir else None
        self.headless = headless
        self.app_path = app_path

        self._driver: Any = None
        self._annotator: Any = None
        self._client: Any = None  # anthropic.Anthropic instance
        self._session: Any = None  # TestSession

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self, bundle_id: str) -> None:
        """Initialise the driver, SoM annotator, and Anthropic client.

        Args:
            bundle_id: iOS bundle identifier of the app under test.

        Raises:
            RuntimeError: When the XCTest runner start-up fails.
            SoMRunnerError: When the Anthropic API key is missing.
        """
        from specterqa.ios.som_annotator import SoMAnnotator

        if not self.api_key:
            raise SoMRunnerError("No Anthropic API key. Set ANTHROPIC_API_KEY or pass api_key= to SoMRunner.")

        self._start_xctest(bundle_id, SoMAnnotator)

        try:
            import anthropic  # type: ignore[import-untyped]

            self._client = anthropic.Anthropic(api_key=self.api_key)
        except ImportError as exc:
            raise SoMRunnerError(
                f"anthropic package not installed. Run: pip install 'specterqa-ios[orchestration]'\n{exc}"
            ) from exc

        if self.verbose:
            logger.debug("[som] started  bundle_id=%s  mode=xctest-runner", bundle_id)

    def _start_xctest(self, bundle_id: str, SoMAnnotator: type) -> None:
        """Start the XCTest-runner-backed pipeline (non-blocking, headless).

        Spawns a TestSession, waits for the runner to be healthy, then wires
        the driver and annotator to talk to it on localhost.

        Args:
            bundle_id: App bundle identifier (for logging).
            SoMAnnotator: The SoMAnnotator class (passed in to avoid circular import).
        """
        from specterqa.ios.session_manager import TestSession
        from specterqa.ios.backends.xctest_client import XCTestBackend

        self._session = TestSession(bundle_id=bundle_id, app_path=self.app_path)
        self._session.start()

        runner_url = self._session.runner_url
        port = self._session._port

        # Use XCTestBackend as the driver (tap/swipe/screenshot).
        self._driver = XCTestBackend(port=port)

        # Wire the annotator to our runner's /source endpoint.
        self._annotator = SoMAnnotator(runner_url=runner_url)

        if self.verbose:
            logger.debug("[som] xctest runner at %s  clone=%s", runner_url, self._session.clone_udid)

    def stop(self) -> None:
        """Clean up driver session and (if active) the TestSession clone."""
        self._driver = None
        self._annotator = None
        self._client = None
        if self._session is not None:
            try:
                self._session.stop()
            except Exception as exc:
                logger.warning("TestSession.stop() error (non-fatal): %s", exc)
            self._session = None
        if self.verbose:
            logger.debug("[som] stopped")

    # ------------------------------------------------------------------
    # Core step execution
    # ------------------------------------------------------------------

    def run_step(
        self,
        goal: str,
        checkpoint: Optional[str] = None,
        max_iterations: int = 15,
    ) -> dict[str, Any]:
        """Execute a single test step using the SoM pipeline.

        For each iteration:
        1. Take screenshot via simctl.
        2. Get element tree from WDA.
        3. Annotate screenshot with numbered elements.
        4. Ask Claude which element to interact with and what action to take.
        5. Execute the action (tap / scroll / type / wait / back / done).
        6. If done: optionally verify the checkpoint; return result.

        Args:
            goal: Natural-language description of what to achieve.
            checkpoint: Optional verification prompt sent to Claude after
                ``done`` to confirm the goal was reached.
            max_iterations: Maximum action iterations before failing.

        Returns:
            dict with keys: passed, duration, actions, error, screenshots.
        """
        if self._driver is None or self._annotator is None:
            raise SoMRunnerError("SoMRunner not started. Call start(bundle_id) first.")

        start = time.monotonic()
        actions: list[dict[str, Any]] = []
        screenshots: list[str] = []
        history: list[str] = []
        same_action_streak: dict[str, int] = {}
        error: Optional[str] = None
        passed = False
        _scroll_count = 0  # Guard 3: consecutive scroll counter

        for i in range(max_iterations):
            # 1. Screenshot
            try:
                result = self._driver.screenshot()
                if isinstance(result, dict):
                    b64, img_w, img_h = result["base64"], result.get("width", 0), result.get("height", 0)
                else:
                    b64, img_w, img_h = result
            except Exception as exc:
                error = f"Screenshot failed: {exc}"
                logger.error(error)
                break

            # 2 & 3. Fetch element tree and annotate
            try:
                elements, annotated_b64 = self._annotator.annotate(b64, img_w, img_h)
            except Exception as exc:
                logger.warning("SoM annotate failed (using plain screenshot): %s", exc)
                elements = []
                annotated_b64 = b64

            elements_text = self._annotator.elements_text(elements) if elements else "(no elements detected)"

            # Save annotated screenshot as evidence
            if self.evidence_dir:
                self._save_screenshot(annotated_b64, f"step_{i:03d}.png")
            screenshots.append(annotated_b64)

            if self.verbose:
                logger.debug("[som] iter=%d  elements=%d  goal=%s", i, len(elements), goal[:60])

            # 4. Ask Claude
            try:
                decision = self._ask_claude(
                    goal=goal,
                    elements=elements,
                    annotated_b64=annotated_b64,
                    elements_text=elements_text,
                    history=history[-_HISTORY_WINDOW:],
                )
            except Exception as exc:
                error = f"Claude request failed: {exc}"
                logger.error(error)
                break

            action_type = decision.get("action", "wait")
            reasoning = decision.get("reasoning", "")

            if self.verbose:
                logger.debug(
                    "[som] decision: action=%s  element=%s  reasoning=%s",
                    action_type,
                    decision.get("element"),
                    reasoning[:80],
                )

            # 6. Handle done / back / wait before execute
            if action_type == "done":
                if checkpoint:
                    passed = self._verify_checkpoint(checkpoint)
                    if not passed:
                        error = f"Checkpoint not met: {checkpoint}"
                else:
                    passed = True
                break

            # Guard 1: Pre-scroll visibility check — skip redundant scrolls.
            if action_type == "scroll":
                pre_scroll_tree: Optional[str] = None
                try:
                    pre_scroll_tree = self._annotator.get_element_tree()
                    if self._is_element_visible(goal, pre_scroll_tree):
                        logger.warning("Element already visible, skipping scroll (goal=%r)", goal[:60])
                        _scroll_count = 0
                        time.sleep(_ACTION_SETTLE_S)
                        continue
                except Exception as exc:
                    logger.debug("Guard 1 pre-scroll check failed (non-fatal): %s", exc)
                    pre_scroll_tree = None

            # 5. Execute
            try:
                result = self._execute_action(decision, elements)
                actions.append({"iter": i, "decision": decision, "result": result})
                history.append(self._format_history_entry(decision, result))
            except Exception as exc:
                logger.warning("Action execution failed (retrying next iter): %s", exc)
                actions.append({"iter": i, "decision": decision, "error": str(exc)})

            # Guard 2: Post-scroll state-change detection — stop if boundary reached.
            if action_type == "scroll" and pre_scroll_tree is not None:
                try:
                    post_scroll_tree = self._annotator.get_element_tree()
                    if not self._annotator._screen_changed(pre_scroll_tree, post_scroll_tree):
                        logger.warning("Screen unchanged after scroll, stopping (guard 2)")
                        error = "Screen unchanged after scroll — scroll boundary reached"
                        break
                except Exception as exc:
                    logger.debug("Guard 2 post-scroll check failed (non-fatal): %s", exc)

            # Guard 3: Max consecutive scroll cap.
            if action_type == "scroll":
                _scroll_count += 1
                if _scroll_count >= MAX_CONSECUTIVE_SCROLLS:
                    logger.warning(
                        "Max scroll cap (%d) reached, marking element as already visible",
                        MAX_CONSECUTIVE_SCROLLS,
                    )
                    error = f"Max consecutive scrolls ({MAX_CONSECUTIVE_SCROLLS}) reached"
                    break
            else:
                _scroll_count = 0  # reset on any non-scroll action

            # Stuck detection: check if screen changed after action.
            # For scroll/swipe, the screen SHOULD change — if not, we're stuck.
            # For taps, same element tapped 3 times without screen change = stuck.
            post_result = self._driver.screenshot()
            if isinstance(post_result, dict):
                post_b64 = post_result["base64"]
            else:
                post_b64, _, _ = post_result
            screen_changed = post_b64[:500] != b64[:500]

            if screen_changed:
                same_action_streak.clear()  # Reset — progress was made
            else:
                action_key = f"{action_type}:{decision.get('element')}:{decision.get('direction')}"
                same_action_streak[action_key] = same_action_streak.get(action_key, 0) + 1
                if same_action_streak[action_key] >= 3:
                    error = f"Stuck: action '{action_key}' repeated 3 times with no screen change"
                    logger.warning(error)
                    break

            time.sleep(_ACTION_SETTLE_S)
        else:
            error = error or f"Max iterations ({max_iterations}) reached without completing goal"

        duration = round(time.monotonic() - start, 3)
        return {
            "passed": passed,
            "duration": duration,
            "actions": actions,
            "error": error,
            "screenshots": len(screenshots),
        }

    # ------------------------------------------------------------------
    # Journey execution
    # ------------------------------------------------------------------

    def run_journey(self, journey_config: dict[str, Any]) -> dict[str, Any]:
        """Execute all steps in a journey.

        Args:
            journey_config: Parsed YAML journey dict.  Supports both top-level
                ``steps`` key and the wrapped ``scenario.steps`` schema.

        Returns:
            dict with keys: journey_name, steps, passed_count, total_count, duration.
        """
        # Support both flat and wrapped YAML schemas
        scenario = journey_config.get("scenario", journey_config)
        journey_name: str = scenario.get("name", scenario.get("id", "unnamed-journey"))
        steps: list[dict[str, Any]] = scenario.get("steps", [])

        step_results: list[dict[str, Any]] = []
        journey_start = time.monotonic()

        for i, step in enumerate(steps, 1):
            step_id = step.get("id", f"step-{i}")
            goal = step.get("goal", step.get("description", step_id))
            checkpoint = step.get("checkpoint")
            max_iter = step.get("max_iterations", 15)

            if self.verbose:
                logger.debug("[som] --- step %d/%d: %s ---", i, len(steps), step_id)
                logger.debug("[som] goal: %s", goal)

            result = self.run_step(goal=goal, checkpoint=checkpoint, max_iterations=max_iter)
            step_results.append(
                {
                    "id": step_id,
                    "goal": goal,
                    "passed": result["passed"],
                    "duration": result["duration"],
                    "error": result.get("error"),
                    "action_count": len(result.get("actions", [])),
                }
            )

            if self.verbose:
                status = "PASS" if result["passed"] else "FAIL"
                logger.debug("[som] %s  (%.1fs)  error=%s", status, result["duration"], result.get("error"))

        total_duration = round(time.monotonic() - journey_start, 3)
        passed_count = sum(1 for s in step_results if s["passed"])

        run_result = {
            "journey_name": journey_name,
            "steps": step_results,
            "passed_count": passed_count,
            "total_count": len(steps),
            "duration": total_duration,
            "passed": passed_count == len(steps),
        }

        # Persist to evidence directory
        if self.evidence_dir:
            result_path = self.evidence_dir / "run-result.json"
            try:
                result_path.write_text(json.dumps(run_result, indent=2), encoding="utf-8")
            except Exception as exc:
                logger.warning("Failed to write run-result.json: %s", exc)

        return run_result

    # ------------------------------------------------------------------
    # Claude integration
    # ------------------------------------------------------------------

    def _ask_claude(
        self,
        goal: str,
        elements: list[Any],
        annotated_b64: str,
        elements_text: str,
        history: Optional[list[str]] = None,
    ) -> dict[str, Any]:
        """Ask Claude what to do next given the annotated screenshot.

        Sends the annotated image plus a text element list to Claude.  The
        model responds in a structured plain-text format that we parse into
        a decision dict.

        Args:
            goal: The current step goal.
            elements: Parsed UIElement list (used for count/validation).
            annotated_b64: Base64-encoded annotated PNG.
            elements_text: Human-readable element list for the text block.
            history: List of recent action strings for context.

        Returns:
            dict with keys: action, element (int|None), direction, text, reasoning.
        """
        history_block = ""
        if history:
            history_block = "\nPrevious actions:\n" + "\n".join(f"- {h}" for h in history) + "\n"

        user_text = (
            f"Goal: {goal}\n"
            f"{history_block}"
            f"\nAvailable elements:\n{elements_text}\n"
            "\nWhat action should be taken next to achieve the goal?"
        )

        response = self._client.messages.create(
            model=self.model,
            max_tokens=256,
            system=_SYSTEM_PROMPT,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": "image/png",
                                "data": annotated_b64,
                            },
                        },
                        {
                            "type": "text",
                            "text": user_text,
                        },
                    ],
                }
            ],
        )

        raw = response.content[0].text.strip()
        if self.verbose:
            logger.debug("[som] claude raw:\n%s", raw)

        return self._parse_claude_response(raw)

    def _parse_claude_response(self, raw: str) -> dict[str, Any]:
        """Parse Claude's structured response into a decision dict.

        Expected format (one key per line):
            ACTION: tap
            ELEMENT: 3
            REASONING: General settings cell is item 3

        Args:
            raw: Raw text from Claude.

        Returns:
            dict with keys: action, element, direction, text, reasoning.
        """
        result: dict[str, Any] = {
            "action": "wait",
            "element": None,
            "direction": None,
            "text": None,
            "reasoning": "",
        }

        for line in raw.splitlines():
            line = line.strip()
            if ":" not in line:
                continue
            key, _, value = line.partition(":")
            key = key.strip().upper()
            value = value.strip()

            if key == "ACTION":
                action = value.lower()
                if action in ("tap", "scroll", "type", "wait", "done", "back"):
                    result["action"] = action
            elif key == "ELEMENT":
                try:
                    result["element"] = int(re.sub(r"[^\d]", "", value))
                except (ValueError, TypeError):
                    logger.warning("Could not parse ELEMENT value: %r", value)
            elif key == "DIRECTION":
                direction = value.lower()
                if direction in ("up", "down", "left", "right"):
                    result["direction"] = direction
            elif key == "TEXT":
                result["text"] = value
            elif key == "REASONING":
                result["reasoning"] = value

        return result

    # ------------------------------------------------------------------
    # Scroll-stuck prevention guards
    # ------------------------------------------------------------------

    def _is_element_visible(self, target_description: str, element_tree_xml: str) -> bool:
        """Check if target element is already visible in the current element tree.

        Guard 1: Pre-scroll visibility check.  Call this before executing a
        scroll action — if the target is already on-screen, the scroll is
        unnecessary and can be skipped.

        Args:
            target_description: The element to find (e.g. "Settings", "Account").
            element_tree_xml: Raw XML string from GET /source.

        Returns:
            True if an element whose label contains ``target_description``
            (case-insensitive) is found with a non-zero width and height.
        """
        needle = target_description.strip().lower()
        if not needle:
            return False

        try:
            import xml.etree.ElementTree as ET  # already imported in annotator

            root = ET.fromstring(element_tree_xml)
        except Exception as exc:
            logger.debug("_is_element_visible: XML parse error — %s", exc)
            return False

        for node in root.iter():
            label = (node.get("label") or node.get("name") or "").lower()
            if needle in label:
                try:
                    w = float(node.get("width", 0))
                    h = float(node.get("height", 0))
                except (TypeError, ValueError):
                    w = h = 0.0
                if w > 0 and h > 0:
                    return True

        return False

    # ------------------------------------------------------------------
    # Action execution
    # ------------------------------------------------------------------

    def _execute_action(self, action_dict: dict[str, Any], elements: list[Any]) -> str:
        """Execute the action Claude selected.

        Args:
            action_dict: Parsed decision from _ask_claude / _parse_claude_response.
            elements: UIElement list for element-number lookup.

        Returns:
            Short string describing what was done.

        Raises:
            SoMRunnerError: When element number is invalid.
        """
        action = action_dict.get("action", "wait")

        if action == "tap":
            element_num = action_dict.get("element")
            if element_num is None:
                raise SoMRunnerError("Claude chose 'tap' but provided no ELEMENT number.")
            # Find the element by its 1-based index
            target = next((e for e in elements if e.index == element_num), None)
            if target is None:
                raise SoMRunnerError(f"Element {element_num} not in current tree (valid: 1–{len(elements)}).")
            # Tap at the exact center in device-point space.  WDA's tap() accepts
            # screenshot-pixel coords and converts internally, but SoM elements
            # are in device points.  We need to provide pixel coords.
            # Convert device points → screenshot pixels using driver dimensions.
            px = target.center_x * (self._driver._display_width / self._driver._device_width)
            py = target.center_y * (self._driver._display_height / self._driver._device_height)
            self._driver.tap(px, py)
            return f"Tapped [{element_num}] {target.label!r} at ({target.center_x:.0f}, {target.center_y:.0f})"

        elif action == "scroll":
            direction = action_dict.get("direction", "down")
            cx = self._driver._display_width / 2
            cy = self._driver._display_height / 2
            offset = 200  # pixels in screenshot space
            if direction == "down":
                self._driver.swipe(cx, cy, cx, cy - offset)
            elif direction == "up":
                self._driver.swipe(cx, cy, cx, cy + offset)
            elif direction == "left":
                self._driver.swipe(cx, cy, cx - offset, cy)
            elif direction == "right":
                self._driver.swipe(cx, cy, cx + offset, cy)
            return f"Scrolled {direction}"

        elif action == "type":
            text = action_dict.get("text") or ""
            if not text:
                logger.warning("Claude chose 'type' but provided no TEXT.")
                return "Type: (no text)"
            self._driver.type_text(text)
            return f"Typed: {text[:40]}"

        elif action == "back":
            self._driver.swipe_back()
            return "Back gesture"

        elif action == "wait":
            time.sleep(1)
            return "Waited 1s"

        else:
            logger.warning("Unknown action %r — skipping.", action)
            return f"Skipped unknown action: {action}"

    # ------------------------------------------------------------------
    # Checkpoint verification
    # ------------------------------------------------------------------

    def _verify_checkpoint(self, checkpoint: str) -> bool:
        """Take a screenshot and ask Claude if the checkpoint was reached.

        Args:
            checkpoint: Natural-language description of the expected state.

        Returns:
            True if Claude confirms the checkpoint is met.
        """
        try:
            result = self._driver.screenshot()
            if isinstance(result, dict):
                b64, img_w, img_h = result["base64"], result.get("width", 0), result.get("height", 0)
            else:
                b64, img_w, img_h = result
        except (Exception, TimeoutError) as exc:
            logger.warning("Checkpoint screenshot failed: %s", exc)
            return False

        verify_prompt = (
            f"Checkpoint to verify: {checkpoint}\n\n"
            "Look at the screenshot and answer with exactly one word: YES or NO.\n"
            "YES = the checkpoint condition is currently met on screen.\n"
            "NO = it is not."
        )

        try:
            response = self._client.messages.create(
                model=self.model,
                max_tokens=10,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "image",
                                "source": {
                                    "type": "base64",
                                    "media_type": "image/png",
                                    "data": b64,
                                },
                            },
                            {"type": "text", "text": verify_prompt},
                        ],
                    }
                ],
            )
            answer = response.content[0].text.strip().upper()
            if self.verbose:
                logger.debug("[som] checkpoint %r: %s", checkpoint[:50], answer)
            return answer.startswith("Y")
        except Exception as exc:
            logger.warning("Checkpoint verification failed: %s", exc)
            return False

    # ------------------------------------------------------------------
    # Evidence helpers
    # ------------------------------------------------------------------

    def _save_screenshot(self, b64: str, filename: str) -> None:
        """Write a base64 PNG to the evidence directory."""
        if not self.evidence_dir:
            return
        try:
            import base64 as _b64

            path = self.evidence_dir / filename
            path.write_bytes(_b64.b64decode(b64))
        except Exception as exc:
            logger.warning("Failed to save screenshot %s: %s", filename, exc)

    # ------------------------------------------------------------------
    # History formatting
    # ------------------------------------------------------------------

    def _format_history_entry(self, decision: dict[str, Any], result: str) -> str:
        """Format a completed action as a short history string."""
        action = decision.get("action", "?")
        if action == "tap":
            reasoning = decision.get("reasoning", "")
            return f"Tapped element {decision.get('element')} → {reasoning[:50]}"
        elif action == "scroll":
            return f"Scrolled {decision.get('direction', '?')}"
        elif action == "type":
            return f"Typed {decision.get('text', '')!r}"
        elif action == "back":
            return "Back gesture"
        return result[:60]
