"""SpecterQA iOS configuration helpers.

Provides persistent config via ~/.specterqa/config.toml so that
opt-in flags (like allow_physical_device) survive Claude Code's
failure to propagate MCP server env blocks (Issue 1 / Maurice Report).

Config file format (TOML-subset, hand-parsed — no tomllib dep on Py<3.11):

    [mcp]
    allow_physical_device = true

Public API
----------
_specterqa_config_dir()         Return Path to ~/.specterqa/ (overridable in tests).
_read_physical_opt_in()         Read allow_physical_device from config file.
write_physical_opt_in(enabled)  Write/update allow_physical_device in config file.
_check_physical_opt_in()        Gate check: env var OR config file OR keychain.
"""
from __future__ import annotations

import logging
import os
import subprocess
from pathlib import Path
from typing import Any

logger = logging.getLogger("specterqa.ios.config")

# ---------------------------------------------------------------------------
# Config directory
# ---------------------------------------------------------------------------


def _specterqa_config_dir() -> Path:
    """Return ~/.specterqa/ — override in tests via patch."""
    return Path.home() / ".specterqa"


# ---------------------------------------------------------------------------
# Minimal TOML reader/writer (stdlib-only, no tomllib dependency)
# ---------------------------------------------------------------------------


def _parse_toml_simple(text: str) -> dict[str, Any]:
    """Parse a minimal subset of TOML: [section] headers and key = value lines.

    Supports:
    - Bare section headers: [section]
    - String values: key = "value"
    - Boolean values: key = true | false
    - Integer values: key = 42
    - Comments: # ...

    Returns:
        dict with nested dicts for each section.
    """
    result: dict[str, Any] = {}
    current_section: dict[str, Any] = result

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue

        # Section header
        if line.startswith("[") and line.endswith("]") and "." not in line:
            section_name = line[1:-1].strip()
            if section_name not in result:
                result[section_name] = {}
            current_section = result[section_name]
            continue

        # Key = value
        if "=" in line:
            key, _, raw_value = line.partition("=")
            key = key.strip()
            raw_value = raw_value.strip()

            # Remove inline comments
            if " #" in raw_value:
                raw_value = raw_value[: raw_value.index(" #")].strip()

            # Boolean
            if raw_value.lower() == "true":
                current_section[key] = True
            elif raw_value.lower() == "false":
                current_section[key] = False
            # String
            elif raw_value.startswith('"') and raw_value.endswith('"'):
                current_section[key] = raw_value[1:-1]
            elif raw_value.startswith("'") and raw_value.endswith("'"):
                current_section[key] = raw_value[1:-1]
            # Integer
            elif raw_value.lstrip("-").isdigit():
                current_section[key] = int(raw_value)
            else:
                current_section[key] = raw_value

    return result


def _serialize_toml_simple(data: dict[str, Any]) -> str:
    """Serialize a nested dict to a minimal TOML string."""
    lines: list[str] = []
    top_level_scalars = {k: v for k, v in data.items() if not isinstance(v, dict)}
    sections = {k: v for k, v in data.items() if isinstance(v, dict)}

    for key, value in sorted(top_level_scalars.items()):
        lines.append(f"{key} = {_toml_value(value)}")

    for section, contents in sorted(sections.items()):
        if lines:
            lines.append("")
        lines.append(f"[{section}]")
        for key, value in sorted(contents.items()):
            lines.append(f"{key} = {_toml_value(value)}")

    return "\n".join(lines) + "\n" if lines else ""


def _toml_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, str):
        escaped = value.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    return str(value)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def _read_physical_opt_in() -> bool:
    """Return True if allow_physical_device is set to true in config.toml."""
    config_dir = _specterqa_config_dir()
    toml_path = config_dir / "config.toml"
    if not toml_path.exists():
        return False
    try:
        text = toml_path.read_text(encoding="utf-8")
        parsed = _parse_toml_simple(text)
        mcp_section = parsed.get("mcp", {})
        return bool(mcp_section.get("allow_physical_device", False))
    except Exception as exc:  # noqa: BLE001
        logger.debug("Failed to parse %s: %s", toml_path, exc)
        return False


def write_physical_opt_in(enabled: bool) -> None:
    """Write allow_physical_device to ~/.specterqa/config.toml.

    Idempotent — safe to call multiple times. Preserves other keys.
    Creates the directory and file if they don't exist.

    Args:
        enabled: True to enable, False to disable.
    """
    config_dir = _specterqa_config_dir()
    config_dir.mkdir(parents=True, exist_ok=True)
    toml_path = config_dir / "config.toml"

    # Load existing config if present
    if toml_path.exists():
        try:
            text = toml_path.read_text(encoding="utf-8")
            data = _parse_toml_simple(text)
        except Exception:  # noqa: BLE001
            data = {}
    else:
        data = {}

    # Set the key
    if "mcp" not in data:
        data["mcp"] = {}
    data["mcp"]["allow_physical_device"] = enabled

    toml_path.write_text(_serialize_toml_simple(data), encoding="utf-8")
    logger.info("Wrote allow_physical_device=%s to %s", enabled, toml_path)


def _read_keychain_opt_in() -> bool:
    """Check macOS Keychain for specterqa-ios/allow-physical = 1.

    Uses `security find-generic-password` via subprocess.
    Returns False if keychain is unavailable or the item is absent.
    """
    try:
        result = subprocess.run(
            ["security", "find-generic-password", "-s", "specterqa-ios", "-a", "allow-physical", "-w"],
            capture_output=True,
            text=True,
            timeout=3,
        )
        if result.returncode == 0:
            return result.stdout.strip() == "1"
    except Exception:  # noqa: BLE001
        pass
    return False


def _check_physical_opt_in() -> dict:
    """Check whether physical device opt-in is active via any mechanism.

    Checks (in order):
    1. SPECTERQA_ALLOW_PHYSICAL_DEVICE env var
    2. ~/.specterqa/config.toml [mcp] allow_physical_device = true
    3. macOS Keychain item specterqa-ios/allow-physical = 1

    Returns:
        {
          "allowed": bool,
          "diagnostics": {
            "env_var_seen_by_process": bool,
            "config_file_value": bool,
            "keychain_value": bool,
          }
        }
    """
    env_val = os.environ.get("SPECTERQA_ALLOW_PHYSICAL_DEVICE", "").strip().lower()
    env_allowed = env_val in ("1", "true", "yes")

    config_allowed = _read_physical_opt_in()
    keychain_allowed = _read_keychain_opt_in()

    allowed = env_allowed or config_allowed or keychain_allowed

    return {
        "allowed": allowed,
        "diagnostics": {
            "env_var_seen_by_process": env_allowed,
            "config_file_value": config_allowed,
            "keychain_value": keychain_allowed,
        },
    }
