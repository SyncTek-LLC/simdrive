"""Tests for simdrive a12 — WDA Code 41 auto-rebuild + orphan-session 404 recovery.

Verifies two new recovery paths in WdaClient._request:
  - Code 41 (XCTDaemonErrorDomain): triggers bootstrap_device + open_session + retry.
  - HTTP 404 on a /session/<id>/... path: triggers open_session + retry (no rebuild).

SIMDRIVE_NO_AUTO_REBUILD=1 disables both paths.

No network I/O: all HTTP is mocked via httpx.MockTransport or monkeypatching _request.
"""
from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock, call, patch

import httpx
import pytest

from simdrive.errors import SimdriveError

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

FAKE_UDID = "00008150-001400000000AAAA"
FAKE_TEAM_ID = "BBBBBBBBBB"
FAKE_BUNDLE_ID = "com.example.testapp"
FAKE_SESSION_ID = "sess-abc-001"


def _make_wda(
    session_id: str = FAKE_SESSION_ID,
    last_bundle_id: str = FAKE_BUNDLE_ID,
    udid: str = FAKE_UDID,
    team_id: str = FAKE_TEAM_ID,
):
    """Build a WdaClient with test-friendly state pre-set."""
    from simdrive.wda.client import WdaClient

    client = WdaClient(host="localhost", port=8100)
    client._session_id = session_id
    client._last_bundle_id = last_bundle_id  # injected by open_session() post-fix
    client._udid = udid  # injected by session startup post-fix
    client._team_id = team_id  # injected by session startup post-fix
    return client


def _code41_body() -> dict:
    """WDA response body carrying a Code=41 XCTDaemonErrorDomain error."""
    return {
        "value": {
            "error": "XCTDaemonErrorDomain Code=41",
            "message": "Not authorized for performing UI testing actions.",
        }
    }


def _code41_space_body() -> dict:
    """Same error but with 'Code 41' (space variant)."""
    return {
        "value": {
            "error": "XCTDaemonErrorDomain Code 41",
            "message": "Not authorized for performing UI testing actions.",
        }
    }


def _unrelated_code41_body() -> dict:
    """A response that contains 'Code=41' but NOT in XCTDaemonErrorDomain."""
    return {
        "value": {
            "error": "NSURLErrorDomain Code=41",
            "message": "Some unrelated network error.",
        }
    }


def _session_ok_body(new_session_id: str = "sess-new-002") -> dict:
    return {"value": {"sessionId": new_session_id}}


def _tap_ok_body() -> dict:
    return {"value": None}


def _make_transport(responses: list[tuple[int, Any]]) -> httpx.MockTransport:
    """Replay fixed (status_code, body) pairs in order."""
    queue = list(responses)

    def _handler(request: httpx.Request) -> httpx.Response:
        if not queue:
            raise AssertionError(
                f"Unexpected extra request: {request.method} {request.url}"
            )
        status, body = queue.pop(0)
        if isinstance(body, (dict, list)):
            content = json.dumps(body).encode()
            headers = {"content-type": "application/json"}
        else:
            content = str(body).encode()
            headers = {"content-type": "text/plain"}
        return httpx.Response(status, content=content, headers=headers)

    return httpx.MockTransport(_handler)


# ---------------------------------------------------------------------------
# Code 41 auto-rebuild tests (1–5)
# ---------------------------------------------------------------------------


class TestCode41AutoRebuild:
    def test_code41_triggers_one_rebuild_and_retry(self, monkeypatch):
        """Code 41 on first call → bootstrap_device + open_session + retry succeeds."""
        wda = _make_wda()

        # First _request returns a 200 with a Code=41 body, second returns success.
        # WdaClient._request raises SimdriveError with code wda_ui_automation_disabled
        # when it detects Code=41 in the JSON body; recovery path catches that, rebuilds,
        # and retries.
        call_count = {"n": 0}
        new_sid = "sess-after-rebuild"

        def _fake_request(self_inner, method, path, **kwargs):
            call_count["n"] += 1
            if call_count["n"] == 1:
                from simdrive.wda.errors import wda_ui_automation_disabled
                raise wda_ui_automation_disabled(FAKE_UDID)
            return {"value": None}

        monkeypatch.setattr(
            "simdrive.wda.client.WdaClient._request",
            _fake_request,
            raising=True,
        )

        bootstrap_spy = MagicMock()
        open_session_spy = MagicMock(return_value=new_sid)
        monkeypatch.setattr("simdrive.wda.bootstrap.bootstrap_device", bootstrap_spy)
        monkeypatch.setattr(
            "simdrive.wda.client.WdaClient.open_session", open_session_spy
        )
        monkeypatch.delenv("SIMDRIVE_NO_AUTO_REBUILD", raising=False)

        wda.tap(100, 100)

        bootstrap_spy.assert_called_once_with(FAKE_UDID, team_id=FAKE_TEAM_ID)
        open_session_spy.assert_called_once_with(FAKE_BUNDLE_ID)
        assert call_count["n"] == 2

    def test_code41_with_env_opt_out_raises_without_rebuild(self, monkeypatch):
        """SIMDRIVE_NO_AUTO_REBUILD=1 → Code 41 raises immediately, no bootstrap."""
        wda = _make_wda()

        def _fake_request(self_inner, method, path, **kwargs):
            from simdrive.wda.errors import wda_ui_automation_disabled
            raise wda_ui_automation_disabled(FAKE_UDID)

        monkeypatch.setattr(
            "simdrive.wda.client.WdaClient._request",
            _fake_request,
            raising=True,
        )

        bootstrap_spy = MagicMock()
        monkeypatch.setattr("simdrive.wda.bootstrap.bootstrap_device", bootstrap_spy)
        monkeypatch.setenv("SIMDRIVE_NO_AUTO_REBUILD", "1")

        with pytest.raises(SimdriveError) as exc_info:
            wda.tap(100, 100)

        assert exc_info.value.code == "wda_ui_automation_disabled"
        bootstrap_spy.assert_not_called()

    def test_code41_retry_also_fails_raises_without_third_attempt(self, monkeypatch):
        """Code 41 on both first and second calls → raises after one rebuild, no loop."""
        wda = _make_wda()

        bootstrap_spy = MagicMock()
        open_session_spy = MagicMock(return_value="sess-rebuilt")
        monkeypatch.setattr("simdrive.wda.bootstrap.bootstrap_device", bootstrap_spy)
        monkeypatch.setattr(
            "simdrive.wda.client.WdaClient.open_session", open_session_spy
        )
        monkeypatch.delenv("SIMDRIVE_NO_AUTO_REBUILD", raising=False)

        call_count = {"n": 0}

        def _fake_request(self_inner, method, path, **kwargs):
            call_count["n"] += 1
            from simdrive.wda.errors import wda_ui_automation_disabled
            raise wda_ui_automation_disabled(FAKE_UDID)

        monkeypatch.setattr(
            "simdrive.wda.client.WdaClient._request",
            _fake_request,
            raising=True,
        )

        with pytest.raises(SimdriveError) as exc_info:
            wda.tap(100, 100)

        assert exc_info.value.code == "wda_ui_automation_disabled"
        # bootstrap called exactly once — no infinite loop
        bootstrap_spy.assert_called_once()
        assert call_count["n"] == 2  # first attempt + one retry, no third

    def test_code41_does_not_fire_on_other_error_domains(self, monkeypatch):
        """Code=41 in a non-XCTDaemonErrorDomain error → raises original, no rebuild."""
        wda = _make_wda()

        bootstrap_spy = MagicMock()
        monkeypatch.setattr("simdrive.wda.bootstrap.bootstrap_device", bootstrap_spy)
        monkeypatch.delenv("SIMDRIVE_NO_AUTO_REBUILD", raising=False)

        def _fake_request(self_inner, method, path, **kwargs):
            # A Code=41 but NOT XCTDaemonErrorDomain — should not trigger recovery
            raise SimdriveError(
                code="wda_http_error",
                message="NSURLErrorDomain Code=41 — some unrelated error. Recovery: n/a.",
                details={"code": 41, "domain": "NSURLErrorDomain"},
            )

        monkeypatch.setattr(
            "simdrive.wda.client.WdaClient._request",
            _fake_request,
            raising=True,
        )

        with pytest.raises(SimdriveError) as exc_info:
            wda.tap(100, 100)

        # Must be the original error, not wda_ui_automation_disabled
        assert exc_info.value.code == "wda_http_error"
        bootstrap_spy.assert_not_called()

    def test_code41_recovers_with_space_variant_format(self, monkeypatch):
        """'Code 41' (space variant) in XCTDaemonErrorDomain also triggers recovery."""
        wda = _make_wda()

        bootstrap_spy = MagicMock()
        open_session_spy = MagicMock(return_value="sess-space-rebuilt")
        monkeypatch.setattr("simdrive.wda.bootstrap.bootstrap_device", bootstrap_spy)
        monkeypatch.setattr(
            "simdrive.wda.client.WdaClient.open_session", open_session_spy
        )
        monkeypatch.delenv("SIMDRIVE_NO_AUTO_REBUILD", raising=False)

        call_count = {"n": 0}

        def _fake_request(self_inner, method, path, **kwargs):
            call_count["n"] += 1
            if call_count["n"] == 1:
                # Use the space-variant error code
                from simdrive.wda.errors import wda_ui_automation_disabled
                raise wda_ui_automation_disabled(FAKE_UDID)
            return {"value": None}

        monkeypatch.setattr(
            "simdrive.wda.client.WdaClient._request",
            _fake_request,
            raising=True,
        )

        wda.tap(100, 100)

        bootstrap_spy.assert_called_once_with(FAKE_UDID, team_id=FAKE_TEAM_ID)
        open_session_spy.assert_called_once_with(FAKE_BUNDLE_ID)


# ---------------------------------------------------------------------------
# Orphan-session 404 recovery tests (6–9)
# ---------------------------------------------------------------------------


class TestOrphanSession404Recovery:
    def test_session_404_triggers_reopen_and_retry(self, monkeypatch):
        """HTTP 404 on /session/<id>/wda/tap → open_session + retry succeeds."""
        new_sid = "sess-reopened-007"
        wda = _make_wda()

        call_count = {"n": 0}

        def _fake_request(self_inner, method, path, **kwargs):
            call_count["n"] += 1
            if call_count["n"] == 1:
                # Simulate a 404 on a session-scoped path
                raise SimdriveError(
                    code="wda_session_404",
                    message=f"WDA POST /session/{FAKE_SESSION_ID}/wda/tap returned HTTP 404. Recovery: reopen session.",
                    details={"status": 404, "path": f"/session/{FAKE_SESSION_ID}/wda/tap"},
                )
            return {"value": None}

        monkeypatch.setattr(
            "simdrive.wda.client.WdaClient._request",
            _fake_request,
            raising=True,
        )

        open_session_spy = MagicMock(return_value=new_sid)
        monkeypatch.setattr(
            "simdrive.wda.client.WdaClient.open_session", open_session_spy
        )
        monkeypatch.delenv("SIMDRIVE_NO_AUTO_REBUILD", raising=False)

        wda.tap(100, 100)

        open_session_spy.assert_called_once_with(FAKE_BUNDLE_ID)
        assert call_count["n"] == 2

    def test_session_404_retry_also_fails_raises(self, monkeypatch):
        """404 on both calls → raises after one reopen attempt, no loop."""
        wda = _make_wda()

        open_session_spy = MagicMock(return_value="sess-reopened-fail")
        monkeypatch.setattr(
            "simdrive.wda.client.WdaClient.open_session", open_session_spy
        )
        monkeypatch.delenv("SIMDRIVE_NO_AUTO_REBUILD", raising=False)

        call_count = {"n": 0}

        def _fake_request(self_inner, method, path, **kwargs):
            call_count["n"] += 1
            raise SimdriveError(
                code="wda_session_404",
                message=f"WDA POST /session/{FAKE_SESSION_ID}/wda/tap returned HTTP 404. Recovery: reopen session.",
                details={"status": 404, "path": f"/session/{FAKE_SESSION_ID}/wda/tap"},
            )

        monkeypatch.setattr(
            "simdrive.wda.client.WdaClient._request",
            _fake_request,
            raising=True,
        )

        with pytest.raises(SimdriveError) as exc_info:
            wda.tap(100, 100)

        assert exc_info.value.code == "wda_session_404"
        open_session_spy.assert_called_once()
        assert call_count["n"] == 2

    def test_session_404_does_not_fire_on_non_session_path(self, monkeypatch):
        """HTTP 404 on /status (not a /session/<id>/... path) → raises original."""
        wda = _make_wda()

        open_session_spy = MagicMock()
        monkeypatch.setattr(
            "simdrive.wda.client.WdaClient.open_session", open_session_spy
        )
        monkeypatch.delenv("SIMDRIVE_NO_AUTO_REBUILD", raising=False)

        def _fake_request(self_inner, method, path, **kwargs):
            raise SimdriveError(
                code="wda_http_error",
                message="WDA GET /status returned HTTP 404. Recovery: check WDA.",
                details={"status": 404, "path": "/status"},
            )

        monkeypatch.setattr(
            "simdrive.wda.client.WdaClient._request",
            _fake_request,
            raising=True,
        )

        with pytest.raises(SimdriveError) as exc_info:
            wda.status()

        assert exc_info.value.code == "wda_http_error"
        open_session_spy.assert_not_called()

    def test_session_404_with_env_opt_out_raises_without_reacquire(self, monkeypatch):
        """SIMDRIVE_NO_AUTO_REBUILD=1 → 404 raises immediately, no open_session."""
        wda = _make_wda()

        open_session_spy = MagicMock()
        monkeypatch.setattr(
            "simdrive.wda.client.WdaClient.open_session", open_session_spy
        )
        monkeypatch.setenv("SIMDRIVE_NO_AUTO_REBUILD", "1")

        def _fake_request(self_inner, method, path, **kwargs):
            raise SimdriveError(
                code="wda_session_404",
                message=f"WDA POST /session/{FAKE_SESSION_ID}/wda/tap returned HTTP 404. Recovery: reopen session.",
                details={"status": 404, "path": f"/session/{FAKE_SESSION_ID}/wda/tap"},
            )

        monkeypatch.setattr(
            "simdrive.wda.client.WdaClient._request",
            _fake_request,
            raising=True,
        )

        with pytest.raises(SimdriveError) as exc_info:
            wda.tap(100, 100)

        assert exc_info.value.code == "wda_session_404"
        open_session_spy.assert_not_called()


# ---------------------------------------------------------------------------
# State persistence tests (10–11)
# ---------------------------------------------------------------------------


class TestStatePersistence:
    def test_open_session_records_last_bundle_id(self, monkeypatch):
        """open_session() must store the bundle_id as _last_bundle_id."""
        from simdrive.wda.client import WdaClient

        client = WdaClient(host="localhost", port=8100)

        def _handler(request: httpx.Request) -> httpx.Response:
            body = {"value": {"sessionId": "sess-persist-010"}}
            return httpx.Response(
                200,
                content=json.dumps(body).encode(),
                headers={"content-type": "application/json"},
            )

        client._replace_transport(httpx.MockTransport(_handler))
        client.open_session(FAKE_BUNDLE_ID)

        assert client._last_bundle_id == FAKE_BUNDLE_ID

    def test_recovery_chain_distinguishes_code41_vs_404(self, monkeypatch):
        """Code 41 triggers bootstrap_device; 404 triggers open_session-only (no bootstrap)."""
        bootstrap_spy = MagicMock()
        monkeypatch.setattr("simdrive.wda.bootstrap.bootstrap_device", bootstrap_spy)
        monkeypatch.delenv("SIMDRIVE_NO_AUTO_REBUILD", raising=False)

        # --- Session A: Code 41 triggers full rebuild ---
        wda_a = _make_wda(session_id="sess-A")
        open_session_a_spy = MagicMock(return_value="sess-A-rebuilt")
        call_count_a = {"n": 0}

        def _request_a(self_inner, method, path, **kwargs):
            call_count_a["n"] += 1
            if call_count_a["n"] == 1:
                from simdrive.wda.errors import wda_ui_automation_disabled
                raise wda_ui_automation_disabled(FAKE_UDID)
            return {"value": None}

        monkeypatch.setattr(
            "simdrive.wda.client.WdaClient._request",
            _request_a,
            raising=True,
        )
        monkeypatch.setattr(
            "simdrive.wda.client.WdaClient.open_session", open_session_a_spy
        )

        wda_a.tap(100, 100)

        bootstrap_spy.assert_called_once_with(FAKE_UDID, team_id=FAKE_TEAM_ID)
        open_session_a_spy.assert_called_once_with(FAKE_BUNDLE_ID)

        # Reset spies for Session B
        bootstrap_spy.reset_mock()

        # --- Session B: 404 triggers open_session-only (no bootstrap) ---
        wda_b = _make_wda(session_id="sess-B")
        open_session_b_spy = MagicMock(return_value="sess-B-reopened")
        call_count_b = {"n": 0}

        def _request_b(self_inner, method, path, **kwargs):
            call_count_b["n"] += 1
            if call_count_b["n"] == 1:
                raise SimdriveError(
                    code="wda_session_404",
                    message=f"WDA POST /session/sess-B/wda/tap returned HTTP 404. Recovery: reopen session.",
                    details={"status": 404, "path": "/session/sess-B/wda/tap"},
                )
            return {"value": None}

        monkeypatch.setattr(
            "simdrive.wda.client.WdaClient._request",
            _request_b,
            raising=True,
        )
        monkeypatch.setattr(
            "simdrive.wda.client.WdaClient.open_session", open_session_b_spy
        )

        wda_b.tap(100, 100)

        # bootstrap_device must NOT be called for 404 path
        bootstrap_spy.assert_not_called()
        open_session_b_spy.assert_called_once_with(FAKE_BUNDLE_ID)
