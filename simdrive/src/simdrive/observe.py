"""Observe the current simulator state — screenshot + optional SoM annotation + logs."""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image

from . import sim, som
from .observability.logger import get_logger
from .observability.metrics import record_histogram
from .som import Mark
from .window import WindowBounds, get_bounds

log = get_logger("simdrive.observe")


@dataclass
class Observation:
    screenshot_path: Path
    annotated_path: Path | None  # SoM-annotated copy; None if SoM disabled
    screenshot_w: int
    screenshot_h: int
    window_bounds: WindowBounds | None
    captured_at: float
    marks: list[Mark] = field(default_factory=list)
    recent_logs: str | None = None

    def to_dict(self) -> dict:
        return {
            "screenshot_path": str(self.screenshot_path),
            "annotated_path": str(self.annotated_path) if self.annotated_path else None,
            "screenshot_size_pixels": [self.screenshot_w, self.screenshot_h],
            "window_bounds_macos": (
                {
                    "x": self.window_bounds.x,
                    "y": self.window_bounds.y,
                    "width": self.window_bounds.width,
                    "height": self.window_bounds.height,
                }
                if self.window_bounds
                else None
            ),
            "captured_at": self.captured_at,
            "marks": [m.to_dict() for m in self.marks],
            "recent_logs": self.recent_logs,
        }


def observe(
    udid: str,
    out_dir: Path,
    annotate: bool = True,
    capture_logs: bool = False,
    log_lines: int = 50,
    log_predicate: str | None = None,
    target: str = "simulator",
) -> Observation:
    """Capture a screenshot + measure it; optionally annotate with SoM marks; optionally tail logs.

    `target` selects the backend: "simulator" (default) or "device" (real iPhone/iPad).
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    _t_start = time.time()
    ts = int(_t_start * 1000)
    raw_path = out_dir / f"observe-{ts}.png"
    if target == "device":
        from . import device  # avoid import cost when not used
        device.screenshot(udid, raw_path)
    else:
        sim.screenshot(udid, raw_path)
    with Image.open(raw_path) as im:
        w, h = im.size

    bounds: WindowBounds | None
    try:
        bounds = get_bounds()
    except Exception:
        bounds = None

    marks: list[Mark] = []
    annotated_path: Path | None = None
    if annotate:
        marks = som.detect_marks(raw_path)
        if marks:
            annotated_path = out_dir / f"observe-{ts}-som.png"
            som.annotate(raw_path, marks, annotated_path)

    logs_text: str | None = None
    if capture_logs:
        try:
            if target == "device":
                from . import device
                logs_text = device.get_log_tail(udid, lines=log_lines, predicate=log_predicate)
            else:
                logs_text = sim.get_log_tail(udid, lines=log_lines, predicate=log_predicate)
        except Exception as exc:
            logs_text = f"<log capture failed: {exc}>"

    captured_at = time.time()
    latency_ms = (captured_at - _t_start) * 1000.0
    record_histogram("observe_latency_ms", latency_ms)
    log.debug(
        "observe complete",
        extra={"udid": udid, "latency_ms": round(latency_ms, 1),
               "marks_count": len(marks), "target": target},
    )

    obs = Observation(
        screenshot_path=raw_path,
        annotated_path=annotated_path,
        screenshot_w=w,
        screenshot_h=h,
        window_bounds=bounds,
        captured_at=captured_at,
        marks=marks,
        recent_logs=logs_text,
    )

    # Persist a sidecar JSON next to the screenshot so anyone reading the
    # session directory has the full structured observation, not just pixels.
    sidecar = raw_path.with_suffix(".json")
    try:
        sidecar.write_text(json.dumps(obs.to_dict(), indent=2))
    except Exception:
        pass  # never let sidecar persistence fail the observe call

    return obs
