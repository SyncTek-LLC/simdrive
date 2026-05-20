"""Cloud API error codes.

Atlas: add these codes to the global errors.py during integration:
  - cloud_auth_missing
  - cloud_auth_invalid
  - cloud_storage_quota_exceeded
  - cloud_recording_not_found
  - cloud_rate_limited (trial/IP rate limit)
  - cloud_quota_exceeded (per-tool/per-month run quota)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from simdrive.errors import SimdriveError


def cloud_error(code: str, message: str, details: dict | None = None) -> dict[str, Any]:
    """Construct a JSON-serializable cloud API error envelope."""
    return {
        "ok": False,
        "error": {
            "code": code,
            "message": message,
            "details": details or {},
        },
    }


@dataclass
class QuotaExceededError(SimdriveError):
    """Raised when a license has consumed its per-month run quota.

    Inherits from SimdriveError so the MCP tool wrapper's structured
    error envelope path picks it up automatically. Carries the per-tool
    metadata Wave 2 needs to render a "you're at <N>/<M> runs this month"
    upsell message without an extra round-trip.
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


def quota_exceeded(
    *,
    tool_name: str,
    tier: str,
    runs_used: int,
    runs_limit: int,
) -> QuotaExceededError:
    """Construct a QuotaExceededError with a uniform message + details payload."""
    return QuotaExceededError(
        code="cloud_quota_exceeded",
        message=(
            f"SimDrive quota exceeded for {tier!r} tier: {runs_used}/{runs_limit} "
            f"runs used this month (tool: {tool_name!r}). "
            "Recovery: upgrade at https://simdrive.dev/pricing or wait for the "
            "monthly quota reset."
        ),
        details={
            "tool_name": tool_name,
            "tier": tier,
            "runs_used": runs_used,
            "runs_limit": runs_limit,
        },
    )
