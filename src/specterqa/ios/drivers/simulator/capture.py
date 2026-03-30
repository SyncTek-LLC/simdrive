"""M3: ScreenCapture — iOS Simulator screenshot and diff utilities.

Captures screenshots via ``xcrun simctl io <device> screenshot``, optionally
resizes them, and provides pixel-diff and polling utilities for visual
change detection.

INIT-2026-492.
"""

from __future__ import annotations

import base64
import io
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any


class ScreenCapture:
    """Screenshot capture and visual diff for an iOS Simulator device.

    Screenshots are taken by ``xcrun simctl io <device> screenshot``, then
    optionally resized using Pillow before being returned as base64-encoded
    PNG strings.

    Args:
        device_id: The simctl device identifier.  Defaults to ``"booted"``.
        resize_width: Target width (pixels) for resized screenshots.  Height
            is scaled proportionally.  Defaults to 1024.
    """

    # Minimum change_ratio threshold for wait_for_change/element_appears
    _CHANGE_THRESHOLD: float = 0.001

    def __init__(
        self,
        device_id: str = "booted",
        resize_width: int = 1024,
    ) -> None:
        self.device_id = device_id
        self.resize_width = resize_width

    # ------------------------------------------------------------------
    # Capture
    # ------------------------------------------------------------------

    def capture(self, resize_width: int | None = None) -> dict[str, Any]:
        """Capture a screenshot of the simulator and return it as a dict.

        Args:
            resize_width: Override the instance ``resize_width`` for this
                capture.  If ``None``, the instance default is used.

        Returns:
            A dict with keys:
            - ``base64`` (str): Base64-encoded PNG data.
            - ``width`` (int): Image width after resizing.
            - ``height`` (int): Image height after resizing.
            - ``timestamp`` (float): Unix timestamp of capture.
            - ``raw_path`` (str): Path to the temporary PNG file on disk.

        Raises:
            RuntimeError: If ``xcrun simctl screenshot`` exits with a
                non-zero return code.
        """
        target_width = resize_width if resize_width is not None else self.resize_width

        # Write screenshot to a temporary file
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
            tmp_path = tmp.name

        result = subprocess.run(
            ["xcrun", "simctl", "io", self.device_id, "screenshot", "--type=png", tmp_path],
            capture_output=True,
        )

        if result.returncode != 0:
            stderr = result.stderr.decode(errors="replace") if result.stderr else ""
            raise RuntimeError(
                f"xcrun simctl screenshot failed (returncode={result.returncode}): {stderr}"
            )

        # Read PNG bytes
        raw_png = Path(tmp_path).read_bytes()

        # Resize using Pillow
        from PIL import Image  # type: ignore[import]

        img = Image.open(io.BytesIO(raw_png))
        orig_w, orig_h = img.size

        if orig_w > 0:
            scale = target_width / orig_w
            new_h = int(orig_h * scale)
            img = img.resize((target_width, new_h), Image.LANCZOS)
        else:
            new_h = orig_h

        # Encode resized image to base64
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        encoded = base64.b64encode(buf.getvalue()).decode("ascii")

        return {
            "base64": encoded,
            "width": img.width,
            "height": img.height,
            "timestamp": time.time(),
            "raw_path": tmp_path,
        }

    # ------------------------------------------------------------------
    # Diff
    # ------------------------------------------------------------------

    def diff(
        self,
        a: dict[str, Any],
        b: dict[str, Any],
    ) -> dict[str, Any]:
        """Compute a pixel-level diff between two capture dicts.

        Decodes the ``base64`` PNG data from each dict and counts pixels that
        differ between them.

        Args:
            a: First capture dict (from :meth:`capture`).
            b: Second capture dict (from :meth:`capture`).

        Returns:
            A dict with keys:
            - ``changed_pixels`` (int): Number of pixels that differ.
            - ``total_pixels`` (int): Total pixels in the image.
            - ``change_ratio`` (float): ``changed_pixels / total_pixels``.
            - ``changed_regions`` (list): Reserved for future region metadata.
        """
        from PIL import Image  # type: ignore[import]
        import numpy as np  # type: ignore[import]

        def _decode(cap: dict[str, Any]) -> Image.Image:
            raw = base64.b64decode(cap["base64"])
            return Image.open(io.BytesIO(raw)).convert("RGB")

        img_a = _decode(a)
        img_b = _decode(b)

        # Normalise dimensions — resize b to match a if needed
        if img_a.size != img_b.size:
            img_b = img_b.resize(img_a.size, Image.LANCZOS)

        arr_a = np.array(img_a, dtype=np.int32)
        arr_b = np.array(img_b, dtype=np.int32)

        # Pixel is "changed" if any channel differs
        diff_mask = np.any(arr_a != arr_b, axis=2)
        changed = int(np.sum(diff_mask))
        total = img_a.width * img_a.height
        ratio = changed / total if total > 0 else 0.0

        return {
            "changed_pixels": changed,
            "total_pixels": total,
            "change_ratio": float(ratio),
            "changed_regions": [],
        }

    # ------------------------------------------------------------------
    # Polling utilities
    # ------------------------------------------------------------------

    def wait_for_change(
        self,
        baseline: dict[str, Any],
        timeout: float = 5.0,
        poll_interval: float = 0.5,
    ) -> bool:
        """Poll until the screen changes relative to *baseline* or timeout elapses.

        Args:
            baseline: A capture dict to compare against.
            timeout: Maximum seconds to wait.  Defaults to 5.0.
            poll_interval: Seconds between polls.  Defaults to 0.5.

        Returns:
            ``True`` if a change is detected, ``False`` if timeout elapses
            without change.
        """
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            current = self.capture()
            diff_result = self.diff(baseline, current)
            if diff_result["change_ratio"] > self._CHANGE_THRESHOLD:
                return True
            time.sleep(poll_interval)
        return False

    def element_appears(
        self,
        description: str,
        timeout: float = 5.0,
    ) -> bool:
        """Wait for a visual change indicating that an element has appeared.

        This is a simplified alias for :meth:`wait_for_change` — it captures
        a baseline and then polls for any screen change.

        Args:
            description: Human-readable description of the expected element
                (used for logging only; not parsed).
            timeout: Maximum seconds to wait.  Defaults to 5.0.

        Returns:
            ``True`` if a screen change is detected within *timeout*,
            ``False`` otherwise.
        """
        baseline = self.capture()
        return self.wait_for_change(baseline=baseline, timeout=timeout)
