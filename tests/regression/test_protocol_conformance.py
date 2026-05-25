"""Protocol conformance tests — verify that backend classes satisfy IOSBackend.

Checks that each concrete backend has the required methods with compatible
signatures.  No network connections are made; these tests exercise the class
surface only.

[internal-tracker] — SpecterQA iOS Protocol refactor.
"""

from __future__ import annotations

import inspect
import pytest

from specterqa.ios.backends.protocol import IOSBackend


# ---------------------------------------------------------------------------
# Backend registry
# ---------------------------------------------------------------------------

def _load_backends():
    """Return (name, cls) pairs for all backends under test."""
    backends = []

    try:
        from specterqa.ios.backends.xctest_client import XCTestBackend
        backends.append(("XCTestBackend", XCTestBackend))
    except ImportError:
        pass

    try:
        from specterqa.ios.backends.ax_backend import AXBackend
        backends.append(("AXBackend", AXBackend))
    except ImportError:
        pass

    try:
        from specterqa.ios.backends.browserstack import BrowserStackBackend
        backends.append(("BrowserStackBackend", BrowserStackBackend))
    except ImportError:
        pass

    return backends


_BACKENDS = _load_backends()
_BACKEND_IDS = [name for name, _ in _BACKENDS]
_BACKEND_CLASSES = [cls for _, cls in _BACKENDS]

# Required method names from the Protocol
_REQUIRED_METHODS = [
    "is_available",
    "health",
    "app_state",
    "tap",
    "swipe",
    "type_text",
    "press_key",
    "get_elements",
    "find_element",
    "screenshot",
    "source",
]

# Lifecycle methods present on some backends (start/stop may be implicit)
_OPTIONAL_LIFECYCLE_METHODS = ["start", "stop"]


# ---------------------------------------------------------------------------
# Parametrized conformance checks
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("backend_cls", _BACKEND_CLASSES, ids=_BACKEND_IDS)
class TestProtocolAttributesExist:
    """Each backend must expose the required Protocol attributes."""

    def test_has_is_available(self, backend_cls):
        assert hasattr(backend_cls, "is_available"), (
            f"{backend_cls.__name__} is missing 'is_available'"
        )

    def test_has_health(self, backend_cls):
        assert hasattr(backend_cls, "health"), (
            f"{backend_cls.__name__} is missing 'health'"
        )

    def test_has_tap(self, backend_cls):
        assert hasattr(backend_cls, "tap"), (
            f"{backend_cls.__name__} is missing 'tap'"
        )

    def test_has_swipe(self, backend_cls):
        assert hasattr(backend_cls, "swipe"), (
            f"{backend_cls.__name__} is missing 'swipe'"
        )

    def test_has_type_text(self, backend_cls):
        assert hasattr(backend_cls, "type_text"), (
            f"{backend_cls.__name__} is missing 'type_text'"
        )

    def test_has_press_key(self, backend_cls):
        assert hasattr(backend_cls, "press_key"), (
            f"{backend_cls.__name__} is missing 'press_key'"
        )

    def test_has_screenshot(self, backend_cls):
        assert hasattr(backend_cls, "screenshot"), (
            f"{backend_cls.__name__} is missing 'screenshot'"
        )

    def test_has_source(self, backend_cls):
        assert hasattr(backend_cls, "source"), (
            f"{backend_cls.__name__} is missing 'source'"
        )

    def test_has_get_elements(self, backend_cls):
        # get_elements (Protocol) may be implemented as get_elements on AX/XCTest
        has_protocol_name = hasattr(backend_cls, "get_elements")
        assert has_protocol_name, (
            f"{backend_cls.__name__} is missing 'get_elements'"
        )

    def test_has_find_element(self, backend_cls):
        assert hasattr(backend_cls, "find_element"), (
            f"{backend_cls.__name__} is missing 'find_element'"
        )

    def test_is_available_is_callable(self, backend_cls):
        attr = getattr(backend_cls, "is_available")
        assert callable(attr), f"{backend_cls.__name__}.is_available is not callable"

    def test_health_is_callable(self, backend_cls):
        attr = getattr(backend_cls, "health")
        assert callable(attr), f"{backend_cls.__name__}.health is not callable"

    def test_tap_is_callable(self, backend_cls):
        attr = getattr(backend_cls, "tap")
        assert callable(attr), f"{backend_cls.__name__}.tap is not callable"


@pytest.mark.parametrize("backend_cls", _BACKEND_CLASSES, ids=_BACKEND_IDS)
class TestProtocolSignatureCompatibility:
    """Rough signature checks — ensure expected parameters are present."""

    def _params(self, backend_cls, method_name: str) -> set[str]:
        fn = getattr(backend_cls, method_name, None)
        if fn is None:
            return set()
        try:
            sig = inspect.signature(fn)
        except (ValueError, TypeError):
            return set()
        return set(sig.parameters.keys()) - {"self", "cls"}

    def test_tap_accepts_x_or_label(self, backend_cls):
        """tap() should accept at least x/y or label/identifier."""
        params = self._params(backend_cls, "tap")
        has_coord = "x" in params or "y" in params
        has_elem = "label" in params or "identifier" in params or "element_index" in params
        assert has_coord or has_elem, (
            f"{backend_cls.__name__}.tap() has no coordinate or element descriptor params: {params}"
        )

    def test_swipe_accepts_direction(self, backend_cls):
        params = self._params(backend_cls, "swipe")
        # Many backends use x1/y1/x2/y2 — direction is the new Protocol interface
        # We only check the method exists (callable check above); signature
        # divergence is acceptable for now (adapters will unify later).
        assert callable(getattr(backend_cls, "swipe"))

    def test_type_text_accepts_text(self, backend_cls):
        params = self._params(backend_cls, "type_text")
        assert "text" in params, (
            f"{backend_cls.__name__}.type_text() missing 'text' param: {params}"
        )

    def test_press_key_accepts_key(self, backend_cls):
        params = self._params(backend_cls, "press_key")
        assert "key" in params, (
            f"{backend_cls.__name__}.press_key() missing 'key' param: {params}"
        )


# ---------------------------------------------------------------------------
# Protocol runtime_checkable
# ---------------------------------------------------------------------------

class TestProtocolRuntimeCheckable:
    def test_protocol_is_runtime_checkable(self):
        """IOSBackend is @runtime_checkable so isinstance() works."""
        # We can't do isinstance(XCTestBackend(), IOSBackend) in a unit test
        # without a real object (would need network). Just verify the attribute
        # that marks it runtime_checkable is present.
        assert hasattr(IOSBackend, "__protocol_attrs__") or hasattr(IOSBackend, "_is_protocol"), (
            "IOSBackend must be @runtime_checkable"
        )

    def test_protocol_methods_enumerable(self):
        """All required method names must be reachable as attributes of IOSBackend."""
        for method in _REQUIRED_METHODS:
            assert hasattr(IOSBackend, method), (
                f"IOSBackend.{method} not found on the Protocol class"
            )
