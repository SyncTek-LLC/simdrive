"""Tests for a12 WDA auto-recovery: Code-41 rebuild and orphan-session-404 re-acquire.

Verifies:
  - Code-41 detected in non-2xx response body → rebuild triggered, retry once
  - Code-41 with SIMDRIVE_NO_AUTO_REBUILD=1 → raises wda_ui_automation_disabled, no retry
  - Retry after Code-41 rebuild also fails with Code-41 → raises (no infinite loop)
  - Orphan-404 on session path → re-acquire session, retry once
  - Orphan-404 with SIMDRIVE_NO_AUTO_REBUILD=1 → raises wda_http_error, no retry
  - Retry after orphan-404 re-acquire also 404s → raises (no infinite loop)
  - Non-session 404 (top-level path) → NOT treated as orphan, raises normally
  - _last_bundle_id persisted from open_session() for re-acquire
  - _request _recovery_attempt kwarg plumbed correctly (max 1 retry per call)
"""
from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock, patch

import httpx
import pytest


# ── helpers ───────────────────────────────────────────────────────────────────


def _make_response(status: int, body: Any, extra_headers: dict | None = None) -> httpx.Response:
    headers = extra_headers or {}
    if isinstance(body, (dict, list)):
        content = json.dumps(body).encode()
        headers["content-type"] = "application/json"
    else:
        content = str(body).encode()
        headers.setdefault("content-type", "text/plain")
    return httpx.Response(status, content=content, headers=headers)


def _make_transport_from_queue(responses: list) -> httpx.MockTransport:
    """Return a MockTransport that replays (status, body) pairs in order."""
    queue = list(responses)

    def _handler(request: httpx.Request) -> httpx.Response:
        if not queue:
            raise AssertionError(
                f"Unexpected extra request: {request.method} {request.url}"
            )
        status, body = queue.pop(0)
        return _make_response(status, body)

    return httpx.MockTransport(_handler)


def _make_client(responses: list, udid: str = "TEST-UDID-0001"):
    from simdrive.wda.client import WdaClient

    client = WdaClient(host="192.168.1.10", port=8100)
    client._udid = udid
    client._replace_transport(_make_transport_from_queue(responses))
    return client


# ── Code-41 recovery ──────────────────────────────────────────────────────────


CODE41_BODY_EQ = (
    '{"value": {"error": "XCTDaemonErrorDomain Code=41 '
    'Not authorized for performing UI testing actions."}}'
)
CODE41_BODY_SPACE = (
    '{"value": {"error": "XCTDaemonErrorDomain Code 41 '
    'Not authorized for performing UI testing actions."}}'
)


def _make_rebuild_mock(new_host="192.168.1.10", new_port=8100):
    """Return (bootstrap_mock, registry_load_mock) that simulate a successful rebuild."""
    fake_registry_entry = {
        "host": new_host,
        "ip": new_host,
        "port": new_port,
        "team_id": "TEAM123",
    }
    bootstrap_mock = MagicMock(return_value=fake_registry_entry)
    registry_load_mock = MagicMock(return_value=fake_registry_entry)
    return bootstrap_mock, registry_load_mock


class TestCode41Recovery:
    """Code=41 mid-session auto-rebuild path."""

    def test_code41_eq_triggers_rebuild_and_retries(self):
        """Code=41 (equals sign) → rebuild triggered, request retried once, success."""
        call_count = [0]

        success_body = {"value": {"ready": True}}

        def _handler(request: httpx.Request) -> httpx.Response:
            call_count[0] += 1
            if call_count[0] == 1:
                return _make_response(403, CODE41_BODY_EQ)
            # Second call (after rebuild) succeeds.
            return _make_response(200, success_body)

        from simdrive.wda.client import WdaClient

        client = WdaClient(host="192.168.1.10", port=8100)
        client._udid = "TEST-UDID-0001"
        client._replace_transport(httpx.MockTransport(_handler))

        bootstrap_mock, registry_load_mock = _make_rebuild_mock()
        with (
            patch("simdrive.wda.client.WdaClient._rebuild_and_reopen") as rebuild_mock,
        ):
            rebuild_mock.return_value = None
            result = client._request("GET", "/status")

        # _request must have been called twice total (initial + retry).
        assert call_count[0] == 2, f"Expected 2 HTTP calls, got {call_count[0]}"
        rebuild_mock.assert_called_once()
        assert result == success_body

    def test_code41_space_variant_triggers_rebuild(self):
        """Code 41 (space variant) also triggers rebuild."""
        call_count = [0]

        def _handler(request: httpx.Request) -> httpx.Response:
            call_count[0] += 1
            if call_count[0] == 1:
                return _make_response(403, CODE41_BODY_SPACE)
            return _make_response(200, {"value": {"ok": True}})

        from simdrive.wda.client import WdaClient

        client = WdaClient(host="192.168.1.10", port=8100)
        client._udid = "TEST-UDID-0001"
        client._replace_transport(httpx.MockTransport(_handler))

        with patch("simdrive.wda.client.WdaClient._rebuild_and_reopen") as rebuild_mock:
            rebuild_mock.return_value = None
            client._request("GET", "/status")

        rebuild_mock.assert_called_once()
        assert call_count[0] == 2

    def test_code41_no_auto_rebuild_env_raises_immediately(self, monkeypatch):
        """SIMDRIVE_NO_AUTO_REBUILD=1 → raises wda_ui_automation_disabled, no rebuild."""
        from simdrive.errors import SimdriveError

        monkeypatch.setenv("SIMDRIVE_NO_AUTO_REBUILD", "1")
        call_count = [0]

        def _handler(request: httpx.Request) -> httpx.Response:
            call_count[0] += 1
            return _make_response(403, CODE41_BODY_EQ)

        from simdrive.wda.client import WdaClient

        client = WdaClient(host="192.168.1.10", port=8100)
        client._udid = "TEST-UDID-0001"
        client._replace_transport(httpx.MockTransport(_handler))

        with patch("simdrive.wda.client.WdaClient._rebuild_and_reopen") as rebuild_mock:
            with pytest.raises(SimdriveError) as exc:
                client._request("GET", "/status")

        assert exc.value.code == "wda_ui_automation_disabled"
        rebuild_mock.assert_not_called()
        assert call_count[0] == 1  # only one HTTP call, no retry

    def test_code41_retry_also_fails_raises_no_loop(self):
        """If retry after rebuild also gets Code-41 → raises wda_http_error, no infinite loop."""
        from simdrive.errors import SimdriveError

        call_count = [0]

        def _handler(request: httpx.Request) -> httpx.Response:
            call_count[0] += 1
            return _make_response(403, CODE41_BODY_EQ)

        from simdrive.wda.client import WdaClient

        client = WdaClient(host="192.168.1.10", port=8100)
        client._udid = "TEST-UDID-0001"
        client._replace_transport(httpx.MockTransport(_handler))

        with patch("simdrive.wda.client.WdaClient._rebuild_and_reopen") as rebuild_mock:
            rebuild_mock.return_value = None
            with pytest.raises(SimdriveError) as exc:
                client._request("GET", "/status")

        # First call → rebuild triggered → second call → raises (no third call).
        assert call_count[0] == 2, f"Expected exactly 2 HTTP calls, got {call_count[0]}"
        rebuild_mock.assert_called_once()
        # The second failure is not Code-41 triggered (recovery_attempt=1) so raises wda_http_error.
        assert exc.value.code == "wda_http_error"

    def test_code41_without_xct_domain_not_triggered(self):
        """A 403 body with Code=41 but without XCTDaemonErrorDomain is NOT treated as entitlement loss."""
        from simdrive.errors import SimdriveError

        call_count = [0]
        plain_403 = '{"value": {"error": "SomeOtherDomain Code=41 something else"}}'

        def _handler(request: httpx.Request) -> httpx.Response:
            call_count[0] += 1
            return _make_response(403, plain_403)

        from simdrive.wda.client import WdaClient

        client = WdaClient(host="192.168.1.10", port=8100)
        client._udid = "TEST-UDID-0001"
        client._replace_transport(httpx.MockTransport(_handler))

        with patch("simdrive.wda.client.WdaClient._rebuild_and_reopen") as rebuild_mock:
            with pytest.raises(SimdriveError) as exc:
                client._request("GET", "/status")

        rebuild_mock.assert_not_called()
        assert call_count[0] == 1
        assert exc.value.code == "wda_http_error"


# ── Orphan-session 404 recovery ───────────────────────────────────────────────


class TestOrphanSession404Recovery:
    """HTTP 404 on session-scoped path → re-acquire WDA session + retry."""

    def test_orphan_404_on_session_path_reacquires_and_retries(self):
        """404 on /session/<sid>/... → open_session called, retry succeeds."""
        UDID = "TEST-UDID-0002"
        BUNDLE = "com.example.MyApp"

        # Track calls to action paths (non-POST-/session) separately.
        action_call_count = [0]
        open_session_calls = [0]

        def _handler(request: httpx.Request) -> httpx.Response:
            path = str(request.url.path)
            if request.method == "POST" and path == "/session":
                open_session_calls[0] += 1
                return _make_response(200, {"value": {"sessionId": f"new-sid-{open_session_calls[0]}"}})
            # Action request: first attempt returns 404, second succeeds.
            action_call_count[0] += 1
            if action_call_count[0] == 1:
                return _make_response(404, "session not found")
            return _make_response(200, {"value": {"data": "ok"}})

        from simdrive.wda.client import WdaClient

        client = WdaClient(host="192.168.1.10", port=8100)
        client._udid = UDID
        client._replace_transport(httpx.MockTransport(_handler))

        # Simulate having a session open.
        client._session_id = "stale-sid-abc"
        client._last_bundle_id = BUNDLE

        result = client._request("GET", "/session/stale-sid-abc/screenshot")

        # open_session called once for re-acquire.
        assert open_session_calls[0] == 1
        # Two action attempts: original (404) + retry (200).
        assert action_call_count[0] == 2
        assert result == {"value": {"data": "ok"}}

    def test_orphan_404_no_auto_rebuild_env_raises(self, monkeypatch):
        """SIMDRIVE_NO_AUTO_REBUILD=1 → raises wda_http_error on orphan-404, no re-acquire."""
        from simdrive.errors import SimdriveError

        monkeypatch.setenv("SIMDRIVE_NO_AUTO_REBUILD", "1")

        call_count = [0]

        def _handler(request: httpx.Request) -> httpx.Response:
            call_count[0] += 1
            return _make_response(404, "session not found")

        from simdrive.wda.client import WdaClient

        client = WdaClient(host="192.168.1.10", port=8100)
        client._udid = "TEST-UDID-0002"
        client._session_id = "stale-sid"
        client._last_bundle_id = "com.example.App"
        client._replace_transport(httpx.MockTransport(_handler))

        with patch("simdrive.wda.client.WdaClient.open_session") as open_mock:
            with pytest.raises(SimdriveError) as exc:
                client._request("GET", "/session/stale-sid/screenshot")

        assert exc.value.code == "wda_http_error"
        open_mock.assert_not_called()
        assert call_count[0] == 1

    def test_orphan_404_retry_also_404s_raises_no_loop(self):
        """If re-acquire succeeds but retry also 404s → raises wda_http_error, no loop."""
        from simdrive.errors import SimdriveError

        http_call_count = [0]
        open_session_calls = [0]

        def _handler(request: httpx.Request) -> httpx.Response:
            http_call_count[0] += 1
            path = str(request.url.path)
            if request.method == "POST" and path == "/session":
                open_session_calls[0] += 1
                return _make_response(200, {"value": {"sessionId": "fresh-sid"}})
            # All action calls return 404.
            return _make_response(404, "session not found")

        from simdrive.wda.client import WdaClient

        client = WdaClient(host="192.168.1.10", port=8100)
        client._udid = "TEST-UDID-0002"
        client._session_id = "stale-sid"
        client._last_bundle_id = "com.example.App"
        client._replace_transport(httpx.MockTransport(_handler))

        with pytest.raises(SimdriveError) as exc:
            client._request("GET", "/session/stale-sid/screenshot")

        # One open_session call for re-acquire; two action-request attempts.
        assert open_session_calls[0] == 1
        assert exc.value.code == "wda_http_error"

    def test_non_session_404_not_treated_as_orphan(self):
        """404 on a non-session path (e.g. /status) is NOT treated as orphan-session."""
        from simdrive.errors import SimdriveError

        call_count = [0]

        def _handler(request: httpx.Request) -> httpx.Response:
            call_count[0] += 1
            return _make_response(404, "not found")

        from simdrive.wda.client import WdaClient

        client = WdaClient(host="192.168.1.10", port=8100)
        client._udid = "TEST-UDID-0002"
        client._session_id = "active-sid"
        client._replace_transport(httpx.MockTransport(_handler))

        with patch("simdrive.wda.client.WdaClient.open_session") as open_mock:
            with pytest.raises(SimdriveError) as exc:
                client._request("GET", "/status")

        open_mock.assert_not_called()
        assert call_count[0] == 1
        assert exc.value.code == "wda_http_error"


# ── _last_bundle_id persistence ───────────────────────────────────────────────


class TestLastBundleIdPersistence:
    """open_session stores bundle_id in _last_bundle_id for recovery."""

    def test_open_session_stores_bundle_id(self):
        from simdrive.wda.client import WdaClient

        client = WdaClient(host="localhost", port=8100)
        client._replace_transport(_make_transport_from_queue([
            (200, {"value": {"sessionId": "sid-abc"}}),
        ]))
        client.open_session("com.example.App")
        assert client._last_bundle_id == "com.example.App"

    def test_open_session_stores_none_for_no_bundle(self):
        from simdrive.wda.client import WdaClient

        client = WdaClient(host="localhost", port=8100)
        client._replace_transport(_make_transport_from_queue([
            (200, {"value": {"sessionId": "sid-xyz"}}),
        ]))
        client.open_session(None)
        assert client._last_bundle_id is None

    def test_recovery_attempt_max_1_no_cascade(self):
        """_recovery_attempt kwarg stops cascade: passing _recovery_attempt=1 suppresses rebuild."""
        from simdrive.errors import SimdriveError

        call_count = [0]

        def _handler(request: httpx.Request) -> httpx.Response:
            call_count[0] += 1
            return _make_response(403, CODE41_BODY_EQ)

        from simdrive.wda.client import WdaClient

        client = WdaClient(host="localhost", port=8100)
        client._udid = "TEST-UDID"
        client._replace_transport(httpx.MockTransport(_handler))

        with patch("simdrive.wda.client.WdaClient._rebuild_and_reopen") as rebuild_mock:
            with pytest.raises(SimdriveError) as exc:
                # Calling with _recovery_attempt=1 simulates "already retried once".
                client._request("GET", "/status", _recovery_attempt=1)

        rebuild_mock.assert_not_called()
        assert call_count[0] == 1
        assert exc.value.code == "wda_http_error"
