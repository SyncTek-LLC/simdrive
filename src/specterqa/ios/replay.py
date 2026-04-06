"""Replay recording and playback for SpecterQA iOS.

Records MCP tool calls during a session, saves as YAML replay files.
Replays execute deterministically in CI without AI.

Record flow (MCP mode):
    ios_start_session  → ReplayRecorder is created
    ios_tap / swipe / type / ...  → each action is appended to the session
    ios_save_replay  → writes .specterqa/replays/<name>.yaml

Replay flow (CI mode):
    specterqa-ios replay <file>  → ReplayPlayer reads the YAML, starts the
                                   XCTest runner, executes each step, verifies
                                   checkpoints.  No AI needed.

Exit codes returned by ReplayPlayer.run():
    0  — all steps passed
    1  — one or more steps failed (assertion, exception)
    2  — UI changed (element label not found — re-record recommended)
"""

from __future__ import annotations

import time
import yaml
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class ReplayStep:
    """A single recorded action in a replay session."""

    action: str  # tap | swipe | swipe_back | type | press_key | long_press
    timestamp: float = 0.0
    # Tap / long_press
    element_index: Optional[int] = None
    element_label: Optional[str] = None
    x: Optional[float] = None
    y: Optional[float] = None
    # Swipe
    direction: Optional[str] = None
    # Type
    text: Optional[str] = None
    # Press key
    key: Optional[str] = None
    # Long press hold duration
    duration: Optional[float] = None
    # Checkpoint — expected element labels visible after this action completes
    expect_elements: list[str] = field(default_factory=list)


@dataclass
class ReplaySession:
    """A complete recorded test session."""

    name: str = ""
    bundle_id: str = ""
    device_id: str = "booted"
    recorded_at: str = ""
    steps: list[ReplayStep] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Recorder
# ---------------------------------------------------------------------------


class ReplayRecorder:
    """Records MCP tool calls during a live session.

    One instance is created per ios_start_session call and lives for the
    duration of that session.  Call save() (or trigger ios_save_replay) to
    persist the session as a YAML file.
    """

    def __init__(self, bundle_id: str = "", device_id: str = "booted") -> None:
        self.session = ReplaySession(
            bundle_id=bundle_id,
            device_id=device_id,
            recorded_at=time.strftime("%Y-%m-%dT%H:%M:%S"),
        )

    # ── Individual action recorders ────────────────────────────────────────

    def record_tap(
        self,
        element_index: int,
        label: str,
        x: float,
        y: float,
    ) -> None:
        """Record a tap action."""
        self.session.steps.append(
            ReplayStep(
                action="tap",
                timestamp=time.time(),
                element_index=element_index,
                element_label=label,
                x=x,
                y=y,
            )
        )

    def record_swipe(self, direction: str) -> None:
        """Record a cardinal-direction swipe."""
        self.session.steps.append(
            ReplayStep(
                action="swipe",
                timestamp=time.time(),
                direction=direction,
            )
        )

    def record_swipe_back(self) -> None:
        """Record an iOS back-navigation swipe."""
        self.session.steps.append(
            ReplayStep(action="swipe_back", timestamp=time.time())
        )

    def record_type(self, text: str) -> None:
        """Record a text-input action."""
        self.session.steps.append(
            ReplayStep(action="type", timestamp=time.time(), text=text)
        )

    def record_press_key(self, key: str) -> None:
        """Record a named key-press (return, delete, tab, …)."""
        self.session.steps.append(
            ReplayStep(action="press_key", timestamp=time.time(), key=key)
        )

    def record_long_press(
        self,
        element_index: int,
        label: str,
        x: float,
        y: float,
        duration: float,
    ) -> None:
        """Record a long-press action."""
        self.session.steps.append(
            ReplayStep(
                action="long_press",
                timestamp=time.time(),
                element_index=element_index,
                element_label=label,
                x=x,
                y=y,
                duration=duration,
            )
        )

    # ── Checkpoint ────────────────────────────────────────────────────────

    def add_checkpoint(self, element_labels: list[str]) -> None:
        """Attach expected visible element labels to the last recorded step.

        During replay, the player will assert that all listed labels appear
        in the element tree after the step executes.
        """
        if self.session.steps:
            self.session.steps[-1].expect_elements = list(element_labels)

    # ── Persistence ───────────────────────────────────────────────────────

    def save(self, path: str, name: str = "") -> Path:
        """Serialize the session to a YAML replay file.

        Args:
            path: Output file path.  Parent directories are created if needed.
            name: Human-readable test name stored in the replay header.
                  Defaults to the filename stem if omitted.

        Returns:
            The resolved Path of the written file.
        """
        if name:
            self.session.name = name

        out = Path(path)
        out.parent.mkdir(parents=True, exist_ok=True)

        # Build a clean step list — skip None fields and empty defaults so the
        # YAML stays human-readable.
        steps = []
        for step in self.session.steps:
            raw = asdict(step)
            cleaned = {
                k: v
                for k, v in raw.items()
                if v is not None
                and v != []
                and not (k == "timestamp" and v == 0.0)
                and k != "timestamp"  # timestamps are write-only; omit from replay
            }
            steps.append(cleaned)

        data = {
            "replay": {
                "name": self.session.name,
                "bundle_id": self.session.bundle_id,
                "device_id": self.session.device_id,
                "recorded_at": self.session.recorded_at,
                "steps": steps,
            }
        }

        out.write_text(yaml.dump(data, default_flow_style=False, sort_keys=False))
        return out


# ---------------------------------------------------------------------------
# Player
# ---------------------------------------------------------------------------


class ReplayPlayer:
    """Execute a recorded YAML replay without AI.

    Loads the replay file, starts the XCTest runner, and executes each step
    in sequence.  Checkpoints are verified after the step's UI settles.

    Exit semantics (via ``result["exit_code"]``):
        0  — all steps passed
        1  — one or more steps failed or raised an exception
        2  — element label not found (UI changed since recording)
    """

    def __init__(self, replay_path: str) -> None:
        self.path = Path(replay_path)
        with self.path.open() as fh:
            data = yaml.safe_load(fh)

        r = data["replay"]
        self.bundle_id: str = r["bundle_id"]
        self.device_id: str = r.get("device_id", "booted")
        self.name: str = r.get("name", self.path.stem)
        self.steps: list[dict] = r.get("steps", [])

    # ── Internal helpers ──────────────────────────────────────────────────

    @staticmethod
    def _find_by_label(elements: list, label: str):
        """Return the first element whose label matches *label*, or None."""
        return next((e for e in elements if e.label == label), None)

    def _execute_step(
        self,
        step: dict,
        backend,
        annotator,
        result: dict,
    ) -> dict:
        """Execute one step and return a step-result dict."""
        action = step["action"]
        step_result: dict = {"action": action, "passed": True, "error": None}

        try:
            if action == "tap":
                self._exec_tap(step, backend, annotator, result)

            elif action == "swipe":
                direction = step.get("direction", "down")
                cx, cy, offset = 195, 422, 200
                coords = {
                    "down":  (cx, cy + offset, cx, cy - offset),
                    "up":    (cx, cy - offset, cx, cy + offset),
                    "left":  (cx + offset, cy, cx - offset, cy),
                    "right": (cx - offset, cy, cx + offset, cy),
                }
                x1, y1, x2, y2 = coords.get(direction, coords["down"])
                backend.swipe(x1, y1, x2, y2)

            elif action == "swipe_back":
                backend.swipe_back()

            elif action == "type":
                backend.type_text(step.get("text", ""))

            elif action == "press_key":
                backend.press_key(step.get("key", ""))

            elif action == "long_press":
                self._exec_long_press(step, backend, annotator, result)

            else:
                step_result["passed"] = False
                step_result["error"] = f"Unknown action: {action!r}"
                if result["exit_code"] == 0:
                    result["exit_code"] = 1
                return step_result

            # Checkpoint — verify expected labels are present in the UI
            expect = step.get("expect_elements", [])
            if expect:
                time.sleep(0.5)  # let UI settle before asserting
                elements = annotator.get_elements_from_runner()
                found_labels = {e.label for e in elements}
                missing = [lbl for lbl in expect if lbl not in found_labels]
                if missing:
                    step_result["passed"] = False
                    step_result["error"] = f"Missing expected elements: {missing}"
                    if result["exit_code"] == 0:
                        result["exit_code"] = 1

        except Exception as exc:
            step_result["passed"] = False
            step_result["error"] = str(exc)
            if result["exit_code"] == 0:
                result["exit_code"] = 1

        return step_result

    def _exec_tap(self, step: dict, backend, annotator, result: dict) -> None:
        """Resolve element by label (resilient) or recorded coords (fallback)."""
        label = step.get("element_label", "")
        elements = annotator.get_elements_from_runner()
        target = self._find_by_label(elements, label)

        if target is not None:
            cx = target.x + target.width / 2
            cy = target.y + target.height / 2
            backend.tap(cx, cy)
        elif step.get("x") is not None and step.get("y") is not None:
            # Coordinate fallback — works when label is empty or unlabelled
            backend.tap(step["x"], step["y"])
        else:
            # Element disappeared and no coords — flag as UI change
            if result["exit_code"] == 0:
                result["exit_code"] = 2
            raise RuntimeError(
                f"Element '{label}' not found — UI may have changed since recording"
            )

    def _exec_long_press(
        self, step: dict, backend, annotator, result: dict
    ) -> None:
        """Resolve element by label then perform a long press."""
        label = step.get("element_label", "")
        duration = float(step.get("duration", 1.0))
        elements = annotator.get_elements_from_runner()
        target = self._find_by_label(elements, label)

        if target is not None:
            cx = target.x + target.width / 2
            cy = target.y + target.height / 2
            backend.tap(cx, cy, duration=duration)
        elif step.get("x") is not None and step.get("y") is not None:
            backend.tap(step["x"], step["y"], duration=duration)
        else:
            if result["exit_code"] == 0:
                result["exit_code"] = 2
            raise RuntimeError(
                f"Element '{label}' not found — UI may have changed since recording"
            )

    # ── Public API ────────────────────────────────────────────────────────

    def run(self, verbose: bool = False) -> dict:
        """Execute the replay and return a result summary dict.

        Args:
            verbose: If True, print per-step status to stdout.

        Returns:
            {
                "name": str,
                "bundle_id": str,
                "steps": [{"action": str, "passed": bool, "error": str|None}, ...],
                "passed": bool,
                "exit_code": int,   # 0=pass, 1=fail, 2=UI changed
            }
        """
        from specterqa.ios.session_manager import TestSession
        from specterqa.ios.backends.xctest_client import XCTestBackend
        from specterqa.ios.som_annotator import SoMAnnotator

        result: dict = {
            "name": self.name,
            "bundle_id": self.bundle_id,
            "steps": [],
            "passed": True,
            "exit_code": 0,
        }

        session = TestSession(
            source_udid=self.device_id,
            bundle_id=self.bundle_id,
        )

        try:
            session.start()
            backend = XCTestBackend(port=session._port)
            annotator = SoMAnnotator(runner_url=session.runner_url)

            for i, step in enumerate(self.steps):
                action = step.get("action", "?")
                label = step.get(
                    "element_label",
                    step.get("direction", step.get("text", step.get("key", ""))),
                )

                if verbose:
                    print(f"  Step {i + 1}/{len(self.steps)}: {action} {label}")

                step_result = self._execute_step(step, backend, annotator, result)

                if not step_result["passed"]:
                    result["passed"] = False

                result["steps"].append(step_result)

                if verbose:
                    if step_result["passed"]:
                        print("    PASS")
                    else:
                        print(f"    FAIL: {step_result['error']}")

                time.sleep(0.3)  # brief inter-action pause

        except Exception as exc:
            result["passed"] = False
            result["exit_code"] = 1
            result["error"] = str(exc)

        finally:
            try:
                session.stop()
            except Exception:
                pass

        return result
