"""Trial-start source attribution telemetry — **true opt-in**, privacy-first.

When a user runs ``simdrive trial start --email <addr> --source <channel>``,
this module *can* fire a single, fire-and-forget POST to the SimDrive Worker
so we can attribute which marketing channel drove the trial. The payload is
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
* **Opt-in by default is OFF.** No POST is made unless the user has
  *explicitly and durably* opted in. The only opt-in signals are:
    - a persisted config at ``~/.simdrive/telemetry.toml`` containing
      ``track = true`` (durable), or
    - a per-invocation ``--track`` flag (``track=True`` here).
  Without one of these, the network call is **never attempted**.
* **One kill-switch.** ``HEKA_TELEMETRY`` set to an off value
  (``off`` / ``0`` / ``false`` / ``no`` / ``none`` / ``disabled``) severs
  *every* telemetry path — it overrides even an explicit opt-in. This is the
  single, provable cross-Heka switch (see the ``HEKA-SIM-NOSAAS`` claim).
* Raw email is **never** sent. Only SHA-256 of the normalized
  (lowercased, stripped) email leaves the machine, and only when opted in.
* ``--no-track`` and the legacy ``SIMDRIVE_TELEMETRY_OFF=1`` also skip the
  call (redundant now that OFF is the default, kept for compatibility).
* Network failure is non-fatal. The trial license is generated locally
  regardless; we print a single "telemetry skipped" notice and exit 0.

This file has zero hard dependencies beyond stdlib + ``requests`` (already
in the package's runtime deps for license-cloud calls).
"""
from __future__ import annotations

import hashlib
import os
import platform
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

# The single cross-Heka telemetry kill-switch. When ``HEKA_TELEMETRY`` is set
# to any of these values, EVERY simdrive telemetry path short-circuits to a
# no-op — this overrides even an explicit opt-in. Unset means "not killed"
# (telemetry is still opt-in-by-default OFF elsewhere).
_KILL_SWITCH_ENV = "HEKA_TELEMETRY"
_KILL_SWITCH_OFF_VALUES = frozenset(
    {"off", "0", "false", "no", "none", "disabled"}
)


def telemetry_killed() -> bool:
    """Return True iff the ``HEKA_TELEMETRY`` kill-switch is set to an off value.

    This is the ONE provable switch that severs every simdrive telemetry path.
    It takes precedence over any opt-in. Unset → not killed.
    """
    raw = os.environ.get(_KILL_SWITCH_ENV)
    if raw is None:
        return False
    return raw.strip().lower() in _KILL_SWITCH_OFF_VALUES


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
    """Return True iff telemetry must NOT fire (**true opt-in — default OUT**).

    Telemetry is disabled unless the user has *explicitly and durably* opted
    in. Precedence (first decisive signal wins):

    1. ``HEKA_TELEMETRY`` kill-switch off → opted out (hard mute; overrides
       any opt-in).
    2. ``SIMDRIVE_TELEMETRY_OFF=1`` (legacy) → opted out.
    3. Persisted config at ``opt_out_path``:
       - File contains ``track = true``  → opted **IN** (the one durable
         opt-in signal).
       - File contains ``track = false`` → opted out.
       - File present with no ``track`` key → opted out.
    4. No config file → opted **OUT** (privacy-safe default; no POST is ever
       attempted without a recorded opt-in).

    A malformed config defaults to opt-out (fail-closed for privacy).
    """
    if telemetry_killed():
        return True
    if os.environ.get("SIMDRIVE_TELEMETRY_OFF") == "1":
        return True
    if not opt_out_path.exists():
        return True  # DEFAULT OFF — no explicit opt-in on record
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
                return False  # explicit, durable opt-in
            return True       # track = false / any other value → opted out
    # File present but no ``track`` key — no explicit opt-in → opted out.
    return True


def write_opt_in(opt_out_path: Path = _DEFAULT_OPT_OUT_PATH) -> Path:
    """Persist an explicit opt-**IN** (``track = true``).

    This is the documented, durable way a user consents to source-attribution
    telemetry. Since OFF is the default, this file is the ONLY thing (besides a
    per-invocation ``--track``) that enables the POST. Returns the path written.
    """
    opt_out_path.parent.mkdir(parents=True, exist_ok=True)
    opt_out_path.write_text(
        "# SimDrive telemetry opt-IN. Telemetry is OFF by default; this file\n"
        "# is the only durable thing that turns source-attribution on for\n"
        "# `simdrive trial start`. Delete it (or set track = false) to opt out.\n"
        'track = true\n',
        encoding="utf-8",
    )
    return opt_out_path


def write_opt_out(opt_out_path: Path = _DEFAULT_OPT_OUT_PATH) -> Path:
    """Persist an explicit opt-out config (``track = false``).

    Mostly redundant now that OFF is the default, but kept so a user can make
    the opt-out visible/explicit on disk. Returns the path that was written.
    """
    opt_out_path.parent.mkdir(parents=True, exist_ok=True)
    opt_out_path.write_text(
        "# SimDrive telemetry opt-out (explicit). Telemetry is OFF by default;\n"
        "# this file records the opt-out visibly. Set track = true to opt in.\n"
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
    # Defense in depth: the kill-switch severs the network path at the
    # narrowest boundary, so even a *direct* call can never phone home when
    # HEKA_TELEMETRY is off. This is what makes HEKA-SIM-NOSAAS provable.
    if telemetry_killed():
        return False, "telemetry disabled (HEKA_TELEMETRY kill-switch)"
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
    track: bool = False,
    opt_out_path: Path = _DEFAULT_OPT_OUT_PATH,
    worker_url: str = _DEFAULT_WORKER_URL,
) -> str:
    """Resolve consent, send only if opted in, return a one-line CLI notice.

    Telemetry is **opt-in-by-default OFF**. The POST is attempted only when
    the user has opted in — either per-invocation (``track=True``, the
    ``--track`` flag) or durably (``track = true`` in ``opt_out_path``) — and
    the ``HEKA_TELEMETRY`` kill-switch is not off.

    The CLI prints whatever string this returns. Never raises.
    """
    # Kill-switch first — a single provable off signal beats everything.
    if telemetry_killed():
        return "telemetry disabled (HEKA_TELEMETRY kill-switch)"
    if no_track:
        return "telemetry opted out (--no-track)"
    if track:
        # Explicit per-invocation opt-in (documented CLI consent).
        _ok, msg = send_trial_attribution(email, source, worker_url=worker_url)
        return msg
    if is_opted_out(opt_out_path):
        return (
            "telemetry off (opt-in is OFF by default — enable durably with "
            f"`track = true` in {opt_out_path}, or pass --track for one run)"
        )
    _ok, msg = send_trial_attribution(email, source, worker_url=worker_url)
    return msg
