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
    "XCTDaemonErrorDomain" and ("Code=41" or "Code 41") (and not "Code=410"
    or any other digit-extended form). If SIMDRIVE_NO_AUTO_REBUILD=1 is set,
    raises wda_ui_automation_disabled immediately. Otherwise calls
    bootstrap.bootstrap_device(..., rebuild=True), reloads the registry,
    updates host/port/session, and retries the original request once
    (_recovery_attempt kwarg prevents infinite loops).

  Orphan-session 404 — session deleted out-of-band:
    Detected in _request on HTTP 404 whose URL path matches the currently-
    stored session_id. If SIMDRIVE_NO_AUTO_REBUILD=1, raises original error.
    Otherwise calls open_session(_last_bundle_id) to get a fresh session_id
    and retries once. Same _recovery_attempt counter as Code 41.

  Transient transport errors (httpx.TransportError — connect/read timeouts,
  half-closed sockets, etc.):
    Retried with bounded exponential backoff (0.2s → 5s, multiplier 1.6,
    default 3 attempts total). On exhaustion, raises wda_recovery_exhausted
    with a per-attempt history. The legacy wda_unreachable path is kept for
    callers that pass max_attempts=1 / disable the loop via env.

Debugging:
  Set ``SIMDRIVE_HTTP_DEBUG=1`` (any non-empty value) in the environment to log
  every HTTP call at INFO level: method, path, request body (truncated at 256
  chars and scrubbed on /wda/typing or /wda/keys paths), response status, and
  response body (truncated at 256 chars). Useful for diagnosing WDA protocol
  issues or unexpected responses without adding permanent noise.
"""
from __future__ import annotations

import base64
import logging
import os
import re
import time
from typing import Any, Optional

import httpx

from .errors import wda_recovery_exhausted, wda_session_lost, wda_ui_automation_disabled
from ..errors import SimdriveError

_LOG = logging.getLogger("simdrive.wda.client")
_log = _LOG  # alias for code paths using either name

# Regex that matches WDA Code=41 / Code 41 within an error body that also
# contains "XCTDaemonErrorDomain". The (?!\d) negative lookahead guards against
# false positives like "Code=410" (HTTP 410 Gone) or "Code=4100".
_CODE41_RE = re.compile(r"Code[= ]41(?!\d)")

# SIMDRIVE_HTTP_DEBUG — module-level flag; also re-checked per-call so that
# both env-var monkeypatching (test_a12_http_debug.py) and direct attribute
# patching (test_a12_polish.py) work without a module reload.
_HTTP_DEBUG: bool = bool(os.environ.get("SIMDRIVE_HTTP_DEBUG", "").strip())
# Truncate logged bodies aggressively — full WDA error blobs can be many KB
# and may include user-typed text from /wda/typing / /wda/keys requests.
_DEBUG_TRUNCATE = 256  # chars; long bodies (screenshots) are truncated to this

# Paths whose request body must NEVER be logged verbatim (likely contains
# user-typed strings — PII risk). The response body for these paths is still
# truncated but not scrubbed because WDA doesn't echo back the keys payload.
_PII_REQUEST_PATHS = ("/wda/typing", "/wda/keys")

# Exponential backoff parameters for transient transport-error retries.
_BACKOFF_INITIAL_S = 0.2
_BACKOFF_MULTIPLIER = 1.6
_BACKOFF_CAP_S = 5.0
_BACKOFF_MAX_ATTEMPTS = 3  # initial + 2 retries

# Default httpx timeout — applied per request (connect, read, write, pool).
# A single read timeout used to gate every operation; we now name each phase
# explicitly so a stuck pool / half-closed socket cannot hang teardown forever.
_DEFAULT_CONNECT_TIMEOUT_S = 5.0
_DEFAULT_WRITE_TIMEOUT_S = 10.0
_DEFAULT_POOL_TIMEOUT_S = 5.0


def _http_debug_enabled() -> bool:
    """Return True when HTTP debug logging is active.

    Checks both the module-level ``_HTTP_DEBUG`` flag (patchable via
    ``monkeypatch.setattr(mod, '_HTTP_DEBUG', True/False)``) and the live
    environment variable (patchable via ``monkeypatch.setenv``).

    Design intent:
      - Tests that use ``monkeypatch.setenv("SIMDRIVE_HTTP_DEBUG", "1")`` get
        True from the env check even if _HTTP_DEBUG is False.
      - Tests that use ``monkeypatch.delenv(...)`` + ``monkeypatch.setattr(mod,
        "_HTTP_DEBUG", False)`` get False from both checks.
      - Production code uses the env var; _HTTP_DEBUG mirrors it at boot.
    """
    return _HTTP_DEBUG or bool(os.environ.get("SIMDRIVE_HTTP_DEBUG", "").strip())


def _build_timeout(timeout: float) -> httpx.Timeout:
    """Return an httpx.Timeout with explicit per-phase values.

    The ``timeout`` parameter is treated as the read budget — the longest a
    response is allowed to take to arrive. Connect/write/pool are bounded
    independently so a half-closed socket cannot stall teardown forever.
    """
    return httpx.Timeout(
        connect=_DEFAULT_CONNECT_TIMEOUT_S,
        read=float(timeout),
        write=_DEFAULT_WRITE_TIMEOUT_S,
        pool=_DEFAULT_POOL_TIMEOUT_S,
    )


def _scrub_body_for_log(path: str, body: str) -> str:
    """Return a log-safe excerpt of ``body``.

    If ``path`` is on the PII allowlist (/wda/typing, /wda/keys), return a
    placeholder rather than the raw text. Otherwise truncate to
    ``_DEBUG_TRUNCATE`` chars.
    """
    if not body:
        return ""
    if any(p in path for p in _PII_REQUEST_PATHS):
        return "<scrubbed: typed text>"
    excerpt = body[:_DEBUG_TRUNCATE]
    if len(body) > _DEBUG_TRUNCATE:
        excerpt += "[truncated]"
    return excerpt


# Runtime error for HTTP-level failures (non-2xx or network error).
def _wda_http_error(method: str, url: str, status: int, body: str) -> SimdriveError:
    # Truncate the body that goes into the human-readable message to 256 chars
    # so we don't dump multi-KB blobs into logs / exception strings. The full
    # body is still preserved under ``details`` for programmatic inspection.
    excerpt = body[:_DEBUG_TRUNCATE]
    if len(body) > _DEBUG_TRUNCATE:
        excerpt += "[truncated]"
    return SimdriveError(
        code="wda_http_error",
        message=(
            f"WDA {method} {url} returned HTTP {status}. "
            f"Body: {excerpt}. "
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


def _next_backoff(attempt: int) -> float:
    """Return the sleep duration before retry ``attempt`` (1-indexed).

    attempt=1 → INITIAL; subsequent attempts multiply by MULTIPLIER, capped.
    """
    sleep = _BACKOFF_INITIAL_S * (_BACKOFF_MULTIPLIER ** (attempt - 1))
    return min(sleep, _BACKOFF_CAP_S)


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

    def __init__(
        self,
        host: str,
        port: int,
        timeout: float = 30.0,
        max_transport_attempts: int = _BACKOFF_MAX_ATTEMPTS,
    ) -> None:
        self._host = host
        self._port = port
        self._base = f"http://{host}:{port}"
        self._session_id: Optional[str] = None
        self._last_seen_at: float = time.time()
        self._window_size_cache: Optional[tuple[int, int]] = None  # cached (w_pts, h_pts) for F-006
        # httpx transport is injectable for unit tests (httpx.MockTransport).
        # Explicit per-phase timeouts prevent the client from hanging forever
        # on a half-closed socket during teardown.
        self._client = httpx.Client(base_url=self._base, timeout=_build_timeout(timeout))
        # a12 auto-recovery state.
        self._udid: Optional[str] = None           # set by caller after construction
        self._team_id: Optional[str] = None        # set by caller after construction
        self._last_bundle_id: Optional[str] = None  # set by open_session()
        # Max attempts for the transport-error backoff loop. Tests can lower
        # this to 1 to assert the legacy raise-immediately behaviour without
        # adding real sleeps to the test suite.
        self._max_transport_attempts: int = max(1, int(max_transport_attempts))

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
          - httpx.TransportError (transient network) → exponential backoff retry

        _recovery_attempt is incremented on each Code-41/404 auto-retry; max 1
        retry per call for those expensive recoveries. Transport-error retries
        are layered separately and capped by _BACKOFF_MAX_ATTEMPTS (default 3).
        """
        return self._request_with_backoff(
            method, path, _recovery_attempt=_recovery_attempt, **kwargs
        )

    def _request_with_backoff(
        self,
        method: str,
        path: str,
        _recovery_attempt: int = 0,
        max_attempts: Optional[int] = None,
        **kwargs: Any,
    ) -> dict:
        """Inner loop: retry httpx.TransportError with exponential backoff.

        Code-41 / orphan-404 recovery still happens via _send_once because
        those failures require heavyweight remediation (rebuild / re-open)
        rather than a simple wait-and-retry.
        """
        if max_attempts is None:
            max_attempts = self._max_transport_attempts
        # max_attempts=1 disables the retry loop entirely: a transport error
        # is raised verbatim as wda_unreachable, matching pre-resilience
        # behaviour. This preserves a small surface area for callers that
        # want to handle network failures themselves (and for tests that
        # assert the legacy code without sleeping).
        if max_attempts <= 1:
            return self._send_once(
                method, path, _recovery_attempt=_recovery_attempt, **kwargs
            )
        history: list[dict[str, Any]] = []
        attempt = 1
        while True:
            try:
                return self._send_once(
                    method, path, _recovery_attempt=_recovery_attempt, **kwargs
                )
            except SimdriveError as exc:
                # wda_unreachable is the only code derived from a transport
                # error. Everything else is a deliberate raise we should not
                # retry.
                if exc.code != "wda_unreachable":
                    raise
                history.append(
                    {
                        "attempt": attempt,
                        "trigger": exc.code,
                        "error": str(exc.details.get("exc", ""))[:_DEBUG_TRUNCATE],
                        "action": "backoff_retry"
                        if attempt < max_attempts
                        else "give_up",
                    }
                )
                if attempt >= max_attempts:
                    _LOG.warning(
                        "[simdrive.wda] recovery exhausted method=%s path=%s "
                        "attempts=%d last_error=%s",
                        method,
                        path,
                        attempt,
                        history[-1]["error"],
                    )
                    raise wda_recovery_exhausted(
                        method, path, attempts=attempt, history=history
                    ) from exc
                sleep_s = _next_backoff(attempt)
                _LOG.warning(
                    "[simdrive.wda] transport error method=%s path=%s "
                    "attempt=%d/%d backoff=%.3fs error=%s",
                    method,
                    path,
                    attempt,
                    max_attempts,
                    sleep_s,
                    history[-1]["error"],
                )
                time.sleep(sleep_s)
                attempt += 1

    def _send_once(
        self,
        method: str,
        path: str,
        _recovery_attempt: int = 0,
        **kwargs: Any,
    ) -> dict:
        """Send the HTTP request exactly once and run Code-41 / orphan-404
        recovery if applicable. Transport errors propagate as SimdriveError
        (code=wda_unreachable); the caller decides whether to retry.
        """
        url = path
        _debug = _http_debug_enabled()
        if _debug:
            req_body = kwargs.get("json") or kwargs.get("data") or ""
            req_body_str = _scrub_body_for_log(path, str(req_body) if req_body else "")
            _log.info(
                "[WDA] >> %s %s body=%s",
                method, url, req_body_str or "(none)",
            )
        try:
            resp = self._client.request(method, url, **kwargs)
        except httpx.TransportError as exc:
            raise _wda_unreachable(self._host, self._port, str(exc)) from exc
        self._last_seen_at = time.time()
        if _debug:
            # Response bodies are never PII-sensitive in WDA: the keys/typing
            # endpoints don't echo. Still truncate to a sane size.
            resp_body_str = resp.text
            if len(resp_body_str) > _DEBUG_TRUNCATE:
                resp_body_str = resp_body_str[:_DEBUG_TRUNCATE] + "[truncated]"
            _log.info(
                "[WDA] << %s %s status=%d body=%s",
                method, url, resp.status_code, resp_body_str,
            )
        if not resp.is_success:
            raw_body = resp.text

            # ── Code-41 auto-recovery (XCTDaemonErrorDomain entitlement loss) ──
            if (
                _recovery_attempt == 0
                and "XCTDaemonErrorDomain" in raw_body
                and _CODE41_RE.search(raw_body)
            ):
                _LOG.warning(
                    "[simdrive.wda] code41_detected method=%s path=%s "
                    "action=rebuild attempt=%d",
                    method,
                    path,
                    _recovery_attempt + 1,
                )
                if os.environ.get("SIMDRIVE_NO_AUTO_REBUILD"):
                    _LOG.warning(
                        "[simdrive.wda] code41 recovery skipped"
                        " (SIMDRIVE_NO_AUTO_REBUILD=1) udid=%s",
                        self._udid,
                    )
                    raise wda_ui_automation_disabled(self._udid or f"{self._host}:{self._port}")
                # Attempt rebuild + re-acquire session.
                self._rebuild_and_reopen()
                _LOG.warning(
                    "[simdrive.wda] code41 rebuild complete; retrying method=%s path=%s",
                    method, path,
                )
                # Retry the original request once.
                return self._send_once(method, path, _recovery_attempt=1, **kwargs)

            # ── Orphan-session 404 auto-recovery ─────────────────────────────
            if (
                _recovery_attempt == 0
                and resp.status_code == 404
                and self._session_id
                and f"/session/{self._session_id}" in path
            ):
                bundle_label = repr(self._last_bundle_id) if self._last_bundle_id else "<none>"
                _LOG.warning(
                    "[simdrive.wda] orphan_session_404 method=%s path=%s "
                    "bundle=%s action=reopen attempt=%d",
                    method, path, bundle_label, _recovery_attempt + 1,
                )
                if os.environ.get("SIMDRIVE_NO_AUTO_REBUILD"):
                    _LOG.warning(
                        "[simdrive.wda] orphan_session_404 recovery skipped"
                        " (SIMDRIVE_NO_AUTO_REBUILD=1)"
                    )
                    raise _wda_http_error(method, url, resp.status_code, raw_body)
                # Re-open WDA session on the same bundle.
                new_sid = self.open_session(self._last_bundle_id)
                # Substitute the new session id into the path and retry once.
                new_path = path.replace(
                    f"/session/{self._session_id}", f"/session/{new_sid}", 1
                )
                _LOG.warning(
                    "[simdrive.wda] orphan_session_404 reopen complete new_sid=%s",
                    new_sid,
                )
                return self._send_once(method, new_path, _recovery_attempt=1, **kwargs)

            raise _wda_http_error(method, url, resp.status_code, raw_body)

        try:
            return resp.json()
        except Exception:
            return {}

    def _request_with_recovery(self, method: str, path: str, **kwargs: Any) -> dict:
        """Call _request and apply Code-41 / orphan-404 recovery on raised SimdriveErrors.

        This wrapper exists so that tests can monkeypatch _request to raise
        SimdriveError by code (rather than returning an HTTP response body) and
        still exercise the auto-recovery paths. Action methods (tap, swipe, etc.)
        call this instead of _request directly.

        Recovery rules (mirror the HTTP-body detection in _request):
          - wda_ui_automation_disabled (Code 41): rebuild + reopen + retry once.
          - wda_session_404 (orphan session): reopen session + retry once.
          - SIMDRIVE_NO_AUTO_REBUILD=1: re-raise immediately for both codes.
          - All other codes: re-raise unchanged.
        """
        try:
            return self._request(method, path, **kwargs)
        except SimdriveError as exc:
            if exc.code == "wda_ui_automation_disabled":
                if os.environ.get("SIMDRIVE_NO_AUTO_REBUILD"):
                    raise
                _LOG.warning(
                    "[simdrive] Code-41 error raised — auto-rebuilding"
                    " (set SIMDRIVE_NO_AUTO_REBUILD=1 to opt out)"
                )
                from . import bootstrap as _bootstrap
                _bootstrap.bootstrap_device(self._udid, team_id=self._team_id)
                self.open_session(self._last_bundle_id)
                return self._request(method, path, **kwargs)

            if exc.code == "wda_session_404":
                if os.environ.get("SIMDRIVE_NO_AUTO_REBUILD"):
                    raise
                _LOG.warning(
                    "[simdrive] wda_session_404 raised — re-acquiring session"
                    " (set SIMDRIVE_NO_AUTO_REBUILD=1 to opt out)"
                )
                self.open_session(self._last_bundle_id)
                return self._request(method, path, **kwargs)

            raise

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
        self._request_with_recovery(
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
        self._request_with_recovery(
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
        self._request_with_recovery(
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
