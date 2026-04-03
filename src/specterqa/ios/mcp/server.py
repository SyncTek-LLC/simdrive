"""SpecterQA iOS MCP Server — Native primitives for Claude Code.

Claude Code IS the reasoning engine. This server exposes direct
simulator control primitives — no Claude API calls, no SoM pipeline,
no orchestration loops. Claude sees annotated screenshots and decides
what to do.

Usage:
    specterqa-ios-mcp            # stdio transport (console_scripts entry point)
    python -m specterqa.ios.mcp  # alternative invocation
    specterqa ios serve          # via CLI serve command

Tools:
    ios_start_session   Start XCTest runner on a cloned simulator
    ios_stop_session    Stop runner and clean up clone
    ios_screenshot      Annotated screenshot with numbered elements
    ios_tap             Tap element by index number
    ios_swipe           Swipe in a direction
    ios_swipe_back      iOS back navigation gesture
    ios_type            Type text into focused field
    ios_elements        Get element list without screenshot

INIT-2026-500 — SpecterQA iOS Headless Driver.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import subprocess
from pathlib import Path
from typing import Any

logger = logging.getLogger("specterqa.ios.mcp")

# ---------------------------------------------------------------------------
# Global session state — one active session at a time
# ---------------------------------------------------------------------------

_session = None          # TestSession instance
_backend = None          # XCTestBackend instance
_annotator = None        # SoMAnnotator instance
_last_elements: list = []  # Element cache from last ios_screenshot / ios_elements call


def _require_session() -> None:
    """Raise RuntimeError if no active session exists."""
    if _backend is None:
        raise RuntimeError("No active session. Call ios_start_session first.")


def _get_annotated_screenshot() -> tuple[str, list]:
    """Capture a screenshot, fetch the element tree, annotate, and return both.

    Returns:
        (annotated_b64, elements) — base-64 PNG string and UIElement list.
    """
    _require_session()

    result = _backend.screenshot()
    # The runner may return the image under 'base64', 'data', or 'image'.
    b64 = result.get("base64") or result.get("data") or result.get("image", "")
    img_w = result.get("width", 390)
    img_h = result.get("height", 844)

    elements, annotated_b64 = _annotator.annotate(b64, img_w, img_h)
    return annotated_b64, elements


# ---------------------------------------------------------------------------
# Utility helpers (kept from original server — not domain-specific)
# ---------------------------------------------------------------------------


def _list_simulator_devices() -> list[dict[str, Any]]:
    """Run ``xcrun simctl list devices --json`` and return a flat device list."""
    proc = subprocess.run(
        ["xcrun", "simctl", "list", "devices", "--json"],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        return []
    try:
        data = json.loads(proc.stdout)
    except (json.JSONDecodeError, ValueError):
        return []

    devices: list[dict[str, Any]] = []
    for runtime_id, device_list in data.get("devices", {}).items():
        runtime_label = (
            runtime_id
            .replace("com.apple.CoreSimulator.SimRuntime.", "")
            .replace("-", " ")
        )
        for dev in device_list:
            devices.append({**dev, "runtime": runtime_label})
    return devices


def _find_booted_udid() -> str | None:
    """Return the UDID of a currently booted simulator, or None."""
    for dev in _list_simulator_devices():
        if dev.get("state") == "Booted":
            return dev.get("udid")
    return None


def _json_serialize(obj: Any) -> str:
    """JSON serializer for non-standard types (Path, etc.)."""
    if isinstance(obj, Path):
        return str(obj)
    raise TypeError(f"Object of type {type(obj)} is not JSON serializable")


# ---------------------------------------------------------------------------
# Tool handler implementations
# ---------------------------------------------------------------------------


def handle_start_session(arguments: dict) -> dict:
    """Start the XCTest runner on a cloned simulator.

    Args:
        bundle_id: Bundle ID of the app under test (required).
        device_id:  Source simulator UDID or "booted" (default "booted").
        app_path:   Path to a .app bundle to install before starting (optional).

    Returns:
        {"status": "ok", "clone_udid": "...", "port": 8222, "runner_url": "..."}
        or {"error": "<message>"} on failure.
    """
    global _session, _backend, _annotator, _last_elements

    bundle_id = arguments.get("bundle_id")
    if not bundle_id:
        return {"error": "bundle_id is required"}

    device_id = arguments.get("device_id", "booted")
    app_path = arguments.get("app_path")

    from specterqa.ios.session_manager import TestSession
    from specterqa.ios.backends.xctest_client import XCTestBackend
    from specterqa.ios.som_annotator import SoMAnnotator

    try:
        _session = TestSession(
            source_udid=device_id,
            bundle_id=bundle_id,
            app_path=app_path,
        )
        _session.start()

        port = _session._port
        runner_url = _session.runner_url

        _backend = XCTestBackend(port=port)
        _annotator = SoMAnnotator(runner_url=runner_url)
        _last_elements = []

        return {
            "status": "ok",
            "clone_udid": _session._clone_udid,
            "port": port,
            "runner_url": runner_url,
        }
    except Exception as exc:
        # Clean up partial state on failure
        _session = None
        _backend = None
        _annotator = None
        _last_elements = []
        return {"error": str(exc)}


def handle_stop_session(arguments: dict) -> dict:
    """Stop the runner and delete the cloned simulator.

    Returns:
        {"status": "stopped"}
    """
    global _session, _backend, _annotator, _last_elements

    if _session is not None:
        try:
            _session.stop()
        except Exception as exc:
            logger.warning("Error stopping session: %s", exc)

    _session = None
    _backend = None
    _annotator = None
    _last_elements = []

    return {"status": "stopped"}


def handle_screenshot(arguments: dict) -> dict:
    """Capture an annotated screenshot with numbered element badges.

    This is the KEY tool — Claude sees the annotated image and picks
    element numbers to interact with via ios_tap.

    Returns:
        {
            "image": "<base64 PNG with numbered bounding-box annotations>",
            "elements": [
                {"index": 1, "label": "General", "type": "Cell",
                 "x": 16, "y": 278, "width": 358, "height": 52},
                ...
            ],
            "count": <int>
        }
        or {"error": "<message>"} on failure.
    """
    global _last_elements

    try:
        annotated_b64, elements = _get_annotated_screenshot()

        _last_elements = elements

        element_list = [
            {
                "index": e.index,
                "label": e.label,
                "type": e.element_type,
                "x": e.x,
                "y": e.y,
                "width": e.width,
                "height": e.height,
            }
            for e in elements
        ]

        return {
            "image": annotated_b64,
            "elements": element_list,
            "count": len(element_list),
        }
    except Exception as exc:
        return {"error": str(exc)}


def handle_tap(arguments: dict) -> dict:
    """Tap an element by its index number from the last screenshot.

    Args:
        element_index: Integer index shown in the annotated screenshot (required).

    Returns:
        {"status": "ok", "tapped": "<label>", "x": <cx>, "y": <cy>}
        or {"error": "<message>"} on failure.
    """
    global _last_elements

    try:
        _require_session()
    except RuntimeError as exc:
        return {"error": str(exc)}

    element_index = arguments.get("element_index")
    if element_index is None:
        return {"error": "element_index is required"}

    try:
        element_index = int(element_index)
    except (TypeError, ValueError):
        return {"error": f"element_index must be an integer, got: {element_index!r}"}

    # Locate the element in the last-captured list
    target = next((e for e in _last_elements if e.index == element_index), None)

    if target is None:
        valid_indices = [e.index for e in _last_elements]
        return {
            "error": (
                f"Element {element_index} not found. "
                f"Call ios_screenshot first to refresh elements. "
                f"Valid indices: {valid_indices}"
            )
        }

    cx = target.x + target.width / 2
    cy = target.y + target.height / 2

    try:
        _backend.tap(cx, cy)
    except Exception as exc:
        return {"error": f"Tap failed: {exc}"}

    return {
        "status": "ok",
        "tapped": target.label,
        "x": cx,
        "y": cy,
    }


def handle_swipe(arguments: dict) -> dict:
    """Swipe in a cardinal direction.

    Args:
        direction: "up", "down", "left", or "right" (default "down").

    Returns:
        {"status": "ok", "direction": "<direction>"}
        or {"error": "<message>"} on failure.
    """
    try:
        _require_session()
    except RuntimeError as exc:
        return {"error": str(exc)}

    direction = arguments.get("direction", "down").lower()
    valid_directions = {"up", "down", "left", "right"}
    if direction not in valid_directions:
        return {
            "error": f"Invalid direction {direction!r}. Must be one of: {sorted(valid_directions)}"
        }

    # Centre of a standard iPhone screen in logical points
    cx, cy = 195, 422
    offset = 200

    coords = {
        "down":  (cx, cy + offset, cx, cy - offset),
        "up":    (cx, cy - offset, cx, cy + offset),
        "left":  (cx + offset, cy, cx - offset, cy),
        "right": (cx - offset, cy, cx + offset, cy),
    }

    x1, y1, x2, y2 = coords[direction]

    try:
        _backend.swipe(x1, y1, x2, y2)
    except Exception as exc:
        return {"error": f"Swipe failed: {exc}"}

    return {"status": "ok", "direction": direction}


def handle_swipe_back(arguments: dict) -> dict:
    """Perform an iOS back-navigation gesture (swipe from left edge).

    Returns:
        {"status": "ok"}
        or {"error": "<message>"} on failure.
    """
    try:
        _require_session()
    except RuntimeError as exc:
        return {"error": str(exc)}

    try:
        _backend.swipe_back()
    except Exception as exc:
        return {"error": f"Swipe-back failed: {exc}"}

    return {"status": "ok"}


def handle_type(arguments: dict) -> dict:
    """Type text into the currently focused field.

    Args:
        text: String to type (required).

    Returns:
        {"status": "ok", "typed": "<text>"}
        or {"error": "<message>"} on failure.
    """
    try:
        _require_session()
    except RuntimeError as exc:
        return {"error": str(exc)}

    text = arguments.get("text", "")
    if not text:
        return {"error": "text is required and must be non-empty"}

    try:
        _backend.type_text(text)
    except Exception as exc:
        return {"error": f"Type failed: {exc}"}

    return {"status": "ok", "typed": text}


def handle_elements(arguments: dict) -> dict:
    """Get the current element list without capturing a screenshot (fast).

    Useful when Claude needs to refresh the element index without the
    overhead of image annotation.

    Returns:
        {
            "elements": [
                {"index": 1, "label": "...", "type": "...",
                 "x": .., "y": .., "width": .., "height": ..},
                ...
            ],
            "count": <int>
        }
        or {"error": "<message>"} on failure.
    """
    global _last_elements

    try:
        _require_session()
    except RuntimeError as exc:
        return {"error": str(exc)}

    try:
        tree_xml = _annotator.get_element_tree()
        elements = _annotator.parse_elements(tree_xml)

        _last_elements = elements

        element_list = [
            {
                "index": e.index,
                "label": e.label,
                "type": e.element_type,
                "x": e.x,
                "y": e.y,
                "width": e.width,
                "height": e.height,
            }
            for e in elements
        ]

        return {"elements": element_list, "count": len(element_list)}
    except Exception as exc:
        return {"error": str(exc)}


# ---------------------------------------------------------------------------
# MCP server factory
# ---------------------------------------------------------------------------


def create_server() -> Any:
    """Create and configure the SpecterQA iOS MCP server.

    Returns a FastMCP server instance with the eight primitive iOS tools.

    Raises:
        ImportError: if the ``mcp`` package is not installed.
    """
    try:
        from mcp.server.fastmcp import FastMCP
    except ImportError:
        raise ImportError(
            "The 'mcp' package is required for the SpecterQA iOS MCP server.\n\n"
            "Install it:\n"
            "  pip install 'specterqa-ios[mcp]'\n"
            "  # or: pip install mcp>=1.0.0"
        )

    mcp = FastMCP(
        "specterqa-ios",
        instructions=(
            "SpecterQA iOS exposes direct simulator control primitives. "
            "Claude Code is the reasoning engine — no AI orchestration happens here. "
            "Workflow: ios_start_session → ios_screenshot (see annotated screen + "
            "numbered elements) → ios_tap / ios_swipe / ios_type → repeat → "
            "ios_stop_session. "
            "Use ios_elements for a fast element refresh without a screenshot. "
            "Requires macOS with Xcode 15+ and a compiled XCTest runner."
        ),
    )

    # ── Tool: ios_start_session ────────────────────────────────────────────

    @mcp.tool(
        name="ios_start_session",
        description=(
            "Start the XCTest runner on a cloned iOS Simulator. "
            "Clones the source device, boots the clone headless, deploys the runner, "
            "and waits for health. Call this once before any other ios_* tools. "
            "bundle_id is required (e.g. 'com.example.MyApp'). "
            "device_id defaults to 'booted'. "
            "app_path is an optional path to a .app bundle to install."
        ),
    )
    async def ios_start_session(
        bundle_id: str,
        device_id: str = "booted",
        app_path: str | None = None,
    ) -> str:
        result = handle_start_session({
            "bundle_id": bundle_id,
            "device_id": device_id,
            "app_path": app_path,
        })
        return json.dumps(result)

    # ── Tool: ios_stop_session ─────────────────────────────────────────────

    @mcp.tool(
        name="ios_stop_session",
        description=(
            "Stop the XCTest runner and delete the cloned simulator. "
            "Always call this when testing is complete to free resources."
        ),
    )
    async def ios_stop_session() -> str:
        result = handle_stop_session({})
        return json.dumps(result)

    # ── Tool: ios_screenshot ───────────────────────────────────────────────

    @mcp.tool(
        name="ios_screenshot",
        description=(
            "Capture an annotated screenshot of the running iOS app. "
            "Returns a base64 PNG with numbered red bounding boxes overlaid on "
            "every interactive element, plus a structured element list. "
            "Use the element index numbers with ios_tap to interact. "
            "This is the primary perception tool — call it before tapping."
        ),
    )
    async def ios_screenshot() -> str:
        result = handle_screenshot({})
        return json.dumps(result, default=_json_serialize)

    # ── Tool: ios_tap ──────────────────────────────────────────────────────

    @mcp.tool(
        name="ios_tap",
        description=(
            "Tap an element by its index number shown in the annotated screenshot. "
            "Call ios_screenshot first to get the current element list, then pass "
            "the element_index of the element you want to tap. "
            "element_index is required (integer matching an index from ios_screenshot)."
        ),
    )
    async def ios_tap(element_index: int) -> str:
        result = handle_tap({"element_index": element_index})
        return json.dumps(result)

    # ── Tool: ios_swipe ────────────────────────────────────────────────────

    @mcp.tool(
        name="ios_swipe",
        description=(
            "Swipe in a cardinal direction on the iOS Simulator screen. "
            "direction must be 'up', 'down', 'left', or 'right'. "
            "Use 'down' to scroll down (content moves up), 'up' to scroll up. "
            "After swiping, call ios_screenshot to see the updated screen."
        ),
    )
    async def ios_swipe(direction: str = "down") -> str:
        result = handle_swipe({"direction": direction})
        return json.dumps(result)

    # ── Tool: ios_swipe_back ───────────────────────────────────────────────

    @mcp.tool(
        name="ios_swipe_back",
        description=(
            "Perform the iOS swipe-from-left-edge back navigation gesture. "
            "Equivalent to the system back swipe on navigation controllers."
        ),
    )
    async def ios_swipe_back() -> str:
        result = handle_swipe_back({})
        return json.dumps(result)

    # ── Tool: ios_type ─────────────────────────────────────────────────────

    @mcp.tool(
        name="ios_type",
        description=(
            "Type text into the currently focused text field on the iOS Simulator. "
            "Tap a text field first (ios_tap) to focus it, then call ios_type. "
            "text is required and must be non-empty."
        ),
    )
    async def ios_type(text: str) -> str:
        result = handle_type({"text": text})
        return json.dumps(result)

    # ── Tool: ios_elements ─────────────────────────────────────────────────

    @mcp.tool(
        name="ios_elements",
        description=(
            "Get the current interactive element list without capturing a screenshot. "
            "Faster than ios_screenshot when you only need element indices and labels. "
            "Also updates the element cache used by ios_tap."
        ),
    )
    async def ios_elements() -> str:
        result = handle_elements({})
        return json.dumps(result, default=_json_serialize)

    return mcp


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def serve() -> None:
    """Start the SpecterQA iOS MCP server on stdio transport.

    Entry points:
      - ``specterqa-ios-mcp`` console script
      - ``python -m specterqa.ios.mcp``
      - ``specterqa ios serve``
    """
    logging.basicConfig(
        level=logging.WARNING,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    server = create_server()
    server.run(transport="stdio")


if __name__ == "__main__":
    serve()
