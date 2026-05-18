"""CLI subcommand dispatchers for license management.

Subcommands:
  simdrive trial start --email <email>
  simdrive trial start --email <email> --offline-dev
  simdrive license activate <key>
  simdrive license status
  simdrive auth <license-key>           (writes a paid key + validates locally)

These functions are called by server.py / the CLI entry point. Each
function is standalone and testable without the click layer.
"""
from __future__ import annotations

import base64
import json
import os
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


def _issue_dev_license(email: str) -> tuple[str, int]:
    """Self-issue a 14-day offline dev trial license using the embedded dev signing key.

    Returns (license_key_str, expires_at_unix_ts).
    The license is signed by DEV_SIGNING_KEY and only validates when the
    payload contains subject='dev-trial'. The validator enforces this.
    """
    from simdrive.license.public_key import get_dev_signing_key

    sk = get_dev_signing_key()
    expires_at = int(time.time()) + 14 * 86400

    payload: dict = {
        "subject": "dev-trial",
        "tier": "trial",
        "seats": 1,
        "customer_email": email,
        "issued_at": int(time.time()),
        "expires_at": expires_at,
    }

    payload_b64 = base64.urlsafe_b64encode(
        json.dumps(payload, separators=(",", ":")).encode("utf-8")
    ).rstrip(b"=").decode("ascii")

    signed = sk.sign(payload_b64.encode("ascii"))
    # nacl SignedMessage: first 64 bytes are signature, rest is message
    sig_bytes = bytes(signed.signature)
    sig_b64 = base64.urlsafe_b64encode(sig_bytes).rstrip(b"=").decode("ascii")

    key = f"{payload_b64}.{sig_b64}"
    return key, expires_at


def cmd_trial_start(
    email: str,
    *,
    server_url: str = _DEFAULT_SERVER_URL,
    license_path: Path = _DEFAULT_LICENSE_PATH,
    offline_dev: bool = False,
) -> dict:
    """Start a 14-day trial — cloud or offline-dev.

    Parameters
    ----------
    email:
        User's email address.
    server_url:
        License server base URL (default: https://cloud.simdrive.dev).
    license_path:
        Where to write license.json (default: ~/.simdrive/license.json).
    offline_dev:
        When True (or env SIMDRIVE_OFFLINE_DEV=1), skip the cloud call and
        self-issue a local dev trial using the embedded dev signing key.
        When False: attempt cloud; on network failure raise LicenseError
        (code="cloud_unreachable") — does NOT silently fall back to offline.

    Returns a dict with: key, expires_at, message.
    Raises LicenseError on network error (offline_dev=False) or rate-limit.
    """
    # Honour SIMDRIVE_OFFLINE_DEV env var
    if os.environ.get("SIMDRIVE_OFFLINE_DEV") == "1":
        offline_dev = True

    if offline_dev:
        # Trial-history check: refuse to issue a second trial for the same
        # (email, machine) pair. Bypasses if the file is missing/corrupt.
        from simdrive.license import trial_history
        from simdrive.license.errors import trial_already_used
        if trial_history.already_issued(email):
            raise trial_already_used(email)

        key, expires_at = _issue_dev_license(email)
        start_trial(
            email=email,
            license_key=key,
            expires_at=expires_at,
            license_path=license_path,
        )
        trial_history.record_issued(email)
        return {
            "key": key,
            "expires_at": expires_at,
            "message": (
                f"Offline dev trial activated for {email}. "
                f"Expires in 14 days (at {expires_at}). "
                "Run `simdrive license show` to verify."
            ),
        }

    # Cloud path
    from simdrive.license.errors import cloud_unreachable, trial_rate_limited

    try:
        resp = requests.post(
            f"{server_url}/v1/trials",
            json={"email": email},
            timeout=15,
        )
    except requests.exceptions.ConnectionError as exc:
        raise cloud_unreachable(str(exc)) from exc
    except requests.exceptions.Timeout as exc:
        raise cloud_unreachable(f"request timed out: {exc}") from exc

    if resp.status_code == 429:
        raise trial_rate_limited(email)
    resp.raise_for_status()

    data = resp.json()
    key = data["key"]
    expires_at = data["expires_at"]

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


def cmd_auth(
    key: str,
    *,
    license_path: Path = _DEFAULT_LICENSE_PATH,
    verify_key=None,
) -> dict:
    """Install a paid license key from ``simdrive auth <key>``.

    Validates the key locally (Ed25519 signature + expiry) before writing it to
    disk; invalid keys never touch ``license.json``. This is the user's entry
    point post-purchase: copy-paste the key string from the email/checkout
    receipt and the agent host immediately picks it up on the next tool call.

    Parameters
    ----------
    key:
        The base64url-encoded license key string.
    license_path:
        Override the destination path (default: ``~/.simdrive/license.json``).
    verify_key:
        Test-only override of the verification key. Production callers should
        leave this ``None`` so the embedded production key is used.

    Returns
    -------
    dict
        ``{"tier": str, "seats": int, "expires_at": int, "message": str}``.

    Raises
    ------
    LicenseError
        ``license_invalid`` / ``license_expired`` — the key is rejected before
        anything is written to disk.
    """
    from simdrive.license.public_key import get_public_key
    from simdrive.license.validator import validate_license

    vk = verify_key if verify_key is not None else get_public_key()
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
        "message": (
            f"License installed: {payload['tier']} tier, {payload['seats']} seat(s). "
            f"Stored at {license_path}."
        ),
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
