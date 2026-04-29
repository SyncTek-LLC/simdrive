"""Observe the current simulator state — screenshot + dimensions + optional logs."""
from __future__ import annotations

import os
import time
from dataclasses import dataclass
from pathlib import Path

from PIL import Image

from . import sim
from .window import WindowBounds, get_bounds


@dataclass
class Observation:
    screenshot_path: Path
    screenshot_w: int  # pixels
    screenshot_h: int  # pixels
    window_bounds: WindowBounds | None  # macOS coords; None if window absent
    captured_at: float  # epoch seconds
    recent_logs: str | None = None

    def to_dict(self) -> dict:
        return {
            "screenshot_path": str(self.screenshot_path),
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
            "recent_logs": self.recent_logs,
        }


def observe(
    udid: str,
    out_dir: Path,
    capture_logs: bool = False,
    log_lines: int = 50,
    log_predicate: str | None = None,
) -> Observation:
    """Capture a screenshot + measure it; optionally tail logs."""
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = int(time.time() * 1000)
    dest = out_dir / f"observe-{ts}.png"
    sim.screenshot(udid, dest)
    with Image.open(dest) as im:
        w, h = im.size

    bounds: WindowBounds | None
    try:
        bounds = get_bounds()
    except Exception:
        bounds = None

    logs_text: str | None = None
    if capture_logs:
        try:
            logs_text = sim.get_log_tail(udid, lines=log_lines, predicate=log_predicate)
        except Exception as exc:
            logs_text = f"<log capture failed: {exc}>"

    return Observation(
        screenshot_path=dest,
        screenshot_w=w,
        screenshot_h=h,
        window_bounds=bounds,
        captured_at=time.time(),
        recent_logs=logs_text,
    )
