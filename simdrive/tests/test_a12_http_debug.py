"""a12: SIMDRIVE_HTTP_DEBUG=1 makes WdaClient._request log method + path + body.

Tests:
  8. test_http_debug_env_logs_request_and_response
     - monkeypatch SIMDRIVE_HTTP_DEBUG=1. Build a WdaClient, mock the HTTP
       transport. Invoke wda.tap(100, 100). Assert caplog contains a record
       with method POST, path /wda/tap, response status 200, AND the truncated
       request body. Without the env, the log should NOT contain those records.
  9. test_http_debug_truncates_large_bodies
     - Same setup but request body or response body is 5 KB. Assert the logged
       body is truncated to ≤2 KB + a '[truncated]' marker.

Both tests FAIL on HEAD because:
  - WdaClient._request does not read SIMDRIVE_HTTP_DEBUG at all.
  - No logging of method/path/body/status occurs in _request.
"""
from __future__ import annotations

import json
import logging
from typing import Any
from unittest.mock import MagicMock

import pytest
import httpx


# ── helpers ──────────────────────────────────────────────────────────────────


def _mock_transport(status_code: int = 200, body: Any = None) -> httpx.MockTransport:
    """Return an httpx.MockTransport that always returns the given response."""
    if body is None:
        body = {"value": None}

    response_body = json.dumps(body).encode()

    def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            status_code=status_code,
            headers={"Content-Type": "application/json"},
            content=response_body,
        )

    return httpx.MockTransport(_handler)


def _build_wda_client(host: str = "localhost", port: int = 8100) -> "WdaClient":
    from simdrive.wda.client import WdaClient
    wda = WdaClient(host=host, port=port)
    # Pre-set a fake session_id so tap() doesn't raise wda_session_not_open.
    wda._session_id = "fake-session-xyz"
    return wda


# ── test 8 ────────────────────────────────────────────────────────────────────


def test_http_debug_env_logs_request_and_response(monkeypatch, caplog):
    """SIMDRIVE_HTTP_DEBUG=1 causes _request to emit a simdrive.wda.client record
    that includes method, path, response status, AND request body.

    Fails on HEAD: _request has no debug logging; SIMDRIVE_HTTP_DEBUG is never read.
    The test specifically looks for records emitted by simdrive.wda.client (not httpx's
    built-in transport logger), because SIMDRIVE_HTTP_DEBUG adds body logging
    that httpx's own logger never includes.
    """
    monkeypatch.setenv("SIMDRIVE_HTTP_DEBUG", "1")

    wda = _build_wda_client()
    transport = _mock_transport(status_code=200, body={"value": None})
    wda._replace_transport(transport)

    with caplog.at_level(logging.DEBUG):
        wda.tap(100.0, 200.0)

    # Filter to only simdrive.wda.client records (not httpx transport logging).
    simdrive_records = [r for r in caplog.records if "simdrive" in r.name]
    all_simdrive_messages = [r.getMessage() for r in simdrive_records]
    combined = "\n".join(all_simdrive_messages)

    # Must have at least one simdrive-level debug record.
    assert simdrive_records, (
        f"Expected at least one simdrive.wda.client log record when "
        f"SIMDRIVE_HTTP_DEBUG=1, but got none. "
        f"All caplog records: {[r.name + ': ' + r.getMessage() for r in caplog.records]}"
    )

    # Must log the HTTP method in the simdrive record.
    assert "POST" in combined or "post" in combined.lower(), (
        f"Expected 'POST' in simdrive debug log when SIMDRIVE_HTTP_DEBUG=1, "
        f"but did not find it. simdrive log records:\n{chr(10).join(all_simdrive_messages)}"
    )

    # Must log the path in the simdrive record.
    assert "/wda/tap" in combined, (
        f"Expected '/wda/tap' in simdrive debug log, but not found. "
        f"simdrive log records:\n{chr(10).join(all_simdrive_messages)}"
    )

    # Must log the response status 200.
    assert "200" in combined, (
        f"Expected status '200' in simdrive debug log, but not found. "
        f"simdrive log records:\n{chr(10).join(all_simdrive_messages)}"
    )

    # Must log some portion of the request body (x=100 or y=200).
    body_logged = "100" in combined or "200" in combined
    assert body_logged, (
        f"Expected request body (x=100, y=200) to appear in simdrive debug log, "
        f"but not found. simdrive log records:\n{chr(10).join(all_simdrive_messages)}"
    )


def test_http_debug_env_absent_no_debug_logs(monkeypatch, caplog):
    """When SIMDRIVE_HTTP_DEBUG is not set, _request must NOT emit simdrive debug logs.

    Validates the negative case: simdrive.wda.client should produce no debug
    HTTP records without the env var.
    Fails on HEAD only if we accidentally add unconditional logging.
    """
    import simdrive.wda.client as wda_client_mod
    monkeypatch.delenv("SIMDRIVE_HTTP_DEBUG", raising=False)
    # Also reset the module-level flag in case a previous test in the same
    # process left it True via monkeypatch (module-level attrs persist across
    # the test run if the flag was True when the module was first imported).
    monkeypatch.setattr(wda_client_mod, "_HTTP_DEBUG", False)

    wda = _build_wda_client()
    transport = _mock_transport(status_code=200, body={"value": None})
    wda._replace_transport(transport)

    with caplog.at_level(logging.DEBUG):
        wda.tap(100.0, 200.0)

    simdrive_wda_debug_records = [
        r for r in caplog.records
        if r.name.startswith("simdrive.wda.client")
        and r.levelno <= logging.DEBUG
        and ("/wda/tap" in r.getMessage() or "POST" in r.getMessage())
    ]
    assert not simdrive_wda_debug_records, (
        f"Expected no HTTP debug log records from simdrive.wda.client without "
        f"SIMDRIVE_HTTP_DEBUG, but found: "
        f"{[r.getMessage() for r in simdrive_wda_debug_records]}"
    )


# ── test 9 ────────────────────────────────────────────────────────────────────


def test_http_debug_truncates_large_bodies(monkeypatch, caplog):
    """Large response body (5 KB) must be truncated to ≤2 KB + '[truncated]' marker.

    Fails on HEAD: no debug logging at all, so no truncation either.
    """
    monkeypatch.setenv("SIMDRIVE_HTTP_DEBUG", "1")

    # Build a 5 KB response body.
    large_value = "x" * 5120
    large_body = {"value": large_value}

    wda = _build_wda_client()
    transport = _mock_transport(status_code=200, body=large_body)
    wda._replace_transport(transport)

    with caplog.at_level(logging.DEBUG):
        wda.tap(50.0, 50.0)

    all_messages = "\n".join(r.getMessage() for r in caplog.records)

    # The full 5 KB string must NOT appear verbatim.
    assert large_value not in all_messages, (
        "5 KB response body was logged verbatim — truncation is required."
    )

    # The '[truncated]' marker must appear.
    assert "[truncated]" in all_messages, (
        f"Expected '[truncated]' marker in debug log for large body, "
        f"but not found. Log output:\n{all_messages[:500]}"
    )

    # The logged body portion must be ≤ 2048 chars (2 KB).
    # Find the section of the log containing the large value prefix.
    for record in caplog.records:
        msg = record.getMessage()
        # Count consecutive 'x' chars — the truncated prefix of large_value.
        x_run = len(msg) - len(msg.lstrip("x")) if msg.startswith("x") else 0
        # Check any contiguous x-run in the message.
        import re
        runs = re.findall(r"x{10,}", msg)
        for run in runs:
            assert len(run) <= 2048, (
                f"Logged body segment length {len(run)} exceeds 2048 bytes. "
                "Truncation limit is 2 KB."
            )
