"""Tests for the tightened offline-grace clock-skew check (INIT-2026-549 W-F).

The check refuses offline grace when:
  - the system clock moved BACKWARDS > 6h relative to last_known_server_time
    (backdating attack indicator), OR
  - the system clock is FORWARD > 30d past last_known_server_time
    (no cloud check in too long; local time is no longer credible).

It must NOT refuse grace when the drift is within the tolerance window
or when there is no last_known_server_time at all (fresh install).
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest


@pytest.fixture
def keypair():
    from simdrive.license.keypair import generate_keypair

    return generate_keypair()


def _write_license_file(
    path: Path,
    keypair,
    *,
    tier: str = "trial",
    expires_offset: int = -86400 * 3,
    last_known_server_time=None,
) -> str:
    """Write a license.json on disk with the given last_known_server_time."""
    from simdrive.license.signer import sign_license

    sk, _ = keypair
    now = int(time.time())
    issued_at = now - 86400 * 30
    expires_at = now + expires_offset
    if expires_at <= issued_at:
        issued_at = expires_at - 1
    key = sign_license(
        signing_key=sk,
        tier=tier,
        seats=1,
        customer_email="skew@example.com",
        issued_at=issued_at,
        expires_at=expires_at,
    )
    data = {
        "license_key": key,
        "installed_at": now,
        "last_server_check": None,
        "last_known_server_time": last_known_server_time,
    }
    path.write_text(json.dumps(data))
    return key


# ---------------------------------------------------------------------------
# Direct validator.check_clock_skew_for_grace tests
# ---------------------------------------------------------------------------


class TestCheckClockSkewForGrace:
    def test_no_anchor_is_a_passthrough(self) -> None:
        """last_known_server_time=None means there's no anchor to compare; no raise."""
        from simdrive.license.validator import check_clock_skew_for_grace

        check_clock_skew_for_grace(last_known_server_time=None)

    def test_clock_back_more_than_six_hours_raises(self) -> None:
        from simdrive.license.errors import ClockSkewError
        from simdrive.license.validator import check_clock_skew_for_grace

        server_t = 2_000_000_000
        # Clock pulled back 7 hours
        with pytest.raises(ClockSkewError) as exc_info:
            check_clock_skew_for_grace(
                last_known_server_time=server_t,
                system_clock=server_t - 7 * 3600,
            )
        assert exc_info.value.code == "license_clock_skew_detected"
        assert "behind" in exc_info.value.details["reason"]

    def test_clock_back_within_tolerance_is_ok(self) -> None:
        from simdrive.license.validator import check_clock_skew_for_grace

        server_t = 2_000_000_000
        # 5 hours behind — still under the 6h tolerance
        check_clock_skew_for_grace(
            last_known_server_time=server_t,
            system_clock=server_t - 5 * 3600,
        )

    def test_clock_forward_more_than_thirty_days_raises(self) -> None:
        from simdrive.license.errors import ClockSkewError
        from simdrive.license.validator import check_clock_skew_for_grace

        server_t = 2_000_000_000
        with pytest.raises(ClockSkewError) as exc_info:
            check_clock_skew_for_grace(
                last_known_server_time=server_t,
                system_clock=server_t + 31 * 86400,
            )
        assert exc_info.value.code == "license_clock_skew_detected"
        assert "no cloud check" in exc_info.value.details["reason"]

    def test_clock_forward_within_freshness_is_ok(self) -> None:
        from simdrive.license.validator import check_clock_skew_for_grace

        server_t = 2_000_000_000
        # 29 days ahead — still under the 30d freshness window
        check_clock_skew_for_grace(
            last_known_server_time=server_t,
            system_clock=server_t + 29 * 86400,
        )

    def test_nominal_in_window_no_raise(self) -> None:
        from simdrive.license.validator import check_clock_skew_for_grace

        server_t = 2_000_000_000
        # Exactly the anchor
        check_clock_skew_for_grace(
            last_known_server_time=server_t,
            system_clock=server_t,
        )


# ---------------------------------------------------------------------------
# trial.assert_trial_clock_trustworthy / entitlement integration
# ---------------------------------------------------------------------------


class TestEntitlementClockSkewGate:
    def test_valid_license_with_anchor_in_tolerance(
        self, keypair, tmp_path: Path
    ) -> None:
        """Nominal: license still valid + recent cloud check + clock fine = pass.

        The new clock-skew gate runs BEFORE the expiry check, so a
        clock-trustworthy entitlement check on a non-expired license must
        not raise ClockSkewError. (The validator itself still hard-rejects
        expired licenses when an anchor is present — clock-skew is a
        gate, not a permit, see the dedicated check_clock_skew_for_grace
        tests above.)
        """
        from simdrive.license.entitlement import check_entitlement

        now = int(time.time())
        # Cloud check happened 3 days ago — well inside the 30d freshness window.
        last_known_server_t = now - 3 * 86400
        path = tmp_path / "license.json"
        # License still valid (expires 7 days from now).
        _write_license_file(
            path,
            keypair,
            expires_offset=7 * 86400,
            last_known_server_time=last_known_server_t,
        )

        ent = check_entitlement(license_path=path, verify_key=keypair[1])
        assert ent.tier == "trial"

    def test_clock_back_seven_hours_blocks_grace(
        self, keypair, tmp_path: Path, monkeypatch
    ) -> None:
        """User backdates their system clock by >6h to keep an expired trial alive."""
        from simdrive.license.entitlement import check_entitlement
        from simdrive.license.errors import ClockSkewError

        now = int(time.time())
        # Pretend the cloud check happened 1 day in the FUTURE relative to wall
        # clock (i.e. user has pulled the system clock backwards by ~25h).
        last_known_server_t = now + 25 * 3600
        path = tmp_path / "license.json"
        _write_license_file(
            path,
            keypair,
            expires_offset=-3 * 86400,
            last_known_server_time=last_known_server_t,
        )

        with pytest.raises(ClockSkewError) as exc_info:
            check_entitlement(license_path=path, verify_key=keypair[1])
        assert exc_info.value.code == "license_clock_skew_detected"

    def test_no_cloud_check_in_31_days_blocks_grace(
        self, keypair, tmp_path: Path
    ) -> None:
        """User hasn't talked to the cloud in over 30 days — refuse offline grace."""
        from simdrive.license.entitlement import check_entitlement
        from simdrive.license.errors import ClockSkewError

        now = int(time.time())
        last_known_server_t = now - 31 * 86400
        path = tmp_path / "license.json"
        _write_license_file(
            path,
            keypair,
            expires_offset=-3 * 86400,
            last_known_server_time=last_known_server_t,
        )

        with pytest.raises(ClockSkewError) as exc_info:
            check_entitlement(license_path=path, verify_key=keypair[1])
        assert exc_info.value.code == "license_clock_skew_detected"

    def test_no_server_check_ever_still_uses_seven_day_grace(
        self, keypair, tmp_path: Path
    ) -> None:
        """Fresh install with no cloud contact yet: 7d grace still applies, no skew error."""
        from simdrive.license.entitlement import check_entitlement

        path = tmp_path / "license.json"
        # last_known_server_time stays None — original code path applies.
        _write_license_file(
            path,
            keypair,
            expires_offset=-3 * 86400,
            last_known_server_time=None,
        )
        # Within 7 days of expiry, no server anchor → grace passes.
        ent = check_entitlement(license_path=path, verify_key=keypair[1])
        assert ent.tier == "trial"

    def test_skew_error_carries_recovery_message(self) -> None:
        """ClockSkewError must include a 'simdrive license status' recovery hint."""
        from simdrive.license.errors import license_clock_skew_detected

        err = license_clock_skew_detected(
            "test reason",
            system_clock=100,
            last_known_server_time=200,
        )
        assert err.code == "license_clock_skew_detected"
        assert "simdrive license status" in err.message
        assert err.details["system_clock"] == 100
        assert err.details["last_known_server_time"] == 200
