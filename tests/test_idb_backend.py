"""Tests for M15: IdbInputBackend — idb-based multi-device touch/keyboard input.

TDD Phase — INIT-2026-492.
These tests are written BEFORE implementation exists and are importable even
when the implementation module is absent.

Module under test (to be created by CodeAtlas):
  specterqa/ios/parallel/idb_backend.py  —  IdbInputBackend
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Conditional import guard — tests remain importable without implementation.
# ---------------------------------------------------------------------------

try:
    from specterqa.ios.parallel.idb_backend import IdbInputBackend  # type: ignore[import]

    _IDB_AVAILABLE = True
except ImportError:
    _IDB_AVAILABLE = False
    IdbInputBackend = None  # type: ignore[assignment,misc]

needs_idb = pytest.mark.skipif(
    not _IDB_AVAILABLE,
    reason="specterqa.ios.parallel.idb_backend not yet implemented",
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_TEST_UDID = "00008110-001A2B3C4D5E6F78"


def _make_backend(udid: str = _TEST_UDID) -> "IdbInputBackend":
    """Construct an IdbInputBackend for the test UDID."""
    return IdbInputBackend(udid=udid)


def _run_ok() -> MagicMock:
    """Return a mock subprocess.CompletedProcess with returncode 0."""
    result = MagicMock()
    result.returncode = 0
    result.stdout = b""
    result.stderr = b""
    return result


# ===========================================================================
# TestIdbInputBackendConstructor — UDID storage
# ===========================================================================


@needs_idb
class TestIdbInputBackendConstructor:
    """Constructor stores the UDID for use in all subsequent commands."""

    def test_constructor_stores_udid(self):
        """The UDID passed to the constructor is accessible on the instance."""
        backend = _make_backend(udid=_TEST_UDID)
        stored = getattr(backend, "udid", None) or getattr(backend, "_udid", None)
        assert stored == _TEST_UDID, f"Expected udid={_TEST_UDID!r}, got {stored!r}"


# ===========================================================================
# TestIdbInputBackendTap — tap command construction
# ===========================================================================


@needs_idb
class TestIdbInputBackendTap:
    """tap() invokes 'idb ui tap --udid <udid> <x> <y>'."""

    def test_tap_calls_subprocess_with_udid_and_coords(self):
        """tap() passes the UDID and coordinates to subprocess."""
        backend = _make_backend()
        with patch("subprocess.run", return_value=_run_ok()) as mock_run:
            backend.tap(x=150, y=300)

        assert mock_run.called, "tap() did not call subprocess.run"
        cmd = mock_run.call_args.args[0]
        cmd_str = " ".join(str(a) for a in cmd)
        assert "idb" in cmd_str, f"'idb' not in tap command: {cmd_str!r}"
        assert _TEST_UDID in cmd_str, f"UDID not in tap command: {cmd_str!r}"
        assert "150" in cmd_str, f"x=150 not in tap command: {cmd_str!r}"
        assert "300" in cmd_str, f"y=300 not in tap command: {cmd_str!r}"

    def test_tap_udid_always_present(self):
        """UDID is always present in the tap subprocess command, never omitted."""
        backend = _make_backend(udid="CUSTOM-UDID-9999")
        with patch("subprocess.run", return_value=_run_ok()) as mock_run:
            backend.tap(x=0, y=0)
        cmd_str = " ".join(str(a) for a in mock_run.call_args.args[0])
        assert "CUSTOM-UDID-9999" in cmd_str

    def test_tap_raises_runtime_error_when_idb_not_installed(self):
        """tap() raises RuntimeError with a helpful message when idb is not on PATH."""
        backend = _make_backend()
        with patch("shutil.which", return_value=None), pytest.raises(RuntimeError) as exc_info:
            backend.tap(x=100, y=200)
        assert "idb" in str(exc_info.value).lower(), "RuntimeError message should mention 'idb'"


# ===========================================================================
# TestIdbInputBackendSwipe — swipe command construction
# ===========================================================================


@needs_idb
class TestIdbInputBackendSwipe:
    """swipe() invokes 'idb ui swipe --udid <udid> x1 y1 x2 y2 --duration <d>'."""

    def test_swipe_calls_subprocess_with_correct_args(self):
        """swipe() passes start/end coordinates and duration to idb."""
        backend = _make_backend()
        with patch("subprocess.run", return_value=_run_ok()) as mock_run:
            backend.swipe(x1=100, y1=600, x2=100, y2=200, duration=0.5)

        assert mock_run.called, "swipe() did not call subprocess.run"
        cmd_str = " ".join(str(a) for a in mock_run.call_args.args[0])
        assert "idb" in cmd_str
        assert _TEST_UDID in cmd_str
        assert "100" in cmd_str  # x1/x2
        assert "600" in cmd_str  # y1
        assert "200" in cmd_str  # y2
        # Duration must appear somewhere (as a float or string)
        assert "0.5" in cmd_str or "0.50" in cmd_str, f"duration=0.5 not found in swipe command: {cmd_str!r}"


# ===========================================================================
# TestIdbInputBackendTypeText — text input command construction
# ===========================================================================


@needs_idb
class TestIdbInputBackendTypeText:
    """type_text() invokes 'idb ui text --udid <udid> <text>'."""

    def test_type_text_calls_subprocess_with_text(self):
        """type_text() passes the text string to idb."""
        backend = _make_backend()
        with patch("subprocess.run", return_value=_run_ok()) as mock_run:
            backend.type_text("hello idb")

        assert mock_run.called, "type_text() did not call subprocess.run"
        cmd_str = " ".join(str(a) for a in mock_run.call_args.args[0])
        assert "idb" in cmd_str
        assert _TEST_UDID in cmd_str
        # The text content should be passed somewhere
        assert "hello idb" in cmd_str or all(w in cmd_str for w in ["hello", "idb"]), (
            f"Text not found in command: {cmd_str!r}"
        )


# ===========================================================================
# TestIdbInputBackendPressKey — key press command construction
# ===========================================================================


@needs_idb
class TestIdbInputBackendPressKey:
    """press_key() invokes 'idb ui key --udid <udid> <key_id>'."""

    def test_press_key_calls_subprocess_with_key_id(self):
        """press_key() passes the integer key_id to the idb command."""
        backend = _make_backend()
        with patch("subprocess.run", return_value=_run_ok()) as mock_run:
            backend.press_key(key_id=36)  # 36 = Enter / Return

        assert mock_run.called, "press_key() did not call subprocess.run"
        cmd_str = " ".join(str(a) for a in mock_run.call_args.args[0])
        assert "idb" in cmd_str
        assert _TEST_UDID in cmd_str
        assert "36" in cmd_str, f"key_id=36 not in press_key command: {cmd_str!r}"


# ===========================================================================
# TestIdbInputBackendIsAvailable — PATH detection
# ===========================================================================


@needs_idb
class TestIdbInputBackendIsAvailable:
    """is_available() class method reports idb presence on PATH."""

    def test_is_available_returns_true_when_idb_on_path(self):
        """is_available() returns True when shutil.which finds idb."""
        with patch("shutil.which", return_value="/usr/local/bin/idb"):
            assert IdbInputBackend.is_available() is True

    def test_is_available_returns_false_when_idb_not_on_path(self):
        """is_available() returns False when shutil.which returns None."""
        with patch("shutil.which", return_value=None):
            assert IdbInputBackend.is_available() is False

    def test_graceful_fallback_message_when_idb_missing(self):
        """When idb is missing, the RuntimeError raised by tap() contains a
        human-readable fallback message explaining how to install idb."""
        backend = _make_backend()
        with patch("shutil.which", return_value=None):
            try:
                backend.tap(x=50, y=50)
                pytest.fail("Expected RuntimeError when idb is missing")
            except RuntimeError as exc:
                msg = str(exc).lower()
                # Message should guide the user — mention idb and ideally install/path
                assert "idb" in msg, f"RuntimeError should mention 'idb', got: {exc!r}"
