"""Ed25519 license key validator with clock-skew defense and offline grace.

Design decisions:
- `last_known_server_time` is passed in (not fetched here) so the caller
  controls when network calls happen; validator is always pure/testable.
- Clock skew: use max(time.time(), last_known_server_time) as the effective
  "now" to defend against local clock backdating attacks.
- Offline grace: if last_known_server_time is None (fully offline), allow
  7-day window past expiry before hard-rejecting. This matches the spec.
"""
from __future__ import annotations

import base64
import json
import time
from typing import Any, Optional

from nacl.exceptions import BadSignatureError
from nacl.signing import VerifyKey

from simdrive.license.errors import (
    license_expired,
    license_invalid,
    license_offline_grace_exhausted,
)

OFFLINE_GRACE_SECONDS: int = 7 * 86400  # 7 days


def _b64url_decode(s: str) -> bytes:
    """Decode URL-safe base64 with automatic padding."""
    s += "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s)


def validate_license(
    key: str,
    *,
    verify_key: VerifyKey,
    last_known_server_time: Optional[int] = None,
) -> dict[str, Any]:
    """Validate a license key and return its payload dict.

    Parameters
    ----------
    key:
        The license key string in ``<payload_b64url>.<signature_b64url>`` format.
    verify_key:
        The Ed25519 VerifyKey (public key) to verify against.
    last_known_server_time:
        Unix timestamp from the most recent /v1/licenses/status response.
        Pass None when offline. Used for clock-skew defense.

    Returns
    -------
    dict
        Validated payload with keys: tier, seats, customer_email, issued_at, expires_at.

    Raises
    ------
    LicenseError(code="license_invalid")
        Signature verification failed, malformed key, or payload not parseable.
    LicenseError(code="license_expired")
        Key has expired (respecting clock-skew defense and grace window).
    LicenseError(code="license_offline_grace_exhausted")
        Key is expired and the 7-day offline grace period has elapsed.
    """
    # ---- 1. Parse structure ----
    if not key or "." not in key:
        raise license_invalid("key must be <payload_b64url>.<signature_b64url>")

    parts = key.split(".")
    if len(parts) != 2:
        raise license_invalid(
            f"key must have exactly one '.'; got {len(parts) - 1}"
        )

    payload_b64, sig_b64 = parts

    # ---- 2. Verify Ed25519 signature ----
    try:
        sig_bytes = _b64url_decode(sig_b64)
        payload_message = payload_b64.encode("ascii")
        verify_key.verify(payload_message, sig_bytes)
    except (BadSignatureError, Exception) as exc:
        raise license_invalid(f"signature verification failed: {exc}") from exc

    # ---- 3. Decode payload ----
    try:
        payload_bytes = _b64url_decode(payload_b64)
        payload: dict[str, Any] = json.loads(payload_bytes)
    except Exception as exc:
        raise license_invalid(f"payload decode failed: {exc}") from exc

    # ---- 4. Expiry check with clock-skew defense ----
    expires_at: int = payload.get("expires_at", 0)
    effective_now = _effective_now(last_known_server_time)

    if effective_now > expires_at:
        # Check offline grace window
        if last_known_server_time is None:
            # Offline: allow 7-day grace from expiry
            grace_deadline = expires_at + OFFLINE_GRACE_SECONDS
            if time.time() > grace_deadline:
                raise license_offline_grace_exhausted(expires_at)
            # Within grace window — pass through
        else:
            # Online: server time is authoritative, no grace
            raise license_expired(expires_at)

    return payload


def _effective_now(last_known_server_time: Optional[int]) -> float:
    """Return the effective current time for expiry checks.

    WHY: A user can backdating their local clock to extend a license.
    Using max(local, server_time) closes that attack vector whenever
    we have a recent server timestamp.
    """
    local_time = time.time()
    if last_known_server_time is not None:
        return max(local_time, float(last_known_server_time))
    return local_time
