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

import logging
import time
import yaml
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger("specterqa.ios.replay")


# ---------------------------------------------------------------------------
# Visual regression helpers
# ---------------------------------------------------------------------------


def screenshot_diff(b64_a: str, b64_b: str) -> float:
    """Return percent difference between two base64 PNGs.

    Uses Pillow's ImageChops.difference to count non-zero pixels.
    Returns 0.0 for identical images, 100.0 if sizes differ.
    Raises ImportError if Pillow is not installed.

    Args:
        b64_a: Base-64 encoded PNG string (baseline).
        b64_b: Base-64 encoded PNG string (current capture).

    Returns:
        Float percentage of differing pixels (0.0–100.0).
    """
    import base64
    import io

    from PIL import Image, ImageChops

    img_a = Image.open(io.BytesIO(base64.b64decode(b64_a)))
    img_b = Image.open(io.BytesIO(base64.b64decode(b64_b)))

    if img_a.size != img_b.size:
        return 100.0  # different sizes = 100% diff

    diff = ImageChops.difference(img_a, img_b)
    bbox = diff.getbbox()
    if bbox is None:
        return 0.0  # identical

    # Count non-zero pixels across all channels
    histogram = diff.histogram()
    # Each channel contributes 256 histogram buckets; index 0 = black (no diff)
    channels = img_a.mode  # e.g. 'RGB' = 3 channels
    n_channels = len(channels)
    diff_pixels = 0
    for ch in range(n_channels):
        offset = ch * 256
        diff_pixels += sum(histogram[offset + i] for i in range(1, 256))
    total_pixels = img_a.size[0] * img_a.size[1] * n_channels
    return (diff_pixels / total_pixels) * 100


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
    expect_element_state: dict = field(default_factory=dict)  # {"label": {"enabled": True, "selected": False}}
    # Wait for element before executing this step
    wait_for: Optional[dict] = None  # {"label": "Read", "timeout": 10}
    # Per-step timeout override (seconds); None = use session default
    step_timeout: Optional[float] = None
    # Conditional execution — skip this step if condition is not met
    if_element_visible: Optional[str] = None  # execute only when element is present
    if_not_element_visible: Optional[str] = None  # execute only when element is absent
    # Step identity and jump target for conditional branching
    step_id: Optional[str] = None  # ID for this step (referenced by skip_to)
    skip_to: Optional[str] = None  # action: jump to step with matching step_id
    # Visual regression — compare current screenshot against a saved baseline
    expect_screenshot: Optional[str] = None  # baseline filename, e.g. "home_screen.png"
    screenshot_threshold: float = 5.0  # max allowed % pixel difference


@dataclass
class ReplaySession:
    """A complete recorded test session."""

    name: str = ""
    bundle_id: str = ""
    device_id: str = "booted"
    recorded_at: str = ""
    steps: list[ReplayStep] = field(default_factory=list)
    # Replay-level defaults
    settle_timeout: float = 2.0  # seconds to wait for UI to settle before assertions
    step_timeout: float = 10.0  # default per-step execution timeout (seconds)


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
        self.session.steps.append(ReplayStep(action="swipe_back", timestamp=time.time()))

    def record_type(self, text: str) -> None:
        """Record a text-input action."""
        self.session.steps.append(ReplayStep(action="type", timestamp=time.time(), text=text))

    def record_press_key(self, key: str) -> None:
        """Record a named key-press (return, delete, tab, …)."""
        self.session.steps.append(ReplayStep(action="press_key", timestamp=time.time(), key=key))

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

    def record_baseline_screenshot(self, name: str, b64: str, baseline_dir: str = "") -> Path:
        """Save a baseline screenshot alongside the replay file for visual regression.

        The baseline is stored as a PNG file.  During replay, steps with
        ``expect_screenshot: <name>`` will compare the live screenshot against
        this baseline using ``screenshot_diff()``.

        Args:
            name:         Filename for the baseline (e.g. ``"home_screen.png"``).
                          The ``.png`` extension is added if absent.
            b64:          Base-64 encoded PNG string to save as the baseline.
            baseline_dir: Directory to write the file into.
                          Defaults to ``.specterqa/baselines/``.

        Returns:
            The resolved Path of the written baseline file.
        """
        import base64

        out_dir = Path(baseline_dir) if baseline_dir else Path(".specterqa/baselines")
        out_dir.mkdir(parents=True, exist_ok=True)
        if not name.endswith(".png"):
            name = f"{name}.png"
        out_path = out_dir / name
        out_path.write_bytes(base64.b64decode(b64))
        return out_path

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
        for var_field in ("element_label", "text", "key"):
            if var_field in resolved and isinstance(resolved[var_field], str):
                resolved[var_field] = self.resolve_vars(resolved[var_field], variables)
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

    @staticmethod
    def _normalize_maestro_step(step: dict) -> dict:
        """Translate Maestro-compatible YAML shortcuts into native step format.

        Supports these Maestro aliases:

        - ``tapOn: "Label"``              → action: tap, label: Label
        - ``assertVisible: "Label"``      → action: assert, expect_elements: [Label]
        - ``assertNotVisible: "Label"``   → action: assert, expect_not_elements: [Label]
        - ``inputText: "hello"``          → action: type, text: hello
        - ``waitFor: "Label"``            → action: wait_for_element, label: Label

        The step dict is modified in-place and returned.
        """
        if "tapOn" in step:
            step.setdefault("action", "tap")
            step.setdefault("element_label", step.pop("tapOn"))
        if "assertVisible" in step:
            step.setdefault("action", "assert")
            step.setdefault("expect_elements", [])
            val = step.pop("assertVisible")
            if isinstance(val, list):
                step["expect_elements"].extend(val)
            else:
                step["expect_elements"].append(val)
        if "assertNotVisible" in step:
            step.setdefault("action", "assert")
            step.setdefault("expect_not_elements", [])
            val = step.pop("assertNotVisible")
            if isinstance(val, list):
                step["expect_not_elements"].extend(val)
            else:
                step["expect_not_elements"].append(val)
        if "inputText" in step:
            step.setdefault("action", "type")
            step.setdefault("text", step.pop("inputText"))
        if "waitFor" in step:
            step.setdefault("action", "wait_for_element")
            step.setdefault("label", step.pop("waitFor"))
        return step

    def _execute_step(
        self,
        step: dict,
        backend,
        annotator,
        result: dict,
        variables: Optional[dict] = None,
    ) -> dict:
        """Execute one step and return a step-result dict."""
        # Normalise Maestro-compatible YAML shortcuts before anything else
        step = self._normalize_maestro_step(dict(step))

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
                        "down": (cx, cy + offset, cx, cy - offset),
                        "up": (cx, cy - offset, cx, cy + offset),
                        "left": (cx + offset, cy, cx - offset, cy),
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

                elif action == "wait_for_element":
                    wf_label = step.get("label", "")
                    wf_timeout = float(step.get("timeout", 10))
                    if not wf_label:
                        exec_error.append(("unknown", "wait_for_element requires 'label'"))
                        return
                    if not self._wait_for_label(annotator, wf_label, wf_timeout):
                        exec_error.append(
                            (
                                "unknown",
                                f"Element '{wf_label}' not found within {wf_timeout}s",
                            )
                        )
                    return

                elif action == "assert":
                    # Pure-assertion step: no interaction — assertions are
                    # evaluated in the shared assertion block below.
                    pass

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
                    target = next((e for e in elements if lbl.lower() in e.label.lower()), None)
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
                                f"expect_element_value: '{lbl}' value '{actual_val}' does not contain '{expected_val}'"
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
                    actual_count = sum(1 for e in elements if e.element_type.lower() == elem_type.lower())
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

        expect_state = step.get("expect_element_state", {})
        if expect_state:
            try:
                elements = annotator.get_elements_from_runner()
                for lbl, expected_props in expect_state.items():
                    target = next((e for e in elements if lbl.lower() in e.label.lower()), None)
                    if target is None:
                        step_result["passed"] = False
                        step_result["error"] = f"expect_element_state: element '{lbl}' not found"
                        if result["exit_code"] == 0:
                            result["exit_code"] = 1
                        break
                    for prop_key, expected_val in expected_props.items():
                        actual_val = getattr(target, prop_key, None)
                        if actual_val != expected_val:
                            step_result["passed"] = False
                            step_result["error"] = (
                                f"expect_element_state: '{lbl}'.{prop_key} "
                                f"expected {expected_val!r}, got {actual_val!r}"
                            )
                            if result["exit_code"] == 0:
                                result["exit_code"] = 1
                            break
            except Exception as exc:
                step_result["passed"] = False
                step_result["error"] = f"expect_element_state check failed: {exc}"
                if result["exit_code"] == 0:
                    result["exit_code"] = 1

        expect_screenshot_name = step.get("expect_screenshot")
        if expect_screenshot_name:
            try:
                threshold = float(step.get("screenshot_threshold", 5.0))
                baseline_dir = step.get("baseline_dir", ".specterqa/baselines")
                baseline_path = Path(baseline_dir) / expect_screenshot_name
                if not expect_screenshot_name.endswith(".png"):
                    baseline_path = Path(baseline_dir) / f"{expect_screenshot_name}.png"

                if not baseline_path.exists():
                    # Save current screenshot as baseline for future runs
                    raw = backend.screenshot()
                    b64_current = raw.get("base64") or raw.get("data") or raw.get("image", "")
                    if b64_current:
                        import base64 as _b64

                        baseline_path.parent.mkdir(parents=True, exist_ok=True)
                        baseline_path.write_bytes(_b64.b64decode(b64_current))
                        step_result["screenshot_note"] = f"Baseline saved: {baseline_path}"
                else:
                    import base64 as _b64

                    raw = backend.screenshot()
                    b64_current = raw.get("base64") or raw.get("data") or raw.get("image", "")
                    if b64_current:
                        b64_baseline = _b64.b64encode(baseline_path.read_bytes()).decode("ascii")
                        diff_pct = screenshot_diff(b64_baseline, b64_current)
                        step_result["screenshot_diff_pct"] = round(diff_pct, 2)
                        if diff_pct > threshold:
                            step_result["passed"] = False
                            step_result["error"] = (
                                f"Visual regression: {diff_pct:.1f}% pixel difference "
                                f"exceeds threshold {threshold}% for '{expect_screenshot_name}'"
                            )
                            if result["exit_code"] == 0:
                                result["exit_code"] = 1
            except ImportError:
                step_result["screenshot_note"] = (
                    "Visual regression skipped — Pillow not installed. Install with: pip install Pillow"
                )
            except Exception as exc:
                step_result["passed"] = False
                step_result["error"] = f"expect_screenshot check failed: {exc}"
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
            raise RuntimeError(f"Element '{label}' not found — UI may have changed since recording")

    def _exec_long_press(self, step: dict, backend, annotator, result: dict) -> None:
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
            raise RuntimeError(f"Element '{label}' not found — UI may have changed since recording")

    # ── Public API ────────────────────────────────────────────────────────

    def run_with_session(self, session, verbose: bool = False, variables: Optional[dict] = None) -> dict:
        """Execute the replay using an already-started *session*.

        Behaves identically to ``run()`` but skips ``session.start()`` and
        ``session.stop()``.  The caller is responsible for lifecycle management.

        This is used by the ``ci --reuse-runner`` flag to keep the XCTest
        runner alive across multiple replays, avoiding the ~10 s cold-start
        penalty per replay.

        Args:
            session:   A started ``TestSession`` instance.
            verbose:   If True, print per-step status to stdout.
            variables: Optional ${VAR} substitution dict.

        Returns:
            Same structure as ``run()``.
        """
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

        backend = XCTestBackend(port=session._port)
        annotator = SoMAnnotator(runner_url=session.runner_url)

        try:
            i = 0
            while i < len(self.steps):
                step = self.steps[i]
                action = step.get("action", "?")
                label = step.get(
                    "element_label",
                    step.get("direction", step.get("text", step.get("key", ""))),
                )

                if verbose:
                    logger.debug("  Step %d/%d: %s %s", i + 1, len(self.steps), action, label)

                # Conditional execution guards
                if_visible = step.get("if_element_visible")
                if if_visible:
                    try:
                        elements = annotator.get_elements_from_runner()
                        if not any(if_visible.lower() in e.label.lower() for e in elements):
                            step_result: dict = {
                                "action": action,
                                "passed": True,
                                "error": None,
                                "status": "skipped",
                                "reason": f"if_element_visible: '{if_visible}' not present",
                            }
                            result["steps"].append(step_result)
                            i += 1
                            continue
                    except Exception:
                        pass

                if_not_visible = step.get("if_not_element_visible")
                if if_not_visible:
                    try:
                        elements = annotator.get_elements_from_runner()
                        if any(if_not_visible.lower() in e.label.lower() for e in elements):
                            step_result = {
                                "action": action,
                                "passed": True,
                                "error": None,
                                "status": "skipped",
                                "reason": f"if_not_element_visible: '{if_not_visible}' present",
                            }
                            result["steps"].append(step_result)
                            i += 1
                            continue
                    except Exception:
                        pass

                # skip_to action — jump to step with matching step_id
                if action == "skip_to":
                    target_id = step.get("step_id", "")
                    target_idx = next(
                        (j for j, s in enumerate(self.steps) if s.get("step_id") == target_id),
                        None,
                    )
                    if target_idx is not None and target_idx > i:
                        i = target_idx
                    else:
                        i += 1
                    continue

                step_result = self._execute_step(step, backend, annotator, result, variables=vars_dict)

                if not step_result["passed"]:
                    result["passed"] = False

                result["steps"].append(step_result)

                if verbose:
                    if step_result["passed"]:
                        logger.debug("    PASS")
                    else:
                        logger.debug("    FAIL: %s", step_result['error'])

                time.sleep(0.3)  # brief inter-action pause
                i += 1

        except Exception as exc:
            result["passed"] = False
            result["exit_code"] = 1
            result["error"] = str(exc)

        return result

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

            i = 0
            while i < len(self.steps):
                step = self.steps[i]
                action = step.get("action", "?")
                label = step.get(
                    "element_label",
                    step.get("direction", step.get("text", step.get("key", ""))),
                )

                if verbose:
                    logger.debug("  Step %d/%d: %s %s", i + 1, len(self.steps), action, label)

                # Conditional execution guards
                if_visible = step.get("if_element_visible")
                if if_visible:
                    try:
                        elements = annotator.get_elements_from_runner()
                        if not any(if_visible.lower() in e.label.lower() for e in elements):
                            step_result: dict = {
                                "action": action,
                                "passed": True,
                                "error": None,
                                "status": "skipped",
                                "reason": f"if_element_visible: '{if_visible}' not present",
                            }
                            result["steps"].append(step_result)
                            i += 1
                            continue
                    except Exception:
                        pass

                if_not_visible = step.get("if_not_element_visible")
                if if_not_visible:
                    try:
                        elements = annotator.get_elements_from_runner()
                        if any(if_not_visible.lower() in e.label.lower() for e in elements):
                            step_result = {
                                "action": action,
                                "passed": True,
                                "error": None,
                                "status": "skipped",
                                "reason": f"if_not_element_visible: '{if_not_visible}' present",
                            }
                            result["steps"].append(step_result)
                            i += 1
                            continue
                    except Exception:
                        pass

                # skip_to action — jump to step with matching step_id
                if action == "skip_to":
                    target_id = step.get("step_id", "")
                    target_idx = next(
                        (j for j, s in enumerate(self.steps) if s.get("step_id") == target_id),
                        None,
                    )
                    if target_idx is not None and target_idx > i:
                        i = target_idx
                    else:
                        i += 1
                    continue

                step_result = self._execute_step(step, backend, annotator, result, variables=vars_dict)

                if not step_result["passed"]:
                    result["passed"] = False

                result["steps"].append(step_result)

                if verbose:
                    if step_result["passed"]:
                        logger.debug("    PASS")
                    else:
                        logger.debug("    FAIL: %s", step_result['error'])

                time.sleep(0.3)  # brief inter-action pause
                i += 1

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
