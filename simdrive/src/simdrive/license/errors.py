"""License-domain error codes.

LicenseError inherits from SimdriveError so the MCP wrapper's
``except errors.SimdriveError`` clause automatically handles it and
preserves the structured error envelope (code, message, details).

Error codes surfaced here:
  - license_invalid
  - license_expired
  - license_offline_grace_exhausted
  - license_tier_insufficient
  - trial_already_used
  - license_not_found
  - trial_rate_limited
  - cloud_unreachable

UX envelope (INIT-2026-549 W1.5):
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


def trial_rate_limited(ip: str) -> LicenseError:
    return LicenseError(
        code="trial_rate_limited",
        message=(
            f"Too many trial requests from {ip!r} (limit: 5/IP/day). "
            "Recovery: try again tomorrow or contact support@synctek.io."
        ),
        details={"ip": ip},
    )
