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

Auto-recovery (a12):
  Code 41 (XCTDaemonErrorDomain) — entitlement revoked mid-session:
    Detected in _request on any non-2xx response body containing both
    "XCTDaemonErrorDomain" and ("Code=41" or "Code 41"). If
    SIMDRIVE_NO_AUTO_REBUILD=1 is set, raises wda_ui_automation_disabled
    immediately. Otherwise calls bootstrap.bootstrap_device(..., rebuild=True),
    reloads the registry, updates host/port/session, and retries the original
    request once (_recovery_attempt kwarg prevents infinite loops).

  Orphan-session 404 — session deleted out-of-band:
    Detected in _request on HTTP 404 whose URL path matches the currently-
    stored session_id. If SIMDRIVE_NO_AUTO_REBUILD=1, raises original error.
    Otherwise calls open_session(_last_bundle_id) to get a fresh session_id
    and retries once. Same _recovery_attempt counter as Code 41.
"""
from __future__ import annotations

import base64
import logging
import os
import re
import time
from typing import Any, Optional

import httpx

from .errors import wda_session_lost, wda_ui_automation_disabled
from ..errors import SimdriveError

_LOG = logging.getLogger("simdrive.wda.client")

# Regex that matches both WDA Code=41 and Code 41 forms within an error body
# that also contains "XCTDaemonErrorDomain".
_CODE41_RE = re.compile(r"Code[= ]41")


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

    Auto-recovery fields (a12):
      _udid           — CoreDevice UUID for the device this client serves.
                        Required for Code-41 rebuild. Set by callers after
                        construction (session.py sets it before open_session).
      _last_bundle_id — Most-recently opened app bundle_id. Persisted on
                        open_session() so orphan-404 recovery can re-open
                        the same app without caller intervention.
    """

    def __init__(self, host: str, port: int, timeout: float = 30.0) -> None:
        self._host = host
        self._port = port
        self._base = f"http://{host}:{port}"
        self._session_id: Optional[str] = None
        self._last_seen_at: float = time.time()
        self._window_size_cache: Optional[tuple[int, int]] = None  # cached (w_pts, h_pts) for F-006
        # httpx transport is injectable for unit tests (httpx.MockTransport).
        self._client = httpx.Client(base_url=self._base, timeout=timeout)
        # a12 auto-recovery state.
        self._udid: Optional[str] = None           # set by caller after construction
        self._last_bundle_id: Optional[str] = None  # set by open_session()

    # Allow injection of a custom transport (used by tests).
    def _replace_transport(self, transport: httpx.BaseTransport) -> None:
        self._client = httpx.Client(
            base_url=self._base,
            timeout=self._client.timeout,
            transport=transport,
        )

    # ── internal helpers ─────────────────────────────────────────────────────

    def _request(self, method: str, path: str, _recovery_attempt: int = 0, **kwargs: Any) -> dict:
        """Issue an HTTP request to WDA, with auto-recovery on:
          - XCTDaemonErrorDomain Code=41 / Code 41 (entitlement lost → rebuild)
          - HTTP 404 on a session-scoped path (orphan session → re-acquire)

        _recovery_attempt is incremented on each auto-retry; max 1 retry per
        call. This is a per-call counter (not global), so cascading retries are
        impossible.
        """
        url = path
        try:
            resp = self._client.request(method, url, **kwargs)
        except httpx.TransportError as exc:
            raise _wda_unreachable(self._host, self._port, str(exc)) from exc
        self._last_seen_at = time.time()

        if not resp.is_success:
            raw_body = resp.text

            # ── Code-41 auto-recovery (XCTDaemonErrorDomain entitlement loss) ──
            if (
                _recovery_attempt == 0
                and "XCTDaemonErrorDomain" in raw_body
                and _CODE41_RE.search(raw_body)
            ):
                _LOG.warning(
                    "[simdrive] WDA UI Automation entitlement was revoked mid-session"
                    " — auto-rebuilding"
                    " (set SIMDRIVE_NO_AUTO_REBUILD=1 to opt out and handle manually)"
                )
                if os.environ.get("SIMDRIVE_NO_AUTO_REBUILD"):
                    raise wda_ui_automation_disabled(self._udid or f"{self._host}:{self._port}")
                # Attempt rebuild + re-acquire session.
                self._rebuild_and_reopen()
                # Retry the original request once.
                return self._request(method, path, _recovery_attempt=1, **kwargs)

            # ── Orphan-session 404 auto-recovery ─────────────────────────────
            if (
                _recovery_attempt == 0
                and resp.status_code == 404
                and self._session_id
                and f"/session/{self._session_id}" in path
            ):
                bundle_label = repr(self._last_bundle_id) if self._last_bundle_id else "<none>"
                _LOG.warning(
                    "[simdrive] WDA session was deleted out-of-band"
                    " — re-acquiring on bundle %s"
                    " (set SIMDRIVE_NO_AUTO_REBUILD=1 to opt out and handle manually)",
                    bundle_label,
                )
                if os.environ.get("SIMDRIVE_NO_AUTO_REBUILD"):
                    raise _wda_http_error(method, url, resp.status_code, raw_body)
                # Re-open WDA session on the same bundle.
                new_sid = self.open_session(self._last_bundle_id)
                # Substitute the new session id into the path and retry once.
                new_path = path.replace(
                    f"/session/{self._session_id}", f"/session/{new_sid}", 1
                )
                return self._request(method, new_path, _recovery_attempt=1, **kwargs)

            raise _wda_http_error(method, url, resp.status_code, raw_body)

        try:
            return resp.json()
        except Exception:
            return {}

    def _rebuild_and_reopen(self) -> None:
        """Trigger a full WDA rebuild for self._udid, reload registry, and
        re-open a WDA session on self._last_bundle_id.

        Called automatically by _request when Code-41 is detected.
        Raises SimdriveError if no udid is stored (safety guard) or if the
        rebuild itself fails.
        """
        if not self._udid:
            raise SimdriveError(
                code="wda_auto_rebuild_no_udid",
                message=(
                    "Cannot auto-rebuild WDA: no UDID is bound to this WdaClient. "
                    "Recovery: set client._udid = '<coredevice-uuid>' after construction, "
                    "or set SIMDRIVE_NO_AUTO_REBUILD=1 to disable auto-rebuild."
                ),
                details={},
            )

        from . import registry as _registry
        from . import bootstrap as _bootstrap

        # Load team_id from existing registry so we can pass it to bootstrap.
        entry = _registry.load(self._udid)
        team_id: Optional[str] = (entry or {}).get("team_id") or None

        _LOG.warning(
            "[simdrive] Running bootstrap-device --rebuild for udid=%s team_id=%s",
            self._udid,
            team_id or "(auto-detect)",
        )
        _bootstrap.bootstrap_device(self._udid, team_id=team_id, rebuild=True)

        # Reload updated registry and wire the new host/port into this client.
        new_entry = _registry.load(self._udid)
        if not new_entry:
            raise SimdriveError(
                code="wda_auto_rebuild_registry_missing",
                message=(
                    f"bootstrap_device succeeded for {self._udid} but the registry "
                    "entry is missing after rebuild. "
                    "Recovery: run `simdrive bootstrap-device <udid>` manually."
                ),
                details={"udid": self._udid},
            )

        new_host = new_entry.get("host") or new_entry.get("ip") or self._host
        new_port = int(new_entry.get("port") or self._port)
        self._host = new_host
        self._port = new_port
        self._base = f"http://{new_host}:{new_port}"
        self._window_size_cache = None  # invalidate cached window size
        # Rebuild the httpx client to point at the new endpoint.
        old_timeout = self._client.timeout
        self._client.close()
        self._client = httpx.Client(base_url=self._base, timeout=old_timeout)

        # Re-open WDA session on the same bundle the caller was using.
        self.open_session(self._last_bundle_id)

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

    def open_session(self, bundle_id: Optional[str]) -> str:
        """POST /session → WDA session_id.

        WDA accepts an XCUITest capabilities dict. When ``bundle_id`` is
        provided, the session is scoped to that app. When ``bundle_id`` is
        None, no bundleId capability is sent — WDA returns a sessionId that
        lets callers tap/swipe at the home screen / current foreground app.

        a12: stores bundle_id in self._last_bundle_id so auto-recovery can
        re-open the same session after Code-41 rebuild or orphan-404.
        """
        always: dict[str, Any] = {"shouldWaitForQuiescence": False}
        if bundle_id:
            always["bundleId"] = bundle_id
        body = {"capabilities": {"alwaysMatch": always}}
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
        self._last_bundle_id = bundle_id   # persist for a12 auto-recovery
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
                json={"value": [""] * 50},  # Delete key 50×
            )
            return
        self._request("POST", f"/session/{self._session_id}/element/{element_id}/clear")

    def screenshot(self) -> bytes:
        """GET /session/<id>/screenshot → raw PNG bytes."""
        resp = self._request("GET", self._session_path("/screenshot"))
        # WDA returns {value: "<base64-encoded-png>"}
        b64 = (resp.get("value") or "")
        return base64.b64decode(b64)

    def screenshot_any(self) -> bytes:
        """GET /screenshot → raw PNG bytes (no open session required).

        WDA exposes a top-level /screenshot route that works without a
        session.  Used by tool_observe on target=device paths where we
        need a screenshot but have not (and need not) open an app session.
        """
        resp = self._request("GET", "/screenshot")
        b64 = (resp.get("value") or "")
        return base64.b64decode(b64)

    def window_size_points(self) -> tuple[int, int]:
        """GET /session/<id>/window/size -> (width_pts, height_pts) in logical points.

        WDA returns the screen dimensions in the same coordinate space as
        XCUIScreen.main -- logical points, not pixels. On a 3x device the pixel
        screenshot is 3x wider/taller than this value.

        Cached on the client after the first call so we never hit the network more
        than once per WDA session (window size is stable for the session lifetime).
        Requires an open WDA session (raises wda_session_not_open if none is open).
        """
        if self._window_size_cache is not None:
            return self._window_size_cache
        resp = self._request("GET", self._session_path("/window/size"))
        # WDA returns {value: {width: N, height: N}} (WebDriver spec).
        value = resp.get("value") or {}
        w = int(value.get("width", 0))
        h = int(value.get("height", 0))
        self._window_size_cache = (w, h)
        return self._window_size_cache

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

    def source(self) -> str:
        """GET /session/<id>/source -> XCUI accessibility tree as UTF-8 XML.

        The WDA response is {value: '<xml ...>'}. We return the inner string
        for xml.etree.ElementTree consumption by som_device.annotate_device_screenshot.
        """
        resp = self._request("GET", self._session_path("/source"))
        xml_str = (resp.get("value") or "")
        return str(xml_str)

    def close(self) -> None:
        """Close the underlying httpx client. Call when done with this WdaClient."""
        self._client.close()
