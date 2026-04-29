"""Background input dispatch.

Internal module — drives Simulator without disturbing the user's foreground app.
Falls back to the cliclick path when its dependencies aren't available.
"""
from __future__ import annotations

import subprocess
import time
from dataclasses import dataclass
from typing import Optional


_QUARTZ_AVAILABLE: Optional[bool] = None
_QUARTZ_ERROR: Optional[str] = None


def quartz_available() -> bool:
    global _QUARTZ_AVAILABLE, _QUARTZ_ERROR
    if _QUARTZ_AVAILABLE is None:
        try:
            from Quartz import (  # noqa: F401
                CGEventCreateMouseEvent,
                CGEventCreateKeyboardEvent,
                CGEventPostToPid,
                kCGEventLeftMouseDown,
                kCGEventLeftMouseUp,
                kCGEventLeftMouseDragged,
                kCGEventMouseMoved,
                kCGMouseButtonLeft,
            )
            _QUARTZ_AVAILABLE = True
        except Exception as exc:
            _QUARTZ_AVAILABLE = False
            _QUARTZ_ERROR = str(exc)
    return _QUARTZ_AVAILABLE


def quartz_error() -> Optional[str]:
    return _QUARTZ_ERROR


def find_simulator_pid() -> Optional[int]:
    """Return the running Simulator.app PID, or None if not running.

    Uses pgrep — fast, doesn't pull focus, doesn't require any permission.
    """
    try:
        res = subprocess.run(
            ["pgrep", "-x", "Simulator"],
            capture_output=True,
            text=True,
            timeout=2.0,
            check=False,
        )
    except Exception:
        return None
    line = res.stdout.strip().splitlines()
    if not line:
        return None
    try:
        return int(line[0])
    except ValueError:
        return None


@dataclass
class InputCapability:
    quartz: bool
    sim_pid: Optional[int]
    error: Optional[str] = None

    @property
    def background_capable(self) -> bool:
        return self.quartz and self.sim_pid is not None


def capability() -> InputCapability:
    if not quartz_available():
        return InputCapability(quartz=False, sim_pid=None, error=quartz_error())
    pid = find_simulator_pid()
    return InputCapability(quartz=True, sim_pid=pid, error=None if pid else "Simulator not running")


def _post_pair(pid: int, down_event, up_event, settle: float = 0.05) -> None:
    from Quartz import CGEventPostToPid
    CGEventPostToPid(pid, down_event)
    time.sleep(settle)
    CGEventPostToPid(pid, up_event)


def tap(pid: int, sx: int, sy: int) -> None:
    """Synthetic left-click delivered to Simulator's event queue."""
    from Quartz import (
        CGEventCreateMouseEvent,
        CGEventPostToPid,
        kCGEventLeftMouseDown,
        kCGEventLeftMouseUp,
        kCGEventMouseMoved,
        kCGMouseButtonLeft,
    )
    # A move event first — many UIs gate hit-test on the cursor location.
    move = CGEventCreateMouseEvent(None, kCGEventMouseMoved, (sx, sy), kCGMouseButtonLeft)
    down = CGEventCreateMouseEvent(None, kCGEventLeftMouseDown, (sx, sy), kCGMouseButtonLeft)
    up = CGEventCreateMouseEvent(None, kCGEventLeftMouseUp, (sx, sy), kCGMouseButtonLeft)
    CGEventPostToPid(pid, move)
    time.sleep(0.02)
    CGEventPostToPid(pid, down)
    time.sleep(0.05)
    CGEventPostToPid(pid, up)


def swipe(pid: int, sx1: int, sy1: int, sx2: int, sy2: int, duration_ms: int = 300) -> None:
    """Synthetic drag delivered to Simulator's event queue."""
    from Quartz import (
        CGEventCreateMouseEvent,
        CGEventPostToPid,
        kCGEventLeftMouseDown,
        kCGEventLeftMouseDragged,
        kCGEventLeftMouseUp,
        kCGEventMouseMoved,
        kCGMouseButtonLeft,
    )
    duration_ms = max(50, min(duration_ms, 5000))
    steps = max(2, duration_ms // 25)

    move = CGEventCreateMouseEvent(None, kCGEventMouseMoved, (sx1, sy1), kCGMouseButtonLeft)
    CGEventPostToPid(pid, move)
    time.sleep(0.02)
    down = CGEventCreateMouseEvent(None, kCGEventLeftMouseDown, (sx1, sy1), kCGMouseButtonLeft)
    CGEventPostToPid(pid, down)

    sleep_per = (duration_ms / 1000.0) / steps
    for i in range(1, steps + 1):
        t = i / steps
        cx = int(round(sx1 + (sx2 - sx1) * t))
        cy = int(round(sy1 + (sy2 - sy1) * t))
        drag = CGEventCreateMouseEvent(None, kCGEventLeftMouseDragged, (cx, cy), kCGMouseButtonLeft)
        CGEventPostToPid(pid, drag)
        time.sleep(sleep_per)

    up = CGEventCreateMouseEvent(None, kCGEventLeftMouseUp, (sx2, sy2), kCGMouseButtonLeft)
    CGEventPostToPid(pid, up)


# ASCII → US keyboard virtual keycodes (subset; cover printable + common).
_ASCII_KEYCODES: dict[str, tuple[int, bool]] = {}


def _build_ascii_keycodes() -> dict[str, tuple[int, bool]]:
    """Map characters → (keycode, shift_required)."""
    base = {
        "a": 0, "s": 1, "d": 2, "f": 3, "h": 4, "g": 5, "z": 6, "x": 7,
        "c": 8, "v": 9, "b": 11, "q": 12, "w": 13, "e": 14, "r": 15,
        "y": 16, "t": 17, "1": 18, "2": 19, "3": 20, "4": 21, "6": 22,
        "5": 23, "9": 25, "7": 26, "8": 28, "0": 29,
        "]": 30, "o": 31, "u": 32, "[": 33, "i": 34, "p": 35,
        "l": 37, "j": 38, "'": 39, "k": 40, ";": 41, "\\": 42,
        ",": 43, "/": 44, "n": 45, "m": 46, ".": 47,
        "`": 50,
        " ": 49, "\t": 48, "\n": 36, "-": 27, "=": 24,
    }
    shift = {
        "A": 0, "S": 1, "D": 2, "F": 3, "H": 4, "G": 5, "Z": 6, "X": 7,
        "C": 8, "V": 9, "B": 11, "Q": 12, "W": 13, "E": 14, "R": 15,
        "Y": 16, "T": 17, "!": 18, "@": 19, "#": 20, "$": 21, "^": 22,
        "%": 23, "(": 25, "&": 26, "*": 28, ")": 29,
        "}": 30, "O": 31, "U": 32, "{": 33, "I": 34, "P": 35,
        "L": 37, "J": 38, '"': 39, "K": 40, ":": 41, "|": 42,
        "<": 43, "?": 44, "N": 45, "M": 46, ">": 47,
        "~": 50, "_": 27, "+": 24,
    }
    out: dict[str, tuple[int, bool]] = {}
    for c, kc in base.items():
        out[c] = (kc, False)
    for c, kc in shift.items():
        out[c] = (kc, True)
    return out


def type_text(pid: int, text: str) -> None:
    """Send keystrokes to the Simulator PID. Limited to US ASCII for now."""
    if not text:
        return
    from Quartz import (
        CGEventCreateKeyboardEvent,
        CGEventPostToPid,
        CGEventSetFlags,
        kCGEventFlagMaskShift,
    )
    global _ASCII_KEYCODES
    if not _ASCII_KEYCODES:
        _ASCII_KEYCODES = _build_ascii_keycodes()

    for ch in text:
        if ch not in _ASCII_KEYCODES:
            # Fallback: skip unsupported chars rather than crash.
            continue
        kc, shifted = _ASCII_KEYCODES[ch]
        down = CGEventCreateKeyboardEvent(None, kc, True)
        up = CGEventCreateKeyboardEvent(None, kc, False)
        if shifted:
            CGEventSetFlags(down, kCGEventFlagMaskShift)
            CGEventSetFlags(up, kCGEventFlagMaskShift)
        CGEventPostToPid(pid, down)
        time.sleep(0.01)
        CGEventPostToPid(pid, up)
        time.sleep(0.01)


_KEY_NAME_TO_CODE = {
    "return": 36,
    "enter": 36,
    "tab": 48,
    "escape": 53,
    "esc": 53,
    "space": 49,
    "delete": 51,
    "backspace": 51,
    "arrow-up": 126,
    "arrow-down": 125,
    "arrow-left": 123,
    "arrow-right": 124,
}


def press_key(pid: int, key: str) -> bool:
    """Press a special key by name. Returns True if dispatched, False if unsupported."""
    from Quartz import CGEventCreateKeyboardEvent, CGEventPostToPid
    kc = _KEY_NAME_TO_CODE.get(key.lower())
    if kc is None:
        return False
    down = CGEventCreateKeyboardEvent(None, kc, True)
    up = CGEventCreateKeyboardEvent(None, kc, False)
    CGEventPostToPid(pid, down)
    time.sleep(0.02)
    CGEventPostToPid(pid, up)
    return True
