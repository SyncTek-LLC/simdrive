"""F-004: bootstrap smoke_test also calls POST /session, handling Code 41.

Tests:
  8. smoke_post_session_code_41_raises_ui_automation_disabled — POST /session returns
     a body with Code 41 (XCTDaemonErrorDomain); must raise SimdriveError with
     code='wda_ui_automation_disabled' and message containing
     "Settings → Developer → Enable UI Automation".
  9. smoke_post_session_success_cleans_up — POST /session returns valid sessionId;
     smoke_test issues DELETE /session/<id> afterward.
  10. smoke_post_session_5xx_warns_but_passes — POST /session returns HTTP 500 with
      no XCTDaemonErrorDomain body; smoke completes without raising.
  11. smoke_status_still_checked_first — /status fails; smoke raises the existing
      status error WITHOUT reaching the /session probe.

All tests FAIL on feat/v17-claude-native HEAD (3a22bd4) because smoke_test only
calls GET /status — it never calls POST /session.

  Tests 8, 9, 10 fail: the POST /session call is never made, so the side effects
  (exception, cleanup DELETE, warning) do not occur.
  Test 11 passes incidentally on 3a22bd4 (status gate exists); included to guard
  against regressions where POST /session is checked before /status.
"""
from __future__ import annotations

import json
import logging
from typing import Any
from unittest.mock import MagicMock, patch, call

import pytest
import httpx


# ── helpers ──────────────────────────────────────────────────────────────────


def _mock_response(status_code: int, body: Any) -> MagicMock:
    """Build a minimal httpx.Response mock."""
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status_code
    resp.is_success = 200 <= status_code < 300
    if isinstance(body, (dict, list)):
        resp.text = json.dumps(body)
        resp.json.return_value = body
    else:
        resp.text = str(body)
        resp.json.side_effect = Exception("not json")
    return resp


_STATUS_READY = {"value": {"ready": True, "build": {}, "os": {"version": "26.0"}}}
_SESSION_CREATED = {"value": {"sessionId": "abc-session-123", "capabilities": {}}}
_SESSION_CODE_41 = {
    "value": {
        "error": "unknown error",
        "message": "An internal error occurred in the XCTDaemonErrorDomain: Code 41 "
                   "UI Automation is not enabled on this device. "
                   "Enable it in Settings → Developer → Enable UI Automation."
    }
}


# ── test 8 ───────────────────────────────────────────────────────────────────


def test_smoke_post_session_code_41_raises_ui_automation_disabled():
    """POST /session returning Code 41 in body must raise wda_ui_automation_disabled.

    Fails on 3a22bd4: smoke_test never calls POST /session; no exception is raised.
    """
    from simdrive.wda.bootstrap import smoke_test
    from simdrive.errors import SimdriveError

    responses: dict[tuple[str, str], MagicMock] = {
        ("GET", "http://127.0.0.1:8100/status"): _mock_response(200, _STATUS_READY),
        # Code 41 body — may be HTTP 200 or HTTP 500 depending on WDA version;
        # the critical signal is the Code 41 in the JSON body.
        ("POST", "http://127.0.0.1:8100/session"): _mock_response(200, _SESSION_CODE_41),
    }

    def _route(method: str, url: str, **kwargs):
        key = (method.upper(), url)
        if key in responses:
            return responses[key]
        raise AssertionError(f"Unexpected {method} {url}")

    with patch("simdrive.wda.bootstrap.httpx") as mock_httpx:
        mock_client = MagicMock()
        mock_client.get.side_effect = lambda url, **kw: _route("GET", url)
        mock_client.post.side_effect = lambda url, **kw: _route("POST", url)
        mock_httpx.get.side_effect = lambda url, **kw: _route("GET", url)
        mock_httpx.post.side_effect = lambda url, **kw: _route("POST", url)

        with pytest.raises(SimdriveError) as exc:
            smoke_test("127.0.0.1", 8100)

    assert exc.value.code == "wda_ui_automation_disabled", (
        f"Expected code='wda_ui_automation_disabled', got {exc.value.code!r}. "
        "F-004: Code 41 in POST /session body must map to this error code."
    )
    assert "Settings" in exc.value.message and "Developer" in exc.value.message and "Enable UI Automation" in exc.value.message, (
        f"Expected message to contain 'Settings → Developer → Enable UI Automation', "
        f"got: {exc.value.message!r}"
    )


# ── test 9 ───────────────────────────────────────────────────────────────────


def test_smoke_post_session_success_cleans_up():
    """Successful POST /session must be followed by DELETE /session/<id>.

    F-004: smoke_test creates a WDA session to verify UI Automation is enabled,
    then deletes it so it doesn't interfere with the caller's session.

    Fails on 3a22bd4: POST /session is never called, so DELETE is also never called.
    """
    from simdrive.wda.bootstrap import smoke_test

    session_id = "abc-session-123"
    delete_calls: list[str] = []

    def _get(url: str, **kw):
        if url.endswith("/status"):
            return _mock_response(200, _STATUS_READY)
        raise AssertionError(f"Unexpected GET {url}")

    def _post(url: str, **kw):
        if url.endswith("/session"):
            return _mock_response(200, _SESSION_CREATED)
        raise AssertionError(f"Unexpected POST {url}")

    def _delete(url: str, **kw):
        delete_calls.append(url)
        return _mock_response(200, {"value": None})

    with patch("simdrive.wda.bootstrap.httpx") as mock_httpx:
        mock_httpx.get.side_effect = _get
        mock_httpx.post.side_effect = _post
        mock_httpx.delete.side_effect = _delete

        result = smoke_test("127.0.0.1", 8100)

    # Verify DELETE was called for the session we created.
    assert any(session_id in url for url in delete_calls), (
        f"Expected DELETE /session/{session_id} to be called after successful POST /session, "
        f"but delete_calls = {delete_calls!r}. "
        "F-004: smoke_test must clean up the WDA session it creates."
    )

    # The overall smoke should still succeed.
    assert result.get("value", {}).get("ready") is True, (
        f"smoke_test should return the /status body on success, got: {result!r}"
    )


# ── test 10 ──────────────────────────────────────────────────────────────────


def test_smoke_post_session_5xx_warns_but_passes(caplog):
    """POST /session returning HTTP 500 (no Code 41) must NOT raise; logs a warning.

    Devices that don't support the /session probe (old WDA, simulator quirk)
    should degrade gracefully — the smoke is informational. Only Code 41 is fatal.

    Fails on 3a22bd4: POST /session is never called, so no warning is emitted.
    This test will fail because the warning assertion won't find any warning
    about the session probe.
    """
    from simdrive.wda.bootstrap import smoke_test

    server_error_body = {"value": {"error": "internal server error", "message": "unexpected error"}}

    def _get(url: str, **kw):
        if url.endswith("/status"):
            return _mock_response(200, _STATUS_READY)
        raise AssertionError(f"Unexpected GET {url}")

    def _post(url: str, **kw):
        if url.endswith("/session"):
            return _mock_response(500, server_error_body)
        raise AssertionError(f"Unexpected POST {url}")

    with patch("simdrive.wda.bootstrap.httpx") as mock_httpx:
        mock_httpx.get.side_effect = _get
        mock_httpx.post.side_effect = _post

        with caplog.at_level(logging.WARNING, logger="simdrive.wda.bootstrap"):
            result = smoke_test("127.0.0.1", 8100)

    # Must not raise — smoke should complete.
    assert result is not None, "smoke_test should return a result dict on HTTP 5xx from /session"

    # Should have logged a warning about the session probe.
    warning_messages = [r.message for r in caplog.records if r.levelno >= logging.WARNING]
    assert any("session" in msg.lower() or "5" in msg for msg in warning_messages), (
        f"Expected a warning about /session probe failure (HTTP 500), "
        f"but no matching warning found. warnings: {warning_messages!r}. "
        "F-004: non-Code-41 session errors should log a warning but not raise."
    )


# ── test 11 ──────────────────────────────────────────────────────────────────


def test_smoke_status_still_checked_first():
    """/status failure must raise BEFORE the /session probe is attempted.

    GET /status is the gate check — if WDA isn't up yet, we shouldn't waste
    time probing /session. This guards against F-004 implementations that
    reorder the checks.

    On 3a22bd4, smoke_test does GET /status first and raises on failure, so
    this test passes. After F-004, the ordering must be preserved.
    """
    from simdrive.wda.bootstrap import smoke_test
    from simdrive.errors import SimdriveError

    session_called: list[bool] = []

    def _get(url: str, **kw):
        if url.endswith("/status"):
            # /status is down — WDA not ready.
            return _mock_response(503, {"error": "service unavailable"})
        raise AssertionError(f"Unexpected GET {url}")

    def _post(url: str, **kw):
        session_called.append(True)
        return _mock_response(200, _SESSION_CREATED)

    with patch("simdrive.wda.bootstrap.httpx") as mock_httpx:
        mock_httpx.get.side_effect = _get
        mock_httpx.post.side_effect = _post

        with pytest.raises(SimdriveError) as exc:
            smoke_test("127.0.0.1", 8100)

    # The status error must be raised.
    assert exc.value.code == "wda_smoke_failed", (
        f"Expected wda_smoke_failed on /status error, got {exc.value.code!r}"
    )

    # POST /session must NOT have been called.
    assert not session_called, (
        f"POST /session was called {len(session_called)} time(s) even though /status failed. "
        "F-004: /status must remain the gate — /session is only probed after /status succeeds."
    )
