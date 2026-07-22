"""Security regression tests: dev-key-signed licenses can never escalate.

Context
-------
The private dev SIGNING key ships inside the distributed package
(public_key.DEV_SIGNING_KEY_HEX) so the client can self-issue offline
trials at runtime (license/cli.py::_issue_dev_license). That means anyone
who unpacks the wheel can produce a VALID dev-key signature over ANY
payload they like — the signature is not a trust boundary.

Historically the validator only checked ``subject == "dev-trial"`` for
dev-key licenses and read ``tier``/``seats``/``expires_at`` straight from
the (attacker-controlled) payload. Exploit: sign
``subject="dev-trial", tier="enterprise", seats=999, expires_at=<far future>``
with the shipped dev key -> unlimited enterprise access.

These tests forge dev-key-signed payloads directly (mirroring the on-wire
format) and assert the validator now HARD-REJECTS any escalation while
still accepting a genuine offline trial. Each test fails against the
pre-fix validator and passes after ``_enforce_dev_trial_limits``.
"""
from __future__ import annotations

import base64
import json
import time

import pytest

from simdrive.license.errors import LicenseError
from simdrive.license.public_key import get_dev_signing_key
from simdrive.license.validator import (
    _DEV_TRIAL_MAX_LIFETIME_SECONDS,
    _DEV_TRIAL_MAX_SEATS,
    validate_license,
)


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _dev_sign(payload: dict) -> str:
    """Sign an ARBITRARY payload dict with the shipped dev key.

    This is exactly what an attacker can do after unpacking the wheel: it
    reproduces license/cli.py::_issue_dev_license but with attacker-chosen
    fields. Returns a ``<payload_b64url>.<sig_b64url>`` license string.
    """
    sk = get_dev_signing_key()
    payload_b64 = _b64url(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    sig = sk.sign(payload_b64.encode("ascii")).signature
    return f"{payload_b64}.{_b64url(bytes(sig))}"


def _legit_trial_payload(*, seats: int = 1, lifetime: int = 14 * 86400) -> dict:
    now = int(time.time())
    return {
        "subject": "dev-trial",
        "tier": "trial",
        "seats": seats,
        "customer_email": "dev@example.com",
        "issued_at": now,
        "expires_at": now + lifetime,
    }


# ---------------------------------------------------------------------------
# 1. Forged enterprise tier -> HARD REJECT
# ---------------------------------------------------------------------------
class TestDevKeyTierEscalationRejected:
    def test_dev_key_enterprise_is_rejected(self) -> None:
        """subject=dev-trial but tier=enterprise must be refused (the core exploit)."""
        payload = _legit_trial_payload()
        payload["tier"] = "enterprise"
        payload["seats"] = 999
        payload["expires_at"] = int(time.time()) + 3650 * 86400  # ~10 years
        key = _dev_sign(payload)
        with pytest.raises(LicenseError) as exc:
            # No verify_key: exercise the real prod-fallback-to-dev path.
            validate_license(key, last_known_server_time=None)
        assert exc.value.code == "license_invalid"

    @pytest.mark.parametrize("tier", ["pro", "team", "solo", "enterprise", "bogus"])
    def test_dev_key_any_non_trial_tier_rejected(self, tier: str) -> None:
        payload = _legit_trial_payload()
        payload["tier"] = tier
        key = _dev_sign(payload)
        with pytest.raises(LicenseError) as exc:
            validate_license(key, last_known_server_time=None)
        assert exc.value.code == "license_invalid"


# ---------------------------------------------------------------------------
# 2. Forged seat count -> rejected
# ---------------------------------------------------------------------------
class TestDevKeySeatEscalationRejected:
    def test_dev_key_trial_with_999_seats_rejected(self) -> None:
        payload = _legit_trial_payload(seats=999)
        key = _dev_sign(payload)
        with pytest.raises(LicenseError) as exc:
            validate_license(key, last_known_server_time=None)
        assert exc.value.code == "license_invalid"

    def test_dev_key_trial_at_seat_cap_ok(self) -> None:
        """Seats exactly at the trial cap must still validate (boundary)."""
        payload = _legit_trial_payload(seats=_DEV_TRIAL_MAX_SEATS)
        key = _dev_sign(payload)
        result = validate_license(key, last_known_server_time=None)
        assert result["seats"] == _DEV_TRIAL_MAX_SEATS

    def test_dev_key_trial_over_seat_cap_rejected(self) -> None:
        payload = _legit_trial_payload(seats=_DEV_TRIAL_MAX_SEATS + 1)
        key = _dev_sign(payload)
        with pytest.raises(LicenseError) as exc:
            validate_license(key, last_known_server_time=None)
        assert exc.value.code == "license_invalid"


# ---------------------------------------------------------------------------
# 3. Far-future expiry -> rejected
# ---------------------------------------------------------------------------
class TestDevKeyExpiryEscalationRejected:
    def test_dev_key_far_future_expiry_rejected(self) -> None:
        """A trial payload with a multi-year expiry must be refused."""
        payload = _legit_trial_payload(lifetime=3650 * 86400)  # ~10 years
        key = _dev_sign(payload)
        with pytest.raises(LicenseError) as exc:
            validate_license(key, last_known_server_time=None)
        assert exc.value.code == "license_invalid"

    def test_dev_key_expiry_just_over_cap_rejected(self) -> None:
        payload = _legit_trial_payload(
            lifetime=_DEV_TRIAL_MAX_LIFETIME_SECONDS + 5 * 86400
        )
        key = _dev_sign(payload)
        with pytest.raises(LicenseError) as exc:
            validate_license(key, last_known_server_time=None)
        assert exc.value.code == "license_invalid"


# ---------------------------------------------------------------------------
# 4. A LEGIT dev trial still validates (don't break offline trials)
# ---------------------------------------------------------------------------
class TestLegitDevTrialStillWorks:
    def test_legit_offline_dev_trial_validates(self) -> None:
        """Exactly what license/cli.py issues: subject=dev-trial, tier=trial,
        seats=1, 14-day expiry — must still validate through the real path."""
        payload = _legit_trial_payload()
        key = _dev_sign(payload)
        result = validate_license(key, last_known_server_time=None)
        assert result["tier"] == "trial"
        assert result["seats"] == 1
        assert result["subject"] == "dev-trial"

    def test_dev_key_missing_subject_still_rejected(self) -> None:
        """Regression guard for the original subject check — a dev-key payload
        without subject=dev-trial is refused."""
        payload = _legit_trial_payload()
        del payload["subject"]
        key = _dev_sign(payload)
        with pytest.raises(LicenseError) as exc:
            validate_license(key, last_known_server_time=None)
        assert exc.value.code == "license_invalid"


# ---------------------------------------------------------------------------
# 5. Prod-key-signed licenses are UNAFFECTED by the dev-key clamp
# ---------------------------------------------------------------------------
class TestProdKeyLicensesUnaffected:
    @pytest.fixture
    def keypair(self):
        from simdrive.license.keypair import generate_keypair
        return generate_keypair()

    @pytest.mark.parametrize("tier", ["trial", "solo", "pro", "team", "enterprise"])
    def test_prod_signed_all_tiers_validate(self, keypair, tier: str) -> None:
        """A genuinely prod-signed license (verified via the passed verify_key)
        must validate for every tier, including enterprise with many seats —
        the dev-key clamp must never touch the prod path."""
        from simdrive.license.signer import sign_license
        sk, vk = keypair
        now = int(time.time())
        key = sign_license(
            signing_key=sk,
            tier=tier,
            seats=999,
            customer_email="paid@example.com",
            issued_at=now - 86400,
            expires_at=now + 365 * 86400,  # 1 year — far past the dev cap
        )
        result = validate_license(key, verify_key=vk, last_known_server_time=None)
        assert result["tier"] == tier
        assert result["seats"] == 999
