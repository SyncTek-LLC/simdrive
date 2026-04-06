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
    ios_start_session    Start XCTest runner on the iOS Simulator
    ios_stop_session     Stop the XCTest runner and clean up
    ios_screenshot       Annotated screenshot with numbered elements
    ios_tap              Tap element by index number
    ios_long_press       Long-press element by index (context menus, drag init)
    ios_press_key        Press a keyboard key (return, escape, delete, tab, ...)
    ios_swipe            Swipe in a direction
    ios_swipe_back       iOS back navigation gesture
    ios_type             Type text into focused field
    ios_elements         Get element list without screenshot
    ios_set_appearance   Toggle dark/light mode on the simulator
    ios_simctl           Run arbitrary simctl subcommand on the simulator

INIT-2026-500 — SpecterQA iOS Headless Driver.
"""

from __future__ import annotations

import base64
import io
import json
import logging
import os
import subprocess
import threading
from pathlib import Path
from typing import Any

from PIL import Image

logger = logging.getLogger("specterqa.ios.mcp")

# ---------------------------------------------------------------------------
# Global session state — one active session at a time
# ---------------------------------------------------------------------------

_session = None          # TestSession instance
_backend = None          # XCTestBackend instance
_annotator = None        # SoMAnnotator instance
_last_elements: list = []  # Element cache from last ios_screenshot / ios_elements call
_session_lock = threading.Lock()  # Serialises start/stop to prevent race conditions
_recorder = None         # ReplayRecorder instance (None when recording is not active)


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


# BUG V5-5 FIX: resize screenshot before encoding to keep MCP payloads small.
_QUALITY_SCALES = {
    "full": 1.0,
    "standard": 0.5,
    "thumbnail": 0.25,
}


def _resize_screenshot(b64_png: str, scale: float = 0.5) -> str:
    """Resize a base64 PNG by *scale* to reduce MCP payload size.

    Args:
        b64_png: Base-64 encoded PNG string.
        scale:   Scale factor (0 < scale <= 1.0).  0.5 = half dimensions.

    Returns:
        Base-64 encoded resized PNG string.
    """
    if scale >= 1.0:
        return b64_png
    raw = base64.b64decode(b64_png)
    img = Image.open(io.BytesIO(raw))
    new_size = (max(1, int(img.width * scale)), max(1, int(img.height * scale)))
    img = img.resize(new_size, Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return base64.b64encode(buf.getvalue()).decode("ascii")


# ---------------------------------------------------------------------------
# Tool handler implementations
# ---------------------------------------------------------------------------


def handle_save_replay(arguments: dict) -> dict:
    """Save the recorded session as a replay YAML file.

    Args:
        name: Human-readable test name used as the filename stem (default "replay").
        path: Override the output path; defaults to .specterqa/replays/<name>.yaml.

    Returns:
        {"status": "ok", "path": "<absolute path>", "steps": <count>}
        or {"error": "<message>"} on failure.
    """
    global _recorder, _last_elements

    if _recorder is None:
        return {"error": "No active recording. Start a session first."}

    name = str(arguments.get("name", "replay")).strip() or "replay"
    path = str(arguments.get("path", "")).strip()
    if not path:
        path = f".specterqa/replays/{name}.yaml"

    try:
        # Snapshot the current element labels as a checkpoint on the last step
        if _last_elements and _recorder.session.steps:
            labels = [e.label for e in _last_elements[:10] if e.label]
            if labels:
                _recorder.add_checkpoint(labels)

        saved = _recorder.save(path, name=name)
        return {
            "status": "ok",
            "path": str(saved.resolve()),
            "steps": len(_recorder.session.steps),
        }
    except Exception as exc:
        return {"error": f"Failed to save replay: {exc}"}


def handle_start_session(arguments: dict) -> dict:
    """Start the XCTest runner on the booted simulator.

    Args:
        bundle_id:   Bundle ID of the app under test (required).
        device_id:   Source simulator UDID or "booted" (default "booted").
        app_path:    Path to a .app bundle to install before starting (optional).
        license_key: SpecterQA license key (optional — falls back to
                     ``SPECTERQA_IOS_LICENSE`` env var; omit for trial mode).

    Returns:
        {"status": "ok", "clone_udid": "...", "port": 8222, "runner_url": "..."}
        or {"error": "<message>"} on failure.
    """
    global _session, _backend, _annotator, _last_elements, _recorder

    with _session_lock:
        # License check — validates key or allows trial/founder bypass.
        # BUG V5-1 FIX: if the caller passes license_key="founder" as an argument,
        # inject it into the environment so LicenseValidator's founder bypass fires.
        from specterqa.ios.license.validator import LicenseValidator
        license_key = arguments.get("license_key", os.environ.get("SPECTERQA_LICENSE_KEY", ""))
        if str(license_key).strip().lower() == "founder":
            os.environ["SPECTERQA_IOS_LICENSE"] = "founder"
        validator = LicenseValidator(license_key=license_key)
        license_result = validator.validate()
        if not license_result.get("valid"):
            return {"error": "Invalid license. Set SPECTERQA_IOS_LICENSE=founder or provide a valid key."}

        bundle_id = arguments.get("bundle_id")
        if not bundle_id:
            return {"error": "bundle_id is required"}

        device_id = arguments.get("device_id", "booted")
        app_path = arguments.get("app_path")

        from specterqa.ios.session_manager import TestSession
        from specterqa.ios.backends.xctest_client import XCTestBackend
        from specterqa.ios.som_annotator import SoMAnnotator

        try:
            clone = arguments.get("clone", False)
            _session = TestSession(
                source_udid=device_id,
                bundle_id=bundle_id,
                app_path=app_path,
                clone=bool(clone),
            )
            _session.start()

            port = _session._port
            runner_url = _session.runner_url

            _backend = XCTestBackend(port=port)
            _annotator = SoMAnnotator(runner_url=runner_url)
            _last_elements = []

            # Start recording — every subsequent tool call will be captured
            from specterqa.ios.replay import ReplayRecorder
            _recorder = ReplayRecorder(bundle_id=bundle_id, device_id=device_id)

            return {
                "status": "ok",
                "clone_udid": _session._target_udid,
                "port": port,
                "runner_url": runner_url,
            }
        except Exception as exc:
            # Clean up partial state on failure
            _session = None
            _backend = None
            _annotator = None
            _last_elements = []
            _recorder = None
            return {"error": str(exc)}


def handle_stop_session(arguments: dict) -> dict:
    """Stop the runner and clean up resources.

    Returns:
        {"status": "stopped"}
    """
    global _session, _backend, _annotator, _last_elements, _recorder

    with _session_lock:
        if _session is not None:
            try:
                _session.stop()
            except Exception as exc:
                logger.warning("Error stopping session: %s", exc)

        _session = None
        _backend = None
        _annotator = None
        _last_elements = []
        _recorder = None

    return {"status": "stopped"}


def handle_screenshot(arguments: dict) -> dict:
    """Capture an annotated screenshot with numbered element badges.

    This is the KEY tool — Claude sees the annotated image and picks
    element numbers to interact with via ios_tap.

    Args:
        max_elements: Cap the number of elements returned (default 100).
                      Use 0 for unlimited.  Excess elements are truncated
                      after annotation so badges remain accurate for the
                      returned set.
        quality:      Screenshot size vs. quality trade-off.
                      "standard" (default) — resize to 50% (< 200 KB typical).
                      "full"               — no resize (original resolution).
                      "thumbnail"          — resize to 25% (< 50 KB typical).

    Returns:
        {
            "image": "<base64 PNG with numbered bounding-box annotations>",
            "elements": [
                {"index": 1, "label": "General", "type": "Cell",
                 "x": 16, "y": 278, "width": 358, "height": 52},
                ...
            ],
            "count": <int>,
            "truncated": <bool>,   # present only when elements were capped
            "total": <int>,        # total before truncation (when truncated=True)
        }
        or {"error": "<message>"} on failure.
    """
    global _last_elements

    # BUG V5-2 FIX: honour max_elements cap (default 100; 0 = unlimited).
    max_elements = int(arguments.get("max_elements", 100))
    # BUG V5-5 FIX: honour quality parameter to control output image size.
    quality = str(arguments.get("quality", "standard")).lower()
    scale = _QUALITY_SCALES.get(quality, 0.5)

    try:
        annotated_b64, elements = _get_annotated_screenshot()

        total = len(elements)
        truncated = False
        if max_elements > 0 and total > max_elements:
            elements = elements[:max_elements]
            truncated = True

        _last_elements = elements

        # Resize the annotated screenshot AFTER annotation so numbers remain
        # readable (annotation was done on full-res; we just shrink the result).
        resized_b64 = _resize_screenshot(annotated_b64, scale=scale)

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

        result: dict = {
            "image": resized_b64,
            "elements": element_list,
            "count": len(element_list),
        }
        if truncated:
            result["truncated"] = True
            result["total"] = total
            result["returned"] = len(element_list)
        return result
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

    # Record the tap for replay
    if _recorder is not None:
        _recorder.record_tap(element_index, target.label, cx, cy)

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

    # Record the swipe for replay
    if _recorder is not None:
        _recorder.record_swipe(direction)

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

    # Record the swipe-back for replay
    if _recorder is not None:
        _recorder.record_swipe_back()

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

    # Record the type action for replay
    if _recorder is not None:
        _recorder.record_type(text)

    return {"status": "ok", "typed": text}


def handle_elements(arguments: dict) -> dict:
    """Get the current element list without capturing a screenshot (fast).

    Useful when Claude needs to refresh the element index without the
    overhead of image annotation.

    Args:
        max_elements: Cap the number of elements returned (default 100).
                      Use 0 for unlimited.

    Returns:
        {
            "elements": [
                {"index": 1, "label": "...", "type": "...",
                 "x": .., "y": .., "width": .., "height": ..},
                ...
            ],
            "count": <int>,
            "truncated": <bool>,   # present only when elements were capped
            "total": <int>,        # total before truncation (when truncated=True)
        }
        or {"error": "<message>"} on failure.
    """
    global _last_elements

    try:
        _require_session()
    except RuntimeError as exc:
        return {"error": str(exc)}

    # BUG V5-2 FIX: honour max_elements cap (default 100; 0 = unlimited).
    max_elements = int(arguments.get("max_elements", 100))

    try:
        # Use JSON-direct path to skip the XML roundtrip.
        elements = _annotator.get_elements_from_runner()

        total = len(elements)
        truncated = False
        if max_elements > 0 and total > max_elements:
            elements = elements[:max_elements]
            truncated = True

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

        result: dict = {"elements": element_list, "count": len(element_list)}
        if truncated:
            result["truncated"] = True
            result["total"] = total
            result["returned"] = len(element_list)
        return result
    except Exception as exc:
        return {"error": str(exc)}


def _find_element(element_index: int | None):
    """Look up an element by index in the last-captured element cache.

    Args:
        element_index: Integer index from the last ``ios_screenshot`` or
                       ``ios_elements`` call.

    Returns:
        The matching ``UIElement``, or ``None`` if not found.
    """
    if element_index is None:
        return None
    return next((e for e in _last_elements if e.index == element_index), None)


def handle_press_key(arguments: dict) -> dict:
    """Press a named keyboard key on the focused element.

    Args:
        key: Key name string — e.g. "return", "escape", "delete", "tab",
             "space".  Forwarded directly to the XCTest runner's ``/key``
             endpoint.

    Returns:
        {"status": "ok", "key": "<key>"}
        or {"error": "<message>"} on failure.
    """
    try:
        _require_session()
    except RuntimeError as exc:
        return {"error": str(exc)}

    key = arguments.get("key", "")
    if not key:
        return {"error": "key is required (return, escape, delete, tab, space, etc.)"}

    try:
        _backend.press_key(key)
    except Exception as exc:
        return {"error": f"press_key failed: {exc}"}

    # Record the key press for replay
    if _recorder is not None:
        _recorder.record_press_key(key)

    return {"status": "ok", "key": key}


def handle_long_press(arguments: dict) -> dict:
    """Long-press an element by its index number.

    Args:
        element_index: Integer index from the last ``ios_screenshot`` call
                       (required).
        duration:      Hold duration in seconds (default 1.0).  Must be > 0.

    Returns:
        {"status": "ok", "label": "<label>", "duration": <float>}
        or {"error": "<message>"} on failure.
    """
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

    duration = float(arguments.get("duration", 1.0))
    if duration <= 0:
        return {"error": "duration must be > 0"}

    target = _find_element(element_index)
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
        _backend.tap(cx, cy, duration=duration)
    except Exception as exc:
        return {"error": f"Long press failed: {exc}"}

    # Record the long press for replay
    if _recorder is not None:
        _recorder.record_long_press(element_index, target.label, cx, cy, duration)

    return {"status": "ok", "label": target.label, "duration": duration}


# BUG V5-3 FIX: appearance toggle and generic simctl access.

def handle_set_appearance(arguments: dict) -> dict:
    """Toggle dark/light mode on the simulator.

    Args:
        mode: "dark" or "light" (default "dark").

    Returns:
        {"status": "ok", "appearance": "<mode>"}
        or {"error": "<message>"} on failure.
    """
    try:
        _require_session()
    except RuntimeError as exc:
        return {"error": str(exc)}

    mode = str(arguments.get("mode", "dark")).lower()
    if mode not in ("dark", "light"):
        return {"error": "mode must be 'dark' or 'light'"}

    result = subprocess.run(
        ["xcrun", "simctl", "ui", _session._target_udid, "appearance", mode],
        capture_output=True,
        text=True,
        timeout=10,
    )
    if result.returncode != 0:
        return {"error": f"simctl failed: {result.stderr.strip()}"}
    return {"status": "ok", "appearance": mode}


def handle_simctl(arguments: dict) -> dict:
    """Run an arbitrary simctl subcommand on the simulator.

    The simulator's UDID is injected automatically wherever the literal
    string ``<udid>`` appears in the command string — or prepended as
    the first positional argument after the subcommand keyword for
    well-known single-UDID commands (``ui``, ``status_bar``,
    ``location``, ``push``, ``privacy``).

    Args:
        command: Simctl subcommand and arguments as a single string.
                 Examples:
                   "ui <udid> appearance dark"
                   "status_bar <udid> override --time 9:41"
                   "ui appearance light"  (UDID auto-inserted)

    Returns:
        {"status": "ok", "stdout": "...", "stderr": "..."}
        or {"error": "<message>"} on failure.
    """
    try:
        _require_session()
    except RuntimeError as exc:
        return {"error": str(exc)}

    command = str(arguments.get("command", "")).strip()
    if not command:
        return {"error": "command is required"}

    udid = _session._target_udid

    # Replace placeholder token with the real UDID.
    if "<udid>" in command:
        command = command.replace("<udid>", udid)
    else:
        # Auto-insert UDID for known single-UDID subcommands.
        _UDID_SUBCOMMANDS = {"ui", "status_bar", "location", "push", "privacy"}
        parts = command.split()
        if parts and parts[0] in _UDID_SUBCOMMANDS:
            parts.insert(1, udid)
            command = " ".join(parts)

    full_cmd = ["xcrun", "simctl"] + command.split()
    try:
        result = subprocess.run(
            full_cmd,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except subprocess.TimeoutExpired:
        return {"error": f"simctl command timed out after 30s: {command}"}
    except Exception as exc:
        return {"error": f"simctl execution error: {exc}"}

    if result.returncode != 0:
        return {
            "error": f"simctl exited with code {result.returncode}",
            "stderr": result.stderr.strip(),
            "stdout": result.stdout.strip(),
        }
    return {"status": "ok", "stdout": result.stdout.strip(), "stderr": result.stderr.strip()}


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
            "numbered elements) → ios_tap / ios_long_press / ios_swipe / ios_type / "
            "ios_press_key → repeat → ios_stop_session. "
            "Use ios_elements for a fast element refresh without a screenshot. "
            "Requires macOS with Xcode 15+ and a compiled XCTest runner."
        ),
    )

    # ── Tool: ios_start_session ────────────────────────────────────────────

    @mcp.tool(
        name="ios_start_session",
        description=(
            "Start the XCTest runner on the booted iOS Simulator. "
            "Deploys directly to the booted sim — no cloning, full networking. "
            "bundle_id is required (e.g. 'com.example.MyApp'). "
            "device_id defaults to 'booted'. "
            "app_path is an optional path to a .app bundle to install. "
            "license_key is optional — omit for trial mode or set to 'founder'."
        ),
    )
    async def ios_start_session(
        bundle_id: str,
        device_id: str = "booted",
        app_path: str | None = None,
        license_key: str | None = None,
        clone: bool = False,
    ) -> str:
        result = handle_start_session({
            "bundle_id": bundle_id,
            "device_id": device_id,
            "app_path": app_path,
            "license_key": license_key or "",
            "clone": clone,
        })
        return json.dumps(result)

    # ── Tool: ios_stop_session ─────────────────────────────────────────────

    @mcp.tool(
        name="ios_stop_session",
        description=(
            "Stop the XCTest runner and clean up. "
            "Call this when testing is complete."
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
            "This is the primary perception tool — call it before tapping. "
            "max_elements caps the returned element count (default 100; 0 = unlimited). "
            "quality controls image size: 'standard' (50%, default), 'full' (no resize), "
            "'thumbnail' (25%)."
        ),
    )
    async def ios_screenshot(
        max_elements: int = 100,
        quality: str = "standard",
    ) -> str:
        result = handle_screenshot({"max_elements": max_elements, "quality": quality})
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
            "Also updates the element cache used by ios_tap. "
            "max_elements caps the returned element count (default 100; 0 = unlimited)."
        ),
    )
    async def ios_elements(max_elements: int = 100) -> str:
        result = handle_elements({"max_elements": max_elements})
        return json.dumps(result, default=_json_serialize)

    # ── Tool: ios_set_appearance ───────────────────────────────────────────

    @mcp.tool(
        name="ios_set_appearance",
        description=(
            "Toggle dark or light mode on the iOS Simulator. "
            "mode must be 'dark' or 'light' (default 'dark'). "
            "Requires an active session (ios_start_session). "
            "After changing appearance, call ios_screenshot to see the updated screen."
        ),
    )
    async def ios_set_appearance(mode: str = "dark") -> str:
        result = handle_set_appearance({"mode": mode})
        return json.dumps(result)

    # ── Tool: ios_press_key ────────────────────────────────────────────────

    @mcp.tool(
        name="ios_press_key",
        description=(
            "Press a named keyboard key on the iOS Simulator. "
            "Use this after tapping a text field to send control keys: "
            "'return' (submit/next field), 'escape' (dismiss), "
            "'delete' (backspace), 'tab' (next field), 'space', etc. "
            "key is required."
        ),
    )
    async def ios_press_key(key: str) -> str:
        result = handle_press_key({"key": key})
        return json.dumps(result)

    # ── Tool: ios_long_press ───────────────────────────────────────────────

    @mcp.tool(
        name="ios_long_press",
        description=(
            "Long-press an element by its index number from the last screenshot. "
            "Use for context menus, drag initiation, or any gesture requiring a "
            "sustained hold. "
            "element_index is required (integer from ios_screenshot). "
            "duration is the hold time in seconds (default 1.0)."
        ),
    )
    async def ios_long_press(element_index: int, duration: float = 1.0) -> str:
        result = handle_long_press({"element_index": element_index, "duration": duration})
        return json.dumps(result)

    # ── Tool: ios_save_replay ──────────────────────────────────────────────

    @mcp.tool(
        name="ios_save_replay",
        description=(
            "Save the current session as a deterministic replay YAML file. "
            "The replay can be run in CI without AI: "
            "  specterqa-ios replay <file.yaml>. "
            "name is the human-readable test name used as the filename stem "
            "(default: 'replay'). "
            "path overrides the output location "
            "(default: .specterqa/replays/<name>.yaml). "
            "Recording starts automatically when ios_start_session is called — "
            "every tap, swipe, type, press_key, and long_press is captured. "
            "Call this tool when the test journey is complete."
        ),
    )
    async def ios_save_replay(name: str = "replay", path: str = "") -> str:
        result = handle_save_replay({"name": name, "path": path or ""})
        return json.dumps(result)

    # ── Tool: ios_simctl ───────────────────────────────────────────────────

    @mcp.tool(
        name="ios_simctl",
        description=(
            "Run an arbitrary simctl subcommand on the simulator. "
            "The simulator UDID is inserted automatically — use '<udid>' as a placeholder "
            "or omit it for well-known single-UDID subcommands (ui, status_bar, "
            "location, push, privacy). "
            "Examples: "
            "'ui <udid> appearance dark', "
            "'status_bar <udid> override --time 9:41', "
            "'ui appearance light' (UDID auto-inserted). "
            "Requires an active session (ios_start_session)."
        ),
    )
    async def ios_simctl(command: str) -> str:
        result = handle_simctl({"command": command})
        return json.dumps(result)

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
