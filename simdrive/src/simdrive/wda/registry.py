"""Per-UDID WDA registry: load/save ~/.simdrive/wda/<udid>.json.

This file is the single source of truth for where WDA is running on a given
device. bootstrap.py writes it; client.py reads it. Nothing else writes here.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Optional


def _wda_dir() -> Path:
    """Return the directory that holds per-UDID WDA registry files.

    Overridable via WDA_REGISTRY_DIR env var for unit-test isolation.
    """
    override = os.environ.get("WDA_REGISTRY_DIR")
    if override:
        return Path(override)
    return Path.home() / ".simdrive" / "wda"


def registry_path(udid: str) -> Path:
    return _wda_dir() / f"{udid}.json"


def load(udid: str) -> Optional[dict]:
    """Load the registry entry for udid. Returns None if not found."""
    path = registry_path(udid)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def save(udid: str, entry: dict) -> Path:
    """Persist the registry entry for udid. Returns the written path."""
    path = registry_path(udid)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(entry, indent=2), encoding="utf-8")
    return path


def delete(udid: str) -> None:
    """Remove the registry entry for udid (no-op if absent)."""
    path = registry_path(udid)
    try:
        path.unlink()
    except FileNotFoundError:
        pass
