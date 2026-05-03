"""Ed25519 public key constant for license verification.

WHY a module-level constant: avoids filesystem reads at import time;
the public key is not secret — it's safe to embed in client code.

PLACEHOLDER: this hex constant is a generated test key. The Chairman
generates the real keypair using:
    python -m simdrive.license.keypair generate
and replaces SIMDRIVE_PUBLIC_KEY_HEX with the output public key hex.
The private key goes into the Railway env var SIMDRIVE_LICENSE_PRIVATE_KEY.
"""
from __future__ import annotations

from nacl.signing import VerifyKey

from simdrive.license.keypair import verify_key_from_hex


# ---- PLACEHOLDER: replace with real public key before launch ----
# Generated via: python -m simdrive.license.keypair generate
SIMDRIVE_PUBLIC_KEY_HEX: str = (
    "0000000000000000000000000000000000000000000000000000000000000001"
)
# ---- END PLACEHOLDER ----


def get_public_key() -> VerifyKey:
    """Return the embedded Ed25519 verify key for license validation.

    The cloud API and client both call this to get the authoritative key.
    In production the constant above is replaced with the real 64-char hex.
    """
    return verify_key_from_hex(SIMDRIVE_PUBLIC_KEY_HEX)
