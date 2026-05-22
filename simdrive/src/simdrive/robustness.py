"""Robustness helpers — alerts, permissions, appearance, sheets, replay listing/validation."""
from __future__ import annotations

import subprocess
from pathlib import Path

import yaml


_ALERT_BUTTON_TEXTS = {
    "allow", "allow once", "allow while using app", "ok",
    "don't allow", "dont allow", "cancel", "deny",
    "settings",
}


def alert_button_match(marks, choice: str):
    """Return the Mark to tap for `choice` ("allow"|"deny"), else None.

    Matching is case-insensitive against `_ALERT_BUTTON_TEXTS`. "allow" prefers
    Allow / Allow Once / OK / Allow While Using App. "deny" prefers Don't Allow /
    Cancel / Deny.
    """
    allow_priority = ["allow", "allow once", "allow while using app", "ok"]
    deny_priority = ["don't allow", "dont allow", "cancel", "deny"]
    priority = allow_priority if choice == "allow" else deny_priority

    by_text: dict[str, object] = {}
    for m in marks or []:
        t = (getattr(m, "text", "") or "").strip().lower()
        if t in _ALERT_BUTTON_TEXTS:
            by_text.setdefault(t, m)
    for key in priority:
        if key in by_text:
            return by_text[key]
    return None


# --------------------------- pre-grant -------------------------------- #


_VALID_PERMISSIONS = {
    "all", "calendar", "contacts-limited", "contacts", "location", "location-always",
    "photos-add", "photos", "media-library", "microphone", "motion", "reminders",
    "siri", "speech", "camera", "homekit", "health", "medialibrary",
}


def grant_permissions(udid: str, bundle_id: str, permissions: list[str]) -> dict:
    granted: list[str] = []
    failed: list[dict] = []
    for perm in permissions:
        # Pass-through to simctl. Names like "photos", "camera", etc. map directly.
        res = subprocess.run(
            ["xcrun", "simctl", "privacy", udid, "grant", perm, bundle_id],
            capture_output=True, text=True, timeout=10.0, check=False,
        )
        if res.returncode == 0:
            granted.append(perm)
        else:
            failed.append({"permission": perm, "stderr": res.stderr.strip()[:200]})
    return {"ok": len(failed) == 0, "granted": granted, "failed": failed}


# --------------------------- appearance ------------------------------- #


def set_appearance(udid: str, appearance: str) -> dict:
    if appearance not in ("light", "dark"):
        return {"ok": False, "error": "appearance must be 'light' or 'dark'"}
    res = subprocess.run(
        ["xcrun", "simctl", "ui", udid, "appearance", appearance],
        capture_output=True, text=True, timeout=10.0, check=False,
    )
    if res.returncode != 0:
        return {"ok": False, "error": res.stderr.strip()[:200], "appearance": appearance}
    return {"ok": True, "appearance": appearance}


# ---------------------- replay listing/validation --------------------- #


_VALID_REPLAY_ACTIONS = {"tap", "swipe", "type_text", "press_key"}

# Top-level recording.yaml keys that are known and optional. validate_replay
# does not require them, but listing here is a hint to readers that these are
# not "extra" fields. `requires` was added in a9.0 for the state contract.
_KNOWN_OPTIONAL_KEYS = {
    "device", "os_version", "app_bundle_id", "app_version",
    "simdrive_version", "created_by_session", "screenshot_size_pixels",
    "tags", "ssim_masks",
    "requires",  # a9.0 — state contract; contents not lint-checked here
}


def list_replays(replays_root: Path, min_steps: int = 1) -> list[dict]:
    """Surface recordings under `replays_root/<name>/recording.yaml` with metadata.

    Args:
        replays_root: Root directory containing recording subdirectories.
        min_steps: Minimum number of steps a recording must have to be included.
            Default is 1, which filters out 0-step placeholder recordings.
            Pass 0 to include all recordings.
    """
    if not replays_root.exists():
        return []
    out: list[dict] = []
    for recording_yaml in sorted(replays_root.glob("*/recording.yaml")):
        try:
            with recording_yaml.open("r", encoding="utf-8") as f:
                data = yaml.safe_load(f)
        except Exception:
            continue
        if not isinstance(data, dict):
            continue
        step_count = len(data.get("steps") or [])
        # F#13: filter out 0-step placeholder entries by default.
        if step_count < min_steps:
            continue
        try:
            stat = recording_yaml.stat()
        except OSError:
            continue
        out.append({
            "name": data.get("name", recording_yaml.parent.name),
            "path": str(recording_yaml),
            "steps": step_count,
            "created_at": data.get("created_at"),
            "modified_at": stat.st_mtime,
            "simdrive_version": data.get("simdrive_version", ""),
            "tags": list(data.get("tags") or []),
        })
    return out


def validate_replay(replays_root: Path, name: str) -> dict:
    errors: list[str] = []
    warnings: list[str] = []

    rec_dir = replays_root / name
    yaml_path = rec_dir / "recording.yaml"
    if not yaml_path.exists():
        return {
            "ok": False,
            "errors": [f"recording.yaml not found at {yaml_path}"],
            "warnings": [],
            "step_count": 0,
            "simdrive_version": "",
        }

    try:
        with yaml_path.open("r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except Exception as exc:
        return {
            "ok": False,
            "errors": [f"YAML parse error: {exc}"],
            "warnings": [],
            "step_count": 0,
            "simdrive_version": "",
        }

    if not isinstance(data, dict):
        return {
            "ok": False,
            "errors": ["recording.yaml top level is not a mapping"],
            "warnings": [],
            "step_count": 0,
            "simdrive_version": "",
        }

    for required in ("name", "created_at", "steps"):
        if required not in data:
            errors.append(f"missing top-level field: {required}")

    steps = data.get("steps") or []
    if not isinstance(steps, list):
        errors.append("'steps' is not a list")
        steps = []

    for i, step in enumerate(steps):
        if not isinstance(step, dict):
            errors.append(f"step {i}: not a mapping")
            continue
        for field_name in ("id", "action", "args", "pre_screenshot"):
            if field_name not in step:
                errors.append(f"step {i}: missing field {field_name!r}")
        action = step.get("action")
        if action and action not in _VALID_REPLAY_ACTIONS:
            errors.append(f"step {i}: unsupported action {action!r}")
        pre = step.get("pre_screenshot")
        if pre:
            ref = (rec_dir / pre) if not Path(pre).is_absolute() else Path(pre)
            if not ref.exists():
                errors.append(f"step {i}: pre_screenshot file missing: {ref}")
        post = step.get("post_screenshot")
        if post:
            ref = (rec_dir / post) if not Path(post).is_absolute() else Path(post)
            if not ref.exists():
                warnings.append(f"step {i}: post_screenshot file missing: {ref}")

    return {
        "ok": len(errors) == 0,
        "errors": errors,
        "warnings": warnings,
        "step_count": len(steps),
        "simdrive_version": data.get("simdrive_version", ""),
    }
