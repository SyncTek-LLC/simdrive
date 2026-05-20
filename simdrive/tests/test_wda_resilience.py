"""Resilience tests for simdrive.wda.client (INIT-2026-549).

Covers the five audit items from the hardening sprint:

  1. Explicit httpx.Timeout (connect / read / write / pool) — verified by
     inspecting the configured client.
  2. Exponential backoff on transient httpx.TransportError, capped at
     max_attempts and recorded in a structured history.
  3. Tightened _CODE41_RE regex rejects false positives like ``Code=410``
     (HTTP 410 Gone) while still matching real Code=41 / Code 41 payloads.
  4. Structured logging — every recovery attempt emits a record with
     attempt index, trigger, action, and outcome.
  5. PII scrubbing — request bodies on /wda/typing / /wda/keys are never
     written verbatim to the debug log.

All tests use httpx.MockTransport so no real network is contacted. ``time.sleep``
is monkeypatched to a counter so the backoff timing is asserted in code instead
of on the wall clock.
"""
from __future__ import annotations

import json
import logging
from typing import Any
from unittest.mock import patch

import httpx
import pytest


# ── helpers ───────────────────────────────────────────────────────────────────


def _make_response(status: int, body: Any) -> httpx.Response:
    if isinstance(body, (dict, list)):
        content = json.dumps(body).encode()
        headers = {"content-type": "application/json"}
    else:
        content = str(body).encode()
        headers = {"content-type": "text/plain"}
    return httpx.Response(status, content=content, headers=headers)


def _patched_sleep(sleeps: list[float]):
    """Return a fake time.sleep that records its argument instead of sleeping."""

    def _fake(seconds: float) -> None:
        sleeps.append(float(seconds))

    return _fake


# ── Item 1: explicit Timeout configuration ────────────────────────────────────


class TestExplicitTimeoutConfig:
    """The httpx.Client should be created with per-phase timeouts so the
    session cannot hang forever on a half-closed socket during teardown.
    """

    def test_timeout_has_explicit_connect_read_write_pool(self):
        from simdrive.wda.client import WdaClient

        client = WdaClient(host="localhost", port=8100, timeout=12.0)
        t = client._client.timeout

        # httpx.Timeout exposes per-phase floats. None means "no limit" which
        # is exactly what we are guarding against.
        assert t.connect is not None, "connect timeout must be explicit"
        assert t.read is not None, "read timeout must be explicit"
        assert t.write is not None, "write timeout must be explicit"
        assert t.pool is not None, "pool timeout must be explicit"
        # Read budget is whatever the caller passed in.
        assert t.read == pytest.approx(12.0)


# ── Item 2: exponential backoff on transient transport errors ────────────────


class TestExponentialBackoff:
    """httpx.TransportError → bounded exponential backoff retry."""

    def test_read_error_then_success_recovers_after_one_retry(self):
        """ReadError once, then 200 → loop succeeds on the second attempt."""
        call_count = [0]
        sleeps: list[float] = []

        def _handler(request: httpx.Request) -> httpx.Response:
            call_count[0] += 1
            if call_count[0] == 1:
                raise httpx.ReadError("mid-swipe disconnect")
            return _make_response(200, {"value": {"ok": True}})

        from simdrive.wda.client import WdaClient
        client = WdaClient(host="localhost", port=8100)
        client._replace_transport(httpx.MockTransport(_handler))

        with patch("simdrive.wda.client.time.sleep", _patched_sleep(sleeps)):
            result = client._request("POST", "/session/s/wda/tap", json={"x": 1, "y": 2})

        assert call_count[0] == 2, "expected exactly one retry"
        assert result == {"value": {"ok": True}}
        # One backoff sleep between attempt 1 and 2 with the documented initial.
        assert len(sleeps) == 1
        assert sleeps[0] == pytest.approx(0.2)

    def test_backoff_multiplier_progression(self):
        """Three failures should sleep 0.2, then 0.2*1.6 between attempts."""
        sleeps: list[float] = []

        def _handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ReadError("still down")

        from simdrive.wda.client import WdaClient
        from simdrive.errors import SimdriveError
        client = WdaClient(host="localhost", port=8100)  # max_attempts default = 3
        client._replace_transport(httpx.MockTransport(_handler))

        with patch("simdrive.wda.client.time.sleep", _patched_sleep(sleeps)):
            with pytest.raises(SimdriveError) as exc:
                client._request("GET", "/status")

        assert exc.value.code == "wda_recovery_exhausted"
        # Two sleeps for three attempts.
        assert len(sleeps) == 2
        assert sleeps[0] == pytest.approx(0.2)
        assert sleeps[1] == pytest.approx(0.2 * 1.6)

    def test_backoff_caps_at_max(self):
        """A large attempt count never sleeps above the cap (5s)."""
        sleeps: list[float] = []

        def _handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ReadError("nope")

        from simdrive.wda.client import WdaClient
        from simdrive.errors import SimdriveError
        client = WdaClient(host="localhost", port=8100, max_transport_attempts=20)
        client._replace_transport(httpx.MockTransport(_handler))

        with patch("simdrive.wda.client.time.sleep", _patched_sleep(sleeps)):
            with pytest.raises(SimdriveError) as exc:
                client._request("GET", "/status")

        assert exc.value.code == "wda_recovery_exhausted"
        # All sleeps must be ≤ 5.0s cap.
        assert sleeps, "expected some backoff sleeps"
        assert max(sleeps) <= 5.0 + 1e-9

    def test_max_attempts_exhausted_raises_structured_error(self):
        """After max_attempts the loop must raise wda_recovery_exhausted
        with a per-attempt history in details."""
        sleeps: list[float] = []

        def _handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ReadError("permanently broken")

        from simdrive.wda.client import WdaClient
        from simdrive.errors import SimdriveError
        client = WdaClient(host="localhost", port=8100, max_transport_attempts=3)
        client._replace_transport(httpx.MockTransport(_handler))

        with patch("simdrive.wda.client.time.sleep", _patched_sleep(sleeps)):
            with pytest.raises(SimdriveError) as exc:
                client._request("GET", "/status")

        err = exc.value
        assert err.code == "wda_recovery_exhausted"
        assert "Recovery:" in err.message
        history = err.details.get("history")
        assert isinstance(history, list)
        assert len(history) == 3, f"expected 3 attempts in history, got {history!r}"
        # Each entry should have attempt / trigger / action keys.
        for entry in history:
            assert "attempt" in entry
            assert entry["trigger"] == "wda_unreachable"
            assert "action" in entry
        # The last entry should be the give-up one.
        assert history[-1]["action"] == "give_up"
        assert err.details["attempts"] == 3
        assert err.details["method"] == "GET"
        assert err.details["path"] == "/status"

    def test_max_attempts_one_falls_back_to_legacy_raise(self):
        """max_transport_attempts=1 should bypass the loop entirely so
        callers that opt out get the original wda_unreachable code."""
        from simdrive.wda.client import WdaClient
        from simdrive.errors import SimdriveError

        def _handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("refused")

        client = WdaClient(host="localhost", port=8100, max_transport_attempts=1)
        client._replace_transport(httpx.MockTransport(_handler))
        with pytest.raises(SimdriveError) as exc:
            client._request("GET", "/status")
        assert exc.value.code == "wda_unreachable"


# ── Item 3: Code-41 detection + false-positive avoidance ──────────────────────


CODE41_BODY = (
    '{"value": {"error": "XCTDaemonErrorDomain Code=41 '
    'Not authorized for performing UI testing actions."}}'
)
# A body that would have matched the old loose `Code[= ]41` regex but is in
# reality an HTTP 410 reference (Gone). It includes the XCTDaemon marker so the
# only line of defence is the regex tightness.
CODE410_BODY = (
    '{"value": {"error": "XCTDaemonErrorDomain Code=410 '
    'Some unrelated framework spew."}}'
)


class TestCode41Regex:
    """The tightened regex must match Code=41 / Code 41 but reject Code=410."""

    def test_code41_real_payload_triggers_rebuild(self):
        call_count = [0]

        def _handler(request: httpx.Request) -> httpx.Response:
            call_count[0] += 1
            if call_count[0] == 1:
                return _make_response(403, CODE41_BODY)
            return _make_response(200, {"value": {"ok": True}})

        from simdrive.wda.client import WdaClient
        client = WdaClient(host="localhost", port=8100)
        client._udid = "TEST-UDID-WDA-D"
        client._replace_transport(httpx.MockTransport(_handler))

        with patch("simdrive.wda.client.WdaClient._rebuild_and_reopen") as rebuild_mock:
            rebuild_mock.return_value = None
            result = client._request("GET", "/status")

        rebuild_mock.assert_called_once()
        assert call_count[0] == 2
        assert result == {"value": {"ok": True}}

    def test_code410_false_positive_not_treated_as_code41(self):
        """Code=410 (HTTP Gone or arbitrary 4-digit code) must NOT trigger rebuild."""
        from simdrive.errors import SimdriveError

        call_count = [0]

        def _handler(request: httpx.Request) -> httpx.Response:
            call_count[0] += 1
            return _make_response(410, CODE410_BODY)

        from simdrive.wda.client import WdaClient
        client = WdaClient(host="localhost", port=8100)
        client._udid = "TEST-UDID-WDA-D"
        client._replace_transport(httpx.MockTransport(_handler))

        with patch("simdrive.wda.client.WdaClient._rebuild_and_reopen") as rebuild_mock:
            with pytest.raises(SimdriveError) as exc:
                client._request("GET", "/status")

        rebuild_mock.assert_not_called()
        assert call_count[0] == 1, "Code=410 must not trigger a recovery retry"
        # Should surface as a plain HTTP error.
        assert exc.value.code == "wda_http_error"

    def test_code_411_also_false_positive(self):
        """Code=411 or other 41-prefixed numerics are not entitlement loss."""
        from simdrive.errors import SimdriveError

        spurious = '{"value": {"error": "XCTDaemonErrorDomain Code=4100 noise"}}'

        def _handler(request: httpx.Request) -> httpx.Response:
            return _make_response(400, spurious)

        from simdrive.wda.client import WdaClient
        client = WdaClient(host="localhost", port=8100)
        client._udid = "TEST-UDID-WDA-D"
        client._replace_transport(httpx.MockTransport(_handler))

        with patch("simdrive.wda.client.WdaClient._rebuild_and_reopen") as rebuild_mock:
            with pytest.raises(SimdriveError):
                client._request("GET", "/status")
        rebuild_mock.assert_not_called()


# ── Item 4: structured logging on recovery attempts ───────────────────────────


class TestRecoveryLogging:
    """Recovery attempts must emit structured logs with attempt / action info."""

    def test_backoff_logs_each_attempt(self, caplog):
        sleeps: list[float] = []

        def _handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ReadError("flake")

        from simdrive.wda.client import WdaClient
        from simdrive.errors import SimdriveError
        client = WdaClient(host="localhost", port=8100, max_transport_attempts=3)
        client._replace_transport(httpx.MockTransport(_handler))

        with caplog.at_level(logging.WARNING, logger="simdrive.wda.client"):
            with patch("simdrive.wda.client.time.sleep", _patched_sleep(sleeps)):
                with pytest.raises(SimdriveError):
                    client._request("GET", "/status")

        messages = [r.message for r in caplog.records
                    if r.name == "simdrive.wda.client"]
        retry_lines = [m for m in messages if "transport error" in m]
        exhaust_lines = [m for m in messages if "recovery exhausted" in m]
        # Two retry log lines (attempts 1 and 2 before the final exhaust).
        assert len(retry_lines) == 2, f"want 2 retry log lines, got {retry_lines!r}"
        assert "attempt=1/3" in retry_lines[0]
        assert "attempt=2/3" in retry_lines[1]
        assert len(exhaust_lines) == 1
        assert "attempts=3" in exhaust_lines[0]

    def test_code41_logs_trigger_action_outcome(self, caplog):
        """Code-41 recovery emits both 'code41_detected' and 'rebuild complete' logs."""
        call_count = [0]

        def _handler(request: httpx.Request) -> httpx.Response:
            call_count[0] += 1
            if call_count[0] == 1:
                return _make_response(403, CODE41_BODY)
            return _make_response(200, {"value": {"ok": True}})

        from simdrive.wda.client import WdaClient
        client = WdaClient(host="localhost", port=8100)
        client._udid = "TEST-UDID-WDA-D"
        client._replace_transport(httpx.MockTransport(_handler))

        with caplog.at_level(logging.WARNING, logger="simdrive.wda.client"):
            with patch("simdrive.wda.client.WdaClient._rebuild_and_reopen") as rebuild_mock:
                rebuild_mock.return_value = None
                client._request("GET", "/status")

        messages = [r.message for r in caplog.records
                    if r.name == "simdrive.wda.client"]
        assert any("code41_detected" in m for m in messages), (
            f"missing code41_detected structured log; got {messages!r}"
        )
        assert any("code41 rebuild complete" in m for m in messages), (
            f"missing 'rebuild complete' log; got {messages!r}"
        )


# ── Item 5: PII scrubbing on debug logs ───────────────────────────────────────


class TestPiiScrub:
    """Request bodies on /wda/typing / /wda/keys must not be logged verbatim."""

    def test_typing_body_scrubbed_in_request_log(self, caplog, monkeypatch):
        """A failed /wda/keys call with debug logging on must not include the
        typed string in any log record."""
        import simdrive.wda.client as wda_client_mod

        monkeypatch.setenv("SIMDRIVE_HTTP_DEBUG", "1")
        monkeypatch.setattr(wda_client_mod, "_HTTP_DEBUG", True)

        secret = "hunter2-super-secret-password"

        def _handler(request: httpx.Request) -> httpx.Response:
            return _make_response(200, {"value": {}})

        client = wda_client_mod.WdaClient(host="localhost", port=8100)
        client._session_id = "sid-scrub"
        client._replace_transport(httpx.MockTransport(_handler))

        with caplog.at_level(logging.DEBUG, logger="simdrive.wda.client"):
            client.type_text(secret)

        all_messages = "\n".join(r.message for r in caplog.records)
        assert secret not in all_messages, (
            "the typed text appeared verbatim in a log record — PII leak"
        )
        # And the scrub placeholder should be present in at least one record.
        assert any("scrubbed" in r.message for r in caplog.records), (
            "expected the request log to include the <scrubbed> placeholder"
        )

    def test_typing_body_in_http_error_not_logged_verbatim(self, caplog, monkeypatch):
        """Even on HTTP error, the *request* body for /wda/keys should be
        scrubbed in any debug log line."""
        import simdrive.wda.client as wda_client_mod

        monkeypatch.setenv("SIMDRIVE_HTTP_DEBUG", "1")
        monkeypatch.setattr(wda_client_mod, "_HTTP_DEBUG", True)

        secret = "alice@example.com:Password!"

        def _handler(request: httpx.Request) -> httpx.Response:
            return _make_response(500, "boom")

        from simdrive.errors import SimdriveError
        client = wda_client_mod.WdaClient(host="localhost", port=8100)
        client._session_id = "sid-err"
        client._replace_transport(httpx.MockTransport(_handler))

        with caplog.at_level(logging.DEBUG, logger="simdrive.wda.client"):
            with pytest.raises(SimdriveError):
                client.type_text(secret)

        all_messages = "\n".join(r.message for r in caplog.records)
        assert secret not in all_messages, (
            "typed text leaked into a log record even after the call failed"
        )

    def test_non_typing_body_not_scrubbed(self, caplog, monkeypatch):
        """Bodies on non-PII paths (e.g. /wda/tap) are still truncated but
        not replaced with a placeholder — coordinates aren't sensitive."""
        import simdrive.wda.client as wda_client_mod

        monkeypatch.setenv("SIMDRIVE_HTTP_DEBUG", "1")
        monkeypatch.setattr(wda_client_mod, "_HTTP_DEBUG", True)

        def _handler(request: httpx.Request) -> httpx.Response:
            return _make_response(200, {"value": {}})

        client = wda_client_mod.WdaClient(host="localhost", port=8100)
        client._session_id = "sid-coord"
        client._replace_transport(httpx.MockTransport(_handler))

        with caplog.at_level(logging.DEBUG, logger="simdrive.wda.client"):
            client.tap(123.0, 456.0)

        messages = [r.message for r in caplog.records if "[WDA] >>" in r.message]
        assert messages, "expected at least one outgoing-request debug log"
        # Tap coordinates should be present, scrubbing should not.
        assert any("123" in m and "456" in m for m in messages)
        assert not any("scrubbed" in m for m in messages)

    def test_error_body_truncated_to_256_chars(self):
        """An HTTP error with a multi-KB body must produce an exception whose
        .message is bounded — defends against a 2KB+ dump in tracebacks/logs.
        """
        from simdrive.errors import SimdriveError

        huge_body = "x" * 4000

        def _handler(request: httpx.Request) -> httpx.Response:
            return _make_response(500, huge_body)

        from simdrive.wda.client import WdaClient
        client = WdaClient(host="localhost", port=8100, max_transport_attempts=1)
        client._replace_transport(httpx.MockTransport(_handler))

        with pytest.raises(SimdriveError) as exc:
            client._request("GET", "/status")

        assert exc.value.code == "wda_http_error"
        # Excerpt in message must be bounded.
        assert "[truncated]" in exc.value.message
        # The raw body is still preserved in details for programmatic access.
        assert exc.value.details["body"] == huge_body


# ── public surface: wda_recovery_exhausted is exported ────────────────────────


def test_wda_recovery_exhausted_reexported_from_package():
    """The new error constructor must be importable from simdrive.wda."""
    from simdrive.wda import wda_recovery_exhausted
    err = wda_recovery_exhausted("GET", "/foo", attempts=3, history=[{"attempt": 1}])
    assert err.code == "wda_recovery_exhausted"
    assert err.details["attempts"] == 3
