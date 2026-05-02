"""Tests for trial.py and entitlement.py modules.

TDD: written before implementation.
Covers: tier resolution, seats enforcement, trial state, entitlement checks.
"""
from __future__ import annotations

import json
import time
import tempfile
from pathlib import Path
import pytest


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def keypair():
    from specterqa_ios.license.keypair import generate_keypair
    return generate_keypair()


@pytest.fixture
def license_file(tmp_path: Path):
    """Return a path to a temp license file dir."""
    return tmp_path / "license.json"


def _write_license_file(path: Path, sk, tier: str = "pro", seats: int = 4,
                        email: str = "test@example.com",
                        expires_offset: int = 86400 * 14) -> str:
    """Write a valid license key to the given path and return the key."""
    from specterqa_ios.license.signer import sign_license
    from specterqa_ios.license.keypair import verify_key_to_hex
    _, vk = sk
    now = int(time.time())
    # issued 30 days ago so negative expires_offset creates valid-structure expired keys
    issued_at = now - (86400 * 30)
    expires_at = now + expires_offset
    if expires_at <= issued_at:
        issued_at = expires_at - 1
    key = sign_license(
        signing_key=sk[0],
        tier=tier,
        seats=seats,
        customer_email=email,
        issued_at=issued_at,
        expires_at=expires_at,
    )
    data = {
        "license_key": key,
        "public_key_hex": verify_key_to_hex(vk),
        "installed_at": now,
        "last_server_check": None,
        "last_known_server_time": None,
    }
    path.write_text(json.dumps(data))
    return key


# ---------------------------------------------------------------------------
# Entitlement tests
# ---------------------------------------------------------------------------

class TestEntitlement:

    def test_pro_tier_entitlement(self, keypair, license_file: Path) -> None:
        from specterqa_ios.license.entitlement import check_entitlement
        _write_license_file(license_file, keypair, tier="pro", seats=4)
        ent = check_entitlement(license_path=license_file, verify_key=keypair[1])
        assert ent.tier == "pro"
        assert ent.seats == 4

    def test_solo_tier_entitlement(self, keypair, license_file: Path) -> None:
        from specterqa_ios.license.entitlement import check_entitlement
        _write_license_file(license_file, keypair, tier="solo", seats=1)
        ent = check_entitlement(license_path=license_file, verify_key=keypair[1])
        assert ent.tier == "solo"
        assert ent.seats == 1

    def test_team_tier_seats(self, keypair, license_file: Path) -> None:
        from specterqa_ios.license.entitlement import check_entitlement
        _write_license_file(license_file, keypair, tier="team", seats=5)
        ent = check_entitlement(license_path=license_file, verify_key=keypair[1])
        assert ent.seats == 5

    def test_expires_at_present(self, keypair, license_file: Path) -> None:
        from specterqa_ios.license.entitlement import check_entitlement
        _write_license_file(license_file, keypair)
        ent = check_entitlement(license_path=license_file, verify_key=keypair[1])
        assert ent.expires_at > time.time()

    def test_missing_license_file_raises(self, keypair, tmp_path: Path) -> None:
        from specterqa_ios.license.entitlement import check_entitlement
        from specterqa_ios.license.errors import LicenseError
        missing = tmp_path / "nonexistent.json"
        with pytest.raises(LicenseError) as exc_info:
            check_entitlement(license_path=missing, verify_key=keypair[1])
        assert exc_info.value.code == "license_not_found"

    def test_expired_license_raises(self, keypair, license_file: Path) -> None:
        """A license that expired > 7 days ago raises even offline (grace exhausted)."""
        from specterqa_ios.license.entitlement import check_entitlement
        from specterqa_ios.license.errors import LicenseError
        # Expire 8 days ago — past the 7-day offline grace window
        _write_license_file(license_file, keypair, expires_offset=-(86400 * 8))
        with pytest.raises(LicenseError) as exc_info:
            check_entitlement(license_path=license_file, verify_key=keypair[1])
        assert exc_info.value.code in ("license_expired", "license_offline_grace_exhausted")

    def test_trial_tier_accepted(self, keypair, license_file: Path) -> None:
        from specterqa_ios.license.entitlement import check_entitlement
        _write_license_file(license_file, keypair, tier="trial", seats=1)
        ent = check_entitlement(license_path=license_file, verify_key=keypair[1])
        assert ent.tier == "trial"

    def test_entitlement_customer_email_present(self, keypair, license_file: Path) -> None:
        from specterqa_ios.license.entitlement import check_entitlement
        _write_license_file(license_file, keypair, email="customer@example.com")
        ent = check_entitlement(license_path=license_file, verify_key=keypair[1])
        assert ent.customer_email == "customer@example.com"


# ---------------------------------------------------------------------------
# Trial module tests
# ---------------------------------------------------------------------------

class TestTrialState:

    def test_start_trial_creates_license_file(self, keypair, tmp_path: Path) -> None:
        from specterqa_ios.license.trial import start_trial
        lf = tmp_path / "license.json"
        now = int(time.time())
        key = start_trial(
            email="new@example.com",
            license_key="mock.key",
            expires_at=now + 86400 * 14,
            license_path=lf,
        )
        assert lf.exists()
        data = json.loads(lf.read_text())
        assert data["license_key"] == "mock.key"

    def test_start_trial_returns_stored_key(self, keypair, tmp_path: Path) -> None:
        from specterqa_ios.license.trial import start_trial
        lf = tmp_path / "license.json"
        now = int(time.time())
        result = start_trial(
            email="a@b.com",
            license_key="token.sig",
            expires_at=now + 86400,
            license_path=lf,
        )
        assert result == "token.sig"

    def test_load_license_data_roundtrip(self, keypair, tmp_path: Path) -> None:
        from specterqa_ios.license.trial import start_trial, load_license_data
        lf = tmp_path / "license.json"
        now = int(time.time())
        exp = now + 86400 * 14
        start_trial(
            email="round@trip.com",
            license_key="abc.def",
            expires_at=exp,
            license_path=lf,
        )
        data = load_license_data(lf)
        assert data["license_key"] == "abc.def"
