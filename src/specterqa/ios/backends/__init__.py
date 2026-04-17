"""specterqa.ios.backends — pluggable touch-injection backends.

Available backends (priority order for the MCP product path):

* :class:`~specterqa.ios.backends.xctest_client.XCTestBackend` —
  HTTP client for the Swift XCTest runner.  Highest fidelity; no window
  required.  Port 8222.

* :class:`~specterqa.ios.backends.ax_backend.AXBackend` —
  Host-side automation via macOS AXUIElement APIs.  Instant session start;
  no XCTest runner, no on-device process, no SIGABRT crashes.  Requires
  macOS Accessibility permission and pyobjc-framework-ApplicationServices.

* :class:`~specterqa.ios.backends.browserstack.BrowserStackBackend` —
  Remote real-device cloud via BrowserStack App Automate.
  Requires ``BROWSERSTACK_USERNAME`` / ``BROWSERSTACK_ACCESS_KEY`` env vars.

Legacy backends (CLI only — not on the MCP product path):

* :class:`~specterqa.ios.wda_driver.WDADriver` —
  Appium WebDriverAgent HTTP client.  W3C Actions API.  Port 8100.

* :class:`~specterqa.ios.backends.indigo_hid.IndigoHIDBackend` —
  Pure-Python headless injection via Apple's private IndigoHID protocol.

* :class:`~specterqa.ios.backends.cgevents.CGEventBackend` —
  Quartz CGEvent-based adapter (fallback; requires visible Simulator window).

Protocol:

* :class:`~specterqa.ios.backends.protocol.IOSBackend` —
  Structural ``typing.Protocol`` that all backends must satisfy.

Selector:

* :class:`~specterqa.ios.backends.selector.BackendSelector` —
  Probes availability in priority order and returns the best backend.
  Use ``BackendSelector.choose()`` for the MCP product path.

Usage::

    from specterqa.ios.backends import BackendSelector, IOSBackend

    backend: IOSBackend = BackendSelector(udid="booted").choose()
    backend.tap(label="Sign In")
"""

from __future__ import annotations

from specterqa.ios.backends.protocol import IOSBackend, NotSupportedError
from specterqa.ios.backends.xctest_client import XCTestBackend
from specterqa.ios.backends.selector import BackendSelector

# Heavy imports are done lazily to keep the package importable on systems
# without pyobjc or BrowserStack credentials.
# Use ``from specterqa.ios.backends.ax_backend import AXBackend`` directly
# when you need it, or let BackendSelector.choose() handle it.

__all__ = [
    "IOSBackend",
    "NotSupportedError",
    "XCTestBackend",
    "BackendSelector",
]
