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

from simdrive.license.errors import license_not_found
from simdrive.license.trial import load_license_data
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

    Raises
    ------
    LicenseError(code="license_not_found")
        No license.json at the given path.
    LicenseError(code="license_invalid")
        Signature invalid.
    LicenseError(code="license_expired")
        Key has expired (online mode).
    LicenseError(code="license_offline_grace_exhausted")
        Key expired and 7-day grace window elapsed (offline mode).
    """
    if license_path is None:
        license_path = _DEFAULT_LICENSE_PATH
    if not license_path.exists():
        raise license_not_found(str(license_path))

    try:
        data = load_license_data(license_path)
    except (json.JSONDecodeError, OSError) as exc:
        from simdrive.license.errors import license_invalid
        raise license_invalid(f"could not read license file: {exc}") from exc

    license_key: str = data.get("license_key", "")
    last_known_server_time = data.get("last_known_server_time")

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
