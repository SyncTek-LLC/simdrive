"""Entitlement resolution — load license from disk, validate, return Entitlement.

WHY a dataclass: typed interface for callers (runner.py, ci.py); no loose dicts.
The `check_entitlement()` function is the single entry point for all
feature-gating code in the simdrive engine.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from nacl.signing import VerifyKey

from simdrive.license.trial import assert_trial_clock_trustworthy, load_license_data
from simdrive.license.validator import validate_license
from simdrive.license.public_key import get_public_key


_DEFAULT_LICENSE_PATH = Path.home() / ".simdrive" / "license.json"


@dataclass(frozen=True)
class Entitlement:
    """Resolved entitlement from a validated license key.

    All fields are from the signed payload — callers should not re-read
    the license.json file after calling check_entitlement().
    """

    tier: str
    seats: int
    expires_at: int
    customer_email: str

    @property
    def is_trial(self) -> bool:
        return self.tier == "trial"

    @property
    def journey_quota_per_month(self) -> Optional[int]:
        """Return monthly journey quota for this tier, or None for unlimited."""
        quotas = {
            "trial": 250,
            "solo": 50,
            "pro": 250,
            "team": 1000,
            "enterprise": None,
            "free": None,  # INIT-2026-610 — retired-product no-license default.
        }
        return quotas.get(self.tier)

    @property
    def max_simulators(self) -> Optional[int]:
        """Return max parallel simulator count, or None for unlimited."""
        limits = {
            "trial": 4,   # Pro features during trial
            "solo": 1,
            "pro": 4,
            "team": 5,
            "enterprise": None,
            "free": None,  # INIT-2026-610 — retired-product no-license default.
        }
        return limits.get(self.tier)


def check_entitlement(
    license_path: Optional[Path] = None,
    *,
    verify_key: Optional[VerifyKey] = None,
) -> Entitlement:
    """Load and validate the on-disk license, returning an Entitlement.

    Parameters
    ----------
    license_path:
        Path to license.json (default: ~/.simdrive/license.json).
    verify_key:
        Override the embedded public key (used in tests). Defaults to
        the key in public_key.SIMDRIVE_PUBLIC_KEY_HEX.

    Returns
    -------
    Entitlement
        Validated entitlement with tier, seats, expires_at, customer_email.
        When no license.json exists at the given path, this returns a free,
        unlimited entitlement rather than raising — see the note on the
        no-license branch below.

    Raises
    ------
    LicenseError(code="license_invalid")
        A license.json exists but its signature is invalid or the file is
        unreadable/malformed.
    LicenseError(code="license_expired")
        Key has expired (online mode).
    LicenseError(code="license_offline_grace_exhausted")
        Key expired and 7-day grace window elapsed (offline mode).
    """
    if license_path is None:
        license_path = _DEFAULT_LICENSE_PATH
    if not license_path.exists():
        # INIT-2026-610 (Chairman decision, 2026-08-26): SimDrive was retired
        # as a commercial product. Every public surface — simdrive.dev/pricing,
        # the README, the CHANGELOG — now says SimDrive is free and requires
        # no license or account. Before this change, this branch raised
        # `license_not_found`, which advertised a 14-day trial and a pricing
        # page for a product that is no longer for sale: a fresh
        # `pip install simdrive` wired into an MCP client failed its very
        # first tool call with an upsell for something not on offer.
        #
        # This is intentional and permanent, NOT a bug to "fix" back to
        # enforcing a paywall: the no-license path returns an unlimited free
        # entitlement so every MCP tool works out of the box on a clean
        # machine, with no license file required.
        #
        # `gate.py` and its 38 call sites in server.py are deliberately left
        # in place and still invoke this function on every tool call — they
        # are not being ripped out, because INIT-2026-569's offline
        # self-hosted entitlement paths may still route through this
        # chokepoint. This branch only covers "no license.json is present at
        # all"; a license.json that IS present is still fully validated below
        # (signature, expiry, clock-skew) so that plumbing keeps working.
        return Entitlement(
            tier="free",
            seats=1,
            expires_at=4070908800,  # 2099-01-01T00:00:00Z — never expires.
            customer_email="",
        )

    try:
        data = load_license_data(license_path)
    except (json.JSONDecodeError, OSError) as exc:
        from simdrive.license.errors import license_invalid
        raise license_invalid(f"could not read license file: {exc}") from exc

    license_key: str = data.get("license_key", "")
    last_known_server_time = data.get("last_known_server_time")

    # Clock-skew guard: if the on-disk last_known_server_time is set,
    # refuse to even attempt validation when the system clock is too far
    # off (either backwards >6h or no contact >30d). This stops a user
    # from extending an expired trial by backdating their machine.
    assert_trial_clock_trustworthy(data)

    vk: VerifyKey = verify_key if verify_key is not None else get_public_key()

    payload = validate_license(
        license_key,
        verify_key=vk,
        last_known_server_time=last_known_server_time,
    )

    return Entitlement(
        tier=payload.get("tier", "trial"),
        seats=payload.get("seats", 1),
        expires_at=payload.get("expires_at", 0),
        customer_email=payload.get("customer_email", payload.get("subject", "")),
    )
