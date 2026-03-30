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
            # Use actual screenshot dimensions for coordinate mapping
            img_w = self._last_img_width
            img_h = self._last_img_height
            if img_w == 0 or img_h == 0:
                capture_result = self._capture.capture()
                img_w = capture_result.get("width", 1024)
                img_h = capture_result.get("height", 2226)
                self._last_img_width = img_w
                self._last_img_height = img_h

            # Centre of screenshot, scroll distance = 25% of height
            cx = img_w // 2
            cy = img_h // 2
            step = int(img_h * 0.25 * amount)

            if direction == "down":
                x1, y1, x2, y2 = cx, cy + step // 2, cx, cy - step // 2
            elif direction == "up":
                x1, y1, x2, y2 = cx, cy - step // 2, cx, cy + step // 2
            elif direction == "left":
                x1, y1, x2, y2 = cx + step // 2, cy, cx - step // 2, cy
            elif direction == "right":
                x1, y1, x2, y2 = cx - step // 2, cy, cx + step // 2, cy
            else:
                x1, y1, x2, y2 = cx, cy + step // 2, cx, cy - step // 2

            self._interaction.swipe(x1, y1, x2, y2, img_w, img_h)
            return {"success": True, "action": "scroll"}
        except Exception as exc:
            return {"success": False, "action": "scroll", "error": str(exc)}

    def _scroll_at(
        self,
        x: int,
        y: int,
        direction: str = "down",
        amount: int = 3,
    ) -> dict[str, Any]:
        """Scroll from a specific coordinate (prototype-style).

        Maps direction + amount to a swipe gesture originating at (x, y).
        This is used when the AI specifies a scroll coordinate (e.g. to scroll
        a specific list rather than the whole screen).

        Args:
            x, y: Screen coordinate in screenshot pixels.
            direction: One of ``"up"``, ``"down"``, ``"left"``, ``"right"``.
            amount: Number of scroll units (each unit ≈ 100px).

        Returns:
            Dict with ``success`` (bool) and ``action`` (str).
        """
        try:
            img_w = self._last_img_width or 1024
            img_h = self._last_img_height or 2226
            distance = amount * 100

            if direction == "down":
                x1, y1, x2, y2 = x, y, x, y - distance
            elif direction == "up":
                x1, y1, x2, y2 = x, y, x, y + distance
            elif direction == "left":
                x1, y1, x2, y2 = x, y, x - distance, y
            elif direction == "right":
                x1, y1, x2, y2 = x, y, x + distance, y
            else:
                x1, y1, x2, y2 = x, y, x, y - distance

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
    # Unified action dispatcher
    # ------------------------------------------------------------------

    def execute(self, decision) -> dict[str, Any]:
        """Dispatch a Decision (or raw computer_use dict) to the appropriate action.

        Handles two input formats:

        1. **Decision objects** (from ComputerUseDecider / IOSAIStepRunner):
           - decision.action == "click"    → decision.target = "x,y"
           - decision.action == "fill"     → decision.value = text
           - decision.action == "scroll"   → decision.value = direction
           - decision.action == "keyboard" → decision.value = key name
           - decision.action == "wait"     → 1s sleep

        2. **Raw computer_use dicts** (for direct/prototype use):
           - {"action": "left_click",   "coordinate": [x, y]}
           - {"action": "screenshot"}
           - {"action": "type",         "text": "..."}
           - {"action": "key",          "key": "Return"}
           - {"action": "scroll",       "coordinate": [x, y],
                                        "direction": "down", "amount": 3}
           - {"action": "left_click_drag", "start_coordinate": [x, y],
                                           "coordinate": [x2, y2]}
        """
        # Raw dict format (computer_use protocol)
        if isinstance(decision, dict):
            return self._execute_raw(decision)

        # Decision object format (IOSAIStepRunner protocol)
        action = getattr(decision, "action", None) or ""
        try:
            if action == "click":
                target = getattr(decision, "target", "0,0")
                parts = str(target).split(",")
                x = int(float(parts[0].strip()))
                y = int(float(parts[1].strip())) if len(parts) > 1 else 0
                return self.click(x, y)
            elif action == "fill":
                return self.fill(getattr(decision, "value", ""))
            elif action == "scroll":
                direction = str(getattr(decision, "value", "down"))
                # target may contain the scroll coordinate, e.g. "512,900"
                coord_str = str(getattr(decision, "target", "") or "")
                if coord_str and "," in coord_str:
                    parts = coord_str.split(",")
                    try:
                        sx = int(float(parts[0].strip()))
                        sy = int(float(parts[1].strip()))
                        return self._scroll_at(sx, sy, direction)
                    except (ValueError, IndexError):
                        pass
                return self.scroll(direction=direction)
            elif action == "keyboard":
                return self.keyboard(getattr(decision, "value", ""))
            elif action == "wait":
                dur = getattr(decision, "value", None)
                secs = float(dur) if dur else 1.0
                return self.wait(secs)
            elif action in ("done", "stuck"):
                return {"success": True, "action": action}
            else:
                return {"success": False, "action": action, "error": f"Unknown action: {action}"}
        except Exception as exc:
            return {"success": False, "action": action, "error": str(exc)}

    def _execute_raw(self, action: dict[str, Any]) -> dict[str, Any]:
        """Execute a raw computer_use tool action dict.

        Maps the Claude Computer Use API format directly to driver methods.
        Used when the driver is invoked without ComputerUseDecider translation.
        """
        action_type = action.get("action", "")

        if action_type == "screenshot":
            return self.screenshot()

        elif action_type in ("left_click", "click"):
            coord = action.get("coordinate", [0, 0])
            return self.click(int(coord[0]), int(coord[1]))

        elif action_type == "double_click":
            coord = action.get("coordinate", [0, 0])
            try:
                img_w = self._last_img_width or 1024
                img_h = self._last_img_height or 2226
                self._interaction.double_tap(
                    int(coord[0]), int(coord[1]), img_w, img_h
                )
                return {"success": True, "action": "double_click"}
            except Exception as exc:
                return {"success": False, "action": "double_click", "error": str(exc)}

        elif action_type in ("right_click", "long_press"):
            coord = action.get("coordinate", [0, 0])
            dur = float(action.get("duration", 3.0))
            try:
                img_w = self._last_img_width or 1024
                img_h = self._last_img_height or 2226
                self._interaction.long_press(
                    int(coord[0]), int(coord[1]), img_w, img_h, duration=dur
                )
                return {"success": True, "action": action_type}
            except Exception as exc:
                return {"success": False, "action": action_type, "error": str(exc)}

        elif action_type == "type":
            return self.fill(action.get("text", ""))

        elif action_type == "key":
            return self.keyboard(action.get("key", ""))

        elif action_type == "scroll":
            coord = action.get("coordinate", None)
            direction = action.get("direction", "down")
            amount = int(action.get("amount", 3))
            if coord:
                return self._scroll_at(int(coord[0]), int(coord[1]), direction, amount)
            return self.scroll(direction=direction, amount=amount)

        elif action_type == "left_click_drag":
            start = action.get("start_coordinate", [0, 0])
            end = action.get("coordinate", [0, 0])
            try:
                img_w = self._last_img_width or 1024
                img_h = self._last_img_height or 2226
                self._interaction.swipe(
                    int(start[0]), int(start[1]),
                    int(end[0]), int(end[1]),
                    img_w, img_h,
                )
                return {"success": True, "action": "left_click_drag"}
            except Exception as exc:
                return {"success": False, "action": "left_click_drag", "error": str(exc)}

        elif action_type == "wait":
            secs = float(action.get("duration", 1))
            return self.wait(secs)

        else:
            return {"success": False, "action": action_type,
                    "error": f"Unknown action: {action_type}"}

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
