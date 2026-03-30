"""specterqa.ios.backends — pluggable touch-injection backends.

Available backends:

* :class:`~specterqa.ios.backends.xctest_client.XCTestBackend` —
  HTTP client for the Swift XCTest runner.  Highest fidelity; no window
  required.

* :class:`~specterqa.ios.backends.indigo_hid.IndigoHIDBackend` —
  Pure-Python headless injection via Apple's private IndigoHID protocol.
  Uses ctypes + ObjC runtime to call SimDeviceLegacyHIDClient directly,
  bypassing Accessibility and the Simulator window entirely.

* :class:`~specterqa.ios.backends.cgevents.CGEventBackend` —
  Quartz CGEvent-based adapter (fallback; requires visible Simulator window).

* :class:`~specterqa.ios.backends.selector.BackendSelector` —
  Probes availability in priority order and returns the best backend.

Usage::

    from specterqa.ios.backends import BackendSelector

    backend = BackendSelector(udid="booted").get_backend()
    backend.tap(196.5, 422.0)
"""

from __future__ import annotations

from specterqa.ios.backends.cgevents import CGEventBackend
from specterqa.ios.backends.indigo_hid import IndigoHIDBackend
from specterqa.ios.backends.selector import BackendSelector
from specterqa.ios.backends.xctest_client import XCTestBackend

__all__ = [
    "XCTestBackend",
    "IndigoHIDBackend",
    "CGEventBackend",
    "BackendSelector",
]
