"""SpecterQA Simulator Action Executor.

Bundled in specterqa-ios — sourced from specterqa.engine (upstream unpublished).

Bridges federated AI decisions to iOS Simulator by implementing the
``ActionExecutor`` protocol.
"""

from __future__ import annotations

import logging
import re
import time
from pathlib import Path

from specterqa.engine.protocols import ActionResult, Decision
from specterqa.engine.simulator_runner import SimulatorRunner

logger = logging.getLogger("specterqa.engine.sim_action_executor")


class SimActionExecutor:
    """Maps ``Decision`` objects to iOS Simulator actions via ``SimulatorRunner``.

    Usage::

        runner = SimulatorRunner(bundle_id="com.example.myapp", evidence_dir=Path("/tmp"))
        runner.start()
        executor = SimActionExecutor(runner, evidence_dir=Path("/tmp"))
        result = executor.execute(decision)
        runner.stop()
    """

    def __init__(
        self,
        runner: SimulatorRunner,
        evidence_dir: Path | None = None,
    ) -> None:
        self._runner = runner
        self._evidence_dir = evidence_dir
        self._ss_counter = 0

    def execute(self, decision: Decision) -> ActionResult:
        """Execute a single AI decision against the iOS Simulator."""
        action = decision.action.lower().strip()
        hash_before = self._snapshot_hash("before")
        start = time.monotonic()
        success = False
        error: str | None = None

        try:
            if action in ("click", "tap"):
                coords = self._parse_coordinates(decision.target)
                if coords is None:
                    error = f"Could not parse coordinates from target: '{decision.target}'."
                else:
                    x, y = coords
                    success = self._runner._action_tap(x, y)

            elif action in ("fill", "type"):
                success = self._runner._action_type_text(decision.value)

            elif action in ("keyboard", "key", "press"):
                success = self._runner._action_send_key(decision.value)

            elif action in ("scroll", "swipe"):
                success = self._do_swipe(decision)

            elif action == "wait":
                wait_secs = self._parse_wait(decision.value)
                time.sleep(wait_secs)
                success = True

            elif action in ("done", "stuck"):
                success = True

            else:
                error = f"Unknown action type: {action}"

        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            logger.error("Simulator action '%s' on '%s' failed: %s", action, decision.target, exc, exc_info=True)

        duration_ms = round((time.monotonic() - start) * 1000, 1)
        hash_after = self._snapshot_hash("after")
        ui_changed = (hash_before != hash_after if (hash_before and hash_after) else True)

        return ActionResult(
            success=success and error is None,
            action=action,
            target=decision.target,
            error=error,
            duration_ms=duration_ms,
            ui_changed=ui_changed,
        )

    @staticmethod
    def _parse_coordinates(text: str) -> tuple[float, float] | None:
        """Extract x,y coordinates from a target description."""
        if not text:
            return None

        m = re.search(r"x\s*[=:]\s*(\d+(?:\.\d+)?)\s*[,;]\s*y\s*[=:]\s*(\d+(?:\.\d+)?)", text, re.I)
        if m:
            return float(m.group(1)), float(m.group(2))

        m = re.search(
            r"(?:at|approximately|around|near|position)\s+(\d{1,4}(?:\.\d+)?)\s*,\s*(\d{1,4}(?:\.\d+)?)",
            text, re.I,
        )
        if m:
            return float(m.group(1)), float(m.group(2))

        m = re.search(r"\(\s*(\d{1,4}(?:\.\d+)?)\s*,\s*(\d{1,4}(?:\.\d+)?)\s*\)", text)
        if m:
            return float(m.group(1)), float(m.group(2))

        m = re.search(r"(\d{1,4}(?:\.\d+)?)\s*,\s*(\d{1,4}(?:\.\d+)?)\s*$", text)
        if m:
            x, y = float(m.group(1)), float(m.group(2))
            if 0 <= x <= 3000 and 0 <= y <= 3000:
                return x, y

        return None

    def _do_swipe(self, decision: Decision) -> bool:
        direction_text = (decision.target + " " + decision.value).lower()
        cx, cy = 195, 422
        distance = 200

        if "up" in direction_text:
            x1, y1 = cx, cy + distance // 2
            x2, y2 = cx, cy - distance // 2
        elif "left" in direction_text:
            x1, y1 = cx + distance // 2, cy
            x2, y2 = cx - distance // 2, cy
        elif "right" in direction_text:
            x1, y1 = cx - distance // 2, cy
            x2, y2 = cx + distance // 2, cy
        else:
            x1, y1 = cx, cy - distance // 2
            x2, y2 = cx, cy + distance // 2

        return self._runner._action_swipe(x1, y1, x2, y2, duration=0.3)

    def _snapshot_hash(self, label: str) -> str:
        if self._evidence_dir is None:
            return ""
        self._ss_counter += 1
        ss_path = self._runner._take_screenshot(
            step_id="chg-detect",
            action_idx=self._ss_counter,
            label=label,
        )
        if ss_path is None:
            return ""
        return SimulatorRunner._hash_file(ss_path)

    @staticmethod
    def _parse_wait(value: str) -> float:
        if not value:
            return 1.0
        try:
            return max(0.1, float(value))
        except (ValueError, TypeError):
            return 1.0
