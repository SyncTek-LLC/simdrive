"""HID injection backend — drives the iOS simulator via a bundled native helper.

This is the primary input backend: real UITouch / keyboard / button events
that trigger UITextField first-responder requests on iOS 26 (which
synthetic mouse events don't). The helper binary lives at
`simdrive/_bin/simdrive-input` inside the package.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Optional


_BINARY_NAME = "simdrive-input"


def _binary_path() -> Optional[Path]:
    """Return path to the bundled simdrive-input binary, or None if missing."""
    here = Path(__file__).parent
    candidate = here / "_bin" / _BINARY_NAME
    if candidate.exists():
        return candidate
    # Dev override
    env = os.environ.get("SIMDRIVE_INPUT_BINARY")
    if env and Path(env).exists():
        return Path(env)
    return None


def available() -> bool:
    p = _binary_path()
    return p is not None and os.access(str(p), os.X_OK)


def _run(args: list[str], timeout: float = 5.0) -> None:
    bp = _binary_path()
    if not bp:
        raise RuntimeError("simdrive-input binary not found in package")
    res = subprocess.run([str(bp), *args], capture_output=True, text=True, timeout=timeout)
    if res.returncode != 0:
        raise RuntimeError(
            f"simdrive-input {args[1]} failed (rc={res.returncode}): "
            f"{res.stderr.strip() or res.stdout.strip()}"
        )


def device_size_points(udid: str) -> tuple[float, float, float]:
    """Return (width_points, height_points, scale) by querying the binary."""
    bp = _binary_path()
    if not bp:
        raise RuntimeError("simdrive-input binary not found")
    res = subprocess.run([str(bp), udid, "size"], capture_output=True, text=True, timeout=5.0)
    if res.returncode != 0:
        raise RuntimeError(f"size query failed: {res.stderr.strip()}")
    parts = res.stdout.strip().split()
    if len(parts) != 3:
        raise RuntimeError(f"unexpected size output: {res.stdout!r}")
    pixel_w, pixel_h, scale = float(parts[0]), float(parts[1]), float(parts[2])
    return (pixel_w / scale, pixel_h / scale, scale)


def tap(udid: str, x_points: float, y_points: float) -> None:
    _run([udid, "tap", f"{x_points:.2f}", f"{y_points:.2f}"])


def touch_down(udid: str, x_points: float, y_points: float) -> None:
    _run([udid, "down", f"{x_points:.2f}", f"{y_points:.2f}"])


def touch_up(udid: str, x_points: float, y_points: float) -> None:
    _run([udid, "up", f"{x_points:.2f}", f"{y_points:.2f}"])


def swipe(
    udid: str, x1: float, y1: float, x2: float, y2: float, steps: int = 12, step_delay_ms: int = 25
) -> None:
    """Drag from (x1, y1) → (x2, y2) by a sequence of down → moves → up.

    The binary doesn't yet have a single-shot 'swipe' command; we approximate
    by dispatching down + intermediate moves + up.
    """
    import time as _t
    touch_down(udid, x1, y1)
    for i in range(1, steps + 1):
        t = i / steps
        ix = x1 + (x2 - x1) * t
        iy = y1 + (y2 - y1) * t
        touch_down(udid, ix, iy)  # 'down' at intermediate points keeps the touch held
        _t.sleep(step_delay_ms / 1000.0)
    touch_up(udid, x2, y2)


def type_text(udid: str, text: str) -> None:
    if not text:
        return
    _run([udid, "text", text], timeout=15.0)


_BUTTON_NAMES = {"home", "lock", "side", "siri"}


def press_button(udid: str, button: str) -> None:
    if button.lower() not in _BUTTON_NAMES:
        raise ValueError(f"unknown button {button!r}; supported: {sorted(_BUTTON_NAMES)}")
    _run([udid, "button", button.lower()])


def press_key(udid: str, hid_usage_code: int) -> None:
    _run([udid, "key", str(int(hid_usage_code))])


def chord(udid: str, modifier: str, key: str) -> None:
    """Send a modifier-key chord (e.g. Cmd-V for paste)."""
    _run([udid, "chord", modifier, key])

