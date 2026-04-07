"""Tests for WebhookEmitter (M17b) — INIT-2026-492.

TDD Phase — tests written BEFORE implementation exists.
These tests are importable even when the implementation module is absent.

Module under test (to be created by CodeAtlas):
  specterqa/ios/webhooks/emitter.py  — WebhookEmitter
"""

from __future__ import annotations

import hashlib
import hmac
import json
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Conditional import guard
# ---------------------------------------------------------------------------

try:
    from specterqa.ios.webhooks.emitter import WebhookEmitter  # type: ignore[import]

    _EMITTER_AVAILABLE = True
except ImportError:
    _EMITTER_AVAILABLE = False
    WebhookEmitter = None  # type: ignore[assignment,misc]

needs_emitter = pytest.mark.skipif(
    not _EMITTER_AVAILABLE,
    reason="specterqa.ios.webhooks.emitter not yet implemented",
)

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

_WEBHOOK_URL = "https://hooks.example.com/specterqa"
_SECRET = "super-secret-key-for-testing"


def _make_200_response() -> MagicMock:
    resp = MagicMock()
    resp.status_code = 200
    return resp


def _make_500_response() -> MagicMock:
    resp = MagicMock()
    resp.status_code = 500
    return resp


# ===========================================================================
# M17b: WebhookEmitter — 8 tests
# ===========================================================================


@needs_emitter
class TestEmitHTTP:
    """emit() HTTP behaviour — method, URL, payload structure."""

    def test_emit_sends_post_to_webhook_url(self):
        emitter = WebhookEmitter(webhook_url=_WEBHOOK_URL)
        with patch("requests.post", return_value=_make_200_response()) as mock_post:
            emitter.emit(event_type="test.completed", payload={"run_id": "r-001"})
        mock_post.assert_called_once()
        call_url = mock_post.call_args[0][0] if mock_post.call_args[0] else mock_post.call_args.kwargs.get("url")
        assert call_url == _WEBHOOK_URL

    def test_emit_includes_event_type_in_payload(self):
        emitter = WebhookEmitter(webhook_url=_WEBHOOK_URL)
        sent_json: dict = {}

        def capture_post(url, **kwargs):
            nonlocal sent_json
            body = kwargs.get("data") or kwargs.get("json")
            if isinstance(body, (bytes, str)):
                sent_json = json.loads(body)
            elif isinstance(body, dict):
                sent_json = body
            return _make_200_response()

        with patch("requests.post", side_effect=capture_post):
            emitter.emit(event_type="test.completed", payload={"run_id": "r-001"})

        assert "event_type" in sent_json, "'event_type' not present in emitted payload"
        assert sent_json["event_type"] == "test.completed"

    def test_emit_includes_timestamp_in_payload(self):
        emitter = WebhookEmitter(webhook_url=_WEBHOOK_URL)
        sent_json: dict = {}

        def capture_post(url, **kwargs):
            nonlocal sent_json
            body = kwargs.get("data") or kwargs.get("json")
            if isinstance(body, (bytes, str)):
                sent_json = json.loads(body)
            elif isinstance(body, dict):
                sent_json = body
            return _make_200_response()

        with patch("requests.post", side_effect=capture_post):
            emitter.emit(event_type="test.completed", payload={"run_id": "r-001"})

        assert "timestamp" in sent_json, "'timestamp' not present in emitted payload"

    def test_emit_returns_true_on_200_response(self):
        emitter = WebhookEmitter(webhook_url=_WEBHOOK_URL)
        with patch("requests.post", return_value=_make_200_response()):
            result = emitter.emit(event_type="test.completed", payload={})
        assert result is True

    def test_emit_returns_false_on_500_response(self):
        emitter = WebhookEmitter(webhook_url=_WEBHOOK_URL)
        with patch("requests.post", return_value=_make_500_response()):
            result = emitter.emit(event_type="test.failed", payload={})
        assert result is False


@needs_emitter
class TestEmitSignature:
    """HMAC-SHA256 signature header behaviour."""

    def test_emit_adds_x_signature_header_when_secret_provided(self):
        emitter = WebhookEmitter(webhook_url=_WEBHOOK_URL, secret=_SECRET)
        captured_headers: dict = {}

        def capture_post(url, **kwargs):
            captured_headers.update(kwargs.get("headers", {}))
            return _make_200_response()

        with patch("requests.post", side_effect=capture_post):
            emitter.emit(event_type="test.completed", payload={"run_id": "r-001"})

        assert "X-Signature" in captured_headers, "X-Signature header not present when secret is configured"

    def test_emit_does_not_add_x_signature_when_no_secret(self):
        emitter = WebhookEmitter(webhook_url=_WEBHOOK_URL)  # no secret
        captured_headers: dict = {}

        def capture_post(url, **kwargs):
            captured_headers.update(kwargs.get("headers", {}))
            return _make_200_response()

        with patch("requests.post", side_effect=capture_post):
            emitter.emit(event_type="test.completed", payload={"run_id": "r-001"})

        assert "X-Signature" not in captured_headers, (
            "X-Signature header should NOT be present when no secret is configured"
        )


@needs_emitter
class TestSign:
    """_sign() produces a valid HMAC-SHA256 hex digest."""

    def test_sign_produces_correct_hmac_sha256_hex_digest(self):
        emitter = WebhookEmitter(webhook_url=_WEBHOOK_URL, secret=_SECRET)
        body = b'{"event_type":"test.completed","run_id":"r-001"}'
        result = emitter._sign(body)
        expected = hmac.new(_SECRET.encode("utf-8"), body, hashlib.sha256).hexdigest()
        assert result == expected, f"_sign() returned {result!r}, expected {expected!r}"
