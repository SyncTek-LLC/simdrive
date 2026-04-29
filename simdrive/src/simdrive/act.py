"""Synthetic input dispatch.

Internal module. Coordinates passed in are screenshot pixels from the most
recent observe; translated to macOS screen pixels via window bounds. Two
backends are available, selected at runtime — see `_backend()`.
"""
from __future__ import annotations

import subprocess
import time
from typing import Iterable

from . import pid_input
from .sim import cliclick_path
from .window import WindowBounds, activate, get_bounds


class ActError(RuntimeError):
    """Raised when an act dispatch fails."""


def _pixels_to_screen(
    bounds: WindowBounds, pixel_x: int, pixel_y: int, screenshot_w: int, screenshot_h: int
) -> tuple[int, int]:
    if screenshot_w <= 0 or screenshot_h <= 0:
        raise ActError(f"Invalid screenshot size: {screenshot_w}x{screenshot_h}")
    sx = bounds.x + (pixel_x / screenshot_w) * bounds.width
    sy = bounds.y + (pixel_y / screenshot_h) * bounds.height
    return int(round(sx)), int(round(sy))


def _backend() -> str:
    """Return which backend will be used for the next act call."""
    cap = pid_input.capability()
    if cap.background_capable:
        return "pid"
    return "cliclick"


def _resolve_pid_or_raise() -> int:
    cap = pid_input.capability()
    if cap.sim_pid is None:
        raise ActError(
            f"Simulator process not found. {cap.error or 'Open a sim first.'}"
        )
    return cap.sim_pid


def _run_cliclick(args: Iterable[str], timeout: float = 5.0) -> None:
    cli = cliclick_path()
    cmd = [cli, *args]
    res = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)
    if res.returncode != 0:
        raise ActError(f"cliclick failed (rc={res.returncode}): {res.stderr.strip() or res.stdout.strip()}")


def tap(pixel_x: int, pixel_y: int, screenshot_w: int, screenshot_h: int) -> tuple[int, int]:
    """Click at screenshot-pixel coordinates. Returns the macOS screen coords used."""
    bounds = get_bounds()
    sx, sy = _pixels_to_screen(bounds, pixel_x, pixel_y, screenshot_w, screenshot_h)
    if _backend() == "pid":
        pid_input.tap(_resolve_pid_or_raise(), sx, sy)
    else:
        activate()
        time.sleep(0.15)
        _run_cliclick([f"c:{sx},{sy}"])
    return sx, sy


def swipe(
    x1: int, y1: int, x2: int, y2: int, screenshot_w: int, screenshot_h: int, duration_ms: int = 300
) -> None:
    """Drag from (x1,y1)→(x2,y2) in screenshot-pixel coords."""
    bounds = get_bounds()
    sx1, sy1 = _pixels_to_screen(bounds, x1, y1, screenshot_w, screenshot_h)
    sx2, sy2 = _pixels_to_screen(bounds, x2, y2, screenshot_w, screenshot_h)
    if _backend() == "pid":
        pid_input.swipe(_resolve_pid_or_raise(), sx1, sy1, sx2, sy2, duration_ms=duration_ms)
        return

    activate()
    time.sleep(0.15)
    duration_ms = max(50, min(duration_ms, 5000))
    steps = max(2, duration_ms // 30)
    moves: list[str] = []
    for i in range(1, steps + 1):
        t = i / steps
        mx = int(round(sx1 + (sx2 - sx1) * t))
        my = int(round(sy1 + (sy2 - sy1) * t))
        moves.append(f"m:{mx},{my}")
    args = ["-w", "30", f"dd:{sx1},{sy1}", *moves, f"du:{sx2},{sy2}"]
    _run_cliclick(args, timeout=10.0)


def type_text(text: str) -> None:
    """Send keystrokes. Caller is responsible for tapping a focused field first."""
    if not text:
        return
    if _backend() == "pid":
        pid_input.type_text(_resolve_pid_or_raise(), text)
        return

    activate()
    time.sleep(0.15)
    _run_cliclick(["t:" + text])


_CLICLICK_KEY_MAP = {
    "return": "kp:return",
    "enter": "kp:return",
    "tab": "kp:tab",
    "escape": "kp:esc",
    "esc": "kp:esc",
    "space": "kp:space",
    "delete": "kp:delete",
    "backspace": "kp:delete",
    "arrow-up": "kp:arrow-up",
    "arrow-down": "kp:arrow-down",
    "arrow-left": "kp:arrow-left",
    "arrow-right": "kp:arrow-right",
}

# Sim-only buttons that go through Simulator's "Device" menu.
# These DO require window activation regardless of backend, but we minimize
# the disruption by only activating for these specific keys.
_DEVICE_MENU_KEYS = {
    "home": "Home",
    "lock": "Lock",
    "shake": "Shake",
    "siri": "Siri",
    "app-switcher": "App Switcher",
    "screenshot": "Trigger Screenshot",
    "rotate-left": "Rotate Left",
    "rotate-right": "Rotate Right",
    "action-button": "Action Button",
}


def press_key(key: str) -> None:
    key_lower = key.lower().strip()

    # Hardware buttons via Device menu — these require AppleScript menu click.
    # Menu clicks via System Events do NOT need Simulator to be the frontmost
    # window when targeted by `process "Simulator"`, so this is non-stealing.
    if key_lower in _DEVICE_MENU_KEYS:
        _menu_click("Device", _DEVICE_MENU_KEYS[key_lower])
        return

    # Keyboard keys
    if _backend() == "pid":
        if pid_input.press_key(_resolve_pid_or_raise(), key_lower):
            return
        # else fall through to cliclick

    cli_arg = _CLICLICK_KEY_MAP.get(key_lower)
    if cli_arg is None:
        raise ActError(
            f"unsupported key: {key!r}. Supported: {sorted(_CLICLICK_KEY_MAP)} "
            f"+ {sorted(_DEVICE_MENU_KEYS)}"
        )
    activate()
    time.sleep(0.15)
    _run_cliclick([cli_arg])


def _menu_click(menu_title: str, item_title: str) -> None:
    """Click a Simulator menu item via AppleScript.

    Targets `process "Simulator"` so menu items can be clicked even when
    Simulator is not the frontmost window.
    """
    script = f'''
    tell application "System Events"
      tell process "Simulator"
        click menu item "{item_title}" of menu "{menu_title}" of menu bar 1
      end tell
    end tell
    '''
    res = subprocess.run(
        ["osascript", "-e", script], capture_output=True, text=True, timeout=5.0, check=False
    )
    if res.returncode != 0:
        raise ActError(f"menu click {menu_title} > {item_title} failed: {res.stderr.strip()}")
