"""Tests for M2: InteractionLayer — iOS Simulator touch/keyboard interaction driver.

TDD Phase — INIT-2026-492.
These tests are written BEFORE implementation exists and are importable even
when the implementation module is absent.

Module under test (to be created by CodeAtlas):
  specterqa/ios/drivers/simulator/interaction.py  —  InteractionLayer
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Conditional import guard — tests remain importable without implementation.
# ---------------------------------------------------------------------------

try:
    from specterqa.ios.drivers.simulator.interaction import InteractionLayer  # type: ignore[import]

    _INTERACTION_AVAILABLE = True
except ImportError:
    _INTERACTION_AVAILABLE = False
    InteractionLayer = None  # type: ignore[assignment,misc]

needs_interaction = pytest.mark.skipif(
    not _INTERACTION_AVAILABLE,
    reason="specterqa.ios.drivers.simulator.interaction not yet implemented",
)


# ---------------------------------------------------------------------------
# Helpers — build mock Quartz window info structures
# ---------------------------------------------------------------------------


def _make_window_info(x: float, y: float, width: float, height: float) -> dict:
    """Build a minimal CGWindowListCopyWindowInfo entry for Simulator.app."""
    return {
        "kCGWindowOwnerName": "Simulator",
        "kCGWindowBounds": {
            "X": x,
            "Y": y,
            "Width": width,
            "Height": height,
        },
        "kCGWindowLayer": 0,
    }


def _make_layer(device_id: str = "booted", title_bar_offset: int = 28) -> "InteractionLayer":
    """Construct an InteractionLayer with a mocked simulator window."""
    layer = InteractionLayer(device_id=device_id, title_bar_offset=title_bar_offset)
    return layer


# ===========================================================================
#  _image_to_screen coordinate math — 15 tests
# ===========================================================================


@needs_interaction
class TestImageToScreenCenter:
    """Center point of an image maps to center of simulator content area."""

    def test_center_of_image(self):
        """Center pixel (w/2, h/2) in image maps to center of simulator content."""
        layer = InteractionLayer()

        window = _make_window_info(x=100.0, y=50.0, width=400.0, height=800.0)
        with patch.object(layer, "_get_simulator_window", return_value=window):
            img_w, img_h = 390, 844  # iPhone 15 Pro logical resolution (approx)
            sx, sy = layer._image_to_screen(img_w // 2, img_h // 2, img_w, img_h)

        # x should be near window_x + width/2 = 100 + 200 = 300
        # y should be near window_y + title_bar + (img_h/2 / img_h) * content_height
        assert isinstance(sx, float)
        assert isinstance(sy, float)
        # Sanity: must be inside the window bounds
        assert 100.0 <= sx <= 500.0
        assert 50.0 <= sy <= 850.0


@needs_interaction
class TestImageToScreenTopLeft:
    """Top-left corner (0, 0) maps to top-left of simulator content area."""

    def test_top_left_corner(self):
        """(0, 0) in image space maps to the top-left of the simulator content."""
        layer = InteractionLayer(title_bar_offset=28)

        window = _make_window_info(x=200.0, y=100.0, width=400.0, height=800.0)
        with patch.object(layer, "_get_simulator_window", return_value=window):
            sx, sy = layer._image_to_screen(0, 0, 390, 844)

        # x should be at (or very near) window_x = 200
        assert abs(sx - 200.0) < 2.0, f"Expected sx≈200, got {sx}"
        # y should be at window_y + title_bar_offset = 100 + 28 = 128
        assert abs(sy - 128.0) < 2.0, f"Expected sy≈128, got {sy}"


@needs_interaction
class TestImageToScreenBottomRight:
    """Bottom-right corner (w-1, h-1) maps to bottom-right of simulator content."""

    def test_bottom_right_corner(self):
        """Near bottom-right pixel maps to near bottom-right of content area."""
        layer = InteractionLayer(title_bar_offset=28)

        win_x, win_y, win_w, win_h = 0.0, 0.0, 400.0, 828.0
        window = _make_window_info(x=win_x, y=win_y, width=win_w, height=win_h)
        with patch.object(layer, "_get_simulator_window", return_value=window):
            img_w, img_h = 390, 800
            sx, sy = layer._image_to_screen(img_w - 1, img_h - 1, img_w, img_h)

        # Should be near right edge of window
        assert sx <= win_x + win_w + 1.0
        # Should be near bottom edge of window (minus title bar region)
        assert sy <= win_y + win_h + 1.0


@needs_interaction
class TestImageToScreenRetina2x:
    """Retina 2x: logical image coords map to same screen coords as non-retina."""

    def test_retina_2x_scaling(self):
        """A 2x retina image (double resolution) of the same window region
        maps to identical screen coordinates as the standard-res equivalent."""
        layer = InteractionLayer(title_bar_offset=28)

        window = _make_window_info(x=100.0, y=50.0, width=390.0, height=844.0)
        with patch.object(layer, "_get_simulator_window", return_value=window):
            # Standard resolution: pixel (195, 422) in a 390x844 image
            sx_1x, sy_1x = layer._image_to_screen(195, 422, 390, 844)
            # 2x retina: same logical position is pixel (390, 844) in a 780x1688 image
            sx_2x, sy_2x = layer._image_to_screen(390, 844, 780, 1688)

        # Both should resolve to the same screen coordinate
        assert abs(sx_1x - sx_2x) < 2.0, f"2x Retina x mismatch: {sx_1x} vs {sx_2x}"
        assert abs(sy_1x - sy_2x) < 2.0, f"2x Retina y mismatch: {sy_1x} vs {sy_2x}"


@needs_interaction
class TestImageToScreenRetina3x:
    """Retina 3x: logical image coords map to same screen coords as 1x."""

    def test_retina_3x_scaling(self):
        """A 3x retina image maps to the same screen coordinate as 1x for the
        same logical position."""
        layer = InteractionLayer(title_bar_offset=28)

        window = _make_window_info(x=0.0, y=0.0, width=390.0, height=844.0)
        with patch.object(layer, "_get_simulator_window", return_value=window):
            # 1x: tap at roughly (100, 200) in a 390x844 image
            sx_1x, sy_1x = layer._image_to_screen(100, 200, 390, 844)
            # 3x: same logical tap is (300, 600) in a 1170x2532 image
            sx_3x, sy_3x = layer._image_to_screen(300, 600, 1170, 2532)

        assert abs(sx_1x - sx_3x) < 3.0, f"3x Retina x mismatch: {sx_1x} vs {sx_3x}"
        assert abs(sy_1x - sy_3x) < 3.0, f"3x Retina y mismatch: {sy_1x} vs {sy_3x}"


@needs_interaction
class TestImageToScreenTitleBar28:
    """Standard 28px title bar shifts y coordinate down by 28px."""

    def test_title_bar_28px_shifts_y(self):
        """With a 28px title bar, y=0 in image space should be at
        window_y + 28 in screen space."""
        layer = InteractionLayer(title_bar_offset=28)

        window = _make_window_info(x=0.0, y=100.0, width=400.0, height=800.0)
        with patch.object(layer, "_get_simulator_window", return_value=window):
            _, sy = layer._image_to_screen(0, 0, 400, 800)

        assert abs(sy - 128.0) < 2.0, f"Expected sy≈128 with 28px title bar, got {sy}"


@needs_interaction
class TestImageToScreenTitleBarZero:
    """Fullscreen (0px title bar): y=0 in image maps directly to window_y."""

    def test_title_bar_zero_fullscreen(self):
        """With title_bar_offset=0, y=0 in image space maps to window_y."""
        layer = InteractionLayer(title_bar_offset=0)

        window = _make_window_info(x=0.0, y=100.0, width=400.0, height=800.0)
        with patch.object(layer, "_get_simulator_window", return_value=window):
            _, sy = layer._image_to_screen(0, 0, 400, 800)

        assert abs(sy - 100.0) < 2.0, f"Expected sy≈100 with 0px title bar, got {sy}"


@needs_interaction
class TestImageToScreenUserResizedWindow:
    """Scaled window (e.g. 75%): coordinate ratio is preserved."""

    def test_user_resized_window(self):
        """When the user resizes the simulator window, the coordinate formula
        scales proportionally — center of image still maps to center of content."""
        layer = InteractionLayer(title_bar_offset=28)

        # Small window (75% scale)
        window = _make_window_info(x=50.0, y=50.0, width=292.0, height=603.0)
        with patch.object(layer, "_get_simulator_window", return_value=window):
            img_w, img_h = 390, 844
            sx, sy = layer._image_to_screen(img_w // 2, img_h // 2, img_w, img_h)

        content_h = 603.0 - 28
        expected_x = 50.0 + (img_w / 2 / img_w) * 292.0
        expected_y = 50.0 + 28 + (img_h / 2 / img_h) * content_h

        assert abs(sx - expected_x) < 2.0, f"sx mismatch: expected {expected_x}, got {sx}"
        assert abs(sy - expected_y) < 2.0, f"sy mismatch: expected {expected_y}, got {sy}"


@needs_interaction
class TestImageToScreenEdgeCoordinates:
    """Boundary pixels (first and last row/col) produce in-bounds screen coords."""

    def test_first_pixel(self):
        """Pixel (0, 0) produces a screen coordinate inside the window."""
        layer = InteractionLayer(title_bar_offset=28)
        window = _make_window_info(x=10.0, y=10.0, width=390.0, height=844.0)
        with patch.object(layer, "_get_simulator_window", return_value=window):
            sx, sy = layer._image_to_screen(0, 0, 390, 844)
        assert sx >= 10.0 and sy >= 10.0

    def test_last_pixel(self):
        """Pixel (w-1, h-1) produces a screen coordinate inside the window."""
        layer = InteractionLayer(title_bar_offset=28)
        window = _make_window_info(x=10.0, y=10.0, width=390.0, height=844.0)
        with patch.object(layer, "_get_simulator_window", return_value=window):
            sx, sy = layer._image_to_screen(389, 843, 390, 844)
        assert sx <= 10.0 + 390.0 + 1.0
        assert sy <= 10.0 + 844.0 + 1.0

    def test_midpoint_x_edge(self):
        """img_x == img_w // 2, img_y == 0 maps to horizontal center, top content edge."""
        layer = InteractionLayer(title_bar_offset=28)
        window = _make_window_info(x=0.0, y=0.0, width=390.0, height=844.0)
        with patch.object(layer, "_get_simulator_window", return_value=window):
            sx, sy = layer._image_to_screen(195, 0, 390, 844)
        assert abs(sx - 195.0) < 2.0
        assert abs(sy - 28.0) < 2.0


@needs_interaction
class TestImageToScreenReturnTypes:
    """_image_to_screen returns a 2-tuple of floats."""

    def test_return_is_float_tuple(self):
        """Return value is exactly a 2-tuple of (float, float)."""
        layer = InteractionLayer()
        window = _make_window_info(x=0.0, y=0.0, width=390.0, height=844.0)
        with patch.object(layer, "_get_simulator_window", return_value=window):
            result = layer._image_to_screen(100, 200, 390, 844)
        assert isinstance(result, tuple)
        assert len(result) == 2
        sx, sy = result
        assert isinstance(sx, float)
        assert isinstance(sy, float)


# ===========================================================================
#  tap — 3 tests
# ===========================================================================


@needs_interaction
class TestTap:
    """tap() sends a CGEvent mouse-down + mouse-up click at converted coords."""

    def test_tap_calls_cgevent(self):
        """tap() must create and post a CGEvent left-click at the screen coordinate
        derived from _image_to_screen."""
        layer = InteractionLayer()
        window = _make_window_info(x=0.0, y=0.0, width=390.0, height=844.0)

        with (
            patch.object(layer, "_get_simulator_window", return_value=window),
            patch.object(layer, "_activate_simulator"),
            patch("Quartz.CGEventCreateMouseEvent") as mock_cg_create,
            patch("Quartz.CGEventPost") as mock_cg_post,
        ):
            mock_event = MagicMock()
            mock_cg_create.return_value = mock_event
            layer.tap(img_x=195, img_y=422, img_w=390, img_h=844)

        # Should have created at least 2 events (mouse-down + mouse-up)
        assert mock_cg_create.call_count >= 2
        assert mock_cg_post.call_count >= 2

    def test_tap_coordinates_are_derived_from_image_to_screen(self):
        """The CGEvent receives the coordinate output of _image_to_screen,
        not the raw image coordinates."""
        layer = InteractionLayer(title_bar_offset=28)
        window = _make_window_info(x=100.0, y=50.0, width=390.0, height=844.0)
        _expected_sx, _expected_sy = 100.0 + 195.0, 50.0 + 28.0  # top-center approx

        posted_points: list[tuple] = []

        def capture_event(event_type, source, position, button):  # type: ignore[override]
            # position is a plain tuple (x, y) — _make_point returns tuple[float, float]
            posted_points.append((position[0], position[1]))
            return MagicMock()

        with (
            patch.object(layer, "_get_simulator_window", return_value=window),
            patch.object(layer, "_activate_simulator"),
            patch("Quartz.CGEventCreateMouseEvent", side_effect=capture_event),
            patch("Quartz.CGEventPost"),
        ):
            layer.tap(img_x=195, img_y=0, img_w=390, img_h=844)

        assert len(posted_points) >= 1
        # The x coordinate should be close to the window-derived value
        px, py = posted_points[0]
        assert abs(px - 295.0) < 3.0, f"Expected px≈295, got {px}"

    def test_tap_no_error_on_valid_coords(self):
        """tap() completes without raising for valid image coordinates."""
        layer = InteractionLayer()
        window = _make_window_info(x=0.0, y=0.0, width=390.0, height=844.0)
        with (
            patch.object(layer, "_get_simulator_window", return_value=window),
            patch.object(layer, "_activate_simulator"),
            patch("Quartz.CGEventCreateMouseEvent", return_value=MagicMock()),
            patch("Quartz.CGEventPost"),
        ):
            layer.tap(img_x=100, img_y=200, img_w=390, img_h=844)  # Must not raise


# ===========================================================================
#  double_tap — 2 tests
# ===========================================================================


@needs_interaction
class TestDoubleTap:
    """double_tap() dispatches two rapid mouse clicks."""

    def test_double_tap_sends_two_click_sequences(self):
        """double_tap() must issue at least 4 CGEvent calls (2x down+up)."""
        layer = InteractionLayer()
        window = _make_window_info(x=0.0, y=0.0, width=390.0, height=844.0)
        with (
            patch.object(layer, "_get_simulator_window", return_value=window),
            patch.object(layer, "_activate_simulator"),
            patch("Quartz.CGEventCreateMouseEvent", return_value=MagicMock()) as mock_create,
            patch("Quartz.CGEventPost"),
        ):
            layer.double_tap(img_x=100, img_y=200, img_w=390, img_h=844)
        assert mock_create.call_count >= 4, f"Expected ≥4 CGEvent creates for double-tap, got {mock_create.call_count}"

    def test_double_tap_at_same_coordinate(self):
        """Both taps in double_tap() target the same screen coordinate."""
        layer = InteractionLayer(title_bar_offset=28)
        window = _make_window_info(x=0.0, y=0.0, width=390.0, height=844.0)
        coords: list[Any] = []

        def capture(event_type, source, position, button):  # type: ignore
            # position is a plain tuple (x, y) — _make_point returns tuple[float, float]
            coords.append((position[0], position[1]))
            return MagicMock()

        with (
            patch.object(layer, "_get_simulator_window", return_value=window),
            patch.object(layer, "_activate_simulator"),
            patch("Quartz.CGEventCreateMouseEvent", side_effect=capture),
            patch("Quartz.CGEventPost"),
        ):
            layer.double_tap(img_x=150, img_y=300, img_w=390, img_h=844)

        # All events should share the same x,y
        assert len(coords) >= 2
        xs = [c[0] for c in coords]
        ys = [c[1] for c in coords]
        assert max(xs) - min(xs) < 2.0, "double_tap events have different x coords"
        assert max(ys) - min(ys) < 2.0, "double_tap events have different y coords"


# ===========================================================================
#  long_press — 2 tests
# ===========================================================================


@needs_interaction
class TestLongPress:
    """long_press() holds mouse button for the specified duration."""

    def test_long_press_sends_mouse_down_then_up(self):
        """long_press() must send mouse-down, wait, then mouse-up.

        Prototype timing: sleep(duration) + sleep(0.5) post-cooldown.
        _activate_simulator is mocked so its 0.15s overhead is excluded.
        Total for duration=1.5: 1.5 + 0.5 = 2.0s.
        """
        layer = InteractionLayer()
        window = _make_window_info(x=0.0, y=0.0, width=390.0, height=844.0)
        call_log: list[str] = []

        with (
            patch.object(layer, "_get_simulator_window", return_value=window),
            patch.object(layer, "_activate_simulator"),
            patch("Quartz.CGEventCreateMouseEvent", return_value=MagicMock()),
            patch("Quartz.CGEventPost", side_effect=lambda tap, ev: call_log.append("post")),
            patch("time.sleep", side_effect=lambda d: call_log.append(f"sleep:{d}")),
        ):
            layer.long_press(img_x=100, img_y=200, img_w=390, img_h=844, duration=1.5)

        # There should be at least one sleep call with ≈1.5 seconds
        sleep_calls = [c for c in call_log if c.startswith("sleep:")]
        assert len(sleep_calls) >= 1, "long_press must sleep to hold the button"
        # Prototype adds a 0.5s post-cooldown: total = duration + 0.5
        total_sleep = sum(float(c.split(":")[1]) for c in sleep_calls)
        expected_total = 1.5 + 0.5  # duration + post-cooldown
        assert abs(total_sleep - expected_total) < 0.2, (
            f"Expected ≈{expected_total}s total sleep for long_press(1.5), got {total_sleep}"
        )

    def test_long_press_default_duration(self):
        """long_press() default duration is 3.0 seconds when not specified.

        Prototype timing: sleep(3.0) + sleep(0.5) post-cooldown = 3.5s total.
        _activate_simulator is mocked so its 0.15s overhead is excluded.
        """
        layer = InteractionLayer()
        window = _make_window_info(x=0.0, y=0.0, width=390.0, height=844.0)
        sleep_durations: list[float] = []

        with (
            patch.object(layer, "_activate_simulator"),
            patch.object(layer, "_get_simulator_window", return_value=window),
            patch("Quartz.CGEventCreateMouseEvent", return_value=MagicMock()),
            patch("Quartz.CGEventPost"),
            patch("time.sleep", side_effect=lambda d: sleep_durations.append(d)),
        ):
            layer.long_press(img_x=100, img_y=200, img_w=390, img_h=844)

        total = sum(sleep_durations)
        expected_total = 3.0 + 0.5  # default duration + post-cooldown
        assert abs(total - expected_total) < 0.2, (
            f"Expected ≈{expected_total}s total long_press sleep (default), got {total}"
        )


# ===========================================================================
#  swipe — 2 tests
# ===========================================================================


@needs_interaction
class TestSwipe:
    """swipe() creates a smooth drag gesture from start to end coordinate."""

    def test_swipe_posts_drag_events(self):
        """swipe() must call CGEventPost with drag (kCGEventLeftMouseDragged)
        events between the start and end points."""
        layer = InteractionLayer()
        window = _make_window_info(x=0.0, y=0.0, width=390.0, height=844.0)

        with (
            patch.object(layer, "_get_simulator_window", return_value=window),
            patch.object(layer, "_activate_simulator"),
            patch("Quartz.CGEventCreateMouseEvent", return_value=MagicMock()) as mock_create,
            patch("Quartz.CGEventPost"),
        ):
            layer.swipe(x1=195, y1=700, x2=195, y2=200, img_w=390, img_h=844)

        # Should have posted multiple drag events (down + multiple drag + up)
        assert mock_create.call_count >= 3, f"Expected ≥3 CGEvent calls for swipe, got {mock_create.call_count}"

    def test_swipe_duration_controls_sleep(self):
        """swipe() with duration=0.5 should sleep ~0.5 seconds across drag steps.

        Prototype timing breakdown (with _activate_simulator mocked):
          - 0.02s initial hold before dragging
          - duration (0.5s) spread across 25 drag steps
          - 0.3s post-swipe cooldown
          Total: 0.02 + 0.5 + 0.3 = 0.82s
        """
        layer = InteractionLayer()
        window = _make_window_info(x=0.0, y=0.0, width=390.0, height=844.0)
        sleep_durations: list[float] = []

        with (
            patch.object(layer, "_get_simulator_window", return_value=window),
            patch.object(layer, "_activate_simulator"),
            patch("Quartz.CGEventCreateMouseEvent", return_value=MagicMock()),
            patch("Quartz.CGEventPost"),
            patch("time.sleep", side_effect=lambda d: sleep_durations.append(d)),
        ):
            layer.swipe(x1=195, y1=700, x2=195, y2=200, img_w=390, img_h=844, duration=0.5)

        total = sum(sleep_durations)
        # 0.02 (initial hold) + 0.5 (drag steps) + 0.3 (post-swipe cooldown) = 0.82
        expected_total = 0.02 + 0.5 + 0.3
        assert abs(total - expected_total) < 0.15, f"Expected ≈{expected_total}s total swipe sleep, got {total}"


# ===========================================================================
#  type_text — 3-strategy cascade — 4 tests
# ===========================================================================


@needs_interaction
class TestTypeTextSimctlFirst:
    """type_text() strategy 1: simctl io keyboard input."""

    def test_type_text_uses_simctl_keyboard_first(self):
        """When simctl io keyboard input succeeds (returncode=0), it is used
        and no fallback strategy is attempted."""
        layer = InteractionLayer(device_id="test-device-123")

        success = MagicMock()
        success.returncode = 0

        with patch("subprocess.run", return_value=success) as mock_run:
            layer.type_text("hello world")

        # First call should be the simctl keyboard strategy
        first_call = mock_run.call_args_list[0]
        cmd = first_call[0][0] if first_call[0] else first_call.args[0]
        cmd_str = " ".join(cmd) if isinstance(cmd, list) else str(cmd)
        assert "simctl" in cmd_str
        assert "keyboard" in cmd_str or "input" in cmd_str
        assert "hello world" in cmd_str or mock_run.call_count == 1


@needs_interaction
class TestTypeTextFallbackToPbcopy:
    """type_text() strategy 2: simctl pbcopy + Cmd+V paste fallback."""

    def test_type_text_falls_back_to_pbcopy(self):
        """When simctl keyboard fails, type_text falls back to pbcopy + paste."""
        layer = InteractionLayer(device_id="test-device-123")

        failure = MagicMock()
        failure.returncode = 1
        failure.stderr = b"unsupported"

        success = MagicMock()
        success.returncode = 0

        call_count = {"n": 0}

        def side_effect(cmd, *args, **kwargs):
            call_count["n"] += 1
            if call_count["n"] == 1:
                return failure
            return success

        with (
            patch("subprocess.run", side_effect=side_effect) as mock_run,
            patch("Quartz.CGEventCreateKeyboardEvent", return_value=MagicMock()),
            patch("Quartz.CGEventPost"),
        ):
            layer.type_text("fallback text")

        # At minimum 2 subprocess calls: first fails, second is pbcopy strategy
        assert mock_run.call_count >= 2


@needs_interaction
class TestTypeTextFallbackToKeystrokes:
    """type_text() strategy 3: individual CGEvent keystrokes as last resort."""

    def test_type_text_falls_back_to_keystrokes(self):
        """When both simctl and pbcopy strategies fail, type_text sends individual
        CGEvent keystroke events for each character."""
        layer = InteractionLayer(device_id="test-device-123")

        failure = MagicMock()
        failure.returncode = 1
        failure.stderr = b"error"

        with (
            patch("subprocess.run", return_value=failure),
            patch("Quartz.CGEventCreateKeyboardEvent", return_value=MagicMock()) as mock_key,
            patch("Quartz.CGEventPost"),
        ):
            layer.type_text("hi")  # 2 chars → at least 2 key-down events

        # At least 2 keystroke events (one per character, key-down minimum)
        assert mock_key.call_count >= 2, f"Expected ≥2 CGEvent keyboard events for 'hi', got {mock_key.call_count}"

    def test_type_text_empty_string_no_error(self):
        """type_text('') must not raise even when all strategies are available."""
        layer = InteractionLayer()
        success = MagicMock()
        success.returncode = 0
        with patch("subprocess.run", return_value=success):
            layer.type_text("")  # Must not raise


# ===========================================================================
#  press_key — key name mapping — 8 tests
# ===========================================================================


@needs_interaction
class TestPressKeyMapping:
    """press_key() maps key names to correct CGEvent virtual key codes."""

    KEY_CODES = {
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

    def _get_posted_keycodes(self, layer: Any, key_name: str) -> list[int]:
        """Helper: press key and collect all key codes sent to CGEvent."""
        codes: list[int] = []

        def capture(source, key_code, key_down):
            codes.append(key_code)
            return MagicMock()

        with patch("Quartz.CGEventCreateKeyboardEvent", side_effect=capture), patch("Quartz.CGEventPost"):
            layer.press_key(key_name)

        return codes

    def test_enter_key_code(self):
        layer = InteractionLayer()
        codes = self._get_posted_keycodes(layer, "enter")
        assert 36 in codes, f"Expected keycode 36 (enter), got {codes}"

    def test_escape_key_code(self):
        layer = InteractionLayer()
        codes = self._get_posted_keycodes(layer, "escape")
        assert 53 in codes, f"Expected keycode 53 (escape), got {codes}"

    def test_tab_key_code(self):
        layer = InteractionLayer()
        codes = self._get_posted_keycodes(layer, "tab")
        assert 48 in codes, f"Expected keycode 48 (tab), got {codes}"

    def test_delete_key_code(self):
        layer = InteractionLayer()
        codes = self._get_posted_keycodes(layer, "delete")
        assert 51 in codes, f"Expected keycode 51 (delete), got {codes}"

    def test_space_key_code(self):
        layer = InteractionLayer()
        codes = self._get_posted_keycodes(layer, "space")
        assert 49 in codes, f"Expected keycode 49 (space), got {codes}"

    def test_arrow_keys(self):
        layer = InteractionLayer()
        assert 126 in self._get_posted_keycodes(layer, "up")
        assert 125 in self._get_posted_keycodes(layer, "down")
        assert 123 in self._get_posted_keycodes(layer, "left")
        assert 124 in self._get_posted_keycodes(layer, "right")

    def test_press_key_sends_down_and_up(self):
        """press_key() must send both key-down and key-up events."""
        layer = InteractionLayer()
        events: list[tuple] = []

        def capture(source, key_code, key_down):
            events.append((key_code, key_down))
            return MagicMock()

        with patch("Quartz.CGEventCreateKeyboardEvent", side_effect=capture), patch("Quartz.CGEventPost"):
            layer.press_key("enter")

        key_downs = [e for e in events if e[1] is True]
        key_ups = [e for e in events if e[1] is False]
        assert len(key_downs) >= 1, "Must have at least one key-down event"
        assert len(key_ups) >= 1, "Must have at least one key-up event"

    def test_press_unknown_key_raises(self):
        """press_key() with an unmapped key name should raise ValueError (not crash silently)."""
        layer = InteractionLayer()
        with pytest.raises((ValueError, KeyError)):
            with patch("Quartz.CGEventCreateKeyboardEvent", return_value=MagicMock()), patch("Quartz.CGEventPost"):
                layer.press_key("__nonexistent_key__")


# ===========================================================================
#  key_combo — 2 tests
# ===========================================================================


@needs_interaction
class TestKeyCombo:
    """key_combo() sends modifier keys + base key combination."""

    def test_key_combo_cmd_v(self):
        """key_combo(['cmd'], 'v') sends Cmd+V paste shortcut."""
        layer = InteractionLayer()
        events: list[tuple] = []

        def capture_event(source, key_code, key_down):
            events.append((key_code, key_down))
            return MagicMock()

        with (
            patch("Quartz.CGEventCreateKeyboardEvent", side_effect=capture_event),
            patch("Quartz.CGEventSetFlags"),
            patch("Quartz.CGEventPost"),
        ):
            layer.key_combo(modifiers=["cmd"], key="v")

        # Should have generated events for V key (key code 9)
        v_events = [e for e in events if e[0] == 9]
        assert len(v_events) >= 1, f"No 'v' key events (keycode 9) found in {events}"

    def test_key_combo_shift_enter(self):
        """key_combo(['shift'], 'enter') generates events with shift modifier."""
        layer = InteractionLayer()
        flags_set: list[int] = []

        def capture_flags(event, flags):
            flags_set.append(flags)

        with (
            patch("Quartz.CGEventCreateKeyboardEvent", return_value=MagicMock()),
            patch("Quartz.CGEventSetFlags", side_effect=capture_flags),
            patch("Quartz.CGEventPost"),
        ):
            layer.key_combo(modifiers=["shift"], key="enter")

        # Shift flag (0x20000) should have been set
        assert any(flags & 0x20000 for flags in flags_set), (
            f"Shift modifier flag not found in CGEventSetFlags calls: {flags_set}"
        )


# ===========================================================================
#  _get_simulator_window — error handling — 2 tests
# ===========================================================================


@needs_interaction
class TestGetSimulatorWindowError:
    """_get_simulator_window raises when Simulator.app is not running."""

    def test_raises_when_simulator_not_found(self):
        """When CGWindowListCopyWindowInfo returns no Simulator.app window,
        _get_simulator_window must raise RuntimeError (or similar) so the
        caller can surface a clear error rather than computing bad coordinates."""
        layer = InteractionLayer()

        # Return an empty window list — no Simulator.app running
        with patch("Quartz.CGWindowListCopyWindowInfo", return_value=[]):
            with pytest.raises((RuntimeError, ValueError, LookupError)):
                layer._get_simulator_window()

    def test_returns_dict_when_simulator_found(self):
        """When Simulator.app window is present, returns a dict with
        x, y, width, height keys."""
        layer = InteractionLayer()

        fake_windows = [
            {
                "kCGWindowOwnerName": "Simulator",
                "kCGWindowBounds": {"X": 200.0, "Y": 100.0, "Width": 390.0, "Height": 844.0},
                "kCGWindowLayer": 0,
            }
        ]

        with patch("Quartz.CGWindowListCopyWindowInfo", return_value=fake_windows):
            result = layer._get_simulator_window()

        assert "x" in result or "X" in result or result.get("width") is not None
        # Must have geometry data
        vals = list(result.values())
        assert any(isinstance(v, (int, float)) for v in vals)


# ===========================================================================
#  Constructor defaults — 1 test
# ===========================================================================


@needs_interaction
class TestInteractionLayerConstructor:
    """InteractionLayer constructor accepts expected arguments."""

    def test_default_constructor(self):
        """InteractionLayer() with no args uses device_id='booted' and
        title_bar_offset=28."""
        layer = InteractionLayer()
        assert layer.device_id == "booted" or getattr(layer, "_device_id", None) == "booted"
        # title_bar_offset should default to 28
        offset = getattr(layer, "title_bar_offset", None) or getattr(layer, "_title_bar_offset", None)
        assert offset == 28, f"Expected title_bar_offset=28, got {offset}"

    def test_custom_device_id(self):
        """device_id and title_bar_offset are stored on the instance."""
        layer = InteractionLayer(device_id="ABC-123", title_bar_offset=0)
        device = getattr(layer, "device_id", None) or getattr(layer, "_device_id", None)
        assert device == "ABC-123"
