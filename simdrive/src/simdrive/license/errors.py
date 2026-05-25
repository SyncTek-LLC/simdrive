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

UX envelope:
  When the MCP-tool wrapper serialises a LicenseError to the agent host, the
  envelope is enriched with:
    error: "license_required"           - umbrella code agents switch on
    code:  <specific code>              - granular code (license_not_found, …)
    message:                            - human-readable
    pricing_url:                        - https://simdrive.dev/pricing
    trial_command_hint:                 - exact CLI string to start a trial
    auth_command_hint:                  - exact CLI string to install a key
  Hosts (Claude Code, Cursor) surface this verbatim so users can
  copy-paste the command without leaving the agent loop.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


PRICING_URL = "https://simdrive.dev/pricing"
TRIAL_COMMAND_HINT = "simdrive trial start --email <your-email>"
AUTH_COMMAND_HINT = "simdrive auth <your-license-key>"


from simdrive.errors import SimdriveError


@dataclass
class LicenseError(SimdriveError):
    """Raised for all license validation failures.

    Inherits from SimdriveError so the MCP tool wrapper's
    ``except errors.SimdriveError`` clause catches it automatically,
    preserving the structured error envelope rather than wrapping it as
    code="internal".

    ``to_dict()`` returns the W1.5 "license_required" envelope rather than the
    generic SimdriveError shape so agent hosts get pricing + command hints
    without parsing the message prose.
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
                # Umbrella code — host switches on this to render the upsell UX.
                "error": "license_required",
                # Granular code (license_not_found / license_expired / …) for
                # callers that want to differentiate trial-expired from missing.
                "code": self.code,
                "message": self.message,
                "details": self.details,
                "pricing_url": PRICING_URL,
                "trial_command_hint": TRIAL_COMMAND_HINT,
                "auth_command_hint": AUTH_COMMAND_HINT,
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
            f"Your SimDrive license is invalid: {reason}. "
            "Recovery: run `simdrive license show` to inspect it, "
            "`simdrive auth <your-license-key>` to install a new one, or "
            "`simdrive trial start --email you@example.com` to begin a trial."
        ),
        details={"reason": reason},
    )


def license_expired(expires_at: int) -> LicenseError:
    return LicenseError(
        code="license_expired",
        message=(
            "SimDrive Pro license required — your trial has expired. "
            f"Recovery: renew at {PRICING_URL} and run "
            "`simdrive auth <your-license-key>` to reactivate. "
            f"(License expired at {expires_at}.)"
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
            "No SimDrive license found. "
            "Recovery: run `simdrive trial start --email you@example.com` to "
            "start a 14-day trial, or `simdrive auth <your-license-key>` if "
            f"you already have a paid key. (Looked at: {path})"
        ),
        details={"path": path},
    )


def cloud_unreachable(detail: str) -> LicenseError:
    return LicenseError(
        code="cloud_unreachable",
        message=(
            f"Could not reach the license server: {detail}. "
            "Recovery: check your network connection, or use "
            "`simdrive trial start --email <you@example.com> --offline-dev` "
            "to self-issue a local dev trial without network access."
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
            "contact support@synctek.io to re-issue the license under an older key."
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
            "Recovery: try again tomorrow or contact support@synctek.io."
        ),
        details={"ip": ip},
    )
