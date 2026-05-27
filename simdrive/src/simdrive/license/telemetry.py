"""Trial-start source attribution telemetry — opt-in, privacy-first.

When a user runs ``simdrive trial start --email <addr> --source <channel>``,
this module fires a single, fire-and-forget POST to the SimDrive Worker so
we can attribute which marketing channel drove the trial. The payload is
deliberately small:

    {
      "hashed_email":    "<sha256(email.lower().strip())>",
      "source":          "<--source value or 'direct'>",
      "ts":              "<ISO-8601 timestamp>",
      "package_version": "<simdrive package version>",
      "os":              "darwin" | "linux" | "other"
    }

Hard rules
----------
* Raw email is **never** sent. Only SHA-256 of the normalized
  (lowercased, stripped) email leaves the machine.
* ``--no-track`` skips the network call entirely.
* A persisted opt-out config at ``~/.simdrive/telemetry.toml`` containing
  ``track = false`` (or just being present with no ``track`` key) skips
  the network call permanently — no further prompts.
* Network failure is non-fatal. The trial license is generated locally
  regardless; we print a single "telemetry skipped" notice and exit 0.

This file has zero hard dependencies beyond stdlib + ``requests`` (already
in the package's runtime deps for license-cloud calls).
"""
from __future__ import annotations

import hashlib
import os
import platform
import sys
import time
from pathlib import Path
from typing import Optional

import requests

# Default endpoint for the trial-attribution Worker. The Worker is being
# deployed under a sibling initiative; if it's not yet live when 1.0.0b8
# ships, the POST will fail silently (caught by the broad except below)
# which is the documented behaviour.
_DEFAULT_WORKER_URL = "https://api.simdrive.dev/trial"

# Path to the user-level opt-out config. Presence of this file (with
# ``track = false`` *or* simply the file existing with no ``track`` key)
# permanently disables source-tracking. Mirrors the user's expectation
# that "I wrote a config that says no" should win over the default.
_DEFAULT_OPT_OUT_PATH = Path.home() / ".simdrive" / "telemetry.toml"

# Default channel string when --source is omitted but tracking is allowed.
_DEFAULT_SOURCE = "direct"


# ---------------------------------------------------------------------------
# Pure helpers (unit-testable, no side effects)
# ---------------------------------------------------------------------------


def hash_email(email: str) -> str:
    """Return SHA-256 hex digest of the normalized email.

    Normalization: lowercase + strip whitespace. Deterministic across
    platforms / Python versions / locales.
    """
    normalized = (email or "").strip().lower()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def os_family() -> str:
    """Return a coarse OS family tag for the payload — never a fingerprint.

    Returns one of: ``"darwin"``, ``"linux"``, ``"other"``. We deliberately
    do NOT send kernel version / arch / distro — just the family.
    """
    sysname = platform.system().lower()
    if sysname == "darwin":
        return "darwin"
    if sysname == "linux":
        return "linux"
    return "other"


def _package_version() -> str:
    """Best-effort lookup of the installed simdrive version."""
    try:
        from simdrive import __version__
        return __version__
    except Exception:  # pragma: no cover - defensive
        return "unknown"


def build_payload(
    email: str,
    source: Optional[str],
    *,
    now: Optional[float] = None,
) -> dict:
    """Construct the POST body. Never contains raw email."""
    ts_iso = time.strftime(
        "%Y-%m-%dT%H:%M:%SZ", time.gmtime(now if now is not None else time.time())
    )
    return {
        "hashed_email": hash_email(email),
        "source": source if source else _DEFAULT_SOURCE,
        "ts": ts_iso,
        "package_version": _package_version(),
        "os": os_family(),
    }


# ---------------------------------------------------------------------------
# Opt-out resolution
# ---------------------------------------------------------------------------


def is_opted_out(opt_out_path: Path = _DEFAULT_OPT_OUT_PATH) -> bool:
    """Return True iff a persisted opt-out config disables tracking.

    Precedence:

    1. ``SIMDRIVE_TELEMETRY_OFF=1`` env var → opt-out
    2. File exists at ``opt_out_path``:
       - File contains ``track = false`` → opt-out
       - File contains ``track = true``  → tracking allowed
       - File present with no ``track`` key → treat as opt-out (the user
         created the file, default-deny their intent)
    3. Otherwise → tracking allowed (default opt-in)

    A malformed config defaults to opt-out (fail-closed for privacy).
    """
    if os.environ.get("SIMDRIVE_TELEMETRY_OFF") == "1":
        return True
    if not opt_out_path.exists():
        return False
    try:
        text = opt_out_path.read_text(encoding="utf-8")
    except OSError:
        return True  # fail-closed
    # Minimal TOML scan — we only care about ``track = true|false``. Avoids
    # pulling in tomllib (3.11+) on 3.10 which would require ``tomli``.
    for raw_line in text.splitlines():
        line = raw_line.split("#", 1)[0].strip()
        if not line or "=" not in line:
            continue
        key, _, value = line.partition("=")
        if key.strip() == "track":
            normalized = value.strip().strip('"').strip("'").lower()
            if normalized == "true":
                return False
            if normalized == "false":
                return True
    # File present but no ``track`` key — default-deny.
    return True


def write_opt_out(opt_out_path: Path = _DEFAULT_OPT_OUT_PATH) -> Path:
    """Persist an opt-out config. Used by --no-track to make it sticky.

    Note: --no-track on a single invocation does NOT itself write the file —
    the user must pass the flag each time, or call this helper / hand-edit
    the config. This keeps the CLI flag side-effect-free (POLA).

    Returns the path that was written.
    """
    opt_out_path.parent.mkdir(parents=True, exist_ok=True)
    opt_out_path.write_text(
        "# SimDrive telemetry opt-out. Remove this file to re-enable\n"
        "# source-attribution telemetry on `simdrive trial start`.\n"
        'track = false\n',
        encoding="utf-8",
    )
    return opt_out_path


# ---------------------------------------------------------------------------
# Sender (network-touching — kept narrow so tests can monkeypatch one call)
# ---------------------------------------------------------------------------


def send_trial_attribution(
    email: str,
    source: Optional[str],
    *,
    worker_url: str = _DEFAULT_WORKER_URL,
    timeout: float = 3.0,
) -> tuple[bool, str]:
    """POST the source-attribution payload. Fire-and-forget; never raises.

    Returns ``(ok, message)``:
      * ``ok=True`` → POST returned 2xx (Worker accepted).
      * ``ok=False`` → network error, non-2xx, or anything else. Message
        is a short human-readable string the CLI can print as a notice.

    The trial license is generated locally regardless of this call's
    outcome. Callers should NEVER raise on a False return — that would
    defeat the "telemetry is non-fatal" invariant.
    """
    payload = build_payload(email, source)
    try:
        resp = requests.post(worker_url, json=payload, timeout=timeout)
        if 200 <= resp.status_code < 300:
            return True, f"telemetry sent (source={payload['source']})"
        return False, (
            f"telemetry skipped (worker returned {resp.status_code})"
        )
    except requests.exceptions.RequestException as exc:
        # Bucket everything network-shaped together. Keep the message
        # short — exact exception types are not useful to the user.
        return False, f"telemetry skipped (network unavailable: {type(exc).__name__})"
    except Exception as exc:  # pragma: no cover - defensive belt-and-braces
        return False, f"telemetry skipped ({type(exc).__name__})"


# ---------------------------------------------------------------------------
# Orchestration helper used by the CLI
# ---------------------------------------------------------------------------


def maybe_send_attribution(
    email: str,
    *,
    source: Optional[str],
    no_track: bool,
    opt_out_path: Path = _DEFAULT_OPT_OUT_PATH,
    worker_url: str = _DEFAULT_WORKER_URL,
) -> str:
    """Resolve opt-out, send if allowed, return a one-line notice for the CLI.

    The CLI prints whatever string this returns. Never raises.
    """
    if no_track:
        return "telemetry opted out (--no-track)"
    if is_opted_out(opt_out_path):
        return f"telemetry opted out ({opt_out_path})"
    _ok, msg = send_trial_attribution(email, source, worker_url=worker_url)
    return msg
