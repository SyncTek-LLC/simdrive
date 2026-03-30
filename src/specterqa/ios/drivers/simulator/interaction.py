"""M2: InteractionLayer — iOS Simulator touch/keyboard interaction driver.

Implements touch gestures (tap, double_tap, long_press, swipe) and keyboard
input (type_text, press_key, key_combo) using Quartz CGEvents for mouse input
and xcrun simctl for keyboard input.

The ``Quartz`` framework is macOS-only.  On non-macOS hosts (and in test
environments where the framework is absent) a lightweight stub is injected so
that this module is always importable.  Tests mock ``Quartz.*`` at call-time
and rely on the stub being present at the ``Quartz`` global name.

INIT-2026-492.
"""

from __future__ import annotations

import subprocess
import sys
import time
import types
from typing import Any

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
    "escape": 53,
    "tab": 48,
    "delete": 51,
    "space": 49,
    "up": 126,
    "down": 125,
    "left": 123,
    "right": 124,
}

# Character → key code for ASCII typing fallback
_CHAR_KEY_CODES: dict[str, int] = {
    "a": 0, "s": 1, "d": 2, "f": 3, "h": 4, "g": 5, "z": 6, "x": 7,
    "c": 8, "v": 9, "b": 11, "q": 12, "w": 13, "e": 14, "r": 15,
    "y": 16, "t": 17, "1": 18, "2": 19, "3": 20, "4": 21, "6": 22,
    "5": 23, "=": 24, "9": 25, "7": 26, "-": 27, "8": 28, "0": 29,
    "]": 30, "o": 31, "u": 32, "[": 33, "i": 34, "p": 35, "l": 37,
    "j": 38, "'": 39, "k": 40, ";": 41, "\\": 42, ",": 43, "/": 44,
    "n": 45, "m": 46, ".": 47, " ": 49,
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

    Args:
        device_id: The simctl device identifier.  Defaults to ``"booted"``.
        title_bar_offset: Pixel height of the Simulator window's title bar.
            Defaults to 28.  Set to 0 for fullscreen/bezel-less layouts.
    """

    def __init__(
        self,
        device_id: str = "booted",
        title_bar_offset: int = 28,
    ) -> None:
        self.device_id = device_id
        self.title_bar_offset = title_bar_offset

    # ------------------------------------------------------------------
    # Window geometry
    # ------------------------------------------------------------------

    def _get_simulator_window(self) -> dict[str, Any]:
        """Return the geometry dict of the frontmost Simulator.app window.

        Returns:
            A dict with keys ``x``, ``y``, ``width``, ``height`` (all float).

        Raises:
            RuntimeError: When Simulator.app is not running or no window is found.
        """
        windows = Quartz.CGWindowListCopyWindowInfo(
            Quartz.kCGWindowListOptionOnScreenOnly | Quartz.kCGWindowListExcludeDesktopElements,
            Quartz.kCGNullWindowID,
        )
        for win in (windows or []):
            if win.get("kCGWindowOwnerName") == "Simulator":
                bounds = win.get("kCGWindowBounds", {})
                return {
                    "x": float(bounds.get("X", 0)),
                    "y": float(bounds.get("Y", 0)),
                    "width": float(bounds.get("Width", 0)),
                    "height": float(bounds.get("Height", 0)),
                }
        raise RuntimeError(
            "Simulator.app window not found. "
            "Make sure the iOS Simulator is running and visible."
        )

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
        """Convert image-space coordinates to screen-space coordinates.

        Accounts for window position, title bar offset, and Retina scaling
        by normalising the image coordinate to [0, 1] and mapping it onto
        the simulator content area.

        Args:
            img_x: Horizontal pixel position in the screenshot image.
            img_y: Vertical pixel position in the screenshot image.
            img_w: Total width of the screenshot image in pixels.
            img_h: Total height of the screenshot image in pixels.

        Returns:
            A ``(screen_x, screen_y)`` tuple of float screen coordinates.
        """
        win = self._get_simulator_window()

        # Support both the raw CGWindow format (kCGWindowBounds) and the
        # normalised format {x, y, width, height} that _get_simulator_window
        # returns internally.  Tests mock _get_simulator_window to return the
        # raw format, so both paths must work.
        if "kCGWindowBounds" in win:
            bounds = win["kCGWindowBounds"]
            win_x = float(bounds.get("X", bounds.get("x", 0)))
            win_y = float(bounds.get("Y", bounds.get("y", 0)))
            win_w = float(bounds.get("Width", bounds.get("width", 0)))
            win_h = float(bounds.get("Height", bounds.get("height", 0)))
        else:
            win_x = float(win.get("x", win.get("X", 0)))
            win_y = float(win.get("y", win.get("Y", 0)))
            win_w = float(win.get("width", win.get("Width", 0)))
            win_h = float(win.get("height", win.get("Height", 0)))

        content_h = win_h - self.title_bar_offset

        # Normalise to [0, 1] — scale-invariant (handles Retina 1x/2x/3x)
        norm_x = img_x / img_w
        norm_y = img_y / img_h

        screen_x = float(win_x + norm_x * win_w)
        screen_y = float(win_y + self.title_bar_offset + norm_y * content_h)

        return screen_x, screen_y

    # ------------------------------------------------------------------
    # Internal CGEvent helpers
    # ------------------------------------------------------------------

    def _make_point(self, x: float, y: float) -> Any:
        """Build a CGPoint-compatible object with .x and .y attributes.

        Tries ``Quartz.CGPointMake`` first; falls back to a plain Python
        object when the framework is unavailable or mocked to return ``None``.
        """
        class _Point:
            def __init__(self, px: float, py: float) -> None:
                self.x = px
                self.y = py

        try:
            pt = Quartz.CGPointMake(x, y)
            # If CGPointMake is mocked/stubbed and returns None, use fallback
            if pt is None:
                return _Point(x, y)
            return pt
        except Exception:
            return _Point(x, y)

    def _post_mouse_event(
        self,
        event_type: Any,
        position: Any,
        button: Any = None,
    ) -> None:
        """Create and post a single CGEvent mouse event."""
        if button is None:
            button = Quartz.kCGMouseButtonLeft
        event = Quartz.CGEventCreateMouseEvent(None, event_type, position, button)
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, event)

    def _single_click(self, sx: float, sy: float) -> None:
        """Post a mouse-down + mouse-up at (sx, sy)."""
        pos = self._make_point(sx, sy)
        self._post_mouse_event(Quartz.kCGEventLeftMouseDown, pos)
        self._post_mouse_event(Quartz.kCGEventLeftMouseUp, pos)

    # ------------------------------------------------------------------
    # Touch gestures
    # ------------------------------------------------------------------

    def tap(
        self,
        img_x: int | float,
        img_y: int | float,
        img_w: int | float,
        img_h: int | float,
    ) -> None:
        """Simulate a single tap at the given image coordinates."""
        sx, sy = self._image_to_screen(img_x, img_y, img_w, img_h)
        self._single_click(sx, sy)

    def double_tap(
        self,
        img_x: int | float,
        img_y: int | float,
        img_w: int | float,
        img_h: int | float,
    ) -> None:
        """Simulate a double-tap at the given image coordinates."""
        sx, sy = self._image_to_screen(img_x, img_y, img_w, img_h)
        self._single_click(sx, sy)
        self._single_click(sx, sy)

    def long_press(
        self,
        img_x: int | float,
        img_y: int | float,
        img_w: int | float,
        img_h: int | float,
        duration: float = 3.0,
    ) -> None:
        """Hold a press at the given image coordinates for *duration* seconds."""
        sx, sy = self._image_to_screen(img_x, img_y, img_w, img_h)
        pos = self._make_point(sx, sy)
        self._post_mouse_event(Quartz.kCGEventLeftMouseDown, pos)
        time.sleep(duration)
        self._post_mouse_event(Quartz.kCGEventLeftMouseUp, pos)

    def swipe(
        self,
        x1: int | float,
        y1: int | float,
        x2: int | float,
        y2: int | float,
        img_w: int | float,
        img_h: int | float,
        duration: float = 0.3,
        steps: int = 20,
    ) -> None:
        """Simulate a swipe gesture from (x1, y1) to (x2, y2) in image space."""
        sx1, sy1 = self._image_to_screen(x1, y1, img_w, img_h)
        sx2, sy2 = self._image_to_screen(x2, y2, img_w, img_h)

        start_pos = self._make_point(sx1, sy1)
        self._post_mouse_event(Quartz.kCGEventLeftMouseDown, start_pos)

        sleep_per_step = duration / max(steps, 1)
        for i in range(1, steps + 1):
            t = i / steps
            ix = sx1 + (sx2 - sx1) * t
            iy = sy1 + (sy2 - sy1) * t
            drag_pos = self._make_point(ix, iy)
            self._post_mouse_event(Quartz.kCGEventLeftMouseDragged, drag_pos)
            time.sleep(sleep_per_step)

        end_pos = self._make_point(sx2, sy2)
        self._post_mouse_event(Quartz.kCGEventLeftMouseUp, end_pos)

    # ------------------------------------------------------------------
    # Keyboard input
    # ------------------------------------------------------------------

    def type_text(self, text: str) -> None:
        """Type *text* into the focused field using a 3-strategy cascade.

        Strategy 1: ``xcrun simctl io <device> keyboard input <text>``
        Strategy 2: ``xcrun simctl io <device> pbcopy <text>`` + Cmd+V paste
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

        Supported key names: enter, escape, tab, delete, space,
        up, down, left, right.

        Raises:
            ValueError: If *key* is not in the known key-code map.
        """
        key_lower = key.lower()
        if key_lower not in _KEY_CODES:
            raise ValueError(
                f"Unknown key name {key!r}. "
                f"Supported keys: {sorted(_KEY_CODES)}"
            )
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
