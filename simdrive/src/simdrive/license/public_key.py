"""Ed25519 public key constants for license verification.

WHY a module-level constant: avoids filesystem reads at import time;
the public key is not secret — it's safe to embed in client code.

PLACEHOLDER: this hex constant is a generated test key. The Chairman
generates the real keypair using:
    python -m simdrive.license.keypair generate
and replaces SIMDRIVE_PUBLIC_KEY_HEX with the output public key hex.
The private key goes into the Railway env var SIMDRIVE_LICENSE_PRIVATE_KEY.

DEV KEY NOTE:
The DEV_VERIFY_KEY_HEX / DEV_SIGNING_KEY_HEX pair is intentionally
embedded in the package — it lets anyone self-issue a local offline
trial (simdrive trial start --email x --offline-dev) without a server.
The validator enforces that dev-key-signed licenses MUST have
subject="dev-trial"; the dev key cannot forge enterprise / pro licenses.
"""
from __future__ import annotations

from nacl.signing import SigningKey, VerifyKey

from simdrive.license.keypair import verify_key_from_hex, signing_key_from_hex


# Ed25519 license-signing public key for SimDrive 1.0.
# Generated 2026-05-02; private key held in Chairman's 1Password under
# "SimDrive license signing" and configured as SIMDRIVE_LICENSE_PRIVATE_KEY
# env var on the Railway license server. DO NOT regenerate without
# coordinated key rotation — every issued license becomes invalid.
SIMDRIVE_PUBLIC_KEY_HEX: str = (
    "8d282e49db135b6e67dd16133bb57c436685e06c3582d28091134c4c15ce462c"
)

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
    """
    return verify_key_from_hex(SIMDRIVE_PUBLIC_KEY_HEX)


def get_dev_verify_key() -> VerifyKey:
    """Return the embedded Ed25519 dev verify key for offline-dev trial validation."""
    return verify_key_from_hex(DEV_VERIFY_KEY_HEX)


def get_dev_signing_key() -> SigningKey:
    """Return the embedded Ed25519 dev signing key for offline-dev trial issuance.

    Intentionally public — the dev key can only sign licenses with
    subject='dev-trial'; the validator rejects anything else.
    """
    return signing_key_from_hex(DEV_SIGNING_KEY_HEX)
