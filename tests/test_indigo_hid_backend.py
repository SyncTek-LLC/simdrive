"""Tests for IndigoHIDBackend — pure-Python headless touch injection via IndigoHID.

TDD Phase — INIT-2026-500.
These tests are written BEFORE implementation exists and are importable even
when the implementation module is absent.

Module under test:
  specterqa/ios/backends/indigo_hid.py  —  IndigoHIDBackend

Architecture note
-----------------
IndigoHIDBackend uses ctypes + the ObjC runtime to call SimDeviceLegacyHIDClient,
NOT a direct C function exported from SimulatorKit.  Therefore tests that need to
inspect whether HID events are sent mock the ``_send`` internal method (which is
the single choke-point for all touch events) or use ``_load_frameworks`` to
control availability, rather than mocking low-level CDLL attributes.

Tests that need a fully-initialised backend set ``backend._hid_client`` to a
sentinel value so that ``_ensure_ready`` is satisfied without actually loading
any private frameworks.
"""

from __future__ import annotations

import ctypes
import struct
import threading
import time
from unittest.mock import MagicMock, patch, call

import pytest

# ---------------------------------------------------------------------------
# Conditional import guard — tests remain importable without implementation.
# ---------------------------------------------------------------------------

try:
    from specterqa.ios.backends.indigo_hid import (  # type: ignore[import]
        IndigoHIDBackend,
        _build_indigo_message,
        _build_indigo_payload,
        _INDIGO_EVENT_DOWN,
        _INDIGO_EVENT_MOVE,
        _INDIGO_EVENT_UP,
        _INDIGO_MAGIC,
        _INDIGO_MSG_HEADER_SIZE,
        _INDIGO_MSG_TOTAL,
        _INDIGO_PAYLOAD_STRIDE,
        _mach_time_ns,
        _load_frameworks,
        _sim_kit_path,
        _xcode_developer_path,
    )
    _INDIGO_AVAILABLE = True
except ImportError:
    _INDIGO_AVAILABLE = False
    IndigoHIDBackend = None  # type: ignore[assignment,misc]

needs_indigo = pytest.mark.skipif(
    not _INDIGO_AVAILABLE,
    reason="specterqa.ios.backends.indigo_hid not yet implemented",
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_TEST_UDID = "00008110-001A2B3C4D5E6F78"
_BOOTED = "booted"
_XCODE_DEV_PATH = "/Applications/Xcode.app/Contents/Developer"


def _make_backend(
    udid: str = _BOOTED,
    device_width: int = 393,
    device_height: int = 852,
    scale_factor: int = 3,
) -> "IndigoHIDBackend":
    """Construct an IndigoHIDBackend.  Constructor is lazy — no frameworks loaded."""
    return IndigoHIDBackend(
        udid=udid,
        device_width=device_width,
        device_height=device_height,
        scale_factor=scale_factor,
    )


def _make_ready_backend(**kw) -> "IndigoHIDBackend":
    """Backend with _hid_client pre-set so _ensure_ready is a no-op."""
    b = _make_backend(**kw)
    b._hid_client = 0xCAFEBABE
    b._bridge = MagicMock()
    return b


def _unpack_header(msg: bytes):
    return struct.unpack_from("<IIQ", msg, 0)


def _unpack_payload(msg: bytes, slot: int = 0):
    offset = _INDIGO_MSG_HEADER_SIZE + slot * _INDIGO_PAYLOAD_STRIDE
    return struct.unpack_from("<IIfff", msg, offset)


# ===========================================================================
# TestConstants — IndigoHID protocol constants
# ===========================================================================


@needs_indigo
class TestConstants:
    """The IndigoHID protocol constants have the expected values."""

    def test_event_down_is_1(self):
        assert _INDIGO_EVENT_DOWN == 0x00000001

    def test_event_move_is_2(self):
        assert _INDIGO_EVENT_MOVE == 0x00000002

    def test_event_up_is_3(self):
        assert _INDIGO_EVENT_UP == 0x00000003

    def test_magic_is_0x1A(self):
        assert _INDIGO_MAGIC == 0x0000001A

    def test_message_total_size(self):
        assert _INDIGO_MSG_TOTAL == 312

    def test_payload_stride(self):
        assert _INDIGO_PAYLOAD_STRIDE == 144


# ===========================================================================
# TestBuildIndigoMessage — byte-level layout verification
# ===========================================================================


@needs_indigo
class TestBuildIndigoMessage:
    """_build_indigo_message produces a correctly laid-out IndigoHID message."""

    def test_total_length(self):
        msg = _build_indigo_message(_INDIGO_EVENT_DOWN, 0.5, 0.5)
        assert len(msg) == _INDIGO_MSG_TOTAL

    def test_header_magic(self):
        msg = _build_indigo_message(_INDIGO_EVENT_DOWN, 0.0, 0.0)
        magic, pad, ts = _unpack_header(msg)
        assert magic == _INDIGO_MAGIC

    def test_header_timestamp_is_positive(self):
        msg = _build_indigo_message(_INDIGO_EVENT_DOWN, 0.5, 0.5)
        _, _, ts = _unpack_header(msg)
        assert ts > 0

    def test_slot0_event_type_down(self):
        msg = _build_indigo_message(_INDIGO_EVENT_DOWN, 0.5, 0.5)
        evt, touch_id, xn, yn, press = _unpack_payload(msg, slot=0)
        assert evt == _INDIGO_EVENT_DOWN

    def test_slot0_event_type_up(self):
        msg = _build_indigo_message(_INDIGO_EVENT_UP, 0.5, 0.5)
        evt, *_ = _unpack_payload(msg, slot=0)
        assert evt == _INDIGO_EVENT_UP

    def test_slot0_coordinates_stored_correctly(self):
        msg = _build_indigo_message(_INDIGO_EVENT_DOWN, 0.25, 0.75)
        _, _, xn, yn, press = _unpack_payload(msg, slot=0)
        assert abs(xn - 0.25) < 1e-5
        assert abs(yn - 0.75) < 1e-5

    def test_slot0_pressure_defaults_to_1(self):
        msg = _build_indigo_message(_INDIGO_EVENT_DOWN, 0.5, 0.5)
        _, _, _, _, press = _unpack_payload(msg, slot=0)
        assert abs(press - 1.0) < 1e-5

    def test_slot1_is_zeroed(self):
        msg = _build_indigo_message(_INDIGO_EVENT_DOWN, 0.5, 0.5)
        slot1_start = _INDIGO_MSG_HEADER_SIZE + _INDIGO_PAYLOAD_STRIDE
        assert all(b == 0 for b in msg[slot1_start:])

    def test_sequential_messages_have_nondecreasing_timestamps(self):
        msg1 = _build_indigo_message(_INDIGO_EVENT_DOWN, 0.5, 0.5)
        time.sleep(0.001)
        msg2 = _build_indigo_message(_INDIGO_EVENT_DOWN, 0.5, 0.5)
        _, _, ts1 = _unpack_header(msg1)
        _, _, ts2 = _unpack_header(msg2)
        assert ts2 >= ts1


# ===========================================================================
# TestNorm — coordinate normalisation
# ===========================================================================


@needs_indigo
class TestNorm:
    """_norm converts device-point coords to 0.0–1.0, clamped."""

    def test_center_normalises_to_half(self):
        b = _make_backend(device_width=393, device_height=852)
        xn, yn = b._norm(196.5, 426.0)
        assert abs(xn - 0.5) < 0.005
        assert abs(yn - 0.5) < 0.005

    def test_top_left_normalises_to_zero(self):
        b = _make_backend(device_width=393, device_height=852)
        xn, yn = b._norm(0.0, 0.0)
        assert xn == 0.0 and yn == 0.0

    def test_bottom_right_normalises_to_one(self):
        b = _make_backend(device_width=393, device_height=852)
        xn, yn = b._norm(393.0, 852.0)
        assert abs(xn - 1.0) < 1e-5 and abs(yn - 1.0) < 1e-5

    def test_negative_coords_clamped_to_zero(self):
        b = _make_backend(device_width=393, device_height=852)
        xn, yn = b._norm(-100.0, -50.0)
        assert xn == 0.0 and yn == 0.0

    def test_out_of_bounds_clamped_to_one(self):
        b = _make_backend(device_width=393, device_height=852)
        xn, yn = b._norm(9999.0, 9999.0)
        assert xn == 1.0 and yn == 1.0

    def test_various_device_sizes(self):
        for w, h in [(375, 667), (390, 844), (430, 932)]:
            b = _make_backend(device_width=w, device_height=h)
            xn, yn = b._norm(w * 0.25, h * 0.75)
            assert abs(xn - 0.25) < 1e-5
            assert abs(yn - 0.75) < 1e-5


# ===========================================================================
# TestIndigoHIDBackendConstructor — state stored correctly
# ===========================================================================


@needs_indigo
class TestIndigoHIDBackendConstructor:
    """Constructor stores UDID and device dimensions for use in all HID calls."""

    def test_constructor_stores_udid(self):
        """The UDID passed to the constructor is accessible on the instance."""
        backend = _make_backend(udid=_TEST_UDID)
        stored = getattr(backend, "udid", None) or getattr(backend, "_udid", None)
        assert stored == _TEST_UDID, f"Expected udid={_TEST_UDID!r}, got {stored!r}"

    def test_constructor_stores_device_dimensions(self):
        """device_width and device_height are stored for coordinate normalisation."""
        backend = _make_backend(device_width=430, device_height=932)
        width = getattr(backend, "device_width", None) or getattr(backend, "_device_width", None)
        height = getattr(backend, "device_height", None) or getattr(backend, "_device_height", None)
        assert width == 430
        assert height == 932

    def test_constructor_is_lazy_no_frameworks_loaded(self):
        """Constructor must NOT call ctypes.cdll.LoadLibrary — lazy init only."""
        with patch("ctypes.cdll.LoadLibrary") as mock_load:
            _make_backend()
        mock_load.assert_not_called()

    def test_hid_client_starts_as_none(self):
        b = _make_backend()
        assert getattr(b, "_hid_client", None) is None


# ===========================================================================
# TestIndigoHIDBackendAvailability — framework detection
# ===========================================================================


@needs_indigo
class TestIndigoHIDBackendAvailability:
    """is_available() detects whether SimulatorKit can be loaded."""

    def test_is_available_returns_bool(self):
        result = IndigoHIDBackend.is_available()
        assert isinstance(result, bool)

    def test_is_available_returns_true_when_hid_client_creatable(self):
        """is_available() returns True only when HID client creation is expected to work.

        This requires both frameworks to load AND _can_create_hid_client() to return
        True.  We mock _can_create_hid_client to simulate Xcode ≤ 15 (where HID
        client creation is safe from outside Simulator.app).
        """
        with patch.object(IndigoHIDBackend, "_can_create_hid_client", return_value=True):
            result = IndigoHIDBackend.is_available()
        assert result is True

    def test_is_available_returns_false_when_xcode16_blocks_hid_client(self):
        """is_available() returns False on Xcode 16+ (Swift-namespaced class).

        On Xcode 16+, SimulatorKit.SimDeviceLegacyHIDClient has a dot in its name,
        indicating it's Swift-module-qualified.  _can_create_hid_client() detects
        this and returns False to prevent initWithDevice:error: from SIGTRAPping.
        """
        with patch.object(IndigoHIDBackend, "_can_create_hid_client", return_value=False):
            result = IndigoHIDBackend.is_available()
        assert result is False

    def test_is_available_returns_false_when_framework_not_found(self):
        """is_available() returns False when LoadLibrary raises OSError."""
        import specterqa.ios.backends.indigo_hid as mod
        orig = mod._frameworks_loaded
        mod._frameworks_loaded = None
        try:
            with patch("ctypes.cdll.LoadLibrary", side_effect=OSError("image not found")):
                result = IndigoHIDBackend.is_available()
            assert result is False
        finally:
            mod._frameworks_loaded = orig

    def test_graceful_error_when_xcode_not_installed(self):
        """is_available() returns False (not raises) when Xcode is absent."""
        import specterqa.ios.backends.indigo_hid as mod
        orig = mod._frameworks_loaded
        mod._frameworks_loaded = None
        try:
            with patch(
                "specterqa.ios.backends.indigo_hid._xcode_developer_path",
                return_value="",
            ):
                result = IndigoHIDBackend.is_available()
        finally:
            mod._frameworks_loaded = orig
        assert result is False

    def test_framework_path_uses_xcode_select_dynamically(self):
        """The framework path is resolved via xcode-select, not hardcoded."""
        import specterqa.ios.backends.indigo_hid as mod
        orig = mod._frameworks_loaded
        mod._frameworks_loaded = None
        fake_dev_path = "/Applications/Xcode_15.app/Contents/Developer"
        try:
            captured_paths: list[str] = []

            def _capture_load(path: str) -> MagicMock:
                captured_paths.append(path)
                return MagicMock()

            mock_sp = MagicMock()
            mock_sp.returncode = 0
            mock_sp.stdout = fake_dev_path
            with patch("subprocess.run", return_value=mock_sp), \
                 patch("ctypes.cdll.LoadLibrary", side_effect=_capture_load), \
                 patch("os.path.exists", return_value=True):
                IndigoHIDBackend.is_available()
        finally:
            mod._frameworks_loaded = orig

        if captured_paths:
            assert any(
                fake_dev_path in p or "Xcode_15" in p for p in captured_paths
            ), f"Framework path does not incorporate xcode-select output.\nCaptured: {captured_paths}"


# ===========================================================================
# TestIndigoHIDBackendTap — HID touch event injection
# ===========================================================================


@needs_indigo
class TestIndigoHIDBackendTap:
    """tap(x, y) injects a touch-down / touch-up HID event pair via _send."""

    def _tap_with_captured_sends(self, x=100.0, y=200.0, **kw):
        b = _make_backend(**kw)
        calls = []
        b._send = lambda et, ex, ey, pressure=1.0: calls.append((et, ex, ey))
        b.tap(x=x, y=y)
        return calls

    def test_tap_calls_hid_send_method(self):
        """tap() invokes _send at least once."""
        calls = self._tap_with_captured_sends()
        assert len(calls) >= 1, "tap() must call _send at least once"

    def test_tap_sends_down_then_up(self):
        calls = self._tap_with_captured_sends()
        event_types = [c[0] for c in calls]
        assert _INDIGO_EVENT_DOWN in event_types
        assert _INDIGO_EVENT_UP in event_types

    def test_tap_down_before_up(self):
        calls = self._tap_with_captured_sends()
        types = [c[0] for c in calls]
        assert types.index(_INDIGO_EVENT_DOWN) < types.index(_INDIGO_EVENT_UP)

    def test_tap_coordinates_are_device_logical_points(self):
        """tap() forwards logical-point coordinates, not pixel coordinates.

        On a @3x device, logical (100, 200) != pixel (300, 600).
        The backend must NOT multiply by scale_factor.
        """
        calls = self._tap_with_captured_sends(x=100.0, y=200.0, scale_factor=3)
        # x=100, y=200 should appear — NOT x=300, y=600
        down = next((c for c in calls if c[0] == _INDIGO_EVENT_DOWN), None)
        assert down is not None
        assert down[1] == 100.0, f"Expected x=100.0, got {down[1]} (scale was applied?)"
        assert down[2] == 200.0, f"Expected y=200.0, got {down[2]} (scale was applied?)"


# ===========================================================================
# TestIndigoHIDBackendSwipe — multi-event HID swipe
# ===========================================================================


@needs_indigo
class TestIndigoHIDBackendSwipe:
    """swipe(x1, y1, x2, y2, duration) fires multiple HID events."""

    def _swipe_captured(self, x1=100.0, y1=700.0, x2=100.0, y2=200.0, duration=0.01, steps=5):
        b = _make_backend()
        calls = []
        b._send = lambda et, ex, ey, pressure=1.0: calls.append((et, ex, ey))
        b.swipe(x1=x1, y1=y1, x2=x2, y2=y2, duration=duration, steps=steps)
        return calls

    def test_swipe_sends_multiple_hid_events(self):
        """swipe() sends more than one HID event (down, move(s), up)."""
        calls = self._swipe_captured()
        assert len(calls) >= 2, "swipe() must send at least 2 HID events"

    def test_swipe_starts_with_down(self):
        calls = self._swipe_captured()
        assert calls[0][0] == _INDIGO_EVENT_DOWN

    def test_swipe_ends_with_up(self):
        calls = self._swipe_captured()
        assert calls[-1][0] == _INDIGO_EVENT_UP

    def test_swipe_has_move_events(self):
        calls = self._swipe_captured(steps=5)
        middle = [c[0] for c in calls[1:-1]]
        assert all(e == _INDIGO_EVENT_MOVE for e in middle)

    def test_swipe_event_count_equals_steps_plus_two(self):
        steps = 7
        calls = self._swipe_captured(steps=steps)
        assert len(calls) == steps + 2


# ===========================================================================
# TestIndigoHIDBackendLongPress — press-and-hold HID event
# ===========================================================================


@needs_indigo
class TestIndigoHIDBackendLongPress:
    """long_press(x, y, duration) sends DOWN then UP (holds in between)."""

    def _long_press_captured(self, x=200.0, y=400.0, duration=0.01):
        b = _make_backend()
        calls = []
        b._send = lambda et, ex, ey, pressure=1.0: calls.append((et, ex, ey))
        b.long_press(x=x, y=y, duration=duration)
        return calls

    def test_long_press_sends_press_event(self):
        calls = self._long_press_captured()
        assert len(calls) >= 1, "long_press() must call _send at least once"

    def test_long_press_sends_down_and_up(self):
        calls = self._long_press_captured()
        types = [c[0] for c in calls]
        assert _INDIGO_EVENT_DOWN in types
        assert _INDIGO_EVENT_UP in types

    def test_long_press_holds_longer_than_tap(self):
        """long_press() default duration is 3.0s (longer than a normal tap)."""
        import inspect
        sig = inspect.signature(IndigoHIDBackend.long_press)
        default_duration = sig.parameters["duration"].default
        assert default_duration == 3.0

    def test_long_press_only_two_events(self):
        """long_press() sends exactly DOWN + UP (no intermediate MOVE events)."""
        calls = self._long_press_captured()
        assert len(calls) == 2


# ===========================================================================
# TestIndigoHIDBackendScreenshot — delegates to simctl
# ===========================================================================


@needs_indigo
class TestIndigoHIDBackendScreenshot:
    """screenshot() delegates to 'xcrun simctl io <udid> screenshot' via subprocess."""

    def test_screenshot_delegates_to_simctl(self):
        """screenshot() calls xcrun simctl io screenshot (not the HID framework)."""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stderr = ""

        with patch("subprocess.run", return_value=mock_result) as mock_run, \
             patch("os.close"):
            backend = _make_backend(udid=_TEST_UDID)
            backend.screenshot(output_path="/tmp/test.png")

        assert mock_run.called, "screenshot() must call subprocess.run"
        cmd_str = " ".join(str(a) for a in mock_run.call_args.args[0])
        assert "simctl" in cmd_str, f"Expected 'simctl' in screenshot command: {cmd_str!r}"

    def test_screenshot_includes_udid(self):
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stderr = ""

        with patch("subprocess.run", return_value=mock_result) as mock_run, \
             patch("os.close"):
            backend = _make_backend(udid=_TEST_UDID)
            backend.screenshot(output_path="/tmp/test.png")

        cmd_args = mock_run.call_args.args[0]
        assert _TEST_UDID in cmd_args or _TEST_UDID in " ".join(str(a) for a in cmd_args)

    def test_screenshot_raises_on_failure(self):
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stderr = "No booted device found"

        with patch("subprocess.run", return_value=mock_result), \
             pytest.raises(RuntimeError):
            _make_backend().screenshot(output_path="/tmp/test.png")


# ===========================================================================
# TestIndigoHIDBackendBooted — 'booted' UDID resolution
# ===========================================================================


@needs_indigo
class TestIndigoHIDBackendBooted:
    """UDID='booted' is passed through; simctl receives 'booted' verbatim."""

    def test_booted_udid_passed_to_simctl(self):
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stderr = ""

        with patch("subprocess.run", return_value=mock_result) as mock_run, \
             patch("os.close"):
            backend = _make_backend(udid=_BOOTED)
            backend.screenshot(output_path="/tmp/test.png")

        cmd_str = " ".join(str(a) for a in mock_run.call_args.args[0])
        assert "booted" in cmd_str


# ===========================================================================
# TestIndigoHIDBackendRepr — human-readable representation
# ===========================================================================


@needs_indigo
class TestIndigoHIDBackendRepr:
    """__repr__ contains UDID, dimensions, and initialisation state."""

    def test_repr_contains_udid(self):
        b = _make_backend(udid=_TEST_UDID)
        assert _TEST_UDID in repr(b)

    def test_repr_contains_dimensions(self):
        b = _make_backend(device_width=390, device_height=844)
        r = repr(b)
        assert "390" in r and "844" in r

    def test_repr_shows_lazy_before_init(self):
        b = _make_backend()
        assert "lazy" in repr(b)

    def test_repr_shows_ready_after_init(self):
        b = _make_backend()
        b._hid_client = 0xDEAD
        assert "ready" in repr(b)

    def test_repr_does_not_raise_on_partial_init(self):
        b = IndigoHIDBackend.__new__(IndigoHIDBackend)
        try:
            r = repr(b)
            assert isinstance(r, str)
        except Exception as exc:
            pytest.fail(f"repr() raised on partially-initialised object: {exc}")


# ===========================================================================
# TestIndigoHIDBackendThreadSafety — concurrent access
# ===========================================================================


@needs_indigo
class TestIndigoHIDBackendThreadSafety:
    """Backend can be safely called from multiple threads concurrently."""

    def test_concurrent_taps_do_not_raise(self):
        """10 concurrent tap() calls on the same backend instance do not raise."""
        errors: list[Exception] = []

        def _tap_worker(backend: "IndigoHIDBackend", idx: int) -> None:
            try:
                backend.tap(x=idx * 10.0, y=idx * 20.0)
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

        backend = _make_backend()
        # Stub out _send so threads don't need real frameworks
        backend._send = lambda et, x, y, pressure=1.0: None

        threads = [
            threading.Thread(target=_tap_worker, args=(backend, i))
            for i in range(10)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5.0)

        assert not errors, (
            f"Thread safety violation — {len(errors)} error(s):\n"
            + "\n".join(str(e) for e in errors)
        )


# ===========================================================================
# TestXcodePathDetection — dynamic path resolution
# ===========================================================================


@needs_indigo
class TestXcodePathDetection:
    """SimulatorKit path is derived from xcode-select -p output."""

    def test_xcode_developer_path_calls_xcode_select(self):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value.returncode = 0
            mock_run.return_value.stdout = _XCODE_DEV_PATH + "\n"
            path = _xcode_developer_path()
        assert path == _XCODE_DEV_PATH

    def test_xcode_developer_path_returns_empty_on_failure(self):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value.returncode = 1
            mock_run.return_value.stdout = ""
            path = _xcode_developer_path()
        assert path == ""

    def test_sim_kit_path_contains_simulatorkit(self):
        with patch(
            "specterqa.ios.backends.indigo_hid._xcode_developer_path",
            return_value=_XCODE_DEV_PATH,
        ):
            path = _sim_kit_path()
        assert "SimulatorKit" in path
        assert path.startswith(_XCODE_DEV_PATH)

    def test_sim_kit_path_empty_when_no_xcode(self):
        with patch(
            "specterqa.ios.backends.indigo_hid._xcode_developer_path",
            return_value="",
        ):
            path = _sim_kit_path()
        assert path == ""
