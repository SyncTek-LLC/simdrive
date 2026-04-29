"""SpecterQA iOS MCP Server — Native primitives for Claude Code.

Claude Code IS the reasoning engine. This server exposes direct
simulator control primitives — no Claude API calls, no SoM pipeline,
no orchestration loops. Claude sees annotated screenshots and decides
what to do.

Usage:
    specterqa-ios-mcp            # stdio transport (console_scripts entry point)
    python -m specterqa.ios.mcp  # alternative invocation
    specterqa ios serve          # via CLI serve command

Tools (35 total — v16.0.0a1 vision-first surface):
    ios_start_session       Start session on the iOS Simulator (AX or XCTest backend)
    ios_stop_session        Stop the XCTest runner and clean up
    ios_observe             Vision-first observation: screenshot + reliable_targets
    ios_act                 Unified action verb: tap/type/swipe/key/scroll/long_press/drag
    ios_app_state           Check app lifecycle state (foreground/background/suspended)
    ios_dismiss_sheet       Dismiss a sheet/modal by swiping down
    ios_set_appearance      Toggle dark/light mode on the simulator
    ios_simctl              Run arbitrary simctl subcommand on the simulator
    ios_webview_elements    Get elements inside WKWebView content (EPUB readers, PDF viewers)
    ios_start_recording     Clear step buffer; begin clean recording
    ios_stop_recording      Save replay YAML + clear buffer (marks end of flow)
    ios_accessibility_audit Audit current screen for accessibility issues
    ios_logs                Get recent app console logs from the iOS Simulator
    ios_crashes             Check for app crashes since session start
    ios_perf                CPU, memory (RSS), and thread count snapshot
    ios_memory              Detailed memory breakdown via footprint tool
    ios_network             Network activity: URLs, bytes in/out, throughput
    ios_perf_baseline       Capture a perf snapshot as a reference baseline
    ios_perf_compare        Compare current perf to the stored baseline (deltas + severity)

v16.0.0a1 deletes the v15.x AX-tree selector layer (ios_screenshot,
ios_tap, ios_elements, ios_long_press, ios_swipe, ios_swipe_back,
ios_type, ios_press_key, ios_dismiss_keyboard, ios_wait,
ios_wait_for_element, ios_wait_idle, ios_capture_state,
ios_action_with_logs) — replaced by ios_observe + ios_act.

INIT-2026-500 — SpecterQA iOS Headless Driver.
"""

from __future__ import annotations

import base64
import datetime
import io
import json
import logging
import os
import subprocess

# INIT-2026-525: Tier-based access control for MCP tools.
# Import is deferred-safe — tier_gate only imports from the standard library
# and lazily imports LicenseValidator on first tool call.
from specterqa.ios.mcp.tier_gate import require_tier  # noqa: E402
import threading
import time
from pathlib import Path
from typing import Any

from PIL import Image

logger = logging.getLogger("specterqa.ios.mcp")

# ---------------------------------------------------------------------------
# Global session state — one active session at a time
# ---------------------------------------------------------------------------

_session = None  # TestSession or RunnerProcess instance (single active session)
_mcp_runner_ref = None  # RunnerProcess pre-deployed in handle_start_session (xctest path)
_session_udid: str | None = None  # Resolved UDID for the active session (not "booted")
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
_perf_baseline: dict | None = None  # Stored perf baseline for ios_perf_compare
_ax_http_server = None  # AXHTTPServer instance (None when AX backend is not active)

# ---------------------------------------------------------------------------
# Async deploy state — used by wait=False path (Issue 2)
# ---------------------------------------------------------------------------

import uuid as _uuid  # noqa: E402

_async_deploy_state: dict = {
    "status": "idle",      # idle | deploying | healthy | failed
    "deploy_id": None,
    "started_at": None,
    "udid": None,
    "error": None,
}
_async_deploy_lock = threading.Lock()

# Circuit-breaker for session health — replaces the per-call health() probe.
# After 3 consecutive ConnectionError failures the breaker opens and
# _require_session() raises RuntimeError immediately without hitting the runner.
from specterqa.ios.backends.retry_policy import RetryPolicy, SessionCrashedError  # noqa: E402

_circuit_breaker = RetryPolicy(
    max_retries=2,
    base_backoff_s=0.3,
    circuit_breaker_threshold=3,
).stateful()


# ---------------------------------------------------------------------------
# Issue 2: Async deploy helpers
# ---------------------------------------------------------------------------


def _get_deploy_state() -> dict:
    """Return current async deploy state snapshot (thread-safe copy)."""
    with _async_deploy_lock:
        state = dict(_async_deploy_state)
        started = state.get("started_at")
        state["elapsed_ms"] = int((time.monotonic() - started) * 1000) if started else 0
        state["udid"] = state.get("udid")
    return state


def _set_deploy_state(status: str, deploy_id: str | None = None, udid: str | None = None, error: str | None = None) -> None:
    """Update async deploy state (thread-safe)."""
    with _async_deploy_lock:
        _async_deploy_state["status"] = status
        if deploy_id is not None:
            _async_deploy_state["deploy_id"] = deploy_id
        if udid is not None:
            _async_deploy_state["udid"] = udid
        if error is not None:
            _async_deploy_state["error"] = error
        if status == "deploying":
            _async_deploy_state["started_at"] = time.monotonic()
            _async_deploy_state["error"] = None
        elif status == "idle":
            _async_deploy_state["started_at"] = None
            _async_deploy_state["deploy_id"] = None
            _async_deploy_state["udid"] = None
            _async_deploy_state["error"] = None


def _background_deploy(arguments: dict) -> None:
    """Thread target: run handle_start_session and update deploy state."""
    deploy_id = arguments.get("_deploy_id", "")
    try:
        # Run with wait=True to do the real blocking deploy
        args_copy = {k: v for k, v in arguments.items() if k not in ("wait", "_deploy_id")}
        result = handle_start_session(args_copy)
        if "error" in result:
            _set_deploy_state("failed", error=result["error"])
        else:
            _set_deploy_state("healthy")
    except Exception as exc:  # noqa: BLE001
        _set_deploy_state("failed", error=str(exc))


# ---------------------------------------------------------------------------
# Issue 5: Stale xcodebuild reaper helpers
# ---------------------------------------------------------------------------


def _reap_orphan_xcodebuild(port: int = 8222) -> None:
    """Kill any xcodebuild processes holding the specified port.

    Uses `lsof -i :<port>` to find PIDs. Sends SIGTERM then (after 5s) SIGKILL.
    Silently ignores errors (lsof absent, permissions, process already gone).
    """
    import os
    import signal as _signal

    try:
        result = subprocess.run(
            ["lsof", "-i", f":{port}", "-t"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode != 0 or not result.stdout.strip():
            return

        pids = []
        for line in result.stdout.splitlines():
            line = line.strip()
            if line.isdigit():
                pids.append(int(line))

        for pid in pids:
            try:
                # Check it's actually xcodebuild (don't kill arbitrary processes)
                try:
                    proc_check = subprocess.run(
                        ["ps", "-p", str(pid), "-o", "comm="],
                        capture_output=True, text=True, timeout=2,
                    )
                    comm = proc_check.stdout.strip()
                    if "xcodebuild" not in comm and "xctest" not in comm.lower():
                        logger.debug("_reap_orphan: pid %d (%s) not xcodebuild — skipping", pid, comm)
                        continue
                except Exception:  # noqa: BLE001
                    pass  # If we can't check, proceed cautiously

                logger.info("_reap_orphan: sending SIGTERM to pid %d on port %d", pid, port)
                os.kill(pid, _signal.SIGTERM)
                time.sleep(5)
                try:
                    os.kill(pid, 0)  # check still alive
                    os.kill(pid, _signal.SIGKILL)
                    logger.info("_reap_orphan: sent SIGKILL to pid %d (still alive after TERM)", pid)
                except ProcessLookupError:
                    pass  # already gone — good
            except (ProcessLookupError, PermissionError):
                pass
            except Exception as exc:  # noqa: BLE001
                logger.debug("_reap_orphan: error killing pid %d: %s", pid, exc)
    except FileNotFoundError:
        logger.debug("_reap_orphan: lsof not found, skipping orphan check")
    except Exception as exc:  # noqa: BLE001
        logger.debug("_reap_orphan: unexpected error: %s", exc)


def _kill_runner_graceful(process: Any, grace_s: float = 5.0) -> None:
    """Send SIGTERM to a process, wait grace_s, then SIGKILL if still alive.

    Args:
        process: subprocess.Popen instance (must have .pid and .poll()).
        grace_s: Seconds to wait between TERM and KILL.
    """
    import os
    import signal as _signal

    if process is None:
        return
    pid = getattr(process, "pid", None)
    if not pid:
        return
    try:
        os.kill(pid, _signal.SIGTERM)
        time.sleep(grace_s)
        if process.poll() is None:
            # Still alive after TERM — escalate to KILL
            try:
                os.kill(pid, _signal.SIGKILL)
            except ProcessLookupError:
                pass
    except ProcessLookupError:
        pass  # already gone
    except Exception as exc:  # noqa: BLE001
        logger.debug("_kill_runner_graceful: error for pid %d: %s", pid, exc)


# ---------------------------------------------------------------------------
# Issue 8: Sim state detection helper
# ---------------------------------------------------------------------------


def _check_sim_state_for_udid(udid: str) -> str:
    """Return the current state of a simulator ('Booted', 'Shutdown', etc.).

    Uses `xcrun simctl list devices --json`. Returns 'Unknown' if the UDID
    is not found or the command fails.
    """
    try:
        result = subprocess.run(
            ["xcrun", "simctl", "list", "devices", "--json"],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode == 0:
            import json as _json
            data = _json.loads(result.stdout)
            for _rt_devices in data.get("devices", {}).values():
                for _d in _rt_devices:
                    if _d.get("udid") == udid:
                        return _d.get("state", "Unknown")
    except Exception as exc:  # noqa: BLE001
        logger.debug("_check_sim_state_for_udid(%s) failed: %s", udid, exc)
    return "Unknown"


def _verify_sim_alive(udid: str, poll_budget_s: float = 15.0) -> tuple[bool, str]:
    """Check if the XCTest runner / simulator is alive mid-session.

    Strategy: probe the runner HTTP health endpoint rather than simctl list.
    When xcodebuild test-without-building owns the sim, simctl list incorrectly
    reports "Shutdown" even while tests are running — so simctl is NOT used here.

    If the runner is reachable → (True, "Booted").
    If the runner is unreachable → poll with ~1s sleeps for up to poll_budget_s
    total, giving SpringBoard time to respawn (typical 5-10s). Only after the
    budget is exhausted without a healthy runner AND simctl confirms Shutdown is
    the session declared dead.

    Returns:
        (True, state)  — sim/runner alive; proceed normally.
        (False, state) — runner unreachable AND simctl confirms Shutdown after
                         the full poll budget has been consumed.
    """
    # First try the runner health endpoint (fastest, most accurate).
    if _backend is not None:
        try:
            result = _backend.health()
            if isinstance(result, dict) and result.get("status") == "ok":
                return (True, "Booted")
        except Exception:
            pass  # Runner unreachable — fall through to poll loop

    # Runner is unreachable. Poll for up to poll_budget_s before declaring dead.
    _dead_states = {"Shutdown", "ShuttingDown"}
    _t0 = time.monotonic()
    _last_state = "Unknown"
    while True:
        _last_state = _check_sim_state_for_udid(udid)
        if _last_state not in _dead_states:
            # Not a dead state yet — treat as alive (Booting / recovering).
            return (True, _last_state)

        # Sim reports dead. Try runner health one more time before giving up.
        if _backend is not None:
            try:
                result = _backend.health()
                if isinstance(result, dict) and result.get("status") == "ok":
                    return (True, "Booted")
            except Exception:
                pass

        elapsed = time.monotonic() - _t0
        if elapsed >= poll_budget_s:
            # Budget exhausted — sim is genuinely dead.
            return (False, _last_state)

        # Sleep 1s and retry.
        time.sleep(1.0)


_SIM_SHUTDOWN_RESPONSE = {
    "error": "sim_shutdown_during_session",
    "action_needed": "boot_and_reauth",
    "retryable": True,
}


def _sim_shutdown_error(state: str) -> dict:
    return {**_SIM_SHUTDOWN_RESPONSE, "sim_state": state}


def _sim_settle_wait(udid: str, settle_timeout_s: float = 10.0) -> float:
    """Smart sim settle: wait only if the sim just booted recently.

    Calls `xcrun simctl list devices --json` and reads the device's lastBootedAt
    (or bootTime) field. If the sim booted less than settle_timeout_s ago, sleeps
    the remaining delta. Otherwise returns immediately with 0 wait.

    Accepts ``udid="booted"`` — resolves to the first device in Booted state
    inside the same simctl listing call (no extra subprocess).  Without this,
    callers using the ``"booted"`` shorthand silently skip settle.

    Returns the actual seconds waited (0 if no wait was needed).
    """
    if settle_timeout_s <= 0:
        return 0.0

    try:
        result = subprocess.run(
            ["xcrun", "simctl", "list", "devices", "--json"],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode != 0:
            return 0.0

        data = json.loads(result.stdout)
        boot_time_str: str | None = None
        # When caller passes "booted", resolve to the first Booted device — this
        # makes settle work for the convenience-string path that previously
        # bypassed it at the call site.
        match_state_only = udid == "booted"
        for _rt_devs in data.get("devices", {}).values():
            for _d in _rt_devs:
                if match_state_only:
                    if _d.get("state") == "Booted":
                        boot_time_str = _d.get("lastBootedAt") or _d.get("bootTime")
                        break
                elif _d.get("udid") == udid:
                    # CoreSimulator ≥ iOS 16 exposes "lastBootedAt" (ISO 8601)
                    boot_time_str = _d.get("lastBootedAt") or _d.get("bootTime")
                    break
            if boot_time_str:
                break

        if not boot_time_str:
            return 0.0

        # Parse ISO 8601 with timezone offset
        try:
            boot_dt = datetime.datetime.fromisoformat(boot_time_str.replace("Z", "+00:00"))
        except ValueError:
            return 0.0

        now_dt = datetime.datetime.now(datetime.timezone.utc)
        age_s = (now_dt - boot_dt).total_seconds()
        if age_s < 0:
            age_s = 0.0

        if age_s >= settle_timeout_s:
            # Sim has been booted long enough — no wait needed.
            logger.debug("_sim_settle_wait: sim %s booted %.1fs ago — skipping settle", udid, age_s)
            return 0.0

        wait_s = settle_timeout_s - age_s
        logger.info(
            "_sim_settle_wait: sim %s booted %.1fs ago (< %.1fs settle_timeout) — waiting %.1fs",
            udid, age_s, settle_timeout_s, wait_s,
        )
        time.sleep(wait_s)
        return wait_s

    except Exception as exc:  # noqa: BLE001
        logger.debug("_sim_settle_wait: could not determine boot time for %s: %s", udid, exc)
        return 0.0


# ---------------------------------------------------------------------------
# Retryable error patterns — Apple-side transients that callers may retry
# ---------------------------------------------------------------------------

# Patterns in error strings that indicate Apple-side transient failures.
# IMPORTANT: Keep patterns as specific as possible.
#   "unable to lookup" was rejected because it also matches bad-UDID errors
#   (simctl emits "unable to lookup udid" for unknown device IDs) — those are
#   fatal, not transient.  "CoreSimulator 405" is the specific retriable form.
#   "runner not become healthy" was removed — grammatically broken, never matches;
#   the actual emitted string is "Runner did not become healthy within" (above).
_RETRYABLE_ERROR_PATTERNS = (
    "sim_shutdown_during_session",
    "installcoordinationd",
    "Runner did not become healthy within",
    "CoreSimulator 405",
    "exited with code 65",
    "exited with code 70",
    "Connection refused",
    "ConnectionRefusedError",
)


def _is_retryable_error(error_str: str) -> bool:
    """Return True if the error string matches a known Apple-side transient."""
    err_lower = error_str.lower()
    for pattern in _RETRYABLE_ERROR_PATTERNS:
        if pattern.lower() in err_lower:
            return True
    return False


def _tag_retryable(result: dict) -> dict:
    """If result is an error dict, add retryable=True when the error is a known transient."""
    if isinstance(result, dict) and "error" in result:
        err = str(result.get("error", ""))
        if _is_retryable_error(err) or result.get("retryable"):
            result = {**result, "retryable": True}
    return result


def _retry_once_on_transient(handler_fn, arguments: dict, *, sleep_s: float = 2.0) -> dict:
    """Call handler_fn(arguments). On a transient error, sleep sleep_s and retry once.

    Only the second failure is returned to the caller. If the first call succeeds
    or returns a non-retryable error, it is returned immediately with no retry.
    """
    result = handler_fn(arguments)
    if isinstance(result, dict) and result.get("error") and _is_retryable_error(str(result["error"])):
        logger.info(
            "_retry_once_on_transient: detected transient '%s' — sleeping %.1fs before retry",
            result["error"],
            sleep_s,
        )
        time.sleep(sleep_s)
        result = handler_fn(arguments)
        result = _tag_retryable(result)
    return result


def _require_session() -> None:
    """Raise RuntimeError if no active session exists or if the circuit breaker is open.

    The per-call health() probe has been replaced by a circuit-breaker pattern:
    after 3 consecutive ConnectionError failures (recorded by backend call sites)
    the breaker opens and all subsequent tool calls fail fast with a clear message.

    This eliminates the extra round-trip to the runner on every tool invocation.
    """
    global _session_state
    if _session_state == "crashed":
        raise RuntimeError(
            "Session crashed. Call ios_stop_session then ios_start_session to recover."
        )
    if _backend is None:
        raise RuntimeError("No active session. Call ios_start_session first.")
    if _circuit_breaker.is_open():
        _session_state = "crashed"
        raise RuntimeError(
            "Session unreachable (circuit breaker open — 3 consecutive failures). "
            "Call ios_stop_session then ios_start_session to recover."
        )


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
    # v2 runner wraps image data under result["result"]["data"].
    # v1 fallback returns it at the top level under "base64", "data", or "image".
    nested = result.get("result") if isinstance(result.get("result"), dict) else {}
    b64 = (
        nested.get("data")
        or result.get("base64")
        or result.get("data")
        or result.get("image", "")
    )
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
    """Start the iOS backend on the booted simulator (or BrowserStack).

    Backend selection is now handled exclusively by
    :meth:`BackendSelector.choose` — this function no longer contains inline
    AX/XCTest/BrowserStack decision logic.

    Args:
        bundle_id:   Bundle ID of the app under test (required).
        device_id:   Simulator UDID or "booted" (default).
        app_path:    Path to a .app bundle to install before starting (optional).
        license_key: SpecterQA license key (optional — falls back to
                     ``SPECTERQA_IOS_LICENSE`` env var; omit for trial mode).
        device_type: "simulator" (default) | "physical" (experimental opt-in).
                     When "physical", SPECTERQA_ALLOW_PHYSICAL_DEVICE=1 env var must be
                     set or the call returns an opt-in error immediately.
        backend:     Backend name override: "auto" (default), "ax", "xctest",
                     or "browserstack".  "auto" lets BackendSelector decide.

    Returns:
        {"status": "ok", "backend": "ax"|"xctest"|"browserstack", ...}
        or {"error": "<message>"} on failure.
    """
    global _session, _mcp_runner_ref, _session_udid, _backend, _annotator, _last_elements, _recorder, _session_state, _console_monitor, _crash_detector, _perf_profiler, _network_inspector, _ax_http_server

    with _session_lock:
        # License check — validates key or allows trial/founder bypass.
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
        device_type = arguments.get("device_type", "simulator")
        sim_settle_timeout: float = float(arguments.get("sim_settle_timeout", 10.0))
        if isinstance(device_type, str):
            device_type = device_type.strip().lower()

        # Physical device opt-in gate.
        if device_type == "physical":
            from specterqa.ios.config import _check_physical_opt_in  # noqa: PLC0415
            _opt_in = _check_physical_opt_in()
            if not _opt_in["allowed"]:
                return {
                    "error": (
                        "Physical device support is experimental and requires opt-in. "
                        "Set SPECTERQA_ALLOW_PHYSICAL_DEVICE=1 OR run "
                        "'specterqa-ios mcp enable-physical' to enable. "
                        "Known xcodebuild issues on iOS 26 may cause instability — see RELEASES.md."
                    ),
                    "diagnostics": _opt_in["diagnostics"],
                }

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
                try:
                    from specterqa.ios.runner_source import RUNNER_SOURCE_DIR, BUILD_SCRIPT

                    runner_dir = RUNNER_SOURCE_DIR
                    build_sh = BUILD_SCRIPT
                except ImportError:
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

        # --- Backend selection via BackendSelector.choose() ---------------
        # Normalise the caller's "backend" argument:
        #   "auto" / missing   → None  (BackendSelector decides)
        #   "xctest"           → "xctest"
        #   "ax"               → "ax"
        #   "browserstack"/"bs" → "browserstack"
        backend_arg = str(arguments.get("backend", "auto")).lower()
        if backend_arg == "auto":
            backend_arg = None  # type: ignore[assignment]

        # Env-var override: SPECTERQA_PROVIDER=browserstack forces BS.
        env_provider = os.environ.get("SPECTERQA_PROVIDER", "").lower()
        if env_provider in ("browserstack", "bs"):
            backend_arg = "browserstack"
        elif env_provider == "local" and backend_arg == "browserstack":
            backend_arg = None  # type: ignore[assignment]

        # v14: Deploy the XCTest runner via RunnerProcess (single owner of runner lifecycle).
        # Replaces the B9 inline deploy path — all xcodebuild management is now in RunnerProcess.
        # We deploy here (before BackendSelector.choose()) so that BackendSelector
        # can probe :8222/health and select XCTestBackend when backend="auto".
        _should_deploy_xctest = backend_arg in ("xctest", None)
        if _should_deploy_xctest:
            try:
                from specterqa.ios.runner_process import RunnerProcess, RunnerDeployError  # noqa: PLC0415

                # Kill any orphaned xcodebuild processes from previous sessions
                # before acquiring a new runner. Mirrors what TestSession._start()
                # does; required here because we bypass TestSession for the
                # direct-runner path.
                try:
                    from specterqa.ios.session_manager import TestSession as _TS  # noqa: PLC0415
                    _TS._kill_stale_runners()
                except Exception as _ks_exc:
                    logger.debug("_kill_stale_runners skipped: %s", _ks_exc)

                # After killing stale runners, the simulator may have shut down
                # (xcodebuild's SIGKILL can trigger sim teardown). Re-boot it
                # if needed so the new runner deploys to a live sim.
                if device_id not in ("booted", ""):
                    try:
                        _sim_list = subprocess.run(
                            ["xcrun", "simctl", "list", "devices", "booted", "-j"],
                            capture_output=True, text=True, timeout=5,
                        )
                        if _sim_list.returncode == 0:
                            import json as _json_boot
                            _sim_data_boot = _json_boot.loads(_sim_list.stdout)
                            _booted_udids = [
                                _d.get("udid", "")
                                for _rt in _sim_data_boot.get("devices", {}).values()
                                for _d in _rt
                                if _d.get("state") == "Booted"
                            ]
                            if device_id not in _booted_udids:
                                logger.info("Simulator %s not booted after stale-runner cleanup — rebooting", device_id)
                                subprocess.run(
                                    ["xcrun", "simctl", "boot", device_id],
                                    capture_output=True, text=True, timeout=30,
                                )
                                time.sleep(3)
                    except Exception as _boot_exc:
                        logger.debug("Sim boot-check after cleanup failed: %s", _boot_exc)

                _deploy_port = 8222

                # v16.0.0a3 P0-1 (Maurice/Example Reader dogfood): ensure the sim is
                # booted before deploy.  Previously _ensure_sim_booted was wired
                # into handle_app_relaunch but NOT into handle_start_session —
                # so a shutdown sim would hand the deploy a guaranteed-fail
                # situation.
                if device_id and device_id != "booted" and device_type == "simulator":
                    try:
                        if not _ensure_sim_booted(device_id):
                            return {
                                "error": "sim_boot_failed",
                                "message": (
                                    f"Simulator {device_id} could not be booted "
                                    "before runner deploy. Check the device exists "
                                    "(xcrun simctl list devices) and try again."
                                ),
                                "udid": device_id,
                                "retryable": True,
                            }
                    except NameError:
                        # _ensure_sim_booted lives lower in the file; if we hit
                        # this during refactor, fall through (the legacy path
                        # below also handles partial sim states).
                        pass

                _mcp_runner = RunnerProcess.acquire(device_id, _deploy_port)
                _mcp_runner.deploy(bundle_id or "")

                # Wait for the runner to become healthy (up to 90s).
                # Cold runner boot on an idle simulator takes 35–50s.
                #
                # v16.0.0a3 P0-1: previously a healthcheck timeout was
                # WARNING-logged and we silently fell through to BackendSelector,
                # which would then return status:ok with a dead runner_url.
                # Maurice's Example Reader dogfood: agent-side this read as success and
                # subsequent ios_observe calls returned cached/empty data with
                # no obvious error.  v16.0.0a3 returns a structured error
                # immediately so the caller knows the deploy failed.
                try:
                    _mcp_runner.healthcheck(timeout_s=90.0)
                    logger.info("v14: MCP runner deployed and healthy on :%d", _deploy_port)
                    _mcp_runner_ref = _mcp_runner  # noqa: PLW0603
                except RunnerDeployError as health_exc:
                    logger.error(
                        "v14: MCP runner did not become healthy within 90s: %s",
                        health_exc,
                    )
                    _set_deploy_state(
                        "failed",
                        udid=device_id,
                        error=str(health_exc),
                    )
                    return {
                        "error": "runner_deploy_health_timeout",
                        "message": (
                            "The XCTest runner did not respond to /health within "
                            "90 seconds of deploy. The runner test process likely "
                            "exited early (common cause on iOS 26.0: SDK mismatch "
                            "between the .xctestrun file's iphonesimulator26.X "
                            "target and the booted sim's runtime; rebuild the "
                            "runner: `specterqa-ios runner clean --yes && "
                            "specterqa-ios runner build`)."
                        ),
                        "udid": device_id,
                        "port": _deploy_port,
                        "retryable": False,
                        "underlying_error": str(health_exc),
                    }

            except RunnerDeployError as deploy_exc:
                # Loud error — no silent fallback to AX
                return {"error": str(deploy_exc)}
            except Exception as deploy_exc:
                logger.warning("v14: MCP runner deploy failed: %s", deploy_exc)

        from specterqa.ios.backends.selector import BackendSelector
        from specterqa.ios.backends.xctest_client import XCTestBackend  # noqa: PLC0415
        from specterqa.ios.som_annotator import SoMAnnotator

        # v15.1.1 dogfood Issue #1: when xctest is explicitly requested AND the
        # deploy block above already ran a successful healthcheck + stability probe,
        # skip the BackendSelector re-probe.  The re-probe was racing the iOS 26.x
        # XCTest watchdog kill — runner could be alive at healthcheck() return,
        # dead by BackendSelector probe, producing a misleading "is not available"
        # error after a successful deploy.  If we got past the stability probe
        # above, the runner is live; trust that signal and instantiate directly.
        if backend_arg == "xctest" and _mcp_runner_ref is not None:
            chosen = XCTestBackend(udid=device_id)
            logger.info(
                "v15.1.1: bypassing BackendSelector probe — runner deployed "
                "+ stability-confirmed above"
            )
        else:
            try:
                chosen = BackendSelector(udid=device_id).choose(
                    device_udid=device_id,
                    requested=backend_arg,
                )
            except (RuntimeError, ValueError) as exc:
                return {"error": str(exc)}

        backend_name = type(chosen).__name__

        # Reset the circuit breaker for the new session.
        _circuit_breaker.reset()

        # --- AX backend path -------------------------------------------
        if backend_name == "AXBackend":
            try:
                from specterqa.ios.backends.ax_backend import AXAnnotator, AXHTTPServer  # noqa: PLC0415
                from specterqa.ios.replay import ReplayRecorder  # noqa: PLC0415
                from specterqa.ios.drivers.simulator.console import ConsoleMonitor  # noqa: PLC0415
                from specterqa.ios.drivers.simulator.crash import CrashDetector  # noqa: PLC0415
                from specterqa.ios.drivers.simulator.perf import PerfProfiler  # noqa: PLC0415
                from specterqa.ios.drivers.simulator.network import NetworkInspector  # noqa: PLC0415

                if app_path:
                    subprocess.run(["xcrun", "simctl", "install", device_id, app_path], check=True)
                    subprocess.run(["xcrun", "simctl", "launch", device_id, bundle_id], check=True)

                _backend = chosen
                _annotator = AXAnnotator(chosen)
                _last_elements = []
                _recorder = ReplayRecorder(bundle_id=bundle_id, device_id=device_id)

                _console_monitor = ConsoleMonitor(device_id=device_id)
                _console_monitor.start()
                _crash_detector = CrashDetector(device_id=device_id, bundle_id=bundle_id)
                _crash_detector.start()
                _perf_profiler = PerfProfiler(device_id=device_id, bundle_id=bundle_id)
                _network_inspector = NetworkInspector(device_id=device_id)
                _network_inspector.start()
                _network_inspector.setup_log_watcher(_console_monitor)

                _ax_http_server = AXHTTPServer(chosen, port=8222)
                _ax_http_server.start()

                # Resolve frontmost simulator UDID for multi-sim disambiguation.
                frontmost_udid: str = device_id
                try:
                    _simlist = subprocess.run(
                        ["xcrun", "simctl", "list", "devices", "booted", "-j"],
                        capture_output=True, text=True, timeout=5,
                    )
                    if _simlist.returncode == 0:
                        import json as _json
                        _sim_data = _json.loads(_simlist.stdout)
                        _booted: list[str] = []
                        for _runtime_devs in _sim_data.get("devices", {}).values():
                            for _dev in _runtime_devs:
                                if _dev.get("state") == "Booted":
                                    _booted.append(_dev.get("udid", ""))
                        if len(_booted) == 1:
                            frontmost_udid = _booted[0]
                        elif len(_booted) > 1:
                            logger.warning(
                                "Multiple simulators booted (%s). AX backend reads frontmost only.",
                                _booted,
                            )
                            frontmost_udid = _booted[0]
                except Exception:  # noqa: BLE001
                    pass

                # Warm up: AX hydration race guard.
                _warmup_deadline = time.monotonic() + 2.0
                while time.monotonic() < _warmup_deadline:
                    try:
                        _probe = chosen.get_elements(limit=5)
                        if len(_probe) > 0:
                            break
                    except Exception:  # noqa: BLE001
                        pass
                    time.sleep(0.2)

                _session_state = "running"
                return {
                    "status": "ok",
                    "backend": "ax",
                    "device_type": device_type,
                    "target_udid": device_id,
                    "frontmost_udid": frontmost_udid,
                    "sim_pid": chosen._sim_pid,
                    "device_w": chosen._device_w,
                    "device_h": chosen._device_h,
                }
            except Exception as exc:
                _backend = None
                _annotator = None
                _last_elements = []
                _recorder = None
                _console_monitor = None
                _crash_detector = None
                _perf_profiler = None
                _network_inspector = None
                _session_state = "idle"
                return {"error": f"AX backend failed: {exc}"}

        # --- BrowserStack path -----------------------------------------
        if backend_name == "BrowserStackBackend":
            try:
                if app_path:
                    chosen.upload_app(app_path)
                session_id = chosen.start_session(bundle_id)
                _backend = chosen
                _annotator = SoMAnnotator()
                _last_elements = []

                from specterqa.ios.replay import ReplayRecorder

                _recorder = ReplayRecorder(bundle_id=bundle_id, device_id=device_id)
                _session_state = "running"
                return {
                    "status": "ok",
                    "provider": "browserstack",
                    "session_id": session_id,
                    "device": chosen.device,
                    "os_version": chosen.os_version,
                }
            except Exception as exc:
                _backend = None
                _annotator = None
                _last_elements = []
                _recorder = None
                _session_state = "idle"
                return {"error": str(exc)}

        # --- XCTest path (default) -------------------------------------
        from specterqa.ios.session_manager import TestSession
        from specterqa.ios.backends.xctest_client import XCTestBackend

        try:
            clone = arguments.get("clone", False)

            # sim_settle_timeout: smart wait when sim just booted.
            # _sim_settle_wait now resolves "booted" → first Booted device internally,
            # so the convenience-string path no longer silently bypasses settle.
            if device_type == "simulator" and device_id:
                _sim_settle_wait(device_id, sim_settle_timeout)

            # v14.0.2 fix: When _mcp_runner_ref is already deployed and healthy
            # (non-clone, simulator path), reuse it directly instead of creating
            # a new TestSession that would deploy a *second* xcodebuild on a
            # different port.  Two concurrent xcodebuild processes targeting the
            # same simulator fight for resources — the first one dies, which kills
            # the simulator, which causes app_relaunch to fail with
            # "No devices are booted."
            from specterqa.ios.runner_process import RunnerState  # noqa: PLC0415
            _use_mcp_runner_direct = (
                _mcp_runner_ref is not None
                and _mcp_runner_ref.state == RunnerState.RUNNING
                and not bool(clone)
                and device_type == "simulator"
            )

            if _use_mcp_runner_direct:
                # Resolve UDID — "booted" → actual UDID
                _predeployed = _mcp_runner_ref
                _resolved_udid = _predeployed._udid
                if _resolved_udid == "booted":
                    try:
                        _simlist2 = subprocess.run(
                            ["xcrun", "simctl", "list", "devices", "booted", "-j"],
                            capture_output=True, text=True, timeout=5,
                        )
                        if _simlist2.returncode == 0:
                            import json as _json2
                            _sim2 = _json2.loads(_simlist2.stdout)
                            for _rt in _sim2.get("devices", {}).values():
                                for _d in _rt:
                                    if _d.get("state") == "Booted":
                                        _resolved_udid = _d.get("udid", "booted")
                                        break
                                if _resolved_udid != "booted":
                                    break
                    except Exception:  # noqa: BLE001
                        pass

                port = _predeployed._port
                runner_url = f"http://localhost:{port}"
                target_udid = _resolved_udid
                _session_udid = _resolved_udid  # persist for handle_app_relaunch

                _session = _predeployed  # type: ignore[assignment]
                _backend = XCTestBackend(host="localhost", port=port)
                _annotator = SoMAnnotator(runner_url=runner_url)
                _last_elements = []

                from specterqa.ios.replay import ReplayRecorder
                from specterqa.ios.drivers.simulator.console import ConsoleMonitor
                from specterqa.ios.drivers.simulator.crash import CrashDetector
                from specterqa.ios.drivers.simulator.perf import PerfProfiler
                from specterqa.ios.drivers.simulator.network import NetworkInspector

                _recorder = ReplayRecorder(bundle_id=bundle_id, device_id=device_id)
                _console_monitor = ConsoleMonitor(device_id=target_udid)
                _console_monitor.start()
                _crash_detector = CrashDetector(device_id=target_udid, bundle_id=bundle_id)
                _crash_detector.start()
                _perf_profiler = PerfProfiler(device_id=target_udid, bundle_id=bundle_id)
                _network_inspector = NetworkInspector(device_id=target_udid)
                _network_inspector.start()
                _network_inspector.setup_log_watcher(_console_monitor)

                logger.info(
                    "v14.0.2: reusing pre-deployed RunnerProcess on :%d (skipped TestSession re-deploy)",
                    port,
                )
                _session_state = "running"
                response: dict = {
                    "status": "ok",
                    "device_type": device_type,
                    "target_udid": target_udid,
                    "port": port,
                    "runner_url": runner_url,
                }
                response["clone_udid"] = target_udid
                return response

            # Non-direct path: clone mode, physical device, or no pre-deployed runner.
            _session = TestSession(
                source_udid=device_id,
                bundle_id=bundle_id,
                app_path=app_path,
                clone=bool(clone),
                device_type=device_type,
            )
            _session.start()

            port = _session._port
            runner_url = _session.runner_url
            device_host = _session._device_host or "localhost"

            _backend = XCTestBackend(host=device_host, port=port)
            _annotator = SoMAnnotator(runner_url=runner_url)
            _last_elements = []

            from specterqa.ios.replay import ReplayRecorder

            _recorder = ReplayRecorder(bundle_id=bundle_id, device_id=device_id)

            from specterqa.ios.drivers.simulator.console import ConsoleMonitor
            from specterqa.ios.drivers.simulator.crash import CrashDetector

            _console_monitor = ConsoleMonitor(device_id=_session._target_udid)
            _console_monitor.start()
            _crash_detector = CrashDetector(device_id=_session._target_udid, bundle_id=bundle_id)
            _crash_detector.start()

            from specterqa.ios.drivers.simulator.perf import PerfProfiler
            from specterqa.ios.drivers.simulator.network import NetworkInspector

            _perf_profiler = PerfProfiler(
                device_id=_session._target_udid,
                bundle_id=bundle_id,
            )
            _network_inspector = NetworkInspector(device_id=_session._target_udid)
            _network_inspector.start()
            _network_inspector.setup_log_watcher(_console_monitor)

            _session_state = "running"
            _session_udid = _session._target_udid  # persist for handle_app_relaunch
            response = {
                "status": "ok",
                "device_type": device_type,
                "target_udid": _session._target_udid,
                "port": port,
                "runner_url": runner_url,
            }
            response["clone_udid"] = _session._target_udid
            if device_type == "physical":
                response["device_host"] = device_host
            return response
        except Exception as exc:
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
    global _session, _mcp_runner_ref, _session_udid, _backend, _annotator, _last_elements, _recorder, _session_state, _console_monitor, _crash_detector, _perf_profiler, _network_inspector, _ax_http_server

    with _session_lock:
        from specterqa.ios.backends.browserstack import BrowserStackBackend

        # Capture UDID and session type before stopping so we can reboot the
        # sim afterward (F3: stop_session must not leave sim in Shutdown state).
        _stop_udid = _session_udid
        from specterqa.ios.session_manager import TestSession as _TestSession
        _session_is_test_session = isinstance(_session, _TestSession)

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

        # F3: When _session is a RunnerProcess (MCP pre-deploy path, not TestSession),
        # TestSession._teardown() is NOT called, so we handle sim reboot here.
        # Wait briefly for CoreSimulator to finish xcodebuild cleanup, then reboot.
        if _stop_udid and not _session_is_test_session:
            try:
                import time as _time
                _time.sleep(2)
                subprocess.run(
                    ["xcrun", "simctl", "boot", _stop_udid],
                    capture_output=True, text=True, timeout=10,
                )
                logger.info("F3: Sent simctl boot to %s after RunnerProcess stop (keep sim Booted)", _stop_udid)
            except Exception as exc:
                logger.debug("F3: simctl boot after stop failed: %s", exc)

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

        # Stop AX HTTP server (shuts down background HTTPServer thread)
        if _ax_http_server is not None:
            try:
                _ax_http_server.stop()
            except Exception as exc:
                logger.warning("Error stopping AX HTTP server: %s", exc)

        # PerfProfiler has no background thread — just clear the reference
        _session = None
        _mcp_runner_ref = None
        _session_udid = None
        _backend = None
        _annotator = None
        _last_elements = []
        _recorder = None
        _console_monitor = None
        _crash_detector = None
        _perf_profiler = None
        _network_inspector = None
        _ax_http_server = None
        _session_state = "idle"
        # Reset the circuit breaker so the next ios_start_session starts clean.
        _circuit_breaker.reset()

    return {"status": "stopped"}


def handle_logs(arguments: dict) -> dict:
    """Get recent app console logs.

    Strategy (bridge-first):
    1. Try the runner HTTP bridge (GET /logs) — works during XCTest sessions
       because the runner maintains an in-process ring buffer.
    2. Fall back to the simctl-based ConsoleMonitor when the bridge is
       unavailable (e.g. standalone mode without an XCTest runner).

    Args:
        seconds:  Time window to query (default 30.0).  Only used in fallback.
        level:    Optional level filter, e.g. ``"error"`` or ``"fault"``.
                  Passed as a query param to the bridge; used as a filter in
                  the simctl fallback path.
        category: Optional category filter (exact match, fallback only).
        pattern:  Optional regex pattern applied to the message field
                  (fallback only).
        limit:    Max entries to return from the bridge (default 100).

    Returns:
        {"count": <int>, "logs": [...], "source": "bridge"|"simctl"}
        or {"error": "<message>"} on failure.
    """
    # ── 1. Bridge path (runner HTTP server) ──────────────────────────────────
    if _backend is not None:
        try:
            limit = int(arguments.get("limit", 100))
            level = arguments.get("level", "")
            path = f"/logs?limit={limit}"
            if level:
                path += f"&level={level}"
            resp = _backend._get(path)
            # Bridge returns {"count": N, "logs": [...]} — enrich and return.
            if "logs" in resp and "error" not in resp:
                resp["source"] = "bridge"
                return resp
            # Bridge returned an error (e.g. old runner without /logs) — fall through.
        except Exception as exc:
            logger.debug("handle_logs: bridge unavailable (%s), falling back to simctl", exc)

    # ── 2. Simctl fallback (ConsoleMonitor) ───────────────────────────────────
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
        "source": "simctl",
    }


def handle_crashes(arguments: dict) -> dict:
    """Check for app crashes since the session started.

    Strategy (bridge-first):
    1. Try GET /crashes on the runner HTTP bridge — returns app responsiveness
       and any error-level log entries from the in-process ring buffer.
    2. Merge bridge results with simctl CrashDetector data when available.
    3. Fall back to CrashDetector alone when the bridge is unavailable.

    Returns:
        {
            "crashes_since_session_start": <int>,
            "crashes": [...],
            "app_running": <bool>,
            "app_state": <str>,           # from bridge when available
            "responsive": <bool>,         # from bridge when available
            "error_count": <int>,         # error-level logs from bridge buffer
            "recent_errors": [...],       # from bridge buffer
            "latest_crash": {...} | null,
            "source": "bridge+simctl"|"simctl",
        }
        or {"error": "<message>"} on failure.
    """
    bridge_data: dict = {}

    # ── 1. Bridge path ─────────────────────────────────────────────────────────
    if _backend is not None:
        try:
            resp = _backend._get("/crashes")
            if "error" not in resp:
                bridge_data = resp
        except Exception as exc:
            logger.debug("handle_crashes: bridge unavailable (%s), using simctl only", exc)

    # ── 2. Simctl CrashDetector ────────────────────────────────────────────────
    if _crash_detector is None:
        if bridge_data:
            # Bridge-only result (no simctl session)
            bridge_data["source"] = "bridge"
            bridge_data.setdefault("crashes_since_session_start", 0)
            bridge_data.setdefault("crashes", [])
            bridge_data.setdefault("latest_crash", None)
            return bridge_data
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

    result: dict = {
        "crashes_since_session_start": len(crash_list),
        "crashes": crash_list,
        "app_running": bridge_data.get("app_running", is_running),
        "latest_crash": {
            "timestamp": str(latest.timestamp),
            "exception_type": latest.exception_type,
            "backtrace": latest.backtrace,
        } if latest is not None else None,
        "source": "bridge+simctl" if bridge_data else "simctl",
    }

    # Merge in bridge-only fields (app_state, responsive, error_count, recent_errors)
    for key in ("app_state", "app_state_raw", "responsive", "response_time_sec",
                "error_count", "recent_errors"):
        if key in bridge_data:
            result[key] = bridge_data[key]

    return result


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

    if _session_udid:
        _alive, _state = _verify_sim_alive(_session_udid)
        if not _alive:
            return _sim_shutdown_error(_state)

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
                # Issue 8: check if sim shut down before returning generic error
                if _session_udid:
                    _sim_state = _check_sim_state_for_udid(_session_udid)
                    if _sim_state == "Shutdown":
                        return {
                            "error": "sim_shutdown_during_session",
                            "action_needed": "boot_and_reauth",
                            "sim_state": "Shutdown",
                            "recovery_hint": (
                                "The simulator shut down mid-session (likely iOS 26 SpringBoard crash). "
                                "Call ios_stop_session then ios_start_session to recover, or set "
                                "auto_recover=True on ios_start_session for automatic recovery."
                            ),
                        }
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

    # Prefer element-based tap via the runner (uses XCTest element.tap()
    # which reliably transfers first-responder focus, even on SwiftUI
    # SecureField inside List/Form cells). Fall back to coordinate tap.
    tap_mode = "coordinate"
    target_label = target.label
    target_identifier = getattr(target, "identifier", "")
    try:
        if target_label or target_identifier:
            _backend.tap_element(
                label=target_label or None,
                identifier=target_identifier or None,
            )
            tap_mode = "element"
        else:
            _backend.tap(cx, cy)
    except Exception:
        # Element tap failed — fall back to coordinate tap
        try:
            _backend.tap(cx, cy)
            tap_mode = "coordinate_fallback"
        except Exception as exc:
            return {"error": f"Tap failed: {exc}"}

    # Record the tap for replay
    if _recorder is not None:
        _recorder.record_tap(target.index, target_label, cx, cy, identifier=target_identifier)

    # Auto-checkpoint: capture element state after action for replay verification
    _auto_checkpoint()

    result = {
        "status": "ok",
        "tapped": target_label,
        "x": cx,
        "y": cy,
        "tap_mode": tap_mode,
    }
    if was_refreshed:
        result["cache_refreshed"] = True
    return result


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

    Saves the replay and clears the step buffer.
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
    """Type text into a field — optionally targeting a specific field.

    Args:
        text: String to type (required).
        label: Target field by label (taps it first to transfer focus).
        identifier: Target field by accessibilityIdentifier.
        element_index: Target field by element index from ios_elements.
        x, y: Target field by coordinates (taps first).

    Returns:
        {"status": "ok", "typed": "<text>"}
        or {"error": "<message>"} on failure.
    """
    try:
        _require_session()
    except RuntimeError as exc:
        return {"error": str(exc)}

    if _session_udid:
        _alive, _state = _verify_sim_alive(_session_udid)
        if not _alive:
            return _sim_shutdown_error(_state)

    text = arguments.get("text", "")
    if not text:
        return {"error": "text is required and must be non-empty"}

    label = arguments.get("label")
    identifier = arguments.get("identifier")
    element_index = arguments.get("element_index")
    x = arguments.get("x")
    y = arguments.get("y")

    # If a target field is specified, resolve it and pass to the runner.
    # The runner taps the field first, then types.
    payload: dict = {"text": text}
    focused_info = None

    # Resolve ALL target types to label (fast runner lookup) or coordinates.
    # Never send identifier directly — findByIdentifier walks the full 50-deep
    # tree and takes 10+ seconds on SwiftUI Forms.
    if identifier is not None:
        # Resolve from element cache first
        target = next(
            (e for e in _last_elements if getattr(e, "identifier", "") == identifier),
            None,
        )
        if target is not None:
            # Send label to runner (fast findByLabel lookup)
            if target.label:
                payload["label"] = target.label
                focused_info = f"identifier:{identifier} (via label:{target.label})"
            else:
                # No label — send coordinates
                cx = target.x + target.width / 2
                cy = target.y + target.height / 2
                payload["x"] = cx
                payload["y"] = cy
                focused_info = f"identifier:{identifier} (via coords:{cx:.0f},{cy:.0f})"
        else:
            # Not in cache — fall back to runner-side lookup (slow but correct)
            payload["identifier"] = identifier
            focused_info = f"identifier:{identifier}"
    elif label is not None:
        payload["label"] = label
        focused_info = f"label:{label}"
    elif element_index is not None:
        target = next((e for e in _last_elements if e.index == int(element_index)), None)
        if target is None:
            return {"error": f"Element index {element_index} not found. Call ios_elements first."}
        cx = target.x + target.width / 2
        cy = target.y + target.height / 2
        payload["x"] = cx
        payload["y"] = cy
        focused_info = f"index:{element_index} ({target.label})"
    elif x is not None and y is not None:
        payload["x"] = float(x)
        payload["y"] = float(y)
        focused_info = f"coordinates:({x},{y})"

    try:
        _backend._post("/type", payload)
    except Exception as exc:
        return {"error": f"Type failed: {exc}"}

    # Record the type action for replay
    if _recorder is not None:
        _recorder.record_type(text)

    # Auto-checkpoint: capture element state after action for replay verification
    _auto_checkpoint()

    result = {"status": "ok", "typed": text}
    if focused_info:
        result["focused"] = focused_info
    return result


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


def handle_observe(arguments: dict) -> dict:
    """v16.0.0a3 — vision-first observation primitive (file-path delivery).

    Captures the current screen and writes the screenshot to a temp file, then
    returns the file path plus structured metadata. Vision-capable agents read
    the path with their native file-read tool to get the image into multimodal
    input — avoiding the v16.0.0a1/a2 problem where the inline base64 payload
    (188KB at standard quality) blew the MCP envelope cap (~25KB).

    Coord space (v16.0.0a3 fix per Maurice §P0-4): ``device_w`` and ``device_h``
    are LOGICAL POINTS (e.g. 390x844 for iPhone 12). Use these for ``ios_act``
    coordinates. Separate ``screenshot_w`` / ``screenshot_h`` report the JPEG
    pixel dims at the chosen quality — useful for normalized math but NOT for
    tap targeting.

    Args:
        quality: "standard" (50%, default), "full" (no resize), "thumbnail" (25%).
        include_legacy_elements: When True, also include the un-filtered legacy
            element list under ``"legacy_elements"`` for transition compatibility.
            Default False — vision-first agents shouldn't need it.

    Returns:
        {
            "screenshot_path": "/tmp/specterqa-observe-<uuid>.jpg",
            "device_w": <int — LOGICAL POINTS>,
            "device_h": <int — LOGICAL POINTS>,
            "screenshot_w": <int — pixel width of the saved JPEG>,
            "screenshot_h": <int — pixel height of the saved JPEG>,
            "reliable_targets": [...],
            "app_state": {...},
            "captured_at": "<ISO 8601 UTC>"
        }
        or {"error": "<message>"} on failure.
    """
    global _last_elements

    try:
        _require_session()
    except RuntimeError as exc:
        return {"error": str(exc)}

    if _session_udid:
        _alive, _state = _verify_sim_alive(_session_udid)
        if not _alive:
            return _sim_shutdown_error(_state)

    quality = str(arguments.get("quality", "standard")).lower()
    include_legacy = bool(arguments.get("include_legacy_elements", False))
    scale = _QUALITY_SCALES.get(quality, 0.5)

    try:
        annotated_b64, elements = _get_annotated_screenshot()
        _last_elements = elements

        # Filter to elements with explicit accessibilityIdentifier set.
        reliable_targets = [
            {
                "identifier": e.identifier,
                "label": e.label,
                "type": e.element_type,
                "x": e.x,
                "y": e.y,
                "width": e.width,
                "height": e.height,
                "center_x": e.center_x,
                "center_y": e.center_y,
            }
            for e in elements
            if getattr(e, "identifier", "")
        ]

        # Resize annotated screenshot per quality flag.
        resized_b64 = _resize_screenshot(annotated_b64, scale=scale)

        # P0-3 fix — write the screenshot to a temp file and return the path
        # instead of the inline base64. Drops the 188KB JSON envelope problem.
        import base64 as _b64  # noqa: PLC0415
        import uuid as _uuid_mod  # noqa: PLC0415
        from PIL import Image as _PILImage  # noqa: PLC0415
        from io import BytesIO as _BytesIO  # noqa: PLC0415

        screenshot_bytes = _b64.b64decode(resized_b64)
        screenshot_id = _uuid_mod.uuid4().hex[:8]
        screenshot_path = f"/tmp/specterqa-observe-{screenshot_id}.jpg"
        try:
            with open(screenshot_path, "wb") as _f:
                _f.write(screenshot_bytes)
        except OSError as exc:
            return {"error": f"observe: could not write screenshot to disk: {exc}"}

        # Read pixel dims from the saved JPEG.
        try:
            img = _PILImage.open(_BytesIO(screenshot_bytes))
            screenshot_w, screenshot_h = img.size
        except Exception:  # noqa: BLE001
            screenshot_w = screenshot_h = 0

        # P0-4 fix — device_w/h must be LOGICAL POINTS, not pixel dims.
        # Try the session/backend objects first, then fall back to a
        # device-name → known-points map, then a safe iPhone default.
        device_w = device_h = 0
        try:
            sess = _session
            if sess is not None:
                device_w = int(getattr(sess, "_device_w", 0) or 0)
                device_h = int(getattr(sess, "_device_h", 0) or 0)
            if (device_w == 0 or device_h == 0) and _backend is not None:
                device_w = int(getattr(_backend, "_device_w", device_w) or device_w)
                device_h = int(getattr(_backend, "_device_h", device_h) or device_h)
        except Exception:  # noqa: BLE001
            pass

        # Look up logical-point dims by device name when session metadata is empty.
        if device_w == 0 or device_h == 0:
            dw, dh = _resolve_device_logical_points(_session_udid)
            if device_w == 0:
                device_w = dw
            if device_h == 0:
                device_h = dh

        # App state (foreground / background / suspended) — out-of-band.
        app_state_payload: dict = {}
        try:
            if _backend is not None:
                app_state_payload = _backend.app_state()
        except Exception as exc:  # noqa: BLE001
            app_state_payload = {"error": f"app_state probe failed: {exc}"}

        from datetime import datetime, timezone  # noqa: PLC0415
        captured_at = datetime.now(timezone.utc).isoformat()

        result = {
            "screenshot_path": screenshot_path,
            "device_w": device_w,
            "device_h": device_h,
            "screenshot_w": screenshot_w,
            "screenshot_h": screenshot_h,
            "reliable_targets": reliable_targets,
            "app_state": app_state_payload,
            "captured_at": captured_at,
        }
        if include_legacy:
            result["legacy_elements"] = [e.to_dict() for e in elements]
        return result

    except Exception as exc:
        return {"error": f"observe failed: {exc}"}


# ---------------------------------------------------------------------------
# _ensure_sim_booted — module-level (shared by handle_start_session +
# handle_app_relaunch's nested copy)
# ---------------------------------------------------------------------------
#
# v16.0.0a3 (Maurice/Example Reader dogfood §P0-1): handle_start_session previously
# proceeded to deploy without checking the sim was Booted. On a Shutdown sim
# the runner deploy was guaranteed to fail; the agent saw status:ok but the
# /health probe never went 200. This module-level helper is now called from
# both handle_start_session (new) and handle_app_relaunch (existing nested
# copy still works — kept for now to keep this PR small).


def _ensure_sim_booted(target_udid: str) -> bool:
    """Return True if the simulator is Booted (or successfully booted now).

    Handles iOS 26.x xcodebuild test lifecycle quirks: sim may be in
    "Shutting Down" or "Shutdown" between MCP calls. Steps:
      1. Poll up to 15s for "Shutting Down" to complete
      2. Issue xcrun simctl boot
      3. Poll up to 20s for "Booted"
    """
    def _get_state() -> str:
        try:
            rp = subprocess.run(
                ["xcrun", "simctl", "list", "devices", "--json"],
                capture_output=True, text=True, timeout=10,
            )
            if rp.returncode == 0:
                data = json.loads(rp.stdout)
                for _rt in data.get("devices", {}).values():
                    for _d in _rt:
                        if _d.get("udid") == target_udid:
                            return _d.get("state", "Unknown")
        except Exception:  # noqa: BLE001
            pass
        return "Unknown"

    try:
        state = _get_state()
        if state == "Booted":
            return True
        if state == "Shutting Down":
            logger.info("_ensure_sim_booted: sim %s is Shutting Down — waiting", target_udid)
            for _ in range(15):
                time.sleep(1)
                state = _get_state()
                if state == "Shutdown":
                    break
                if state == "Booted":
                    return True
        logger.info("_ensure_sim_booted: sim %s in state %r — attempting boot", target_udid, state)
        boot_r = subprocess.run(
            ["xcrun", "simctl", "boot", target_udid],
            capture_output=True, text=True, timeout=30,
        )
        if boot_r.returncode != 0 and "current state: Booted" not in boot_r.stderr:
            logger.warning("_ensure_sim_booted: simctl boot failed: %s", boot_r.stderr.strip())
            return False
        for _ in range(20):
            time.sleep(1)
            if _get_state() == "Booted":
                logger.info("_ensure_sim_booted: sim %s is Booted", target_udid)
                return True
        logger.warning("_ensure_sim_booted: sim %s did not reach Booted within 20s", target_udid)
        return False
    except Exception as exc:  # noqa: BLE001
        logger.warning("_ensure_sim_booted error: %s", exc)
        return False


# ---------------------------------------------------------------------------
# Device-name → logical-point dimensions
# ---------------------------------------------------------------------------
#
# v16.0.0a3 (Maurice/Example Reader dogfood §P0-4): ios_observe + ios_act use ONE
# coordinate space — logical points, matching what the Swift runner hit-tests
# against and what `:8222/health` reports. The pixel dimensions of the saved
# JPEG are surfaced separately (screenshot_w / screenshot_h) for any agent
# that wants to do pixel-level reasoning, but tap targeting always goes
# through points.

_KNOWN_DEVICE_POINTS: dict[str, tuple[int, int]] = {
    # iPhone 12 family
    "iPhone 12 mini":     (375, 812),
    "iPhone 12":          (390, 844),
    "iPhone 12 Pro":      (390, 844),
    "iPhone 12 Pro Max":  (428, 926),
    # iPhone 13 family
    "iPhone 13 mini":     (375, 812),
    "iPhone 13":          (390, 844),
    "iPhone 13 Pro":      (390, 844),
    "iPhone 13 Pro Max":  (428, 926),
    # iPhone 14 family
    "iPhone 14":          (390, 844),
    "iPhone 14 Plus":     (428, 926),
    "iPhone 14 Pro":      (393, 852),
    "iPhone 14 Pro Max":  (430, 932),
    # iPhone 15 family
    "iPhone 15":          (393, 852),
    "iPhone 15 Plus":     (430, 932),
    "iPhone 15 Pro":      (393, 852),
    "iPhone 15 Pro Max":  (430, 932),
    # iPhone 16 family
    "iPhone 16":          (393, 852),
    "iPhone 16 Plus":     (430, 932),
    "iPhone 16 Pro":      (402, 874),
    "iPhone 16 Pro Max":  (440, 956),
    # iPhone 17 family
    "iPhone 17":          (402, 874),
    "iPhone 17 Pro":      (402, 874),
    "iPhone 17 Pro Max":  (440, 956),
    # iPhone SE 3rd gen
    "iPhone SE (3rd generation)": (375, 667),
}


def _resolve_device_logical_points(udid: str | None) -> tuple[int, int]:
    """Return (device_w, device_h) logical-point dimensions for *udid*.

    Looks up the device name via ``xcrun simctl list devices --json`` and
    matches against ``_KNOWN_DEVICE_POINTS``. Falls back to a safe iPhone 14
    default (390x844) when the device is unknown or simctl is unavailable —
    that's the modal iPhone form factor and matches the v15.x default.
    """
    if not udid:
        return (390, 844)
    try:
        result = subprocess.run(
            ["xcrun", "simctl", "list", "devices", "--json"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode != 0:
            return (390, 844)
        data = json.loads(result.stdout)
        for runtime_devices in data.get("devices", {}).values():
            for d in runtime_devices:
                if d.get("udid") == udid:
                    name = d.get("name", "")
                    if name in _KNOWN_DEVICE_POINTS:
                        return _KNOWN_DEVICE_POINTS[name]
                    # Best-effort partial match (e.g. simctl might add a suffix)
                    for known_name, dims in _KNOWN_DEVICE_POINTS.items():
                        if known_name in name:
                            return dims
                    break
    except Exception:  # noqa: BLE001
        pass
    return (390, 844)


def handle_act(arguments: dict) -> dict:
    """v16.0.0 — unified vision-first action dispatcher.

    Single entry point for tap / type / swipe / key / scroll / long_press / drag.
    All actions are coordinate-primary: the agent is responsible for choosing
    coordinates from the ``ios_observe`` screenshot. ``identifier`` is allowed
    on tap/long_press as an opt-in semantic helper for elements with explicit
    ``accessibilityIdentifier`` (the ``reliable_targets`` shape from observe).
    Label-based selectors are *not* supported — they're the v15.x crash class
    that v16 deletes.

    Args:
        action: dict with required ``"kind"`` and kind-specific fields:
            {"kind": "tap",        "x": float, "y": float}
            {"kind": "tap",        "identifier": str}        # opt-in semantic
            {"kind": "long_press", "x": float, "y": float, "duration_s"?: float}
            {"kind": "long_press", "identifier": str, "duration_s"?: float}
            {"kind": "type",       "text": str, "x"?: float, "y"?: float}
            {"kind": "swipe",      "from": [x,y], "to": [x,y], "duration_ms"?: int}
            {"kind": "key",        "name": str}
            {"kind": "scroll",     "direction": "up"|"down"|"left"|"right",
                                   "x"?: float, "y"?: float}
            {"kind": "drag",       "from": [x,y], "to": [x,y], "duration_ms"?: int}
        normalized: when True, x/y values in [0.0, 1.0] are treated as fractions
            of device dimensions and converted to device-points before dispatch.

    Returns:
        Runner response dict, or {"error": "<message>"}.
    """
    if not isinstance(arguments, dict):
        return {"error": "ios_act: arguments must be a dict"}

    action = arguments.get("action")
    if not isinstance(action, dict):
        return {"error": "ios_act: 'action' must be a dict with a 'kind' field"}

    kind = action.get("kind")
    if not kind:
        return {"error": "ios_act: action.kind is required"}

    normalized = bool(arguments.get("normalized", False))

    # Resolve normalized coords to logical points (v16.0.0a3 — single coord
    # space everywhere, matches handle_observe.device_w/h).
    def _denorm(coords: tuple[float, float]) -> tuple[float, float]:
        if not normalized:
            return coords
        dw = dh = 0
        try:
            if _session is not None:
                dw = int(getattr(_session, "_device_w", 0) or 0)
                dh = int(getattr(_session, "_device_h", 0) or 0)
            if (dw == 0 or dh == 0) and _backend is not None:
                dw = int(getattr(_backend, "_device_w", dw) or dw)
                dh = int(getattr(_backend, "_device_h", dh) or dh)
        except Exception:  # noqa: BLE001
            pass
        if dw <= 0 or dh <= 0:
            dw, dh = _resolve_device_logical_points(_session_udid)
        if dw <= 0 or dh <= 0:
            return coords  # unable to denormalize; pass through
        return (coords[0] * dw, coords[1] * dh)

    if kind == "tap":
        identifier = action.get("identifier")
        if identifier:
            return handle_tap({"identifier": identifier})
        x = action.get("x")
        y = action.get("y")
        if x is None or y is None:
            return {"error": "ios_act tap: requires either identifier or (x, y)"}
        nx, ny = _denorm((float(x), float(y)))
        return handle_tap({"x": nx, "y": ny})

    if kind == "long_press":
        # v16.0.0a1: handle_long_press is element-only (legacy). For coord-based
        # long press we dispatch through handle_tap with duration > 0 — the
        # runner's TapRoute supports this via TouchInjector.tap(x, y, duration:).
        x = action.get("x")
        y = action.get("y")
        if x is None or y is None:
            return {"error": "ios_act long_press: requires (x, y) coordinates"}
        nx, ny = _denorm((float(x), float(y)))
        duration = float(action.get("duration_s", 1.0))
        return handle_tap({"x": nx, "y": ny, "duration": duration})

    if kind == "type":
        text = action.get("text", "")
        x = action.get("x")
        y = action.get("y")
        payload = {"text": text}
        if x is not None and y is not None:
            nx, ny = _denorm((float(x), float(y)))
            payload["x"] = nx
            payload["y"] = ny
        return handle_type(payload)

    if kind == "swipe":
        frm = action.get("from") or [None, None]
        to = action.get("to") or [None, None]
        if len(frm) != 2 or len(to) != 2:
            return {"error": "ios_act swipe: 'from' and 'to' must be [x, y] arrays"}
        fx, fy = _denorm((float(frm[0]), float(frm[1])))
        tx, ty = _denorm((float(to[0]), float(to[1])))
        duration_ms = int(action.get("duration_ms", 200))
        return handle_swipe({
            "start_x": fx, "start_y": fy,
            "end_x": tx, "end_y": ty,
            "duration_ms": duration_ms,
        })

    if kind == "drag":
        frm = action.get("from") or [None, None]
        to = action.get("to") or [None, None]
        if len(frm) != 2 or len(to) != 2:
            return {"error": "ios_act drag: 'from' and 'to' must be [x, y] arrays"}
        fx, fy = _denorm((float(frm[0]), float(frm[1])))
        tx, ty = _denorm((float(to[0]), float(to[1])))
        duration_ms = int(action.get("duration_ms", 500))
        return handle_swipe({
            "start_x": fx, "start_y": fy,
            "end_x": tx, "end_y": ty,
            "duration_ms": duration_ms,
        })

    if kind == "key":
        name = action.get("name")
        if not name:
            return {"error": "ios_act key: 'name' is required"}
        return handle_press_key({"key": name})

    if kind == "scroll":
        direction = action.get("direction")
        if direction not in ("up", "down", "left", "right"):
            return {"error": "ios_act scroll: direction must be up/down/left/right"}
        # Anchor: caller-supplied (x, y) is the gesture starting point.
        # If absent, we infer screen center from the most recent ios_observe
        # screenshot dimensions (cached from the session backend / annotator
        # / PIL fallback). When dims aren't available, use safe iPhone defaults.
        x = action.get("x")
        y = action.get("y")
        if x is None or y is None:
            dw = dh = 0
            try:
                if _session is not None:
                    dw = int(getattr(_session, "_device_w", 0) or 0)
                    dh = int(getattr(_session, "_device_h", 0) or 0)
                if (dw == 0 or dh == 0) and _backend is not None:
                    dw = int(getattr(_backend, "_device_w", dw) or dw)
                    dh = int(getattr(_backend, "_device_h", dh) or dh)
                if (dw == 0 or dh == 0) and _annotator is not None:
                    sz = getattr(_annotator, "screen_size", None)
                    if sz and len(sz) >= 2:
                        dw = int(sz[0]) if dw == 0 else dw
                        dh = int(sz[1]) if dh == 0 else dh
            except Exception:  # noqa: BLE001
                pass
            if dw <= 0 or dh <= 0:
                # Safe iPhone defaults (iPhone 14 device-points)
                dw, dh = 390, 844
            x, y = dw / 2, dh / 2
        else:
            x, y = _denorm((float(x), float(y)))
        # Scroll-down means content moves up — swipe from below to above the anchor.
        delta = 200.0
        if direction == "down":
            return handle_swipe({"start_x": x, "start_y": y + delta, "end_x": x, "end_y": y - delta, "duration_ms": 200})
        if direction == "up":
            return handle_swipe({"start_x": x, "start_y": y - delta, "end_x": x, "end_y": y + delta, "duration_ms": 200})
        if direction == "left":
            return handle_swipe({"start_x": x + delta, "start_y": y, "end_x": x - delta, "end_y": y, "duration_ms": 200})
        if direction == "right":
            return handle_swipe({"start_x": x - delta, "start_y": y, "end_x": x + delta, "end_y": y, "duration_ms": 200})

    return {"error": f"ios_act: unsupported action.kind={kind!r}"}


def handle_app_state(arguments: dict) -> dict:
    """Check if the app is running, backgrounded, or crashed.

    Returns:
        Runner response dict with app lifecycle state, or {"error": "<message>"}.
    """
    try:
        _require_session()
    except RuntimeError as exc:
        return {"error": str(exc)}

    if _session_udid:
        _alive, _state = _verify_sim_alive(_session_udid)
        if not _alive:
            return _sim_shutdown_error(_state)

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

    Strategy (bridge-first):
    1. Try GET /perf on the runner HTTP bridge — returns mach_task_basic_info
       metrics from inside the XCTest process (RSS, virtual, threads, CPU time).
       This works when simctl-based monitoring fails due to "device not booted"
       errors during XCTest sessions.
    2. Fall back to the simctl-based PerfProfiler when the bridge is unavailable
       (e.g. standalone mode without an XCTest runner).

    Returns:
        {"memory_rss_mb": float, "thread_count": int, "cpu_time_total_sec": float,
         ...}  (bridge fields differ slightly from simctl fields — both are useful)
        or {"error": "<message>"} on failure.
    """
    # ── 1. Bridge path ─────────────────────────────────────────────────────────
    if _backend is not None:
        try:
            resp = _backend._get("/perf")
            if "error" not in resp and resp:
                resp["source"] = "bridge"
                return resp
        except Exception as exc:
            logger.debug("handle_perf: bridge unavailable (%s), falling back to simctl", exc)

    # ── 2. Simctl fallback (PerfProfiler) ─────────────────────────────────────
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
            "source": "simctl",
        }
    except Exception as exc:
        return {"error": f"perf snapshot failed: {exc}"}


def handle_perf_baseline(arguments: dict) -> dict:
    """Capture current performance metrics as a baseline for comparison.

    Stores the current ios_perf snapshot so that a subsequent call to
    handle_perf_compare can show deltas.  Call this BEFORE running the
    user flow you want to measure.

    Returns:
        {"status": "ok", "baseline": {...}, "message": "..."}
        or {"error": "<message>"} if the perf snapshot failed.
    """
    global _perf_baseline
    current = handle_perf({})
    if "error" in current:
        return current
    _perf_baseline = current
    return {
        "status": "ok",
        "baseline": current,
        "message": "Baseline captured. Call ios_perf_compare after your test actions.",
    }


def handle_perf_compare(arguments: dict) -> dict:
    """Compare current performance metrics against the stored baseline.

    Computes deltas for RSS, CPU time, and thread count and applies a simple
    severity classification so that the agent can decide whether to escalate.

    Returns:
        {
          "baseline": {...},
          "current": {...},
          "deltas": {"memory_rss_mb": float, "cpu_time_sec": float, "thread_count": int},
          "issues": [...],
          "verdict": "ISSUES_FOUND" | "OK",
        }
        or {"error": "<message>"} if no baseline has been captured or the
        current perf snapshot failed.
    """
    if _perf_baseline is None:
        return {"error": "No baseline captured. Call ios_perf_baseline first."}

    current = handle_perf({})
    if "error" in current:
        return current

    baseline = _perf_baseline

    def _delta(key: str) -> float | None:
        c = current.get(key, None)
        b = baseline.get(key, None)
        if isinstance(c, (int, float)) and isinstance(b, (int, float)):
            return round(c - b, 2)
        return None

    # Bridge exposes memory_rss_mb; simctl fallback uses memory_mb — handle both.
    rss_delta = _delta("memory_rss_mb") if _delta("memory_rss_mb") is not None else _delta("memory_mb")
    cpu_delta = _delta("cpu_time_total_sec") if _delta("cpu_time_total_sec") is not None else _delta("cpu_time")
    thread_delta = _delta("thread_count")

    issues: list[str] = []
    if rss_delta is not None and rss_delta > 50:
        issues.append(f"HIGH: RSS grew {rss_delta}MB — possible memory leak")
    elif rss_delta is not None and rss_delta > 20:
        issues.append(f"MEDIUM: RSS grew {rss_delta}MB — monitor for leak")

    if thread_delta is not None and thread_delta > 10:
        issues.append(f"HIGH: {thread_delta} new threads — possible thread leak")

    if cpu_delta is not None and cpu_delta > 5:
        issues.append(f"MEDIUM: {cpu_delta}s CPU time consumed — heavy processing")

    return {
        "baseline": baseline,
        "current": current,
        "deltas": {
            "memory_rss_mb": rss_delta,
            "cpu_time_sec": cpu_delta,
            "thread_count": thread_delta,
        },
        "issues": issues if issues else ["No significant performance issues detected"],
        "verdict": "ISSUES_FOUND" if any("HIGH" in i for i in issues) else "OK",
    }


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

    Strategy (bridge-first):
    1. Try GET /network on the runner HTTP bridge — returns basic reachability
       from inside the XCTest process and a clear note about the cross-process
       limitation (the runner cannot intercept the app's URLSession traffic).
    2. Merge bridge reachability with simctl NetworkInspector data when both
       are available.
    3. Fall back to NetworkInspector alone when the bridge is unavailable.

    Args:
        seconds: Time window for recent requests (default 30.0).

    Returns:
        {"requests": [...], "bytes_in": int, ..., "network_reachable": bool,
         "source": "bridge+simctl"|"simctl"}
        or {"error": "<message>"} on failure.
    """
    bridge_data: dict = {}

    # ── 1. Bridge path ─────────────────────────────────────────────────────────
    if _backend is not None:
        try:
            resp = _backend._get("/network")
            if "error" not in resp:
                bridge_data = resp
        except Exception as exc:
            logger.debug("handle_network: bridge unavailable (%s), using simctl only", exc)

    # ── 2. Simctl fallback (NetworkInspector) ──────────────────────────────────
    if _network_inspector is None:
        if bridge_data:
            bridge_data["source"] = "bridge"
            return bridge_data
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

        result: dict = {
            "requests": request_list,
            "request_count": len(request_list),
            "bytes_in": snap.bytes_in,
            "bytes_out": snap.bytes_out,
            "throughput_in_bps": snap.throughput_in,
            "throughput_out_bps": snap.throughput_out,
            "active_connections": snap.active_connections,
            "nettop_available": snap.nettop_available,
            "window_seconds": seconds,
            "source": "bridge+simctl" if bridge_data else "simctl",
        }

        # Merge in bridge-only fields (reachability, note)
        for key in ("network_reachable", "note"):
            if key in bridge_data:
                result[key] = bridge_data[key]

        return result
    except Exception as exc:
        return {"error": f"network snapshot failed: {exc}"}


# ---------------------------------------------------------------------------
# Task A — Replay MCP handlers
# ---------------------------------------------------------------------------

_DEFAULT_REPLAY_DIR = ".specterqa/replays"


def handle_list_replays(arguments: dict) -> list:
    """List saved replay YAML files with name, step count, and last-modified time.

    Args:
        replay_dir: Directory to scan (default .specterqa/replays).

    Returns:
        List of {"name": str, "path": str, "steps": int, "modified": str}
        Sorted by last-modified descending (newest first).
    """
    import datetime

    replay_dir = Path(arguments.get("replay_dir", _DEFAULT_REPLAY_DIR))
    if not replay_dir.exists():
        return []

    results = []
    for p in sorted(replay_dir.glob("*.yaml"), key=lambda f: f.stat().st_mtime, reverse=True):
        try:
            import yaml as _yaml
            data = _yaml.safe_load(p.read_text(encoding="utf-8"))
            r = data.get("replay", {})
            step_count = len(r.get("steps", []))
            mtime = datetime.datetime.fromtimestamp(p.stat().st_mtime).strftime("%Y-%m-%dT%H:%M:%S")
            results.append({
                "name": r.get("name", p.stem),
                "path": str(p.resolve()),
                "steps": step_count,
                "modified": mtime,
            })
        except Exception as exc:  # noqa: BLE001 — skip unreadable files
            logger.debug("handle_list_replays: skipping %s (%s)", p, exc)
    return results


def handle_replay(arguments: dict) -> dict:
    """Run a saved replay YAML end-to-end against the active session.

    Args:
        name: Replay name (from ios_list_replays) or a file path.
        replay_dir: Directory to look in (default .specterqa/replays).

    Returns:
        {"status": "passed"|"failed"|"error", "steps_executed": int,
         "failed_step_index": int|None, "failures": [...]}
    """
    if _backend is None:
        return {
            "error": "No active session. Call ios_start_session first.",
            "hint": "ios_replay requires an active session — the XCTest runner must be running.",
        }

    name = arguments.get("name", "")
    if not name:
        return {"error": "'name' is required — see ios_list_replays for available replays"}

    # Resolve path: check if name is already a path
    candidate = Path(name)
    if not candidate.exists():
        replay_dir = Path(arguments.get("replay_dir", _DEFAULT_REPLAY_DIR))
        # Try exact match first
        candidate = replay_dir / name
        if not candidate.exists():
            # Try adding .yaml extension
            candidate = replay_dir / f"{name}.yaml"
        if not candidate.exists():
            return {
                "error": f"Replay '{name}' not found. Call ios_list_replays to see available replays.",
            }

    try:
        from specterqa.ios.replay import ReplayPlayer
        player = ReplayPlayer(str(candidate))
    except Exception as exc:
        return {"error": f"Failed to load replay '{candidate}': {exc}"}

    # Execute using the active session's backend
    try:
        result: dict = {
            "name": player.name,
            "bundle_id": player.bundle_id,
            "steps": [],
            "passed": True,
            "exit_code": 0,
        }

        from specterqa.ios.som_annotator import SoMAnnotator

        # Determine runner URL from the active backend
        runner_url = getattr(_backend, "_runner_url", None) or getattr(_backend, "runner_url", None)
        if runner_url is None:
            # Try constructing from port
            port = getattr(_backend, "_port", None) or getattr(_backend, "port", 8100)
            runner_url = f"http://localhost:{port}"

        annotator = SoMAnnotator(runner_url=runner_url)

        i = 0
        while i < len(player.steps):
            step = player.steps[i]
            step_result = player._execute_step(step, _backend, annotator, result)
            result["steps"].append(step_result)
            if not step_result.get("passed", True):
                result["passed"] = False
            i += 1

        failed_indices = [j for j, s in enumerate(result["steps"]) if not s.get("passed", True)]
        return {
            "status": "passed" if result["passed"] else "failed",
            "steps_executed": len(result["steps"]),
            "failed_step_index": failed_indices[0] if failed_indices else None,
            "failures": [result["steps"][j] for j in failed_indices],
            "exit_code": result["exit_code"],
        }
    except Exception as exc:
        logger.exception("handle_replay: unexpected error")
        return {"status": "error", "error": str(exc), "steps_executed": 0, "failed_step_index": None, "failures": []}


def handle_validate_replay(arguments: dict) -> dict:
    """Parse a replay YAML and validate structure without executing it.

    Args:
        name: Replay name or file path.
        replay_dir: Directory to look in (default .specterqa/replays).

    Returns:
        {"valid": bool, "step_count": int, "issues": [...], "name": str, "bundle_id": str}
    """
    name = arguments.get("name", "")
    if not name:
        return {"valid": False, "issues": ["'name' is required"]}

    candidate = Path(name)
    if not candidate.exists():
        replay_dir = Path(arguments.get("replay_dir", _DEFAULT_REPLAY_DIR))
        candidate = replay_dir / name
        if not candidate.exists():
            candidate = replay_dir / f"{name}.yaml"
        if not candidate.exists():
            return {
                "valid": False,
                "issues": [f"Replay '{name}' not found. Call ios_list_replays to see available replays."],
            }

    issues = []
    try:
        import yaml as _yaml
        raw = _yaml.safe_load(candidate.read_text(encoding="utf-8"))
    except Exception as exc:
        return {"valid": False, "issues": [f"YAML parse error: {exc}"]}

    if not isinstance(raw, dict) or "replay" not in raw:
        return {"valid": False, "issues": ["Missing top-level 'replay' key"]}

    r = raw["replay"]
    bundle_id = r.get("bundle_id", "")
    replay_name = r.get("name", candidate.stem)

    if not bundle_id:
        issues.append("Missing 'bundle_id' — replay may not launch the correct app")

    steps = r.get("steps", [])
    if not steps:
        issues.append("No steps defined — replay will do nothing")

    known_actions = {"tap", "swipe", "swipe_back", "type", "press_key", "long_press",
                     "wait_for_element", "assert", "skip_to"}
    for i, step in enumerate(steps):
        if not isinstance(step, dict):
            issues.append(f"Step {i}: expected dict, got {type(step).__name__}")
            continue
        action = step.get("action")
        if not action:
            # Check Maestro aliases
            maestro_aliases = {"tapOn", "assertVisible", "assertNotVisible", "inputText", "waitFor", "tapOnIdentifier"}
            if not any(k in step for k in maestro_aliases):
                issues.append(f"Step {i}: missing 'action' field and no recognized Maestro alias")
        elif action not in known_actions:
            issues.append(f"Step {i}: unknown action '{action}'")

        if step.get("action") == "tap" or step.get("tapOn"):
            label = step.get("element_label") or step.get("tapOn")
            x = step.get("x")
            y = step.get("y")
            identifier = step.get("element_identifier")
            if not label and not identifier and (x is None or y is None):
                issues.append(f"Step {i}: tap step has no label, identifier, or coordinates")

    return {
        "valid": len(issues) == 0,
        "step_count": len(steps),
        "issues": issues,
        "name": replay_name,
        "bundle_id": bundle_id,
    }


# ---------------------------------------------------------------------------
# Task B — Discovery MCP handlers
# ---------------------------------------------------------------------------


def handle_doctor(arguments: dict) -> dict:
    """Check environment readiness for SpecterQA iOS.

    Returns:
        {"checks": {"xcode_present": {...}, "simulators_available": {...},
                    "runner_built": {...}}, "overall": "ok"|"degraded"|"fail"}
    """
    import shutil

    checks = {}

    # 1. Xcode check
    try:
        xcode_path = subprocess.check_output(
            ["xcode-select", "-p"], text=True, stderr=subprocess.DEVNULL
        ).strip()
        checks["xcode_present"] = {
            "pass": bool(xcode_path),
            "detail": xcode_path,
            "fix": None,
        }
    except Exception:
        checks["xcode_present"] = {
            "pass": False,
            "detail": "xcode-select -p failed",
            "fix": "Install Xcode from the App Store and run: sudo xcode-select --switch /Applications/Xcode.app",
        }

    # 2. xcrun available
    xcrun_available = shutil.which("xcrun") is not None
    checks["xcrun_available"] = {
        "pass": xcrun_available,
        "detail": "xcrun found" if xcrun_available else "xcrun not in PATH",
        "fix": None if xcrun_available else "Install Xcode Command Line Tools: xcode-select --install",
    }

    # 3. Booted simulators
    try:
        raw = subprocess.check_output(
            ["xcrun", "simctl", "list", "devices", "booted", "--json"],
            text=True, stderr=subprocess.DEVNULL,
        )
        import json as _json
        sim_data = _json.loads(raw)
        booted = []
        for runtime, devices in sim_data.get("devices", {}).items():
            for d in devices:
                if d.get("state", "").lower() == "booted":
                    booted.append(d.get("udid", ""))
        checks["simulators_available"] = {
            "pass": len(booted) > 0,
            "detail": f"{len(booted)} booted simulator(s): {booted}",
            "fix": None if booted else "Boot a simulator: open Simulator.app or xcrun simctl boot <udid>",
        }
    except Exception as exc:
        checks["simulators_available"] = {
            "pass": False,
            "detail": f"simctl query failed: {exc}",
            "fix": "Check Xcode installation",
        }

    # 4. Runner built check
    try:
        from specterqa.ios.session_manager import _DEFAULT_RUNNER_BUILD_DIR
        runner_build = Path(_DEFAULT_RUNNER_BUILD_DIR)
        runner_exists = runner_build.exists() and any(runner_build.rglob("*.xctestrun"))
        checks["runner_built"] = {
            "pass": runner_exists,
            "detail": str(runner_build) if runner_exists else f"No .xctestrun in {runner_build}",
            "fix": None if runner_exists else "Build runner: specterqa-ios runner build",
        }
    except Exception as exc:
        checks["runner_built"] = {
            "pass": False,
            "detail": f"Could not check runner: {exc}",
            "fix": "Build runner: specterqa-ios runner build",
        }

    # 5. Active session status
    checks["session_active"] = {
        "pass": _backend is not None,
        "detail": "Session active" if _backend is not None else "No session running",
        "fix": None if _backend is not None else "Start a session: ios_start_session(bundle_id=...)",
    }

    all_pass = all(c["pass"] for c in checks.values())
    critical_fail = not checks.get("xcode_present", {}).get("pass", True)
    overall = "ok" if all_pass else ("fail" if critical_fail else "degraded")

    return {"checks": checks, "overall": overall}


def handle_devices(arguments: dict) -> list:
    """List booted iOS simulators.

    Returns:
        List of {"udid": str, "name": str, "runtime": str, "state": str}
        Empty list if no simulators are booted.
    """
    try:
        raw = subprocess.check_output(
            ["xcrun", "simctl", "list", "devices", "booted", "--json"],
            text=True, stderr=subprocess.DEVNULL,
        )
        import json as _json
        sim_data = _json.loads(raw)
        results = []
        for runtime, devices in sim_data.get("devices", {}).items():
            for d in devices:
                if d.get("state", "").lower() == "booted":
                    results.append({
                        "udid": d.get("udid", ""),
                        "name": d.get("name", ""),
                        "runtime": runtime,
                        "state": d.get("state", ""),
                    })
        return results
    except Exception as exc:
        logger.debug("handle_devices failed: %s", exc)
        return []


def handle_apps(arguments: dict) -> list:
    """List apps installed on a booted simulator.

    Args:
        device_udid: Simulator UDID (required).

    Returns:
        List of {"bundle_id": str, "display_name": str, "version": str, "install_path": str}
        Returns [{"warning": "<message>"}] on parse failure.

    Note:
        Uses ``simctl listapps -j`` (JSON output) by default; falls back to
        the plist format for older Xcode versions that don't support -j.
    """
    import plistlib
    import json as _json

    device_udid = arguments.get("device_udid", "").strip()
    if not device_udid:
        raise ValueError("device_udid is required — call ios_devices to list booted simulators")

    def _parse_app_dict(app_dict: dict) -> list:
        results = []
        for bundle_id, info in app_dict.items():
            results.append({
                "bundle_id": bundle_id,
                "display_name": info.get("CFBundleDisplayName") or info.get("CFBundleName", ""),
                "version": info.get("CFBundleShortVersionString") or info.get("CFBundleVersion", ""),
                "install_path": info.get("Path", ""),
            })
        results.sort(key=lambda a: a["display_name"].lower())
        return results

    # ── Primary path: JSON (Xcode 14+) ──────────────────────────────────────
    try:
        raw_json = subprocess.check_output(
            ["xcrun", "simctl", "listapps", "-j", device_udid],
            stderr=subprocess.PIPE,
        )
        app_dict = _json.loads(raw_json.decode("utf-8", errors="replace"))
        return _parse_app_dict(app_dict)
    except subprocess.CalledProcessError as exc:
        stderr = exc.stderr.decode("utf-8", errors="replace") if exc.stderr else ""
        if "invalid device" in stderr.lower() or "unable to lookup" in stderr.lower():
            raise ValueError(
                f"No simulator with UDID '{device_udid}'. "
                "Call ios_devices to see booted simulators."
            ) from exc
        # Could be old Xcode that doesn't support -j — fall through to plist path
        logger.debug("simctl listapps -j failed (%s): %s — trying plist fallback", exc.returncode, stderr[:200])
    except (_json.JSONDecodeError, UnicodeDecodeError):
        # Output wasn't JSON — Xcode version may not support -j
        logger.debug("simctl listapps -j did not return JSON — trying plist fallback")
    except Exception as exc:
        raise ValueError(f"simctl listapps failed: {exc}") from exc

    # ── Fallback: plist format (older Xcode) ─────────────────────────────────
    try:
        raw_plist = subprocess.check_output(
            ["xcrun", "simctl", "listapps", device_udid],
            stderr=subprocess.PIPE,
        )
        app_dict = plistlib.loads(raw_plist)
        return _parse_app_dict(app_dict)
    except subprocess.CalledProcessError as exc:
        stderr = exc.stderr.decode("utf-8", errors="replace") if exc.stderr else ""
        raise ValueError(f"simctl listapps failed: {stderr}") from exc
    except Exception as exc:
        return [{"warning": f"Failed to parse app list: {exc}", "bundle_id": "", "display_name": "", "version": "", "install_path": ""}]


def handle_license_status(arguments: dict) -> dict:
    """Report SpecterQA license tier and feature entitlements.

    Returns:
        {"tier": str, "entitlements": dict, "expiry": str|None, "valid": bool}
    """
    try:
        from specterqa.ios.license.validator import LicenseValidator, _DOGFOOD_RESULT, _TRIAL_RESULT

        env_key = os.environ.get("SPECTERQA_IOS_LICENSE", "").strip()
        license_key = os.environ.get("SPECTERQA_LICENSE_KEY", "").strip()

        validator = LicenseValidator(license_key=license_key)
        result = validator.validate()

        tier = result.get("tier", "trial")
        expires_at = result.get("expires_at")
        valid = result.get("valid", False)

        # Map tier to entitlements
        entitlements = {
            "browserstack": tier in ("pro", "team", "enterprise", "founder"),
            "indigo_hid": tier in ("pro", "team", "enterprise", "founder"),
            "multi_sim": tier in ("indie", "pro", "team", "enterprise", "founder"),
            "ci_replay": tier != "trial",
            "max_concurrent_sims": result.get("max_concurrent_sims", 1),
        }

        return {
            "tier": tier,
            "valid": valid,
            "entitlements": entitlements,
            "expiry": expires_at,
        }
    except Exception as exc:
        return {
            "tier": "unknown",
            "valid": False,
            "entitlements": {},
            "expiry": None,
            "error": str(exc),
        }


# ---------------------------------------------------------------------------
# Runner lifecycle handlers (exposed as ios_start_runner / ios_stop_runner)
# ---------------------------------------------------------------------------

# Module-level registry so ios_start_runner / ios_stop_runner can track their
# own processes independently of the session lifecycle.
_standalone_runner_procs: dict[int, Any] = {}  # port → subprocess.Popen


def handle_start_runner(arguments: dict) -> dict:
    """Build (if needed) and deploy the XCTest runner, then wait for health.

    Idempotent — if a runner is already healthy on the requested port, returns
    immediately without spawning a new process.

    Args:
        device_udid: Simulator UDID (required).
        bundle_id:   App bundle ID for runner env injection (optional).
        timeout_s:   Health-poll timeout in seconds (default 90).

    Returns:
        {"status": "ok", "port": <int>, "message": "<str>"}
        or {"error": "<message>"} on failure.
    """
    device_udid = arguments.get("device_udid")
    if not device_udid:
        return {"error": "device_udid is required"}

    bundle_id = arguments.get("bundle_id", "")
    timeout_s = float(arguments.get("timeout_s", 90.0))

    from specterqa.ios.session_manager import (  # noqa: PLC0415
        _find_xctestrun,
        _DEFAULT_RUNNER_BUILD_DIR,
        _wait_for_health,
        TestSession,
    )

    port = 8222

    # Check if runner is already healthy (idempotent).
    try:
        _wait_for_health(f"http://localhost:{port}/health", timeout_s=2.0)
        logger.info("ios_start_runner: runner already healthy on :%d", port)
        return {"status": "ok", "port": port, "message": f"Runner already running on port {port}"}
    except Exception:
        pass  # Not healthy yet — deploy below.

    xctestrun = _find_xctestrun(_DEFAULT_RUNNER_BUILD_DIR)
    if xctestrun is None:
        return {
            "error": (
                "No built xctestrun found. Build the runner first: "
                "specterqa-ios runner build, or call ios_start_session which auto-builds."
            )
        }

    try:
        TestSession._inject_xctestrun_env(
            Path(xctestrun),
            {
                "SPECTERQA_PORT": str(port),
                "SPECTERQA_BUNDLE_ID": bundle_id or "",
            },
        )

        proc = subprocess.Popen(
            [
                "xcodebuild",
                "test-without-building",
                "-xctestrun", str(xctestrun),
                "-destination", f"id={device_udid}",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

        _standalone_runner_procs[port] = proc

        _wait_for_health(f"http://localhost:{port}/health", timeout_s=timeout_s)
        logger.info("ios_start_runner: runner deployed and healthy on :%d", port)
        return {"status": "ok", "port": port, "message": f"Runner deployed and healthy on port {port}"}

    except Exception as exc:
        return {"error": f"Failed to deploy runner: {exc}"}


def handle_stop_runner(arguments: dict) -> dict:
    """Terminate the XCTest runner subprocess on the given port.

    Idempotent — does not error if no runner is running.

    Args:
        port: Port the runner is listening on (default 8222).

    Returns:
        {"status": "ok", "message": "<str>"}
    """
    port = int(arguments.get("port", 8222))

    proc = _standalone_runner_procs.pop(port, None)
    if proc is not None:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
        logger.info("ios_stop_runner: terminated runner on :%d", port)
        return {"status": "ok", "message": f"Runner on port {port} stopped"}

    # Also attempt to stop the session-managed runner if one is active.
    global _session
    if _session is not None and hasattr(_session, "_runner_process"):
        runner_proc = getattr(_session, "_runner_process", None)
        if runner_proc is not None and runner_proc.poll() is None:
            runner_proc.terminate()
            try:
                runner_proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                runner_proc.kill()
            logger.info("ios_stop_runner: terminated session runner on :%d", port)
            return {"status": "ok", "message": f"Session runner on port {port} stopped"}

    return {"status": "ok", "message": f"No active runner found on port {port} (idempotent)"}


# ---------------------------------------------------------------------------
# Phase 2 — AI debugging primitives (v14.0.0b1)
# ---------------------------------------------------------------------------

# Per-call log tail cursor: maps session_id (or "default") → ISO timestamp str
_log_tail_cursors: dict[str, str] = {}


def _restart_runner_for_relaunch(udid: str, bundle_id: str) -> str | None:
    """iOS 26.3 recovery path for ios_app_relaunch.

    When xcodebuild's test lifecycle has shut down the simulator (a behaviour
    unique to iOS 26.3's test-without-building mode), the sim cannot be booted
    while xcodebuild owns it.  The only reliable fix is:

      1. SIGKILL xcodebuild (via _mcp_runner_ref.stop)
      2. xcrun simctl boot <udid>
      3. Re-deploy the xcodebuild runner (RunnerProcess.deploy)
      4. xcrun simctl launch <bundle_id>

    Returns None on success, or an error string on failure.
    Mutates globals: _mcp_runner_ref, _session, _backend.

    A 120s outer timeout caps the entire recovery. If exceeded the runner is
    stopped (via finally) and an error string is returned.
    Only one recovery runs at a time per MCP server — entry is serialised under
    _session_lock.
    """
    global _mcp_runner_ref, _session, _backend  # noqa: PLW0603

    from specterqa.ios.runner_process import RunnerProcess, RunnerState  # noqa: PLC0415
    from specterqa.ios.backends.xctest_client import XCTestBackend  # noqa: PLC0415

    import json as _json_restart  # noqa: PLC0415
    import json as _json_w  # noqa: PLC0415  (used in shutdown poll loop below)

    # Pre-flight: poll runner HTTP health for up to 10s before kicking the
    # expensive 36-42s recovery path. If the runner recovers on its own, skip.
    if _backend is not None:
        _precheck_t0 = time.monotonic()
        _precheck_budget_s = 10.0
        _runner_recovered = False
        _precheck_iter = 0
        while time.monotonic() - _precheck_t0 < _precheck_budget_s:
            _precheck_iter += 1
            _precheck_elapsed = time.monotonic() - _precheck_t0
            try:
                _ph = _backend.health()
                if isinstance(_ph, dict) and _ph.get("status") == "ok":
                    logger.info(
                        "_restart_runner_for_relaunch: pre-flight health check passed "
                        "(runner healthy after %.1fs) — skipping recovery",
                        _precheck_elapsed,
                    )
                    _runner_recovered = True
                    break
                logger.debug(
                    "_restart_runner_for_relaunch: pre-flight iter %d at %.1fs — "
                    "health=%r (not ok); will retry",
                    _precheck_iter, _precheck_elapsed, _ph,
                )
            except Exception as _ph_exc:  # noqa: BLE001
                logger.debug(
                    "_restart_runner_for_relaunch: pre-flight iter %d at %.1fs — "
                    "health raised %s: %s; will retry",
                    _precheck_iter, _precheck_elapsed, type(_ph_exc).__name__, _ph_exc,
                )
            time.sleep(1.0)
        if _runner_recovered:
            return None  # Skip recovery — transient sim-Shutdown signal

    _RECOVERY_TIMEOUT_S = 120
    _t0_recovery = time.monotonic()

    def _wait_for_sim_state(target: str, timeout_s: int = 20) -> bool:
        """Poll until the sim reaches target state. Returns True on success."""
        for _ in range(timeout_s):
            time.sleep(1)
            try:
                rp = subprocess.run(
                    ["xcrun", "simctl", "list", "devices", "--json"],
                    capture_output=True, text=True, timeout=10,
                )
                if rp.returncode == 0:
                    data = _json_restart.loads(rp.stdout)
                    for _rt in data.get("devices", {}).values():
                        for _d in _rt:
                            if _d.get("udid") == udid and _d.get("state") == target:
                                return True
            except Exception:  # noqa: BLE001
                pass
        return False

    def _check_outer_timeout() -> str | None:
        """Return an error string if the 120s recovery ceiling has been exceeded."""
        if time.monotonic() - _t0_recovery > _RECOVERY_TIMEOUT_S:
            return (
                "runner-restart recovery exceeded 120s — simulator may be stuck. "
                "Try: xcrun simctl shutdown all && xcrun simctl erase all"
            )
        return None

    new_runner = None
    with _session_lock:
        try:
            # Step 1: Kill the existing runner (frees the sim from xcodebuild's lifecycle)
            old_runner = _mcp_runner_ref
            if old_runner is not None:
                logger.info("_restart_runner_for_relaunch: stopping old runner on port %d", old_runner._port)
                old_runner.stop(shutdown_sim=False)
                # Give xcodebuild a moment to fully exit and release the sim
                time.sleep(2)

            _tout = _check_outer_timeout()
            if _tout:
                logger.error("_restart_runner_for_relaunch: %s", _tout)
                return _tout

            # Step 2: Re-deploy the runner (xcodebuild will boot sim, install runner, then shut sim down)
            # IMPORTANT: Do NOT boot the sim here — let xcodebuild manage it during its test lifecycle.
            # The sim will be Booted briefly during xcodebuild's test setup, then Shutdown after.
            # We boot AFTER healthcheck when the sim is stably Shutdown and xcodebuild has settled.
            logger.info("_restart_runner_for_relaunch: re-deploying runner for %s on :8222", udid)
            from specterqa.ios.runner_process import RunnerDeployError  # noqa: PLC0415
            new_runner = RunnerProcess.acquire(udid=udid, port=8222)
            new_runner.deploy(bundle_id=bundle_id)
            # Wait for runner to become healthy (mirrors handle_start_session: up to 90s)
            try:
                new_runner.healthcheck(timeout_s=90.0)
                logger.info("_restart_runner_for_relaunch: runner healthy on :8222 (sim will be Shutdown)")
            except RunnerDeployError as he:
                return f"Runner did not become healthy after restart: {he}"

            _tout = _check_outer_timeout()
            if _tout:
                logger.error("_restart_runner_for_relaunch: %s", _tout)
                return _tout

            # Update globals atomically under _session_lock so concurrent MCP calls
            # never observe partial state (e.g. new _backend but old _mcp_runner_ref).
            _mcp_runner_ref = new_runner
            _session = new_runner  # type: ignore[assignment]
            _backend = XCTestBackend(host="localhost", port=8222)

            # Step 3: Wait for sim to reach stable Shutdown AFTER the new xcodebuild's
            # test lifecycle. xcodebuild boots the sim during test setup, then shuts it
            # down after. We must wait for that shutdown to complete before booting
            # the sim ourselves — otherwise our launch races against xcodebuild's teardown.
            logger.info(
                "_restart_runner_for_relaunch: waiting for sim %s to reach stable Shutdown "
                "after xcodebuild test lifecycle (iOS 26.3)",
                udid,
            )
            # First: wait for any "Booted" or "Shutting Down" to complete → sim reaches Shutdown.
            # Poll for up to 30s.
            for _w in range(30):
                time.sleep(1)
                _tout = _check_outer_timeout()
                if _tout:
                    logger.error("_restart_runner_for_relaunch: %s", _tout)
                    return _tout
                try:
                    rp = subprocess.run(
                        ["xcrun", "simctl", "list", "devices", "--json"],
                        capture_output=True, text=True, timeout=10,
                    )
                    if rp.returncode == 0:
                        data = _json_w.loads(rp.stdout)
                        for _rt in data.get("devices", {}).values():
                            for _d in _rt:
                                if _d.get("udid") == udid and _d.get("state") == "Shutdown":
                                    break
                            else:
                                continue
                            break
                        else:
                            continue
                        # sim is Shutdown — give it an extra 2s for xcodebuild teardown to stabilize
                        time.sleep(2)
                        break
                except Exception:  # noqa: BLE001
                    pass

            _tout = _check_outer_timeout()
            if _tout:
                logger.error("_restart_runner_for_relaunch: %s", _tout)
                return _tout

            # Now boot the sim — xcodebuild has completed its test lifecycle, sim is Shutdown.
            logger.info("_restart_runner_for_relaunch: booting sim %s (post-xcodebuild-lifecycle)", udid)
            boot_r = subprocess.run(
                ["xcrun", "simctl", "boot", udid],
                capture_output=True, text=True, timeout=30,
            )
            if boot_r.returncode != 0 and "Unable to boot device in current state: Booted" not in boot_r.stderr:
                return f"simctl boot failed during relaunch recovery: {boot_r.stderr.strip()}"

            if not _wait_for_sim_state("Booted", timeout_s=30):
                return f"Simulator {udid} did not reach Booted within 30s after runner re-deploy"

            _tout = _check_outer_timeout()
            if _tout:
                logger.error("_restart_runner_for_relaunch: %s", _tout)
                return _tout

            logger.info("_restart_runner_for_relaunch: sim Booted — launching app %s", bundle_id)

            # Step 4: Launch the app
            time.sleep(1)  # brief settle before simctl commands
            subprocess.run(
                ["xcrun", "simctl", "terminate", udid, bundle_id],
                capture_output=True, text=True, timeout=10,
            )
            r = subprocess.run(
                ["xcrun", "simctl", "launch", udid, bundle_id],
                capture_output=True, text=True, timeout=30,
            )
            if r.returncode != 0:
                return f"simctl launch failed after runner restart: {r.stderr.strip()}"

            logger.info("_restart_runner_for_relaunch: app %s launched successfully", bundle_id)
            return None

        except Exception as exc:  # noqa: BLE001
            logger.exception("_restart_runner_for_relaunch failed: %s", exc)
            return f"Runner restart failed: {exc}"
        finally:
            # If the recovery overran or raised, ensure the runner registry is clean.
            if time.monotonic() - _t0_recovery > _RECOVERY_TIMEOUT_S and new_runner is not None:
                try:
                    new_runner.stop()
                except Exception:  # noqa: BLE001
                    pass


def handle_app_relaunch(arguments: dict) -> dict:
    """Terminate and relaunch the app under test.

    Without app_path: xcrun simctl terminate + launch (<2s).
    With app_path: xcrun simctl install + terminate + launch (~15s).
    The RunnerProcess is NOT torn down — only the user app restarts.

    Args:
        bundle_id: App bundle ID (required).
        app_path:  Path to .app bundle; triggers reinstall when provided.
        udid:      Simulator UDID (defaults to "booted").
        session_id: Ignored (single-session design) — reserved for future multi-session.

    Returns:
        {bundle_id, udid, elapsed_ms, foreground_verified, mode}
        or {"error": "<message>"} on failure.

        recovery: "runner-restart" when Shutdown-mid-session was detected and full
        runner recreation was needed; absent on happy path. Callers should expect
        ~30-45s on the recovery path vs <2s on the happy path.
    """
    if _backend is None:
        return {"error": "No active session. Call ios_start_session first."}

    bundle_id = str(arguments.get("bundle_id", "")).strip()
    if not bundle_id:
        return {"error": "'bundle_id' is required"}

    _raw_app_path = arguments.get("app_path")
    app_path = str(_raw_app_path).strip() if (_raw_app_path is not None and str(_raw_app_path).strip() not in ("", "None")) else None
    # Use the session-resolved UDID when available (avoids "booted" lookup failure
    # when iOS 26.3 briefly shuts down the sim during xcodebuild test lifecycle).
    udid_arg = str(arguments.get("udid", "booted")).strip() or "booted"
    udid = _session_udid if (_session_udid and udid_arg == "booted") else udid_arg
    mode = "reinstall-launch" if app_path else "terminate-launch"

    def _ensure_sim_booted(target_udid: str) -> bool:
        """Return True if already booted; attempt xcrun simctl boot and return True on success.

        Handles iOS 26.3 xcodebuild test lifecycle which leaves the simulator in
        "Shutting Down" or "Shutdown" state between MCP tool calls. We:
          1. Poll until Shutting Down completes (up to 10s)
          2. Issue xcrun simctl boot
          3. Poll until Booted (up to 20s)
        """
        import json as _json

        def _get_state() -> str:
            try:
                rp = subprocess.run(
                    ["xcrun", "simctl", "list", "devices", "--json"],
                    capture_output=True, text=True, timeout=10,
                )
                if rp.returncode == 0:
                    data = _json.loads(rp.stdout)
                    for _rt in data.get("devices", {}).values():
                        for _d in _rt:
                            if _d.get("udid") == target_udid:
                                return _d.get("state", "Unknown")
            except Exception:  # noqa: BLE001
                pass
            return "Unknown"

        try:
            state = _get_state()
            if state == "Booted":
                return True

            # Wait for "Shutting Down" to complete before booting
            if state == "Shutting Down":
                logger.info("handle_app_relaunch: sim %s is Shutting Down — waiting for completion", target_udid)
                for _ in range(15):
                    time.sleep(1)
                    state = _get_state()
                    if state == "Shutdown":
                        break
                    if state == "Booted":
                        return True

            logger.info("handle_app_relaunch: sim %s in state %r — attempting xcrun simctl boot", target_udid, state)
            boot_r = subprocess.run(
                ["xcrun", "simctl", "boot", target_udid],
                capture_output=True, text=True, timeout=30,
            )
            if boot_r.returncode != 0 and "Unable to boot device in current state: Booted" not in boot_r.stderr:
                logger.warning("simctl boot failed: %s", boot_r.stderr.strip())
                return False
            # Wait up to 20s for Booted state
            for _ in range(20):
                time.sleep(1)
                if _get_state() == "Booted":
                    logger.info("handle_app_relaunch: sim %s is Booted (recovery complete)", target_udid)
                    return True
            logger.warning("handle_app_relaunch: sim %s did not reach Booted within 20s", target_udid)
            return False
        except Exception as exc:  # noqa: BLE001
            logger.warning("_ensure_sim_booted error: %s", exc)
            return False

    t0 = time.monotonic()
    try:
        if app_path:
            # Before install, ensure sim is booted (iOS 26.3 teardown recovery)
            if udid != "booted":
                _ensure_sim_booted(udid)
            r = subprocess.run(
                ["xcrun", "simctl", "install", udid, app_path],
                capture_output=True, text=True, timeout=60,
            )
            if r.returncode != 0:
                # Last-chance: maybe sim just needed a moment — reboot and retry once
                _install_shutdown = any(ind in r.stderr for ind in (
                    "No devices are booted", "Unable to lookup in current state: Shutdown", "current state: Shutdown",
                ))
                if _install_shutdown and udid != "booted":
                    if _ensure_sim_booted(udid):
                        r = subprocess.run(
                            ["xcrun", "simctl", "install", udid, app_path],
                            capture_output=True, text=True, timeout=60,
                        )
                if r.returncode != 0:
                    return {"error": f"simctl install failed: {r.stderr.strip()}"}

        # iOS 26.3: proactively check if simulator is booted before terminate+launch.
        # xcodebuild test lifecycle shuts the sim down but keeps the HTTP server alive.
        # When the sim is shutdown AND xcodebuild is alive, boot will silently fail
        # (rc=0 but sim never reaches Booted — xcodebuild keeps it down).
        # The only reliable recovery is: stop xcodebuild → boot sim → re-deploy.
        _needs_restart = False
        if udid != "booted":
            _sim_state_now = "Unknown"
            try:
                import json as _json_check
                _rsc = subprocess.run(
                    ["xcrun", "simctl", "list", "devices", "--json"],
                    capture_output=True, text=True, timeout=10,
                )
                if _rsc.returncode == 0:
                    _data_check = _json_check.loads(_rsc.stdout)
                    for _rt in _data_check.get("devices", {}).values():
                        for _d in _rt:
                            if _d.get("udid") == udid:
                                _sim_state_now = _d.get("state", "Unknown")
            except Exception:  # noqa: BLE001
                pass

            if _sim_state_now in ("Shutdown", "Shutting Down"):
                # Check if xcodebuild is alive — if so, we cannot boot via simctl alone
                _xc_alive = subprocess.run(
                    ["pgrep", "-f", f"xcodebuild.*{udid}"],
                    capture_output=True, text=True,
                ).stdout.strip()
                if _xc_alive:
                    logger.info(
                        "handle_app_relaunch: sim %s is %s and xcodebuild (%s) is alive "
                        "— using runner-restart recovery",
                        udid, _sim_state_now, _xc_alive,
                    )
                    _needs_restart = True
                else:
                    # No xcodebuild — normal _ensure_sim_booted is safe
                    if not _ensure_sim_booted(udid):
                        _needs_restart = True

        if _needs_restart:
            # _ensure_sim_booted returned False — sim is held down by xcodebuild.
            # Recovery: kill xcodebuild, boot sim, re-deploy runner, then launch.
            logger.info(
                "handle_app_relaunch: sim %s is held down by xcodebuild — "
                "stopping runner, rebooting sim, and re-deploying",
                udid,
            )
            recovery_err = _restart_runner_for_relaunch(udid, bundle_id)
            if recovery_err:
                return {"error": recovery_err}
            # Runner re-deployed and app launched — build the success result
            elapsed_ms = (time.monotonic() - t0) * 1000
            foreground_verified = False
            try:
                if _backend is not None:
                    state_result = _backend.app_state()
                    foreground_verified = str(state_result.get("state", "")).lower() == "foreground"
            except Exception:
                foreground_verified = False
            result: dict = {
                "bundle_id": bundle_id,
                "udid": udid,
                "elapsed_ms": int(elapsed_ms),
                "foreground_verified": foreground_verified,
                "mode": mode,
                "recovery": "runner-restart",
            }
            return result

        # terminate (ignore errors — app may not be running)
        subprocess.run(
            ["xcrun", "simctl", "terminate", udid, bundle_id],
            capture_output=True, text=True, timeout=10,
        )

        r = subprocess.run(
            ["xcrun", "simctl", "launch", udid, bundle_id],
            capture_output=True, text=True, timeout=30,
        )
        if r.returncode != 0:
            return {"error": f"simctl launch failed: {r.stderr.strip()}"}

        elapsed_ms = (time.monotonic() - t0) * 1000

        # Verify foreground — try backend.app_state(), do not hang
        foreground_verified = False
        try:
            state_result = _backend.app_state()
            foreground_verified = str(state_result.get("state", "")).lower() == "foreground"
        except Exception:
            foreground_verified = False

        result: dict = {
            "bundle_id": bundle_id,
            "udid": udid,
            "elapsed_ms": int(elapsed_ms),
            "foreground_verified": foreground_verified,
            "mode": mode,
        }
        if mode == "reinstall-launch" and elapsed_ms > 20000:
            result["slow_warning"] = True
            result["warn"] = f"Reinstall took {elapsed_ms:.0f}ms (>20s) — check .app bundle size"
        return result

    except subprocess.TimeoutExpired as exc:
        return {"error": f"Command timed out: {exc}"}
    except Exception as exc:
        return {"error": f"app_relaunch failed: {exc}"}


def handle_logs_tail(arguments: dict) -> dict:
    """Return incremental logs since the last call for this session.

    Maintains a per-session cursor (ISO timestamp). First call returns
    the last 2 seconds of logs as the initial boundary.

    Args:
        since_last_call: If True (default), return only logs after the cursor.
                         If False, return recent logs ignoring the cursor.
        level:    Optional log level filter (e.g. "error", "fault").
        category: Optional category filter.
        regex:    Optional regex pattern applied to message field.
        session_id: Cursor namespace (default "default" — single-session design).

    Returns:
        {logs: [...], cursor: "<ISO timestamp>", since_ms: <int>}
        or {"error": "<message>"} on failure.
    """
    if _console_monitor is None:
        return {"error": "No active session or console monitor not started. Call ios_start_session first."}

    session_id = str(arguments.get("session_id", "default") or "default")
    since_last_call = arguments.get("since_last_call", True)
    level = arguments.get("level")
    category = arguments.get("category")
    regex = arguments.get("regex")

    t0 = time.monotonic()

    try:
        # Fetch raw entries
        if regex:
            entries = _console_monitor.search(regex)
        elif level and str(level).lower() in ("error", "fault"):
            entries = _console_monitor.errors(seconds=30)
        else:
            seconds = 30.0
            entries = _console_monitor.recent(seconds=seconds, level=level, category=category)

        # Cursor filtering
        cursor = _log_tail_cursors.get(session_id)
        if since_last_call and cursor:
            # Keep only entries strictly after the cursor timestamp
            filtered = []
            for e in entries:
                ts = str(getattr(e, "timestamp", ""))
                if ts > cursor:
                    filtered.append(e)
            entries = filtered
        elif since_last_call and cursor is None:
            # First call — apply a 2s window
            entries = entries[-50:]  # Last 50 entries as a reasonable first-call boundary

        # Build log list
        logs = []
        latest_ts = cursor or ""
        for e in entries[-100:]:
            ts = str(getattr(e, "timestamp", ""))
            logs.append({
                "timestamp": ts,
                "level": getattr(e, "level", ""),
                "subsystem": getattr(e, "subsystem", ""),
                "category": getattr(e, "category", ""),
                "message": getattr(e, "message", ""),
                "process": getattr(e, "process", ""),
            })
            if ts > latest_ts:
                latest_ts = ts

        # Update cursor to latest observed timestamp (or now)
        import datetime
        now_iso = datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
        new_cursor = latest_ts if latest_ts else now_iso
        _log_tail_cursors[session_id] = new_cursor

        since_ms = int((time.monotonic() - t0) * 1000)

        return {
            "logs": logs,
            "cursor": new_cursor,
            "since_ms": since_ms,
            "count": len(logs),
        }
    except Exception as exc:
        return {"error": f"logs_tail failed: {exc}"}


def handle_promote_session_to_test(arguments: dict) -> dict:
    """Promote the current recording buffer to a named test replay YAML.

    Writes the replay, auto-validates it, and returns validation status.
    On validation failure the file is kept (not deleted) so the agent can iterate.

    Args:
        name:      Test name used as the filename stem (required).
        path:      Override save path; defaults to ./replays/<name>.yaml (per OQ-3).
        session_id: Ignored (reserved for future multi-session).

    Returns:
        {saved_to, validation: "passed"|"failed", steps, can_replay, errors?}
        or {"error": "<message>"} on failure.
    """
    if _recorder is None:
        return {"error": "No active recording. Call ios_start_session first."}

    name = str(arguments.get("name", "")).strip()
    if not name:
        return {"error": "'name' is required"}

    import re as _re
    _SAFE_NAME_RE = _re.compile(r'^[a-zA-Z0-9._-]+$')
    if not _SAFE_NAME_RE.match(name):
        return {"error": "name must match [a-zA-Z0-9._-]+"}
    if name.startswith("."):
        return {"error": "name must match [a-zA-Z0-9._-]+"}

    _path_arg = arguments.get("path")
    path_override = _path_arg.strip() if isinstance(_path_arg, str) and _path_arg.strip() else None

    if path_override is not None:
        # Resolve against cwd and reject if it escapes
        try:
            resolved = Path(path_override).resolve()
            cwd = Path.cwd().resolve()
            resolved.relative_to(cwd)
        except ValueError:
            return {"error": "resolved path escapes the working directory"}

    save_path = path_override or f"./replays/{name}.yaml"

    try:
        # Ensure parent directory exists
        Path(save_path).parent.mkdir(parents=True, exist_ok=True)
        saved = _recorder.save(save_path, name=name)
        saved_str = str(saved)
    except Exception as exc:
        return {"error": f"Failed to save replay: {exc}"}

    # Auto-validate the written file
    validation_result = handle_validate_replay({"name": saved_str})

    if validation_result.get("valid"):
        return {
            "saved_to": saved_str,
            "validation": "passed",
            "steps": validation_result.get("step_count", len(_recorder.session.steps)),
            "can_replay": True,
        }
    else:
        return {
            "saved_to": saved_str,
            "validation": "failed",
            "steps": validation_result.get("step_count", len(_recorder.session.steps)),
            "can_replay": False,
            "errors": validation_result.get("issues", []),
        }


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

AVAILABLE TOOLS (35 total):

  Session lifecycle:
    ios_start_session    — Deploy XCTest runner; launch the app (required first step)
    ios_stop_session     — Stop runner and clean up (always call when done)

  Vision-first primitives (v16.0.0 — the recommended driving surface):
    ios_observe          — Screenshot + reliable_targets (elements with explicit
                            accessibilityIdentifier) + device_w/h + app_state.
                            Replaces all v15.x screenshot/element-list tools.
    ios_act              — Unified action verb. action.kind ∈ {tap, type, swipe,
                            key, scroll, long_press, drag}. Coordinate-primary;
                            identifier optional on tap/long_press for elements
                            with explicit accessibilityIdentifier. Replaces all
                            v15.x selector-based interaction tools.

  Lifecycle / state:
    ios_app_state        — Check app lifecycle state (foreground/background/suspended)
    ios_dismiss_sheet    — Dismiss a sheet/modal by swiping down

  Recording & Replay (recording-rewrite pending in v16.0.0a2):
    ios_start_recording  — Clear step buffer; begin clean recording
    ios_stop_recording   — Save replay YAML + clear buffer (end of flow); preferred save path
    ios_list_replays     — List saved replay YAML files (name, steps, modified). Call before ios_replay.
    ios_replay           — Run a saved replay end-to-end against the active session.
    ios_validate_replay  — Validate replay structure without executing it.

  Environment Discovery:
    ios_doctor           — Check environment readiness: Xcode, sims, runner build. Call when sessions fail.
    ios_devices          — List booted simulators (UDID, name, runtime). Use to pick device_udid.
    ios_apps             — List apps installed on a simulator. Use to find bundle_id.
    ios_license_status   — Report license tier and feature entitlements.

  Quality & Diagnostics:
    ios_accessibility_audit        — Audit for missing labels, small targets, duplicate labels
    ios_set_appearance             — Toggle dark/light mode on the simulator
    ios_simctl                     — Run arbitrary xcrun simctl subcommand
    ios_webview_elements           — Query elements inside WKWebView (EPUB, PDF, audiobook UI)
    ios_logs                       — Get recent app console logs (filterable by level, category, regex)
    ios_crashes                    — Check for app crashes since session start (parses .ips files)
    ios_pre_grant_permissions      — Pre-grant system permissions via simctl (call before ios_start_session)
    ios_dismiss_springboard_alert  — Dismiss a SpringBoard alert (requires backend='ax')

  Performance & Network Monitoring:
    ios_perf                — CPU %, RSS memory, thread count snapshot (call periodically for regression detection)
    ios_memory              — Detailed memory breakdown: footprint, dirty, swapped, clean pages
    ios_network             — Network activity: recent HTTP URLs, bytes in/out, throughput
    ios_perf_baseline       — Capture a perf snapshot as a reference baseline for comparison
    ios_perf_compare        — Compare current perf to the stored baseline (deltas + severity)

  AI Debugging Primitives (v14.0.0b1):
    ios_app_relaunch        — Restart the app without tearing down the runner (fast debug cycle)
    ios_logs_tail           — Incremental logs since last call (cursor-based, use in debug loops)
    ios_promote_session_to_test — Promote recording buffer to a named replay YAML + auto-validate

FIRST SESSION — minimum viable loop (v16.0.0a1):
  ios_start_session(bundle_id="com.example.app")
  → ios_observe()                              # screenshot + reliable_targets
  → ios_act({"kind": "tap", "x": 195, "y": 337})
  → ios_observe()                              # verify state
  → ios_stop_session()

BACKEND SELECTION (ios_start_session backend= param):
- Default to backend="xctest" for comprehensive element trees, typing into forms, and navigating .sheet-presented UIKit content.
- Use backend="ax" when startup speed matters and you only need tap-by-label on root-level elements. Note: AX does not enumerate .sheet-presented UIKit content.
- backend="auto" (default) uses AX if available, falls back to XCTest.

WAITING (v16.0.0a1):
- Poll ios_observe() in a loop until the screen reflects the expected state.
  The vision-first model pulls waiting into the agent's reasoning loop —
  it's the agent that decides "still loading" vs "ready", not a selector.

PERFORMANCE TESTING:
1. Call ios_perf_baseline() at app launch — this is your BASELINE
2. Perform the user flow you're testing
3. Call ios_perf_compare() — compare RSS and CPU to baseline
4. Repeat the flow 3-5 times, calling ios_perf_compare after each iteration
5. If RSS grows monotonically (never decreases), you have a MEMORY LEAK

Run ios_perf_baseline + ios_perf_compare when the user says "check for leaks", "profile this flow", or "make sure nothing regressed".

INTERPRETING ios_perf():
- memory_rss_mb: Physical RAM used. <100MB = good, 100-200MB = normal, >300MB = investigate, >500MB = critical
- thread_count: Active threads. <20 = normal, >50 = thread leak
- cpu_time_total_sec: Cumulative CPU. Compare deltas between calls — >2s delta for a simple action = perf issue

INTERPRETING ios_memory():
- dirty_mb: Memory that cannot be reclaimed. High dirty = app is caching too much
- swapped_mb: Memory pushed to compressed storage. >50MB = memory pressure

CRASH RECOVERY — capture diagnostics BEFORE restarting:
If you see {"error": "Session crashed..."} or unexpected blank screens:
  1. ios_crashes() — capture the crash report (exception type, backtrace)
  2. ios_logs(level="error") — capture error logs
  THEN restart. If you restart first, the .ips crash file is overwritten and diagnostics are lost.

DEBUGGING:
- ios_logs(level="error") — check for errors after unexpected behavior
- ios_crashes() — check if the app crashed (app_running=false means crash)
- ios_network() — check recent HTTP requests if the app seems stuck
- ios_app_state() — verify the app is in foreground

TYPING INTO FORMS (v16.0.0a1):
- ios_act({"kind": "tap", "x": <field_x>, "y": <field_y>}) to focus
- ios_act({"kind": "type", "text": "value"}) types into the focused field
- After typing, call ios_observe() to verify the screen reflects the value

COMMON PITFALLS:
- Keyboard covers buttons: tap the iOS dismiss-keyboard glyph or send a Return
  via ios_act({"kind": "key", "name": "return"})
- Tab bar covered: same dismissal — keyboard goes via ios_act, not a dedicated tool
- Stale screen state after navigation: call ios_observe() to refresh
- SecureField value masked: SecureField shows bullet characters (•), not the actual text

RECORDING WORKFLOW (best practice):
  1. ios_start_session → exploratory taps to find the right flow
  2. ios_start_recording() → clears exploratory steps
  3. Execute the clean, successful flow (tap, type, etc.)
  4. ios_stop_recording(name="feature-name") → saves YAML + clears buffer
  5. Next flow: ios_start_recording() → repeat

  Recording — when to use which:
  - ios_stop_recording(name=...) — end of flow, save + clear buffer (most common)
  - ios_start_recording() — discard exploratory steps, start a clean buffer

LIMITATIONS:
- Physical device support is under development — MCP sessions currently target iOS Simulators only
- For real-device profiling, use Apple's xctrace directly from the terminal:
    xcrun xctrace record --device <UDID> --template 'Leaks' --attach <PID> --time-limit 30s --output /tmp/profile.trace
  This gives allocation graphs, leak detection, CPU flame graphs that ios_perf cannot match.
- ios_perf/ios_perf_compare give lightweight snapshots useful for regression detection, NOT production profiling

PROVIDERS:
- Local simulator (default) — requires macOS + Xcode 15+
- BrowserStack (auto-detected) — set BROWSERSTACK_USERNAME + BROWSERSTACK_ACCESS_KEY
- CI replay — specterqa-ios ci .specterqa/replays/ --json-output results.json

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
            "Start a SpecterQA session on an iOS Simulator or physical device. "
            "bundle_id is the app's CFBundleIdentifier (required). "
            "device_id defaults to 'booted' (uses the currently booted simulator). "
            "app_path is an optional path to a .app bundle to install before starting. "
            "license_key is optional — omit for trial mode or set to 'founder'. "
            "backend controls the automation engine: "
            "'auto' (default) uses the AXUIElement host-side backend when available "
            "and falls back to the XCTest runner; "
            "'xctest' (recommended for most flows) — comprehensive element trees, reliable typing, "
            "and .sheet-presented UIKit content; "
            "'ax' — instant start, no runner deployment, tap-by-label on root-level elements only; "
            "note AX does not enumerate .sheet-presented UIKit content — use xctest for those flows. "
            "device_type: 'simulator' (default, fully supported) | 'physical' (experimental opt-in). "
            "To enable physical device support run 'specterqa-ios mcp enable-physical' or set "
            "SPECTERQA_ALLOW_PHYSICAL_DEVICE=1 and pass device_type='physical'. "
            "wait: bool = True (default). When False, returns immediately with "
            "{status: 'deploying', deploy_id, health_url, estimated_ready_in_s} — "
            "then poll ios_wait_for_session(deploy_id) to wait for healthy status. "
            "auto_recover: bool = False. When True, automatically boots the sim and "
            "re-deploys the runner if a simulator shutdown is detected during the session. "
            "sim_settle_timeout: float = 10.0. Smart settle wait when the sim just booted — "
            "sleeps only the remaining delta (e.g. if sim booted 3s ago, waits 7s). "
            "Set to 0 to disable. Has no effect when sim has been booted >sim_settle_timeout seconds."
        ),
    )
    async def ios_start_session(
        bundle_id: str,
        device_id: str = "booted",
        app_path: str | None = None,
        license_key: str | None = None,
        clone: bool = False,
        backend: str = "auto",
        device_type: str = "simulator",
        wait: bool = True,
        auto_recover: bool = False,
        sim_settle_timeout: float = 10.0,
    ) -> str:
        arguments = {
            "bundle_id": bundle_id,
            "device_id": device_id,
            "app_path": app_path,
            "license_key": license_key or "",
            "clone": clone,
            "device_type": device_type,
            "backend": backend,
            "wait": wait,
            "auto_recover": auto_recover,
            "sim_settle_timeout": sim_settle_timeout,
        }

        if not wait:
            # Async path: kick off deploy in a background thread.
            deploy_id = str(_uuid.uuid4())
            _set_deploy_state("deploying", deploy_id=deploy_id, udid=device_id)
            arguments["_deploy_id"] = deploy_id
            t = threading.Thread(target=_background_deploy, args=(arguments,), daemon=True)
            t.start()
            return json.dumps({
                "status": "deploying",
                "deploy_id": deploy_id,
                "health_url": "http://localhost:8222/health",
                "estimated_ready_in_s": 45,
            })

        result = handle_start_session(arguments)
        return json.dumps(result)

    # ── Tool: ios_stop_session ─────────────────────────────────────────────

    @mcp.tool(
        name="ios_stop_session",
        description=("Stop the XCTest runner and clean up. Call this when testing is complete."),
    )
    async def ios_stop_session() -> str:
        result = handle_stop_session({})
        return json.dumps(result)

    # ── Tool: ios_get_capabilities ─────────────────────────────────────────

    @mcp.tool(
        name="ios_get_capabilities",
        description=(
            "Introspect SpecterQA capabilities: version, supported backends, and available "
            "device types. Use this before ios_start_session to discover what device targets "
            "are available. Physical device support is experimental and requires opting in via "
            "SPECTERQA_ALLOW_PHYSICAL_DEVICE=1 — this tool shows whether that gate is open."
        ),
    )
    async def ios_get_capabilities() -> str:
        import specterqa.ios as _pkg
        from specterqa.ios.config import _check_physical_opt_in  # noqa: PLC0415
        _version = getattr(_pkg, "__version__", "15.0.0")
        _opt_in = _check_physical_opt_in()
        _physical_enabled = _opt_in["allowed"]
        caps = {
            "version": _version,
            "backends": ["xctest", "ax"],
            "device_types": [
                {
                    "type": "simulator",
                    "available": True,
                    "default": True,
                    "experimental": False,
                },
                {
                    "type": "physical",
                    "available": True,
                    "default": False,
                    "experimental": True,
                    "opt_in_env": "SPECTERQA_ALLOW_PHYSICAL_DEVICE",
                    "opt_in_active": _physical_enabled,
                    "diagnostics": _opt_in["diagnostics"],
                    "notes": (
                        "Requires SPECTERQA_ALLOW_PHYSICAL_DEVICE=1 OR run "
                        "'specterqa-ios mcp enable-physical'. "
                        "Known xcodebuild issues on iOS 26 may cause flakiness."
                    ),
                },
            ],
            "tool_count": 35,
        }
        return json.dumps(caps)

    # ── v16.0.0 vision-first primitives: ios_observe + ios_act ────────────
    #
    # These are the recommended entry points for vision-capable agents (Claude,
    # GPT-4V, Gemini, etc.). The legacy tools below (ios_screenshot, ios_tap,
    # ios_elements, ios_wait_for_element, ...) are scheduled for removal — they
    # depend on the XCUIElementQuery selector layer that's lossy/brittle/crash-
    # prone on iOS 26.x SwiftUI per Maurice's v15.x dogfood. See
    # `.specterqa/dogfood/v15.2.0-direction-proposal-maurice.md` for the
    # strategic rationale.

    @mcp.tool(
        name="ios_observe",
        description=(
            "VISION-FIRST OBSERVATION (v16.0.0a3). Returns: screenshot_path "
            "(/tmp/specterqa-observe-<uuid>.jpg — read it with your file-read "
            "tool to get the image into vision input), device_w/device_h "
            "(LOGICAL POINTS — use these for ios_act coordinates), "
            "screenshot_w/screenshot_h (pixel dims of the saved JPEG), "
            "reliable_targets (only elements with explicit "
            "accessibilityIdentifier — by-construction unique and stable), "
            "app_state, captured_at. "
            "v16.0.0a3 changes from a1/a2: screenshot is delivered as a file "
            "path, not inline base64 — fits the MCP envelope at every quality "
            "level. device_w/device_h are now LOGICAL POINTS (not pixels) and "
            "match the runner's coord space. "
            "quality: 'standard' (50%, default), 'full' (no resize), 'thumbnail' (25%). "
            "include_legacy_elements=True also returns the v15.x-style filtered "
            "element list (transition compatibility; default False)."
        ),
    )
    @require_tier("trial")
    async def ios_observe(
        quality: str = "standard",
        include_legacy_elements: bool = False,
    ) -> str:
        result = handle_observe({
            "quality": quality,
            "include_legacy_elements": include_legacy_elements,
        })
        return json.dumps(result, default=_json_serialize)

    @mcp.tool(
        name="ios_act",
        description=(
            "VISION-FIRST UNIFIED ACTION (v16.0.0). Single dispatcher for "
            "tap / type / swipe / key / scroll / long_press / drag. All actions "
            "are coordinate-primary: the agent picks (x, y) from the ios_observe "
            "screenshot. identifier is allowed on tap/long_press as an opt-in "
            "semantic helper for elements with explicit accessibilityIdentifier "
            "(the reliable_targets shape). Label-based selectors are NOT "
            "supported — they're the v15.x crash class that v16 deletes.\n"
            "action shapes:\n"
            "  {kind: 'tap',        x, y}\n"
            "  {kind: 'tap',        identifier}\n"
            "  {kind: 'long_press', x, y, duration_s?}\n"
            "  {kind: 'long_press', identifier, duration_s?}\n"
            "  {kind: 'type',       text, x?, y?}\n"
            "  {kind: 'swipe',      from: [x, y], to: [x, y], duration_ms?}\n"
            "  {kind: 'drag',       from: [x, y], to: [x, y], duration_ms?}\n"
            "  {kind: 'key',        name}\n"
            "  {kind: 'scroll',     direction: up|down|left|right, x?, y?}\n"
            "normalized=true treats x/y in [0.0, 1.0] as fractions of device dims."
        ),
    )
    @require_tier("trial")
    async def ios_act(action: dict, normalized: bool = False) -> str:
        result = handle_act({"action": action, "normalized": normalized})
        return json.dumps(result, default=_json_serialize)


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
    @require_tier("indie")
    async def ios_start_recording() -> str:
        result = handle_start_recording({})
        return json.dumps(result)

    # ── Tool: ios_stop_recording ───────────────────────────────────────────

    @mcp.tool(
        name="ios_stop_recording",
        description=(
            "Save the current recording as a replay YAML file AND clear the step buffer. "
            "name is the test name / filename stem (default 'replay'). "
            "path overrides the output location (default: .specterqa/replays/<name>.yaml). "
            "Internally awaits checkpoint completion before saving (prevents stale expect_elements)."
        ),
    )
    @require_tier("indie")
    async def ios_stop_recording(name: str = "replay", path: str = "") -> str:
        # B8 hardening (v13.3.0): await checkpoint-settling before saving.
        # The 300ms _auto_checkpoint window may not have completed if the caller
        # invokes ios_stop_recording immediately after the last action.
        # Calling ios_wait_idle first ensures the final step's expect_elements
        # captures the post-action screen state, not a stale snapshot.
        if _recorder is not None and _annotator is not None:
            try:
                import asyncio  # noqa: PLC0415

                # Give the event loop a tick to let any in-flight checkpoint flush.
                await asyncio.sleep(0.0)
                # Then let the UI settle (mirrors what ios_wait_idle does).
                time.sleep(0.35)  # slightly longer than _auto_checkpoint's 300ms sleep
            except Exception:  # noqa: BLE001
                pass
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
    @require_tier("pro")
    async def ios_accessibility_audit() -> str:
        result = handle_accessibility_audit({})
        return json.dumps(result)

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
    @require_tier("indie")
    async def ios_set_appearance(mode: str = "dark") -> str:
        result = handle_set_appearance({"mode": mode})
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
    @require_tier("indie")
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
    @require_tier("indie")
    async def ios_webview_elements() -> str:
        try:
            result = handle_webview_elements({})
        except RuntimeError as exc:
            result = {"error": str(exc)}
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
        result = _retry_once_on_transient(handle_app_state, {})
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
    @require_tier("pro")
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
    @require_tier("pro")
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
    @require_tier("pro")
    async def ios_network(seconds: float = 30.0) -> str:
        result = handle_network({"seconds": seconds})
        return json.dumps(result, default=str)

    # ── Tool: ios_perf_baseline ────────────────────────────────────────────

    @mcp.tool(
        name="ios_perf_baseline",
        description=(
            "Capture current CPU, memory, and thread metrics as a baseline. "
            "Call this BEFORE running the user flow you want to measure. "
            "Then call ios_perf_compare after to see the impact. "
            "Requires an active session (ios_start_session)."
        ),
    )
    @require_tier("pro")
    async def ios_perf_baseline() -> str:
        result = handle_perf_baseline({})
        return json.dumps(result, default=str)

    # ── Tool: ios_perf_compare ─────────────────────────────────────────────

    @mcp.tool(
        name="ios_perf_compare",
        description=(
            "Compare current performance metrics against the baseline captured by ios_perf_baseline. "
            "Returns deltas for RSS memory, CPU time, and thread count, plus a severity assessment. "
            "verdict=ISSUES_FOUND means at least one HIGH-severity issue was detected — investigate immediately. "
            "verdict=OK means metrics are within normal range. "
            "HIGH: RSS grew >50MB or >10 new threads. "
            "MEDIUM: RSS grew >20MB or >5s CPU time consumed. "
            "Use after completing the user flow you are measuring. "
            "Requires ios_perf_baseline to have been called first."
        ),
    )
    @require_tier("pro")
    async def ios_perf_compare() -> str:
        result = handle_perf_compare({})
        return json.dumps(result, default=str)

    # ── Tool: ios_dismiss_springboard_alert ────────────────────────────────
    #  Example Reader dogfood Issue 3 / Task #17

    @mcp.tool(
        name="ios_dismiss_springboard_alert",
        description=(
            "Dismiss a SpringBoard-level iOS system permission alert by tapping a named button. "
            "Use when a permission prompt (Notifications, Location, Camera, Bluetooth, etc.) "
            "appears on screen and ios_elements() doesn't show its buttons. "
            "Pass label='Allow' (default), \"Don't Allow\", 'While Using App', or 'Only This Time'. "
            "The tool walks all Simulator AX windows — including the SpringBoard alert window "
            "that sits above the app — and presses the matching button via AX action or CGEvent tap. "
            "IMPORTANT: On iOS 18.4, SpringBoard 'notifications' alerts cannot be dismissed "
            "programmatically via simctl. Call ios_pre_grant_permissions() BEFORE launching the "
            "app as a workaround (works on iOS 17.x; iOS 18.4 notifications are OS-restricted). "
            "Requires an active session with backend='ax'."
        ),
    )
    @require_tier("indie")
    async def ios_dismiss_springboard_alert(label: str = "Allow") -> str:
        global _backend
        if _backend is None:
            return json.dumps({"error": "No active session. Call ios_start_session first."})
        from specterqa.ios.backends.ax_backend import AXBackend  # noqa: PLC0415

        if not isinstance(_backend, AXBackend):
            return json.dumps({
                "error": (
                    "ios_dismiss_springboard_alert requires backend='ax'. "
                    "Restart session with ios_start_session(backend='ax')."
                )
            })
        result = _backend.dismiss_springboard_alert(label=label)
        return json.dumps(result, default=str)

    # ── Tool: ios_pre_grant_permissions ────────────────────────────────────
    #  Example Reader dogfood Issue 3 / Task #17 (workaround helper)

    @mcp.tool(
        name="ios_pre_grant_permissions",
        description=(
            "Pre-grant iOS app permissions via xcrun simctl BEFORE the app launches, "
            "preventing permission alerts from appearing at runtime. "
            "Call this BEFORE ios_start_session or before reinstalling the app. "
            "bundle_id: app bundle id (e.g. 'com.example.reader'). "
            "permissions: list of service names — 'notifications', 'location', 'camera', "
            "'microphone', 'contacts', 'photos', 'calendars', 'reminders', 'motion', "
            "'bluetooth', 'health'. "
            "device_id: simulator UDID (default 'booted'). "
            "Returns which permissions were granted and which failed. "
            "iOS 18.4 note: 'notifications' returns Operation not permitted — "
            "OS-level restriction; all other services typically succeed."
        ),
    )
    @require_tier("indie")
    async def ios_pre_grant_permissions(
        bundle_id: str,
        permissions: list[str],
        device_id: str = "booted",
    ) -> str:
        from specterqa.ios.backends.ax_backend import AXBackend  # noqa: PLC0415

        result = AXBackend.pre_grant_permissions(
            device_udid=device_id,
            bundle_id=bundle_id,
            permissions=permissions,
        )
        return json.dumps(result, default=str)

    # ── Tool: ios_list_replays ─────────────────────────────────────────────

    @mcp.tool(
        name="ios_list_replays",
        description=(
            "List saved replay YAML files with their names, step counts, and last-modified timestamps. "
            "Use before ios_replay to discover available replays. "
            "Returns a list of {name, path, steps, modified} dicts, newest first. "
            "replay_dir overrides the default scan directory (.specterqa/replays)."
        ),
    )
    @require_tier("indie")
    async def ios_list_replays(replay_dir: str = ".specterqa/replays") -> str:
        result = handle_list_replays({"replay_dir": replay_dir})
        return json.dumps(result)

    # ── Tool: ios_replay ───────────────────────────────────────────────────

    @mcp.tool(
        name="ios_replay",
        description=(
            "Run a saved replay YAML end-to-end against the booted simulator. "
            "name: the replay name from ios_list_replays (or an absolute file path). "
            "An active session is required — call ios_start_session first. "
            "Returns {status: 'passed'|'failed'|'error', steps_executed, failed_step_index, failures}. "
            "Use ios_validate_replay first to catch bad replays before running."
        ),
    )
    @require_tier("indie")
    async def ios_replay(name: str, replay_dir: str = ".specterqa/replays") -> str:
        result = handle_replay({"name": name, "replay_dir": replay_dir})
        return json.dumps(result)

    # ── Tool: ios_validate_replay ──────────────────────────────────────────

    @mcp.tool(
        name="ios_validate_replay",
        description=(
            "Parse a replay YAML and validate structure + referenced element identifiers "
            "without executing it. Use to catch bad replays before running. "
            "name: the replay name from ios_list_replays (or an absolute file path). "
            "Returns {valid: bool, step_count, issues: [...], name, bundle_id}. "
            "No active session required."
        ),
    )
    @require_tier("indie")
    async def ios_validate_replay(name: str, replay_dir: str = ".specterqa/replays") -> str:
        result = handle_validate_replay({"name": name, "replay_dir": replay_dir})
        return json.dumps(result)

    # ── Tool: ios_doctor ───────────────────────────────────────────────────

    @mcp.tool(
        name="ios_doctor",
        description=(
            "Check environment readiness: Xcode path, simulator runtimes, booted devices, "
            "SpecterQA runner build status. Returns a structured health summary with pass/fail "
            "per check and suggested-fix strings. Call first when a session fails unexpectedly."
        ),
    )
    async def ios_doctor() -> str:
        result = handle_doctor({})
        return json.dumps(result)

    # ── Tool: ios_devices ──────────────────────────────────────────────────

    @mcp.tool(
        name="ios_devices",
        description=(
            "List booted iOS simulators: UDID, name, runtime, state. "
            "Use to pick device_udid when starting a session or calling ios_apps. "
            "Returns an empty list if no simulators are booted — does not crash."
        ),
    )
    async def ios_devices() -> str:
        result = handle_devices({})
        return json.dumps(result)

    # ── Tool: ios_apps ─────────────────────────────────────────────────────

    @mcp.tool(
        name="ios_apps",
        description=(
            "List apps installed on a booted simulator. "
            "device_udid: simulator UDID from ios_devices (required). "
            "Returns bundle_id, display_name, version, install_path for each app. "
            "Use to find bundle_id for ios_start_session. "
            "Raises ValueError with a clear message if the UDID is invalid."
        ),
    )
    async def ios_apps(device_udid: str) -> str:
        try:
            result = handle_apps({"device_udid": device_udid})
            return json.dumps(result)
        except ValueError as exc:
            return json.dumps({"error": str(exc)})

    # ── Tool: ios_license_status ───────────────────────────────────────────

    @mcp.tool(
        name="ios_license_status",
        description=(
            "Report SpecterQA license tier and feature entitlements. "
            "Returns {tier: 'free'|'trial'|'indie'|'founder'|'pro'|'team', "
            "entitlements: {browserstack, indigo_hid, multi_sim, ci_replay, max_concurrent_sims}, "
            "expiry: str|None, valid: bool}. "
            "No active session required."
        ),
    )
    async def ios_license_status() -> str:
        result = handle_license_status({})
        return json.dumps(result)


    # ios_start_runner REMOVED in v14.0.0a1 — ios_start_session handles runner lifecycle automatically.
    # ios_stop_runner REMOVED in v14.0.0a1 — ios_stop_session handles cleanup.

    # ── Phase 2 tools: AI debugging primitives (v14.0.0b1) ────────────────

    # ── Tool: ios_app_relaunch ─────────────────────────────────────────────

    @mcp.tool(
        name="ios_app_relaunch",
        description=(
            "Restart the app under test without tearing down the XCTest runner. "
            "No app_path: xcrun simctl terminate + launch (<2s, mode='terminate-launch'). "
            "With app_path: simctl install + terminate + launch (~15s, mode='reinstall-launch'). "
            "Returns {bundle_id, udid, elapsed_ms, foreground_verified, mode}. "
            "foreground_verified=True means the app is confirmed in the foreground within 5s. "
            "Use after reproducing a crash or to start a clean app state without stopping the session. "
            "Requires an active session (ios_start_session)."
        ),
    )
    @require_tier("team")
    async def ios_app_relaunch(
        bundle_id: str,
        app_path: str | None = None,
        udid: str = "booted",
        session_id: str | None = None,
    ) -> str:
        result = handle_app_relaunch({
            "bundle_id": bundle_id,
            "app_path": app_path,
            "udid": udid,
            "session_id": session_id,
        })
        return json.dumps(result, default=str)

    # ── Tool: ios_logs_tail ────────────────────────────────────────────────

    @mcp.tool(
        name="ios_logs_tail",
        description=(
            "Return only the logs that have arrived since the last call (incremental). "
            "Maintains a per-session cursor so each call returns new entries only. "
            "First call returns the last ~2s of logs as the initial boundary. "
            "since_last_call=False returns all recent logs (ignores cursor). "
            "Filters: level ('error', 'fault'), category (exact), regex (message pattern). "
            "Returns {logs: [...], cursor: '<ISO timestamp>', since_ms, count}. "
            "Use in a debugging loop: call after each action to see what the app logged. "
            "Requires an active session (ios_start_session)."
        ),
    )
    @require_tier("pro")
    async def ios_logs_tail(
        since_last_call: bool = True,
        level: str | None = None,
        category: str | None = None,
        regex: str | None = None,
        session_id: str | None = None,
    ) -> str:
        result = handle_logs_tail({
            "since_last_call": since_last_call,
            "level": level,
            "category": category,
            "regex": regex,
            "session_id": session_id,
        })
        return json.dumps(result, default=str)

    # ── Tool: ios_promote_session_to_test ──────────────────────────────────

    @mcp.tool(
        name="ios_promote_session_to_test",
        description=(
            "Promote the current recording buffer to a named replay YAML test. "
            "Writes the replay to ./replays/<name>.yaml (default, CI-friendly) or path= override. "
            "Auto-validates the file with specterqa-ios validate-replay before returning. "
            "validation='passed' + can_replay=True means the replay is ready for CI. "
            "validation='failed' means the file WAS saved but has issues — errors[] explains why. "
            "The file is NEVER deleted on validation failure — iterate and fix. "
            "Returns {saved_to, validation, steps, can_replay, errors?}. "
            "Requires ios_start_recording to have been called first."
        ),
    )
    @require_tier("team")
    async def ios_promote_session_to_test(
        name: str,
        path: str | None = None,
        session_id: str | None = None,
    ) -> str:
        result = handle_promote_session_to_test({
            "name": name,
            "path": path,
            "session_id": session_id,
        })
        return json.dumps(result, default=str)

    # ── Tool: ios_wait_for_session (Issue 2) ──────────────────────────────

    @mcp.tool(
        name="ios_wait_for_session",
        description=(
            "Wait for an async session deploy to become healthy. "
            "Use after ios_start_session(wait=False) returns {status: 'deploying'}. "
            "deploy_id: the ID returned by the async start (optional — waits for any deploy). "
            "timeout_s: maximum seconds to poll (default 120). "
            "Returns {status: 'healthy', ...} on success or {status: 'failed'|'timeout', error} on failure. "
            "Also usable without a prior async start — returns {status: 'idle'} when no deploy is in flight."
        ),
    )
    async def ios_wait_for_session(
        deploy_id: str | None = None,
        timeout_s: float = 120.0,
    ) -> str:
        t0 = time.monotonic()
        deadline = t0 + timeout_s

        state = _get_deploy_state()

        # If no deploy in flight, return immediately
        if state["status"] == "idle":
            return json.dumps({"status": "idle", "elapsed_ms": 0, "udid": None})

        # Poll until healthy, failed, or timeout
        while time.monotonic() < deadline:
            state = _get_deploy_state()
            if state["status"] in ("healthy", "failed"):
                return json.dumps(state)
            # Also check if the backend is up (sync path may have set it)
            if _backend is not None and state["status"] == "idle":
                return json.dumps({"status": "healthy", "elapsed_ms": state["elapsed_ms"], "udid": _session_udid})
            time.sleep(1.0)

        return json.dumps({"status": "timeout", "elapsed_ms": int((time.monotonic() - t0) * 1000), "udid": None})

    # ── Tool: ios_session_status (Issue 2) ────────────────────────────────

    @mcp.tool(
        name="ios_session_status",
        description=(
            "Return the current session status without blocking. "
            "Returns {status: 'idle'|'deploying'|'healthy'|'degraded'|'failed', "
            "elapsed_ms, udid, deploy_id, started_at, error, daemon_pid}. "
            "Status semantics (v16.0.0a3):\n"
            "  idle      — no session has been configured this daemon lifetime\n"
            "  deploying — handle_start_session is in flight (wait=False path)\n"
            "  healthy   — runner is responding to /health right now (probed live)\n"
            "  degraded  — backend object exists but runner /health is failing\n"
            "  failed    — last deploy attempt errored\n"
            "Use between ios_start_session(wait=False) and ios_wait_for_session "
            "to check progress without blocking. "
            "status='healthy' is the only state in which tools can be called."
        ),
    )
    async def ios_session_status() -> str:
        # v16.0.0a3 (Maurice/Example Reader dogfood §P0-1, §P1-1): the previous
        # implementation overrode any idle/deploying state to "healthy" if
        # `_backend` was non-None — even when the runner had silently died.
        # That gave agents a false-success reading. Now we probe live.
        state = _get_deploy_state()
        state["daemon_pid"] = os.getpid()

        # If the deploy state machine is in a definitive terminal state, trust it.
        if state["status"] in ("deploying", "failed"):
            return json.dumps(state)

        # If we have a backend object, probe its /health for the live truth.
        if _backend is not None:
            try:
                health = _backend.health()
                if isinstance(health, dict) and health.get("status") == "ok":
                    state["status"] = "healthy"
                    if state.get("udid") is None and _session_udid is not None:
                        state["udid"] = _session_udid
                else:
                    state["status"] = "degraded"
                    state["error"] = (
                        f"runner /health did not return ok: {health!r}"
                    )
            except Exception as exc:  # noqa: BLE001
                state["status"] = "degraded"
                state["error"] = (
                    f"runner /health probe failed: {type(exc).__name__}: {exc}"
                )
        # else: keep status="idle" (no backend ever configured this lifetime)

        return json.dumps(state)

    # ── Tool: ios_dismiss_first_launch_alerts (Issue 9 helper 1) ─────────

    @mcp.tool(
        name="ios_dismiss_first_launch_alerts",
        description=(
            "Dismiss iOS first-launch permission alerts (e.g. 'Allow Notifications?'). "
            "decline=True (default) taps 'Don't Allow'. decline=False taps 'Allow'. "
            "permissions: optional list of permission names to dismiss in sequence "
            "(e.g. ['notifications', 'tracking']). When omitted, attempts to dismiss "
            "whatever alert is currently visible. "
            "Coordinates are auto-scaled to the device screen size. "
            "Returns {dismissed: N, attempts: N, taps: [{x, y, label}]}. "
            "Requires an active session (ios_start_session)."
        ),
    )
    @require_tier("indie")
    async def ios_dismiss_first_launch_alerts(
        decline: bool = True,
        permissions: list[str] | None = None,
    ) -> str:
        if _backend is None:
            return json.dumps({"error": "No active session. Call ios_start_session first."})

        try:
            # Get current screen size from the session backend
            screen_w, screen_h = 390, 844  # iPhone 12 defaults
            try:
                if _annotator is not None:
                    screen_size = _annotator.screen_size
                    if screen_size and len(screen_size) >= 2:
                        screen_w, screen_h = screen_size[0], screen_size[1]
            except Exception:  # noqa: BLE001
                pass

            # Determine button coordinates based on decline flag.
            # iPhone 12 (390×844 portrait):
            #   "Don't Allow" is at approx (120, 500) — left-side button
            #   "Allow" is at approx (270, 500) — right-side button
            # Scale proportionally to actual screen size.
            scale_x = screen_w / 390.0
            scale_y = screen_h / 844.0

            dont_allow_x = int(120 * scale_x)
            dont_allow_y = int(500 * scale_y)
            allow_x = int(270 * scale_x)
            allow_y = int(500 * scale_y)

            tap_x = dont_allow_x if decline else allow_x
            tap_y = dont_allow_y if decline else allow_y

            label = "Don't Allow" if decline else "Allow"
            permission_list = permissions or ["current"]
            taps = []
            dismissed = 0

            for _perm in permission_list:
                try:
                    tap_result = _backend.tap(tap_x, tap_y)
                    taps.append({"x": tap_x, "y": tap_y, "label": label, "permission": _perm})
                    dismissed += 1
                    time.sleep(0.5)  # let alert dismiss animation complete
                except Exception:  # noqa: BLE001
                    taps.append({"x": tap_x, "y": tap_y, "label": label, "permission": _perm, "error": "tap_failed"})

            return json.dumps({
                "dismissed": dismissed,
                "attempts": len(permission_list),
                "taps": taps,
                "decline": decline,
            })

        except Exception as exc:  # noqa: BLE001
            return json.dumps({"error": f"dismiss_first_launch_alerts failed: {exc}"})

    return mcp


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def _reap_orphan_daemons() -> int:
    """Kill any other specterqa-ios-mcp processes before this one binds.

    v16.0.0a3 (Maurice/Example Reader dogfood §P0-1.5): orphan MCP daemons from prior
    Claude Code sessions or `/mcp` reconnects sometimes survive their parent
    process and continue holding port :8222 with stale state. A new daemon
    that comes up alongside an orphan is silently piggybacking on the
    orphan's HTTP server, returning fresh-shaped responses with stale pixels.

    On daemon startup, locate any other specterqa-ios-mcp processes via pgrep
    and SIGKILL them (excluding self). Returns the count of orphans killed.

    Note: this is intentionally aggressive. The cost of a false positive
    (killing a legitimately concurrent daemon) is much lower than the cost
    of silent piggyback (false-success agent decisions on dead state). If
    multi-daemon support is needed in the future it can be opted in via
    `SPECTERQA_ALLOW_MULTI_DAEMON=1`.
    """
    if os.environ.get("SPECTERQA_ALLOW_MULTI_DAEMON", "").strip().lower() in ("1", "true", "yes"):
        return 0
    try:
        import signal as _signal  # noqa: PLC0415
        my_pid = os.getpid()
        # pgrep may not be on PATH in every environment; ps -A is portable.
        result = subprocess.run(
            ["pgrep", "-f", "specterqa-ios-mcp"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode != 0:
            return 0
        pids = [int(p) for p in result.stdout.split() if p.strip().isdigit()]
        orphans = [p for p in pids if p != my_pid]
        for pid in orphans:
            try:
                os.kill(pid, _signal.SIGKILL)
                logger.warning(
                    "Reaped orphan specterqa-ios-mcp daemon pid=%d before binding", pid,
                )
            except (ProcessLookupError, PermissionError) as exc:  # noqa: PERF203
                logger.debug("Could not kill orphan pid=%d: %s", pid, exc)
        return len(orphans)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Orphan-daemon reaping failed: %s", exc)
        return 0


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

    # v16.0.0a3 — kill any orphan daemons holding the MCP port with stale state
    # before this daemon binds. See _reap_orphan_daemons for rationale.
    reaped = _reap_orphan_daemons()
    if reaped:
        logger.warning("Reaped %d orphan daemon(s) on startup", reaped)

    server = create_server()
    server.run(transport="stdio")


if __name__ == "__main__":
    serve()
