"""Tests for multi-key license validator support.

The validator must:
  - Accept a payload signed under the FIRST trusted key when no key_id
    is present (legacy / pre-rotation licenses).
  - Accept a payload signed under any trusted key when the matching
    key_id is embedded in the payload.
  - Reject a payload whose key_id is unknown with KeyRotationError
    (NOT plain "license_invalid" — the message must point the user at
    `pip install -U simdrive`).
  - Reject a payload signed under an untrusted key when key_id is absent
    (signature verification still fails — legacy behaviour preserved).
"""
from __future__ import annotations

import time

import pytest


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def two_trusted_keys():
    """Return ((kid1, sk1, vk1), (kid2, sk2, vk2)) and the corresponding
    list of (key_id, VerifyKey) tuples for the validator.
    """
    from simdrive.license.keypair import generate_keypair

    sk1, vk1 = generate_keypair()
    sk2, vk2 = generate_keypair()

    kid1 = "prod-2026-05"
    kid2 = "prod-2026-12"

    trusted_keys = [(kid1, vk1), (kid2, vk2)]
    return (kid1, sk1, vk1), (kid2, sk2, vk2), trusted_keys


def _sign(sk, *, key_id=None, expires_offset=86400 * 14, email="test@example.com"):
    from simdrive.license.signer import sign_license

    now = int(time.time())
    return sign_license(
        signing_key=sk,
        tier="pro",
        seats=4,
        customer_email=email,
        issued_at=now - 86400,
        expires_at=now + expires_offset,
        key_id=key_id,
    )


# ---------------------------------------------------------------------------
# Multi-key validation
# ---------------------------------------------------------------------------


class TestKeyRotation:
    def test_legacy_payload_without_key_id_uses_first_trusted_key(
        self, two_trusted_keys
    ) -> None:
        """A payload with no key_id must validate against the first trusted key."""
        from simdrive.license.validator import validate_license

        (kid1, sk1, vk1), _, trusted = two_trusted_keys
        key = _sign(sk1, key_id=None)
        payload = validate_license(key, trusted_keys=trusted)
        assert payload["tier"] == "pro"
        assert "key_id" not in payload

    def test_key_id_routes_to_matching_trusted_key(self, two_trusted_keys) -> None:
        """When key_id is present, the validator picks the matching key."""
        from simdrive.license.validator import validate_license

        (kid1, sk1, _), (kid2, sk2, _), trusted = two_trusted_keys

        key1 = _sign(sk1, key_id=kid1)
        payload1 = validate_license(key1, trusted_keys=trusted)
        assert payload1["key_id"] == kid1

        key2 = _sign(sk2, key_id=kid2)
        payload2 = validate_license(key2, trusted_keys=trusted)
        assert payload2["key_id"] == kid2

    def test_unknown_key_id_raises_key_rotation_required(
        self, two_trusted_keys
    ) -> None:
        """A payload referencing a key_id we don't trust must raise KeyRotationError."""
        from simdrive.license.errors import KeyRotationError
        from simdrive.license.validator import validate_license

        _, _, trusted = two_trusted_keys

        # Sign with a third, untrusted key but declare a key_id pointing
        # at it. The validator must surface the rotation-required error
        # rather than letting the signature check fall through.
        from simdrive.license.keypair import generate_keypair

        sk3, _ = generate_keypair()
        key = _sign(sk3, key_id="prod-2027-01")

        with pytest.raises(KeyRotationError) as exc_info:
            validate_license(key, trusted_keys=trusted)
        assert exc_info.value.code == "license_key_rotation_required"
        details = exc_info.value.details
        assert details["key_id"] == "prod-2027-01"
        assert "prod-2026-05" in details["trusted_key_ids"]

    def test_untrusted_signature_without_key_id_rejected_as_invalid(
        self, two_trusted_keys
    ) -> None:
        """Legacy payload signed by a non-trusted key fails as 'license_invalid'."""
        from simdrive.license.errors import LicenseError
        from simdrive.license.keypair import generate_keypair
        from simdrive.license.validator import validate_license

        _, _, trusted = two_trusted_keys
        sk_other, _ = generate_keypair()
        key = _sign(sk_other, key_id=None)

        with pytest.raises(LicenseError) as exc_info:
            validate_license(key, trusted_keys=trusted)
        # Must NOT be the rotation error (no key_id means we can't tell
        # the user to upgrade) — falls back to the generic signature error.
        assert exc_info.value.code == "license_invalid"

    def test_default_uses_embedded_trusted_keys_when_no_args(self) -> None:
        """validate_license() with neither verify_key nor trusted_keys uses TRUSTED_PUBLIC_KEYS."""
        from simdrive.license.public_key import get_trusted_verify_keys

        trusted = get_trusted_verify_keys()
        assert len(trusted) >= 1
        # The first entry must be the active prod key id.
        assert trusted[0][0].startswith("prod-")

    def test_public_key_list_shape(self) -> None:
        """TRUSTED_PUBLIC_KEYS is a list of (str, str) tuples."""
        from simdrive.license.public_key import TRUSTED_PUBLIC_KEYS

        assert isinstance(TRUSTED_PUBLIC_KEYS, list)
        assert TRUSTED_PUBLIC_KEYS, "must have at least one trusted key"
        for entry in TRUSTED_PUBLIC_KEYS:
            assert isinstance(entry, tuple)
            assert len(entry) == 2
            kid, hexstr = entry
            assert isinstance(kid, str) and kid
            assert isinstance(hexstr, str) and len(hexstr) == 64

    def test_legacy_single_verify_key_path_still_works(self, two_trusted_keys) -> None:
        """The pre-rotation API (verify_key=...) keeps working unchanged."""
        from simdrive.license.validator import validate_license

        (_, sk1, vk1), _, _ = two_trusted_keys
        key = _sign(sk1, key_id=None)
        payload = validate_license(key, verify_key=vk1)
        assert payload["tier"] == "pro"

    def test_signer_payload_includes_key_id_when_passed(self) -> None:
        """sign_license embeds key_id in the payload only when caller supplies one."""
        import base64
        import json

        from simdrive.license.keypair import generate_keypair
        from simdrive.license.signer import sign_license

        sk, _ = generate_keypair()
        now = int(time.time())
        key = sign_license(
            signing_key=sk,
            tier="pro",
            seats=1,
            customer_email="e@e",
            issued_at=now,
            expires_at=now + 86400,
            key_id="prod-2026-12",
        )
        payload_b64 = key.split(".")[0]
        payload_b64 += "=" * (-len(payload_b64) % 4)
        payload = json.loads(base64.urlsafe_b64decode(payload_b64))
        assert payload["key_id"] == "prod-2026-12"

    def test_signer_payload_omits_key_id_when_not_passed(self) -> None:
        """sign_license() without key_id keeps the legacy payload shape exactly."""
        import base64
        import json

        from simdrive.license.keypair import generate_keypair
        from simdrive.license.signer import sign_license

        sk, _ = generate_keypair()
        now = int(time.time())
        key = sign_license(
            signing_key=sk,
            tier="pro",
            seats=1,
            customer_email="e@e",
            issued_at=now,
            expires_at=now + 86400,
        )
        payload_b64 = key.split(".")[0]
        payload_b64 += "=" * (-len(payload_b64) % 4)
        payload = json.loads(base64.urlsafe_b64decode(payload_b64))
        assert "key_id" not in payload
