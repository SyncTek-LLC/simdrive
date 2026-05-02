"""License-domain error codes.

These are standalone error classes for the license package.
Atlas: add these codes to the global errors.py during integration:
  - license_invalid
  - license_expired
  - license_offline_grace_exhausted
  - license_tier_insufficient
  - trial_already_used
  - license_not_found
  - trial_rate_limited
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class LicenseError(Exception):
    """Raised for all license validation failures.

    WHY separate from SimdriveError: the license package is importable
    independently of the full simdrive stack; avoiding circular imports
    and keeping the package self-contained.
    """

    code: str
    message: str
    details: dict = field(default_factory=dict)

    def __str__(self) -> str:
        return f"[{self.code}] {self.message}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": False,
            "error": {
                "code": self.code,
                "message": self.message,
                "details": self.details,
            },
        }


# ---- Constructor functions (mirroring errors.py pattern) ----


def license_invalid(reason: str) -> LicenseError:
    return LicenseError(
        code="license_invalid",
        message=(
            f"License key is invalid: {reason}. "
            "Recovery: run `simdrive license status` to check your key, "
            "or `simdrive trial start` to begin a new trial."
        ),
        details={"reason": reason},
    )


def license_expired(expires_at: int) -> LicenseError:
    return LicenseError(
        code="license_expired",
        message=(
            f"License expired at {expires_at}. "
            "Recovery: run `simdrive license activate <key>` to install a renewed key, "
            "or visit https://simdrive.dev/pricing to renew."
        ),
        details={"expires_at": expires_at},
    )


def license_offline_grace_exhausted(expires_at: int, grace_days: int = 7) -> LicenseError:
    return LicenseError(
        code="license_offline_grace_exhausted",
        message=(
            f"License expired at {expires_at} and offline grace period of {grace_days} days has elapsed. "
            "Recovery: connect to the internet and run `simdrive license status` to refresh, "
            "or visit https://simdrive.dev/pricing to renew."
        ),
        details={"expires_at": expires_at, "grace_days": grace_days},
    )


def license_tier_insufficient(required: str, current: str) -> LicenseError:
    return LicenseError(
        code="license_tier_insufficient",
        message=(
            f"This feature requires {required!r} tier or above; "
            f"your license is {current!r}. "
            "Recovery: visit https://simdrive.dev/pricing to upgrade."
        ),
        details={"required": required, "current": current},
    )


def trial_already_used(email: str) -> LicenseError:
    return LicenseError(
        code="trial_already_used",
        message=(
            f"A trial has already been activated for {email!r}. "
            "Recovery: visit https://simdrive.dev/pricing to purchase a license."
        ),
        details={"email": email},
    )


def license_not_found(path: str) -> LicenseError:
    return LicenseError(
        code="license_not_found",
        message=(
            f"No license file found at {path!r}. "
            "Recovery: run `simdrive trial start --email <you@example.com>` "
            "to begin a 14-day free trial."
        ),
        details={"path": path},
    )


def trial_rate_limited(ip: str) -> LicenseError:
    return LicenseError(
        code="trial_rate_limited",
        message=(
            f"Too many trial requests from {ip!r} (limit: 5/IP/day). "
            "Recovery: try again tomorrow or contact support@synctek.io."
        ),
        details={"ip": ip},
    )
