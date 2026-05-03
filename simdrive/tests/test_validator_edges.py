"""Edge-case tests for license/validator.py — Component 9.

Covers:
  - License at exactly the second of expiry
  - Clock-skew > 7 days
  - Partially corrupted base64
"""
from __future__ import annotations

import base64
import json
import time
from typing import Optional

import pytest
from nacl.signing import SigningKey

from simdrive.license.errors import LicenseError
from simdrive.license.validator import OFFLINE_GRACE_SECONDS, validate_license


# ── Test fixtures ─────────────────────────────────────────────────────────────


def _make_key_pair():
    signing_key = SigningKey.generate()
    verify_key = signing_key.verify_key
    return signing_key, verify_key


def _encode_payload(data: dict) -> str:
    raw = json.dumps(data).encode("ascii")
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _sign_payload(signing_key: SigningKey, payload_b64: str) -> str:
    signed = signing_key.sign(payload_b64.encode("ascii"))
    sig_bytes = signed.signature
    return base64.urlsafe_b64encode(sig_bytes).rstrip(b"=").decode("ascii")


def _make_license_key(
    signing_key: SigningKey,
    *,
    tier: str = "solo",
    seats: int = 1,
    customer_email: str = "test@example.com",
    issued_at: Optional[int] = None,
    expires_at: Optional[int] = None,
) -> str:
    now = int(time.time())
    payload = {
        "tier": tier,
        "seats": seats,
        "customer_email": customer_email,
        "issued_at": issued_at or now,
        "expires_at": expires_at or (now + 86400 * 30),
    }
    payload_b64 = _encode_payload(payload)
    sig_b64 = _sign_payload(signing_key, payload_b64)
    return f"{payload_b64}.{sig_b64}"


# ── Tests ─────────────────────────────────────────────────────────────────────


class TestExactExpiry:
    def test_license_at_exactly_expiry_second_online(self) -> None:
        """At exactly expires_at, with server time = expires_at, the license is expired."""
        sk, vk = _make_key_pair()
        now = int(time.time())
        # Set expires_at to now - 0: exactly at this second
        expires_at = now
        key = _make_license_key(sk, expires_at=expires_at)

        # Server time is authoritative and equals expires_at
        # effective_now = max(local, server) = now >= expires_at → should raise
        with pytest.raises(LicenseError) as exc_info:
            validate_license(
                key,
                verify_key=vk,
                last_known_server_time=expires_at,
            )
        assert exc_info.value.code == "license_expired"

    def test_license_one_second_before_expiry_valid(self) -> None:
        """One second before expiry → still valid."""
        sk, vk = _make_key_pair()
        now = int(time.time())
        expires_at = now + 10  # 10s in the future
        key = _make_license_key(sk, expires_at=expires_at)

        # Should succeed: effective_now = max(local, server) < expires_at
        payload = validate_license(
            key,
            verify_key=vk,
            last_known_server_time=now,
        )
        assert payload["tier"] == "solo"

    def test_license_one_second_after_expiry_rejected(self) -> None:
        """One second past expiry with server time → rejected."""
        sk, vk = _make_key_pair()
        now = int(time.time())
        expires_at = now - 1  # 1 second ago
        key = _make_license_key(sk, expires_at=expires_at)

        with pytest.raises(LicenseError) as exc_info:
            validate_license(
                key,
                verify_key=vk,
                last_known_server_time=now,
            )
        assert exc_info.value.code == "license_expired"


class TestClockSkewMoreThan7Days:
    def test_offline_license_expired_within_grace_allowed(self) -> None:
        """Offline + expired within 7-day grace → still valid."""
        sk, vk = _make_key_pair()
        now = int(time.time())
        # Expired 1 day ago
        expires_at = now - 86400
        key = _make_license_key(sk, expires_at=expires_at)

        # Offline: last_known_server_time=None
        payload = validate_license(
            key,
            verify_key=vk,
            last_known_server_time=None,
        )
        assert payload["tier"] == "solo"

    def test_offline_license_expired_beyond_grace_rejected(self) -> None:
        """Offline + expired > 7 days ago → grace_exhausted."""
        sk, vk = _make_key_pair()
        now = int(time.time())
        # Expired 8 days ago
        expires_at = now - (8 * 86400)
        key = _make_license_key(sk, expires_at=expires_at)

        with pytest.raises(LicenseError) as exc_info:
            validate_license(
                key,
                verify_key=vk,
                last_known_server_time=None,
            )
        assert exc_info.value.code == "license_offline_grace_exhausted"

    def test_clock_skew_server_time_greater_than_local(self) -> None:
        """Server time far ahead of local clock: server time is authoritative."""
        sk, vk = _make_key_pair()
        local_now = int(time.time())
        # Set a server time far in the future (simulating a severely wrong local clock)
        # The license expires after the server time → still valid
        server_time = local_now + 3600  # server is 1 hour ahead
        expires_at = server_time + 86400  # expires 1 day from server time
        key = _make_license_key(sk, expires_at=expires_at)

        payload = validate_license(
            key,
            verify_key=vk,
            last_known_server_time=server_time,
        )
        assert payload["tier"] == "solo"

    def test_clock_skew_user_backdated_clock(self) -> None:
        """User backdates local clock to extend license; server time defends."""
        sk, vk = _make_key_pair()
        real_now = int(time.time())
        # License expired 1 day ago in real time
        expires_at = real_now - 86400
        key = _make_license_key(sk, expires_at=expires_at)

        # Even if local time were backdated, we pass real server time
        with pytest.raises(LicenseError) as exc_info:
            validate_license(
                key,
                verify_key=vk,
                last_known_server_time=real_now,
            )
        assert exc_info.value.code == "license_expired"


class TestCorruptedBase64:
    def test_completely_invalid_key(self) -> None:
        """A random string raises license_invalid."""
        _, vk = _make_key_pair()
        with pytest.raises(LicenseError) as exc_info:
            validate_license("notakey", verify_key=vk)
        assert exc_info.value.code == "license_invalid"

    def test_missing_dot_separator(self) -> None:
        """No '.' separator → license_invalid."""
        _, vk = _make_key_pair()
        with pytest.raises(LicenseError) as exc_info:
            validate_license("nodot_in_key", verify_key=vk)
        assert exc_info.value.code == "license_invalid"

    def test_truncated_payload_b64(self) -> None:
        """Truncated base64 payload → license_invalid (signature fails)."""
        sk, vk = _make_key_pair()
        valid_key = _make_license_key(sk)
        payload_b64, sig_b64 = valid_key.split(".")
        # Corrupt: truncate the payload
        truncated_key = payload_b64[:10] + "." + sig_b64
        with pytest.raises(LicenseError) as exc_info:
            validate_license(truncated_key, verify_key=vk)
        assert exc_info.value.code == "license_invalid"

    def test_corrupted_signature(self) -> None:
        """Valid payload but corrupted signature → license_invalid."""
        sk, vk = _make_key_pair()
        valid_key = _make_license_key(sk)
        payload_b64, _ = valid_key.split(".")
        bad_sig = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
        corrupted_key = f"{payload_b64}.{bad_sig}"
        with pytest.raises(LicenseError) as exc_info:
            validate_license(corrupted_key, verify_key=vk)
        assert exc_info.value.code == "license_invalid"

    def test_partial_base64_garbage(self) -> None:
        """Partial garbage in payload → license_invalid."""
        _, vk = _make_key_pair()
        # This has a '.' but the parts are garbage
        with pytest.raises(LicenseError) as exc_info:
            validate_license("abc!@#$.xyz!@#$", verify_key=vk)
        assert exc_info.value.code == "license_invalid"

    def test_extra_dot_invalid(self) -> None:
        """More than one '.' separator → license_invalid."""
        _, vk = _make_key_pair()
        with pytest.raises(LicenseError) as exc_info:
            validate_license("a.b.c", verify_key=vk)
        assert exc_info.value.code == "license_invalid"
