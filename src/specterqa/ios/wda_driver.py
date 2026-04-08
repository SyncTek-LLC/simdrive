"""WDA-backed iOS Simulator driver.

Uses Appium's WebDriverAgent for touch injection — the same approach used by
every production iOS testing tool (Appium, Maestro, Detox).

WDA runs inside the simulator as an XCTest bundle and exposes an HTTP API
on port 8100.  Touch coordinates are in device logical points — no window
detection, no title bars, no coordinate mapping complexity.

Screenshot capture uses xcrun simctl (faster and more reliable than WDA
screenshots).

Coordinate conversion:
    Claude receives a resized screenshot (default 1024 px wide).
    ``_img_to_device`` scales those pixel coordinates to device logical points
    (e.g. 393×852 for iPhone 16 Pro) using the window-size reported by WDA
    after session creation.

Usage::

    driver = WDADriver(udid="booted", verbose=True)
    driver.create_session("com.example.MyApp")
    b64, w, h = driver.screenshot()
    driver.tap(196, 400)   # screenshot-pixel coords

INIT-2026-493 — SpecterQA WDA touch backend.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from typing import Any, Tuple, Optional

from PIL import Image

logger = logging.getLogger("specterqa.ios.wda_driver")

WDA_BASE = "http://localhost:8100"
_DEFAULT_TIMEOUT = 10  # seconds for regular requests
_STATUS_TIMEOUT = 3  # seconds for quick availability probe


class WDAError(Exception):
    """Raised when a WDA operation fails."""


class WDADriver:
    """Controls the iOS Simulator via WebDriverAgent's HTTP API.

    WDA exposes a W3C WebDriver-compatible endpoint on port 8100.  All touch
    input is delivered as W3C Actions (``pointer`` type, ``touch``
    pointerType), so coordinates are native device logical points — the same
    coordinate space the iOS app uses.  No window detection, no title-bar
    offsets, no macOS screen coordinate mapping.

    Screenshot capture delegates to ``xcrun simctl io … screenshot`` which is
    faster and more reliable than WDA's own screenshot endpoint.

    Args:
        udid: Simulator UDID or ``"booted"`` to target the currently-booted
            device.
        wda_url: WDA base URL.  Defaults to ``http://localhost:8100``.
        verbose: When ``True``, print debug lines prefixed with ``[wda]``.
    """

    def __init__(
        self,
        udid: str = "booted",
        wda_url: str = WDA_BASE,
        verbose: bool = False,
    ) -> None:
        self.udid = udid
        self.wda_url = wda_url.rstrip("/")
        self.verbose = verbose
        self._session_id: Optional[str] = None
        # Logical-point dimensions reported by WDA after session creation.
        self._device_width: float = 393.0
        self._device_height: float = 852.0
        # Screenshot pixel dimensions (updated on every screenshot() call).
        self._display_width: int = 1024
        self._display_height: int = 2226
        self._screenshot_dir: str = tempfile.mkdtemp(prefix="specterqa_wda_")

    # ------------------------------------------------------------------
    # Class-level availability probe
    # ------------------------------------------------------------------

    @classmethod
    def is_available(cls, wda_url: str = WDA_BASE) -> bool:
        """Return ``True`` when WDA is running and reports ``ready: true``.

        Sends ``GET /status`` and inspects the ``value.ready`` field.
        Any network error (refused connection, timeout) → ``False``.

        Args:
            wda_url: WDA base URL to probe (defaults to ``http://localhost:8100``).
        """
        url = f"{wda_url.rstrip('/')}/status"
        try:
            with urllib.request.urlopen(url, timeout=_STATUS_TIMEOUT) as resp:
                data = json.loads(resp.read())
                return bool(data.get("value", {}).get("ready", False))
        except Exception:
            return False

    # ------------------------------------------------------------------
    # Low-level HTTP helpers
    # ------------------------------------------------------------------

    def _request(
        self,
        method: str,
        path: str,
        body: Optional[dict[str, Any]] = None,
        timeout: int = _DEFAULT_TIMEOUT,
    ) -> dict[str, Any]:
        """Send an HTTP request to WDA and return parsed JSON.

        Args:
            method: HTTP verb (``"GET"``, ``"POST"``, ``"DELETE"``).
            path: URL path (e.g. ``"/session"``).
            body: Optional dict serialised as JSON request body.
            timeout: Request timeout in seconds.

        Returns:
            Parsed JSON response dict.

        Raises:
            WDAError: On connection failures or HTTP errors.
        """
        url = f"{self.wda_url}{path}"
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(url, data=data, method=method)
        if data is not None:
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read()
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as exc:
            raise WDAError(f"WDA HTTP {exc.code} on {method} {path}: {exc.reason}") from exc
        except urllib.error.URLError as exc:
            raise WDAError(f"WDA connection failed on {method} {path}: {exc.reason}") from exc
        except Exception as exc:
            raise WDAError(f"WDA request error on {method} {path}: {exc}") from exc

    # ------------------------------------------------------------------
    # Session management
    # ------------------------------------------------------------------

    def create_session(self, bundle_id: str = "com.apple.Preferences") -> str:
        """Create a WDA session for the given app.

        Also fetches the window size so that ``_img_to_device`` has accurate
        logical-point dimensions for coordinate conversion.

        Args:
            bundle_id: iOS bundle identifier of the app under test.  WDA will
                activate (bring to foreground) the app if it is already running,
                or launch it if it is not.

        Returns:
            The WDA session ID string.

        Raises:
            WDAError: If WDA rejects the session creation request.
        """
        body: dict[str, Any] = {
            "capabilities": {
                "alwaysMatch": {
                    "bundleId": bundle_id,
                }
            }
        }
        resp = self._request("POST", "/session", body, timeout=30)
        session_id = resp.get("sessionId") or resp.get("value", {}).get("sessionId")
        if not session_id:
            raise WDAError(f"WDA did not return a sessionId in response: {resp!r}")
        self._session_id = session_id

        # Fetch window size for coordinate conversion.
        try:
            size_resp = self._request("GET", f"/session/{self._session_id}/window/size")
            dims = size_resp.get("value", {})
            self._device_width = float(dims.get("width") or 393)
            self._device_height = float(dims.get("height") or 852)
        except WDAError:
            # Non-fatal: fall back to iPhone 16 Pro defaults.
            self._device_width = 393.0
            self._device_height = 852.0

        if self.verbose:
            logger.debug(
                "  [wda] session=%r  device=%.0fx%.0f pts",
                self._session_id, self._device_width, self._device_height,
            )
        return self._session_id

    def _require_session(self) -> str:
        """Return the active session ID, raising ``WDAError`` if none exists."""
        if not self._session_id:
            raise WDAError("No active WDA session. Call create_session(bundle_id) first.")
        return self._session_id

    # ------------------------------------------------------------------
    # Coordinate conversion
    # ------------------------------------------------------------------

    def _img_to_device(self, img_x: float, img_y: float) -> Tuple[float, float]:
        """Convert screenshot-pixel coordinates to device logical points.

        Claude operates in the resized-screenshot coordinate space (e.g. 1024
        px wide).  WDA expects device logical points (e.g. 393 pts wide for an
        iPhone 16 Pro).  This method scales linearly between the two spaces.

        Args:
            img_x: Horizontal coordinate in screenshot-pixel space.
            img_y: Vertical coordinate in screenshot-pixel space.

        Returns:
            ``(dev_x, dev_y)`` — device logical-point coordinates.
        """
        dev_x = img_x * (self._device_width / self._display_width)
        dev_y = img_y * (self._device_height / self._display_height)
        return dev_x, dev_y

    # ------------------------------------------------------------------
    # Touch input via WDA W3C Actions API
    # ------------------------------------------------------------------

    def tap(self, img_x: float, img_y: float) -> None:
        """Tap at screenshot-pixel coordinates.

        Converts to device logical points and delivers a W3C pointer action
        sequence (move → down → pause → up) via WDA.

        Args:
            img_x: Horizontal coordinate in screenshot-pixel space.
            img_y: Vertical coordinate in screenshot-pixel space.
        """
        session = self._require_session()
        dev_x, dev_y = self._img_to_device(img_x, img_y)
        if self.verbose:
            logger.debug("  [wda] tap  img=(%.0f,%.0f)  dev=(%.1f,%.1f)", img_x, img_y, dev_x, dev_y)
        body: dict[str, Any] = {
            "actions": [
                {
                    "type": "pointer",
                    "id": "finger1",
                    "parameters": {"pointerType": "touch"},
                    "actions": [
                        {"type": "pointerMove", "duration": 0, "x": int(dev_x), "y": int(dev_y)},
                        {"type": "pointerDown", "button": 0},
                        {"type": "pause", "duration": 100},
                        {"type": "pointerUp", "button": 0},
                    ],
                }
            ]
        }
        self._request("POST", f"/session/{session}/actions", body)
        time.sleep(0.3)

    def double_tap(self, img_x: float, img_y: float) -> None:
        """Double-tap at screenshot-pixel coordinates.

        Delivers two tap sequences back-to-back with a short pause between them.

        Args:
            img_x: Horizontal coordinate in screenshot-pixel space.
            img_y: Vertical coordinate in screenshot-pixel space.
        """
        session = self._require_session()
        dev_x, dev_y = self._img_to_device(img_x, img_y)
        if self.verbose:
            logger.debug("  [wda] double_tap  dev=(%.1f,%.1f)", dev_x, dev_y)
        ix, iy = int(dev_x), int(dev_y)
        body: dict[str, Any] = {
            "actions": [
                {
                    "type": "pointer",
                    "id": "finger1",
                    "parameters": {"pointerType": "touch"},
                    "actions": [
                        {"type": "pointerMove", "duration": 0, "x": ix, "y": iy},
                        {"type": "pointerDown", "button": 0},
                        {"type": "pause", "duration": 80},
                        {"type": "pointerUp", "button": 0},
                        {"type": "pause", "duration": 100},
                        {"type": "pointerDown", "button": 0},
                        {"type": "pause", "duration": 80},
                        {"type": "pointerUp", "button": 0},
                    ],
                }
            ]
        }
        self._request("POST", f"/session/{session}/actions", body)
        time.sleep(0.3)

    def long_press(self, img_x: float, img_y: float, duration: float = 1.5) -> None:
        """Long-press at screenshot-pixel coordinates.

        Holds the pointer down for *duration* milliseconds (minimum 500 ms to
        reliably trigger iOS long-press context menus).

        Args:
            img_x: Horizontal coordinate in screenshot-pixel space.
            img_y: Vertical coordinate in screenshot-pixel space.
            duration: Hold duration in seconds (converted to ms for WDA).
        """
        session = self._require_session()
        dev_x, dev_y = self._img_to_device(img_x, img_y)
        hold_ms = max(500, int(duration * 1000))
        if self.verbose:
            logger.debug("  [wda] long_press  dev=(%.1f,%.1f)  %dms", dev_x, dev_y, hold_ms)
        body: dict[str, Any] = {
            "actions": [
                {
                    "type": "pointer",
                    "id": "finger1",
                    "parameters": {"pointerType": "touch"},
                    "actions": [
                        {"type": "pointerMove", "duration": 0, "x": int(dev_x), "y": int(dev_y)},
                        {"type": "pointerDown", "button": 0},
                        {"type": "pause", "duration": hold_ms},
                        {"type": "pointerUp", "button": 0},
                    ],
                }
            ]
        }
        self._request("POST", f"/session/{session}/actions", body)
        time.sleep(0.5)

    def swipe(
        self,
        x1: float,
        y1: float,
        x2: float,
        y2: float,
        duration: float = 0.4,
    ) -> None:
        """Swipe from (x1, y1) to (x2, y2) in screenshot-pixel coordinates.

        Args:
            x1: Start horizontal coordinate (screenshot pixels).
            y1: Start vertical coordinate (screenshot pixels).
            x2: End horizontal coordinate (screenshot pixels).
            y2: End vertical coordinate (screenshot pixels).
            duration: Gesture duration in seconds.
        """
        session = self._require_session()
        dx1, dy1 = self._img_to_device(x1, y1)
        dx2, dy2 = self._img_to_device(x2, y2)
        if self.verbose:
            logger.debug("  [wda] swipe  (%.0f,%.0f) -> (%.0f,%.0f)  %.2fs", dx1, dy1, dx2, dy2, duration)
        body: dict[str, Any] = {
            "actions": [
                {
                    "type": "pointer",
                    "id": "finger1",
                    "parameters": {"pointerType": "touch"},
                    "actions": [
                        {"type": "pointerMove", "duration": 0, "x": int(dx1), "y": int(dy1)},
                        {"type": "pointerDown", "button": 0},
                        {
                            "type": "pointerMove",
                            "duration": int(duration * 1000),
                            "x": int(dx2),
                            "y": int(dy2),
                        },
                        {"type": "pointerUp", "button": 0},
                    ],
                }
            ]
        }
        self._request("POST", f"/session/{session}/actions", body)
        time.sleep(0.3)

    def swipe_back(self) -> None:
        """iOS edge-swipe back gesture (left-edge → center)."""
        self.swipe(
            5,
            self._display_height // 2,
            self._display_width // 2,
            self._display_height // 2,
            duration=0.3,
        )

    # ------------------------------------------------------------------
    # Keyboard input
    # ------------------------------------------------------------------

    def type_text(self, text: str) -> None:
        """Type *text* into the currently-focused field.

        Tries ``xcrun simctl io … keyboard input`` first (most reliable for
        Unicode and multi-character strings).  Falls back to WDA ``/keys`` if
        simctl is unavailable or returns a non-zero exit code.

        Args:
            text: String to type.
        """
        result = subprocess.run(
            ["xcrun", "simctl", "io", self.udid, "keyboard", "input", text],
            capture_output=True,
        )
        if result.returncode != 0:
            if self.verbose:
                logger.debug("  [wda] simctl keyboard failed — falling back to WDA /keys")
            session = self._require_session()
            body: dict[str, Any] = {"value": list(text)}
            self._request("POST", f"/session/{session}/keys", body)
        time.sleep(0.2)

    def press_key(self, key: str) -> None:
        """Press a named key.

        Attempts ``xcrun simctl`` first for special keys; falls back to WDA
        ``/keys`` for others.

        Args:
            key: Key name string (e.g. ``"return"``, ``"escape"``, ``"delete"``).
        """
        # simctl supports a limited set; try it first.
        result = subprocess.run(
            ["xcrun", "simctl", "io", self.udid, "keyboard", "input", key],
            capture_output=True,
        )
        if result.returncode != 0:
            session = self._require_session()
            body: dict[str, Any] = {"value": [key]}
            self._request("POST", f"/session/{session}/keys", body)
        time.sleep(0.1)

    # ------------------------------------------------------------------
    # Screenshot via simctl (faster than WDA screenshots)
    # ------------------------------------------------------------------

    def screenshot(self, resize_width: int = 1024) -> Tuple[str, int, int]:
        """Capture a screenshot via simctl, resize, and return base64.

        Uses ``xcrun simctl io … screenshot`` rather than WDA's own screenshot
        endpoint because simctl is faster and does not require an active WDA
        session.  The resized dimensions are stored in ``_display_width`` /
        ``_display_height`` for subsequent coordinate conversions.

        Args:
            resize_width: Target width in pixels.  The image is scaled
                proportionally.  Pass 0 or a value larger than the native width
                to skip resizing.

        Returns:
            Tuple of ``(base64_png_string, width_px, height_px)``.
        """
        path = os.path.join(self._screenshot_dir, f"shot_{int(time.time() * 1000)}.png")
        subprocess.run(
            ["xcrun", "simctl", "io", self.udid, "screenshot", "--type=png", path],
            capture_output=True,
            timeout=10,
        )
        img = Image.open(path)
        if resize_width and img.width > resize_width:
            ratio = resize_width / img.width
            img = img.resize((resize_width, int(img.height * ratio)), Image.LANCZOS)
        resized_path = path.replace(".png", "_r.png")
        img.save(resized_path, "PNG")

        with open(resized_path, "rb") as fh:
            b64 = base64.standard_b64encode(fh.read()).decode("ascii")

        self._display_width = img.width
        self._display_height = img.height
        return b64, img.width, img.height

    # ------------------------------------------------------------------
    # App lifecycle
    # ------------------------------------------------------------------

    def launch_app(self, bundle_id: str) -> None:
        """Launch (or bring to foreground) *bundle_id*.

        Tries WDA's ``/wda/apps/launch`` endpoint first; falls back to
        ``xcrun simctl launch`` if WDA rejects the request or the session is
        not initialised.

        Args:
            bundle_id: iOS bundle identifier (e.g. ``"com.example.MyApp"``).
        """
        launched = False
        if self._session_id:
            try:
                body: dict[str, Any] = {"bundleId": bundle_id}
                self._request(
                    "POST",
                    f"/session/{self._session_id}/wda/apps/launch",
                    body,
                )
                launched = True
            except WDAError as exc:
                logger.debug("WDA launch_app failed (using simctl fallback): %s", exc)
        if not launched:
            subprocess.run(
                ["xcrun", "simctl", "launch", self.udid, bundle_id],
                capture_output=True,
            )
        time.sleep(2)

    def terminate_app(self, bundle_id: str) -> None:
        """Terminate *bundle_id* via simctl.

        Args:
            bundle_id: iOS bundle identifier.
        """
        subprocess.run(
            ["xcrun", "simctl", "terminate", self.udid, bundle_id],
            capture_output=True,
        )
        time.sleep(0.5)

    # ------------------------------------------------------------------
    # Device info helper
    # ------------------------------------------------------------------

    def device_info(self) -> Optional[dict[str, Any]]:
        """Return the simctl device dict for the current UDID.

        Also resolves ``self.udid`` from ``"booted"`` to the actual UDID if a
        booted simulator is found.

        Returns:
            The simctl device dict (``name``, ``udid``, ``state``, …) or
            ``None`` if the device cannot be found.
        """
        result = subprocess.run(
            ["xcrun", "simctl", "list", "devices", "-j"],
            capture_output=True,
            text=True,
        )
        try:
            data = json.loads(result.stdout)
        except json.JSONDecodeError:
            return None
        for _runtime, devices in data.get("devices", {}).items():
            for dev in devices:
                if dev.get("state") == "Booted" and (self.udid == "booted" or dev["udid"] == self.udid):
                    if self.udid == "booted":
                        self.udid = dev["udid"]
                    return dev
        return None

    # ------------------------------------------------------------------
    # Unified execute — ActionExecutor protocol (SpecterQA compatibility)
    # ------------------------------------------------------------------

    def execute(self, action: dict[str, Any]) -> dict[str, Any]:
        """Execute a ``computer_use`` action dict.

        Implements the same interface as ``SimDriver.execute`` so that this
        driver is a drop-in replacement anywhere ``SimDriver`` is used.

        Supported action types:
            ``screenshot``, ``left_click`` / ``click``, ``double_click``,
            ``right_click`` / ``long_press``, ``type``, ``key``, ``scroll``,
            ``left_click_drag``, ``wait``.

        Args:
            action: Dict with at least an ``"action"`` key.

        Returns:
            Result dict — ``{"type": "image", …}`` for screenshots, or
            ``{"type": "text", "text": "…"}`` for all other actions.
        """
        action_type = action.get("action", "")

        if action_type == "screenshot":
            b64, w, h = self.screenshot()
            return {"type": "image", "base64": b64, "width": w, "height": h}

        elif action_type in ("left_click", "click"):
            coord = action.get("coordinate", [0, 0])
            self.tap(coord[0], coord[1])
            return {"type": "text", "text": f"Tapped at ({coord[0]}, {coord[1]})"}

        elif action_type == "double_click":
            coord = action.get("coordinate", [0, 0])
            self.double_tap(coord[0], coord[1])
            return {"type": "text", "text": f"Double-tapped at ({coord[0]}, {coord[1]})"}

        elif action_type in ("right_click", "long_press"):
            coord = action.get("coordinate", [0, 0])
            dur = action.get("duration", 1.5)
            self.long_press(coord[0], coord[1], duration=dur)
            return {"type": "text", "text": f"Long-pressed at ({coord[0]}, {coord[1]}) for {dur}s"}

        elif action_type == "type":
            text = action.get("text", "")
            self.type_text(text)
            return {"type": "text", "text": f"Typed: {text[:50]}"}

        elif action_type == "key":
            key = action.get("key", "")
            self.press_key(key)
            return {"type": "text", "text": f"Pressed key: {key}"}

        elif action_type == "scroll":
            coord = action.get(
                "coordinate",
                [self._display_width // 2, self._display_height // 2],
            )
            direction = action.get("direction", "down")
            amount = action.get("amount", 3)
            distance = amount * 100  # pixels in screenshot space
            x, y = coord[0], coord[1]
            if direction == "down":
                self.swipe(x, y, x, y - distance)
            elif direction == "up":
                self.swipe(x, y, x, y + distance)
            elif direction == "left":
                self.swipe(x, y, x - distance, y)
            elif direction == "right":
                self.swipe(x, y, x + distance, y)
            return {"type": "text", "text": f"Scrolled {direction} by {amount}"}

        elif action_type == "left_click_drag":
            start = action.get("start_coordinate", [0, 0])
            end = action.get("coordinate", [0, 0])
            self.swipe(start[0], start[1], end[0], end[1])
            return {"type": "text", "text": f"Dragged {start} → {end}"}

        elif action_type == "wait":
            secs = action.get("duration", 1)
            time.sleep(secs)
            return {"type": "text", "text": f"Waited {secs}s"}

        else:
            return {"type": "text", "text": f"Unknown action: {action_type}"}

    # ------------------------------------------------------------------
    # Dunder helpers
    # ------------------------------------------------------------------

    def __repr__(self) -> str:
        return f"WDADriver(udid={self.udid!r}, wda_url={self.wda_url!r}, session={self._session_id!r})"
