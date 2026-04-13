"""SpecterQA iOS MCP Server — Native primitives for Claude Code.

Claude Code IS the reasoning engine. This server exposes direct
simulator control primitives — no Claude API calls, no SoM pipeline,
no orchestration loops. Claude sees annotated screenshots and decides
what to do.

Usage:
    specterqa-ios-mcp            # stdio transport (console_scripts entry point)
    python -m specterqa.ios.mcp  # alternative invocation
    specterqa ios serve          # via CLI serve command

Tools (27 total):
    ios_start_session       Start XCTest runner on the iOS Simulator
    ios_stop_session        Stop the XCTest runner and clean up
    ios_screenshot          Annotated screenshot with numbered elements
    ios_tap                 Tap element by label (preferred) or index number
    ios_long_press          Long-press element by index (context menus, drag init)
    ios_press_key           Press a keyboard key (return, escape, delete, tab, ...)
    ios_swipe               Swipe in a direction
    ios_swipe_back          iOS back navigation gesture
    ios_type                Type text into focused field
    ios_wait                Sleep for N seconds
    ios_wait_for_element    Poll until a labelled element appears
    ios_wait_idle           Wait for app to become idle (element tree stabilizes)
    ios_app_state           Check app lifecycle state (foreground/background/suspended)
    ios_dismiss_sheet       Dismiss a sheet/modal by swiping down
    ios_elements            Get element list without screenshot
    ios_set_appearance      Toggle dark/light mode on the simulator
    ios_simctl              Run arbitrary simctl subcommand on the simulator
    ios_webview_elements    Get elements inside WKWebView content (EPUB readers, PDF viewers)
    ios_start_recording     Clear step buffer; begin clean recording
    ios_stop_recording      Save replay YAML + clear buffer (marks end of flow)
    ios_save_replay         Save replay YAML without clearing the step buffer
    ios_accessibility_audit Audit current screen for accessibility issues
    ios_logs                Get recent app console logs from the iOS Simulator
    ios_crashes             Check for app crashes since session start
    ios_perf                CPU, memory (RSS), and thread count snapshot
    ios_memory              Detailed memory breakdown via footprint tool
    ios_network             Network activity: URLs, bytes in/out, throughput

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
import time
from pathlib import Path
from typing import Any

from PIL import Image

logger = logging.getLogger("specterqa.ios.mcp")

# ---------------------------------------------------------------------------
# Global session state — one active session at a time
# ---------------------------------------------------------------------------

_session = None  # TestSession instance
_backend = None  # XCTestBackend instance
_annotator = None  # SoMAnnotator instance
_last_elements: list = []  # Element cache from last ios_screenshot / ios_elements call
_session_lock = threading.Lock()  # Serialises start/stop to prevent race conditions
_recorder = None  # ReplayRecorder instance (None when recording is not active)
_session_state = "idle"  # idle | running | crashed
_console_monitor = None  # ConsoleMonitor instance (None when session is not active)
_crash_detector = None  # CrashDetector instance (None when session is not active)
_perf_profiler = None  # PerfProfiler instance (None when session is not active)
_network_inspector = None  # NetworkInspector instance (None when session is not active)


def _require_session() -> None:
    """Raise RuntimeError if no active session exists or session has crashed."""
    global _session_state
    if _session_state == "crashed":
        raise RuntimeError("Session crashed. Call ios_stop_session then ios_start_session to recover.")
    if _backend is None:
        raise RuntimeError("No active session. Call ios_start_session first.")
    # Health probe — detect runner death mid-session
    try:
        _backend.health()
    except Exception:
        _session_state = "crashed"
        raise RuntimeError("Session crashed (runner unreachable). Call ios_stop_session then ios_start_session.")


def _auto_checkpoint() -> None:
    """Capture current element state as a replay checkpoint after an action."""
    if _recorder is not None and _annotator is not None:
        try:
            import time

            time.sleep(0.3)  # let UI settle
            elements = _annotator.get_elements_from_runner()
            labels = [e.label for e in elements[:15] if e.label]
            if labels:
                _recorder.add_checkpoint(labels)
        except Exception as exc:  # noqa: BLE001 — checkpoint auto-record is best-effort
            logger.debug("_record_checkpoint failed: %s", exc)


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
        runtime_label = runtime_id.replace("com.apple.CoreSimulator.SimRuntime.", "").replace("-", " ")
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
    img = img.convert("RGB")
    img.save(buf, format="JPEG", quality=85, optimize=True)
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
        (Starts PerfProfiler and NetworkInspector alongside the session.)
    """
    global _session, _backend, _annotator, _last_elements, _recorder, _session_state, _console_monitor, _crash_detector, _perf_profiler, _network_inspector

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

        # Auto-build runner if not built or stale (version marker mismatch).
        from specterqa.ios.session_manager import (
            _find_xctestrun,
            _DEFAULT_RUNNER_BUILD_DIR,
            _needs_rebuild,
            write_version_marker,
        )

        if _find_xctestrun(_DEFAULT_RUNNER_BUILD_DIR) is None or _needs_rebuild(_DEFAULT_RUNNER_BUILD_DIR):
            logger.info("Runner not built or stale — building automatically...")
            try:
                runner_dir = Path(__file__).parent.parent.parent.parent / "runner"
                build_sh = runner_dir / "build.sh"
                if build_sh.exists():
                    result = subprocess.run(
                        ["bash", str(build_sh)],
                        capture_output=True,
                        text=True,
                        timeout=120,
                        cwd=str(runner_dir),
                    )
                    if result.returncode == 0:
                        write_version_marker(_DEFAULT_RUNNER_BUILD_DIR)
            except Exception as exc:
                logger.warning("Auto-build failed: %s", exc)

        # Auto-detect provider: local sim or BrowserStack
        provider = "local"
        from specterqa.ios.backends.browserstack import BrowserStackBackend

        if BrowserStackBackend.is_available():
            # Check if a local sim is booted
            sim_check = subprocess.run(
                ["xcrun", "simctl", "list", "devices", "booted", "-j"],
                capture_output=True,
                text=True,
            )
            has_local_sim = '"state" : "Booted"' in sim_check.stdout
            if not has_local_sim:
                provider = "browserstack"

        # Explicit env-var override wins over auto-detection
        env_provider = os.environ.get("SPECTERQA_PROVIDER", "").lower()
        if env_provider in ("browserstack", "bs"):
            provider = "browserstack"
        elif env_provider == "local":
            provider = "local"

        from specterqa.ios.som_annotator import SoMAnnotator

        if provider == "browserstack":
            try:
                bs = BrowserStackBackend()
                if app_path:
                    bs.upload_app(app_path)
                session_id = bs.start_session(bundle_id)
                _backend = bs
                _annotator = SoMAnnotator()
                _last_elements = []

                from specterqa.ios.replay import ReplayRecorder

                _recorder = ReplayRecorder(bundle_id=bundle_id, device_id=device_id)

                _session_state = "running"
                return {
                    "status": "ok",
                    "provider": "browserstack",
                    "session_id": session_id,
                    "device": bs.device,
                    "os_version": bs.os_version,
                }
            except Exception as exc:
                _backend = None
                _annotator = None
                _last_elements = []
                _recorder = None
                _session_state = "idle"
                return {"error": str(exc)}

        from specterqa.ios.session_manager import TestSession
        from specterqa.ios.backends.xctest_client import XCTestBackend

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

            # Start console log monitor and crash detector
            from specterqa.ios.drivers.simulator.console import ConsoleMonitor
            from specterqa.ios.drivers.simulator.crash import CrashDetector

            _console_monitor = ConsoleMonitor(device_id=_session._target_udid)
            _console_monitor.start()

            _crash_detector = CrashDetector(device_id=_session._target_udid, bundle_id=bundle_id)
            _crash_detector.start()

            # Start performance profiler and network inspector
            from specterqa.ios.drivers.simulator.perf import PerfProfiler
            from specterqa.ios.drivers.simulator.network import NetworkInspector

            _perf_profiler = PerfProfiler(
                device_id=_session._target_udid,
                bundle_id=bundle_id,
            )

            _network_inspector = NetworkInspector(device_id=_session._target_udid)
            _network_inspector.start()
            # Wire CFNetwork log watcher to capture HTTP activity from os_log
            _network_inspector.setup_log_watcher(_console_monitor)

            _session_state = "running"
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
            _console_monitor = None
            _crash_detector = None
            _perf_profiler = None
            _network_inspector = None
            _session_state = "idle"
            return {"error": str(exc)}


def handle_stop_session(arguments: dict) -> dict:
    """Stop the runner and clean up resources.

    Returns:
        {"status": "stopped"}
    """
    global _session, _backend, _annotator, _last_elements, _recorder, _session_state, _console_monitor, _crash_detector, _perf_profiler, _network_inspector

    with _session_lock:
        from specterqa.ios.backends.browserstack import BrowserStackBackend

        if isinstance(_backend, BrowserStackBackend):
            try:
                _backend.stop()
            except Exception as exc:
                logger.warning("Error stopping BrowserStack session: %s", exc)
        elif _session is not None:
            try:
                _session.stop()
            except Exception as exc:
                logger.warning("Error stopping session: %s", exc)

        # Stop console monitor (terminates background log stream process)
        if _console_monitor is not None:
            try:
                _console_monitor.stop()
            except Exception as exc:
                logger.warning("Error stopping console monitor: %s", exc)

        # Stop crash detector (clears baseline state; no background process)
        if _crash_detector is not None:
            try:
                _crash_detector.stop()
            except Exception as exc:
                logger.warning("Error stopping crash detector: %s", exc)

        # Stop network inspector (terminates nettop background thread)
        if _network_inspector is not None:
            try:
                _network_inspector.stop()
            except Exception as exc:
                logger.warning("Error stopping network inspector: %s", exc)

        # PerfProfiler has no background thread — just clear the reference
        _session = None
        _backend = None
        _annotator = None
        _last_elements = []
        _recorder = None
        _console_monitor = None
        _crash_detector = None
        _perf_profiler = None
        _network_inspector = None
        _session_state = "idle"

    return {"status": "stopped"}


def handle_logs(arguments: dict) -> dict:
    """Get recent app console logs from the iOS Simulator.

    Args:
        seconds:  Time window to query (default 30.0).
        level:    Optional level filter, e.g. ``"error"`` or ``"fault"``.
                  When set to ``"error"`` or ``"fault"``, the dedicated error
                  buffer is queried instead of the main ring buffer.
        category: Optional category filter (exact match).
        pattern:  Optional regex pattern applied to the message field.
                  When provided, overrides ``level`` and ``seconds``.

    Returns:
        {"count": <int>, "logs": [...], "summary": {...}}
        or {"error": "<message>"} on failure.
    """
    if _console_monitor is None:
        return {
            "error": (
                "No active session or console monitor not started. "
                "Call ios_start_session first."
            )
        }

    seconds = float(arguments.get("seconds", 30))
    level = arguments.get("level")
    category = arguments.get("category")
    pattern = arguments.get("pattern")

    if pattern:
        entries = _console_monitor.search(pattern)
    elif level and level.lower() in ("error", "fault"):
        entries = _console_monitor.errors(seconds=seconds)
    else:
        entries = _console_monitor.recent(seconds=seconds, level=level, category=category)

    # Cap at 100 entries to keep MCP payloads reasonable
    log_list = []
    for entry in entries[-100:]:
        log_list.append({
            "timestamp": str(getattr(entry, "timestamp", "")),
            "level": getattr(entry, "level", ""),
            "subsystem": getattr(entry, "subsystem", ""),
            "category": getattr(entry, "category", ""),
            "message": getattr(entry, "message", ""),
            "process": getattr(entry, "process", ""),
        })

    return {
        "count": len(log_list),
        "logs": log_list,
        "summary": _console_monitor.summary(),
    }


def handle_crashes(arguments: dict) -> dict:
    """Check for app crashes since the session started.

    Returns:
        {
            "crashes_since_session_start": <int>,
            "crashes": [...],
            "app_running": <bool>,
            "latest_crash": {...} | null,
        }
        or {"error": "<message>"} on failure.
    """
    if _crash_detector is None:
        return {
            "error": (
                "No active session or crash detector not started. "
                "Call ios_start_session first."
            )
        }

    crashes = _crash_detector.check()
    latest = _crash_detector.latest_crash()
    is_running = _crash_detector.is_app_running()

    crash_list = []
    for crash in crashes:
        crash_list.append({
            "timestamp": str(getattr(crash, "timestamp", "")),
            "exception_type": getattr(crash, "exception_type", ""),
            "exception_code": getattr(crash, "exception_code", ""),
            "crashing_thread": getattr(crash, "crashing_thread", 0),
            "backtrace": getattr(crash, "backtrace", []),
            "app_version": getattr(crash, "app_version", ""),
            "os_version": getattr(crash, "os_version", ""),
        })

    return {
        "crashes_since_session_start": len(crash_list),
        "crashes": crash_list,
        "app_running": is_running,
        "latest_crash": {
            "timestamp": str(latest.timestamp),
            "exception_type": latest.exception_type,
            "backtrace": latest.backtrace,
        } if latest is not None else None,
    }


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


def _lookup(label, identifier, element_index, element_type, elements):
    """Look up an element from *elements* using priority: identifier > label > index.

    Label matching uses scored matching: exact > prefix > substring, shorter wins.
    Index matching does NOT auto-refresh (indices are position-dependent).

    Args:
        label:         Label substring to match (case-insensitive).
        identifier:    Exact accessibilityIdentifier to match.
        element_index: Integer index to match.
        element_type:  Optional element type filter (applied to label candidates).
        elements:      List of UIElement objects to search.

    Returns:
        The matching UIElement, or None.
    """
    # 1. Identifier (exact match)
    if identifier:
        match = next((e for e in elements if getattr(e, "identifier", "") == identifier), None)
        if match:
            return match

    # 2. Label (scored: exact > prefix > substring, shorter wins)
    if label:
        label_lower = label.lower()
        candidates = []
        for e in elements:
            el = e.label.lower()
            if el == label_lower:
                candidates.append((2, -len(el), e))  # exact
            elif el.startswith(label_lower):
                candidates.append((1, -len(el), e))  # prefix
            elif label_lower in el:
                candidates.append((0, -len(el), e))  # substring

        if element_type:
            type_lower = element_type.lower()
            typed = [(s, l, e) for s, l, e in candidates if e.element_type.lower() == type_lower]
            if typed:
                candidates = typed

        if candidates:
            candidates.sort(key=lambda x: (-x[0], -x[1]))  # highest score, shortest label
            return candidates[0][2]

    # 3. Index (NO auto-refresh — indices are position-dependent)
    if element_index is not None:
        return next((e for e in elements if e.index == element_index), None)

    return None


def _resolve_element(
    label=None,
    identifier=None,
    element_index=None,
    element_type=None,
):
    """Resolve an element from cache, auto-refreshing on miss.

    Returns (element, was_refreshed).
    Priority: identifier > label > element_index.
    Label uses scored matching: exact > prefix > substring, shorter wins.
    Index-based lookups do NOT trigger auto-refresh (indices are stale after navigation).
    """
    global _last_elements

    target = _lookup(label, identifier, element_index, element_type, _last_elements)
    if target is not None:
        return target, False

    # Cache miss for identifier/label — auto-refresh once (navigation likely happened).
    # Do NOT auto-refresh for index-only lookups (position-dependent, refresh is misleading).
    if element_index is None and _annotator is not None:
        try:
            fresh = _annotator.get_elements_from_runner()
            _last_elements = fresh
            target = _lookup(label, identifier, None, element_type, fresh)
            return target, True
        except Exception:
            pass

    return None, False


def handle_tap(arguments: dict) -> dict:
    """Tap an element by identifier, label, index, or explicit coordinates.

    Args:
        identifier:    Exact accessibilityIdentifier match (most reliable).
                       Use this when the element has an accessibility identifier set.
        element_index: Integer index shown in the annotated screenshot.
                       Use this OR label — not both.
        label:         Case-insensitive substring to match against element labels.
                       Preferred over element_index when available (label-stable tapping).
        type:          Optional element type filter when using label (e.g. "Button").
                       Only applies when label is provided.
        x:             X coordinate for a direct coordinate tap (used with y).
        y:             Y coordinate for a direct coordinate tap (used with x).

    Returns:
        {"status": "ok", "tapped": "<label>", "x": <cx>, "y": <cy>}
        or {"error": "<message>"} on failure.
    """
    global _last_elements

    identifier = arguments.get("identifier")
    label = arguments.get("label")
    element_type_filter = arguments.get("type")
    element_index = arguments.get("element_index")
    coord_x = arguments.get("x")
    coord_y = arguments.get("y")

    # ── Coordinate tap (direct — no element lookup needed) ──
    if identifier is None and label is None and element_index is None:
        if coord_x is not None and coord_y is not None:
            try:
                _require_session()
            except RuntimeError as exc:
                return {"error": str(exc)}

            try:
                _backend.tap(float(coord_x), float(coord_y))
            except Exception as exc:
                return {"error": f"Coordinate tap failed: {exc}"}

            if _recorder is not None:
                _recorder.record_tap(-1, "", float(coord_x), float(coord_y))

            _auto_checkpoint()

            return {
                "status": "ok",
                "tapped": "",
                "x": float(coord_x),
                "y": float(coord_y),
            }

        # No lookup method specified at all
        return {"error": "One of identifier, label, element_index, or x+y coordinates is required"}

    # ── Element lookup (identifier / label / index) via unified resolver ──
    try:
        _require_session()
    except RuntimeError as exc:
        return {"error": str(exc)}

    if element_index is not None:
        try:
            element_index = int(element_index)
        except (TypeError, ValueError):
            return {"error": f"element_index must be an integer, got: {element_index!r}"}

    target, was_refreshed = _resolve_element(
        label=label,
        identifier=identifier,
        element_index=element_index,
        element_type=element_type_filter,
    )

    if target is None:
        if coord_x is not None and coord_y is not None:
            # Fall through to coordinate tap below
            try:
                _backend.tap(float(coord_x), float(coord_y))
            except Exception as exc:
                return {"error": f"Coordinate tap failed: {exc}"}

            if _recorder is not None:
                _recorder.record_tap(-1, "", float(coord_x), float(coord_y))

            _auto_checkpoint()

            return {
                "status": "ok",
                "tapped": "",
                "x": float(coord_x),
                "y": float(coord_y),
            }

        # Build a helpful error message
        if identifier is not None:
            return {
                "error": (
                    f"No element found with identifier '{identifier}'. "
                    "Call ios_screenshot first to refresh elements."
                )
            }
        if label is not None:
            return {
                "error": (
                    f"No element found with label containing '{label}'"
                    + (f" and type '{element_type_filter}'" if element_type_filter else "")
                    + ". Call ios_screenshot first to refresh elements."
                )
            }
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

    # Fix 6: hittable fallback — element found but obscured (behind sheet/overlay)
    if getattr(target, "hittable", True) is False:
        try:
            _backend.tap(cx, cy)
        except Exception as exc:
            return {"error": f"Coordinate tap failed: {exc}"}

        if _recorder is not None:
            _recorder.record_tap(target.index, target.label, cx, cy, identifier=getattr(target, "identifier", ""))

        _auto_checkpoint()

        return {
            "status": "ok",
            "tapped": target.label,
            "x": cx,
            "y": cy,
            "warning": "Element not hittable — used coordinate tap (may be behind overlay)",
            **({"cache_refreshed": True} if was_refreshed else {}),
        }

    try:
        _backend.tap(cx, cy)
    except Exception as exc:
        return {"error": f"Tap failed: {exc}"}

    # Record the tap for replay
    if _recorder is not None:
        _recorder.record_tap(target.index, target.label, cx, cy, identifier=getattr(target, "identifier", ""))

    # Auto-checkpoint: capture element state after action for replay verification
    _auto_checkpoint()

    result = {
        "status": "ok",
        "tapped": target.label,
        "x": cx,
        "y": cy,
    }
    if was_refreshed:
        result["cache_refreshed"] = True
    return result


def handle_wait(arguments: dict) -> dict:
    """Sleep for a specified number of seconds (capped at 30s).

    Args:
        seconds: Time to wait in seconds (default 1.0, max 30.0).

    Returns:
        {"status": "ok", "waited": <seconds>}
    """
    import time as _time

    seconds = max(0.0, min(float(arguments.get("seconds", 1.0)), 30.0))
    _time.sleep(seconds)
    return {"status": "ok", "waited": seconds}


def handle_wait_for_element(arguments: dict) -> dict:
    """Poll the element tree until an element matching *label* appears.

    Args:
        label:   Case-insensitive substring to match against element labels (required).
        timeout: Maximum wait in seconds (default 10, max 30).

    Returns:
        {"status": "found", "label": "<matched label>", "index": <int>}
        or {"status": "not_found", "label": "<label>", "timeout": <seconds>}
        or {"error": "<message>"} when no session is active.
    """
    try:
        _require_session()
    except RuntimeError as exc:
        return {"error": str(exc)}

    label = str(arguments.get("label", ""))
    if not label:
        return {"error": "label is required"}

    timeout = min(float(arguments.get("timeout", 10)), 30.0)
    poll_interval = 0.5
    deadline = time.time() + timeout

    while time.time() < deadline:
        try:
            elements = _annotator.get_elements_from_runner()
            for e in elements:
                if label.lower() in e.label.lower():
                    return {"status": "found", "label": e.label, "index": e.index}
        except Exception as exc:  # noqa: BLE001 — element probe must not abort wait loop
            logger.debug("Element probe failed during wait: %s", exc)
        time.sleep(poll_interval)

    return {"status": "not_found", "label": label, "timeout": timeout}


def handle_start_recording(arguments: dict) -> dict:
    """Clear the recorder's step list to start a fresh recording.

    Useful when you want to discard earlier exploratory steps and record
    only the clean, successful flow.

    Returns:
        {"status": "ok", "message": "Recording started fresh"}
        or {"error": "<message>"} when no session is active.
    """
    global _recorder
    if _recorder is None:
        return {"error": "No active session. Call ios_start_session first."}
    from specterqa.ios.replay import ReplayRecorder
    bundle_id = _recorder.session.bundle_id
    device_id = _recorder.session.device_id
    _recorder = ReplayRecorder(bundle_id=bundle_id, device_id=device_id)
    return {"status": "ok", "message": "Recording started — fresh buffer"}


def handle_stop_recording(arguments: dict) -> dict:
    """Save the replay AND clear the recorder (marks end-of-recording).

    Equivalent to ios_save_replay followed by clearing the step buffer.
    The session remains active — you can keep testing; a new ios_start_recording
    will start fresh for the next flow.

    Args:
        name: Human-readable test name used as filename stem (default "replay").
        path: Override output path (default: .specterqa/replays/<name>.yaml).

    Returns:
        {"status": "ok", "path": "...", "steps": <count>}
        or {"error": "<message>"} on failure.
    """
    global _recorder
    result = handle_save_replay(arguments)
    if "error" not in result and _recorder is not None:
        _recorder.session.steps.clear()
    return result


def handle_accessibility_audit(arguments: dict) -> dict:
    """Audit the current screen for common accessibility issues.

    Checks performed:
    - Missing labels on interactive elements
    - Touch targets smaller than 44x44 pt (Apple HIG minimum)
    - Duplicate accessibility labels (ambiguous for screen readers)

    Returns:
        {"issues": [...], "count": <int>, "elements_checked": <int>}
        Each issue has: {"type": str, "label": str, ...extra context}
        or {"error": "<message>"} when no session is active.
    """
    try:
        _require_session()
    except RuntimeError as exc:
        return {"error": str(exc)}

    try:
        elements = _annotator.get_elements_from_runner()
    except Exception as exc:
        return {"error": f"Failed to fetch elements: {exc}"}

    interactive_types = {
        "Button",
        "TextField",
        "SecureTextField",
        "Switch",
        "Slider",
        "Link",
        "MenuItem",
        "Cell",
    }

    issues = []

    for e in elements:
        # Missing label on an interactive element
        if not e.label and e.element_type in interactive_types:
            issues.append(
                {
                    "type": "missing_label",
                    "element_type": e.element_type,
                    "index": e.index,
                    "frame": f"{e.x},{e.y} {e.width}x{e.height}",
                }
            )

        # Touch target too small — only flag actually-interactive element types.
        # StaticText / Image / Other are non-interactive by design and routinely
        # smaller than 44 pt; including them floods the report with false positives.
        INTERACTIVE_FOR_AUDIT = {
            "XCUIElementTypeButton",
            "XCUIElementTypeCell",
            "XCUIElementTypeSwitch",
            "XCUIElementTypeSlider",
            "XCUIElementTypeLink",
            "XCUIElementTypeTab",
            "XCUIElementTypeMenuItem",
            "XCUIElementTypeRadioButton",
            "XCUIElementTypeCheckBox",
            # Short-form aliases (runner may omit the prefix)
            "Button",
            "Cell",
            "Switch",
            "Slider",
            "Link",
            "Tab",
            "MenuItem",
            "RadioButton",
            "CheckBox",
        }
        if e.element_type in INTERACTIVE_FOR_AUDIT and (e.width < 44 or e.height < 44):
            issues.append(
                {
                    "type": "small_target",
                    "label": e.label or f"[{e.element_type}@{e.index}]",
                    "element_type": e.element_type,
                    "size": f"{e.width}x{e.height}",
                    "index": e.index,
                }
            )

    # Duplicate labels
    labels = [e.label for e in elements if e.label]
    seen: dict[str, int] = {}
    for lbl in labels:
        seen[lbl] = seen.get(lbl, 0) + 1
    for lbl, count in seen.items():
        if count > 1:
            issues.append(
                {
                    "type": "duplicate_label",
                    "label": lbl,
                    "count": count,
                }
            )

    return {
        "issues": issues,
        "count": len(issues),
        "elements_checked": len(elements),
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
        return {"error": f"Invalid direction {direction!r}. Must be one of: {sorted(valid_directions)}"}

    # Centre of a standard iPhone screen in logical points
    cx, cy = 195, 422
    offset = 200

    coords = {
        "down": (cx, cy + offset, cx, cy - offset),
        "up": (cx, cy - offset, cx, cy + offset),
        "left": (cx + offset, cy, cx - offset, cy),
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

    # Auto-checkpoint: capture element state after action for replay verification
    _auto_checkpoint()

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

    # Auto-checkpoint: capture element state after action for replay verification
    _auto_checkpoint()

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

    # Auto-checkpoint: capture element state after action for replay verification
    _auto_checkpoint()

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

    # Allow the UI to settle after the key press.  For return/enter this is
    # critical: the keyboard dismiss animation takes ~300 ms, and the XCTest
    # accessibility tree is in a corrupted state until it completes.  Any
    # interaction (tap, screenshot, elements) arriving before the tree
    # stabilizes will crash the runner.  0.5 s covers the animation with margin.
    time.sleep(0.5)

    # Record the key press for replay
    if _recorder is not None:
        _recorder.record_press_key(key)

    # Auto-checkpoint: capture element state after action for replay verification
    _auto_checkpoint()

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

    # Auto-checkpoint: capture element state after action for replay verification
    _auto_checkpoint()

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

    # Try the runner's /appearance endpoint first (faster, no simctl dependency)
    try:
        result = _backend.set_appearance(mode)
        if result.get("success") or result.get("status") == "ok":
            return {"status": "ok", "appearance": mode, "method": "runner"}
    except Exception:
        pass  # Fall back to simctl

    # The "booted" alias fails when multiple simulators are booted or when
    # xcodebuild keeps its own simulator context that simctl can't see via the
    # "booted" shorthand.  Instead, enumerate ALL booted simulators from
    # `simctl list devices -j` and try each UDID until one accepts the change.
    import json as _json

    list_result = subprocess.run(
        ["xcrun", "simctl", "list", "devices", "-j"],
        capture_output=True,
        text=True,
        timeout=5,
    )
    booted_udids: list[str] = []
    try:
        data = _json.loads(list_result.stdout)
        for runtime_devs in data.get("devices", {}).values():
            for dev in runtime_devs:
                if dev.get("state") == "Booted":
                    booted_udids.append(dev["udid"])
    except (json.JSONDecodeError, KeyError):
        pass

    if not booted_udids:
        return {"error": "No booted simulators found"}

    last_error = ""
    for udid in booted_udids:
        result = subprocess.run(
            ["xcrun", "simctl", "ui", udid, "appearance", mode],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0:
            return {"status": "ok", "appearance": mode, "udid": udid, "method": "simctl"}
        last_error = result.stderr.strip()

    return {"error": f"All booted sims rejected appearance change: {last_error}"}


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

    # Enumerate booted simulators rather than using the "booted" alias, which
    # fails when multiple sims are up or when xcodebuild holds its own context.
    # Pick the first booted UDID; fall back to "booted" if enumeration fails.
    import json as _json

    _list = subprocess.run(
        ["xcrun", "simctl", "list", "devices", "-j"],
        capture_output=True,
        text=True,
        timeout=5,
    )
    _booted: list[str] = []
    try:
        _data = _json.loads(_list.stdout)
        for _devs in _data.get("devices", {}).values():
            for _d in _devs:
                if _d.get("state") == "Booted":
                    _booted.append(_d["udid"])
    except (json.JSONDecodeError, KeyError):
        pass
    udid = _booted[0] if _booted else "booted"

    # Replace placeholder token with the resolved UDID.
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


def handle_webview_elements(arguments: dict) -> dict:
    """Get elements inside WKWebView content (EPUB readers, PDF viewers, etc).

    XCTest can see WKWebView descendants via the .webViews descendants chain.
    This is the only way to interact with web content (EPUB readers, PDF viewers,
    audiobook UI) rendered in WKWebView.

    Returns:
        {"success": True, "elements": [...], "count": <int>}
        or {"error": "<message>"} when no session is active.
    """
    _require_session()
    try:
        result = _backend._get("/webview")
        return result
    except Exception as exc:
        return {"error": str(exc)}


def handle_wait_idle(arguments: dict) -> dict:
    """Wait for the app to become idle (element tree stabilizes).

    Args:
        timeout: Maximum wait in seconds (default 10.0, max 30.0).

    Returns:
        Runner response dict, or {"error": "<message>"} on failure.
    """
    try:
        _require_session()
    except RuntimeError as exc:
        return {"error": str(exc)}

    timeout = min(float(arguments.get("timeout", 10.0)), 30.0)
    try:
        result = _backend.wait_idle(timeout=timeout)
        return result
    except Exception as exc:
        return {"error": f"wait_idle failed: {exc}"}


def handle_app_state(arguments: dict) -> dict:
    """Check if the app is running, backgrounded, or crashed.

    Returns:
        Runner response dict with app lifecycle state, or {"error": "<message>"}.
    """
    try:
        _require_session()
    except RuntimeError as exc:
        return {"error": str(exc)}

    try:
        result = _backend.app_state()
        return result
    except Exception as exc:
        return {"error": f"app_state failed: {exc}"}


def handle_dismiss_sheet(arguments: dict) -> dict:
    """Dismiss a presented sheet by swiping down.

    Returns:
        {"status": "ok", "action": "swipe_down_dismiss"}
        or {"error": "<message>"} on failure.
    """
    try:
        _require_session()
    except RuntimeError as exc:
        return {"error": str(exc)}

    try:
        # Swipe down from top-center of screen to dismiss a sheet
        _backend.swipe(x1=195, y1=300, x2=195, y2=700, duration=0.3)
        time.sleep(0.5)
        return {"status": "ok", "action": "swipe_down_dismiss"}
    except Exception as exc:
        return {"error": f"dismiss_sheet failed: {exc}"}


# ---------------------------------------------------------------------------
# Performance / Memory / Network tool handlers
# ---------------------------------------------------------------------------


def handle_perf(arguments: dict) -> dict:
    """Get CPU, memory, and thread metrics for the app under test.

    Returns:
        {"cpu_percent": float, "memory_mb": float, "thread_count": int,
         "pid": int | null, "timestamp": float}
        or {"error": "<message>"} on failure.
    """
    if _perf_profiler is None:
        return {"error": "No active session. Call ios_start_session first."}

    try:
        snap = _perf_profiler.snapshot()
        pid = _perf_profiler._get_app_pid()
        return {
            "cpu_percent": snap.cpu_percent,
            "memory_mb": snap.memory_mb,
            "thread_count": snap.thread_count,
            "disk_usage_mb": snap.disk_usage_mb,
            "fps_estimate": snap.fps_estimate,
            "pid": pid,
            "timestamp": snap.timestamp,
        }
    except Exception as exc:
        return {"error": f"perf snapshot failed: {exc}"}


def handle_memory(arguments: dict) -> dict:
    """Get detailed memory breakdown via the ``footprint`` tool.

    Returns a dict with physical footprint, dirty pages, swapped/compressed,
    and clean pages — all in MB.  Falls back gracefully if footprint is
    unavailable or the app is not running.

    Returns:
        {"pid": int, "footprint_mb": float, "dirty_mb": float,
         "swapped_mb": float, "clean_mb": float}
        or {"pid": int | null, "error": "<message>"} on failure.
    """
    if _perf_profiler is None:
        return {"error": "No active session. Call ios_start_session first."}

    try:
        return _perf_profiler.memory_detail()
    except Exception as exc:
        return {"error": f"memory_detail failed: {exc}"}


def handle_network(arguments: dict) -> dict:
    """Get network activity for the app under test.

    Args:
        seconds: Time window for recent requests (default 30.0).

    Returns:
        {"requests": [...], "bytes_in": int, "bytes_out": int,
         "throughput_in": float, "throughput_out": float,
         "active_connections": int, "nettop_available": bool}
        or {"error": "<message>"} on failure.
    """
    if _network_inspector is None:
        return {"error": "No active session. Call ios_start_session first."}

    seconds = float(arguments.get("seconds", 30))
    try:
        snap = _network_inspector.snapshot(seconds=seconds)

        request_list = []
        for req in snap.requests[-50:]:  # Cap at 50 most recent
            request_list.append({
                "url": getattr(req, "url", ""),
                "method": getattr(req, "method", ""),
                "host": getattr(req, "host", ""),
                "path": getattr(req, "path", ""),
                "status_code": getattr(req, "status_code", None),
                "duration_ms": getattr(req, "duration_ms", None),
                "started_at": getattr(req, "started_at", None),
                "completed_at": getattr(req, "completed_at", None),
                "is_failed": getattr(req, "is_failed", False),
                "is_auth": getattr(req, "is_auth", False),
                "error": getattr(req, "error", None),
            })

        return {
            "requests": request_list,
            "request_count": len(request_list),
            "bytes_in": snap.bytes_in,
            "bytes_out": snap.bytes_out,
            "throughput_in_bps": snap.throughput_in,
            "throughput_out_bps": snap.throughput_out,
            "active_connections": snap.active_connections,
            "nettop_available": snap.nettop_available,
            "window_seconds": seconds,
        }
    except Exception as exc:
        return {"error": f"network snapshot failed: {exc}"}


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
        instructions="""SpecterQA iOS — AI-native iOS testing via MCP.

AVAILABLE TOOLS (27 total):

  Session lifecycle:
    ios_start_session    — Deploy XCTest runner; launch the app (required first step)
    ios_stop_session     — Stop runner and clean up (always call when done)

  Observation:
    ios_screenshot       — Annotated screenshot with numbered bounding boxes + element list
    ios_elements         — Element list only (faster than screenshot, no image)

  Interaction:
    ios_tap              — Tap by label (preferred) or element index
    ios_long_press       — Long-press by index (context menus, drag init)
    ios_type             — Type text into the focused field
    ios_press_key        — Press a named key: return, escape, delete, tab, space
    ios_swipe            — Swipe in a direction: up, down, left, right
    ios_swipe_back       — iOS edge swipe back navigation gesture

  Waiting:
    ios_wait             — Sleep for N seconds (animations, splash screens)
    ios_wait_for_element — Poll until a labelled element appears (async loads)
    ios_wait_idle        — Wait for app to become idle (element tree stabilizes)
    ios_app_state        — Check app lifecycle state (foreground/background/suspended)
    ios_dismiss_sheet    — Dismiss a sheet/modal by swiping down

  Recording & Replay:
    ios_start_recording  — Clear step buffer; begin clean recording
    ios_stop_recording   — Save replay YAML + clear buffer (end of flow)
    ios_save_replay      — Save replay YAML without clearing (keep recording)

  Quality & Diagnostics:
    ios_accessibility_audit — Audit for missing labels, small targets, duplicate labels
    ios_set_appearance      — Toggle dark/light mode on the simulator
    ios_simctl              — Run arbitrary xcrun simctl subcommand
    ios_webview_elements    — Query elements inside WKWebView (EPUB, PDF, audiobook UI)
    ios_logs                — Get recent app console logs (filterable by level, category, regex)
    ios_crashes             — Check for app crashes since session start (parses .ips files)

  Performance & Network Monitoring:
    ios_perf                — CPU %, RSS memory, thread count snapshot (call periodically for regression detection)
    ios_memory              — Detailed memory breakdown: footprint, dirty, swapped, clean pages
    ios_network             — Network activity: recent HTTP URLs, bytes in/out, throughput

WORKFLOW (follow this sequence):

1. START: ios_start_session(bundle_id="com.example.App")
   - Deploys the XCTest runner to the booted simulator
   - The app launches automatically
   - Recording begins immediately — every action is captured

2. OBSERVE: ios_screenshot() or ios_elements()
   - ios_screenshot returns an annotated image with numbered elements
   - ios_elements returns just the element list (faster, no image)
   - Use element index numbers with ios_tap

3. INTERACT: ios_tap(label="Save"), ios_swipe(direction="down"),
   ios_type(text="hello"), ios_press_key(key="return"), ios_swipe_back()
   - PREFER label-based tapping: ios_tap(label="Login Button") — more stable than indices
   - Use type= to narrow label matches: ios_tap(label="Cancel", type="Button")
   - Fall back to element_index only when no meaningful label exists
   - After each interaction, call ios_screenshot to verify the result

3b. WAIT: ios_wait(seconds=1.0) or ios_wait_for_element(label="Home")
   - Use ios_wait_for_element after navigations that load content asynchronously
   - Use ios_wait for fixed delays (animations, splash screens)

4. RECORD: ios_start_recording() / ios_stop_recording(name="...")
   - ios_start_recording() clears exploratory steps — call before the clean flow
   - ios_stop_recording(name="login-flow") saves AND clears (marks end of flow)
   - ios_save_replay(name="...") saves without clearing (keep recording)

5. SAVE: ios_save_replay(name="descriptive-name")
   - ALWAYS save a replay after a successful test flow
   - The replay runs in CI without AI: specterqa-ios replay <file>
   - Saves to .specterqa/replays/<name>.yaml
   - Checkpoints are captured automatically from the current element state

6. AUDIT: ios_accessibility_audit()
   - Run on each key screen to surface missing labels, small targets, duplicate labels
   - Results feed directly into an accessibility report

7. CLEANUP: ios_stop_session()
   - Always call this when testing is complete

RECORDING WORKFLOW (best practice):
  1. ios_start_session → exploratory taps to find the right flow
  2. ios_start_recording() → clears exploratory steps
  3. Execute the clean, successful flow (tap, type, etc.)
  4. ios_stop_recording(name="feature-name") → saves YAML + clears buffer
  5. Next flow: ios_start_recording() → repeat

TIPS:
- Take a screenshot BEFORE and AFTER every tap to verify the action worked
- If an element isn't visible, try ios_swipe(direction="down") to scroll
- Use ios_elements() for fast element checks without screenshots
- Use ios_wait_for_element(label="...") after navigations — never assume instant load
- Name replays descriptively: "settings-privacy-toggles" not "test1"
- One replay per user flow — keep them focused and short
- Run ios_accessibility_audit on the home screen and each major screen

PROVIDERS:
- Local simulator (default) — requires macOS + Xcode 15+
- BrowserStack (auto-detected) — set BROWSERSTACK_USERNAME + BROWSERSTACK_ACCESS_KEY
- CI replay — specterqa-ios ci .specterqa/replays/ --json-output results.json

WKWebView content:
Use ios_webview_elements to query elements inside WKWebView (EPUB readers,
PDF viewers, audiobook UI). XCTest's .webViews descendants chain exposes
labelled/identified web elements. For complex DOM nodes without accessibility
labels, a JavaScript bridge requires app-side instrumentation (out of scope).

SETUP CHECK:
  specterqa-ios doctor              — diagnose your environment
  specterqa-ios runner build ...    — build the XCTest runner (one-time)
  specterqa-ios init                — scaffold .specterqa/ for a new project
""",
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
        result = handle_start_session(
            {
                "bundle_id": bundle_id,
                "device_id": device_id,
                "app_path": app_path,
                "license_key": license_key or "",
                "clone": clone,
            }
        )
        return json.dumps(result)

    # ── Tool: ios_stop_session ─────────────────────────────────────────────

    @mcp.tool(
        name="ios_stop_session",
        description=("Stop the XCTest runner and clean up. Call this when testing is complete."),
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
            "Tap an element by IDENTIFIER (most reliable), LABEL, index number, or raw COORDINATES. "
            "Priority: identifier > label > element_index > coordinates. "
            "IDENTIFIER: use identifier='settingsButton' to tap the element with that accessibilityIdentifier (exact match). "
            "LABEL: use label='Save' for case-insensitive substring match. "
            "Optional type='Button' narrows label matching to a specific element type. "
            "FALLBACK: use element_index=N (integer from ios_screenshot) when no label or identifier is available. "
            "COORDINATES: use x=195, y=275 to tap at exact screen coordinates (use when element has no label or identifier). "
            "Call ios_screenshot first to populate the element cache (not needed for coordinate taps)."
        ),
    )
    async def ios_tap(
        element_index: int | None = None,
        label: str | None = None,
        type: str | None = None,
        identifier: str | None = None,
        x: float | None = None,
        y: float | None = None,
    ) -> str:
        result = handle_tap(
            {
                "element_index": element_index,
                "label": label,
                "type": type,
                "identifier": identifier,
                "x": x,
                "y": y,
            }
        )
        return json.dumps(result)

    # ── Tool: ios_wait ─────────────────────────────────────────────────────

    @mcp.tool(
        name="ios_wait",
        description=(
            "Wait (sleep) for a specified number of seconds. "
            "Use after interactions that trigger animations or async loading. "
            "seconds defaults to 1.0; capped at 30. "
            "For waiting until a specific element appears, use ios_wait_for_element instead."
        ),
    )
    async def ios_wait(seconds: float = 1.0) -> str:
        result = handle_wait({"seconds": seconds})
        return json.dumps(result)

    # ── Tool: ios_wait_for_element ─────────────────────────────────────────

    @mcp.tool(
        name="ios_wait_for_element",
        description=(
            "Poll the element tree until an element matching label appears, or timeout expires. "
            "label is a case-insensitive substring matched against element labels (required). "
            "timeout is the maximum wait in seconds (default 10, max 30). "
            "Returns {status: 'found', label, index} on success or {status: 'not_found'} on timeout. "
            "Use this instead of ios_wait when you need to wait for a specific UI element."
        ),
    )
    async def ios_wait_for_element(label: str, timeout: float = 10.0) -> str:
        result = handle_wait_for_element({"label": label, "timeout": timeout})
        return json.dumps(result)

    # ── Tool: ios_start_recording ──────────────────────────────────────────

    @mcp.tool(
        name="ios_start_recording",
        description=(
            "Clear the recorder's step buffer to start a fresh recording. "
            "Use this after exploratory taps to discard those steps and begin "
            "recording only the clean, successful test flow. "
            "The session continues — no restart needed."
        ),
    )
    async def ios_start_recording() -> str:
        result = handle_start_recording({})
        return json.dumps(result)

    # ── Tool: ios_stop_recording ───────────────────────────────────────────

    @mcp.tool(
        name="ios_stop_recording",
        description=(
            "Save the current recording as a replay YAML file AND clear the step buffer. "
            "Equivalent to ios_save_replay followed by clearing steps. "
            "name is the test name / filename stem (default 'replay'). "
            "path overrides the output location (default: .specterqa/replays/<name>.yaml). "
            "Use ios_save_replay if you want to keep recording after saving."
        ),
    )
    async def ios_stop_recording(name: str = "replay", path: str = "") -> str:
        result = handle_stop_recording({"name": name, "path": path or ""})
        return json.dumps(result)

    # ── Tool: ios_accessibility_audit ─────────────────────────────────────

    @mcp.tool(
        name="ios_accessibility_audit",
        description=(
            "Audit the current screen for common accessibility issues. "
            "Checks: missing labels on interactive elements, touch targets < 44x44 pt, "
            "and duplicate accessibility labels. "
            "Returns a list of issues with type, label, and context. "
            "Run after navigating to each key screen to build an accessibility report."
        ),
    )
    async def ios_accessibility_audit() -> str:
        result = handle_accessibility_audit({})
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

    # ── Tool: ios_webview_elements ─────────────────────────────────────────

    @mcp.tool(
        name="ios_webview_elements",
        description=(
            "Get elements inside WKWebView content (EPUB readers, PDF viewers, "
            "audiobook UI rendered in WKWebView). "
            "Use this for testing EPUB readers, PDF viewers, audiobook UI rendered "
            "in WKWebView. "
            "XCTest can see WKWebView descendants via the .webViews chain — this is "
            "the only way to interact with web content embedded in a native app. "
            "Returns a flat list of elements found inside all WKWebView instances "
            "currently on screen. "
            "Requires an active session (ios_start_session)."
        ),
    )
    async def ios_webview_elements() -> str:
        try:
            result = handle_webview_elements({})
        except RuntimeError as exc:
            result = {"error": str(exc)}
        return json.dumps(result)

    # ── Tool: ios_wait_idle ────────────────────────────────────────────────

    @mcp.tool(
        name="ios_wait_idle",
        description=(
            "Wait for the app to become idle (no pending UI changes). "
            "Monitors element tree stability. "
            "Use instead of ios_wait for navigation transitions, async loads, and animations. "
            "timeout defaults to 10s, max 30s."
        ),
    )
    async def ios_wait_idle(timeout: float = 10.0) -> str:
        result = handle_wait_idle({"timeout": timeout})
        return json.dumps(result)

    # ── Tool: ios_app_state ────────────────────────────────────────────────

    @mcp.tool(
        name="ios_app_state",
        description=(
            "Check app lifecycle state (foreground, background, suspended). "
            "Use to diagnose session issues or verify app is active before interactions."
        ),
    )
    async def ios_app_state() -> str:
        result = handle_app_state({})
        return json.dumps(result)

    # ── Tool: ios_dismiss_sheet ────────────────────────────────────────────

    @mcp.tool(
        name="ios_dismiss_sheet",
        description=(
            "Dismiss a presented sheet (half-sheet, action sheet, modal) by swiping down. "
            "Use when a sheet is blocking access to underlying content like the tab bar."
        ),
    )
    async def ios_dismiss_sheet() -> str:
        result = handle_dismiss_sheet({})
        return json.dumps(result)

    # ── Tool: ios_logs ─────────────────────────────────────────────────────

    @mcp.tool(
        name="ios_logs",
        description=(
            "Get recent app console logs from the iOS Simulator. "
            "Returns structured log entries with timestamp, level, subsystem, category, and message. "
            "Use seconds=N to control the time window (default 30s). "
            "Use level='error' to filter to errors and faults only (queries the dedicated error buffer). "
            "Use pattern='regex' to search log messages by regex (overrides level and seconds). "
            "Use category='subsystem.category' to filter by log category. "
            "Returns at most 100 entries plus an aggregate summary. "
            "Requires an active session (ios_start_session)."
        ),
    )
    async def ios_logs(
        seconds: float = 30.0,
        level: str | None = None,
        pattern: str | None = None,
        category: str | None = None,
    ) -> str:
        result = handle_logs({
            "seconds": seconds,
            "level": level,
            "pattern": pattern,
            "category": category,
        })
        return json.dumps(result, default=str)

    # ── Tool: ios_crashes ──────────────────────────────────────────────────

    @mcp.tool(
        name="ios_crashes",
        description=(
            "Check for app crashes since the session started. "
            "Returns crash reports with exception type, exception code, crashing thread, "
            "backtrace, app version, and OS version. "
            "Also reports whether the app process is currently running. "
            "Use after unexpected behavior or blank screens to diagnose if the app crashed. "
            "Parses .ips crash files from ~/Library/Logs/DiagnosticReports/ — "
            "only reports crashes that appeared after ios_start_session was called. "
            "Requires an active session (ios_start_session)."
        ),
    )
    async def ios_crashes() -> str:
        result = handle_crashes({})
        return json.dumps(result, default=str)

    # ── Tool: ios_perf ─────────────────────────────────────────────────────

    @mcp.tool(
        name="ios_perf",
        description=(
            "Get real-time CPU usage, memory footprint (RSS), and thread count for the app "
            "under test. "
            "Use to detect performance regressions, memory leaks, and thread explosion. "
            "Call before and after test flows to capture baseline and post-action metrics. "
            "Returns cpu_percent, memory_mb (resident set size), thread_count, and the PID. "
            "Requires an active session (ios_start_session)."
        ),
    )
    async def ios_perf() -> str:
        result = handle_perf({})
        return json.dumps(result, default=str)

    # ── Tool: ios_memory ───────────────────────────────────────────────────

    @mcp.tool(
        name="ios_memory",
        description=(
            "Get a detailed memory breakdown for the app under test via the macOS footprint tool. "
            "Reports: physical memory footprint, dirty pages, swapped/compressed pages, clean pages — all in MB. "
            "More detailed than ios_perf memory (RSS). "
            "Use to diagnose memory leaks (growing dirty_mb), excessive caching (high clean_mb), "
            "or memory pressure (non-zero swapped_mb). "
            "Requires an active session (ios_start_session). "
            "Falls back gracefully if footprint is unavailable."
        ),
    )
    async def ios_memory() -> str:
        result = handle_memory({})
        return json.dumps(result, default=str)

    # ── Tool: ios_network ──────────────────────────────────────────────────

    @mcp.tool(
        name="ios_network",
        description=(
            "Get network activity for the app under test. "
            "Returns recent HTTP requests captured from CFNetwork / URLSession os_log entries: "
            "URL, method, status code, host, whether the request failed, whether it is auth-related. "
            "Also reports cumulative bytes in/out and real-time throughput (bytes/sec) when "
            "nettop is available. "
            "Use seconds=N to control the time window (default 30s). "
            "Use to verify API calls fire correctly, detect failed requests (4xx/5xx), "
            "and measure network performance during test flows. "
            "Requires an active session (ios_start_session)."
        ),
    )
    async def ios_network(seconds: float = 30.0) -> str:
        result = handle_network({"seconds": seconds})
        return json.dumps(result, default=str)

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
