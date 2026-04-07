"""M2: InteractionLayer — iOS Simulator touch/keyboard interaction driver.

Implements touch gestures (tap, double_tap, long_press, swipe) and keyboard
input (type_text, press_key, key_combo) using Quartz CGEvents for mouse input
and xcrun simctl for keyboard input.

Coordinate mapping approach (from proven prototype sim_driver.py):
  - Activate Simulator window via ``open -a Simulator`` before every gesture
  - Find the main Simulator window using layer==0, alpha>=1.0, height>200 filter
  - Title bar detection: has kCGWindowName → 28px, empty name → 0px (fullscreen)
  - Window result cached for 2 seconds to avoid excessive Quartz queries
  - Scale image coords: scale_x = win.width / img_w, scale_y = content_h / img_h
  - screen_x = win.x + img_x * scale_x
  - screen_y = win.y + title_bar + img_y * scale_y

CGEvent positions use a _Point wrapper object with .x and .y attributes (for
test-compatibility) while preserving the same numeric math as the prototype.

The ``Quartz`` framework is macOS-only.  On non-macOS hosts (and in test
environments where the framework is absent) a lightweight stub is injected so
that this module is always importable.  Tests mock ``Quartz.*`` at call-time
and rely on the stub being present at the ``Quartz`` global name.

INIT-2026-492 / INIT-2026-493.
"""

from __future__ import annotations

import logging
import subprocess
import sys
import time
import types
from typing import Any

logger = logging.getLogger("specterqa.ios.interaction")

# ---------------------------------------------------------------------------
# Quartz availability — inject stub if the real framework is absent
# ---------------------------------------------------------------------------

try:
    import Quartz  # type: ignore[import]
except ImportError:  # pragma: no cover — only triggered outside macOS
    # Minimal stub so the module can be imported and tests can patch Quartz.*
    _stub = types.ModuleType("Quartz")

    # Mouse event type constants
    _stub.kCGEventLeftMouseDown = 1
    _stub.kCGEventLeftMouseUp = 2
    _stub.kCGEventLeftMouseDragged = 6
    _stub.kCGMouseButtonLeft = 0
    _stub.kCGHIDEventTap = 0

    # Window list constants
    _stub.kCGWindowListOptionOnScreenOnly = 1
    _stub.kCGWindowListExcludeDesktopElements = 16
    _stub.kCGNullWindowID = 0

    def _stub_noop(*args: Any, **kwargs: Any) -> None:  # type: ignore[misc]
        """No-op stub — returns None.  Tests patch these at call-time."""
        return None

    _stub.CGEventCreateMouseEvent = _stub_noop
    _stub.CGEventCreateKeyboardEvent = _stub_noop
    _stub.CGEventSetFlags = _stub_noop
    _stub.CGEventPost = _stub_noop
    _stub.CGWindowListCopyWindowInfo = _stub_noop
    _stub.CGPointMake = _stub_noop

    sys.modules["Quartz"] = _stub
    Quartz = _stub  # type: ignore[assignment]


# ---------------------------------------------------------------------------
# Key code map: name → macOS virtual key code
# ---------------------------------------------------------------------------

_KEY_CODES: dict[str, int] = {
    "enter": 36,
    "return": 36,
    "escape": 53,
    "tab": 48,
    "delete": 51,
    "backspace": 51,
    "space": 49,
    "up": 126,
    "down": 125,
    "left": 123,
    "right": 124,
}

# Character → key code for ASCII typing fallback
_CHAR_KEY_CODES: dict[str, int] = {
    "a": 0,
    "s": 1,
    "d": 2,
    "f": 3,
    "h": 4,
    "g": 5,
    "z": 6,
    "x": 7,
    "c": 8,
    "v": 9,
    "b": 11,
    "q": 12,
    "w": 13,
    "e": 14,
    "r": 15,
    "y": 16,
    "t": 17,
    "1": 18,
    "2": 19,
    "3": 20,
    "4": 21,
    "6": 22,
    "5": 23,
    "=": 24,
    "9": 25,
    "7": 26,
    "-": 27,
    "8": 28,
    "0": 29,
    "]": 30,
    "o": 31,
    "u": 32,
    "[": 33,
    "i": 34,
    "p": 35,
    "l": 37,
    "j": 38,
    "'": 39,
    "k": 40,
    ";": 41,
    "\\": 42,
    ",": 43,
    "/": 44,
    "n": 45,
    "m": 46,
    ".": 47,
    " ": 49,
}

# Modifier name → CGEvent flag value
_MODIFIER_FLAGS: dict[str, int] = {
    "cmd": 0x100000,
    "command": 0x100000,
    "shift": 0x20000,
    "option": 0x80000,
    "alt": 0x80000,
    "ctrl": 0x40000,
    "control": 0x40000,
}


class InteractionLayer:
    """Drives all touch and keyboard input for an iOS Simulator instance.

    Uses Quartz CGEvents for mouse-based gesture simulation and xcrun simctl
    for keyboard input (with CGEvent keystroke fallback).

    Coordinate mapping (proven prototype approach):
        1. Activate Simulator.app via ``open -a Simulator`` before each gesture
        2. Find the main window using: layer==0, alpha>=1.0, height>200
        3. Use the tallest qualifying window (device window, not toolbar)
        4. Title bar: 28px when window has a name, 0px when name is empty
        5. Cache window bounds for 2 seconds
        6. scale_x = win_width / img_w  (direct ratio, not normalised 0-1)
        7. screen_x = win_x + img_x * scale_x
        8. screen_y = win_y + title_bar + img_y * scale_y

    Args:
        device_id: The simctl device identifier.  Defaults to ``"booted"``.
        title_bar_offset: Pixel height of the Simulator window's title bar.
            Defaults to 28.  Set to 0 for fullscreen/bezel-less layouts.
            When ``auto_detect_title_bar`` is True this is used as a fallback.
        auto_detect_title_bar: When True (default), ignore the fixed
            ``title_bar_offset`` and instead auto-detect per-window from the
            kCGWindowName presence (matching proven prototype logic).
    """

    def __init__(
        self,
        device_id: str = "booted",
        title_bar_offset: int = 28,
        auto_detect_title_bar: bool = True,
    ) -> None:
        self.device_id = device_id
        self._auto_detect_title_bar = auto_detect_title_bar
        # Stored as the fallback; also exposed as .title_bar_offset for tests
        self.title_bar_offset = title_bar_offset

        # Window cache — invalidated after 2 seconds (prototype approach)
        self._window_cache: dict[str, Any] | None = None
        self._window_cache_time: float = 0.0

    # ------------------------------------------------------------------
    # Simulator activation
    # ------------------------------------------------------------------

    def _activate_simulator(self) -> None:
        """Bring Simulator.app to front before posting CGEvents.

        Without this, CGEvents may go to the wrong window.
        Uses ``open -a Simulator`` (same as proven prototype).
        """
        subprocess.run(["open", "-a", "Simulator"], check=False)
        time.sleep(0.15)

    # ------------------------------------------------------------------
    # Window geometry
    # ------------------------------------------------------------------

    def _get_simulator_window(self) -> dict[str, Any]:
        """Return the geometry dict of the main Simulator.app window.

        Uses the proven prototype filtering strategy:
          - Owner name contains 'Simulator'
          - kCGWindowLayer == 0
          - kCGWindowAlpha >= 1.0
          - window height > 200
          - Of all matching windows, pick the tallest (device window)

        Title bar auto-detection:
          - kCGWindowName is non-empty → 28px title bar
          - kCGWindowName is empty → 0px (fullscreen)

        Result is cached for 2 seconds.

        Returns:
            A dict with keys ``x``, ``y``, ``width``, ``height``,
            ``title_bar_height`` (all float).

        Raises:
            RuntimeError: When no qualifying Simulator window is found.
        """
        now = time.time()
        if self._window_cache is not None and (now - self._window_cache_time) < 2.0:
            return self._window_cache

        windows = Quartz.CGWindowListCopyWindowInfo(
            Quartz.kCGWindowListOptionOnScreenOnly,
            Quartz.kCGNullWindowID,
        )

        sim_windows = []
        for w in windows or []:
            owner = w.get("kCGWindowOwnerName", "")
            if "Simulator" not in owner:
                continue
            bounds = w.get("kCGWindowBounds", {})
            layer = w.get("kCGWindowLayer", 0)
            alpha = w.get("kCGWindowAlpha", 1.0)
            height = float(bounds.get("Height", 0))
            # Main window: layer 0, fully opaque, tall enough to be the device
            if layer == 0 and alpha >= 1.0 and height > 200:
                sim_windows.append(
                    {
                        "bounds": bounds,
                        "name": w.get("kCGWindowName", ""),
                        "height": height,
                    }
                )

        if not sim_windows:
            raise RuntimeError("Simulator.app window not found. Make sure the iOS Simulator is running and visible.")

        # Tallest window is the device window (not toolbar/panel)
        best = max(sim_windows, key=lambda w: w["height"])
        b = best["bounds"]

        # Title bar: window has a name → standard 28px bar; nameless → fullscreen
        if self._auto_detect_title_bar:
            title_bar = 28.0 if best["name"] else 0.0
        else:
            title_bar = float(self.title_bar_offset)

        result: dict[str, Any] = {
            "x": float(b.get("X", b.get("x", 0))),
            "y": float(b.get("Y", b.get("y", 0))),
            "width": float(b.get("Width", b.get("width", 0))),
            "height": float(b.get("Height", b.get("height", 0))),
            "title_bar_height": title_bar,
        }

        self._window_cache = result
        self._window_cache_time = now
        return result

    # ------------------------------------------------------------------
    # Coordinate conversion
    # ------------------------------------------------------------------

    def _image_to_screen(
        self,
        img_x: int | float,
        img_y: int | float,
        img_w: int | float,
        img_h: int | float,
    ) -> tuple[float, float]:
        """Convert image-space coordinates to absolute screen coordinates.

        Uses the proven prototype math:
            scale_x = win_width / img_w
            scale_y = content_height / img_h
            screen_x = win_x + img_x * scale_x
            screen_y = win_y + title_bar + img_y * scale_y

        This produces the correct hit targets regardless of window resize,
        Retina scale factor, or screenshot resize width.

        Args:
            img_x: Horizontal pixel position in the screenshot image.
            img_y: Vertical pixel position in the screenshot image.
            img_w: Total width of the screenshot image in pixels.
            img_h: Total height of the screenshot image in pixels.

        Returns:
            A ``(screen_x, screen_y)`` tuple of float screen coordinates.
        """
        win = self._get_simulator_window()

        # Support both normalised {x,y,width,height} and raw kCGWindowBounds
        # format (tests may mock _get_simulator_window to return kCGWindowBounds).
        if "kCGWindowBounds" in win:
            bounds = win["kCGWindowBounds"]
            win_x = float(bounds.get("X", bounds.get("x", 0)))
            win_y = float(bounds.get("Y", bounds.get("y", 0)))
            win_w = float(bounds.get("Width", bounds.get("width", 0)))
            win_h = float(bounds.get("Height", bounds.get("height", 0)))
            # Title bar from stored offset when using raw format
            title_bar = float(self.title_bar_offset)
        else:
            win_x = float(win.get("x", win.get("X", 0)))
            win_y = float(win.get("y", win.get("Y", 0)))
            win_w = float(win.get("width", win.get("Width", 0)))
            win_h = float(win.get("height", win.get("Height", 0)))
            title_bar = float(win.get("title_bar_height", self.title_bar_offset))

        content_h = win_h - title_bar

        # Direct ratio scaling (prototype approach — not normalised 0→1)
        scale_x = win_w / img_w if img_w else 1.0
        scale_y = content_h / img_h if img_h else 1.0

        screen_x = float(win_x + img_x * scale_x)
        screen_y = float(win_y + title_bar + img_y * scale_y)

        logger.debug(
            "coords: img(%s,%s)/%sx%s → screen(%.1f,%.1f) [win@(%.0f,%.0f) %.0fx%.0f titlebar=%.0f]",
            img_x,
            img_y,
            img_w,
            img_h,
            screen_x,
            screen_y,
            win_x,
            win_y,
            win_w,
            win_h,
            title_bar,
        )

        return screen_x, screen_y

    # ------------------------------------------------------------------
    # Internal CGEvent helpers
    # ------------------------------------------------------------------

    def _make_point(self, x: float, y: float) -> tuple[float, float]:
        """Return a raw (x, y) tuple for CGEventCreateMouseEvent.

        PyObjC's Quartz bridge reliably accepts plain tuples as CGPoint.
        The previous _Point wrapper object was not guaranteed to be
        coerced correctly by PyObjC — the working prototype uses raw
        tuples exclusively.
        """
        return (x, y)

    # ------------------------------------------------------------------
    # Touch gestures — proven prototype timing and approach
    # ------------------------------------------------------------------

    def tap(
        self,
        img_x: int | float,
        img_y: int | float,
        img_w: int | float,
        img_h: int | float,
    ) -> None:
        """Simulate a single tap at the given image coordinates.

        Timing (prototype-proven):
          - Activate Simulator first
          - 80ms between mouse-down and mouse-up
          - 400ms post-tap cooldown
        """
        self._activate_simulator()
        sx, sy = self._image_to_screen(img_x, img_y, img_w, img_h)
        pos = self._make_point(sx, sy)

        down = Quartz.CGEventCreateMouseEvent(None, Quartz.kCGEventLeftMouseDown, pos, 0)
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, down)
        time.sleep(0.08)
        up = Quartz.CGEventCreateMouseEvent(None, Quartz.kCGEventLeftMouseUp, pos, 0)
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, up)
        time.sleep(0.4)

    def double_tap(
        self,
        img_x: int | float,
        img_y: int | float,
        img_w: int | float,
        img_h: int | float,
    ) -> None:
        """Simulate a double-tap at the given image coordinates.

        Timing (prototype-proven):
          - Activate Simulator first
          - 50ms down/up per tap, 100ms between taps
          - 300ms post double-tap cooldown
        """
        self._activate_simulator()
        sx, sy = self._image_to_screen(img_x, img_y, img_w, img_h)
        pos = self._make_point(sx, sy)

        for _ in range(2):
            down = Quartz.CGEventCreateMouseEvent(None, Quartz.kCGEventLeftMouseDown, pos, 0)
            up = Quartz.CGEventCreateMouseEvent(None, Quartz.kCGEventLeftMouseUp, pos, 0)
            Quartz.CGEventPost(Quartz.kCGHIDEventTap, down)
            time.sleep(0.05)
            Quartz.CGEventPost(Quartz.kCGHIDEventTap, up)
            time.sleep(0.1)
        time.sleep(0.3)

    def long_press(
        self,
        img_x: int | float,
        img_y: int | float,
        img_w: int | float,
        img_h: int | float,
        duration: float = 3.0,
    ) -> None:
        """Hold a press at the given image coordinates for *duration* seconds.

        Timing (prototype-proven):
          - Activate Simulator first
          - Mouse-down, sleep(duration), mouse-up
          - 500ms post long-press cooldown
        """
        self._activate_simulator()
        sx, sy = self._image_to_screen(img_x, img_y, img_w, img_h)
        pos = self._make_point(sx, sy)

        down = Quartz.CGEventCreateMouseEvent(None, Quartz.kCGEventLeftMouseDown, pos, 0)
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, down)
        time.sleep(duration)
        up = Quartz.CGEventCreateMouseEvent(None, Quartz.kCGEventLeftMouseUp, pos, 0)
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, up)
        time.sleep(0.5)

    def swipe(
        self,
        x1: int | float,
        y1: int | float,
        x2: int | float,
        y2: int | float,
        img_w: int | float,
        img_h: int | float,
        duration: float = 0.4,
        steps: int = 25,
    ) -> None:
        """Simulate a swipe gesture from (x1, y1) to (x2, y2) in image space.

        Timing (prototype-proven):
          - Activate Simulator first
          - 20ms initial hold before dragging
          - 25 drag steps (matches prototype exactly)
          - 300ms post-swipe cooldown
        """
        self._activate_simulator()
        sx1, sy1 = self._image_to_screen(x1, y1, img_w, img_h)
        sx2, sy2 = self._image_to_screen(x2, y2, img_w, img_h)

        start_pos = self._make_point(sx1, sy1)
        down = Quartz.CGEventCreateMouseEvent(None, Quartz.kCGEventLeftMouseDown, start_pos, 0)
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, down)
        time.sleep(0.02)  # 20ms initial hold (prototype value)

        sleep_per_step = duration / max(steps, 1)
        for i in range(1, steps + 1):
            t = i / steps
            cx = sx1 + (sx2 - sx1) * t
            cy = sy1 + (sy2 - sy1) * t
            drag_pos = self._make_point(cx, cy)
            drag = Quartz.CGEventCreateMouseEvent(None, Quartz.kCGEventLeftMouseDragged, drag_pos, 0)
            Quartz.CGEventPost(Quartz.kCGHIDEventTap, drag)
            time.sleep(sleep_per_step)

        end_pos = self._make_point(sx2, sy2)
        up = Quartz.CGEventCreateMouseEvent(None, Quartz.kCGEventLeftMouseUp, end_pos, 0)
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, up)
        time.sleep(0.3)  # post-swipe cooldown (prototype value)

    # ------------------------------------------------------------------
    # Keyboard input
    # ------------------------------------------------------------------

    def type_text(self, text: str) -> None:
        """Type *text* into the focused field using a 3-strategy cascade.

        Strategy 1: ``xcrun simctl io <device> keyboard input <text>``
        Strategy 2: ``xcrun simctl io <device> pbcopy`` + Cmd+V paste
        Strategy 3: Individual CGEvent keystroke per character (last resort)
        """
        if not text:
            return

        # Strategy 1: simctl keyboard input
        result = subprocess.run(
            ["xcrun", "simctl", "io", self.device_id, "keyboard", "input", text],
            capture_output=True,
        )
        if result.returncode == 0:
            return

        # Strategy 2: simctl pbcopy + Cmd+V
        result2 = subprocess.run(
            ["xcrun", "simctl", "io", self.device_id, "pbcopy"],
            input=text.encode(),
            capture_output=True,
        )
        if result2.returncode == 0:
            time.sleep(0.2)
            self.key_combo(modifiers=["cmd"], key="v")
            return

        # Strategy 3: CGEvent keystroke per character
        for char in text:
            key_code = _CHAR_KEY_CODES.get(char.lower(), 0)
            down = Quartz.CGEventCreateKeyboardEvent(None, key_code, True)
            Quartz.CGEventPost(Quartz.kCGHIDEventTap, down)
            up = Quartz.CGEventCreateKeyboardEvent(None, key_code, False)
            Quartz.CGEventPost(Quartz.kCGHIDEventTap, up)

    def press_key(self, key: str) -> None:
        """Press and release a named key.

        Supported key names: enter, return, escape, tab, delete, backspace,
        space, up, down, left, right.

        Raises:
            ValueError: If *key* is not in the known key-code map.
        """
        key_lower = key.lower()
        if key_lower not in _KEY_CODES:
            raise ValueError(f"Unknown key name {key!r}. Supported keys: {sorted(_KEY_CODES)}")
        key_code = _KEY_CODES[key_lower]
        down = Quartz.CGEventCreateKeyboardEvent(None, key_code, True)
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, down)
        up = Quartz.CGEventCreateKeyboardEvent(None, key_code, False)
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, up)

    def key_combo(self, modifiers: list[str], key: str) -> None:
        """Send a modifier + key combination (e.g. Cmd+V, Shift+Enter).

        Args:
            modifiers: List of modifier names (``"cmd"``, ``"shift"``,
                ``"option"``, ``"ctrl"``).
            key: The base key name or single character.
        """
        key_lower = key.lower()
        if key_lower in _KEY_CODES:
            key_code = _KEY_CODES[key_lower]
        else:
            key_code = _CHAR_KEY_CODES.get(key_lower, 0)

        # Compute combined modifier flags
        combined_flags = 0
        for mod in modifiers:
            combined_flags |= _MODIFIER_FLAGS.get(mod.lower(), 0)

        down_event = Quartz.CGEventCreateKeyboardEvent(None, key_code, True)
        Quartz.CGEventSetFlags(down_event, combined_flags)
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, down_event)

        up_event = Quartz.CGEventCreateKeyboardEvent(None, key_code, False)
        Quartz.CGEventSetFlags(up_event, combined_flags)
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, up_event)
