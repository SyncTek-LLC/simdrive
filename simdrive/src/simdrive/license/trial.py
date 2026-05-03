"""Trial state management — write/read license.json on disk.

WHY this module exists separately from entitlement.py: separation of concerns.
trial.py owns reading and writing the on-disk license.json; entitlement.py
owns interpreting it against a verify key.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any


_DEFAULT_LICENSE_PATH = Path.home() / ".simdrive" / "license.json"


def start_trial(
    *,
    email: str,
    license_key: str,
    expires_at: int,
    license_path: Path = _DEFAULT_LICENSE_PATH,
) -> str:
    """Persist a trial license key to disk.

    Parameters
    ----------
    email:
        User's email address (for display in `simdrive license status`).
    license_key:
        The signed key string returned by the license server.
    expires_at:
        Unix timestamp when the trial expires.
    license_path:
        Path to write license.json (default: ~/.simdrive/license.json).

    Returns
    -------
    str
        The stored license_key (for convenience).
    """
    license_path.parent.mkdir(parents=True, exist_ok=True)
    data: dict[str, Any] = {
        "license_key": license_key,
        "email": email,
        "expires_at": expires_at,
        "installed_at": int(time.time()),
        "last_server_check": None,
        "last_known_server_time": None,
    }
    license_path.write_text(json.dumps(data, indent=2))
    return license_key


def load_license_data(license_path: Path = _DEFAULT_LICENSE_PATH) -> dict[str, Any]:
    """Load raw license.json dict from disk.

    Raises FileNotFoundError if the file does not exist.
    """
    return json.loads(license_path.read_text())


def save_license_data(
    data: dict[str, Any],
    license_path: Path = _DEFAULT_LICENSE_PATH,
) -> None:
    """Persist updated license.json dict to disk."""
    license_path.parent.mkdir(parents=True, exist_ok=True)
    license_path.write_text(json.dumps(data, indent=2))


def update_server_check_time(
    server_time: int,
    license_path: Path = _DEFAULT_LICENSE_PATH,
) -> None:
    """Update last_known_server_time after a successful status check.

    Called after GET /v1/licenses/status returns successfully.
    This is the value used for clock-skew defense in validator.py.
    """
    try:
        data = load_license_data(license_path)
    except FileNotFoundError:
        return
    data["last_server_check"] = int(time.time())
    data["last_known_server_time"] = server_time
    save_license_data(data, license_path)
