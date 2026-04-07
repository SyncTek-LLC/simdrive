"""BackendSelector — runtime backend selection for SpecterQA iOS.

Probes available backends in priority order and returns the best one:

    1. XCTestBackend  — highest fidelity, no window required, on-device gestures
    2. WDABackend     — Appium WebDriverAgent, W3C Actions, headless, port 8100
    3. IndigoHIDBackend — headless, Mach IPC, no window required
    4. CGEventBackend — fallback, requires visible Simulator window

The ``preferred`` argument bypasses auto-selection and forces a specific backend.

INIT-2026-500 — SpecterQA iOS Headless Driver.
INIT-2026-493 — WDA backend added.
"""

from __future__ import annotations

import logging
from typing import Any

from specterqa.ios.backends.xctest_client import XCTestBackend
from specterqa.ios.backends.indigo_hid import IndigoHIDBackend
from specterqa.ios.backends.cgevents import CGEventBackend
from specterqa.ios.wda_driver import WDADriver

logger = logging.getLogger("specterqa.ios.backends.selector")

# Backend name aliases accepted by the ``preferred`` parameter
_PREFERRED_MAP: dict[str, str] = {
    "xctest": "xctest",
    "xc_test": "xctest",
    "wda": "wda",
    "webdriveragent": "wda",
    "indigo": "indigo",
    "indigo_hid": "indigo",
    "cgevents": "cgevents",
    "cg_events": "cgevents",
    "cgevent": "cgevents",
}


class BackendSelector:
    """Select and return the best available iOS interaction backend at runtime.

    Backends are probed in priority order on each :meth:`get_backend` call so
    that availability changes (e.g. the XCTest runner coming online mid-session)
    are picked up automatically.

    Args:
        udid: Simulator UDID (or ``"booted"``).
        preferred: Force a specific backend — ``"xctest"``, ``"indigo"``,
            ``"cgevents"``, or ``None`` for auto-selection.
    """

    def __init__(
        self,
        udid: str = "booted",
        preferred: str | None = None,
    ) -> None:
        self.udid = udid
        self.preferred = preferred

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_backend(self) -> Any:
        """Return the best available backend instance.

        When ``preferred`` is set, instantiates that backend directly.
        Otherwise probes availability in priority order:
            XCTestBackend → IndigoHIDBackend → CGEventBackend

        Returns:
            An instantiated backend object exposing at minimum:
            ``tap``, ``swipe``, ``type_text``, ``screenshot``.

        Raises:
            RuntimeError: When no backend is available (all probes fail).
        """
        if self.preferred is not None:
            return self._get_preferred()
        return self._auto_select()

    def available_backends(self) -> list[str]:
        """Return a list of names of all currently available backends.

        Re-evaluates availability on each call.

        Returns:
            List of backend name strings (e.g. ``["xctest", "wda", "cgevents"]``).
        """
        names: list[str] = []
        if self._check_available(XCTestBackend):
            names.append("xctest")
        if self._check_available(WDADriver):
            names.append("wda")
        if self._check_available(IndigoHIDBackend):
            names.append("indigo")
        if self._check_available(CGEventBackend):
            names.append("cgevents")
        return names

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _check_available(backend_class: type) -> bool:
        """Call ``is_available()`` on *backend_class* defensively.

        Supports both classmethod-style and instance-method-style ``is_available``:
          - If the class has a classmethod or staticmethod named ``is_available``,
            call it directly on the class.
          - Otherwise, instantiate the class and call the instance method.

        This keeps the selector compatible with mock classes (used in tests) as
        well as the real backend classes.
        """
        try:
            return backend_class.is_available()  # works for classmethods & mocks
        except TypeError:
            # is_available is an instance method — instantiate with no args
            try:
                return backend_class().is_available()
            except Exception:
                return False
        except Exception:
            return False

    def _auto_select(self) -> Any:
        """Probe backends in priority order and return the first available."""
        # Priority 1: XCTestBackend (custom Swift runner, port 8222)
        if self._check_available(XCTestBackend):
            backend = XCTestBackend(udid=self.udid)
            logger.info("BackendSelector: selected XCTestBackend (udid=%r)", self.udid)
            return backend

        # Priority 2: WDADriver (Appium WebDriverAgent, port 8100)
        if self._check_available(WDADriver):
            backend = WDADriver(udid=self.udid)
            logger.info("BackendSelector: selected WDADriver (udid=%r)", self.udid)
            return backend

        # Priority 3: IndigoHIDBackend (headless Mach IPC)
        if self._check_available(IndigoHIDBackend):
            backend = IndigoHIDBackend(udid=self.udid)
            logger.info("BackendSelector: selected IndigoHIDBackend (udid=%r)", self.udid)
            return backend

        # Priority 4: CGEventBackend (fallback, requires visible window)
        if self._check_available(CGEventBackend):
            backend = CGEventBackend(udid=self.udid)
            logger.info("BackendSelector: selected CGEventBackend (udid=%r)", self.udid)
            return backend

        raise RuntimeError(
            "No iOS backend is available. "
            "Ensure one of the following: "
            "(1) XCTest runner is listening on port 8222, "
            "(2) WebDriverAgent is running on port 8100 (run: specterqa-ios wda start), "
            "(3) Xcode + SimulatorKit are installed (for IndigoHID), or "
            "(4) Simulator.app is running and visible (for CGEvents)."
        )

    def _get_preferred(self) -> Any:
        """Instantiate and return the backend named by ``self.preferred``."""
        key = _PREFERRED_MAP.get((self.preferred or "").lower())

        if key == "xctest":
            backend = XCTestBackend(udid=self.udid)
            logger.info("BackendSelector: forced XCTestBackend (udid=%r)", self.udid)
            return backend

        if key == "wda":
            backend = WDADriver(udid=self.udid)
            logger.info("BackendSelector: forced WDADriver (udid=%r)", self.udid)
            return backend

        if key == "indigo":
            backend = IndigoHIDBackend(udid=self.udid)
            logger.info("BackendSelector: forced IndigoHIDBackend (udid=%r)", self.udid)
            return backend

        if key == "cgevents":
            backend = CGEventBackend(udid=self.udid)
            logger.info("BackendSelector: forced CGEventBackend (udid=%r)", self.udid)
            return backend

        raise ValueError(
            f"Unknown preferred backend {self.preferred!r}. Valid values: 'xctest', 'wda', 'indigo', 'cgevents'."
        )

    # ------------------------------------------------------------------
    # Dunder helpers
    # ------------------------------------------------------------------

    def __repr__(self) -> str:
        return f"BackendSelector(udid={self.udid!r}, preferred={self.preferred!r})"
