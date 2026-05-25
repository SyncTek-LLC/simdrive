"""End-to-end trial CLI tests — [internal-tracker].5 workstream 2.

Covers the user-facing flow promised in the README and ``simdrive --help``:

  1. ``simdrive trial start --email <addr>`` — issues a real 14-day Ed25519
     trial license. Per W1.5 we keep issuance client-side (no cloud) for the
     b1 release; cloud issuance is a W2 hardening item.
  2. ``simdrive auth <license-key>`` — writes a paid key to disk and validates
     it locally.
  3. Trial uniqueness — issuing two trials for the same (email, machine) pair
     fails with ``trial_already_used`` so a user cannot extend their trial by
     re-running the command.

These are the only paths a brand-new user touches before they get any
tool calls past the paywall, so they get end-to-end coverage rather than
unit-mocked coverage.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from unittest.mock import patch

import pytest


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def isolated_state_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect every ``~/.simdrive`` state file (license + history) into tmp."""
    state_dir = tmp_path / ".simdrive"
    state_dir.mkdir()

    import simdrive.license.cli as cli_mod
    import simdrive.license.trial as trial_mod
    import simdrive.license.trial_history as history_mod

    monkeypatch.setattr(cli_mod, "_DEFAULT_LICENSE_PATH", state_dir / "license.json")
    monkeypatch.setattr(trial_mod, "_DEFAULT_LICENSE_PATH", state_dir / "license.json")
    monkeypatch.setattr(
        history_mod, "_DEFAULT_HISTORY_PATH", state_dir / "trial_history.json"
    )
    return state_dir


# ---------------------------------------------------------------------------
# Happy path — `simdrive trial start --email <addr>`
# ---------------------------------------------------------------------------


class TestTrialStartHappyPath:

    def test_trial_start_issues_valid_license(self, isolated_state_dir: Path) -> None:
        """A fresh trial issuance writes a license that check_entitlement accepts."""
        from simdrive.license.cli import cmd_trial_start
        from simdrive.license.entitlement import check_entitlement

        lic_path = isolated_state_dir / "license.json"
        # Force offline issuance (W1.5 release: client-side trials)
        result = cmd_trial_start(
            "fresh@example.com",
            license_path=lic_path,
            offline_dev=True,
        )

        assert lic_path.exists()
        ent = check_entitlement(license_path=lic_path)
        assert ent.tier == "trial"
        assert ent.customer_email == "fresh@example.com"

    def test_trial_expires_14_days_out(self, isolated_state_dir: Path) -> None:
        from simdrive.license.cli import cmd_trial_start

        lic_path = isolated_state_dir / "license.json"
        result = cmd_trial_start(
            "fresh@example.com",
            license_path=lic_path,
            offline_dev=True,
        )

        now = int(time.time())
        delta = result["expires_at"] - now
        # Allow 1-hour wiggle room either side
        assert 86400 * 13 < delta < 86400 * 15

    def test_trial_message_mentions_14_days(self, isolated_state_dir: Path) -> None:
        from simdrive.license.cli import cmd_trial_start

        lic_path = isolated_state_dir / "license.json"
        result = cmd_trial_start(
            "fresh@example.com",
            license_path=lic_path,
            offline_dev=True,
        )
        assert "14" in result["message"], (
            "The success message must tell the user the trial length"
        )


# ---------------------------------------------------------------------------
# Trial uniqueness — email+machine fingerprint
# ---------------------------------------------------------------------------


class TestTrialUniqueness:

    def test_second_trial_same_email_machine_rejected(self, isolated_state_dir: Path) -> None:
        """Re-running ``trial start --email x`` on the same machine must fail."""
        from simdrive.license.cli import cmd_trial_start
        from simdrive.license.errors import LicenseError

        lic_path = isolated_state_dir / "license.json"
        cmd_trial_start("dup@example.com", license_path=lic_path, offline_dev=True)

        # Even if the user deletes the license.json, the trial-history file
        # records the (email, machine) hash so re-issuance is blocked.
        lic_path.unlink()

        with pytest.raises(LicenseError) as exc:
            cmd_trial_start("dup@example.com", license_path=lic_path, offline_dev=True)
        assert exc.value.code == "trial_already_used"

    def test_different_email_can_start_trial(self, isolated_state_dir: Path) -> None:
        """A second email on the same machine is allowed (covers shared workstations)."""
        from simdrive.license.cli import cmd_trial_start

        lic_path = isolated_state_dir / "license.json"
        cmd_trial_start("a@example.com", license_path=lic_path, offline_dev=True)
        # Re-issuing for a different email is allowed; just overwrites the file.
        result = cmd_trial_start("b@example.com", license_path=lic_path, offline_dev=True)
        assert result["expires_at"] > 0


# ---------------------------------------------------------------------------
# `simdrive auth <license-key>` subcommand
# ---------------------------------------------------------------------------


class TestAuthCommand:

    def test_auth_writes_key_and_validates(self, isolated_state_dir: Path) -> None:
        """``simdrive auth <key>`` writes the key to license.json and validates it.

        Uses the production-signed payload generated from the test keypair so
        validate_license accepts it.
        """
        from simdrive.license.cli import cmd_auth
        from simdrive.license.keypair import generate_keypair, verify_key_to_hex
        from simdrive.license.signer import sign_license

        sk, vk = generate_keypair()
        now = int(time.time())
        key = sign_license(
            signing_key=sk,
            tier="pro",
            seats=4,
            customer_email="paid@example.com",
            issued_at=now,
            expires_at=now + 365 * 86400,
        )

        lic_path = isolated_state_dir / "license.json"

        # The auth path normally validates against the embedded production verify
        # key. For tests we inject our test verify key via an optional parameter.
        result = cmd_auth(key, license_path=lic_path, verify_key=vk)
        assert lic_path.exists()
        assert result["tier"] == "pro"
        assert result["seats"] == 4

        data = json.loads(lic_path.read_text())
        assert data["license_key"] == key

    def test_auth_rejects_invalid_key(self, isolated_state_dir: Path) -> None:
        from simdrive.license.cli import cmd_auth
        from simdrive.license.errors import LicenseError

        lic_path = isolated_state_dir / "license.json"
        with pytest.raises(LicenseError) as exc:
            cmd_auth("not.a.valid.key", license_path=lic_path)
        assert exc.value.code in ("license_invalid", "license_expired")
        assert not lic_path.exists(), (
            "Invalid key must NOT be written to disk"
        )


# ---------------------------------------------------------------------------
# `simdrive` dispatcher routes `auth` to the auth handler
# ---------------------------------------------------------------------------


class TestServeDispatchesAuth:

    def test_serve_registers_auth_subcommand(self) -> None:
        from simdrive import server
        assert "auth" in server._SUBCOMMANDS, (
            "`simdrive auth <key>` must be wired into the CLI dispatcher"
        )


# ---------------------------------------------------------------------------
# Server cloud unreachable path — still raises LicenseError, not bare exception
# (Carry-over invariant from test_license_cli_trial.py)
# ---------------------------------------------------------------------------


class TestCloudUnreachable:

    def test_cloud_unreachable_raises_license_error(
        self, isolated_state_dir: Path
    ) -> None:
        """offline_dev=False + network down → LicenseError (cloud_unreachable)."""
        import requests
        from simdrive.license.cli import cmd_trial_start
        from simdrive.license.errors import LicenseError

        lic_path = isolated_state_dir / "license.json"
        with patch(
            "requests.post",
            side_effect=requests.exceptions.ConnectionError("DNS failure"),
        ):
            with pytest.raises(LicenseError) as exc:
                cmd_trial_start(
                    "remote@example.com",
                    offline_dev=False,
                    license_path=lic_path,
                )
        assert exc.value.code == "cloud_unreachable"
