"""CLI subcommand dispatchers for license management.

Subcommands:
  simdrive trial start --email <email>
  simdrive license activate <key>
  simdrive license status

These functions are called by server.py / the CLI entry point (Atlas wires
them into the click group during integration). Each function is standalone
and testable without the click layer.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Optional

import requests

from simdrive.license.errors import LicenseError
from simdrive.license.trial import (
    load_license_data,
    start_trial,
    update_server_check_time,
)

_DEFAULT_LICENSE_PATH = Path.home() / ".simdrive" / "license.json"
_DEFAULT_SERVER_URL = "https://cloud.simdrive.dev"


def cmd_trial_start(
    email: str,
    *,
    server_url: str = _DEFAULT_SERVER_URL,
    license_path: Path = _DEFAULT_LICENSE_PATH,
) -> dict:
    """Start a 14-day trial via the license server.

    Returns a dict with: key, expires_at, message.
    Raises on network error or rate-limit (LicenseError).
    """
    resp = requests.post(
        f"{server_url}/v1/trials",
        json={"email": email},
        timeout=15,
    )
    if resp.status_code == 429:
        from simdrive.license.errors import trial_rate_limited
        raise trial_rate_limited(email)
    resp.raise_for_status()

    data = resp.json()
    key: str = data["key"]
    expires_at: int = data["expires_at"]

    start_trial(
        email=email,
        license_key=key,
        expires_at=expires_at,
        license_path=license_path,
    )

    return {
        "key": key,
        "expires_at": expires_at,
        "message": (
            f"Trial activated! Your 14-day Pro trial expires "
            f"at {expires_at}. Run `simdrive license status` to verify."
        ),
    }


def cmd_license_activate(
    key: str,
    *,
    license_path: Path = _DEFAULT_LICENSE_PATH,
) -> dict:
    """Install a purchased license key.

    Writes to license.json and verifies locally (offline-capable).
    """
    from simdrive.license.public_key import get_public_key
    from simdrive.license.validator import validate_license

    vk = get_public_key()
    payload = validate_license(key, verify_key=vk)

    license_path.parent.mkdir(parents=True, exist_ok=True)
    data: dict = {
        "license_key": key,
        "email": payload.get("customer_email", ""),
        "expires_at": payload["expires_at"],
        "installed_at": int(time.time()),
        "last_server_check": None,
        "last_known_server_time": None,
    }
    license_path.write_text(json.dumps(data, indent=2))

    return {
        "tier": payload["tier"],
        "seats": payload["seats"],
        "expires_at": payload["expires_at"],
        "message": f"License activated: {payload['tier']} tier, {payload['seats']} seat(s).",
    }


def cmd_license_status(
    *,
    server_url: str = _DEFAULT_SERVER_URL,
    license_path: Path = _DEFAULT_LICENSE_PATH,
) -> dict:
    """Show current license status, refreshing from server if possible.

    Attempts a server refresh; falls back to offline validation.
    Returns a dict with: valid, tier, seats, expires_at, mode, message.
    """
    if not license_path.exists():
        return {
            "valid": False,
            "mode": "no_license",
            "message": (
                "No license found. Run `simdrive trial start --email <you@example.com>` "
                "to begin a 14-day free trial."
            ),
        }

    try:
        disk_data = load_license_data(license_path)
    except Exception as exc:
        return {"valid": False, "mode": "read_error", "message": str(exc)}

    key = disk_data.get("license_key", "")
    mode = "offline"
    server_time: Optional[int] = None

    # Try online refresh
    try:
        resp = requests.get(
            f"{server_url}/v1/licenses/status",
            params={"key": key},
            timeout=5,
        )
        if resp.ok:
            resp_data = resp.json()
            server_time = resp_data.get("server_time")
            if server_time:
                update_server_check_time(server_time, license_path)
            mode = "online"
    except Exception:
        pass  # fall through to offline check

    from simdrive.license.public_key import get_public_key
    from simdrive.license.validator import validate_license

    try:
        payload = validate_license(
            key,
            verify_key=get_public_key(),
            last_known_server_time=server_time,
        )
        return {
            "valid": True,
            "tier": payload["tier"],
            "seats": payload["seats"],
            "expires_at": payload["expires_at"],
            "mode": mode,
            "message": f"License valid: {payload['tier']} tier, expires {payload['expires_at']}.",
        }
    except LicenseError as exc:
        return {
            "valid": False,
            "code": exc.code,
            "mode": mode,
            "message": exc.message,
        }
