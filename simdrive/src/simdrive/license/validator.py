"""Ed25519 license key validator with clock-skew defense and offline grace.

Design decisions:
- `last_known_server_time` is passed in (not fetched here) so the caller
  controls when network calls happen; validator is always pure/testable.
- Clock skew: use max(time.time(), last_known_server_time) as the effective
  "now" to defend against local clock backdating attacks.
- Offline grace: if last_known_server_time is None (fully offline), allow
  7-day window past expiry before hard-rejecting. This matches the spec.
- Dev key: licenses signed with DEV_SIGNING_KEY are accepted but MUST have
  subject="dev-trial"; the dev key cannot forge enterprise/pro licenses.
- Multi-key rotation: payloads may include a ``key_id``
  field naming which entry in TRUSTED_PUBLIC_KEYS signed them. Payloads
  without ``key_id`` fall back to the first trusted key (backwards compat
  with every license issued before this change).
"""
from __future__ import annotations

import base64
import json
import time
from typing import Any, Iterable, Optional, Tuple

from nacl.signing import VerifyKey

from simdrive.license.errors import (
    license_clock_skew_detected,
    license_expired,
    license_invalid,
    license_key_rotation_required,
    license_offline_grace_exhausted,
)
from simdrive.observability.logger import get_logger

log = get_logger("simdrive.license.validator")

OFFLINE_GRACE_SECONDS: int = 7 * 86400  # 7 days

# Clock-skew thresholds for the offline grace check.
# - CLOCK_BACK_TOLERANCE_SECONDS: how far the local clock may drift behind
#   last_known_server_time before we refuse to grant grace (suggests
#   backdating or a clock reset).
# - SERVER_CHECK_FRESHNESS_SECONDS: if last_known_server_time is older than
#   this much wall-clock time, refuse grace until a fresh cloud check runs.
CLOCK_BACK_TOLERANCE_SECONDS: int = 6 * 3600       # 6 hours
SERVER_CHECK_FRESHNESS_SECONDS: int = 30 * 86400   # 30 days

# Subject value required for dev-key-signed licenses.
_DEV_TRIAL_SUBJECT: str = "dev-trial"


def _b64url_decode(s: str) -> bytes:
    """Decode URL-safe base64 with automatic padding."""
    s += "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s)


def _try_verify(verify_key: VerifyKey, payload_b64: str, sig_bytes: bytes) -> bool:
    """Return True if the signature is valid for the given key, False otherwise."""
    try:
        verify_key.verify(payload_b64.encode("ascii"), sig_bytes)
        return True
    except Exception:
        return False


def _peek_payload(payload_b64: str) -> dict[str, Any]:
    """Decode the payload portion of a license key without verifying it.

    Used to read the optional ``key_id`` field before signature
    verification; the caller still verifies the signature afterwards
    so this peek can never be a trust boundary.
    """
    try:
        return json.loads(_b64url_decode(payload_b64))
    except Exception:
        return {}


def _select_verify_key(
    payload_b64: str,
    *,
    verify_key: Optional[VerifyKey],
    trusted_keys: Optional[Iterable[Tuple[str, VerifyKey]]],
) -> VerifyKey:
    """Pick the verify key to check the payload signature against.

    Resolution order:
      1. Caller passed an explicit ``verify_key`` AND no trusted_keys list →
         use it. This is the legacy single-key path and preserves all
         existing test/production callers.
      2. Caller passed ``trusted_keys`` → peek at the payload's ``key_id``
         and look it up. If the payload has no ``key_id``, return the first
         trusted key (backwards-compat with pre-rotation licenses). If the
         payload's ``key_id`` is unknown, raise KeyRotationError.
      3. Neither set → fall back to the embedded TRUSTED_PUBLIC_KEYS list
         (this is what production clients want by default).
    """
    if verify_key is not None and trusted_keys is None:
        return verify_key

    if trusted_keys is None:
        # Lazy import: avoid a circular import at module load.
        from simdrive.license.public_key import get_trusted_verify_keys
        trusted_keys = get_trusted_verify_keys()

    trusted_list = list(trusted_keys)
    if not trusted_list:
        raise license_invalid("no trusted public keys configured")

    payload_peek = _peek_payload(payload_b64)
    payload_key_id = payload_peek.get("key_id")

    if payload_key_id is None:
        # Legacy license — pin to the first trusted key.
        return trusted_list[0][1]

    for kid, vk in trusted_list:
        if kid == payload_key_id:
            return vk

    # Unknown key_id — surface a rotation-specific error so the user
    # sees "upgrade simdrive" instead of "signature invalid".
    raise license_key_rotation_required(
        payload_key_id,
        trusted_ids=[kid for kid, _ in trusted_list],
    )


def validate_license(
    key: str,
    *,
    verify_key: Optional[VerifyKey] = None,
    trusted_keys: Optional[Iterable[Tuple[str, VerifyKey]]] = None,
    last_known_server_time: Optional[int] = None,
) -> dict[str, Any]:
    """Validate a license key and return its payload dict.

    Accepts licenses signed by either:
    - One of the trusted production verify keys (selected via the payload's
      ``key_id`` field or the legacy ``verify_key`` parameter).
    - The embedded dev verify key (DEV_VERIFY_KEY_HEX, ONLY for subject="dev-trial").

    Parameters
    ----------
    key:
        The license key string in ``<payload_b64url>.<signature_b64url>`` format.
    verify_key:
        Optional single Ed25519 VerifyKey to verify against. Use this for
        tests where you generate a one-off keypair. Mutually exclusive with
        the rotation path: when set without ``trusted_keys`` the validator
        skips key_id lookup entirely (backwards compat).
    trusted_keys:
        Optional iterable of ``(key_id, VerifyKey)`` tuples to pick from
        based on the payload's ``key_id``. When omitted *and* ``verify_key``
        is also None, falls back to ``public_key.get_trusted_verify_keys()``.
    last_known_server_time:
        Unix timestamp from the most recent /v1/licenses/status response.
        Pass None when offline. Used for clock-skew defense.

    Returns
    -------
    dict
        Validated payload with keys: tier, seats, customer_email,
        issued_at, expires_at, and optionally key_id.

    Raises
    ------
    LicenseError(code="license_invalid")
        Signature verification failed, malformed key, or payload not parseable.
    LicenseError(code="license_expired")
        Key has expired (respecting clock-skew defense and grace window).
    LicenseError(code="license_offline_grace_exhausted")
        Key is expired and the 7-day offline grace period has elapsed.
    KeyRotationError(code="license_key_rotation_required")
        Payload's ``key_id`` is unknown to this client.
    ClockSkewError(code="license_clock_skew_detected")
        System clock cannot be trusted for the offline-grace evaluation.
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

    # ---- 2. Verify Ed25519 signature (resolve prod key by key_id) ----
    try:
        sig_bytes = _b64url_decode(sig_b64)
    except Exception as exc:
        raise license_invalid(f"signature base64 decode failed: {exc}") from exc

    selected_verify_key = _select_verify_key(
        payload_b64,
        verify_key=verify_key,
        trusted_keys=trusted_keys,
    )

    signed_by_prod = _try_verify(selected_verify_key, payload_b64, sig_bytes)
    signed_by_dev = False

    if not signed_by_prod:
        # Try the embedded dev key as a fallback
        try:
            from simdrive.license.public_key import get_dev_verify_key
            dev_vk = get_dev_verify_key()
            signed_by_dev = _try_verify(dev_vk, payload_b64, sig_bytes)
        except Exception:
            pass

    if not signed_by_prod and not signed_by_dev:
        raise license_invalid("signature verification failed: invalid signature")

    # ---- 3. Decode payload ----
    try:
        payload_bytes = _b64url_decode(payload_b64)
        payload: dict[str, Any] = json.loads(payload_bytes)
    except Exception as exc:
        raise license_invalid(f"payload decode failed: {exc}") from exc

    # ---- 4. Dev-key subject enforcement ----
    if signed_by_dev and not signed_by_prod:
        subject = payload.get("subject", "")
        if subject != _DEV_TRIAL_SUBJECT:
            raise license_invalid(
                f"dev-key-signed license must have subject={_DEV_TRIAL_SUBJECT!r}; "
                f"got {subject!r}. Dev key cannot forge non-trial licenses."
            )
        log.debug("license validated via dev key (offline trial)", extra={"subject": subject})

    # ---- 5. Expiry check with clock-skew defense ----
    expires_at: int = payload.get("expires_at", 0)
    effective_now = _effective_now(last_known_server_time)

    if effective_now > expires_at:
        # Check offline grace window
        if last_known_server_time is None:
            # Offline: allow 7-day grace from expiry
            grace_deadline = expires_at + OFFLINE_GRACE_SECONDS
            if time.time() > grace_deadline:
                log.warning(
                    "license offline grace exhausted",
                    extra={"expires_at": expires_at, "tier": payload.get("tier")},
                )
                raise license_offline_grace_exhausted(expires_at)
            # Within grace window — pass through
            log.debug(
                "license in offline grace window",
                extra={"expires_at": expires_at, "tier": payload.get("tier")},
            )
        else:
            # Online: server time is authoritative, no grace
            log.warning(
                "license expired",
                extra={"expires_at": expires_at, "tier": payload.get("tier")},
            )
            raise license_expired(expires_at)
    else:
        log.debug(
            "license valid",
            extra={
                "tier": payload.get("tier"),
                "expires_at": expires_at,
                "customer_email": payload.get("customer_email", ""),
            },
        )

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


def check_clock_skew_for_grace(
    *,
    last_known_server_time: Optional[int],
    system_clock: Optional[int] = None,
) -> None:
    """Refuse offline grace when the local clock is untrustworthy.

    Two refusal conditions:
      1. The system clock is more than ``CLOCK_BACK_TOLERANCE_SECONDS``
         (6h) BEHIND the last known server time — strongly suggests
         backdating to extend a license.
      2. The system clock is more than ``SERVER_CHECK_FRESHNESS_SECONDS``
         (30d) AHEAD of the last known server time — the user has been
         offline so long that local time is no longer a credible
         "current time" signal; force a fresh cloud check before
         granting any grace.

    If ``last_known_server_time`` is None the validator has no anchor to
    compare against and simply returns — the older 7-day-since-expiry
    grace check in :func:`validate_license` is the only protection in
    that case (see ``OFFLINE_GRACE_SECONDS``).

    Parameters
    ----------
    last_known_server_time:
        Unix timestamp recorded after the last successful
        GET /v1/licenses/status response, or None if no cloud check has
        ever happened (fresh install on an offline machine).
    system_clock:
        Override for the system clock — defaults to ``time.time()``.
        Tests pass an explicit value to simulate skew.

    Raises
    ------
    ClockSkewError(code="license_clock_skew_detected")
        Either skew condition triggered.
    """
    if last_known_server_time is None:
        return

    now = int(system_clock if system_clock is not None else time.time())
    drift = now - int(last_known_server_time)

    if drift < -CLOCK_BACK_TOLERANCE_SECONDS:
        # Local clock is significantly behind last known server time.
        raise license_clock_skew_detected(
            f"system clock is {-drift}s behind last known server time "
            f"(tolerance: {CLOCK_BACK_TOLERANCE_SECONDS}s)",
            system_clock=now,
            last_known_server_time=int(last_known_server_time),
        )

    if drift > SERVER_CHECK_FRESHNESS_SECONDS:
        # The last server check is too old to anchor the grace window.
        raise license_clock_skew_detected(
            f"no cloud check in {drift}s (max: {SERVER_CHECK_FRESHNESS_SECONDS}s); "
            "force a fresh license status refresh",
            system_clock=now,
            last_known_server_time=int(last_known_server_time),
        )
