"""M1: SimulatorDriver — main facade that composes all iOS Simulator sub-modules.

This is the primary entry-point for driving an iOS Simulator session.
It wires together all eight sub-modules (interaction, capture, console,
network, perf, state, crash, ai_context) and exposes a clean ActionExecutor
protocol plus lifecycle and context-aggregation methods.

INIT-2026-492 — SpecterQA iOS Simulator Driver.
"""

from __future__ import annotations

import subprocess
import time
from typing import Any, Optional

from specterqa.ios.drivers.simulator.interaction import InteractionLayer
from specterqa.ios.drivers.simulator.capture import ScreenCapture
from specterqa.ios.drivers.simulator.console import ConsoleMonitor
from specterqa.ios.drivers.simulator.network import NetworkInspector
from specterqa.ios.drivers.simulator.perf import PerfProfiler
from specterqa.ios.drivers.simulator.state import StateInspector
from specterqa.ios.drivers.simulator.crash import CrashDetector
from specterqa.ios.drivers.simulator.ai_context import SimulatorAIContext
from specterqa.ios.security.redactor import DataRedactor


class SimulatorDriver:
    """Main facade that composes all iOS Simulator driver sub-modules.

    Acts as the single entry-point for controlling an iOS Simulator session.
    Implements the ActionExecutor protocol so it can be used as a drop-in
    driver within the SpecterQA agentic loop.

    Args:
        config: Configuration dict.  Required keys:
            - ``device_id`` (str): Simulator UDID or ``"booted"``.
            - ``bundle_id`` (str): App bundle identifier.

            Optional keys:
            - ``device_name`` (str): Human-readable device name.
            - ``screenshot_resize_width`` (int): Target screenshot width
              (default: 1024).
            - ``title_bar_offset`` (int): Simulator window title-bar height
              in pixels (default: 28).
            - ``log_subsystem`` (str): Subsystem filter for ConsoleMonitor.
            - ``enable_network_capture`` (bool): Enable NetworkInspector
              (default: True).
            - ``enable_perf_monitoring`` (bool): Enable PerfProfiler
              (default: True).
            - ``enable_crash_detection`` (bool): Enable CrashDetector
              (default: True).
    """

    def __init__(self, config: dict[str, Any]) -> None:
        self._config = config
        self._device_id: str = config["device_id"]
        self._bundle_id: str = config["bundle_id"]

        # Shared redactor — injected into NetworkInspector and SimulatorAIContext
        self._redactor = DataRedactor()

        # Sub-module construction
        resize_width: int = config.get("screenshot_resize_width", 1024)
        title_bar_offset: int = config.get("title_bar_offset", 28)

        self._interaction = InteractionLayer(
            device_id=self._device_id,
            title_bar_offset=title_bar_offset,
        )
        self._capture = ScreenCapture(
            device_id=self._device_id,
            resize_width=resize_width,
        )
        self._console = ConsoleMonitor(
            device_id=self._device_id,
        )
        self._network = NetworkInspector(
            device_id=self._device_id,
            redactor=self._redactor,
        )
        self._perf = PerfProfiler(
            device_id=self._device_id,
            bundle_id=self._bundle_id,
        )
        self._state = StateInspector(
            device_id=self._device_id,
            bundle_id=self._bundle_id,
        )
        self._crash = CrashDetector(
            device_id=self._device_id,
            bundle_id=self._bundle_id,
        )
        self._ai_context = SimulatorAIContext(redactor=self._redactor)

        # Cache last screenshot dimensions for coordinate translation
        self._last_img_width: int = 0
        self._last_img_height: int = 0

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Boot the simulator and start all background monitors.

        Runs ``xcrun simctl boot <device_id>`` then starts the console,
        network, and crash monitors.  The perf profiler is started if
        ``enable_perf_monitoring`` is truthy in the config (default: True).
        """
        # Boot the simulator device
        subprocess.run(
            ["xcrun", "simctl", "boot", self._device_id],
            capture_output=True,
        )

        # Start background monitors
        self._console.start()
        self._network.start()
        self._crash.start()

        if self._config.get("enable_perf_monitoring", True):
            if hasattr(self._perf, "start"):
                self._perf.start()

    def stop(self) -> None:
        """Stop all background monitors.

        Stops console, network, and crash monitors.  Does not shut down
        the simulator itself — call ``xcrun simctl shutdown`` externally if
        needed.
        """
        self._console.stop()
        self._network.stop()
        self._crash.stop()

        if self._config.get("enable_perf_monitoring", True):
            if hasattr(self._perf, "stop"):
                try:
                    self._perf.stop()
                except Exception:
                    pass

    def launch_app(self) -> None:
        """Launch the configured app on the simulator.

        Runs ``xcrun simctl launch <device_id> <bundle_id>``.
        """
        subprocess.run(
            ["xcrun", "simctl", "launch", self._device_id, self._bundle_id],
            capture_output=True,
        )

    def terminate_app(self) -> None:
        """Terminate the configured app on the simulator.

        Runs ``xcrun simctl terminate <device_id> <bundle_id>``.
        """
        subprocess.run(
            ["xcrun", "simctl", "terminate", self._device_id, self._bundle_id],
            capture_output=True,
        )

    # ------------------------------------------------------------------
    # ActionExecutor protocol
    # ------------------------------------------------------------------

    def screenshot(self) -> dict[str, Any]:
        """Capture a screenshot of the simulator.

        Returns:
            Dict with keys ``success`` (bool), ``action`` (str), and the
            capture result fields (``base64``, ``width``, ``height``,
            ``timestamp``, ``raw_path``).  On failure, returns
            ``{"success": False, "action": "screenshot", "error": str}``.
        """
        try:
            result = self._capture.capture()
            # Cache dimensions for coordinate translation in click()
            self._last_img_width = result.get("width", 0)
            self._last_img_height = result.get("height", 0)
            return {"success": True, "action": "screenshot", **result}
        except Exception as exc:
            return {"success": False, "action": "screenshot", "error": str(exc)}

    def click(self, x: int, y: int) -> dict[str, Any]:
        """Tap a point on the simulator screen.

        Coordinates are in screenshot pixel space.  The driver takes a fresh
        screenshot to resolve the simulator window bounds before tapping.
        If the last screenshot dimensions are already cached, they are reused.

        Args:
            x: Horizontal coordinate in screenshot pixels.
            y: Vertical coordinate in screenshot pixels.

        Returns:
            Dict with ``success`` (bool) and ``action`` (str).
        """
        try:
            # Ensure we have image dimensions for the interaction layer
            img_w = self._last_img_width
            img_h = self._last_img_height
            if img_w == 0 or img_h == 0:
                capture_result = self._capture.capture()
                img_w = capture_result.get("width", 0)
                img_h = capture_result.get("height", 0)
                self._last_img_width = img_w
                self._last_img_height = img_h

            self._interaction.tap(x, y, img_w, img_h)
            return {"success": True, "action": "click"}
        except Exception as exc:
            return {"success": False, "action": "click", "error": str(exc)}

    def fill(self, text: str) -> dict[str, Any]:
        """Type text into the currently focused field.

        Args:
            text: The string to type.

        Returns:
            Dict with ``success`` (bool) and ``action`` (str).
        """
        try:
            self._interaction.type_text(text)
            return {"success": True, "action": "fill"}
        except Exception as exc:
            return {"success": False, "action": "fill", "error": str(exc)}

    def scroll(self, direction: str = "down", amount: int = 3) -> dict[str, Any]:
        """Scroll the screen in a given direction.

        Maps direction strings (``"up"``, ``"down"``, ``"left"``, ``"right"``)
        to InteractionLayer ``swipe()`` calls.

        Args:
            direction: Scroll direction — one of ``"up"``, ``"down"``,
                ``"left"``, ``"right"``.
            amount: Number of swipe steps (default: 3).

        Returns:
            Dict with ``success`` (bool) and ``action`` (str).
        """
        try:
            # Map direction to swipe coordinates (centre-based, 390×844 logical)
            cx, cy = 195, 422  # logical centre of a typical iPhone screen
            step = 100 * amount

            if direction == "down":
                # Swipe up to scroll down
                x1, y1, x2, y2 = cx, cy + step, cx, cy - step
            elif direction == "up":
                # Swipe down to scroll up
                x1, y1, x2, y2 = cx, cy - step, cx, cy + step
            elif direction == "left":
                x1, y1, x2, y2 = cx + step, cy, cx - step, cy
            elif direction == "right":
                x1, y1, x2, y2 = cx - step, cy, cx + step, cy
            else:
                x1, y1, x2, y2 = cx, cy + step, cx, cy - step

            img_w = self._last_img_width or 390
            img_h = self._last_img_height or 844

            self._interaction.swipe(x1, y1, x2, y2, img_w, img_h)
            return {"success": True, "action": "scroll"}
        except Exception as exc:
            return {"success": False, "action": "scroll", "error": str(exc)}

    def keyboard(self, key: str) -> dict[str, Any]:
        """Press a named key on the simulator.

        Args:
            key: Key name (e.g. ``"enter"``, ``"backspace"``).

        Returns:
            Dict with ``success`` (bool) and ``action`` (str).
        """
        try:
            self._interaction.press_key(key)
            return {"success": True, "action": "keyboard"}
        except Exception as exc:
            return {"success": False, "action": "keyboard", "error": str(exc)}

    def wait(self, seconds: float) -> dict[str, Any]:
        """Pause execution for the given number of seconds.

        Args:
            seconds: Duration to sleep.

        Returns:
            Dict with ``success`` (bool) and ``action`` (str).
        """
        time.sleep(seconds)
        return {"success": True, "action": "wait"}

    # ------------------------------------------------------------------
    # Context aggregation
    # ------------------------------------------------------------------

    def get_context(self) -> dict[str, Any]:
        """Aggregate all sub-module state into a serialisable dict.

        Takes a screenshot, then delegates to
        :meth:`SimulatorAIContext.build_context` to gather console logs,
        network requests, perf snapshot, app state, and crash reports.

        Returns:
            Dict with keys: ``screenshot``, ``logs``, ``network``, ``perf``,
            ``state``, ``crashes``.
        """
        # Capture screenshot for context
        screenshot_b64 = ""
        try:
            capture_result = self._capture.capture()
            screenshot_b64 = capture_result.get("base64", "")
            self._last_img_width = capture_result.get("width", 0)
            self._last_img_height = capture_result.get("height", 0)
        except Exception:
            pass

        ctx = self._ai_context.build_context(
            screenshot_b64,
            console=self._console,
            network=self._network,
            perf=self._perf,
            state=self._state,
            crash=self._crash,
        )

        return {
            "screenshot": ctx.screenshot_base64,
            "logs": ctx.recent_logs,
            "network": ctx.active_requests,
            "perf": ctx.perf_snapshot,
            "state": ctx.app_state,
            "crashes": ctx.crashes,
        }
