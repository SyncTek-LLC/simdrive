"""SpecterQA Simulator Runner -- iOS Simulator testing.

Bundled in specterqa-ios — sourced from specterqa.engine (upstream unpublished).

Manages iOS Simulator lifecycle via ``xcrun simctl``: boot devices, install
and launch apps, capture screenshots, simulate touch and keyboard input, and
detect stuck states via perceptual screenshot hashing.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import logging
import subprocess
import time
from pathlib import Path
from typing import Any

from specterqa.engine.report_generator import Finding, StepReport

logger = logging.getLogger("specterqa.engine.simulator_runner")


@dataclasses.dataclass
class SimulatorStepResult:
    """Result of executing a single iOS simulator step."""

    step_id: str
    passed: bool
    screenshots: list[str]
    ux_observations: list[str]
    actions_taken: list[dict[str, Any]]
    action_count: int
    duration_seconds: float
    findings: list[Finding]
    error: str | None = None
    goal_achieved: bool = False


class SimulatorRunner:
    """Executes iOS Simulator steps from scenario definitions.

    Manages a simulator device: boot, install app, launch, interact via
    ``simctl`` and AppleScript, capture screenshots, and detect stuck states.

    Usage::

        runner = SimulatorRunner(
            bundle_id="com.example.myapp",
            app_path="/path/to/MyApp.app",
            evidence_dir=Path("/tmp/evidence"),
        )
        runner.start()
        result = runner.execute_step(step_dict)
        runner.stop()
    """

    def __init__(
        self,
        bundle_id: str,
        evidence_dir: Path,
        app_path: str | None = None,
        device_id: str | None = None,
        device_name: str | None = None,
        os_version: str | None = None,
        product_config: dict[str, Any] | None = None,
    ) -> None:
        self._bundle_id = bundle_id
        self._evidence_dir = Path(evidence_dir)
        self._app_path = app_path
        self._device_id = device_id
        self._device_name = device_name or "iPhone 15 Pro"
        self._os_version = os_version
        self._product_config = product_config or {}
        self._booted_by_us = False

    # -- Lifecycle -----------------------------------------------------------

    def start(self) -> None:
        """Boot the simulator (if needed), install the app, and launch it."""
        if self._device_id is None:
            self._device_id = self._find_best_device()
            if self._device_id is None:
                raise RuntimeError(
                    f"No simulator device found matching name='{self._device_name}' "
                    f"os='{self._os_version}'."
                )

        logger.info("Using simulator device: %s (name=%s)", self._device_id, self._device_name)

        if not self._is_booted():
            logger.info("Booting simulator %s", self._device_id)
            self._simctl("boot", self._device_id)
            self._booted_by_us = True
            self._wait_for_boot(timeout=60)
        else:
            logger.info("Simulator %s already booted", self._device_id)

        subprocess.run(
            ["open", "-a", "Simulator", "--args", "-CurrentDeviceUDID", self._device_id],
            capture_output=True,
            timeout=15,
        )
        time.sleep(2)

        if self._app_path:
            logger.info("Installing app: %s", self._app_path)
            self._simctl("install", self._device_id, self._app_path)
            time.sleep(1)

        logger.info("Launching app: %s", self._bundle_id)
        self._simctl("launch", self._device_id, self._bundle_id)
        time.sleep(2)

    def stop(self, shutdown: bool = False) -> None:
        """Terminate the app and optionally shut down the simulator."""
        if self._device_id is None:
            return

        try:
            self._simctl("terminate", self._device_id, self._bundle_id)
            logger.info("Terminated app %s on simulator %s", self._bundle_id, self._device_id)
        except Exception as exc:
            logger.warning("Failed to terminate app: %s", exc)

        if shutdown and self._booted_by_us:
            try:
                self._simctl("shutdown", self._device_id)
                logger.info("Shut down simulator %s", self._device_id)
            except Exception as exc:
                logger.warning("Failed to shut down simulator: %s", exc)

    # -- Step Execution ------------------------------------------------------

    def execute_step(
        self,
        step: dict[str, Any],
        captured_vars: dict[str, Any] | None = None,
    ) -> SimulatorStepResult:
        """Execute a single iOS simulator step."""
        step_id = step.get("id", "unknown")
        goal = step.get("goal", "")
        max_actions = step.get("max_actions", 20)
        max_duration = step.get("max_duration_seconds", 120)
        actions_spec = step.get("actions", [])

        logger.info("Simulator step %s: goal=%s, %d scripted actions", step_id, goal[:80], len(actions_spec))

        screenshots: list[str] = []
        ux_observations: list[str] = []
        actions_taken: list[dict[str, Any]] = []
        findings: list[Finding] = []
        goal_achieved = False
        error_msg: str | None = None

        start_time = time.monotonic()
        prev_ss_hash: str | None = None
        consecutive_stuck = 0
        max_stuck = 5

        action_idx = 0
        for action_spec in actions_spec:
            if action_idx >= max_actions:
                error_msg = f"Max actions ({max_actions}) reached"
                break

            elapsed = time.monotonic() - start_time
            if elapsed > max_duration:
                error_msg = f"Step timed out after {elapsed:.0f}s (limit: {max_duration}s)"
                break

            ss_path = self._take_screenshot(step_id, action_idx, "before")
            if ss_path:
                screenshots.append(ss_path)
                ss_hash = self._hash_file(ss_path)
                if ss_hash == prev_ss_hash:
                    consecutive_stuck += 1
                else:
                    consecutive_stuck = 0
                prev_ss_hash = ss_hash

                if consecutive_stuck >= max_stuck:
                    error_msg = f"App stuck: no visual change for {consecutive_stuck} actions"
                    break

            action_type = action_spec.get("action", "")
            target = action_spec.get("target", "")
            value = action_spec.get("value", "")

            action_start = time.monotonic()
            success = False
            action_error: str | None = None

            try:
                if action_type == "tap":
                    x = action_spec.get("x", 0)
                    y = action_spec.get("y", 0)
                    success = self._action_tap(x, y)
                elif action_type == "type":
                    success = self._action_type_text(value)
                elif action_type == "key":
                    success = self._action_send_key(value)
                elif action_type == "swipe":
                    x1 = action_spec.get("x1", 200)
                    y1 = action_spec.get("y1", 400)
                    x2 = action_spec.get("x2", 200)
                    y2 = action_spec.get("y2", 200)
                    duration = action_spec.get("duration", 0.3)
                    success = self._action_swipe(x1, y1, x2, y2, duration)
                elif action_type == "wait":
                    wait_secs = float(value) if value else 1.0
                    time.sleep(wait_secs)
                    success = True
                elif action_type == "home":
                    success = self._action_home()
                elif action_type == "done":
                    goal_achieved = True
                    success = True
                else:
                    action_error = f"Unknown action type: {action_type}"
            except Exception as exc:
                action_error = str(exc)
                logger.error("Action %s failed: %s", action_type, exc, exc_info=True)

            action_duration = time.monotonic() - action_start
            actions_taken.append({
                "index": action_idx,
                "action": action_type,
                "target": target,
                "value": value,
                "success": success,
                "error": action_error,
                "duration_ms": round(action_duration * 1000, 1),
            })

            if goal_achieved:
                break

            time.sleep(0.5)
            action_idx += 1

        final_ss = self._take_screenshot(step_id, action_idx, "final")
        if final_ss:
            screenshots.append(final_ss)

        if not goal_achieved and error_msg is None:
            error_msg = "All scripted actions completed but goal not explicitly achieved"

        duration = round(time.monotonic() - start_time, 2)
        passed = goal_achieved and error_msg is None

        return SimulatorStepResult(
            step_id=step_id,
            passed=passed,
            screenshots=screenshots,
            ux_observations=ux_observations,
            actions_taken=actions_taken,
            action_count=action_idx,
            duration_seconds=duration,
            findings=findings,
            error=error_msg,
            goal_achieved=goal_achieved,
        )

    def to_step_report(self, result: SimulatorStepResult, description: str = "") -> StepReport:
        """Convert a SimulatorStepResult into a generic StepReport."""
        return StepReport(
            step_id=result.step_id,
            description=description,
            mode="ios_simulator",
            passed=result.passed,
            duration_seconds=result.duration_seconds,
            error=result.error,
            notes=f"{result.action_count} actions, {'goal achieved' if result.goal_achieved else 'goal NOT achieved'}",
            action_count=result.action_count,
            screenshots=result.screenshots,
            ux_observations=result.ux_observations,
            actions_taken=result.actions_taken,
        )

    # -- simctl Helpers ------------------------------------------------------

    def _simctl(self, *args: str) -> subprocess.CompletedProcess[str]:
        cmd = ["xcrun", "simctl", *args]
        logger.debug("simctl: %s", " ".join(cmd))
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if result.returncode != 0:
            logger.warning("simctl %s returned %d: %s", args[0], result.returncode, result.stderr.strip())
        return result

    def _list_devices(self) -> dict[str, Any]:
        result = self._simctl("list", "devices", "--json")
        try:
            data = json.loads(result.stdout)
            return data.get("devices", {})
        except (json.JSONDecodeError, KeyError):
            return {}

    def _find_best_device(self) -> str | None:
        devices = self._list_devices()
        candidates: list[tuple[str, str, str]] = []

        for runtime, device_list in devices.items():
            for device in device_list:
                if not device.get("isAvailable", False):
                    continue
                name = device.get("name", "")
                udid = device.get("udid", "")
                if self._device_name.lower() not in name.lower():
                    continue
                if self._os_version and self._os_version not in runtime:
                    continue
                candidates.append((udid, name, runtime))

        if not candidates:
            for runtime, device_list in devices.items():
                for device in device_list:
                    if device.get("isAvailable", False) and "iPhone" in device.get("name", ""):
                        return device.get("udid")
            return None

        for udid, name, runtime in candidates:
            if self._is_device_booted(udid):
                logger.info("Found booted device: %s (%s)", name, runtime)
                return udid

        udid, name, runtime = candidates[0]
        logger.info("Selected device: %s (%s)", name, runtime)
        return udid

    def _is_booted(self) -> bool:
        if self._device_id is None:
            return False
        return self._is_device_booted(self._device_id)

    def _is_device_booted(self, udid: str) -> bool:
        devices = self._list_devices()
        for device_list in devices.values():
            for device in device_list:
                if device.get("udid") == udid:
                    return device.get("state") == "Booted"
        return False

    def _wait_for_boot(self, timeout: int = 60) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self._is_booted():
                logger.info("Simulator %s booted successfully", self._device_id)
                return
            time.sleep(1)
        raise RuntimeError(f"Simulator {self._device_id} did not boot within {timeout}s")

    # -- Actions -------------------------------------------------------------

    def _action_tap(self, x: int | float, y: int | float) -> bool:
        try:
            script = (
                f'tell application "Simulator"\n'
                f"  activate\n"
                f"end tell\n"
                f'tell application "System Events"\n'
                f'  tell process "Simulator"\n'
                f"    click at {{{int(x)}, {int(y)}}}\n"
                f"  end tell\n"
                f"end tell"
            )
            subprocess.run(["osascript", "-e", script], capture_output=True, timeout=10)
            time.sleep(0.3)
            return True
        except Exception as exc:
            logger.warning("Tap at (%s, %s) failed: %s", x, y, exc)
            return False

    def _action_type_text(self, text: str) -> bool:
        if not text:
            return True
        try:
            escaped = text.replace("\\", "\\\\").replace('"', '\\"')
            script = (
                f'tell application "Simulator"\n'
                f"  activate\n"
                f"end tell\n"
                f'tell application "System Events"\n'
                f'  keystroke "{escaped}"\n'
                f"end tell"
            )
            subprocess.run(["osascript", "-e", script], capture_output=True, timeout=10)
            time.sleep(0.3)
            return True
        except Exception as exc:
            logger.warning("Type text failed: %s", exc)
            return False

    def _action_send_key(self, key_name: str) -> bool:
        if self._device_id is None:
            return False
        key_map: dict[str, str] = {
            "return": "return", "enter": "return", "tab": "tab",
            "delete": "delete", "backspace": "delete", "escape": "escape",
            "esc": "escape", "home": "home", "space": "space",
            "up": "up", "down": "down", "left": "left", "right": "right",
        }
        simctl_key = key_map.get(key_name.lower().strip())
        if simctl_key is None:
            logger.warning("Unknown key name for simctl: '%s'", key_name)
            return False
        try:
            result = subprocess.run(
                ["xcrun", "simctl", "io", self._device_id, "sendkey", simctl_key],
                capture_output=True, text=True, timeout=10,
            )
            if result.returncode != 0:
                return self._applescript_key(simctl_key)
            time.sleep(0.2)
            return True
        except Exception as exc:
            logger.warning("Send key '%s' failed: %s", key_name, exc)
            return False

    def _action_swipe(
        self,
        x1: int | float, y1: int | float,
        x2: int | float, y2: int | float,
        duration: float = 0.3,
    ) -> bool:
        try:
            steps = max(int(duration / 0.02), 5)
            dx = (x2 - x1) / steps
            dy = (y2 - y1) / steps
            lines = [
                'tell application "Simulator"', "  activate", "end tell",
                'tell application "System Events"', '  tell process "Simulator"',
            ]
            for i in range(steps + 1):
                cx = int(x1 + dx * i)
                cy = int(y1 + dy * i)
                if i == 0:
                    lines.append(f"    click at {{{cx}, {cy}}}")
            lines.extend(["  end tell", "end tell"])
            subprocess.run(["osascript", "-e", "\n".join(lines)], capture_output=True, timeout=10)
            time.sleep(duration + 0.2)
            return True
        except Exception as exc:
            logger.warning("Swipe failed: %s", exc)
            return False

    def _action_home(self) -> bool:
        if self._device_id is None:
            return False
        try:
            script = (
                'tell application "Simulator"\n  activate\nend tell\n'
                'tell application "System Events"\n'
                "  key code 4 using {command down, shift down}\nend tell"
            )
            subprocess.run(["osascript", "-e", script], capture_output=True, timeout=10)
            time.sleep(0.5)
            return True
        except Exception as exc:
            logger.warning("Home button failed: %s", exc)
            return False

    def _applescript_key(self, key_name: str) -> bool:
        key_code_map: dict[str, int] = {
            "return": 36, "tab": 48, "delete": 51, "escape": 53,
            "space": 49, "up": 126, "down": 125, "left": 123, "right": 124,
        }
        code = key_code_map.get(key_name)
        if code is None:
            return False
        try:
            script = (
                'tell application "Simulator"\n  activate\nend tell\n'
                f'tell application "System Events"\n  key code {code}\nend tell'
            )
            subprocess.run(["osascript", "-e", script], capture_output=True, timeout=10)
            time.sleep(0.2)
            return True
        except Exception as exc:
            logger.warning("AppleScript key '%s' failed: %s", key_name, exc)
            return False

    # -- Screenshot ----------------------------------------------------------

    def _take_screenshot(self, step_id: str, action_idx: int, label: str) -> str | None:
        if self._device_id is None:
            return None
        self._evidence_dir.mkdir(parents=True, exist_ok=True)
        filename = f"{step_id}-{action_idx:03d}-{label}.png"
        filepath = self._evidence_dir / filename
        try:
            result = subprocess.run(
                ["xcrun", "simctl", "io", self._device_id, "screenshot", str(filepath)],
                capture_output=True, text=True, timeout=15,
            )
            if result.returncode != 0:
                logger.warning("simctl screenshot failed: %s", result.stderr.strip())
                return None
            return str(filepath)
        except Exception as exc:
            logger.warning("Screenshot failed: %s", exc)
            return None

    # -- Utilities -----------------------------------------------------------

    @staticmethod
    def _hash_file(filepath: str) -> str:
        try:
            h = hashlib.sha256()
            with open(filepath, "rb") as f:
                for chunk in iter(lambda: f.read(8192), b""):
                    h.update(chunk)
            return h.hexdigest()[:16]
        except Exception:
            return ""

    @classmethod
    def list_available_devices(cls) -> list[dict[str, Any]]:
        """List all available simulator devices."""
        try:
            result = subprocess.run(
                ["xcrun", "simctl", "list", "devices", "--json"],
                capture_output=True, text=True, timeout=30,
            )
            data = json.loads(result.stdout)
            devices_by_runtime = data.get("devices", {})
        except Exception:
            return []

        flat: list[dict[str, Any]] = []
        for runtime, device_list in devices_by_runtime.items():
            for device in device_list:
                if device.get("isAvailable", False):
                    flat.append({
                        "udid": device.get("udid", ""),
                        "name": device.get("name", ""),
                        "state": device.get("state", ""),
                        "runtime": runtime,
                    })
        return flat
