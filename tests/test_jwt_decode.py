"""Tests for SEC-HIGH-005: _decode_jwt() and _check_offline_grace() correctness.

TDD suite — written before the implementation fix. These tests confirm:
  1. _decode_jwt() extracts payload fields from a JWT-shaped license key.
  2. _decode_jwt() returns {} gracefully for opaque (non-JWT) keys.
  3. _decode_jwt() handles base64 payloads with missing padding.
  4. _check_offline_grace() honours offline_exp / iat from the decoded payload.

Initiative: INIT-2026-525
Finding: SEC-HIGH-005
"""

from __future__ import annotations

import base64
import json
import time
from typing import Any, Dict
from unittest.mock import patch

import pytest

from specterqa.ios.license.validator import LicenseValidator


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_jwt(payload: Dict[str, Any], strip_padding: bool = False) -> str:
    """Build a minimal JWT-shaped string: header.payload.sig.

    Args:
        payload: Dict to encode as the middle (payload) segment.
        strip_padding: When True the payload b64 has its ``=`` stripped,
            simulating a real JWT token without padding (the common case).
    """
    header = base64.urlsafe_b64encode(b'{"alg":"RS256","typ":"JWT"}').rstrip(b"=").decode()
    raw = json.dumps(payload).encode()
    payload_b64 = base64.urlsafe_b64encode(raw)
    if strip_padding:
        payload_b64 = payload_b64.rstrip(b"=")
    payload_str = payload_b64.decode()
    # Signature is opaque — content doesn't matter for offline decode
    return f"{header}.{payload_str}.fakesig123"


# ---------------------------------------------------------------------------
# SEC-HIGH-005: _decode_jwt()
# ---------------------------------------------------------------------------


class TestDecodeJwt:
    """_decode_jwt() extracts the payload segment from a JWT-shaped key."""

    def test_decode_jwt_extracts_offline_exp(self):
        """_decode_jwt() returns offline_exp and iat when the key is JWT-shaped."""
        now = int(time.time())
        payload = {"offline_exp": now + 3600, "iat": now, "sub": "lic-abc"}
        token = _make_jwt(payload)

        validator = LicenseValidator(license_key=token)
        decoded = validator._decode_jwt()

        assert decoded.get("offline_exp") == now + 3600
        assert decoded.get("iat") == now

    def test_decode_jwt_returns_empty_on_malformed(self):
        """Non-JWT / opaque key (no dots) returns {} without raising."""
        opaque_key = "LIC-TEST-OPAQUE-0000"
        validator = LicenseValidator(license_key=opaque_key)
        decoded = validator._decode_jwt()
        assert decoded == {}

    def test_decode_jwt_handles_padding(self):
        """Payload missing '=' base64 padding still decodes correctly."""
        now = int(time.time())
        payload = {"offline_exp": now + 7200, "iat": now}
        # strip_padding=True simulates real JWTs which omit the '=' suffix
        token = _make_jwt(payload, strip_padding=True)

        # Sanity-check that the raw token really has no '=' padding chars
        middle_segment = token.split(".")[1]
        assert "=" not in middle_segment

        validator = LicenseValidator(license_key=token)
        decoded = validator._decode_jwt()

        assert decoded.get("offline_exp") == now + 7200

    def test_decode_jwt_returns_empty_on_invalid_json_payload(self):
        """Payload that is valid b64 but not valid JSON returns {}."""
        bad_payload_b64 = base64.urlsafe_b64encode(b"not-valid-json").rstrip(b"=").decode()
        header = base64.urlsafe_b64encode(b"{}").rstrip(b"=").decode()
        token = f"{header}.{bad_payload_b64}.fakesig"

        validator = LicenseValidator(license_key=token)
        decoded = validator._decode_jwt()
        assert decoded == {}

    def test_decode_jwt_single_segment_key(self):
        """A key with only one segment (no dots at all) returns {}."""
        validator = LicenseValidator(license_key="singlesegment")
        assert validator._decode_jwt() == {}


# ---------------------------------------------------------------------------
# SEC-HIGH-005: _check_offline_grace()
# ---------------------------------------------------------------------------


class TestCheckOfflineGrace:
    """_check_offline_grace() honours decoded payload fields."""

    def test_offline_grace_returns_true_with_future_offline_exp(self):
        """When offline_exp is in the future, grace window is open."""
        now = int(time.time())
        payload = {"offline_exp": now + 3600, "iat": now}
        token = _make_jwt(payload)

        validator = LicenseValidator(license_key=token)
        assert validator._check_offline_grace() is True

    def test_offline_grace_returns_false_with_expired_offline_exp(self):
        """When offline_exp is in the past, grace window is closed."""
        now = int(time.time())
        payload = {"offline_exp": now - 1, "iat": now - 80000}
        token = _make_jwt(payload)

        validator = LicenseValidator(license_key=token)
        assert validator._check_offline_grace() is False

    def test_offline_grace_falls_back_to_iat_within_72h(self):
        """When offline_exp is absent, falls back to iat + 72h grace window."""
        now = int(time.time())
        # iat = 1 hour ago → well within 72h window
        payload = {"iat": now - 3600}
        token = _make_jwt(payload)

        validator = LicenseValidator(license_key=token)
        assert validator._check_offline_grace() is True

    def test_offline_grace_falls_back_to_iat_outside_72h(self):
        """When iat is older than 72h and no offline_exp, grace returns False."""
        now = int(time.time())
        # iat = 73 hours ago → outside 72h window
        payload = {"iat": now - (73 * 3600)}
        token = _make_jwt(payload)

        validator = LicenseValidator(license_key=token)
        assert validator._check_offline_grace() is False

    def test_offline_grace_returns_false_for_opaque_key(self):
        """Opaque (non-JWT) key has no payload → _check_offline_grace() is False."""
        validator = LicenseValidator(license_key="LIC-OPAQUE-0000-ABCD")
        assert validator._check_offline_grace() is False
