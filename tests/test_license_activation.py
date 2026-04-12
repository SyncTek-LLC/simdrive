"""Tests for license activation flow — INIT-2026-525.

Covers:
  - specterqa-ios license activate (valid key, invalid key)
  - specterqa-ios license status
  - specterqa-ios license deactivate
  - BYOK enforcement (check_byok / assert_ready_for_run)
  - Trial mode limits (consume_trial_run)
  - auth.yaml write / read / delete cycle
  - LicenseValidator with Keygen.sh mock responses
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict
from unittest.mock import MagicMock, patch

import pytest
import yaml
from click.testing import CliRunner

# ---------------------------------------------------------------------------
# Imports under test
# ---------------------------------------------------------------------------

from specterqa.ios.cli.license_cmd import (
    TIER_SIM_LIMITS,
    TRIAL_MAX_RUNS,
    _AUTH_PATH,
    _PURCHASE_URL,
    _keygen_validate,
    _read_auth,
    _write_yaml,
    activate,
    deactivate,
    license_group,
    status,
)
from specterqa.ios.license.validator import (
    LicenseValidator,
    LicenseBYOKError,
    TrialLimitError,
    check_byok,
    consume_trial_run,
    reset_trial_counter,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def tmp_auth_path(tmp_path, monkeypatch) -> Path:
    """Override _AUTH_PATH so tests never touch ~/.specterqa/auth.yaml."""
    auth_file = tmp_path / "auth.yaml"
    monkeypatch.setattr("specterqa.ios.cli.license_cmd._AUTH_PATH", auth_file)
    return auth_file


@pytest.fixture(autouse=True)
def reset_trial(monkeypatch):
    """Reset the trial run counter before every test."""
    reset_trial_counter()
    yield
    reset_trial_counter()


@pytest.fixture()
def keygen_account_env(monkeypatch):
    """Set SPECTERQA_KEYGEN_ACCOUNT for tests that need it."""
    monkeypatch.setenv("SPECTERQA_KEYGEN_ACCOUNT", "test-account-123")


@pytest.fixture()
def api_key_env(monkeypatch):
    """Set ANTHROPIC_API_KEY for tests that need BYOK satisfied."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-key")


# ---------------------------------------------------------------------------
# Helper — build mock httpx response
# ---------------------------------------------------------------------------


def _mock_httpx_response(
    status_code: int = 200,
    tier: str = "pro",
    max_sims: int = 4,
    valid: bool = True,
    expires_at: str = "2027-06-01T00:00:00Z",
) -> MagicMock:
    payload: Dict[str, Any] = {
        "meta": {"valid": valid},
        "data": {
            "id": "lic-abc123",
            "attributes": {
                "status": "ACTIVE" if valid else "EXPIRED",
                "name": "test@example.com",
                "expiry": expires_at,
                "metadata": {
                    "tier": tier,
                    "max_concurrent_sims": max_sims,
                },
            },
        },
    }
    mock = MagicMock()
    mock.status_code = status_code
    mock.json.return_value = payload
    if status_code >= 400:
        from httpx import HTTPStatusError, Request, Response

        mock.raise_for_status.side_effect = HTTPStatusError(
            message=f"HTTP {status_code}",
            request=MagicMock(),
            response=MagicMock(status_code=status_code, text="error"),
        )
    else:
        mock.raise_for_status = MagicMock()
    return mock


# ===========================================================================
# 1. activate — valid key
# ===========================================================================


class TestActivateValidKey:
    def test_activate_writes_auth_yaml(self, tmp_auth_path, keygen_account_env, monkeypatch):
        """activate with a valid key should create auth.yaml with expected fields."""
        mock_resp = _mock_httpx_response(tier="pro", max_sims=4)
        monkeypatch.setattr("specterqa.ios.cli.license_cmd._AUTH_PATH", tmp_auth_path)

        with patch("httpx.get", return_value=mock_resp):
            runner = CliRunner()
            result = runner.invoke(activate, ["LIC-TEST-0000-1111"])

        assert result.exit_code == 0, result.output
        assert tmp_auth_path.exists(), "auth.yaml should be created after successful activation"

        data = yaml.safe_load(tmp_auth_path.read_text())
        assert data["tier"] == "pro"
        assert data["max_sims"] == 4
        assert "license_key" in data

    def test_activate_shows_tier_and_sim_count(self, tmp_auth_path, keygen_account_env, monkeypatch):
        """activate output should display tier and simulator count."""
        mock_resp = _mock_httpx_response(tier="team", max_sims=10)
        monkeypatch.setattr("specterqa.ios.cli.license_cmd._AUTH_PATH", tmp_auth_path)

        with patch("httpx.get", return_value=mock_resp):
            runner = CliRunner()
            result = runner.invoke(activate, ["LIC-TEAM-XXXX"])

        assert result.exit_code == 0
        assert "team" in result.output.lower() or "10" in result.output

    def test_activate_byok_reminder_when_no_api_key(
        self, tmp_auth_path, keygen_account_env, monkeypatch
    ):
        """activate should print a BYOK warning when ANTHROPIC_API_KEY is absent."""
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.setattr("specterqa.ios.cli.license_cmd._AUTH_PATH", tmp_auth_path)
        mock_resp = _mock_httpx_response()

        with patch("httpx.get", return_value=mock_resp):
            runner = CliRunner()
            result = runner.invoke(activate, ["LIC-ANY-KEY"])

        # The output should mention ANTHROPIC_API_KEY
        assert "ANTHROPIC_API_KEY" in result.output


# ===========================================================================
# 2. activate — invalid key
# ===========================================================================


class TestActivateInvalidKey:
    def test_activate_invalid_key_exits_nonzero(self, keygen_account_env, monkeypatch, tmp_auth_path):
        """activate with a key the API marks invalid should exit non-zero."""
        monkeypatch.setattr("specterqa.ios.cli.license_cmd._AUTH_PATH", tmp_auth_path)
        mock_resp = _mock_httpx_response(valid=False)

        with patch("httpx.get", return_value=mock_resp):
            runner = CliRunner()
            result = runner.invoke(activate, ["LIC-BAD-KEY"])

        assert result.exit_code != 0
        assert not tmp_auth_path.exists(), "auth.yaml should NOT be created for invalid key"

    def test_activate_404_key_shows_purchase_url(self, keygen_account_env, monkeypatch, tmp_auth_path):
        """When the API returns 404, the error message should include the purchase URL."""
        monkeypatch.setattr("specterqa.ios.cli.license_cmd._AUTH_PATH", tmp_auth_path)
        mock_resp = _mock_httpx_response(status_code=404)

        with patch("httpx.get", return_value=mock_resp):
            runner = CliRunner()
            result = runner.invoke(activate, ["LIC-NOTFOUND"])

        assert result.exit_code != 0
        assert _PURCHASE_URL in result.output or "synctek.io" in result.output

    def test_activate_missing_account_env_exits_nonzero(self, monkeypatch, tmp_auth_path):
        """activate should exit non-zero when SPECTERQA_KEYGEN_ACCOUNT is not set."""
        monkeypatch.delenv("SPECTERQA_KEYGEN_ACCOUNT", raising=False)
        monkeypatch.setattr("specterqa.ios.cli.license_cmd._AUTH_PATH", tmp_auth_path)

        runner = CliRunner()
        result = runner.invoke(activate, ["LIC-ANY"])

        assert result.exit_code != 0
        assert "SPECTERQA_KEYGEN_ACCOUNT" in result.output


# ===========================================================================
# 3. BYOK enforcement
# ===========================================================================


class TestBYOKEnforcement:
    def test_check_byok_raises_when_api_key_missing(self, monkeypatch):
        """check_byok() raises LicenseBYOKError when ANTHROPIC_API_KEY is absent."""
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        with pytest.raises(LicenseBYOKError) as exc_info:
            check_byok()
        assert "ANTHROPIC_API_KEY" in str(exc_info.value)
        assert "console.anthropic.com" in str(exc_info.value)

    def test_check_byok_passes_when_api_key_present(self, api_key_env):
        """check_byok() does not raise when ANTHROPIC_API_KEY is set."""
        check_byok()  # should not raise

    def test_assert_ready_for_run_raises_without_api_key(self, monkeypatch):
        """assert_ready_for_run() raises LicenseBYOKError when API key is absent."""
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.setenv("SPECTERQA_IOS_LICENSE", "founder")  # bypass API

        v = LicenseValidator()
        with pytest.raises(LicenseBYOKError):
            v.assert_ready_for_run()


# ===========================================================================
# 4. Trial mode limits
# ===========================================================================


class TestTrialModeLimits:
    def test_trial_allows_first_three_runs(self, api_key_env, monkeypatch):
        """Trial mode allows exactly TRIAL_MAX_RUNS runs before blocking."""
        monkeypatch.delenv("SPECTERQA_IOS_LICENSE", raising=False)
        v = LicenseValidator()  # no key → trial

        import warnings

        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            for _ in range(TRIAL_MAX_RUNS):
                v._cache = None  # reset cache between runs
                v.assert_ready_for_run()  # should not raise

    def test_trial_blocks_on_fourth_run(self, api_key_env, monkeypatch):
        """The 4th run in trial mode should raise TrialLimitError."""
        monkeypatch.delenv("SPECTERQA_IOS_LICENSE", raising=False)
        v = LicenseValidator()

        import warnings

        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            for _ in range(TRIAL_MAX_RUNS):
                v._cache = None
                v.assert_ready_for_run()

            v._cache = None
            with pytest.raises(TrialLimitError) as exc_info:
                v.assert_ready_for_run()

        assert "synctek.io" in str(exc_info.value) or "license activate" in str(exc_info.value)

    def test_consume_trial_run_increments_counter(self):
        """consume_trial_run() returns the incremented run count."""
        assert consume_trial_run() == 1
        assert consume_trial_run() == 2

    def test_reset_trial_counter_restores_zero(self):
        """reset_trial_counter() allows subsequent runs to start from zero."""
        consume_trial_run()
        consume_trial_run()
        reset_trial_counter()
        assert consume_trial_run() == 1


# ===========================================================================
# 5. auth.yaml write / read / delete cycle
# ===========================================================================


class TestAuthYamlCycle:
    def test_write_yaml_creates_file(self, tmp_path):
        """_write_yaml creates a YAML file at the given path."""
        dest = tmp_path / ".specterqa" / "auth.yaml"
        _write_yaml(dest, {"tier": "pro", "max_sims": 4})
        assert dest.exists()
        data = yaml.safe_load(dest.read_text())
        assert data["tier"] == "pro"
        assert data["max_sims"] == 4

    def test_read_auth_returns_none_when_absent(self, tmp_path, monkeypatch):
        """_read_auth returns None when auth.yaml does not exist."""
        monkeypatch.setattr(
            "specterqa.ios.cli.license_cmd._AUTH_PATH",
            tmp_path / "nonexistent.yaml",
        )
        assert _read_auth() is None

    def test_read_auth_returns_dict_when_present(self, tmp_auth_path):
        """_read_auth returns a dict when auth.yaml exists and is valid."""
        _write_yaml(
            tmp_auth_path,
            {"license_key": "LIC-XXXX", "tier": "indie", "max_sims": 2, "expires_at": None},
        )
        data = _read_auth()
        assert isinstance(data, dict)
        assert data["tier"] == "indie"

    def test_deactivate_removes_auth_yaml(self, tmp_auth_path, monkeypatch):
        """deactivate command removes auth.yaml."""
        _write_yaml(tmp_auth_path, {"tier": "pro", "max_sims": 4})
        assert tmp_auth_path.exists()

        monkeypatch.setattr("specterqa.ios.cli.license_cmd._AUTH_PATH", tmp_auth_path)
        runner = CliRunner()
        # --yes bypasses the confirmation prompt
        result = runner.invoke(deactivate, ["--yes"])

        assert result.exit_code == 0
        assert not tmp_auth_path.exists(), "auth.yaml should be removed after deactivate"


# ===========================================================================
# 6. license status command
# ===========================================================================


class TestLicenseStatus:
    def test_status_shows_trial_when_no_auth_file(self, tmp_auth_path, monkeypatch, api_key_env):
        """license status shows trial mode when auth.yaml is absent."""
        monkeypatch.setattr("specterqa.ios.cli.license_cmd._AUTH_PATH", tmp_auth_path)

        runner = CliRunner()
        result = runner.invoke(status)

        assert result.exit_code == 0
        assert "trial" in result.output.lower()

    def test_status_shows_tier_when_licensed(self, tmp_auth_path, monkeypatch, api_key_env):
        """license status shows the tier from auth.yaml."""
        _write_yaml(
            tmp_auth_path,
            {"license_key": "LIC-PRO-XXXX", "tier": "pro", "max_sims": 4, "expires_at": "2027-01-01"},
        )
        monkeypatch.setattr("specterqa.ios.cli.license_cmd._AUTH_PATH", tmp_auth_path)

        runner = CliRunner()
        result = runner.invoke(status)

        assert result.exit_code == 0
        assert "pro" in result.output.lower()

    def test_status_flags_missing_api_key(self, tmp_auth_path, monkeypatch):
        """license status shows a warning when ANTHROPIC_API_KEY is absent."""
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.setattr("specterqa.ios.cli.license_cmd._AUTH_PATH", tmp_auth_path)

        runner = CliRunner()
        result = runner.invoke(status)

        assert result.exit_code == 0
        assert "ANTHROPIC_API_KEY" in result.output or "not set" in result.output.lower()


# ===========================================================================
# 7. LicenseValidator — Keygen API mock
# ===========================================================================


class TestLicenseValidatorWithKeygenMock:
    def test_validate_returns_valid_true_for_active_license(self, monkeypatch):
        """LicenseValidator.validate() returns valid=True when Keygen says ACTIVE."""
        monkeypatch.setenv("SPECTERQA_KEYGEN_ACCOUNT", "acct-test")
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {
            "meta": {"valid": True},
            "data": {
                "attributes": {
                    "status": "ACTIVE",
                    "expiry": "2027-01-01T00:00:00Z",
                    "metadata": {"tier": "pro", "max_concurrent_sims": 4},
                }
            },
        }
        with patch("httpx.get", return_value=mock_resp):
            v = LicenseValidator(license_key="LIC-ACTIVE")
            result = v.validate()

        assert result["valid"] is True
        assert result["tier"] == "pro"
        assert result["max_concurrent_sims"] == 4

    def test_validate_returns_valid_false_for_expired_license(self, monkeypatch):
        """LicenseValidator.validate() returns valid=False when Keygen says EXPIRED."""
        monkeypatch.setenv("SPECTERQA_KEYGEN_ACCOUNT", "acct-test")
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {
            "meta": {"valid": False},
            "data": {
                "attributes": {
                    "status": "EXPIRED",
                    "expiry": "2020-01-01T00:00:00Z",
                    "metadata": {"tier": "indie", "max_concurrent_sims": 2},
                }
            },
        }
        with patch("httpx.get", return_value=mock_resp):
            v = LicenseValidator(license_key="LIC-EXPIRED")
            result = v.validate()

        assert result["valid"] is False
