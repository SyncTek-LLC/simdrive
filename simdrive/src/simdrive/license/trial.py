"""Trial state management — write/read license.json on disk.

WHY this module exists separately from entitlement.py: separation of concerns.
trial.py owns reading and writing the on-disk license.json; entitlement.py
owns interpreting it against a verify key.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Optional


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


def assert_trial_clock_trustworthy(
    license_data: dict[str, Any],
    *,
    system_clock: Optional[int] = None,
) -> None:
    """Refuse to grant offline grace when the system clock is untrustworthy.

    Wraps :func:`simdrive.license.validator.check_clock_skew_for_grace`
    with the on-disk ``last_known_server_time`` field — entitlement.py
    calls this right before validating an expired trial license so the
    7-day grace window cannot be exploited via clock backdating or
    indefinite-offline operation.

    Parameters
    ----------
    license_data:
        Dict loaded from ``license.json`` (see :func:`load_license_data`).
        Reads the ``last_known_server_time`` field.
    system_clock:
        Override for the system clock — defaults to ``time.time()``.
        Tests pass an explicit value to simulate skew.

    Raises
    ------
    ClockSkewError(code="license_clock_skew_detected")
        Either: system clock moved back > 6h relative to last known
        server time, OR no successful cloud check in > 30d.
    """
    from simdrive.license.validator import check_clock_skew_for_grace

    last_known = license_data.get("last_known_server_time")
    check_clock_skew_for_grace(
        last_known_server_time=last_known,
        system_clock=system_clock,
    )
