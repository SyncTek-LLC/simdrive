"""BackendSelector — runtime backend selection for SpecterQA iOS.

This module is the **single** backend-decision point for the MCP product path.
Use :meth:`BackendSelector.choose` (new) from ``mcp/server.py`` and anywhere
else that needs to pick a backend at runtime.

``get_backend`` is retained for backward-compatibility but internally delegates
to the same lazy-import logic.

Backend priority for ``choose()``:
    1. XCTestBackend  — highest fidelity, no window required, on-device gestures
    2. AXBackend      — host-side AX tree, instant start, no runner required
    3. BrowserStack   — remote real-device cloud (requires license + credentials)

Legacy auto-select path (``get_backend()``) also probes WDA, IndigoHID, and
CGEvents for backward-compat with CLI commands — those backends are not on the
MCP product path.

INIT-2026-500 — SpecterQA iOS Headless Driver.
INIT-2026-525 — Consolidate backend selection; define IOSBackend Protocol.
"""

from __future__ import annotations

import logging
from typing import Any

from specterqa.ios.backends.xctest_client import XCTestBackend

logger = logging.getLogger("specterqa.ios.backends.selector")

# Backend name aliases accepted by ``preferred`` / ``requested`` parameters
_PREFERRED_MAP: dict[str, str] = {
    "xctest": "xctest",
    "xc_test": "xctest",
    "ax": "ax",
    "wda": "wda",
    "webdriveragent": "wda",
    "indigo": "indigo",
    "indigo_hid": "indigo",
    "cgevents": "cgevents",
    "cg_events": "cgevents",
    "cgevent": "cgevents",
    "browserstack": "browserstack",
    "bs": "browserstack",
}


class BackendSelector:
    """Select and return the best available iOS interaction backend at runtime.

    Args:
        udid:      Simulator UDID (or ``"booted"``).
        preferred: Force a specific backend — ``"xctest"``, ``"ax"``,
                   ``"browserstack"``, ``"wda"``, ``"indigo"``, ``"cgevents"``,
                   or ``None`` for auto-selection.
    """

    def __init__(
        self,
        udid: str = "booted",
        preferred: str | None = None,
    ) -> None:
        self.udid = udid
        self.preferred = preferred

    # ------------------------------------------------------------------
    # Primary public API — MCP product path
    # ------------------------------------------------------------------

    def choose(
        self,
        device_udid: str | None = None,
        requested: str | None = None,
        license_tier: str = "free",
    ) -> Any:
        """Select and return the best available backend.

        This is the **canonical** backend-selection entry point for the MCP
        product path.  ``mcp/server.py`` must call this instead of making its
        own inline backend decisions.

        Selection logic:
          1. If *requested* is set, try that backend first.  Raise
             ``RuntimeError`` if it is unavailable.
          2. Otherwise: XCTestBackend → AXBackend → BrowserStack (if license
             permits).

        Args:
            device_udid:   Simulator UDID or ``"booted"`` (overrides ``self.udid``).
            requested:     Backend name override (user-supplied ``backend=`` argument).
            license_tier:  ``"free"`` (default) or ``"pro"``/``"enterprise"`` —
                           controls whether BrowserStack is eligible.

        Returns:
            An instantiated backend object.

        Raises:
            RuntimeError: When no eligible backend is available.
        """
        udid = device_udid if device_udid is not None else self.udid

        if requested is not None:
            key = _PREFERRED_MAP.get(requested.lower())
            if key is None:
                raise ValueError(
                    f"Unknown backend {requested!r}. Valid: 'xctest', 'ax', 'browserstack'."
                )
            backend = self._instantiate(key, udid)
            if backend is None:
                raise RuntimeError(
                    f"Requested backend {requested!r} is not available on this system."
                )
            logger.info("BackendSelector.choose: explicit %r (udid=%r)", requested, udid)
            return backend

        # Auto-selection: XCTest → AX → BrowserStack
        if self._check_available(XCTestBackend):
            backend = XCTestBackend(udid=udid)
            logger.info("BackendSelector.choose: selected XCTestBackend (udid=%r)", udid)
            return backend

        try:
            from specterqa.ios.backends.ax_backend import AXBackend  # noqa: PLC0415

            if AXBackend.is_available():
                backend = AXBackend(device_udid=udid)
                logger.info("BackendSelector.choose: selected AXBackend (udid=%r)", udid)
                return backend
        except ImportError:
            pass

        if license_tier not in ("free",):
            try:
                from specterqa.ios.backends.browserstack import BrowserStackBackend  # noqa: PLC0415

                if BrowserStackBackend.is_available():
                    backend = BrowserStackBackend()
                    logger.info("BackendSelector.choose: selected BrowserStackBackend")
                    return backend
            except ImportError:
                pass

        raise RuntimeError(
            "No iOS backend is available on this system. "
            "Ensure one of the following:\n"
            "  (1) XCTest runner is listening on port 8222 (run: specterqa ios build-runner), or\n"
            "  (2) iOS Simulator is booted and macOS Accessibility permission is granted (for AX backend)."
        )

    # ------------------------------------------------------------------
    # Legacy public API — backward-compat for CLI commands
    # ------------------------------------------------------------------

    def get_backend(self) -> Any:
        """Return the best available backend instance (legacy interface).

        Retained for backward-compatibility.  New code should call ``choose()``.

        When ``preferred`` is set, instantiates that backend directly.
        Otherwise probes availability in priority order:
            XCTestBackend → AXBackend → WDADriver → IndigoHIDBackend → CGEventBackend

        Returns:
            An instantiated backend object.

        Raises:
            RuntimeError: When no backend is available.
        """
        if self.preferred is not None:
            return self._get_preferred_legacy()
        return self._auto_select_legacy()

    def available_backends(self) -> list[str]:
        """Return a list of names of all currently available backends."""
        names: list[str] = []
        if self._check_available(XCTestBackend):
            names.append("xctest")

        try:
            from specterqa.ios.backends.ax_backend import AXBackend  # noqa: PLC0415

            if AXBackend.is_available():
                names.append("ax")
        except ImportError:
            pass

        try:
            from specterqa.ios.wda_driver import WDADriver  # noqa: PLC0415

            if self._check_available(WDADriver):
                names.append("wda")
        except ImportError:
            pass

        try:
            from specterqa.ios.backends.indigo_hid import IndigoHIDBackend  # noqa: PLC0415

            if self._check_available(IndigoHIDBackend):
                names.append("indigo")
        except ImportError:
            pass

        try:
            from specterqa.ios.backends.cgevents import CGEventBackend  # noqa: PLC0415

            if self._check_available(CGEventBackend):
                names.append("cgevents")
        except ImportError:
            pass

        try:
            from specterqa.ios.backends.browserstack import BrowserStackBackend  # noqa: PLC0415

            if BrowserStackBackend.is_available():
                names.append("browserstack")
        except ImportError:
            pass

        return names

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _instantiate(self, key: str, udid: str) -> Any | None:
        """Instantiate the backend named *key* if it is available.

        Returns the backend object or ``None`` if unavailable / not importable.
        """
        if key == "xctest":
            if self._check_available(XCTestBackend):
                return XCTestBackend(udid=udid)
            return None

        if key == "ax":
            try:
                from specterqa.ios.backends.ax_backend import AXBackend  # noqa: PLC0415

                if AXBackend.is_available():
                    return AXBackend(device_udid=udid)
            except ImportError:
                pass
            return None

        if key == "browserstack":
            try:
                from specterqa.ios.backends.browserstack import BrowserStackBackend  # noqa: PLC0415

                if BrowserStackBackend.is_available():
                    return BrowserStackBackend()
            except ImportError:
                pass
            return None

        if key == "wda":
            try:
                from specterqa.ios.wda_driver import WDADriver  # noqa: PLC0415

                if self._check_available(WDADriver):
                    return WDADriver(udid=udid)
            except ImportError:
                pass
            return None

        if key == "indigo":
            try:
                from specterqa.ios.backends.indigo_hid import IndigoHIDBackend  # noqa: PLC0415

                if self._check_available(IndigoHIDBackend):
                    return IndigoHIDBackend(udid=udid)
            except ImportError:
                pass
            return None

        if key == "cgevents":
            try:
                from specterqa.ios.backends.cgevents import CGEventBackend  # noqa: PLC0415

                if self._check_available(CGEventBackend):
                    return CGEventBackend(udid=udid)
            except ImportError:
                pass
            return None

        return None

    @staticmethod
    def _check_available(backend_class: type) -> bool:
        """Call ``is_available()`` on *backend_class* defensively."""
        try:
            return backend_class.is_available()  # works for classmethods & mocks
        except TypeError:
            try:
                return backend_class().is_available()
            except Exception:  # noqa: BLE001
                return False
        except Exception:  # noqa: BLE001
            return False

    def _auto_select_legacy(self) -> Any:
        """Probe backends in priority order (legacy CLI path)."""
        # Priority 1: XCTestBackend
        if self._check_available(XCTestBackend):
            backend = XCTestBackend(udid=self.udid)
            logger.info("BackendSelector: selected XCTestBackend (udid=%r)", self.udid)
            return backend

        # Priority 2: AXBackend
        try:
            from specterqa.ios.backends.ax_backend import AXBackend  # noqa: PLC0415

            if AXBackend.is_available():
                backend = AXBackend(device_udid=self.udid)
                logger.info("BackendSelector: selected AXBackend (udid=%r)", self.udid)
                return backend
        except ImportError:
            pass

        # Priority 3: WDADriver
        try:
            from specterqa.ios.wda_driver import WDADriver  # noqa: PLC0415

            if self._check_available(WDADriver):
                backend = WDADriver(udid=self.udid)
                logger.info("BackendSelector: selected WDADriver (udid=%r)", self.udid)
                return backend
        except ImportError:
            pass

        # Priority 4: IndigoHIDBackend
        try:
            from specterqa.ios.backends.indigo_hid import IndigoHIDBackend  # noqa: PLC0415

            if self._check_available(IndigoHIDBackend):
                backend = IndigoHIDBackend(udid=self.udid)
                logger.info("BackendSelector: selected IndigoHIDBackend (udid=%r)", self.udid)
                return backend
        except ImportError:
            pass

        # Priority 5: CGEventBackend
        try:
            from specterqa.ios.backends.cgevents import CGEventBackend  # noqa: PLC0415

            if self._check_available(CGEventBackend):
                backend = CGEventBackend(udid=self.udid)
                logger.info("BackendSelector: selected CGEventBackend (udid=%r)", self.udid)
                return backend
        except ImportError:
            pass

        raise RuntimeError(
            "No iOS backend is available. "
            "Ensure one of the following: "
            "(1) XCTest runner is listening on port 8222, "
            "(2) macOS Accessibility permission granted (for AXBackend), "
            "(3) WebDriverAgent is running on port 8100 (run: specterqa-ios wda start), "
            "(4) Xcode + SimulatorKit are installed (for IndigoHID), or "
            "(5) Simulator.app is running and visible (for CGEvents)."
        )

    def _get_preferred_legacy(self) -> Any:
        """Instantiate and return the backend named by ``self.preferred`` (legacy)."""
        key = _PREFERRED_MAP.get((self.preferred or "").lower())
        if key is None:
            raise ValueError(
                f"Unknown preferred backend {self.preferred!r}. "
                "Valid values: 'xctest', 'ax', 'wda', 'indigo', 'cgevents', 'browserstack'."
            )
        backend = self._instantiate(key, self.udid)
        if backend is not None:
            logger.info("BackendSelector: forced %r (udid=%r)", key, self.udid)
            return backend
        raise RuntimeError(
            f"Preferred backend {self.preferred!r} is not available on this system."
        )

    # ------------------------------------------------------------------
    # Dunder helpers
    # ------------------------------------------------------------------

    def __repr__(self) -> str:
        return f"BackendSelector(udid={self.udid!r}, preferred={self.preferred!r})"
