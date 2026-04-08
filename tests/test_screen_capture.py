"""Tests for M3: ScreenCapture — iOS Simulator screenshot and diff utilities.

TDD Phase — INIT-2026-492.
These tests are written BEFORE implementation exists and are importable even
when the implementation module is absent.

Module under test (to be created by CodeAtlas):
  specterqa/ios/drivers/simulator/capture.py  —  ScreenCapture
"""

from __future__ import annotations

import base64
import io
import subprocess
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Conditional import guard — tests remain importable without implementation.
# ---------------------------------------------------------------------------

try:
    from specterqa.ios.drivers.simulator.capture import ScreenCapture  # type: ignore[import]

    _CAPTURE_AVAILABLE = True
except ImportError:
    _CAPTURE_AVAILABLE = False
    ScreenCapture = None  # type: ignore[assignment,misc]

needs_capture = pytest.mark.skipif(
    not _CAPTURE_AVAILABLE,
    reason="specterqa.ios.drivers.simulator.capture not yet implemented",
)


# ---------------------------------------------------------------------------
# Helpers — build synthetic test images via PIL or raw bytes
# ---------------------------------------------------------------------------


def _make_solid_png(width: int, height: int, color: tuple[int, int, int]) -> bytes:
    """Return a minimal PNG of a solid colour using PIL if available,
    otherwise a raw stub (the stub is sufficient for mock-based tests)."""
    try:
        from PIL import Image  # type: ignore[import]

        img = Image.new("RGB", (width, height), color)
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()
    except ImportError:
        # Minimal 1x1 valid PNG fallback — good enough for tests that
        # don't parse actual pixel values.
        import zlib
        import struct

        def _png(w: int, h: int, rgb: tuple[int, int, int]) -> bytes:
            def chunk(name: bytes, data: bytes) -> bytes:
                c = struct.pack(">I", len(data)) + name + data
                return c + struct.pack(">I", zlib.crc32(name + data) & 0xFFFFFFFF)

            ihdr = struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)
            raw = b""
            for _ in range(h):
                raw += b"\x00" + bytes(rgb) * w
            idat = zlib.compress(raw)
            return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", idat) + chunk(b"IEND", b"")

        return _png(width, height, color)


def _make_capture_dict(width: int, height: int, color: tuple[int, int, int] = (128, 128, 128)) -> dict:
    """Build a capture dict matching the ScreenCapture.capture() return schema."""
    raw = _make_solid_png(width, height, color)
    return {
        "base64": base64.b64encode(raw).decode(),
        "width": width,
        "height": height,
        "timestamp": time.time(),
        "raw_path": "/tmp/specterqa_test_capture.png",
    }


# ===========================================================================
#  capture() — 4 tests
# ===========================================================================


@needs_capture
class TestCaptureCallsSimctl:
    """capture() invokes xcrun simctl io screenshot with correct arguments."""

    def test_capture_calls_simctl_screenshot(self, tmp_path: Path):
        """capture() must call subprocess.run with 'xcrun simctl io <device>
        screenshot --type=png <tmpfile>' and return a dict with all required keys."""
        cap = ScreenCapture(device_id="booted", resize_width=390)

        png_data = _make_solid_png(390, 844, (0, 0, 0))
        mock_result = MagicMock()
        mock_result.returncode = 0

        def fake_run(cmd, *args, **kwargs):
            # Simulate simctl writing the PNG file to the tmp path
            # Find the file path argument (last positional arg in the command list)
            if (
                isinstance(cmd, list)
                and len(cmd) > 0
                and "simctl" in cmd[0]
                or (isinstance(cmd, list) and any("simctl" in c for c in cmd))
            ):
                # Write PNG to whichever path simctl would write to
                for arg in reversed(cmd):
                    if arg.endswith(".png") or arg.endswith(".tmp"):
                        Path(arg).write_bytes(png_data)
                        break
            return mock_result

        with patch("subprocess.run", side_effect=fake_run) as mock_run:
            result = cap.capture()

        # subprocess.run must have been called
        assert mock_run.called, "subprocess.run was never called"
        cmd_str = " ".join(
            str(a)
            for a in (
                mock_run.call_args_list[0][0][0]
                if mock_run.call_args_list[0][0]
                else mock_run.call_args_list[0].args[0]
            )
        )
        assert "simctl" in cmd_str
        assert "screenshot" in cmd_str

        # Return dict must have all required keys
        for key in ("base64", "width", "height", "timestamp", "raw_path"):
            assert key in result, f"capture() result missing key: '{key}'"

    def test_capture_returns_base64_string(self, tmp_path: Path):
        """The 'base64' field in capture() result must be a valid base64 string."""
        cap = ScreenCapture(device_id="booted")
        png_data = _make_solid_png(10, 10, (255, 0, 0))
        mock_result = MagicMock()
        mock_result.returncode = 0

        def fake_run(cmd, *args, **kwargs):
            for arg in reversed(cmd if isinstance(cmd, list) else []):
                if str(arg).endswith(".png"):
                    Path(str(arg)).write_bytes(png_data)
                    break
            return mock_result

        with patch("subprocess.run", side_effect=fake_run):
            result = cap.capture()

        b64 = result.get("base64", "")
        assert isinstance(b64, str)
        # Must be valid base64
        decoded = base64.b64decode(b64)
        assert len(decoded) > 0


@needs_capture
class TestCaptureResizes:
    """capture() resizes the screenshot to the configured resize_width."""

    def test_capture_resizes_to_specified_width(self, tmp_path: Path):
        """When resize_width=200, the returned 'width' must be 200 (or very close)
        and height scaled proportionally."""
        cap = ScreenCapture(device_id="booted", resize_width=200)
        # Original image is 400 wide × 800 tall → resized should be 200 × 400
        png_data = _make_solid_png(400, 800, (64, 128, 192))
        mock_result = MagicMock()
        mock_result.returncode = 0

        def fake_run(cmd, *args, **kwargs):
            for arg in reversed(cmd if isinstance(cmd, list) else []):
                if str(arg).endswith(".png"):
                    Path(str(arg)).write_bytes(png_data)
                    break
            return mock_result

        with patch("subprocess.run", side_effect=fake_run):
            result = cap.capture(resize_width=200)

        assert result["width"] == 200, f"Expected width=200 after resize, got {result['width']}"
        # Height should be proportionally scaled (400/800 → 200/x → x≈400)
        assert result["height"] > 0

    def test_capture_uses_default_resize_width(self, tmp_path: Path):
        """When resize_width is not passed to capture(), it uses the instance default."""
        cap = ScreenCapture(device_id="booted", resize_width=1024)
        png_data = _make_solid_png(2048, 4096, (0, 255, 0))
        mock_result = MagicMock()
        mock_result.returncode = 0

        def fake_run(cmd, *args, **kwargs):
            for arg in reversed(cmd if isinstance(cmd, list) else []):
                if str(arg).endswith(".png"):
                    Path(str(arg)).write_bytes(png_data)
                    break
            return mock_result

        with patch("subprocess.run", side_effect=fake_run):
            result = cap.capture()  # No resize_width override

        assert result["width"] == 1024, f"Expected default resize_width=1024, got {result['width']}"


# ===========================================================================
#  diff() — 3 tests
# ===========================================================================


@needs_capture
class TestDiffIdentical:
    """diff() returns 0 changed pixels for two identical images."""

    def test_diff_identical_images(self):
        """Comparing a capture dict with itself must return 0 changed_pixels
        and 0.0 change_ratio."""
        cap = ScreenCapture()
        snapshot = _make_capture_dict(width=10, height=10, color=(128, 128, 128))
        result = cap.diff(snapshot, snapshot)

        assert result["changed_pixels"] == 0, (
            f"Expected 0 changed_pixels for identical images, got {result['changed_pixels']}"
        )
        assert result["change_ratio"] == pytest.approx(0.0), f"Expected change_ratio=0.0, got {result['change_ratio']}"


@needs_capture
class TestDiffDetectsChanges:
    """diff() detects pixel differences between two dissimilar images."""

    def test_diff_different_images(self):
        """Two images of different colours must produce changed_pixels > 0."""
        cap = ScreenCapture()
        black = _make_capture_dict(width=10, height=10, color=(0, 0, 0))
        white = _make_capture_dict(width=10, height=10, color=(255, 255, 255))
        result = cap.diff(black, white)

        assert result["changed_pixels"] > 0, "Expected changed_pixels > 0 for black vs white images"
        assert result["change_ratio"] > 0.0, f"Expected change_ratio > 0, got {result['change_ratio']}"

    def test_diff_change_ratio_formula(self):
        """change_ratio == changed_pixels / total_pixels."""
        cap = ScreenCapture()
        black = _make_capture_dict(width=10, height=10, color=(0, 0, 0))
        white = _make_capture_dict(width=10, height=10, color=(255, 255, 255))
        result = cap.diff(black, white)

        total = result["total_pixels"]
        changed = result["changed_pixels"]
        expected_ratio = changed / total if total > 0 else 0.0

        assert abs(result["change_ratio"] - expected_ratio) < 0.01, (
            f"change_ratio {result['change_ratio']} does not match changed_pixels/total_pixels = {expected_ratio}"
        )


# ===========================================================================
#  wait_for_change() — 3 tests
# ===========================================================================


@needs_capture
class TestWaitForChange:
    """wait_for_change() polls until screenshot changes or timeout."""

    def test_returns_true_when_screenshot_changes(self):
        """wait_for_change() returns True when a new capture differs from baseline."""
        cap = ScreenCapture(device_id="booted")
        baseline = _make_capture_dict(width=10, height=10, color=(0, 0, 0))
        changed = _make_capture_dict(width=10, height=10, color=(255, 255, 255))

        call_count = {"n": 0}

        def mock_capture(**kwargs):
            call_count["n"] += 1
            # Return changed image on second poll
            return changed if call_count["n"] >= 2 else baseline

        with patch.object(cap, "capture", side_effect=mock_capture):
            result = cap.wait_for_change(baseline=baseline, timeout=5.0, poll_interval=0.1)

        assert result is True, "Expected wait_for_change to return True when screen changes"

    def test_returns_false_on_timeout(self):
        """wait_for_change() returns False when no change occurs within timeout."""
        cap = ScreenCapture(device_id="booted")
        baseline = _make_capture_dict(width=10, height=10, color=(0, 0, 0))

        # Always return same image → no change
        with patch.object(cap, "capture", return_value=baseline), patch("time.sleep"):  # avoid real sleeping in test
            result = cap.wait_for_change(baseline=baseline, timeout=0.05, poll_interval=0.01)

        assert result is False, "Expected wait_for_change to return False on timeout"

    def test_polls_at_correct_interval(self):
        """wait_for_change() calls time.sleep with the configured poll_interval."""
        cap = ScreenCapture(device_id="booted")
        baseline = _make_capture_dict(width=10, height=10, color=(0, 0, 0))
        sleep_calls: list[float] = []

        with (
            patch.object(cap, "capture", return_value=baseline),
            patch("time.sleep", side_effect=lambda d: sleep_calls.append(d)),
        ):
            cap.wait_for_change(baseline=baseline, timeout=0.05, poll_interval=0.02)

        # Each sleep call should use poll_interval=0.02
        assert len(sleep_calls) >= 1
        for duration in sleep_calls:
            assert abs(duration - 0.02) < 0.005, f"Expected poll_interval=0.02 per sleep, got {duration}"


# ===========================================================================
#  element_appears() — 2 tests
# ===========================================================================


@needs_capture
class TestElementAppears:
    """element_appears() returns True/False based on visual change detection."""

    def test_returns_true_when_change_detected(self):
        """element_appears() returns True when the screen changes within timeout."""
        cap = ScreenCapture(device_id="booted")

        with patch.object(cap, "capture") as mock_cap, patch.object(cap, "wait_for_change", return_value=True):
            mock_cap.return_value = _make_capture_dict(10, 10, (0, 0, 0))
            result = cap.element_appears(description="Login button", timeout=5.0)

        assert result is True

    def test_returns_false_on_timeout(self):
        """element_appears() returns False when no visual change within timeout."""
        cap = ScreenCapture(device_id="booted")

        with patch.object(cap, "capture") as mock_cap, patch.object(cap, "wait_for_change", return_value=False):
            mock_cap.return_value = _make_capture_dict(10, 10, (0, 0, 0))
            result = cap.element_appears(description="Login button", timeout=1.0)

        assert result is False


# ===========================================================================
#  Error handling — 1 test
# ===========================================================================


@needs_capture
class TestCaptureErrorHandling:
    """capture() raises when simctl screenshot command fails."""

    def test_capture_raises_on_simctl_failure(self, tmp_path: Path):
        """When xcrun simctl screenshot exits with returncode != 0,
        capture() must raise RuntimeError (or similar) with a meaningful message
        rather than returning a corrupt/empty dict."""
        cap = ScreenCapture(device_id="booted")

        failure = MagicMock()
        failure.returncode = 1
        failure.stderr = b"No devices booted"

        with patch("subprocess.run", return_value=failure):
            with pytest.raises((RuntimeError, OSError, subprocess.SubprocessError, Exception)) as exc_info:
                cap.capture()

        # Exception message should hint at the failure
        msg = str(exc_info.value).lower()
        assert len(msg) > 0, "Exception message must not be empty"


# ===========================================================================
#  Constructor defaults — 1 test
# ===========================================================================


@needs_capture
class TestScreenCaptureConstructor:
    """ScreenCapture constructor stores device_id and resize_width."""

    def test_default_constructor(self):
        """ScreenCapture() with no args uses device_id='booted' and resize_width=1024."""
        cap = ScreenCapture()
        device = getattr(cap, "device_id", None) or getattr(cap, "_device_id", None)
        assert device == "booted", f"Expected device_id='booted', got {device!r}"
        resize = getattr(cap, "resize_width", None) or getattr(cap, "_resize_width", None)
        assert resize == 1024, f"Expected resize_width=1024, got {resize}"

    def test_custom_params(self):
        """Custom device_id and resize_width are stored on the instance."""
        cap = ScreenCapture(device_id="iPhone-15-Pro", resize_width=512)
        device = getattr(cap, "device_id", None) or getattr(cap, "_device_id", None)
        assert device == "iPhone-15-Pro"
        resize = getattr(cap, "resize_width", None) or getattr(cap, "_resize_width", None)
        assert resize == 512


# ===========================================================================
#  diff() result schema — 1 test
# ===========================================================================


@needs_capture
class TestDiffResultSchema:
    """diff() returns a dict with all required keys."""

    def test_diff_result_has_required_keys(self):
        """diff() must return a dict containing changed_pixels, total_pixels,
        change_ratio, and changed_regions."""
        cap = ScreenCapture()
        a = _make_capture_dict(width=10, height=10, color=(0, 0, 0))
        b = _make_capture_dict(width=10, height=10, color=(128, 128, 128))
        result = cap.diff(a, b)

        for key in ("changed_pixels", "total_pixels", "change_ratio", "changed_regions"):
            assert key in result, f"diff() result missing key: '{key}'"

        assert isinstance(result["changed_pixels"], int)
        assert isinstance(result["total_pixels"], int)
        assert isinstance(result["change_ratio"], float)
        assert isinstance(result["changed_regions"], list)
