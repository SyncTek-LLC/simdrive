"""CGEventBackend — thin wrapper around InteractionLayer for backend selector compatibility.

Adapts the CGEvent-based InteractionLayer to the standard backend interface
used by BackendSelector.  This is the fallback backend when neither XCTest
nor IndigoHID is available.

Requires:
  - macOS (for Quartz CGEvent APIs)
  - Simulator.app running and visible (CGEvents target the window)
  - No Accessibility permission required (unlike Accessibility Inspector tools)

[internal-tracker] — SpecterQA iOS Headless Driver.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("specterqa.ios.backends.cgevents")

# Default screenshot dimensions used when converting logical coords.
# InteractionLayer.tap/swipe expect image dimensions for the scaling math.
_DEFAULT_IMG_W = 1024
_DEFAULT_IMG_H = 2226


class CGEventBackend:
    """Backend adapter that drives the iOS Simulator via Quartz CGEvents.

    Wraps :class:`~specterqa.ios.drivers.simulator.interaction.InteractionLayer`
    so that :class:`~specterqa.ios.backends.selector.BackendSelector` can treat
    it uniformly alongside XCTestBackend and IndigoHIDBackend.

    Args:
        udid: Simulator UDID (or ``"booted"``).
        img_w: Screenshot width used for coordinate scaling (default: 1024).
        img_h: Screenshot height used for coordinate scaling (default: 2226).
    """

    def __init__(
        self,
        udid: str = "booted",
        img_w: int = _DEFAULT_IMG_W,
        img_h: int = _DEFAULT_IMG_H,
    ) -> None:
        from specterqa.ios.drivers.simulator.interaction import InteractionLayer  # noqa: PLC0415

        self._layer = InteractionLayer(device_id=udid)
        self._img_w = img_w
        self._img_h = img_h

    # ------------------------------------------------------------------
    # Availability check
    # ------------------------------------------------------------------

    @classmethod
    def is_available(cls) -> bool:
        """Return ``True`` when Quartz loads *and* a Simulator window is visible.

        This is a lightweight probe — it attempts to query the Simulator window
        list but does not perform any input injection.

        Returns:
            ``True`` only when the Simulator window is found.
        """
        try:
            from specterqa.ios.drivers.simulator.interaction import InteractionLayer  # noqa: PLC0415

            layer = InteractionLayer(device_id="booted")
            layer._get_simulator_window()
            return True
        except Exception:  # noqa: BLE001 — capability probe: any failure = unavailable
            return False

    # ------------------------------------------------------------------
    # Gesture API
    # ------------------------------------------------------------------

    def tap(self, x: float, y: float) -> None:
        """Tap at device-point coordinates.

        Coordinates are forwarded directly to :meth:`InteractionLayer.tap`
        using the configured image dimensions for scaling.

        Args:
            x: Horizontal position in logical points.
            y: Vertical position in logical points.
        """
        self._layer.tap(x, y, self._img_w, self._img_h)

    def swipe(
        self,
        x1: float,
        y1: float,
        x2: float,
        y2: float,
        duration: float = 0.3,
    ) -> None:
        """Swipe from (x1, y1) to (x2, y2) in device-point coordinates.

        Args:
            x1: Start horizontal position in logical points.
            y1: Start vertical position in logical points.
            x2: End horizontal position in logical points.
            y2: End vertical position in logical points.
            duration: Gesture duration in seconds (default: 0.3).
        """
        self._layer.swipe(x1, y1, x2, y2, self._img_w, self._img_h, duration=duration)

    def type_text(self, text: str) -> None:
        """Type *text* into the focused field.

        Delegates to :meth:`InteractionLayer.type_text`.

        Args:
            text: String to type.
        """
        self._layer.type_text(text)

    def press_key(self, key: str) -> None:
        """Press a named key.

        Delegates to :meth:`InteractionLayer.press_key`.

        Args:
            key: Key name string (e.g. ``"enter"``, ``"escape"``).
        """
        self._layer.press_key(key)

    def screenshot(self) -> Any:
        """Capture a screenshot via ScreenCapture.

        Returns:
            Whatever :class:`~specterqa.ios.drivers.simulator.capture.ScreenCapture`
            returns (typically a dict or path string).
        """
        from specterqa.ios.drivers.simulator.capture import ScreenCapture  # noqa: PLC0415

        cap = ScreenCapture(device_id=self._layer.device_id)
        return cap.capture()

    # ------------------------------------------------------------------
    # Dunder helpers
    # ------------------------------------------------------------------

    def __repr__(self) -> str:
        return f"CGEventBackend(udid={self._layer.device_id!r}, img={self._img_w}x{self._img_h})"
