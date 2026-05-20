"""Ed25519 license key signer.

Format: base64url(json(payload)) + "." + base64url(ed25519_signature)

WHY this format: self-contained, ~200 chars, user can paste into a terminal
without quoting issues (no +/= characters from base64url), payload is
transparent (decode to inspect tier/expiry without a server call).
"""
from __future__ import annotations

import base64
import json
from typing import Final

from nacl.signing import SigningKey

VALID_TIERS: Final[frozenset[str]] = frozenset(
    {"trial", "solo", "pro", "team", "enterprise"}
)


def _b64url_encode(data: bytes) -> str:
    """Encode bytes as URL-safe base64 without padding."""
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def sign_license(
    *,
    signing_key: SigningKey,
    tier: str,
    seats: int,
    customer_email: str,
    issued_at: int,
    expires_at: int,
    key_id: str | None = None,
) -> str:
    """Sign a license payload and return a compact key string.

    Parameters
    ----------
    signing_key:
        The Ed25519 SigningKey (private key). Must never be distributed to clients.
    tier:
        One of "trial", "solo", "pro", "team", "enterprise".
    seats:
        Number of seats (>= 1).
    customer_email:
        Email address of the licensee.
    issued_at:
        Unix timestamp of issue.
    expires_at:
        Unix timestamp of expiry. Must be > issued_at.
    key_id:
        Optional id of the public key the signature should be verified
        against (must match an entry in TRUSTED_PUBLIC_KEYS on the client).
        When omitted the payload does not carry a key_id and the client
        falls back to the first trusted key — this is the behaviour of
        every license issued before INIT-2026-549.

    Returns
    -------
    str
        License key in format: `<payload_b64url>.<signature_b64url>`
    """
    if tier not in VALID_TIERS:
        raise ValueError(
            f"tier {tier!r} is not valid; must be one of {sorted(VALID_TIERS)}"
        )
    if seats < 1:
        raise ValueError(f"seats must be >= 1; got {seats}")
    if expires_at <= issued_at:
        raise ValueError(
            f"expires_at ({expires_at}) must be after issued_at ({issued_at})"
        )

    payload: dict = {
        "tier": tier,
        "seats": seats,
        "customer_email": customer_email,
        "issued_at": issued_at,
        "expires_at": expires_at,
    }
    if key_id is not None:
        payload["key_id"] = key_id
    # Compact JSON with sorted keys for deterministic encoding
    payload_bytes = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    payload_b64 = _b64url_encode(payload_bytes)

    # Sign the base64url-encoded payload (not the raw JSON) — the verifier
    # receives the b64 string and must sign the same form to verify.
    signed = signing_key.sign(payload_b64.encode("ascii"))
    # signed.signature is the 64-byte Ed25519 signature
    sig_b64 = _b64url_encode(signed.signature)

    return f"{payload_b64}.{sig_b64}"
