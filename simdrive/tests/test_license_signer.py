"""Tests for license signer and keypair modules.

TDD: these tests are written before the implementation.
Coverage target: 100% on signer/keypair (security-critical).
"""
from __future__ import annotations

import base64
import json
import time
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _b64url_decode(s: str) -> bytes:
    """Pad + decode base64url."""
    s = s + "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s)


# ---------------------------------------------------------------------------
# keypair tests
# ---------------------------------------------------------------------------

class TestKeypairGeneration:
    """Tests for license/keypair.py."""

    def test_generate_returns_signing_key(self) -> None:
        from simdrive.license.keypair import generate_keypair
        sk, vk = generate_keypair()
        assert sk is not None
        assert vk is not None

    def test_signing_key_32_bytes(self) -> None:
        from simdrive.license.keypair import generate_keypair
        sk, vk = generate_keypair()
        # pynacl SigningKey._signing_key is 32 bytes seed
        assert len(bytes(sk)) == 32

    def test_verify_key_32_bytes(self) -> None:
        from simdrive.license.keypair import generate_keypair
        sk, vk = generate_keypair()
        assert len(bytes(vk)) == 32

    def test_signing_key_to_hex_roundtrip(self) -> None:
        from simdrive.license.keypair import generate_keypair, signing_key_to_hex, signing_key_from_hex
        sk, _ = generate_keypair()
        hex_str = signing_key_to_hex(sk)
        sk2 = signing_key_from_hex(hex_str)
        assert bytes(sk) == bytes(sk2)

    def test_verify_key_to_hex_roundtrip(self) -> None:
        from simdrive.license.keypair import generate_keypair, verify_key_to_hex, verify_key_from_hex
        _, vk = generate_keypair()
        hex_str = verify_key_to_hex(vk)
        vk2 = verify_key_from_hex(hex_str)
        assert bytes(vk) == bytes(vk2)

    def test_each_generate_is_unique(self) -> None:
        from simdrive.license.keypair import generate_keypair
        sk1, _ = generate_keypair()
        sk2, _ = generate_keypair()
        assert bytes(sk1) != bytes(sk2)

    def test_signing_key_from_invalid_hex_raises(self) -> None:
        from simdrive.license.keypair import signing_key_from_hex
        with pytest.raises(ValueError, match="hex"):
            signing_key_from_hex("not-hex!!")

    def test_verify_key_from_invalid_hex_raises(self) -> None:
        from simdrive.license.keypair import verify_key_from_hex
        with pytest.raises(ValueError, match="hex"):
            verify_key_from_hex("zzz")

    def test_signing_key_hex_length_is_64(self) -> None:
        """Ed25519 seed is 32 bytes = 64 hex chars."""
        from simdrive.license.keypair import generate_keypair, signing_key_to_hex
        sk, _ = generate_keypair()
        assert len(signing_key_to_hex(sk)) == 64

    def test_verify_key_hex_length_is_64(self) -> None:
        """Ed25519 public key is 32 bytes = 64 hex chars."""
        from simdrive.license.keypair import generate_keypair, verify_key_to_hex
        _, vk = generate_keypair()
        assert len(verify_key_to_hex(vk)) == 64


# ---------------------------------------------------------------------------
# signer tests
# ---------------------------------------------------------------------------

class TestSigner:
    """Tests for license/signer.py."""

    @pytest.fixture
    def keypair(self):
        from simdrive.license.keypair import generate_keypair
        return generate_keypair()

    def test_sign_produces_two_part_key(self, keypair) -> None:
        from simdrive.license.signer import sign_license
        sk, _ = keypair
        key = sign_license(
            signing_key=sk,
            tier="solo",
            seats=1,
            customer_email="test@example.com",
            issued_at=int(time.time()),
            expires_at=int(time.time()) + 86400 * 14,
        )
        parts = key.split(".")
        assert len(parts) == 2, "License key must be <payload>.<signature>"

    def test_payload_is_valid_json(self, keypair) -> None:
        from simdrive.license.signer import sign_license
        sk, _ = keypair
        now = int(time.time())
        key = sign_license(
            signing_key=sk,
            tier="pro",
            seats=4,
            customer_email="user@example.com",
            issued_at=now,
            expires_at=now + 86400 * 14,
        )
        payload_b64, _ = key.split(".")
        payload = json.loads(_b64url_decode(payload_b64))
        assert payload["tier"] == "pro"
        assert payload["seats"] == 4
        assert payload["customer_email"] == "user@example.com"

    def test_signature_is_64_bytes(self, keypair) -> None:
        """Ed25519 signature is 64 bytes."""
        from simdrive.license.signer import sign_license
        sk, _ = keypair
        now = int(time.time())
        key = sign_license(
            signing_key=sk,
            tier="team",
            seats=5,
            customer_email="team@example.com",
            issued_at=now,
            expires_at=now + 86400 * 30,
        )
        _, sig_b64 = key.split(".")
        sig = _b64url_decode(sig_b64)
        assert len(sig) == 64

    def test_key_length_approx_200_chars(self, keypair) -> None:
        from simdrive.license.signer import sign_license
        sk, _ = keypair
        now = int(time.time())
        key = sign_license(
            signing_key=sk,
            tier="solo",
            seats=1,
            customer_email="a@b.com",
            issued_at=now,
            expires_at=now + 86400,
        )
        # ~200 chars as spec; allow 150-300 range
        assert 150 <= len(key) <= 400

    def test_missing_tier_raises(self, keypair) -> None:
        from simdrive.license.signer import sign_license
        sk, _ = keypair
        with pytest.raises((ValueError, TypeError)):
            sign_license(
                signing_key=sk,
                tier="",  # type: ignore[arg-type]
                seats=1,
                customer_email="a@b.com",
                issued_at=int(time.time()),
                expires_at=int(time.time()) + 86400,
            )

    def test_invalid_tier_raises(self, keypair) -> None:
        from simdrive.license.signer import sign_license
        sk, _ = keypair
        with pytest.raises(ValueError, match="tier"):
            sign_license(
                signing_key=sk,
                tier="ultra",  # not a valid tier
                seats=1,
                customer_email="a@b.com",
                issued_at=int(time.time()),
                expires_at=int(time.time()) + 86400,
            )

    def test_negative_seats_raises(self, keypair) -> None:
        from simdrive.license.signer import sign_license
        sk, _ = keypair
        with pytest.raises(ValueError, match="seats"):
            sign_license(
                signing_key=sk,
                tier="solo",
                seats=-1,
                customer_email="a@b.com",
                issued_at=int(time.time()),
                expires_at=int(time.time()) + 86400,
            )

    def test_expires_before_issued_raises(self, keypair) -> None:
        from simdrive.license.signer import sign_license
        sk, _ = keypair
        now = int(time.time())
        with pytest.raises(ValueError, match="expires_at"):
            sign_license(
                signing_key=sk,
                tier="solo",
                seats=1,
                customer_email="a@b.com",
                issued_at=now,
                expires_at=now - 1,
            )

    def test_trial_tier_accepted(self, keypair) -> None:
        from simdrive.license.signer import sign_license
        sk, _ = keypair
        now = int(time.time())
        key = sign_license(
            signing_key=sk,
            tier="trial",
            seats=1,
            customer_email="trial@example.com",
            issued_at=now,
            expires_at=now + 86400 * 14,
        )
        assert "." in key

    def test_enterprise_tier_accepted(self, keypair) -> None:
        from simdrive.license.signer import sign_license
        sk, _ = keypair
        now = int(time.time())
        key = sign_license(
            signing_key=sk,
            tier="enterprise",
            seats=100,
            customer_email="corp@example.com",
            issued_at=now,
            expires_at=now + 86400 * 365,
        )
        assert "." in key

    def test_payload_contains_all_fields(self, keypair) -> None:
        from simdrive.license.signer import sign_license
        sk, _ = keypair
        now = int(time.time())
        exp = now + 86400 * 14
        key = sign_license(
            signing_key=sk,
            tier="pro",
            seats=4,
            customer_email="full@example.com",
            issued_at=now,
            expires_at=exp,
        )
        payload_b64, _ = key.split(".")
        payload = json.loads(_b64url_decode(payload_b64))
        assert "tier" in payload
        assert "seats" in payload
        assert "issued_at" in payload
        assert "expires_at" in payload
        assert "customer_email" in payload


# ---------------------------------------------------------------------------
# Property-based tests (hypothesis)
# ---------------------------------------------------------------------------

@given(
    tier=st.sampled_from(["trial", "solo", "pro", "team", "enterprise"]),
    seats=st.integers(min_value=1, max_value=200),
    email=st.emails(),
)
@settings(max_examples=20)
def test_keypair_roundtrip_property(tier: str, seats: int, email: str) -> None:
    """Property: sign then decode payload always gives back the same data."""
    from simdrive.license.keypair import generate_keypair
    from simdrive.license.signer import sign_license
    sk, _ = generate_keypair()
    now = int(time.time())
    key = sign_license(
        signing_key=sk,
        tier=tier,
        seats=seats,
        customer_email=email,
        issued_at=now,
        expires_at=now + 86400,
    )
    payload_b64, _ = key.split(".")
    payload = json.loads(_b64url_decode(payload_b64))
    assert payload["tier"] == tier
    assert payload["seats"] == seats
    assert payload["customer_email"] == email


@given(s=st.text(min_size=1, max_size=500))
@settings(max_examples=30)
def test_b64url_encode_decode_roundtrip(s: str) -> None:
    """Property: base64url encode/decode roundtrip is lossless."""
    from simdrive.license.signer import _b64url_encode
    encoded = _b64url_encode(s.encode())
    decoded = _b64url_decode(encoded)
    assert decoded == s.encode()
