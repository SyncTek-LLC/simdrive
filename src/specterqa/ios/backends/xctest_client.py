"""XCTestBackend — Python HTTP client for the Swift XCTest runner.

Communicates with the XCTest HTTP server that runs inside the iOS Simulator
via a Swift XCUITest host process.  All I/O uses stdlib ``urllib`` — no
third-party dependencies.

Coordinate system: device logical points (e.g. 390×844 for iPhone 16 Pro).
The caller is responsible for any pixel→point conversion; this client
forwards coordinates as-is.

[internal-tracker] — SpecterQA iOS Headless Driver.
"""

from __future__ import annotations

import json
import logging
import socket
import urllib.error
import urllib.request
from typing import Any

logger = logging.getLogger("specterqa.ios.backends.xctest_client")

_DEFAULT_HOST = "localhost"
_DEFAULT_PORT = 8222
_DEFAULT_TIMEOUT = 10  # seconds — element-based tap+type needs time for focus + a11y settle


class XCTestBackend:
    """HTTP client that talks to the Swift XCTest runner.

    The runner exposes a lightweight HTTP server on ``http://<host>:<port>/``.
    All gesture commands are POST requests with a JSON body; status checks and
    screenshots use GET.

    Args:
        host: Hostname or IP where the XCTest runner is listening.
        port: Port number (default: 8222).
        udid: Simulator UDID (or ``"booted"``).  Stored for reference; the
            runner itself is already bound to a specific device.
    """

    def __init__(
        self,
        host: str = _DEFAULT_HOST,
        port: int = _DEFAULT_PORT,
        udid: str = "booted",
    ) -> None:
        self.host = host
        self.port = port
        self.udid = udid
        # SoM runner accesses these for coordinate conversion.
        # XCTest runner works in device points — no conversion needed,
        # so display and device dimensions are identical.
        self._device_width = 390.0
        self._device_height = 844.0
        self._display_width = 390
        self._display_height = 844

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _url(self, path: str) -> str:
        """Build a full URL for the given path."""
        return f"http://{self.host}:{self.port}{path}"

    def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Send a POST request with a JSON body.

        Returns:
            Parsed JSON response dict.

        Raises:
            ConnectionError: On socket timeout or refused connection.
            RuntimeError: On HTTP 5xx responses.
        """
        url = self._url(path)
        body = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        return self._send(req)

    def _get(self, path: str) -> dict[str, Any]:
        """Send a GET request and return the parsed JSON response.

        Raises:
            ConnectionError: On socket timeout or refused connection.
            RuntimeError: On HTTP 5xx responses.
        """
        # Pass the URL string directly so that the URL is visible in repr(call_args)
        # when tests inspect mock_open.call_args for port/path verification.
        url = self._url(path)
        return self._send_url(url)

    def _send(self, req: urllib.request.Request) -> dict[str, Any]:
        """Execute *req* and return parsed JSON, surfacing errors cleanly."""
        try:
            with urllib.request.urlopen(req, timeout=_DEFAULT_TIMEOUT) as resp:
                raw = resp.read()
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as exc:
            logger.warning("XCTestBackend HTTP error %d on %s", exc.code, req.full_url)
            return {"success": False, "error": str(exc), "status": exc.code}
        except urllib.error.URLError as exc:
            reason = exc.reason
            if isinstance(reason, socket.timeout) or isinstance(reason, TimeoutError):
                raise ConnectionError(f"XCTest runner timed out on {req.full_url}") from exc
            raise ConnectionError(f"XCTest runner unavailable at {self._url('')}: {reason}") from exc

    def _send_url(self, url: str) -> dict[str, Any]:
        """Execute a GET by passing the URL string directly to urlopen.

        Passing the raw URL string (rather than a Request object) ensures the
        URL is visible in ``str(mock.call_args)`` during testing, allowing
        port/path assertions to work reliably.
        """
        try:
            with urllib.request.urlopen(url, timeout=_DEFAULT_TIMEOUT) as resp:
                raw = resp.read()
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as exc:
            logger.warning("XCTestBackend HTTP error %d on %s", exc.code, url)
            return {"success": False, "error": str(exc), "status": exc.code}
        except urllib.error.URLError as exc:
            reason = exc.reason
            if isinstance(reason, socket.timeout) or isinstance(reason, TimeoutError):
                raise ConnectionError(f"XCTest runner timed out on {url}") from exc
            raise ConnectionError(f"XCTest runner unavailable at {self._url('')}: {reason}") from exc

    # ------------------------------------------------------------------
    # Availability check
    # ------------------------------------------------------------------

    def is_available(
        self,
        host: str | None = None,
        port: int | None = None,
    ) -> bool:
        """Return ``True`` when the XCTest runner is responding.

        Uses ``self.host`` / ``self.port`` by default.  Pass explicit *host*
        and *port* to probe a different address.

        Sends a ``GET /health`` and checks that ``{"status": "ok"}`` is returned.
        Any network error (refused, timeout, etc.) → ``False``.

        This is an instance method so that ``backend.is_available()`` naturally
        probes the same address the backend itself targets.  To probe from a
        class context without an existing instance, call::

            XCTestBackend(host=h, port=p).is_available()

        Args:
            host: Hostname override (defaults to ``self.host``).
            port: Port override (defaults to ``self.port``).

        Returns:
            ``True`` if the runner replies with ``status == "ok"``.
        """
        effective_host = host if host is not None else self.host
        effective_port = port if port is not None else self.port
        probe_url = f"http://{effective_host}:{effective_port}/health"
        try:
            with urllib.request.urlopen(probe_url, timeout=_DEFAULT_TIMEOUT) as resp:
                raw = resp.read()
                data = json.loads(raw) if raw else {}
                return data.get("status") == "ok"
        except (OSError, urllib.error.URLError, json.JSONDecodeError):
            return False

    def health(self) -> dict[str, Any]:
        """Check runner health."""
        return self._get("/health")

    def app_state(self) -> dict[str, Any]:
        """Check app lifecycle state (foreground, background, suspended).

        Returns:
            Runner response dict with app state information.
        """
        return self._get("/app_state")

    def wait_idle(self, timeout: float = 10.0) -> dict[str, Any]:
        """Wait for the app to become idle (element tree stabilizes).

        Args:
            timeout: Maximum wait in seconds (default 10.0).

        Returns:
            Runner response dict.
        """
        return self._post("/idle", {"timeout": timeout})

    def set_appearance(self, mode: str) -> dict[str, Any]:
        """Set the simulator appearance via the runner's /appearance endpoint.

        Args:
            mode: "dark" or "light".

        Returns:
            Runner response dict.
        """
        return self._post("/appearance", {"mode": mode})

    # ------------------------------------------------------------------
    # Gesture API
    # ------------------------------------------------------------------

    def tap(self, x: float, y: float, duration: float = 0.0) -> dict[str, Any]:
        """Tap at device-point coordinates.

        Args:
            x: Horizontal position in logical points.
            y: Vertical position in logical points.
            duration: Hold duration in seconds (default 0.0 = normal tap).
                      Use > 0.5 for a long-press gesture.

        Returns:
            Runner response dict (``{"success": True}`` on success).
        """
        logger.debug("tap(%.1f, %.1f, duration=%.2f)", x, y, duration)
        payload: dict[str, Any] = {"x": float(x), "y": float(y)}
        if duration > 0.0:
            payload["duration"] = float(duration)
        return self._post("/tap", payload)

    def tap_element(
        self,
        label: str | None = None,
        identifier: str | None = None,
        element_type: str | None = None,
    ) -> dict[str, Any]:
        """Tap an element by label or identifier using XCTest's element.tap().

        Unlike coordinate tap, this uses the XCTest accessibility API to find
        and tap the element, which reliably transfers first-responder focus
        even on SwiftUI SecureField inside List/Form cells.

        Returns:
            Runner response dict with ``mode: "element"``.
        """
        payload: dict[str, Any] = {}
        if label is not None:
            payload["label"] = label
        if identifier is not None:
            payload["identifier"] = identifier
        if element_type is not None:
            payload["type"] = element_type
        return self._post("/tap", payload)

    def swipe(
        self,
        x1: float,
        y1: float,
        x2: float,
        y2: float,
        duration: float = 0.3,
    ) -> dict[str, Any]:
        """Swipe from (x1, y1) to (x2, y2) in device-point coordinates.

        Args:
            x1: Start horizontal position in logical points.
            y1: Start vertical position in logical points.
            x2: End horizontal position in logical points.
            y2: End vertical position in logical points.
            duration: Gesture duration in seconds (default: 0.3).

        Returns:
            Runner response dict.
        """
        logger.debug("swipe(%.1f,%.1f → %.1f,%.1f) %.2fs", x1, y1, x2, y2, duration)
        return self._post(
            "/swipe",
            {
                "fromX": float(x1),
                "fromY": float(y1),
                "toX": float(x2),
                "toY": float(y2),
                "duration": float(duration),
            },
        )

    def swipe_back(self) -> dict[str, Any]:
        """Perform a swipe-from-left-edge gesture (iOS back navigation).

        Swipes from x=5 to x=200 at the vertical center of the screen.

        Returns:
            Runner response dict.
        """
        logger.debug("swipe_back()")
        return self.swipe(x1=5, y1=422, x2=200, y2=422, duration=0.3)

    def type_text(self, text: str) -> dict[str, Any]:
        """Type *text* into the currently focused field.

        Args:
            text: String to type.

        Returns:
            Runner response dict.
        """
        logger.debug("type_text(%r)", text)
        return self._post("/type", {"text": text})

    def press_key(self, key: str) -> dict[str, Any]:
        """Press a named key (e.g. ``"home"``, ``"back"``, ``"enter"``).

        Args:
            key: Key name string.

        Returns:
            Runner response dict.
        """
        logger.debug("press_key(%r)", key)
        return self._post("/key", {"key": key})

    def press_button(self, button: str) -> dict[str, Any]:
        """Press a hardware button (e.g. ``"home"``, ``"lock"``, ``"volumeUp"``).

        Args:
            button: Button identifier string.

        Returns:
            Runner response dict.
        """
        logger.debug("press_button(%r)", button)
        return self._post("/press_button", {"button": button})

    def source(self) -> dict[str, Any]:
        """Fetch the accessibility element tree from the runner.

        The runner returns a JSON object with an ``"xml"`` key containing the
        full accessibility hierarchy as XML, plus optional metadata.

        Returns:
            Dict with at least ``{"xml": "<AppElement ...>"}`` on success.
        """
        logger.debug("source()")
        return self._get("/source")

    def screenshot(self) -> dict[str, Any]:
        """Capture a screenshot.

        Returns:
            A dict containing at least one of: ``image``, ``data``, or
            ``base64`` — the base64-encoded PNG bytes.  Also includes
            ``width`` and ``height`` when provided by the runner.
        """
        logger.debug("screenshot()")
        result = self._get("/screenshot")
        # Update display dimensions from actual screenshot.
        if "width" in result and "height" in result:
            self._display_width = int(result["width"])
            self._display_height = int(result["height"])
            self._device_width = float(result["width"])
            self._device_height = float(result["height"])
        return result

    def webview(self) -> dict[str, Any]:
        """Fetch elements inside WKWebView content.

        Queries WKWebView descendants via the XCTest .webViews chain — the
        only way to see EPUB readers, PDF viewers, and other web content
        embedded in the app.

        Returns:
            Dict with ``success``, ``elements`` list, and ``count``.
        """
        logger.debug("webview()")
        return self._get("/webview")

    def shutdown(self) -> dict[str, Any]:
        """Gracefully shut down the XCTest runner process.

        Returns:
            Runner response dict (``{"ok": True}`` on success).
        """
        logger.debug("shutdown()")
        return self._post("/shutdown", {})

    # ------------------------------------------------------------------
    # IOSBackend Protocol shims
    # (thin wrappers so XCTestBackend satisfies the Protocol structurally)
    # ------------------------------------------------------------------

    def start(self, device_udid: str = "booted", bundle_id: str = "", **kwargs: Any) -> None:
        """No-op for XCTestBackend — the runner is started externally via TestSession."""
        self.udid = device_udid

    def stop(self) -> None:
        """Shut down the XCTest runner."""
        try:
            self.shutdown()
        except Exception:  # noqa: BLE001 — best-effort
            pass

    def get_elements(self, max_elements: int = 0) -> dict[str, Any]:
        """Fetch the element tree from the runner's /source endpoint.

        Returns a normalised dict compatible with the IOSBackend Protocol:
        ``{"elements": list, "count": int, "xml": str}``.
        """
        result = self.source()
        # The runner returns {"xml": "<AppElement …>"} — callers use the xml
        # key to build the element list via SoMAnnotator; this shim exposes
        # both so Protocol consumers that expect "elements" still work.
        elements: list = result.get("elements", [])
        return {
            "elements": elements,
            "count": len(elements),
            "xml": result.get("xml", ""),
        }

    def find_element(self, **criteria: Any) -> dict[str, Any] | None:
        """Search for an element by label or identifier via the runner.

        Returns the first match from the accessibility tree, or ``None``.
        """
        label = criteria.get("label")
        identifier = criteria.get("identifier")
        if not label and not identifier:
            return None
        payload: dict[str, Any] = {}
        if label:
            payload["label"] = label
        if identifier:
            payload["identifier"] = identifier
        result = self._post("/find", payload)
        if result.get("success") and result.get("element"):
            return result["element"]
        return None

    # ------------------------------------------------------------------
    # Dunder helpers
    # ------------------------------------------------------------------

    def __repr__(self) -> str:
        return f"XCTestBackend(host={self.host!r}, port={self.port}, udid={self.udid!r})"
