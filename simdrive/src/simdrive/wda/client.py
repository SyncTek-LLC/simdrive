"""WDA HTTP client.

Thin wrapper around the WebDriverAgent REST API. All network I/O goes through
httpx (sync client) so tests can mock at the transport level.

WDA REST reference (Appium WDA fork):
  POST   /session                                → open session
  DELETE /session/<id>                           → close session
  GET    /status                                 → server status
  POST   /session/<id>/wda/tap                   → tap
  POST   /session/<id>/wda/dragfromtoforduration → swipe/drag
  POST   /session/<id>/wda/keys                  → type text
  POST   /session/<id>/wda/pressButton           → hardware button
  GET    /session/<id>/screenshot                → PNG screenshot (base64)
"""
from __future__ import annotations

import base64
import time
from typing import Any, Optional

import httpx

from .errors import wda_session_lost, wda_smoke_failed
from ..errors import SimdriveError


# Runtime error for HTTP-level failures (non-2xx or network error).
def _wda_http_error(method: str, url: str, status: int, body: str) -> SimdriveError:
    return SimdriveError(
        code="wda_http_error",
        message=(
            f"WDA {method} {url} returned HTTP {status}. "
            f"Body: {body[:300]}. "
            "Recovery: verify WDA is still running on the device and the tunnel is alive."
        ),
        details={"method": method, "url": url, "status": status, "body": body},
    )


def _wda_unreachable(host: str, port: int, exc: str) -> SimdriveError:
    return SimdriveError(
        code="wda_unreachable",
        message=(
            f"Cannot reach WDA at {host}:{port}. "
            f"Network error: {exc}. "
            "Recovery: confirm the device is connected and the CoreDevice tunnel is up "
            "(`xcrun devicectl device info details --device <udid>`), "
            "then retry. Run `simdrive bootstrap-device <udid>` to restart WDA."
        ),
        details={"host": host, "port": port, "exc": exc},
    )


class WdaClient:
    """HTTP client for a running WebDriverAgent instance.

    Lifetime: one WdaClient per WDA host:port pair. Call open_session() to
    get a WDA session_id before issuing any action calls. delete_session()
    when done.
    """

    def __init__(self, host: str, port: int, timeout: float = 30.0) -> None:
        self._host = host
        self._port = port
        self._base = f"http://{host}:{port}"
        self._session_id: Optional[str] = None
        self._last_seen_at: float = time.time()
        # httpx transport is injectable for unit tests (httpx.MockTransport).
        self._client = httpx.Client(base_url=self._base, timeout=timeout)

    # Allow injection of a custom transport (used by tests).
    def _replace_transport(self, transport: httpx.BaseTransport) -> None:
        self._client = httpx.Client(
            base_url=self._base,
            timeout=self._client.timeout,
            transport=transport,
        )

    # ── internal helpers ─────────────────────────────────────────────────────

    def _request(self, method: str, path: str, **kwargs: Any) -> dict:
        url = path
        try:
            resp = self._client.request(method, url, **kwargs)
        except httpx.TransportError as exc:
            raise _wda_unreachable(self._host, self._port, str(exc)) from exc
        self._last_seen_at = time.time()
        if not resp.is_success:
            raise _wda_http_error(method, url, resp.status_code, resp.text)
        try:
            return resp.json()
        except Exception:
            return {}

    def _session_path(self, tail: str = "") -> str:
        if not self._session_id:
            raise SimdriveError(
                code="wda_session_not_open",
                message=(
                    "No WDA session is open. "
                    "Recovery: call WdaClient.open_session(bundle_id) before issuing actions."
                ),
                details={},
            )
        return f"/session/{self._session_id}{tail}"

    # ── public surface ────────────────────────────────────────────────────────

    def status(self) -> dict:
        """GET /status → raw WDA status dict."""
        return self._request("GET", "/status")

    def open_session(self, bundle_id: str) -> str:
        """POST /session → WDA session_id.

        WDA accepts an XCUITest capabilities dict; the only required field is
        bundleId. Returns the string session_id for subsequent calls.
        """
        body = {
            "capabilities": {
                "alwaysMatch": {
                    "bundleId": bundle_id,
                    "shouldWaitForQuiescence": False,
                }
            }
        }
        resp = self._request("POST", "/session", json=body)
        # WDA wraps everything in {value: {sessionId: ...}}.
        sid = (resp.get("value") or {}).get("sessionId") or resp.get("sessionId")
        if not sid:
            raise SimdriveError(
                code="wda_session_open_failed",
                message=(
                    f"WDA POST /session did not return a sessionId. "
                    f"Response: {str(resp)[:300]}. "
                    "Recovery: confirm the bundle_id is correct and the app is installed on the device."
                ),
                details={"response": resp},
            )
        self._session_id = str(sid)
        return self._session_id

    def tap(self, x: float, y: float) -> None:
        """POST /session/<id>/wda/tap — tap at logical device-point coordinates."""
        self._request(
            "POST",
            self._session_path("/wda/tap"),
            json={"x": x, "y": y},
        )

    def swipe(
        self,
        from_x: float,
        from_y: float,
        to_x: float,
        to_y: float,
        duration_ms: int = 300,
    ) -> None:
        """POST /session/<id>/wda/dragfromtoforduration."""
        self._request(
            "POST",
            self._session_path("/wda/dragfromtoforduration"),
            json={
                "fromX": from_x,
                "fromY": from_y,
                "toX": to_x,
                "toY": to_y,
                "duration": duration_ms / 1000.0,
            },
        )

    def type_text(self, text: str) -> None:
        """POST /session/<id>/wda/keys — inject text into the focused element."""
        self._request(
            "POST",
            self._session_path("/wda/keys"),
            json={"value": list(text)},
        )

    def press_key(self, name: str) -> None:
        """POST /session/<id>/wda/pressButton — press a hardware button.

        Valid names: home, volumeUp, volumeDown, power (maps to lock/wake).
        """
        _WDA_BUTTON_MAP = {
            "home": "home",
            "volumeup": "volumeUp",
            "volumedown": "volumeDown",
            "power": "power",
            "lock": "power",  # common alias
        }
        mapped = _WDA_BUTTON_MAP.get(name.lower())
        if mapped is None:
            raise SimdriveError(
                code="wda_unknown_button",
                message=(
                    f"Unknown WDA button {name!r}. "
                    f"Supported: {sorted(_WDA_BUTTON_MAP)}. "
                    "Recovery: use one of the supported button names."
                ),
                details={"name": name},
            )
        self._request(
            "POST",
            self._session_path("/wda/pressButton"),
            json={"name": mapped},
        )

    def clear_field(self) -> None:
        """Clear the active (focused) text field.

        WDA strategy: find the active element, call clearText on it. This is
        the most reliable cross-version approach for WDA.
        """
        # Find the active (focused) element first.
        resp = self._request("GET", self._session_path("/element/active"))
        element_id = (
            (resp.get("value") or {}).get("ELEMENT")
            or (resp.get("value") or {}).get("element-6066-11e4-a52e-4f735466cecf")
        )
        if not element_id:
            # Fallback: send a backspace sequence via wda/keys if no focused element found.
            # This handles edge cases where iOS hasn't surfaced an active element yet.
            self._request(
                "POST",
                self._session_path("/wda/keys"),
                json={"value": [""] * 50},  # Delete key 50×
            )
            return
        self._request("POST", f"/session/{self._session_id}/element/{element_id}/clear")

    def screenshot(self) -> bytes:
        """GET /session/<id>/screenshot → raw PNG bytes."""
        resp = self._request("GET", self._session_path("/screenshot"))
        # WDA returns {value: "<base64-encoded-png>"}
        b64 = (resp.get("value") or "")
        return base64.b64decode(b64)

    def delete_session(self) -> None:
        """DELETE /session/<id> — close the WDA session."""
        if not self._session_id:
            return
        try:
            self._request("DELETE", self._session_path())
        except SimdriveError:
            # Best-effort; swallow errors on teardown.
            pass
        self._session_id = None

    def check_alive(self, udid: str) -> None:
        """Verify WDA is still reachable. Raises wda_session_lost if not."""
        try:
            self.status()
        except SimdriveError:
            raise wda_session_lost(udid, last_seen_at=self._last_seen_at)

    def close(self) -> None:
        """Close the underlying httpx client. Call when done with this WdaClient."""
        self._client.close()
