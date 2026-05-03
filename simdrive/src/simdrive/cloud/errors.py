"""Cloud API error codes.

Atlas: add these codes to the global errors.py during integration:
  - cloud_auth_missing
  - cloud_auth_invalid
  - cloud_storage_quota_exceeded
  - cloud_recording_not_found
  - cloud_rate_limited (trial/IP rate limit)
"""
from __future__ import annotations

from typing import Any


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
