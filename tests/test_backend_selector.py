"""Tests for BackendSelector — runtime backend selection logic.

TDD Phase — INIT-2026-500.
These tests are written BEFORE implementation exists and are importable even
when the implementation module is absent.

Module under test (to be created by CodeAtlas):
  specterqa/ios/backends/selector.py  —  BackendSelector
"""

from __future__ import annotations

import logging
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Conditional import guard — tests remain importable without implementation.
# ---------------------------------------------------------------------------

try:
    from specterqa.ios.backends.selector import BackendSelector  # type: ignore[import]

    _SELECTOR_AVAILABLE = True
except ImportError:
    _SELECTOR_AVAILABLE = False
    BackendSelector = None  # type: ignore[assignment,misc]

needs_selector = pytest.mark.skipif(
    not _SELECTOR_AVAILABLE,
    reason="specterqa.ios.backends.selector not yet implemented",
)

# ---------------------------------------------------------------------------
# Backend module paths (as imported inside selector.py)
# ---------------------------------------------------------------------------

_XCTEST_PATH = "specterqa.ios.backends.selector.XCTestBackend"
_INDIGO_PATH = "specterqa.ios.backends.selector.IndigoHIDBackend"
_CGEVENTS_PATH = "specterqa.ios.backends.selector.CGEventBackend"

# ---------------------------------------------------------------------------
# Helpers — build mock backend classes
# ---------------------------------------------------------------------------

_DEFAULT_UDID = "booted"
_ALT_UDID = "00008110-001A2B3C4D5E6F78"

_REQUIRED_METHODS = ("tap", "swipe", "type_text", "screenshot")


def _make_mock_backend_class(is_available: bool = True) -> MagicMock:
    """Return a mock backend class whose instances pass the interface check."""
    mock_instance = MagicMock()
    for method in _REQUIRED_METHODS:
        setattr(mock_instance, method, MagicMock())

    mock_class = MagicMock()
    mock_class.is_available.return_value = is_available
    mock_class.return_value = mock_instance
    return mock_class


def _make_selector(udid: str = _DEFAULT_UDID, preferred: str | None = None) -> "BackendSelector":
    """Construct a BackendSelector for testing."""
    return BackendSelector(udid=udid, preferred=preferred)


# ===========================================================================
# TestBackendSelectorAutoSelection — priority-based fallback chain
# ===========================================================================


@needs_selector
class TestBackendSelectorAutoSelection:
    """get_backend() selects backends in priority order: XCTest > IndigoHID > CGEvents."""

    def test_selects_xctest_when_runner_is_responding(self):
        """get_backend() returns an XCTestBackend when XCTest runner is up."""
        xctest_class = _make_mock_backend_class(is_available=True)
        indigo_class = _make_mock_backend_class(is_available=True)
        cgevent_class = _make_mock_backend_class(is_available=True)

        with patch(_XCTEST_PATH, xctest_class), patch(_INDIGO_PATH, indigo_class), patch(_CGEVENTS_PATH, cgevent_class):
            selector = _make_selector()
            backend = selector.get_backend()

        # XCTest should win — it has the highest priority
        xctest_class.assert_called(), "XCTestBackend was not instantiated"
        # Verify we got back an instance of the xctest mock
        assert backend is xctest_class.return_value, "get_backend() did not return the XCTestBackend instance"

    def test_selects_indigo_when_xctest_unavailable(self):
        """get_backend() falls back to IndigoHIDBackend when XCTest is unavailable."""
        xctest_class = _make_mock_backend_class(is_available=False)
        indigo_class = _make_mock_backend_class(is_available=True)
        cgevent_class = _make_mock_backend_class(is_available=True)

        with patch(_XCTEST_PATH, xctest_class), patch(_INDIGO_PATH, indigo_class), patch(_CGEVENTS_PATH, cgevent_class):
            selector = _make_selector()
            backend = selector.get_backend()

        assert backend is indigo_class.return_value, (
            "get_backend() should fall back to IndigoHIDBackend when XCTest is unavailable"
        )

    def test_selects_cgevents_when_neither_xctest_nor_indigo_available(self):
        """get_backend() falls back to CGEventBackend when both preferred backends fail."""
        xctest_class = _make_mock_backend_class(is_available=False)
        indigo_class = _make_mock_backend_class(is_available=False)
        cgevent_class = _make_mock_backend_class(is_available=True)

        with patch(_XCTEST_PATH, xctest_class), patch(_INDIGO_PATH, indigo_class), patch(_CGEVENTS_PATH, cgevent_class):
            selector = _make_selector()
            backend = selector.get_backend()

        assert backend is cgevent_class.return_value, (
            "get_backend() should fall back to CGEventBackend when XCTest and IndigoHID are unavailable"
        )


# ===========================================================================
# TestBackendSelectorPreferred — forced backend selection
# ===========================================================================


@needs_selector
class TestBackendSelectorPreferred:
    """preferred= forces a specific backend regardless of auto-detection order."""

    def test_preferred_xctest_forces_xctest(self):
        """preferred='xctest' selects XCTestBackend even if others are available."""
        xctest_class = _make_mock_backend_class(is_available=True)
        indigo_class = _make_mock_backend_class(is_available=True)
        cgevent_class = _make_mock_backend_class(is_available=True)

        with patch(_XCTEST_PATH, xctest_class), patch(_INDIGO_PATH, indigo_class), patch(_CGEVENTS_PATH, cgevent_class):
            selector = _make_selector(preferred="xctest")
            backend = selector.get_backend()

        assert backend is xctest_class.return_value, "preferred='xctest' must select XCTestBackend"

    def test_preferred_indigo_forces_indigo(self):
        """preferred='indigo' selects IndigoHIDBackend even if XCTest is available."""
        xctest_class = _make_mock_backend_class(is_available=True)
        indigo_class = _make_mock_backend_class(is_available=True)
        cgevent_class = _make_mock_backend_class(is_available=True)

        with patch(_XCTEST_PATH, xctest_class), patch(_INDIGO_PATH, indigo_class), patch(_CGEVENTS_PATH, cgevent_class):
            selector = _make_selector(preferred="indigo")
            backend = selector.get_backend()

        assert backend is indigo_class.return_value, "preferred='indigo' must select IndigoHIDBackend"

    def test_preferred_cgevents_forces_cgevents(self):
        """preferred='cgevents' selects CGEventBackend regardless of availability."""
        xctest_class = _make_mock_backend_class(is_available=True)
        indigo_class = _make_mock_backend_class(is_available=True)
        cgevent_class = _make_mock_backend_class(is_available=True)

        with patch(_XCTEST_PATH, xctest_class), patch(_INDIGO_PATH, indigo_class), patch(_CGEVENTS_PATH, cgevent_class):
            selector = _make_selector(preferred="cgevents")
            backend = selector.get_backend()

        assert backend is cgevent_class.return_value, "preferred='cgevents' must select CGEventBackend"


# ===========================================================================
# TestBackendSelectorInterface — returned backend has required methods
# ===========================================================================


@needs_selector
class TestBackendSelectorInterface:
    """get_backend() always returns an object exposing the standard backend interface."""

    def test_returned_backend_has_tap(self):
        """get_backend() result has a callable tap() method."""
        xctest_class = _make_mock_backend_class(is_available=True)
        with (
            patch(_XCTEST_PATH, xctest_class),
            patch(_INDIGO_PATH, _make_mock_backend_class(is_available=False)),
            patch(_CGEVENTS_PATH, _make_mock_backend_class(is_available=False)),
        ):
            selector = _make_selector()
            backend = selector.get_backend()
        assert callable(getattr(backend, "tap", None)), "Backend must expose tap()"

    def test_returned_backend_has_swipe(self):
        """get_backend() result has a callable swipe() method."""
        xctest_class = _make_mock_backend_class(is_available=True)
        with (
            patch(_XCTEST_PATH, xctest_class),
            patch(_INDIGO_PATH, _make_mock_backend_class(is_available=False)),
            patch(_CGEVENTS_PATH, _make_mock_backend_class(is_available=False)),
        ):
            selector = _make_selector()
            backend = selector.get_backend()
        assert callable(getattr(backend, "swipe", None)), "Backend must expose swipe()"

    def test_returned_backend_has_type_text(self):
        """get_backend() result has a callable type_text() method."""
        xctest_class = _make_mock_backend_class(is_available=True)
        with (
            patch(_XCTEST_PATH, xctest_class),
            patch(_INDIGO_PATH, _make_mock_backend_class(is_available=False)),
            patch(_CGEVENTS_PATH, _make_mock_backend_class(is_available=False)),
        ):
            selector = _make_selector()
            backend = selector.get_backend()
        assert callable(getattr(backend, "type_text", None)), "Backend must expose type_text()"

    def test_returned_backend_has_screenshot(self):
        """get_backend() result has a callable screenshot() method."""
        xctest_class = _make_mock_backend_class(is_available=True)
        with (
            patch(_XCTEST_PATH, xctest_class),
            patch(_INDIGO_PATH, _make_mock_backend_class(is_available=False)),
            patch(_CGEVENTS_PATH, _make_mock_backend_class(is_available=False)),
        ):
            selector = _make_selector()
            backend = selector.get_backend()
        assert callable(getattr(backend, "screenshot", None)), "Backend must expose screenshot()"


# ===========================================================================
# TestBackendSelectorLogging — INFO-level switch logging
# ===========================================================================


@needs_selector
class TestBackendSelectorLogging:
    """Backend selection is logged at INFO level for observability."""

    def test_backend_switch_logged_at_info(self, caplog):
        """Selecting a backend emits at least one INFO-level log message."""
        xctest_class = _make_mock_backend_class(is_available=True)

        with (
            patch(_XCTEST_PATH, xctest_class),
            patch(_INDIGO_PATH, _make_mock_backend_class(is_available=False)),
            patch(_CGEVENTS_PATH, _make_mock_backend_class(is_available=False)),
        ):
            with caplog.at_level(logging.INFO):
                selector = _make_selector()
                selector.get_backend()

        info_records = [r for r in caplog.records if r.levelno >= logging.INFO]
        assert info_records, "BackendSelector must log at INFO level when selecting a backend"


# ===========================================================================
# TestBackendSelectorDynamicAvailability — re-checks on each call
# ===========================================================================


@needs_selector
class TestBackendSelectorDynamicAvailability:
    """get_backend() re-evaluates availability on each call, not just at construction."""

    def test_rechecks_availability_on_each_call(self):
        """If a backend becomes available after construction, get_backend() notices."""
        # Start with XCTest unavailable
        xctest_class = _make_mock_backend_class(is_available=False)
        indigo_class = _make_mock_backend_class(is_available=True)
        cgevent_class = _make_mock_backend_class(is_available=False)

        with patch(_XCTEST_PATH, xctest_class), patch(_INDIGO_PATH, indigo_class), patch(_CGEVENTS_PATH, cgevent_class):
            selector = _make_selector()

            # First call — XCTest down, should get IndigoHID
            first_backend = selector.get_backend()
            assert first_backend is indigo_class.return_value, (
                "First call should return IndigoHIDBackend when XCTest is unavailable"
            )

            # Now XCTest comes online
            xctest_class.is_available.return_value = True

            # Second call — XCTest now up, should get XCTestBackend
            second_backend = selector.get_backend()
            assert second_backend is xctest_class.return_value, (
                "Second call should return XCTestBackend after it becomes available"
            )


# ===========================================================================
# TestBackendSelectorAvailableList — introspection
# ===========================================================================


@needs_selector
class TestBackendSelectorAvailableList:
    """available_backends() returns the names of all currently available backends."""

    def test_available_backends_returns_list(self):
        """available_backends() returns a list (possibly empty)."""
        xctest_class = _make_mock_backend_class(is_available=True)
        indigo_class = _make_mock_backend_class(is_available=False)
        cgevent_class = _make_mock_backend_class(is_available=True)

        with patch(_XCTEST_PATH, xctest_class), patch(_INDIGO_PATH, indigo_class), patch(_CGEVENTS_PATH, cgevent_class):
            selector = _make_selector()
            result = selector.available_backends()

        assert isinstance(result, list), f"available_backends() must return a list, got {type(result)}"

    def test_available_backends_contains_available_names(self):
        """available_backends() includes names of backends that report is_available=True."""
        xctest_class = _make_mock_backend_class(is_available=True)
        indigo_class = _make_mock_backend_class(is_available=False)
        cgevent_class = _make_mock_backend_class(is_available=True)

        with patch(_XCTEST_PATH, xctest_class), patch(_INDIGO_PATH, indigo_class), patch(_CGEVENTS_PATH, cgevent_class):
            selector = _make_selector()
            names = selector.available_backends()

        # Names must be strings — accept any reasonable casing/spelling
        name_str = " ".join(str(n).lower() for n in names)
        assert "xctest" in name_str or "xc_test" in name_str, (
            f"'xctest' not found in available_backends() result: {names}"
        )
        assert "cgevent" in name_str or "cg_event" in name_str, (
            f"'cgevent' not found in available_backends() result: {names}"
        )
        # IndigoHID is unavailable — should NOT appear
        assert "indigo" not in name_str, (
            f"'indigo' should not appear in available_backends() when is_available=False: {names}"
        )
