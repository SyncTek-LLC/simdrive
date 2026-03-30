"""M17b: WebhookEmitter — Async-compatible webhook delivery with HMAC signing.

Sends POST requests to a configured webhook URL, optionally signing the body
with HMAC-SHA256 and including the digest in an X-Signature header.

INIT-2026-492 — SpecterQA iOS Simulator Driver, Phase 4.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from typing import Any


class WebhookEmitter:
    """HTTP webhook emitter with optional HMAC-SHA256 request signing.

    Args:
        webhook_url: Full URL to POST events to.
        secret: Optional signing secret. When provided, each request includes
            an ``X-Signature`` header containing the HMAC-SHA256 hex digest of
            the serialised request body.
    """

    _PAYLOAD_VERSION = "1.0"

    def __init__(
        self,
        webhook_url: str,
        secret: str | None = None,
    ) -> None:
        self._webhook_url = webhook_url
        self._secret = secret

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def emit(self, event_type: str, payload: dict[str, Any]) -> bool:
        """Send an event to the configured webhook URL.

        Args:
            event_type: Dot-separated event name, e.g. ``"test.completed"``.
            payload: Arbitrary data to include in the event body.

        Returns:
            True if the server responded with a 2xx status code, False
            otherwise.
        """
        import requests  # local import to keep top-level import surface small

        body_dict = self._build_payload(event_type, payload)
        body_bytes = json.dumps(body_dict, separators=(",", ":")).encode("utf-8")

        headers: dict[str, str] = {"Content-Type": "application/json"}
        if self._secret:
            headers["X-Signature"] = self._sign(body_bytes)

        response = requests.post(
            self._webhook_url,
            data=body_bytes,
            headers=headers,
        )
        return 200 <= response.status_code < 300

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _sign(self, body: bytes) -> str:
        """Compute the HMAC-SHA256 hex digest of *body* using the stored secret.

        Args:
            body: Raw request body bytes to sign.

        Returns:
            Lowercase hex string of the HMAC-SHA256 digest.

        Raises:
            ValueError: If no secret is configured.
        """
        if not self._secret:
            raise ValueError("Cannot sign payload: no secret configured")
        return hmac.new(
            self._secret.encode("utf-8"),
            body,
            hashlib.sha256,
        ).hexdigest()

    def _build_payload(
        self,
        event_type: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        """Wrap *payload* with standard envelope fields.

        Args:
            event_type: Event type string.
            payload: Caller-supplied event data.

        Returns:
            Envelope dict with ``event_type``, ``timestamp``, ``version``,
            and all keys from *payload*.
        """
        return {
            "event_type": event_type,
            "timestamp": time.time(),
            "version": self._PAYLOAD_VERSION,
            **payload,
        }
