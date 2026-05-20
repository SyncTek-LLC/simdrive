"""Ed25519 public key constants for license verification.

WHY a module-level constant: avoids filesystem reads at import time;
the public key is not secret — it's safe to embed in client code.

PRODUCTION: TRUSTED_PUBLIC_KEYS below is the live license-signing
public key set for SimDrive paid tiers. The first entry is the
*current* production key; additional entries support smooth rotation
where licenses already issued under an older key keep validating until
their natural expiry.

DEV KEY NOTE:
The DEV_VERIFY_KEY_HEX / DEV_SIGNING_KEY_HEX pair is intentionally
embedded in the package — it lets anyone self-issue a local offline
trial (simdrive trial start --email x --offline-dev) without a server.
The validator enforces that dev-key-signed licenses MUST have
subject="dev-trial"; the dev key cannot forge enterprise / pro licenses.

KEY ROTATION (INIT-2026-549 W-F):
TRUSTED_PUBLIC_KEYS is a list of (key_id, hex_pubkey) tuples. When a
license is signed, the issuer SHOULD embed the matching ``key_id`` in
the payload so the validator picks the correct key without trying each
one in turn. Licenses without ``key_id`` are validated against the
first trusted key (backwards compatible with legacy issued licenses).

To rotate: prepend the new (key_id, hex) tuple to TRUSTED_PUBLIC_KEYS,
keep the old one for the duration of the longest-lived in-the-wild
license, then remove it on the next release.
"""
from __future__ import annotations

from typing import List, Tuple

from nacl.signing import SigningKey, VerifyKey

from simdrive.license.keypair import verify_key_from_hex, signing_key_from_hex


# Ed25519 license-signing public key for SimDrive 1.0 paid tiers.
# Generated 2026-05-18 (rotated from 2026-05-02 placeholder — no licenses
# were ever issued under the prior key, so rotation has no customer impact).
# Private key lives in BusinessAtlas vault: `simdrive/license_signing_private_key`
# (scope: DeployAtlas). Retrieve for Cloudflare Worker deploy via:
#   cd /Users/atlas/BusinessAtlas
#   .venv/bin/python v2/ba vault get --service simdrive --key license_signing_private_key
# Then: wrangler secret put LICENSE_SIGNING_PRIVATE_KEY (paste hex at prompt).
# DO NOT regenerate without coordinated key rotation — every issued
# license becomes invalid the instant this constant changes.
SIMDRIVE_PUBLIC_KEY_HEX: str = (
    "6de89dc03064c3fd50a916d08e2d4a68a52082c804b4eceaa0be241c247749c6"
)

# Stable id for the current production key. Bump the date suffix on
# every coordinated rotation. Tools and licenses reference keys by this
# id so the validator can find the right pubkey without trial-and-error.
PROD_KEY_ID: str = "prod-2026-05"


# Ordered list of (key_id, hex_pubkey) tuples. The FIRST entry is the
# active production key — that's the one used for legacy licenses
# without a ``key_id`` field and the one fresh signers should pick.
# To add a rotation key: prepend its (key_id, hex) tuple and keep the
# prior entries around until all licenses signed under them have
# expired (typically: one full year past the rotation).
TRUSTED_PUBLIC_KEYS: List[Tuple[str, str]] = [
    (PROD_KEY_ID, SIMDRIVE_PUBLIC_KEY_HEX),
]


# Ed25519 dev-only keypair for offline self-issued trial licenses.
# Generated 2026-05-04. Both keys are intentionally embedded — the dev
# signing key only issues licenses with subject="dev-trial" and the
# validator rejects any non-dev-trial license signed with this key.
DEV_VERIFY_KEY_HEX: str = (
    "4ce4c377cddbcbbba91933341ac80c5d0fac152f258c9ab2a8074928e811cdd1"
)
DEV_SIGNING_KEY_HEX: str = (
    "186c4d05326d0e7561bf9fbd6e65fed132037dbf60668de74c681f523e0809a3"
)


def get_public_key() -> VerifyKey:
    """Return the embedded Ed25519 verify key for license validation.

    The cloud API and client both call this to get the authoritative key.
    In production the constant above is replaced with the real 64-char hex.

    Returns the FIRST trusted key (the current production key). Callers
    that need to support multiple keys for rotation should use
    :func:`get_trusted_verify_keys` instead.
    """
    return verify_key_from_hex(TRUSTED_PUBLIC_KEYS[0][1])


def get_trusted_verify_keys() -> List[Tuple[str, VerifyKey]]:
    """Return all trusted (key_id, VerifyKey) tuples in priority order.

    The first entry is the active production key — the validator falls
    back to it for legacy licenses that do not carry a ``key_id`` field.
    """
    return [(kid, verify_key_from_hex(hex_str)) for kid, hex_str in TRUSTED_PUBLIC_KEYS]


def get_dev_verify_key() -> VerifyKey:
    """Return the embedded Ed25519 dev verify key for offline-dev trial validation."""
    return verify_key_from_hex(DEV_VERIFY_KEY_HEX)


def get_dev_signing_key() -> SigningKey:
    """Return the embedded Ed25519 dev signing key for offline-dev trial issuance.

    Intentionally public — the dev key can only sign licenses with
    subject='dev-trial'; the validator rejects anything else.
    """
    return signing_key_from_hex(DEV_SIGNING_KEY_HEX)
