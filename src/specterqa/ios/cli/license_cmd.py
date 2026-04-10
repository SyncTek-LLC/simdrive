"""license_cmd — ``specterqa-ios license`` CLI subcommand group.

Sub-commands:
  specterqa-ios license activate <key>   — validate key against Keygen.sh and write ~/.specterqa/auth.yaml
  specterqa-ios license status           — display current license info
  specterqa-ios license deactivate       — remove auth.yaml and clear local state

Environment variables:
  SPECTERQA_KEYGEN_ACCOUNT  — Keygen.sh account ID (required for activate)
  ANTHROPIC_API_KEY         — required for any test run (BYOK enforcement)
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any, Dict, Optional

import click
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

console = Console(stderr=True)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_AUTH_PATH = Path.home() / ".specterqa" / "auth.yaml"
_KEYGEN_BASE = "https://api.keygen.sh/v1"
_PURCHASE_URL = "https://synctek.io/specterqa#pricing"

# Tier → max concurrent simulators
TIER_SIM_LIMITS: Dict[str, int] = {
    "trial": 1,
    "indie": 2,
    "pro": 4,
    "team": 10,
    "enterprise": 0,  # 0 = unlimited
    "founder": 4,
}

# Trial run limit per session
TRIAL_MAX_RUNS = 3


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load_yaml_safe(path: Path) -> Dict[str, Any]:
    """Load a YAML file, returning {} on any error."""
    try:
        import yaml  # type: ignore[import-untyped]

        with path.open("r", encoding="utf-8") as fh:
            return yaml.safe_load(fh) or {}
    except Exception:
        return {}


def _write_yaml(path: Path, data: Dict[str, Any]) -> None:
    """Write *data* to *path* as YAML, creating parent dirs."""
    import yaml  # type: ignore[import-untyped]

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        yaml.safe_dump(data, fh, default_flow_style=False, allow_unicode=True)


def _read_auth() -> Optional[Dict[str, Any]]:
    """Return the contents of ~/.specterqa/auth.yaml, or None if absent."""
    if not _AUTH_PATH.exists():
        return None
    data = _load_yaml_safe(_AUTH_PATH)
    return data if data else None


def _keygen_validate(key: str, account: str) -> Dict[str, Any]:
    """Call Keygen.sh validate endpoint and return normalised dict.

    Returns:
        Dict with keys: valid (bool), tier (str), max_sims (int), expires_at (str|None),
        key (str), name (str).

    Raises:
        click.ClickException: on network error or non-200 response.
    """
    try:
        import httpx
    except ImportError:
        raise click.ClickException(
            "httpx is required for license activation.\n"
            "Install it with: pip install httpx"
        )

    url = f"{_KEYGEN_BASE}/accounts/{account}/licenses/{key}/validate"
    try:
        response = httpx.get(url, timeout=15.0)
    except httpx.RequestError as exc:
        raise click.ClickException(
            f"Network error contacting Keygen.sh: {exc}\n"
            "Check your internet connection and try again."
        )

    if response.status_code == 404:
        raise click.ClickException(
            f"License key not found: {key!r}\n"
            f"Purchase a license at: {_PURCHASE_URL}"
        )
    if response.status_code != 200:
        raise click.ClickException(
            f"Keygen.sh returned HTTP {response.status_code}.\n"
            "Try again in a moment, or contact support at support@synctek.io"
        )

    data = response.json()
    attrs = data.get("data", {}).get("attributes", {})
    status = attrs.get("status", "")
    metadata = attrs.get("metadata", {})
    name = data.get("data", {}).get("attributes", {}).get("name", key)

    tier = str(metadata.get("tier", "indie")).lower()
    raw_max = metadata.get("max_concurrent_sims")
    if raw_max is not None:
        max_sims = int(raw_max)
    else:
        max_sims = TIER_SIM_LIMITS.get(tier, 2)

    valid = status.upper() in ("ACTIVE", "VALID")
    expires_at = attrs.get("expiry")

    # Keygen validate endpoint embeds a `meta.valid` boolean on the top level
    meta_valid = data.get("meta", {}).get("valid")
    if meta_valid is not None:
        valid = bool(meta_valid)

    return {
        "valid": valid,
        "tier": tier,
        "max_sims": max_sims,
        "expires_at": expires_at,
        "key": key,
        "name": name,
    }


# ---------------------------------------------------------------------------
# CLI group
# ---------------------------------------------------------------------------


@click.group("license")
def license_group() -> None:
    """Manage your SpecterQA license key."""


# ---------------------------------------------------------------------------
# activate
# ---------------------------------------------------------------------------


@license_group.command("activate")
@click.argument("key")
def activate(key: str) -> None:
    """Validate KEY against Keygen.sh and save it locally.

    KEY is your SpecterQA license key (e.g. LIC-XXXX-XXXX-XXXX-XXXX).

    Requires SPECTERQA_KEYGEN_ACCOUNT environment variable.

    Purchase a license at: https://synctek.io/specterqa#pricing
    """
    account = os.environ.get("SPECTERQA_KEYGEN_ACCOUNT", "").strip()
    if not account:
        raise click.ClickException(
            "SPECTERQA_KEYGEN_ACCOUNT environment variable is not set.\n"
            "Set it to your Keygen.sh account ID before activating:\n"
            "  export SPECTERQA_KEYGEN_ACCOUNT=<your-account-id>"
        )

    console.print(f"[dim]Validating license key with Keygen.sh…[/dim]")

    result = _keygen_validate(key.strip(), account)

    if not result["valid"]:
        raise click.ClickException(
            f"License key {key!r} is not active (status returned by Keygen.sh indicates invalid).\n"
            f"Renew or purchase a license at: {_PURCHASE_URL}"
        )

    # Write auth.yaml
    auth_data: Dict[str, Any] = {
        "license_key": result["key"],
        "tier": result["tier"],
        "max_sims": result["max_sims"],
        "expires_at": result["expires_at"],
        "name": result["name"],
    }
    _write_yaml(_AUTH_PATH, auth_data)

    tier_label = result["tier"].capitalize()
    sims_label = "unlimited" if result["max_sims"] == 0 else str(result["max_sims"])
    expires_label = result["expires_at"] or "never"

    console.print(
        Panel(
            f"[bold green]License activated![/bold green]\n\n"
            f"  Tier:        [cyan]{tier_label}[/cyan]\n"
            f"  Simulators:  [cyan]{sims_label}[/cyan]\n"
            f"  Expires:     [cyan]{expires_label}[/cyan]\n"
            f"  Saved to:    [dim]{_AUTH_PATH}[/dim]",
            title="[green]SpecterQA License[/green]",
            border_style="green",
        )
    )

    # BYOK reminder
    if not os.environ.get("ANTHROPIC_API_KEY"):
        console.print(
            Panel(
                "[bold yellow]BYOK required before running tests.[/bold yellow]\n\n"
                "  export ANTHROPIC_API_KEY=sk-ant-...\n\n"
                "Get an API key at: https://console.anthropic.com/",
                title="[yellow]Anthropic API Key Missing[/yellow]",
                border_style="yellow",
            )
        )


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------


@license_group.command("status")
def status() -> None:
    """Show current license status and BYOK state."""
    auth = _read_auth()
    api_key_set = bool(os.environ.get("ANTHROPIC_API_KEY"))

    table = Table(show_header=False, box=None, padding=(0, 2))
    table.add_column("Field", style="dim")
    table.add_column("Value")

    if auth:
        tier = auth.get("tier", "unknown").capitalize()
        sims = auth.get("max_sims", 1)
        sims_label = "unlimited" if sims == 0 else str(sims)
        expires = auth.get("expires_at") or "never"
        key = auth.get("license_key", "")
        masked_key = f"{key[:8]}…{key[-4:]}" if len(key) > 12 else key

        table.add_row("License", f"[green]Active[/green]")
        table.add_row("Key", masked_key)
        table.add_row("Tier", f"[cyan]{tier}[/cyan]")
        table.add_row("Simulators", sims_label)
        table.add_row("Expires", expires)
    else:
        table.add_row("License", "[yellow]Trial mode (no license)[/yellow]")
        table.add_row("Simulators", "1 (trial limit)")
        table.add_row("Run limit", f"{TRIAL_MAX_RUNS} runs/session (trial limit)")
        table.add_row("Purchase", _PURCHASE_URL)

    table.add_row("ANTHROPIC_API_KEY", "[green]Set[/green]" if api_key_set else "[red]Not set[/red]")

    console.print(
        Panel(
            table,
            title="[bold]SpecterQA License Status[/bold]",
            border_style="blue",
        )
    )

    if not api_key_set:
        console.print(
            "[yellow]BYOK required:[/yellow] set ANTHROPIC_API_KEY before running tests.\n"
            "  export ANTHROPIC_API_KEY=sk-ant-...\n"
            "  Get a key at: https://console.anthropic.com/"
        )


# ---------------------------------------------------------------------------
# deactivate
# ---------------------------------------------------------------------------


@license_group.command("deactivate")
@click.confirmation_option(prompt="Remove local license file?")
def deactivate() -> None:
    """Remove the local license file (~/.specterqa/auth.yaml)."""
    if _AUTH_PATH.exists():
        _AUTH_PATH.unlink()
        console.print(f"[green]License deactivated.[/green] Removed: {_AUTH_PATH}")
    else:
        console.print("[dim]No license file found — nothing to remove.[/dim]")
