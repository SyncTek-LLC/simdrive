"""Tests for M18: LicenseValidator — license key validation with offline grace period.

TDD Phase — INIT-2026-492.
These tests are written BEFORE implementation exists and are importable even
when the implementation module is absent.

Module under test (to be created by CodeAtlas):
  specterqa/ios/license/validator.py  —  LicenseValidator
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Conditional import guard — tests remain importable without implementation.
# ---------------------------------------------------------------------------

try:
    from specterqa.ios.license.validator import LicenseValidator  # type: ignore[import]

    _VALIDATOR_AVAILABLE = True
except ImportError:
    _VALIDATOR_AVAILABLE = False
    LicenseValidator = None  # type: ignore[assignment,misc]

needs_validator = pytest.mark.skipif(
    not _VALIDATOR_AVAILABLE,
    reason="specterqa.ios.license.validator not yet implemented",
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_TEST_KEY = "LIC-TEST-0000-1111-2222-3333"
_API_URL = "https://api.keygen.sh/v1"
_REQUIRED_VALIDATE_KEYS = {"valid", "max_concurrent_sims", "tier", "expires_at"}

# Tier → expected max_concurrent_sims values per spec:
#   solo      → 1
#   team      → 4
#   enterprise → 16 (or a value indicating "unlimited" ≥ 16)
_TIER_CONCURRENCY = {
    "solo": 1,
    "team": 4,
    "enterprise": 16,
}


def _make_api_response(
    valid: bool = True,
    tier: str = "team",
    max_concurrent: int = 4,
    expires_at: str = "2027-01-01T00:00:00Z",
) -> dict:
    """Build a realistic mock API response payload."""
    return {
        "data": {
            "attributes": {
                "status": "ACTIVE" if valid else "EXPIRED",
                "metadata": {
                    "tier": tier,
                    "max_concurrent_sims": max_concurrent,
                },
                "expiry": expires_at,
            }
        }
    }


def _patch_requests_get(response_payload: dict, status_code: int = 200):
    """Context manager: patch requests.get (and urllib fallback) to return a mock."""
    mock_response = MagicMock()
    mock_response.status_code = status_code
    mock_response.json.return_value = response_payload
    mock_response.raise_for_status = MagicMock()
    return patch("requests.get", return_value=mock_response)


# ===========================================================================
# TestLicenseValidatorValidate — core validate() contract
# ===========================================================================


@needs_validator
class TestLicenseValidatorValidate:
    """validate() calls the API and returns a well-shaped result dict."""

    def test_validate_returns_required_keys(self):
        """validate() returns a dict with valid, max_concurrent_sims, tier, expires_at."""
        validator = LicenseValidator(license_key=_TEST_KEY, api_url=_API_URL)
        payload = _make_api_response(valid=True, tier="team", max_concurrent=4)

        with _patch_requests_get(payload):
            result = validator.validate()

        assert isinstance(result, dict), "validate() must return a dict"
        missing = _REQUIRED_VALIDATE_KEYS - result.keys()
        assert not missing, f"validate() result missing keys: {missing}"

    def test_validate_calls_api_with_license_key(self):
        """validate() makes an HTTP request that includes the license key."""
        validator = LicenseValidator(license_key=_TEST_KEY, api_url=_API_URL)
        payload = _make_api_response()

        with patch("requests.get") as mock_get:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = payload
            mock_get.return_value = mock_resp
            validator.validate()

        assert mock_get.called, "validate() must call requests.get"
        call_args = mock_get.call_args
        # The license key should appear in the URL or request body/headers
        url = call_args.args[0] if call_args.args else str(call_args)
        kwargs_str = str(call_args.kwargs)
        assert _TEST_KEY in url or _TEST_KEY in kwargs_str, (
            f"License key not found in API call. URL={url!r}, kwargs={kwargs_str!r}"
        )

    def test_validate_returns_valid_true_for_active_license(self):
        """validate()['valid'] is True when the API reports ACTIVE status."""
        validator = LicenseValidator(license_key=_TEST_KEY, api_url=_API_URL)
        with _patch_requests_get(_make_api_response(valid=True)):
            result = validator.validate()
        assert result["valid"] is True

    def test_validate_returns_valid_false_for_expired_license(self):
        """validate()['valid'] is False when the API reports EXPIRED status."""
        validator = LicenseValidator(license_key=_TEST_KEY, api_url=_API_URL)
        with _patch_requests_get(_make_api_response(valid=False)):
            result = validator.validate()
        assert result["valid"] is False


# ===========================================================================
# TestLicenseValidatorIsValid — cached validity check
# ===========================================================================


@needs_validator
class TestLicenseValidatorIsValid:
    """is_valid() returns a boolean and reflects the most recent validation."""

    def test_is_valid_returns_true_for_valid_license(self):
        """is_valid() returns True after validate() confirms the license is active."""
        validator = LicenseValidator(license_key=_TEST_KEY, api_url=_API_URL)
        with _patch_requests_get(_make_api_response(valid=True)):
            validator.validate()
        assert validator.is_valid() is True

    def test_is_valid_returns_false_for_invalid_license(self):
        """is_valid() returns False after validate() reports an expired license."""
        validator = LicenseValidator(license_key=_TEST_KEY, api_url=_API_URL)
        with _patch_requests_get(_make_api_response(valid=False)):
            validator.validate()
        assert validator.is_valid() is False


# ===========================================================================
# TestLicenseValidatorMaxConcurrentSims — tier-based concurrency limits
# ===========================================================================


@needs_validator
class TestLicenseValidatorMaxConcurrentSims:
    """max_concurrent_sims() reflects tier-based limits from the API response."""

    def test_solo_tier_returns_1(self):
        """solo tier → max_concurrent_sims() == 1."""
        validator = LicenseValidator(license_key=_TEST_KEY, api_url=_API_URL)
        with _patch_requests_get(_make_api_response(tier="solo", max_concurrent=1)):
            validator.validate()
        assert validator.max_concurrent_sims() == 1

    def test_team_tier_returns_4(self):
        """team tier → max_concurrent_sims() == 4."""
        validator = LicenseValidator(license_key=_TEST_KEY, api_url=_API_URL)
        with _patch_requests_get(_make_api_response(tier="team", max_concurrent=4)):
            validator.validate()
        assert validator.max_concurrent_sims() == 4

    def test_enterprise_tier_returns_unlimited(self):
        """enterprise tier → max_concurrent_sims() >= 16 (unlimited / high cap)."""
        validator = LicenseValidator(license_key=_TEST_KEY, api_url=_API_URL)
        with _patch_requests_get(_make_api_response(tier="enterprise", max_concurrent=16)):
            validator.validate()
        assert validator.max_concurrent_sims() >= 16


# ===========================================================================
# TestLicenseValidatorTier — tier string extraction
# ===========================================================================


@needs_validator
class TestLicenseValidatorTier:
    """tier() returns the license tier string from the API response."""

    def test_tier_returns_correct_string(self):
        """tier() returns the exact tier string from the validation response."""
        for expected_tier in ("solo", "team", "enterprise"):
            validator = LicenseValidator(license_key=_TEST_KEY, api_url=_API_URL)
            with _patch_requests_get(
                _make_api_response(tier=expected_tier, max_concurrent=_TIER_CONCURRENCY[expected_tier])
            ):
                validator.validate()
            assert validator.tier() == expected_tier, f"Expected tier={expected_tier!r}, got {validator.tier()!r}"


# ===========================================================================
# TestLicenseValidatorOfflineGrace — offline fallback behaviour
# ===========================================================================


@needs_validator
class TestLicenseValidatorOfflineGrace:
    """_check_offline_grace() uses a JWT token to allow offline operation."""

    def test_offline_grace_returns_true_within_grace_period(self):
        """_check_offline_grace() returns True when the JWT token's offline
        expiry is in the future (within the 72-hour window)."""
        validator = LicenseValidator(license_key=_TEST_KEY, api_url=_API_URL)
        # Simulate a token that was issued 1 hour ago → still within grace
        issued_at = datetime.now(timezone.utc) - timedelta(hours=1)
        grace_exp = issued_at + timedelta(hours=72)

        mock_token_payload = {
            "iat": int(issued_at.timestamp()),
            "offline_exp": int(grace_exp.timestamp()),
            "valid": True,
        }
        with patch.object(validator, "_decode_jwt", return_value=mock_token_payload):
            result = validator._check_offline_grace()
        assert result is True, "_check_offline_grace() should return True within the 72h grace window"

    def test_offline_grace_returns_false_after_grace_period(self):
        """_check_offline_grace() returns False when the grace window has expired."""
        validator = LicenseValidator(license_key=_TEST_KEY, api_url=_API_URL)
        # Simulate a token issued 80 hours ago → grace window expired
        issued_at = datetime.now(timezone.utc) - timedelta(hours=80)
        grace_exp = issued_at + timedelta(hours=72)

        mock_token_payload = {
            "iat": int(issued_at.timestamp()),
            "offline_exp": int(grace_exp.timestamp()),
            "valid": True,
        }
        with patch.object(validator, "_decode_jwt", return_value=mock_token_payload):
            result = validator._check_offline_grace()
        assert result is False, "_check_offline_grace() should return False after the 72h grace window expires"

    def test_api_error_triggers_offline_grace_check(self):
        """When the API is unreachable, validate() falls back to _check_offline_grace().

        If the grace check returns True (within window), validate() should NOT raise
        and should return a result dict with 'valid' reflecting the offline state.
        """
        validator = LicenseValidator(license_key=_TEST_KEY, api_url=_API_URL)

        # Patch requests.get to simulate a network error
        with (
            patch("requests.get", side_effect=Exception("Network unreachable")),
            patch.object(validator, "_check_offline_grace", return_value=True),
        ):
            # Should not raise — offline grace covers the gap
            result = validator.validate()

        assert isinstance(result, dict), (
            "validate() must return a dict even when API is unreachable and grace is active"
        )


# ===========================================================================
# TestLicenseValidatorCaching — result caching / idempotency
# ===========================================================================


@needs_validator
class TestLicenseValidatorCaching:
    """validate() caches its result so subsequent calls do not re-hit the API."""

    def test_validate_caches_result_second_call_skips_api(self):
        """Calling validate() twice should only call the API once."""
        validator = LicenseValidator(license_key=_TEST_KEY, api_url=_API_URL)
        payload = _make_api_response(valid=True, tier="team", max_concurrent=4)

        with patch("requests.get") as mock_get:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = payload
            mock_get.return_value = mock_resp

            result_1 = validator.validate()
            result_2 = validator.validate()

        assert mock_get.call_count == 1, f"Expected 1 API call due to caching, but got {mock_get.call_count}"
        assert result_1 == result_2, "Cached result must equal first result"
