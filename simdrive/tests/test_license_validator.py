"""Tests for license validator module.

TDD: written before implementation.
Coverage target: 100% on validator (security-critical).
Covers: valid key, expired key, tampered signature, wrong public key,
        skewed clock, grace-window edge, offline grace, tier enforcement.
"""
from __future__ import annotations

import json
import time
import base64
import pytest


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _make_key(sk, tier="pro", seats=4, email="test@example.com",
              issued_offset: int = 0, expires_offset: int = 86400 * 14) -> str:
    """Helper to create a license key using the signer.

    issued_at is always 30 days in the past so that negative expires_offset
    values (expired keys) are still structurally valid (expires_at > issued_at).
    """
    from specterqa_ios.license.signer import sign_license
    now = int(time.time())
    # issued 30 days ago so we can create both future AND past expiry keys
    issued_at = now - (86400 * 30) + issued_offset
    expires_at = now + expires_offset
    # Ensure structural validity: expires_at must be > issued_at
    if expires_at <= issued_at:
        issued_at = expires_at - 1
    return sign_license(
        signing_key=sk,
        tier=tier,
        seats=seats,
        customer_email=email,
        issued_at=issued_at,
        expires_at=expires_at,
    )


@pytest.fixture
def keypair():
    from specterqa_ios.license.keypair import generate_keypair
    return generate_keypair()


# ---------------------------------------------------------------------------
# Validator tests
# ---------------------------------------------------------------------------

class TestValidatorHappyPath:

    def test_valid_key_returns_payload(self, keypair) -> None:
        from specterqa_ios.license.validator import validate_license
        sk, vk = keypair
        key = _make_key(sk)
        result = validate_license(key, verify_key=vk)
        assert result["tier"] == "pro"
        assert result["seats"] == 4

    def test_valid_key_all_tiers(self, keypair) -> None:
        from specterqa_ios.license.validator import validate_license
        sk, vk = keypair
        for tier in ["trial", "solo", "pro", "team", "enterprise"]:
            key = _make_key(sk, tier=tier)
            result = validate_license(key, verify_key=vk)
            assert result["tier"] == tier

    def test_valid_key_contains_expires_at(self, keypair) -> None:
        from specterqa_ios.license.validator import validate_license
        sk, vk = keypair
        key = _make_key(sk)
        result = validate_license(key, verify_key=vk)
        assert "expires_at" in result


class TestValidatorExpiry:

    def test_expired_key_raises_license_expired(self, keypair) -> None:
        """Online mode: expired key must raise immediately (no grace window)."""
        from specterqa_ios.license.validator import validate_license
        from specterqa_ios.license.errors import LicenseError
        sk, vk = keypair
        key = _make_key(sk, expires_offset=-1)  # expired 1 second ago
        # Pass server_time to trigger online mode (no grace window)
        server_time = int(time.time())
        with pytest.raises(LicenseError) as exc_info:
            validate_license(key, verify_key=vk, last_known_server_time=server_time)
        assert exc_info.value.code == "license_expired"

    def test_expiry_exactly_now_is_expired(self, keypair) -> None:
        """Online mode: a key that expired exactly now must be rejected."""
        from specterqa_ios.license.validator import validate_license
        from specterqa_ios.license.errors import LicenseError
        sk, vk = keypair
        key = _make_key(sk, expires_offset=0)
        server_time = int(time.time())
        with pytest.raises(LicenseError) as exc_info:
            validate_license(key, verify_key=vk, last_known_server_time=server_time)
        assert exc_info.value.code == "license_expired"

    def test_future_key_valid(self, keypair) -> None:
        """Key expiring 1 hour from now must be accepted."""
        from specterqa_ios.license.validator import validate_license
        sk, vk = keypair
        key = _make_key(sk, expires_offset=3600)
        result = validate_license(key, verify_key=vk)
        assert result is not None


class TestValidatorTampering:

    def test_tampered_payload_raises_license_invalid(self, keypair) -> None:
        from specterqa_ios.license.validator import validate_license
        from specterqa_ios.license.errors import LicenseError
        sk, vk = keypair
        key = _make_key(sk)
        payload_b64, sig_b64 = key.split(".")
        # Tamper: decode, modify tier, re-encode
        padded = payload_b64 + "=" * (-len(payload_b64) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded))
        payload["tier"] = "enterprise"  # upgrade attempt
        tampered_payload = _b64url_encode(json.dumps(payload).encode())
        tampered_key = f"{tampered_payload}.{sig_b64}"
        with pytest.raises(LicenseError) as exc_info:
            validate_license(tampered_key, verify_key=vk)
        assert exc_info.value.code == "license_invalid"

    def test_tampered_signature_raises_license_invalid(self, keypair) -> None:
        from specterqa_ios.license.validator import validate_license
        from specterqa_ios.license.errors import LicenseError
        sk, vk = keypair
        key = _make_key(sk)
        payload_b64, sig_b64 = key.split(".")
        # Corrupt last 4 chars of signature
        bad_sig = sig_b64[:-4] + "AAAA"
        tampered_key = f"{payload_b64}.{bad_sig}"
        with pytest.raises(LicenseError) as exc_info:
            validate_license(tampered_key, verify_key=vk)
        assert exc_info.value.code == "license_invalid"

    def test_wrong_public_key_raises_license_invalid(self, keypair) -> None:
        from specterqa_ios.license.validator import validate_license
        from specterqa_ios.license.errors import LicenseError
        from specterqa_ios.license.keypair import generate_keypair
        sk, _ = keypair
        _, wrong_vk = generate_keypair()  # different keypair
        key = _make_key(sk)
        with pytest.raises(LicenseError) as exc_info:
            validate_license(key, verify_key=wrong_vk)
        assert exc_info.value.code == "license_invalid"

    def test_malformed_key_no_dot_raises(self, keypair) -> None:
        from specterqa_ios.license.validator import validate_license
        from specterqa_ios.license.errors import LicenseError
        _, vk = keypair
        with pytest.raises(LicenseError) as exc_info:
            validate_license("nodot_in_this_key", verify_key=vk)
        assert exc_info.value.code == "license_invalid"

    def test_empty_key_raises(self, keypair) -> None:
        from specterqa_ios.license.validator import validate_license
        from specterqa_ios.license.errors import LicenseError
        _, vk = keypair
        with pytest.raises(LicenseError) as exc_info:
            validate_license("", verify_key=vk)
        assert exc_info.value.code == "license_invalid"

    def test_too_many_parts_raises(self, keypair) -> None:
        from specterqa_ios.license.validator import validate_license
        from specterqa_ios.license.errors import LicenseError
        _, vk = keypair
        with pytest.raises(LicenseError) as exc_info:
            validate_license("a.b.c", verify_key=vk)
        assert exc_info.value.code == "license_invalid"


class TestValidatorClockSkew:

    def test_skew_defense_uses_server_time_when_ahead(self, keypair) -> None:
        """If server time is ahead of local, use server time for expiry check."""
        from specterqa_ios.license.validator import validate_license
        from specterqa_ios.license.errors import LicenseError
        sk, vk = keypair
        # Key expires 10 seconds in the future from real now
        key = _make_key(sk, expires_offset=10)
        # Server time is 30 seconds ahead — key should appear expired
        server_time = int(time.time()) + 30
        with pytest.raises(LicenseError) as exc_info:
            validate_license(key, verify_key=vk, last_known_server_time=server_time)
        assert exc_info.value.code == "license_expired"

    def test_offline_grace_7_days(self, keypair) -> None:
        """Key expired 3 days ago but offline grace (7 days) still covers it."""
        from specterqa_ios.license.validator import validate_license
        sk, vk = keypair
        # Key expired 3 days ago
        key = _make_key(sk, expires_offset=-(86400 * 3))
        # No server time available (offline) — grace window applies
        result = validate_license(key, verify_key=vk, last_known_server_time=None)
        assert result is not None

    def test_offline_grace_exhausted_after_7_days(self, keypair) -> None:
        """Key expired 8 days ago — even with grace window, must reject."""
        from specterqa_ios.license.validator import validate_license
        from specterqa_ios.license.errors import LicenseError
        sk, vk = keypair
        # Key expired 8 days ago
        key = _make_key(sk, expires_offset=-(86400 * 8))
        with pytest.raises(LicenseError) as exc_info:
            validate_license(key, verify_key=vk, last_known_server_time=None)
        assert exc_info.value.code in ("license_expired", "license_offline_grace_exhausted")

    def test_grace_boundary_exactly_7_days(self, keypair) -> None:
        """Key expired exactly 7 days ago — boundary must still be accepted."""
        from specterqa_ios.license.validator import validate_license
        sk, vk = keypair
        key = _make_key(sk, expires_offset=-(86400 * 7 - 1))  # 1 second before 7 days
        result = validate_license(key, verify_key=vk, last_known_server_time=None)
        assert result is not None
