"""Simulator window introspection + activation via AppleScript.

Bridges between simulator's logical-points coordinate system (what the agent
sees in screenshots) and macOS screen pixels (what cliclick clicks at).
"""
from __future__ import annotations

import subprocess
from dataclasses import dataclass


class WindowError(RuntimeError):
    """Raised when AppleScript window queries fail."""


@dataclass(frozen=True)
class WindowBounds:
    x: int  # macOS screen x of window's top-left
    y: int  # macOS screen y of window's top-left
    width: int  # window width in macOS points
    height: int  # window height in macOS points


_GET_BOUNDS_SCRIPT = '''
tell application "System Events"
  if not (exists process "Simulator") then return "no_process"
  tell process "Simulator"
    if (count of windows) = 0 then return "no_window"
    set p to position of window 1
    set s to size of window 1
    return (item 1 of p as string) & "," & (item 2 of p as string) & "," & (item 1 of s as string) & "," & (item 2 of s as string)
  end tell
end tell
'''


def _osa(script: str, timeout: float = 5.0) -> str:
    res = subprocess.run(
        ["osascript", "-e", script],
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    if res.returncode != 0:
        raise WindowError(f"osascript failed: {res.stderr.strip()}")
    return res.stdout.strip()


def activate() -> None:
    """Bring the Simulator app to the foreground. Required before each cliclick."""
    _osa('tell application "Simulator" to activate')


def get_bounds() -> WindowBounds:
    """Return current Simulator window position+size in macOS points.

    Raises WindowError if no Simulator process or no visible window.
    """
    raw = _osa(_GET_BOUNDS_SCRIPT)
    if raw in {"no_process", "no_window"}:
        raise WindowError(f"Simulator window not available ({raw})")
    parts = raw.split(",")
    if len(parts) != 4:
        raise WindowError(f"Unexpected bounds format: {raw!r}")
    try:
        x, y, w, h = (int(p) for p in parts)
    except ValueError as exc:
        raise WindowError(f"Could not parse bounds {raw!r}: {exc}") from exc
    return WindowBounds(x=x, y=y, width=w, height=h)


def points_to_screen(bounds: WindowBounds, point_x: float, point_y: float, device_w: int, device_h: int) -> tuple[int, int]:
    """Translate logical device points → macOS screen pixels for a click target.

    Assumes the simulator window content fills the window (no chrome we need to subtract).
    Recent simulator versions have transparent chrome; the bezel is part of the rendered
    image. The window's macOS bounds correspond to the device's logical points.
    """
    if device_w <= 0 or device_h <= 0:
        raise WindowError(f"Invalid device size: {device_w}x{device_h}")
    sx = bounds.x + (point_x / device_w) * bounds.width
    sy = bounds.y + (point_y / device_h) * bounds.height
    return int(round(sx)), int(round(sy))
