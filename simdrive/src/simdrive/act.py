"""Synthetic input dispatch.

Internal module. Coordinates passed in are screenshot pixels from the most
recent observe; translated to logical iOS device points using the cached
scale, then dispatched via the HID-injection backend.

Three backends, in preference order:
  1. hid    — bundled native helper (real UITouch events; focuses TextFields)
  2. cliclick — synthetic mouse via the macOS window (fallback)
"""
from __future__ import annotations

import os
import subprocess
import time
from typing import Iterable, Optional

from . import hid_inject, sim
from .observability.logger import get_logger
from .observability.metrics import record_histogram
from .sim import cliclick_path
from .window import WindowBounds, activate, get_bounds

log = get_logger("simdrive.act")


class ActError(RuntimeError):
    """Raised when an act dispatch fails."""


# Cache of UDID → (logical_w, logical_h, scale) from hid_inject.device_size_points()
_DEVICE_GEOM_CACHE: dict[str, tuple[float, float, float]] = {}


def _backend() -> str:
    """Return which backend will be used. Override via SIMDRIVE_INPUT_BACKEND."""
    requested = os.environ.get("SIMDRIVE_INPUT_BACKEND", "").lower()
    if requested == "cliclick":
        return "cliclick"
    if hid_inject.available():
        return "hid"
    return "cliclick"


def _device_geom(udid: str) -> tuple[float, float, float]:
    cached = _DEVICE_GEOM_CACHE.get(udid)
    if cached is not None:
        return cached
    geom = hid_inject.device_size_points(udid)
    _DEVICE_GEOM_CACHE[udid] = geom
    return geom


def _pixels_to_points(udid: str, pixel_x: int, pixel_y: int, screenshot_w: int, screenshot_h: int) -> tuple[float, float]:
    """Map a screenshot pixel coord to logical iOS device points for the HID path."""
    if screenshot_w <= 0 or screenshot_h <= 0:
        raise ActError(f"Invalid screenshot size: {screenshot_w}x{screenshot_h}")
    logical_w, logical_h, _scale = _device_geom(udid)
    px = (pixel_x / screenshot_w) * logical_w
    py = (pixel_y / screenshot_h) * logical_h
    return px, py


def _pixels_to_screen(
    bounds: WindowBounds, pixel_x: int, pixel_y: int, screenshot_w: int, screenshot_h: int
) -> tuple[int, int]:
    if screenshot_w <= 0 or screenshot_h <= 0:
        raise ActError(f"Invalid screenshot size: {screenshot_w}x{screenshot_h}")
    sx = bounds.x + (pixel_x / screenshot_w) * bounds.width
    sy = bounds.y + (pixel_y / screenshot_h) * bounds.height
    return int(round(sx)), int(round(sy))


def _run_cliclick(args: Iterable[str], timeout: float = 5.0) -> None:
    cli = cliclick_path()
    cmd = [cli, *args]
    res = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)
    if res.returncode != 0:
        raise ActError(f"cliclick failed (rc={res.returncode}): {res.stderr.strip() or res.stdout.strip()}")


# ----------------------------- Public API ------------------------------ #


def tap(pixel_x: int, pixel_y: int, screenshot_w: int, screenshot_h: int, udid: Optional[str] = None) -> tuple[int, int]:
    """Click at screenshot-pixel coordinates. Returns the macOS screen coords used (or 0,0 for HID path)."""
    _t0 = time.time()
    if _backend() == "hid" and udid:
        x_pt, y_pt = _pixels_to_points(udid, pixel_x, pixel_y, screenshot_w, screenshot_h)
        hid_inject.tap(udid, x_pt, y_pt)
        _tap_latency = (time.time() - _t0) * 1000.0
        record_histogram("tap_latency_ms", _tap_latency)
        log.debug("tap dispatched (hid)", extra={"x": pixel_x, "y": pixel_y, "latency_ms": round(_tap_latency, 1)})
        return (0, 0)

    bounds = get_bounds()
    sx, sy = _pixels_to_screen(bounds, pixel_x, pixel_y, screenshot_w, screenshot_h)
    activate()
    time.sleep(0.15)
    _run_cliclick([f"c:{sx},{sy}"])
    _tap_latency = (time.time() - _t0) * 1000.0
    record_histogram("tap_latency_ms", _tap_latency)
    log.debug("tap dispatched (cliclick)", extra={"x": sx, "y": sy, "latency_ms": round(_tap_latency, 1)})
    return sx, sy


def swipe(
    x1: int, y1: int, x2: int, y2: int, screenshot_w: int, screenshot_h: int, duration_ms: int = 300,
    udid: Optional[str] = None,
) -> None:
    if _backend() == "hid" and udid:
        x1p, y1p = _pixels_to_points(udid, x1, y1, screenshot_w, screenshot_h)
        x2p, y2p = _pixels_to_points(udid, x2, y2, screenshot_w, screenshot_h)
        steps = max(4, duration_ms // 25)
        hid_inject.swipe(udid, x1p, y1p, x2p, y2p, steps=steps, step_delay_ms=25)
        return

    bounds = get_bounds()
    sx1, sy1 = _pixels_to_screen(bounds, x1, y1, screenshot_w, screenshot_h)
    sx2, sy2 = _pixels_to_screen(bounds, x2, y2, screenshot_w, screenshot_h)
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


def type_text(text: str, udid: Optional[str] = None) -> None:
    """Send keystrokes. Caller is responsible for tapping a focused field first.

    For non-ASCII characters (accented, emoji, non-Latin), falls back to the
    pasteboard path: simctl pbcopy + Cmd-V — preserves the focused-field state
    and works around the HID keyboard's US-ASCII-only key map.
    """
    if not text:
        return

    is_ascii = all(ord(c) < 128 for c in text)

    if _backend() == "hid" and udid:
        if is_ascii:
            hid_inject.type_text(udid, text)
            return
        # Non-ASCII path: pbcopy + paste-shortcut
        sim.set_pasteboard(udid, text)
        time.sleep(0.05)
        # Cmd-V via HID — issue Cmd modifier hold + V keypress
        # HID usage 0xE3 = Left Cmd; 0x19 = V
        _hid_paste(udid)
        return

    activate()
    time.sleep(0.15)
    _run_cliclick(["t:" + text])


def _hid_paste(udid: str) -> None:
    """Cmd-V via the HID helper — works in background mode."""
    hid_inject.chord(udid, "cmd", "v")


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

# HID usage codes for special keys (US layout, HID Keyboard/Keypad page)
_HID_KEY_MAP = {
    "return": 40,
    "enter": 40,
    "tab": 43,
    "escape": 41,
    "esc": 41,
    "space": 44,
    "delete": 42,
    "backspace": 42,
    "arrow-up": 82,
    "arrow-down": 81,
    "arrow-left": 80,
    "arrow-right": 79,
}

_DEVICE_BUTTONS = {"home", "lock", "siri"}  # buttons routed to hid_inject.press_button

# Sim-only buttons that go through Simulator's "Device" menu (cliclick fallback path).
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


def press_key(key: str, udid: Optional[str] = None) -> None:
    key_lower = key.lower().strip()

    if _backend() == "hid" and udid:
        if key_lower in _DEVICE_BUTTONS:
            hid_inject.press_button(udid, key_lower)
            return
        hid_code = _HID_KEY_MAP.get(key_lower)
        if hid_code is not None:
            hid_inject.press_key(udid, hid_code)
            return
        # fall through to cliclick path for unknown keys

    if key_lower in _DEVICE_MENU_KEYS:
        _menu_click("Device", _DEVICE_MENU_KEYS[key_lower])
        return

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
