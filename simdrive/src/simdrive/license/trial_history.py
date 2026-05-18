"""Trial-history bookkeeping — prevents infinite trial extension.

INIT-2026-549 W1.5: a brand-new user can self-issue a 14-day trial without
talking to the cloud. To stop the same user re-running ``simdrive trial start``
forever, we record a one-way hash of (email, machine_fingerprint) in
``~/.simdrive/trial_history.json`` and reject subsequent issuance for the
same hash.

The history file is not authoritative — a sufficiently motivated user can
delete it (or move machines). That is fine; the trial economy assumes good
faith for the first 14 days, and W2 adds a server-side de-dupe via the cloud
issuance path.

Why a separate module instead of merging into ``trial.py``:
  * trial.py is consumed by tests that don't want to touch history files.
  * The history hash is a different concept (privacy-preserving identity) from
    license persistence, so they get separate seams to monkey-patch.
"""
from __future__ import annotations

import hashlib
import json
import platform
import time
import uuid
from pathlib import Path
from typing import Any


_DEFAULT_HISTORY_PATH = Path.home() / ".simdrive" / "trial_history.json"


def _machine_fingerprint() -> str:
    """Return a per-machine identifier used in the trial-history hash.

    Uses ``uuid.getnode()`` (MAC address) plus the machine + system strings to
    produce a stable, opaque string. Hashed before use — we never store the
    raw MAC.
    """
    parts = [
        str(uuid.getnode()),
        platform.machine(),
        platform.system(),
        platform.node(),
    ]
    return "|".join(parts)


def _email_machine_hash(email: str) -> str:
    """SHA-256 of (lowercased email + machine fingerprint).

    The hash is one-way — the history file never sees the raw email or MAC.
    Lowercase the email so ``Foo@Example.com`` and ``foo@example.com`` collide
    (same person, different capitalisation).
    """
    fp = _machine_fingerprint()
    payload = f"{email.strip().lower()}|{fp}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _resolve_path(path: Path | None) -> Path:
    """Resolve the history path lazily.

    Caller-supplied path wins; otherwise re-read the module-level constant on
    every call so tests can ``monkeypatch.setattr(trial_history,
    "_DEFAULT_HISTORY_PATH", tmp)``.
    """
    if path is not None:
        return path
    # Late lookup so monkey-patched test paths take effect.
    import simdrive.license.trial_history as _self
    return _self._DEFAULT_HISTORY_PATH


def load_history(path: Path | None = None) -> dict[str, Any]:
    """Return the history dict, or an empty skeleton if the file is absent."""
    real_path = _resolve_path(path)
    if not real_path.exists():
        return {"trials": {}}
    try:
        return json.loads(real_path.read_text())
    except (json.JSONDecodeError, OSError):
        # Corrupt history file → start fresh rather than blowing up.
        return {"trials": {}}


def already_issued(email: str, *, path: Path | None = None) -> bool:
    """Return True when a trial was previously issued for this (email, machine)."""
    history = load_history(path)
    return _email_machine_hash(email) in history.get("trials", {})


def record_issued(email: str, *, path: Path | None = None) -> None:
    """Append an entry to the history file for this (email, machine) pair."""
    real_path = _resolve_path(path)
    history = load_history(real_path)
    history.setdefault("trials", {})
    history["trials"][_email_machine_hash(email)] = {"issued_at": int(time.time())}
    real_path.parent.mkdir(parents=True, exist_ok=True)
    real_path.write_text(json.dumps(history, indent=2))
