"""Ed25519 keypair generation and hex serialization helpers.

WHY pynacl: well-audited, maintained binding to libsodium; Ed25519 is the
recommended algorithm for offline-verifiable signed tokens (no key-escrow,
no symmetric secret distribution needed on the client).

CLI usage:
    python -m simdrive.license.keypair generate

This writes the *private* (signing) key hex to stdout only — never to disk
by default. The operator pipes it to a secrets manager / Railway env var.
The public (verify) key goes into public_key.py as a hex constant.
"""
from __future__ import annotations

import sys
from typing import Tuple

from nacl.signing import SigningKey, VerifyKey


def generate_keypair() -> Tuple[SigningKey, VerifyKey]:
    """Generate a fresh Ed25519 keypair.

    Returns (signing_key, verify_key). The signing key is the private key
    and must NEVER be embedded in client code. The verify key is the public
    key and is safe to embed in client code.
    """
    sk = SigningKey.generate()
    vk: VerifyKey = sk.verify_key
    return sk, vk


def signing_key_to_hex(sk: SigningKey) -> str:
    """Serialize signing key (32-byte seed) to lowercase hex string."""
    return bytes(sk).hex()


def signing_key_from_hex(hex_str: str) -> SigningKey:
    """Deserialize signing key from hex string.

    Raises ValueError if hex_str is not valid hex.
    """
    try:
        raw = bytes.fromhex(hex_str)
    except ValueError as exc:
        raise ValueError(f"hex: invalid hex string for signing key: {exc}") from exc
    return SigningKey(raw)


def verify_key_to_hex(vk: VerifyKey) -> str:
    """Serialize verify key (32-byte public key) to lowercase hex string."""
    return bytes(vk).hex()


def verify_key_from_hex(hex_str: str) -> VerifyKey:
    """Deserialize verify key from hex string.

    Raises ValueError if hex_str is not valid hex.
    """
    try:
        raw = bytes.fromhex(hex_str)
    except ValueError as exc:
        raise ValueError(f"hex: invalid hex string for verify key: {exc}") from exc
    return VerifyKey(raw)


if __name__ == "__main__":
    # CLI: `python -m simdrive.license.keypair generate`
    # Writes private key hex to stdout. Public key hex to stderr.
    if len(sys.argv) < 2 or sys.argv[1] != "generate":
        print("Usage: python -m simdrive.license.keypair generate", file=sys.stderr)
        sys.exit(1)

    sk, vk = generate_keypair()
    private_hex = signing_key_to_hex(sk)
    public_hex = verify_key_to_hex(vk)

    print(private_hex)  # stdout: pipe to secrets manager
    print("# Public key hex (embed in public_key.py):", file=sys.stderr)
    print(f"# {public_hex}", file=sys.stderr)
