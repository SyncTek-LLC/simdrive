"""License-domain error codes.

LicenseError inherits from SimdriveError so the MCP wrapper's
``except errors.SimdriveError`` clause automatically handles it and
preserves the structured error envelope (code, message, details).

Error codes surfaced here:
  - license_invalid
  - license_expired
  - license_offline_grace_exhausted
  - license_tier_insufficient
  - license_key_rotation_required  (signed under a key_id this client doesn't trust)
  - license_clock_skew_detected    (system clock drifted too far from last known server time)
  - trial_already_used
  - license_not_found
  - trial_rate_limited
  - cloud_unreachable

INIT-2026-610 (Chairman decision, 2026-08-26): SimDrive was retired as a
commercial product — every public surface says it is free and unsold, and
``simdrive.license.entitlement.check_entitlement()`` no longer raises when no
license.json is present (see that module for the no-license default). None of
the codes above fire on a normal install any more. They stay in place —
inert, not deleted — because a license.json that IS present (a leftover file,
or a future INIT-2026-569 offline self-hosted entitlement) is still fully
validated, and these codes are how that validation failure is reported.

UX envelope:
  When the MCP-tool wrapper serialises a LicenseError to the agent host, the
  envelope is enriched with:
    error: "license_required"           - umbrella code agents switch on
    code:  <specific code>              - granular code (license_not_found, …)
    message:                            - human-readable
  Prior to INIT-2026-610 this envelope also carried ``pricing_url``,
  ``trial_command_hint``, and ``auth_command_hint`` so hosts could render a
  copy-pasteable upsell. Those fields — and any recovery copy that pointed at
  a trial signup or the pricing page — are removed: SimDrive is not for sale,
  so a purchase path must never be advertised, no matter which of these codes
  fires.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


from simdrive.errors import SimdriveError


@dataclass
class LicenseError(SimdriveError):
    """Raised for all license validation failures.

    Inherits from SimdriveError so the MCP tool wrapper's
    ``except errors.SimdriveError`` clause catches it automatically,
    preserving the structured error envelope rather than wrapping it as
    code="internal".

    ``to_dict()`` returns the "license_required" envelope rather than the
    generic SimdriveError shape so callers can still switch on the umbrella
    ``error`` field — but (INIT-2026-610) it no longer carries pricing_url or
    trial/auth command hints. SimDrive is a retired, free product; nothing in
    this envelope may advertise a purchase path.
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
                # Umbrella code — host switches on this for license-domain errors.
                "error": "license_required",
                # Granular code (license_not_found / license_expired / …) for
                # callers that want to differentiate trial-expired from missing.
                "code": self.code,
                "message": self.message,
                "details": self.details,
            },
        }


@dataclass
class KeyRotationError(LicenseError):
    """Raised when a license's ``key_id`` is not in TRUSTED_PUBLIC_KEYS.

    Distinct subclass so callers (CLI, MCP wrapper) can render a
    "your simdrive is too old" upsell that differs from generic
    invalid-signature messaging.
    """


@dataclass
class ClockSkewError(LicenseError):
    """Raised when the local clock cannot be trusted for offline-grace evaluation."""


# ---- Constructor functions (mirroring errors.py pattern) ----


def license_invalid(reason: str) -> LicenseError:
    return LicenseError(
        code="license_invalid",
        message=(
            f"Your SimDrive license is invalid: {reason}. SimDrive itself is "
            "free and does not require a license — this only fires because a "
            "license.json is present but unreadable. "
            "Recovery: run `simdrive license show` to inspect it, or delete "
            "the file at the path above to run SimDrive unlicensed."
        ),
        details={"reason": reason},
    )


def license_expired(expires_at: int) -> LicenseError:
    return LicenseError(
        code="license_expired",
        message=(
            "The license.json on disk has expired. SimDrive itself is free "
            "and does not require a license. "
            "Recovery: delete the expired license.json to run SimDrive "
            "unlicensed, or run `simdrive auth <your-license-key>` if your "
            f"license administrator has issued a renewed key. (License expired at {expires_at}.)"
        ),
        details={"expires_at": expires_at},
    )


def license_offline_grace_exhausted(expires_at: int, grace_days: int = 7) -> LicenseError:
    return LicenseError(
        code="license_offline_grace_exhausted",
        message=(
            f"License expired at {expires_at} and offline grace period of {grace_days} days has elapsed. "
            "SimDrive itself is free and does not require a license. "
            "Recovery: connect to the internet and run `simdrive license status` to refresh, "
            "or delete the license.json to run SimDrive unlicensed."
        ),
        details={"expires_at": expires_at, "grace_days": grace_days},
    )


def license_tier_insufficient(required: str, current: str) -> LicenseError:
    return LicenseError(
        code="license_tier_insufficient",
        message=(
            f"This feature requires {required!r} tier or above; "
            f"your license is {current!r}. "
            "Recovery: contact whoever issued your license.json to request "
            f"a {required!r}-tier key, or delete the file to fall back to "
            "unlicensed use of everything else."
        ),
        details={"required": required, "current": current},
    )


def trial_already_used(email: str) -> LicenseError:
    return LicenseError(
        code="trial_already_used",
        message=(
            f"A trial has already been activated for {email!r}. SimDrive no "
            "longer requires a trial or license to use. "
            "Recovery: skip the trial and just run SimDrive — every tool "
            "works unlicensed."
        ),
        details={"email": email},
    )


def license_not_found(path: str) -> LicenseError:
    return LicenseError(
        code="license_not_found",
        message=(
            f"No SimDrive license found at {path!r}. This is not an error — "
            "SimDrive is free and runs fully unlicensed by default. "
            "Recovery: no action needed; if you expected an existing license "
            "to be picked up, confirm it is at the path above."
        ),
        details={"path": path},
    )


def cloud_unreachable(detail: str) -> LicenseError:
    return LicenseError(
        code="cloud_unreachable",
        message=(
            f"Could not reach the license server: {detail}. This only "
            "affects optional license-management commands — SimDrive itself "
            "is free and runs unlicensed without any network access. "
            "Recovery: check your network connection and retry."
        ),
        details={"detail": detail},
    )


def license_key_rotation_required(key_id: str, trusted_ids: list[str]) -> "KeyRotationError":
    """Raised when the payload's key_id is unknown to this client.

    The likely cause is that the license was signed with a freshly-rotated
    key whose public counterpart ships in a newer simdrive release. The
    recovery is to upgrade simdrive (so the new trusted public key is in
    the embedded ``TRUSTED_PUBLIC_KEYS`` list) and re-run the command.
    """
    return KeyRotationError(
        code="license_key_rotation_required",
        message=(
            f"Your license was signed with key {key_id!r} but this simdrive build only "
            f"trusts {trusted_ids!r}. Recovery: upgrade simdrive (`pip install -U simdrive`) "
            "so it picks up the new signing key, then retry. If you cannot upgrade, "
            "contact support@simdrive.dev to re-issue the license under an older key."
        ),
        details={"key_id": key_id, "trusted_key_ids": trusted_ids},
    )


def license_clock_skew_detected(
    reason: str,
    *,
    system_clock: int,
    last_known_server_time: int,
) -> "ClockSkewError":
    """Raised when the local clock cannot be trusted for the offline-grace check.

    Two ways this can trigger:
      - system clock moved backwards > 6 hours behind last known server time
        (likely backdating attack or clock reset),
      - system clock has not seen a server check in > 30 days
        (offline for too long to trust local time for grace decisions).

    Recovery is the same in both cases: connect to the internet and run
    `simdrive license status` to refresh the trusted server timestamp.
    """
    return ClockSkewError(
        code="license_clock_skew_detected",
        message=(
            f"Refusing offline grace window: {reason}. "
            "Recovery: connect to the internet and run `simdrive license status` to "
            "refresh the trusted server timestamp, then retry."
        ),
        details={
            "reason": reason,
            "system_clock": system_clock,
            "last_known_server_time": last_known_server_time,
        },
    )


def trial_rate_limited(ip: str) -> LicenseError:
    return LicenseError(
        code="trial_rate_limited",
        message=(
            f"Too many trial requests from {ip!r} (limit: 5/IP/day). "
            "Recovery: try again tomorrow or contact support@simdrive.dev."
        ),
        details={"ip": ip},
    )
