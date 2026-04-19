"""SpecterQA iOS MCP Server — Native primitives for Claude Code.

Claude Code IS the reasoning engine. This server exposes direct
simulator control primitives — no Claude API calls, no SoM pipeline,
no orchestration loops. Claude sees annotated screenshots and decides
what to do.

Usage:
    specterqa-ios-mcp            # stdio transport (console_scripts entry point)
    python -m specterqa.ios.mcp  # alternative invocation
    specterqa ios serve          # via CLI serve command

Tools (41 total):
    ios_start_session       Start session on the iOS Simulator (AX or XCTest backend)
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
    ios_perf_baseline       Capture a perf snapshot as a reference baseline
    ios_perf_compare        Compare current perf to the stored baseline (deltas + severity)

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
_perf_baseline: dict | None = None  # Stored perf baseline for ios_perf_compare
_ax_http_server = None  # AXHTTPServer instance (None when AX backend is not active)

# Circuit-breaker for session health — replaces the per-call health() probe.
# After 3 consecutive ConnectionError failures the breaker opens and
# _require_session() raises RuntimeError immediately without hitting the runner.
from specterqa.ios.backends.retry_policy import RetryPolicy, SessionCrashedError  # noqa: E402

_circuit_breaker = RetryPolicy(
    max_retries=2,
    base_backoff_s=0.3,
    circuit_breaker_threshold=3,
).stateful()


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
        device_type: Internal use only — reserved for future physical device support.
        backend:     Backend name override: "auto" (default), "ax", "xctest",
                     or "browserstack".  "auto" lets BackendSelector decide.

    Returns:
        {"status": "ok", "backend": "ax"|"xctest"|"browserstack", ...}
        or {"error": "<message>"} on failure.
    """
    global _session, _backend, _annotator, _last_elements, _recorder, _session_state, _console_monitor, _crash_detector, _perf_profiler, _network_inspector, _ax_http_server

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

        # B9 fix (v13.2.1): Deploy the XCTest runner before calling
        # BackendSelector.choose() when xctest is explicitly requested or when
        # auto-select might pick it.  In 13.2.0 the MCP path only called build.sh
        # (compile only), so XCTestBackend.is_available() always returned False and
        # the runner was never started.  The CLI path already deployed correctly via
        # TestSession._deploy_runner() → xcodebuild test-without-building.
        #
        # We deploy here (not in the XCTest branch below) so that BackendSelector
        # can probe :8222/health and select XCTestBackend when backend="auto".
        _should_deploy_xctest = backend_arg in ("xctest", None)
        if _should_deploy_xctest:
            _xctestrun = _find_xctestrun(_DEFAULT_RUNNER_BUILD_DIR)
            if _xctestrun is not None:
                # Deploy the pre-built runner to the simulator.
                # Mirrors TestSession._deploy_runner() but without the clone/session
                # machinery — MCP uses the user's booted simulator directly.
                try:
                    from specterqa.ios.session_manager import TestSession  # noqa: PLC0415

                    _deploy_port = 8222
                    _deploy_xctestrun = Path(_xctestrun)

                    # Inject environment variables the runner needs.
                    TestSession._inject_xctestrun_env(
                        _deploy_xctestrun,
                        {
                            "SPECTERQA_PORT": str(_deploy_port),
                            "SPECTERQA_BUNDLE_ID": bundle_id or "",
                        },
                    )

                    _mcp_runner_proc = subprocess.Popen(
                        [
                            "xcodebuild",
                            "test-without-building",
                            "-xctestrun",
                            str(_deploy_xctestrun),
                            "-destination",
                            f"id={device_id}",
                        ],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                    )

                    # Store the process on _session (will be cleaned up on stop)
                    # We set _session as a lightweight holder so handle_stop_session
                    # can terminate the runner process.
                    class _MCPRunnerSession:  # noqa: N801
                        """Minimal session wrapper for MCP-deployed XCTest runner."""

                        def __init__(self, proc: subprocess.Popen) -> None:
                            self._runner_process = proc
                            self._target_udid = device_id
                            self._clone_udid = None

                        def stop(self) -> None:
                            if self._runner_process and self._runner_process.poll() is None:
                                self._runner_process.terminate()
                                try:
                                    self._runner_process.wait(timeout=5)
                                except subprocess.TimeoutExpired:
                                    self._runner_process.kill()

                    _session = _MCPRunnerSession(_mcp_runner_proc)

                    # Wait for the runner to become healthy.
                    # Cold runner boot on an idle simulator takes 35–50s (Maurice, dogfood 2026-04-18).
                    # Bumped from 30s → 90s (W4 fix, v13.3.0) to avoid first-call errors.
                    from specterqa.ios.session_manager import _wait_for_health  # noqa: PLC0415

                    try:
                        _wait_for_health(
                            f"http://localhost:{_deploy_port}/health",
                            timeout_s=90.0,
                        )
                        logger.info("B9: MCP runner deployed and healthy on :%d", _deploy_port)
                    except Exception as health_exc:
                        logger.warning(
                            "B9: MCP runner did not become healthy within 90s: %s", health_exc
                        )

                except Exception as deploy_exc:
                    logger.warning("B9: MCP runner deploy failed: %s", deploy_exc)

        from specterqa.ios.backends.selector import BackendSelector
        from specterqa.ios.som_annotator import SoMAnnotator

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
            response: dict = {
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
    global _session, _backend, _annotator, _last_elements, _recorder, _session_state, _console_monitor, _crash_detector, _perf_profiler, _network_inspector, _ax_http_server

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

        # Stop AX HTTP server (shuts down background HTTPServer thread)
        if _ax_http_server is not None:
            try:
                _ax_http_server.stop()
            except Exception as exc:
                logger.warning("Error stopping AX HTTP server: %s", exc)

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


def handle_dismiss_keyboard(arguments: dict) -> dict:
    """Dismiss the software keyboard by tapping just above it.

    Calls the runner's /dismiss_keyboard endpoint which taps above the keyboard
    frame to dismiss it, with a swipe-down fallback if the tap doesn't work.

    Returns:
        {"status": "ok", "dismissed": true}
        or {"status": "ok", "dismissed": false} if no keyboard was visible
        or {"error": "<message>"} on failure.
    """
    try:
        _require_session()
    except RuntimeError as exc:
        return {"error": str(exc)}

    try:
        result = _backend._post("/dismiss_keyboard", {})
        return result
    except Exception as exc:
        return {"error": f"dismiss_keyboard failed: {exc}"}


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
    """
    import plistlib

    device_udid = arguments.get("device_udid", "").strip()
    if not device_udid:
        raise ValueError("device_udid is required — call ios_devices to list booted simulators")

    try:
        raw = subprocess.check_output(
            ["xcrun", "simctl", "listapps", device_udid],
            stderr=subprocess.PIPE,
        )
    except subprocess.CalledProcessError as exc:
        stderr = exc.stderr.decode("utf-8", errors="replace") if exc.stderr else ""
        if "invalid device" in stderr.lower() or "unable to lookup" in stderr.lower():
            raise ValueError(
                f"No simulator with UDID '{device_udid}'. "
                "Call ios_devices to see booted simulators."
            ) from exc
        raise ValueError(f"simctl listapps failed: {stderr}") from exc
    except Exception as exc:
        raise ValueError(f"simctl listapps failed: {exc}") from exc

    try:
        plist_data = plistlib.loads(raw)
    except Exception as exc:
        return [{"warning": f"Failed to parse app list: {exc}", "bundle_id": "", "display_name": "", "version": "", "install_path": ""}]

    results = []
    for bundle_id, info in plist_data.items():
        results.append({
            "bundle_id": bundle_id,
            "display_name": info.get("CFBundleDisplayName") or info.get("CFBundleName", ""),
            "version": info.get("CFBundleShortVersionString") or info.get("CFBundleVersion", ""),
            "install_path": info.get("Path", ""),
        })

    results.sort(key=lambda a: a["display_name"].lower())
    return results


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

AVAILABLE TOOLS (41 total):

  Session lifecycle:
    ios_start_session    — Deploy XCTest runner; launch the app (required first step)
    ios_stop_session     — Stop runner and clean up (always call when done)
    ios_start_runner     — Build (if needed) and deploy the XCTest runner. Use for explicit runner lifecycle control.
    ios_stop_runner      — Terminate the XCTest runner. Idempotent. Use after all sessions are done.

  Observation:
    ios_screenshot       — Annotated screenshot with numbered bounding boxes + element list
    ios_elements         — Element list only (faster than screenshot, no image)

  Interaction:
    ios_tap              — Tap: identifier > label > element_index > (x,y) coordinates
    ios_long_press       — Long-press by index (context menus, drag init)
    ios_type             — Type text: identifier > label > element_index > (x,y) coordinates
    ios_press_key        — Press a named key: return, escape, delete, tab, space
    ios_swipe            — Swipe in a direction: up, down, left, right
    ios_swipe_back       — iOS edge swipe back navigation gesture
    ios_dismiss_keyboard — Dismiss the software keyboard

  Waiting:
    ios_wait             — Arbitrary delay (N seconds). Use only when no better option.
    ios_wait_for_element — Poll until a labelled element appears. Use for specific element.
    ios_wait_idle        — Wait for element tree to stabilize. Use after navigation/transitions.
    ios_app_state        — Check app lifecycle state (foreground/background/suspended)
    ios_dismiss_sheet    — Dismiss a sheet/modal by swiping down

  Recording & Replay:
    ios_start_recording  — Clear step buffer; begin clean recording
    ios_stop_recording   — Save replay YAML + clear buffer (end of flow); preferred save path
    ios_save_replay      — [deprecated in v12 — use ios_stop_recording] Save YAML without clearing buffer
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

FIRST SESSION — minimum viable loop:
  ios_start_session(bundle_id="com.example.app")
  → ios_screenshot()
  → ios_tap(label="Sign In")
  → ios_screenshot()
  → ios_stop_recording(name="signin-flow")
  → ios_stop_session()

BACKEND SELECTION (ios_start_session backend= param):
- Default to backend="xctest" for comprehensive element trees, typing into forms, and navigating .sheet-presented UIKit content.
- Use backend="ax" when startup speed matters and you only need tap-by-label on root-level elements. Note: AX does not enumerate .sheet-presented UIKit content.
- backend="auto" (default) uses AX if available, falls back to XCTest.

WAITING — decision tree:
- Waiting for a specific element to appear → ios_wait_for_element(label="...")
- After navigation / screen transition → ios_wait_idle()
- Fixed animation delay or splash screen → ios_wait(seconds=N)

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

TYPING INTO FORMS:
- ALWAYS specify the target field: ios_type(text="value", identifier="field_id")
- Priority: identifier > label > element_index > (x,y) coordinates
- After typing, call ios_elements() to verify the value was accepted

COMMON PITFALLS:
- Keyboard covers buttons: call ios_dismiss_keyboard() before tapping buttons below the keyboard
- Tab bar covered: call ios_dismiss_keyboard() before switching tabs
- Stale elements after navigation: ios_tap auto-refreshes, but call ios_elements() if unsure
- SecureField value masked: SecureField shows bullet characters (•), not the actual text

RECORDING WORKFLOW (best practice):
  1. ios_start_session → exploratory taps to find the right flow
  2. ios_start_recording() → clears exploratory steps
  3. Execute the clean, successful flow (tap, type, etc.)
  4. ios_stop_recording(name="feature-name") → saves YAML + clears buffer
  5. Next flow: ios_start_recording() → repeat

  Recording trio — when to use which:
  - ios_stop_recording(name=...) — end of flow, save + clear buffer (most common)
  - ios_save_replay(name=...) — checkpoint mid-flow without clearing; continue recording after
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
            "Start a SpecterQA session on the booted iOS Simulator. "
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
            "note AX does not enumerate .sheet-presented UIKit content — use xctest for those flows."
        ),
    )
    async def ios_start_session(
        bundle_id: str,
        device_id: str = "booted",
        app_path: str | None = None,
        license_key: str | None = None,
        clone: bool = False,
        backend: str = "auto",
    ) -> str:
        result = handle_start_session(
            {
                "bundle_id": bundle_id,
                "device_id": device_id,
                "app_path": app_path,
                "license_key": license_key or "",
                "clone": clone,
                "device_type": "simulator",  # physical device support hidden until Xcode 26 GM
                "backend": backend,
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
            "Use for fixed animation delays or splash screens. "
            "seconds defaults to 1.0; capped at 30. "
            "Decision: specific element → ios_wait_for_element. "
            "Navigation/transition → ios_wait_idle. Arbitrary delay → ios_wait."
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
            "Decision: specific element → ios_wait_for_element. "
            "Navigation/transition → ios_wait_idle. Arbitrary delay → ios_wait."
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
            "Use ios_save_replay if you want to keep recording after saving. "
            "Internally awaits checkpoint completion before saving (prevents stale expect_elements)."
        ),
    )
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
            "Type text into a text field on the iOS Simulator. "
            "Target field priority: identifier > label > element_index > (x,y) coordinates. "
            "RECOMMENDED: always specify a target field to ensure text goes into the correct field. "
            "Without a target, types into whatever field currently has focus (unreliable on multi-field forms). "
            "Examples: ios_type(text='hello', identifier='emailField') or "
            "ios_type(text='hello', label='Password') or ios_type(text='hello', element_index=5). "
            "text is required and must be non-empty."
        ),
    )
    async def ios_type(
        text: str,
        label: str | None = None,
        identifier: str | None = None,
        element_index: int | None = None,
        x: float | None = None,
        y: float | None = None,
    ) -> str:
        result = handle_type({
            "text": text,
            "label": label,
            "identifier": identifier,
            "element_index": element_index,
            "x": x,
            "y": y,
        })
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
            "[DEPRECATED in v12 — prefer ios_stop_recording which saves and clears the buffer.] "
            "Save the current session as a deterministic replay YAML file WITHOUT clearing the step buffer. "
            "Use this only when you want to checkpoint mid-flow and continue recording after saving. "
            "For end-of-flow saves, use ios_stop_recording(name=...) instead. "
            "The replay can be run in CI without AI: "
            "  specterqa-ios replay <file.yaml>. "
            "name is the human-readable test name used as the filename stem "
            "(default: 'replay'). "
            "path overrides the output location "
            "(default: .specterqa/replays/<name>.yaml)."
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
            "Use after navigation transitions, screen pushes/pops, and tab switches. "
            "timeout defaults to 10s, max 30s. "
            "Decision: specific element → ios_wait_for_element. "
            "Navigation/transition → ios_wait_idle. Arbitrary delay → ios_wait."
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

    # ── Tool: ios_dismiss_keyboard ────────────────────────────────────────────

    @mcp.tool(
        name="ios_dismiss_keyboard",
        description=(
            "Dismiss the software keyboard. "
            "Call before tapping buttons or switching tabs that are covered by an open keyboard. "
            "Returns {dismissed: true} if a keyboard was visible and dismissed, "
            "{dismissed: false} if no keyboard was present. "
            "Requires an active session (ios_start_session)."
        ),
    )
    async def ios_dismiss_keyboard() -> str:
        result = handle_dismiss_keyboard({})
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
    async def ios_perf_compare() -> str:
        result = handle_perf_compare({})
        return json.dumps(result, default=str)

    # ── Tool: ios_dismiss_springboard_alert ────────────────────────────────
    #  Palace dogfood Issue 3 / Task #17

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
    #  Palace dogfood Issue 3 / Task #17 (workaround helper)

    @mcp.tool(
        name="ios_pre_grant_permissions",
        description=(
            "Pre-grant iOS app permissions via xcrun simctl BEFORE the app launches, "
            "preventing permission alerts from appearing at runtime. "
            "Call this BEFORE ios_start_session or before reinstalling the app. "
            "bundle_id: app bundle id (e.g. 'org.thepalaceproject.palace'). "
            "permissions: list of service names — 'notifications', 'location', 'camera', "
            "'microphone', 'contacts', 'photos', 'calendars', 'reminders', 'motion', "
            "'bluetooth', 'health'. "
            "device_id: simulator UDID (default 'booted'). "
            "Returns which permissions were granted and which failed. "
            "iOS 18.4 note: 'notifications' returns Operation not permitted — "
            "OS-level restriction; all other services typically succeed."
        ),
    )
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

    # ── Tool: ios_start_runner ─────────────────────────────────────────────

    @mcp.tool(
        name="ios_start_runner",
        description=(
            "Build (if needed) and deploy the SpecterQA XCTest runner against a booted simulator. "
            "Returns when /health is responsive. Idempotent — safe to call if a runner is already running. "
            "Use this before ios_start_session(backend='xctest') if you want explicit control over runner lifecycle. "
            "This enables long-lived runner sharing across many sessions without re-deploying. "
            "Args: device_udid (required — simulator UDID from ios_devices), "
            "bundle_id (optional, for runner build env), timeout_s (default 90)."
        ),
    )
    async def ios_start_runner(
        device_udid: str,
        bundle_id: str | None = None,
        timeout_s: float = 90.0,
    ) -> str:
        result = handle_start_runner(
            {"device_udid": device_udid, "bundle_id": bundle_id or "", "timeout_s": timeout_s}
        )
        return json.dumps(result)

    # ── Tool: ios_stop_runner ──────────────────────────────────────────────

    @mcp.tool(
        name="ios_stop_runner",
        description=(
            "Terminate the SpecterQA XCTest runner subprocess on the given port. "
            "Idempotent — does not error if no runner is running. "
            "Use after your test session is complete to free simulator resources. "
            "Args: port (default 8222)."
        ),
    )
    async def ios_stop_runner(port: int = 8222) -> str:
        result = handle_stop_runner({"port": port})
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
