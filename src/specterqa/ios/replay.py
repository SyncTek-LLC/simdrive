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
    # Rich assertions (checked after the action's UI settles)
    expect_not_elements: list[str] = field(default_factory=list)
    expect_element_value: dict = field(default_factory=dict)  # {"label": "expected_value"}
    expect_element_count: dict = field(default_factory=dict)  # {"Button": 3}
    # Wait for element before executing this step
    wait_for: Optional[dict] = None  # {"label": "Read", "timeout": 10}
    # Per-step timeout override (seconds); None = use session default
    step_timeout: Optional[float] = None


@dataclass
class ReplaySession:
    """A complete recorded test session."""

    name: str = ""
    bundle_id: str = ""
    device_id: str = "booted"
    recorded_at: str = ""
    steps: list[ReplayStep] = field(default_factory=list)
    # Replay-level defaults
    settle_timeout: float = 2.0   # seconds to wait for UI to settle before assertions
    step_timeout: float = 10.0    # default per-step execution timeout (seconds)


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
                and v != {}
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
        self.settle_timeout: float = float(r.get("settle_timeout", 2.0))
        self.step_timeout: float = float(r.get("step_timeout", 10.0))

    # ── Internal helpers ──────────────────────────────────────────────────

    @staticmethod
    def _find_by_label(elements: list, label: str):
        """Return the first element whose label matches *label*, or None."""
        return next((e for e in elements if e.label == label), None)

    @staticmethod
    def resolve_vars(text: str, variables: dict) -> str:
        """Substitute ${VAR} placeholders in *text* from *variables* dict."""
        for key, value in variables.items():
            text = text.replace(f"${{{key}}}", str(value))
        return text

    def _resolve_step_vars(self, step: dict, variables: dict) -> dict:
        """Return a shallow copy of *step* with variable substitution applied."""
        if not variables:
            return step
        resolved = dict(step)
        for field in ("element_label", "text", "key"):
            if field in resolved and isinstance(resolved[field], str):
                resolved[field] = self.resolve_vars(resolved[field], variables)
        return resolved

    def _wait_for_label(self, annotator, label: str, timeout: float) -> bool:
        """Poll until an element with *label* appears; return True if found."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                elements = annotator.get_elements_from_runner()
                if any(label.lower() in e.label.lower() for e in elements):
                    return True
            except Exception:
                pass
            time.sleep(0.5)
        return False

    def _execute_step(
        self,
        step: dict,
        backend,
        annotator,
        result: dict,
        variables: Optional[dict] = None,
    ) -> dict:
        """Execute one step and return a step-result dict."""
        if variables:
            step = self._resolve_step_vars(step, variables)

        action = step["action"]
        step_result: dict = {"action": action, "passed": True, "error": None}

        # Per-step timeout — wrap execution in a thread with join timeout
        step_timeout = float(step.get("step_timeout") or self.step_timeout)

        import threading

        exec_error: list = []  # mutable container for thread result

        def _do_execute() -> None:
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
                    exec_error.append(("unknown", f"Unknown action: {action!r}"))
                    return

            except Exception as exc:
                exec_error.append(("exception", str(exc)))

        # Wait-for-element before executing this step
        wait_for = step.get("wait_for")
        if wait_for:
            wf_label = wait_for.get("label", "")
            wf_timeout = float(wait_for.get("timeout", 10))
            if wf_label and not self._wait_for_label(annotator, wf_label, wf_timeout):
                step_result["passed"] = False
                step_result["error"] = f"wait_for: element '{wf_label}' not found within {wf_timeout}s"
                if result["exit_code"] == 0:
                    result["exit_code"] = 1
                return step_result

        t = threading.Thread(target=_do_execute, daemon=True)
        t.start()
        t.join(timeout=step_timeout)

        if t.is_alive():
            step_result["passed"] = False
            step_result["error"] = f"Step timed out after {step_timeout}s"
            if result["exit_code"] == 0:
                result["exit_code"] = 1
            return step_result

        if exec_error:
            kind, msg = exec_error[0]
            step_result["passed"] = False
            step_result["error"] = msg
            if result["exit_code"] == 0:
                result["exit_code"] = 1 if kind == "exception" else 1
            return step_result

        # ── Assertions (with settle + retry) ─────────────────────────────

        # Let UI settle before checking assertions
        settle = self.settle_timeout
        if settle > 0:
            time.sleep(min(settle, 1.0))  # initial settle

        expect = step.get("expect_elements", [])
        if expect:
            # Retry up to 3 times with 1s between
            missing = list(expect)
            for _attempt in range(3):
                try:
                    elements = annotator.get_elements_from_runner()
                    found_labels = {e.label for e in elements}
                    missing = [lbl for lbl in expect if lbl not in found_labels]
                    if not missing:
                        break
                except Exception:
                    pass
                if missing:
                    time.sleep(1.0)
            if missing:
                step_result["passed"] = False
                step_result["error"] = f"Missing expected elements: {missing}"
                if result["exit_code"] == 0:
                    result["exit_code"] = 1

        expect_not = step.get("expect_not_elements", [])
        if expect_not:
            try:
                elements = annotator.get_elements_from_runner()
                found_labels = {e.label for e in elements}
                present = [lbl for lbl in expect_not if lbl in found_labels]
                if present:
                    step_result["passed"] = False
                    step_result["error"] = f"Elements that should NOT be present: {present}"
                    if result["exit_code"] == 0:
                        result["exit_code"] = 1
            except Exception as exc:
                step_result["passed"] = False
                step_result["error"] = f"expect_not_elements check failed: {exc}"
                if result["exit_code"] == 0:
                    result["exit_code"] = 1

        expect_value = step.get("expect_element_value", {})
        if expect_value:
            try:
                elements = annotator.get_elements_from_runner()
                for lbl, expected_val in expect_value.items():
                    target = next(
                        (e for e in elements if lbl.lower() in e.label.lower()), None
                    )
                    if target is None:
                        step_result["passed"] = False
                        step_result["error"] = f"expect_element_value: element '{lbl}' not found"
                        if result["exit_code"] == 0:
                            result["exit_code"] = 1
                    else:
                        actual_val = getattr(target, "value", "") or ""
                        if str(expected_val) not in str(actual_val):
                            step_result["passed"] = False
                            step_result["error"] = (
                                f"expect_element_value: '{lbl}' value '{actual_val}' "
                                f"does not contain '{expected_val}'"
                            )
                            if result["exit_code"] == 0:
                                result["exit_code"] = 1
            except Exception as exc:
                step_result["passed"] = False
                step_result["error"] = f"expect_element_value check failed: {exc}"
                if result["exit_code"] == 0:
                    result["exit_code"] = 1

        expect_count = step.get("expect_element_count", {})
        if expect_count:
            try:
                elements = annotator.get_elements_from_runner()
                for elem_type, expected_count in expect_count.items():
                    actual_count = sum(
                        1 for e in elements
                        if e.element_type.lower() == elem_type.lower()
                    )
                    if actual_count != int(expected_count):
                        step_result["passed"] = False
                        step_result["error"] = (
                            f"expect_element_count: expected {expected_count} '{elem_type}' "
                            f"elements, found {actual_count}"
                        )
                        if result["exit_code"] == 0:
                            result["exit_code"] = 1
            except Exception as exc:
                step_result["passed"] = False
                step_result["error"] = f"expect_element_count check failed: {exc}"
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

    def run(self, verbose: bool = False, variables: Optional[dict] = None) -> dict:
        """Execute the replay and return a result summary dict.

        Args:
            verbose:   If True, print per-step status to stdout.
            variables: Optional dict of ${VAR} substitutions for replay values.
                       Keys are variable names (without braces), values are strings.

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

        vars_dict: dict = variables or {}

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

                step_result = self._execute_step(
                    step, backend, annotator, result, variables=vars_dict
                )

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
