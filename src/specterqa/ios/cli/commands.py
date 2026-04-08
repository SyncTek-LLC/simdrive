"""SpecterQA iOS CLI commands.

Provides the ``ios_command_group`` (mounted as ``specterqa ios``) with
sub-commands for setting up and running AI-driven iOS Simulator tests.

Commands:
  specterqa ios setup              — verify Xcode and environment
  specterqa ios doctor             — diagnose the full SpecterQA environment
  specterqa ios init               — scaffold .specterqa/ for a new project
  specterqa ios devices            — list available iOS simulators
  specterqa ios boot               — boot a simulator by name
  specterqa ios install            — install a .app bundle on a simulator
  specterqa ios run                — run a test journey on iOS Simulator
  specterqa ios smoke              — quick smoke test for a product
  specterqa ios replay             — replay a recorded session without AI
  specterqa ios ci                 — run all replays in a directory (CI mode)
  specterqa ios validate-replay    — validate a replay YAML file
  specterqa ios runner             — manage the Swift XCTest runner
  specterqa ios wda                — manage WebDriverAgent
  specterqa ios serve              — start the MCP server
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any

import click
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

logger = logging.getLogger("specterqa.ios.cli")

console = Console(stderr=True)
out = Console()  # stdout — for machine-readable / piped output

# ---------------------------------------------------------------------------
# Version — guarded import so the CLI remains usable when the upstream
# specterqa package is installed in a broken or development state where
# __version__ is missing (e.g. editable installs missing PKG-INFO).
# ---------------------------------------------------------------------------

try:
    from specterqa import __version__ as _SPECTERQA_VERSION
except (ImportError, AttributeError):
    _SPECTERQA_VERSION = "unknown"


# ---------------------------------------------------------------------------
# Helpers — project config resolution
# ---------------------------------------------------------------------------


def _resolve_project_dir() -> Path:
    """Find .specterqa/ searching upward from cwd, mirroring the web CLI."""
    current = Path.cwd()
    for directory in [current, *current.parents]:
        candidate = directory / ".specterqa"
        if candidate.is_dir():
            return candidate
    return current / ".specterqa"


def _load_yaml(path: Path) -> dict[str, Any]:
    """Load a YAML file, returning a dict.  Raises click.ClickException on error."""
    try:
        import yaml  # type: ignore[import-untyped]

        with path.open("r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
        return data or {}
    except ImportError:
        # yaml not available — fall back to a minimal parser hint
        raise click.ClickException("PyYAML is required to read config files.\nInstall it with: pip install pyyaml")
    except FileNotFoundError:
        raise click.ClickException(f"File not found: {path}")
    except Exception as exc:
        raise click.ClickException(f"Failed to parse {path}: {exc}")


def _load_product(project_dir: Path, slug: str) -> dict[str, Any]:
    """Load .specterqa/products/<slug>.yaml."""
    path = project_dir / "products" / f"{slug}.yaml"
    data = _load_yaml(path)
    return data.get("product", data)  # support both wrapped and flat schemas


def _load_journey(project_dir: Path, journey_id: str) -> dict[str, Any]:
    """Load .specterqa/journeys/<journey_id>.yaml."""
    path = project_dir / "journeys" / f"{journey_id}.yaml"
    data = _load_yaml(path)
    return data.get("scenario", data)


def _load_persona(project_dir: Path, persona_name: str) -> dict[str, Any]:
    """Load .specterqa/personas/<persona_name>.yaml."""
    path = project_dir / "personas" / f"{persona_name}.yaml"
    data = _load_yaml(path)
    return data.get("persona", data)


# ---------------------------------------------------------------------------
# Helpers — simulator/xcrun wrappers
# ---------------------------------------------------------------------------


def _xcrun_available() -> bool:
    result = subprocess.run(
        ["xcrun", "--version"],
        capture_output=True,
        text=True,
    )
    return result.returncode == 0


def _list_simulators() -> dict[str, Any]:
    """Run ``xcrun simctl list devices --json`` and return the parsed dict."""
    result = subprocess.run(
        ["xcrun", "simctl", "list", "devices", "--json"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise click.ClickException(f"xcrun simctl list devices failed:\n{result.stderr}")
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise click.ClickException(f"Failed to parse simulator list: {exc}")


def _find_booted_udid() -> str | None:
    """Return the UDID of a currently-booted simulator, or None."""
    try:
        data = _list_simulators()
    except click.ClickException:
        return None
    for _runtime, devices in data.get("devices", {}).items():
        for dev in devices:
            if dev.get("state") == "Booted":
                return dev.get("udid")
    return None


def _find_any_iphone_udid() -> str | None:
    """Return the UDID of any available iPhone simulator, booted or not.

    Prefers booted simulators so that the user's current session is used.
    Falls back to the first available (shutdown) iPhone sim when none is booted.
    The TestSession boot/shutdown dance handles state preservation regardless.
    """
    try:
        data = _list_simulators()
    except click.ClickException:
        return None

    booted_udid: str | None = None
    first_udid: str | None = None

    for _runtime, devices in sorted(data.get("devices", {}).items(), reverse=True):
        for dev in devices:
            if "iphone" not in dev.get("name", "").lower():
                continue
            if not dev.get("isAvailable", True):
                continue
            if dev.get("state") == "Booted" and booted_udid is None:
                booted_udid = dev.get("udid")
            if first_udid is None:
                first_udid = dev.get("udid")

    return booted_udid or first_udid


def _find_simulator_by_name(name_fragment: str) -> dict[str, Any] | None:
    """Find the first simulator whose name contains *name_fragment* (case-insensitive)."""
    try:
        data = _list_simulators()
    except click.ClickException:
        return None
    needle = name_fragment.lower()
    for _runtime, devices in data.get("devices", {}).items():
        for dev in devices:
            if needle in dev.get("name", "").lower():
                return dev
    return None


def _boot_simulator(device_id: str) -> None:
    """Boot a simulator by UDID, ignoring 'already booted' errors."""
    result = subprocess.run(
        ["xcrun", "simctl", "boot", device_id],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0 and "Unable to boot device in current state: Booted" not in result.stderr:
        raise click.ClickException(f"Failed to boot simulator {device_id}:\n{result.stderr}")


def _install_app(device_id: str, app_path: str) -> None:
    """Install a .app bundle on a simulator."""
    result = subprocess.run(
        ["xcrun", "simctl", "install", device_id, app_path],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise click.ClickException(f"Failed to install {app_path} on {device_id}:\n{result.stderr}")


# ---------------------------------------------------------------------------
# Command group
# ---------------------------------------------------------------------------


@click.group(name="ios", help="iOS Simulator testing commands.")
def ios_command_group() -> None:
    """SpecterQA iOS Simulator commands.

    Run AI-driven tests against iOS apps running in the Xcode simulator.
    Requires macOS with Xcode 15+ and ANTHROPIC_API_KEY.
    """


# ---------------------------------------------------------------------------
# specterqa ios setup
# ---------------------------------------------------------------------------


@ios_command_group.command("setup")
def setup() -> None:
    """Verify Xcode, simulator availability, and API key.

    Checks:
    - xcrun / simctl availability
    - At least one available iOS simulator
    - ANTHROPIC_API_KEY is set in environment
    """
    checks: list[tuple[str, bool, str]] = []

    # 1. xcrun
    xcrun_ok = _xcrun_available()
    xcode_version = ""
    if xcrun_ok:
        r = subprocess.run(["xcrun", "--version"], capture_output=True, text=True)
        xcode_version = r.stdout.strip()
    checks.append(("Xcode / xcrun", xcrun_ok, xcode_version or "not found"))

    # 2. simctl
    simctl_ok = False
    sim_count = 0
    sim_detail = ""
    if xcrun_ok:
        try:
            data = _list_simulators()
            all_devices = [dev for devices in data.get("devices", {}).values() for dev in devices]
            sim_count = len(all_devices)
            booted = [d for d in all_devices if d.get("state") == "Booted"]
            simctl_ok = sim_count > 0
            if booted:
                sim_detail = f"{sim_count} simulators, {len(booted)} booted"
            else:
                sim_detail = f"{sim_count} simulators available"
        except click.ClickException as exc:
            sim_detail = str(exc)
    checks.append(("iOS Simulators", simctl_ok, sim_detail))

    # 3. ANTHROPIC_API_KEY
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    api_ok = bool(api_key)
    api_detail = "set" if api_ok else "NOT SET — export ANTHROPIC_API_KEY=sk-ant-..."
    checks.append(("ANTHROPIC_API_KEY", api_ok, api_detail))

    # 4. specterqa-ios package importability
    pkg_ok = False
    try:
        from specterqa.ios.sim_driver import SimDriver  # noqa: F401

        pkg_ok = True
        pkg_detail = "specterqa.ios importable"
    except ImportError as exc:
        pkg_detail = f"import error: {exc}"
    checks.append(("specterqa-ios package", pkg_ok, pkg_detail))

    # Render table
    table = Table(title="SpecterQA iOS Environment Check", border_style="cyan")
    table.add_column("Check", style="bold")
    table.add_column("Status")
    table.add_column("Detail", style="dim")

    all_ok = True
    for label, ok, detail in checks:
        if ok:
            status = Text("PASS", style="bold green")
        else:
            status = Text("FAIL", style="bold red")
            all_ok = False
        table.add_row(label, status, detail)

    console.print()
    console.print(table)
    console.print()

    if all_ok:
        console.print(
            Panel(
                "[bold green]Environment ready.[/bold green]\n\n"
                "Run [bold]specterqa-ios devices[/bold] to see simulators,\n"
                "or [bold]specterqa-ios run --product <slug> --journey <id>[/bold] to start testing.",
                title="[green]All Checks Passed[/green]",
                border_style="green",
            )
        )
    else:
        console.print(
            Panel(
                "[bold red]One or more checks failed.[/bold red]\n\n"
                "Fix the issues above, then re-run [bold]specterqa-ios setup[/bold].",
                title="[red]Setup Incomplete[/red]",
                border_style="red",
            )
        )
        raise SystemExit(1)


# ---------------------------------------------------------------------------
# specterqa ios doctor
# ---------------------------------------------------------------------------


@ios_command_group.command("doctor")
def doctor() -> None:
    """Diagnose the SpecterQA iOS environment.

    Checks Python version, Xcode installation, simulator state, runner build,
    license key, BrowserStack credentials, and installed package version.

    \b
    Example:
      specterqa-ios doctor
    """
    import shutil
    import subprocess as _sp

    click.echo("SpecterQA iOS Doctor")
    click.echo("=" * 40)

    # Python version
    py_ver = sys.version.split()[0]
    py_ok = sys.version_info >= (3, 10)
    click.echo(f"  {'[OK]' if py_ok else '[!!]'} Python {py_ver}{'  (need 3.10+)' if not py_ok else ''}")

    # specterqa-ios version
    try:
        from specterqa import __version__ as _sqver

        click.echo(f"  [OK] specterqa-ios {_sqver}")
    except Exception:
        click.echo("  [??] specterqa-ios version unknown")

    # Xcode / xcodebuild
    xcode_bin = shutil.which("xcodebuild")
    if xcode_bin:
        try:
            _xr = _sp.run(
                ["xcodebuild", "-version"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            xcode_ver = _xr.stdout.strip().split("\n")[0]
            click.echo(f"  [OK] {xcode_ver}")
        except Exception:
            click.echo("  [??] xcodebuild found but version check failed")
    else:
        click.echo("  [!!] Xcode not installed — install from the Mac App Store")

    # Simulator (booted?)
    try:
        _sr = _sp.run(
            ["xcrun", "simctl", "list", "devices", "booted"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        booted = "Booted" in _sr.stdout
        click.echo(
            f"  {'[OK]' if booted else '[--]'} Simulator "
            f"{'booted' if booted else 'not booted — run: specterqa-ios boot'}"
        )
    except Exception:
        click.echo("  [!!] simctl not available — install Xcode")

    # XCTest runner built?
    runner_dir = Path.home() / ".specterqa" / "runner-build"
    runner_built = (runner_dir / "Build" / "Products").exists()
    click.echo(
        f"  {'[OK]' if runner_built else '[--]'} XCTest runner "
        f"{'built' if runner_built else 'not built — run: specterqa-ios runner build'}"
    )

    # License key
    license_key = os.environ.get("SPECTERQA_IOS_LICENSE", "")
    if license_key == "founder":
        click.echo("  [OK] License: founder mode (unlimited simulators)")
    elif license_key:
        click.echo(f"  [OK] License: {license_key[:8]}...")
    else:
        click.echo("  [--] License: trial mode (1 simulator) — set SPECTERQA_IOS_LICENSE")

    # BrowserStack
    bs_user = os.environ.get("BROWSERSTACK_USERNAME", "")
    bs_key = os.environ.get("BROWSERSTACK_ACCESS_KEY", "")
    if bs_user and bs_key:
        click.echo(f"  [OK] BrowserStack: {bs_user} (real-device testing enabled)")
    else:
        click.echo("  [  ] BrowserStack: not configured (local simulator only)")

    # ANTHROPIC_API_KEY (needed for AI-driven run, not for replay)
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if api_key:
        click.echo(f"  [OK] ANTHROPIC_API_KEY: set ({api_key[:8]}...)")
    else:
        click.echo("  [--] ANTHROPIC_API_KEY: not set (needed for AI-driven runs)")

    click.echo("")
    click.echo("Quick start:")
    click.echo("  1. specterqa-ios runner build --project App.xcodeproj --scheme App")
    click.echo('  2. Add to .claude/mcp.json: {"specterqa-ios": {"command": "specterqa-ios-mcp"}}')
    click.echo("  3. Ask Claude Code: 'Test my iOS app and save a replay'")
    click.echo("  4. specterqa-ios ci .specterqa/replays/ --json-output results.json")


# ---------------------------------------------------------------------------
# specterqa ios devices
# ---------------------------------------------------------------------------


@ios_command_group.command("devices")
@click.option("--available-only", is_flag=True, default=False, help="Show only available (non-unavailable) devices.")
@click.option("--json-output", "json_output", is_flag=True, default=False, help="Output raw JSON.")
def devices(available_only: bool, json_output: bool) -> None:
    """List iOS simulators from xcrun simctl.

    Wraps ``xcrun simctl list devices --json`` and renders a table.
    """
    if not _xcrun_available():
        raise click.ClickException("xcrun not found. Install Xcode from the Mac App Store.")

    data = _list_simulators()

    if json_output:
        out.print(json.dumps(data, indent=2))
        return

    table = Table(title="Available iOS Simulators", border_style="cyan")
    table.add_column("Name", style="bold")
    table.add_column("UDID", style="dim")
    table.add_column("State")
    table.add_column("Runtime", style="dim")

    rows_shown = 0
    for runtime_id, device_list in sorted(data.get("devices", {}).items()):
        # Shorten runtime key: com.apple.CoreSimulator.SimRuntime.iOS-17-2 → iOS 17.2
        runtime_label = runtime_id.replace("com.apple.CoreSimulator.SimRuntime.", "").replace("-", " ")
        for dev in device_list:
            if available_only and dev.get("availability", "").lower().startswith("(unavailable"):
                continue
            state = dev.get("state", "?")
            if state == "Booted":
                state_text = Text(state, style="bold green")
            elif state == "Shutdown":
                state_text = Text(state, style="dim")
            else:
                state_text = Text(state, style="yellow")
            table.add_row(
                dev.get("name", "?"),
                dev.get("udid", "?"),
                state_text,
                runtime_label,
            )
            rows_shown += 1

    if rows_shown == 0:
        console.print("[yellow]No simulators found.[/yellow]")
        return

    console.print()
    console.print(table)
    console.print()
    console.print(f"[dim]{rows_shown} simulator(s). Use [bold]--available-only[/bold] to filter.[/dim]")


# ---------------------------------------------------------------------------
# specterqa ios boot
# ---------------------------------------------------------------------------


@ios_command_group.command("boot")
@click.option("--device", "device", default=None, help="Simulator name fragment or UDID to boot.")
def boot(device: str | None) -> None:
    """Boot an iOS simulator.

    If --device is not specified, boots the first available iPhone simulator.
    Accepts a name fragment (e.g. 'iPhone 15') or a full UDID.
    """
    if not _xcrun_available():
        raise click.ClickException("xcrun not found. Install Xcode.")

    if device is None:
        # Try to find a sensible default: latest iPhone
        data = _list_simulators()
        target: dict[str, Any] | None = None
        for _rt, devs in sorted(data.get("devices", {}).items(), reverse=True):
            for d in devs:
                if "iphone" in d.get("name", "").lower() and d.get("state") != "Booted":
                    target = d
                    break
            if target:
                break
        if target is None:
            raise click.ClickException("No unbooted iPhone simulator found. Pass --device <name or UDID>.")
        device_id = target["udid"]
        device_name = target["name"]
    elif len(device) == 36 and device.count("-") == 4:
        # Looks like a UDID
        device_id = device
        device_name = device
    else:
        found = _find_simulator_by_name(device)
        if not found:
            raise click.ClickException(f"No simulator matching '{device}' found.")
        device_id = found["udid"]
        device_name = found["name"]

    console.print(f"[bold]Booting simulator:[/bold] {device_name} ({device_id})")
    _boot_simulator(device_id)
    console.print(f"[green]Booted:[/green] {device_name}")


# ---------------------------------------------------------------------------
# specterqa ios install
# ---------------------------------------------------------------------------


@ios_command_group.command("install")
@click.argument("app_path")
@click.option("--device", "device_id", default=None, help="Simulator UDID. Defaults to currently booted simulator.")
def install(app_path: str, device_id: str | None) -> None:
    """Install a .app bundle on a simulator.

    APP_PATH should be the path to the .app directory produced by a debug build
    (e.g. DerivedData/.../Debug-iphonesimulator/MyApp.app).
    """
    if not _xcrun_available():
        raise click.ClickException("xcrun not found. Install Xcode.")

    resolved_path = Path(app_path).resolve()
    if not resolved_path.exists():
        raise click.ClickException(f"App not found: {app_path}")

    if device_id is None:
        device_id = _find_booted_udid()
        if device_id is None:
            raise click.ClickException(
                "No booted simulator found. Boot one first with [bold]specterqa-ios boot[/bold], "
                "or pass --device <UDID>."
            )

    console.print(f"[bold]Installing:[/bold] {resolved_path.name} → {device_id}")
    _install_app(device_id, str(resolved_path))
    console.print(f"[green]Installed:[/green] {resolved_path.name}")


# ---------------------------------------------------------------------------
# specterqa ios run
# ---------------------------------------------------------------------------


def _xctest_runner_available() -> bool:
    """Return True when the compiled .xctestrun file exists in the runner build dir.

    Does NOT check whether the runner process is currently running — only whether
    the build artifact exists so it can be deployed.

    Returns:
        True if at least one .xctestrun is found in ``~/.specterqa/runner-build/``.
    """
    import glob as _glob

    build_dir = Path.home() / ".specterqa" / "runner-build"
    pattern = str(build_dir / "Build" / "Products" / "*.xctestrun")
    return bool(_glob.glob(pattern))


def _runner_build_dir() -> Path:
    """Return the canonical path to the runner build output directory."""
    env_override = os.environ.get("SPECTERQA_DERIVED_DATA")
    if env_override:
        return Path(env_override)
    return Path.home() / ".specterqa" / "runner-build"


def _runner_source_dir() -> Path | None:
    """Locate the Swift runner source directory relative to the installed package.

    Searches for ``runner/build.sh`` relative to the specterqa-ios package
    root.  Returns None if it cannot be determined.

    Returns:
        Path to the runner directory, or None.
    """
    try:
        import specterqa.ios as _pkg

        pkg_root = Path(_pkg.__file__).parent.parent.parent  # src/specterqa/ios → repo root
        candidate = pkg_root / "runner"
        if (candidate / "build.sh").exists():
            return candidate
    except Exception:
        pass
    return None


def _get_driver(udid: str = "booted", verbose: bool = False) -> tuple[Any, str]:
    """Return the best available touch driver and its backend name.

    Selection priority:
        1. WDADriver  — Appium WebDriverAgent on port 8100 (headless, device pts)
        2. SimDriver  — Quartz CGEvents fallback (requires visible Simulator window)

    The WDA path is preferred because it injects native touch events directly
    into the simulator process — no window detection, no title-bar offset math,
    works headless in CI.  SimDriver is kept as a reliable fallback for local
    developer workflows where Simulator.app is already open.

    Args:
        udid: Simulator UDID or ``"booted"``.
        verbose: Enable debug output on the returned driver.

    Returns:
        ``(driver, backend_name)`` where *backend_name* is one of
        ``"wda"`` or ``"cgevents"``.
    """
    try:
        from specterqa.ios.wda_driver import WDADriver

        if WDADriver.is_available():
            if verbose:
                print("[specterqa] Using WDA backend (headless, device points)")
            return WDADriver(udid=udid, verbose=verbose), "wda"
    except ImportError:
        pass

    try:
        from specterqa.ios.sim_driver import SimDriver

        if verbose:
            print("[specterqa] Using CGEvent backend (requires visible Simulator)")
        return SimDriver(udid=udid, verbose=verbose), "cgevents"
    except ImportError as exc:
        raise click.ClickException(f"No driver available. WDA is not running and SimDriver import failed: {exc}")


def _decision_to_action(decision: Any) -> dict:
    """Convert a ComputerUseDecider Decision to sim_driver action dict."""
    if decision.action == "click":
        parts = decision.target.split(",")
        x, y = int(float(parts[0])), int(float(parts[1]))
        return {"action": "left_click", "coordinate": [x, y]}
    elif decision.action == "fill":
        return {"action": "type", "text": decision.value}
    elif decision.action == "keyboard":
        return {"action": "key", "key": decision.value}
    elif decision.action == "scroll":
        parts = (decision.target or "512,1108").split(",")
        x, y = int(float(parts[0])), int(float(parts[1]))
        return {
            "action": "scroll",
            "coordinate": [x, y],
            "direction": decision.value or "down",
            "amount": 3,
        }
    elif decision.action == "wait":
        return {"action": "wait", "duration": 1}
    else:
        return {"action": decision.action}


@ios_command_group.command("run")
@click.option("--product", "-p", required=True, help="Product slug (matches .specterqa/products/<slug>.yaml).")
@click.option("--journey", "-j", required=True, help="Journey ID (matches .specterqa/journeys/<id>.yaml).")
@click.option("--device", "device_id", default=None, help="Simulator UDID. Defaults to booted simulator.")
@click.option("--app", "app_path", default=None, help="Path to .app bundle to install before running.")
@click.option("--budget", "-b", default=5.00, type=float, show_default=True, help="Max spend in USD for this run.")
@click.option("--max-steps", default=20, type=int, show_default=True, help="Max AI iterations per journey step.")
@click.option("--verbose", "-v", is_flag=True, default=False, help="Enable verbose logging.")
@click.option("--plain", is_flag=True, default=False, help="Plain ASCII output (no Rich).")
def run(
    product: str,
    journey: str,
    device_id: str | None,
    app_path: str | None,
    budget: float,
    max_steps: int,
    verbose: bool,
    plain: bool,
) -> None:
    """Run a test journey against an iOS app in the simulator.

    \b
    Example:
      specterqa ios run --product example-ios --journey smoke-test
      specterqa ios run --product example-ios --journey smoke-test --device <UDID>
      specterqa ios run --product example-ios --journey smoke-test --app ./build/Example Reader.app
    """
    if verbose:
        logging.basicConfig(level=logging.DEBUG, format="%(name)s  %(message)s")

    # Auto-enable plain in non-TTY contexts (e.g. CI)
    if not sys.stdout.isatty() and not plain:
        plain = True

    def _print(msg: str) -> None:
        if plain:
            print(msg, file=sys.stderr, flush=True)
        else:
            console.print(msg)

    def _err(msg: str, title: str = "Error") -> None:
        if plain:
            print(f"[{title}] {msg}", file=sys.stderr, flush=True)
        else:
            console.print(Panel(f"[red]{msg}[/red]", title=f"[red]{title}[/red]", border_style="red"))

    if not _xcrun_available():
        _err("xcrun not found. Install Xcode from the Mac App Store.", "Environment Error")
        raise SystemExit(2)

    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        _err(
            "ANTHROPIC_API_KEY is not set.\n\n"
            "Export it first:  export ANTHROPIC_API_KEY=sk-ant-...\n"
            "Get a key at: https://console.anthropic.com/",
            "API Key Missing",
        )
        raise SystemExit(2)

    # Resolve project directory and load configs
    project_dir = _resolve_project_dir()

    try:
        product_cfg = _load_product(project_dir, product)
    except click.ClickException as exc:
        _err(str(exc), "Product Config Error")
        raise SystemExit(2)

    try:
        journey_cfg = _load_journey(project_dir, journey)
    except click.ClickException as exc:
        _err(str(exc), "Journey Config Error")
        raise SystemExit(2)

    # Resolve bundle_id from product config
    bundle_id: str = product_cfg.get("bundle_id", product_cfg.get("name", product))

    # Resolve / discover device.
    # TestSession handles the boot/shutdown dance for cloning, so we no longer
    # need the source simulator to be booted before passing its UDID in.
    if device_id is None:
        device_id = _find_any_iphone_udid()
        if device_id is None:
            _err("No iPhone simulator found. Install Xcode and run 'specterqa-ios setup'.", "Simulator Error")
            raise SystemExit(2)
        # Log whether we found a booted or shutdown sim so the user has context.
        booted_udid = _find_booted_udid()
        if booted_udid and booted_udid == device_id:
            _print(f"[dim]Using booted simulator: {device_id}[/dim]")
        else:
            _print(f"[dim]Using available simulator: {device_id} (TestSession will manage boot state)[/dim]")

    # Validate app path if provided — don't install here, let TestSession
    # install on the clone so the cloned sim has the app.
    resolved_app_path: str | None = None
    if app_path:
        resolved_app = Path(app_path).resolve()
        if not resolved_app.exists():
            _err(f"App not found: {app_path}", "Install Error")
            raise SystemExit(2)
        resolved_app_path = str(resolved_app)

    # Print run header
    run_id = f"IOS-RUN-{time.strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex[:8].upper()}"
    evidence_dir = project_dir / "evidence" / run_id
    evidence_dir.mkdir(parents=True, exist_ok=True)

    if plain:
        print(
            f"SpecterQA iOS run: product={product} journey={journey} "
            f"device={device_id} budget=${budget:.2f} run_id={run_id}",
            file=sys.stderr,
            flush=True,
        )
    else:
        console.print()
        console.print(
            Panel(
                "\n".join(
                    [
                        f"[bold]Product:[/bold]    {product}",
                        f"[bold]Journey:[/bold]    {journey}",
                        f"[bold]Device ID:[/bold]  {device_id}",
                        f"[bold]Bundle ID:[/bold]  {bundle_id}",
                        f"[bold]Budget:[/bold]     ${budget:.2f}",
                        f"[bold]Run ID:[/bold]     {run_id}",
                        f"[bold]Evidence:[/bold]   {evidence_dir}",
                    ]
                ),
                title="[bold cyan]SpecterQA iOS Run[/bold cyan]",
                border_style="cyan",
            )
        )
        console.print()

    # Decide which backend to use.
    # Priority:
    #   1. XCTest runner  — runner .xctestrun built → SoM pipeline (headless, no cursor)
    #   2. WDA            — WDA running on port 8100 → SoM pipeline (headless, device pts)
    #   3. CGEvents       — fallback (blocks cursor, requires visible Simulator.app window)
    use_xctest = _xctest_runner_available()

    wda_url: str | None = None
    if not use_xctest:
        # Check whether WDA is reachable before committing to CGEvents.
        try:
            from specterqa.ios.wda_driver import WDADriver  # type: ignore[import-untyped]

            if WDADriver.is_available():
                wda_url = "http://localhost:8100"
        except ImportError:
            pass

    if use_xctest:
        if not plain:
            console.print("  [dim]Backend: SoM (XCTest runner + Set-of-Mark — headless)[/dim]")
    elif wda_url:
        _print(
            "[yellow]Warning:[/yellow] XCTest runner not built — using WDA fallback.\n"
            "  Run: specterqa-ios runner build --project <path> --scheme <scheme>\n"
            "  WDA provides headless operation but the runner gives better results."
        )
        if not plain:
            console.print("  [dim]Backend: SoM (WDA fallback — headless)[/dim]")
    else:
        # Neither runner nor WDA — fall back to CGEvents with a clear warning.
        _print(
            "[yellow]Warning:[/yellow] XCTest runner not built and WDA not available.\n"
            "  Run: specterqa-ios runner build --project <path> --scheme <scheme>\n"
            "  Falling back to CGEvents (will steal mouse cursor during test)."
        )
        if not plain:
            console.print("  [dim]Backend: CGEvents (cursor-blocking fallback)[/dim]")

    # Build evidence directory for this run
    runner = None
    from specterqa.ios.som_runner import SoMRunner

    runner = SoMRunner(
        api_key=api_key,
        verbose=verbose,
        evidence_dir=str(evidence_dir),
        use_xctest_runner=use_xctest,
        wda_url=wda_url,
        headless=True,
        app_path=resolved_app_path,
    )

    _print(f"Launching {bundle_id}...")
    try:
        runner.start(bundle_id)
    except Exception as exc:
        _err(f"Failed to start SoM runner: {exc}", "Startup Error")
        raise SystemExit(3)

    # Execute journey steps
    steps = journey_cfg.get("steps", journey_cfg.get("scenario", {}).get("steps", []))
    if not steps:
        _print("[yellow]Warning: journey has no steps defined.[/yellow]")

    all_passed = True
    step_results = []
    start_time = time.monotonic()

    try:
        for i, step in enumerate(steps, 1):
            step_id = step.get("id", f"step-{i}")
            description = step.get("description", step.get("goal", step_id))
            goal = step.get("goal", description)
            checkpoint = step.get("checkpoint", None)
            step_max_iter = step.get("max_iterations", max_steps)

            if plain:
                print(f"Step {i}/{len(steps)}: {description}", file=sys.stderr, flush=True)
            else:
                console.print(f"  [bold]Step {i}/{len(steps)}:[/bold] {description}")

            result = runner.run_step(goal=goal, checkpoint=checkpoint, max_iterations=step_max_iter)
            step_passed = result["passed"]
            step_error = result.get("error")

            if step_passed:
                if plain:
                    print(f"  PASS ({result['duration']:.1f}s)", file=sys.stderr, flush=True)
                else:
                    console.print(f"    [green]PASS[/green] ({result['duration']:.1f}s)")
            else:
                if plain:
                    print(f"  FAIL — {step_error}", file=sys.stderr, flush=True)
                else:
                    console.print(f"    [red]FAIL[/red] — {step_error}")

            if not step_passed:
                all_passed = False

            step_results.append(
                {
                    "step_id": step_id,
                    "description": description,
                    "passed": step_passed,
                    "duration_seconds": result["duration"],
                    "error": step_error,
                    "findings": [],
                }
            )
    finally:
        runner.stop()

    total_duration = round(time.monotonic() - start_time, 3)

    # Save run result JSON
    run_result = {
        "run_id": run_id,
        "product": product,
        "journey": journey,
        "device_id": device_id,
        "bundle_id": bundle_id,
        "passed": all_passed,
        "step_count": len(steps),
        "step_reports": step_results,
        "findings": [],
        "duration_seconds": total_duration,
    }
    result_path = evidence_dir / "run-result.json"
    try:
        result_path.write_text(json.dumps(run_result, indent=2, default=str))
    except Exception as exc:
        logger.warning("Failed to write run-result.json: %s", exc)

    # Print summary
    passed_count = sum(1 for sr in step_results if sr.get("passed"))
    if plain:
        verdict = "PASSED" if all_passed else "FAILED"
        print(
            f"RESULT: {verdict} -- {passed_count}/{len(steps)} steps passed, {total_duration:.1f}s",
            file=sys.stderr,
            flush=True,
        )
        print(f"Run ID: {run_id}", file=sys.stderr, flush=True)
        print(f"Evidence: {evidence_dir}", file=sys.stderr, flush=True)
    else:
        console.print()
        border = "green" if all_passed else "red"
        verdict = "[bold green]ALL STEPS PASSED[/bold green]" if all_passed else "[bold red]STEPS FAILED[/bold red]"
        console.print(
            Panel(
                "\n".join(
                    [
                        verdict,
                        "",
                        f"  Steps:     {passed_count}/{len(steps)} passed",
                        f"  Duration:  {total_duration:.1f}s",
                        f"  Run ID:    {run_id}",
                        f"  Evidence:  {evidence_dir}",
                    ]
                ),
                border_style=border,
            )
        )
        console.print()

    raise SystemExit(0 if all_passed else 1)


# ---------------------------------------------------------------------------
# specterqa ios init
# ---------------------------------------------------------------------------


@ios_command_group.command("init")
@click.option(
    "--slug", "app_slug", default="my-ios-app", show_default=True, help="Short app identifier for file names."
)
@click.option("--name", "display_name", default="My iOS App", show_default=True, help="Human-readable app name.")
@click.option("--dir", "target_dir", default=".", show_default=True, help="Directory to create .specterqa/ in.")
@click.option("--force", is_flag=True, default=False, help="Overwrite existing files.")
def ios_init(app_slug: str, display_name: str, target_dir: str, force: bool) -> None:
    """Scaffold a .specterqa/ project directory for iOS testing.

    Creates template product, persona, and journey YAML files pre-configured
    for iOS Simulator testing.

    \b
    Example:
      specterqa ios init --slug example-ios --name "Example Reader"
    """
    from specterqa.ios.cli.setup import scaffold_ios_project

    resolved = Path(target_dir).resolve()
    # Avoid double-nesting: if the user points --dir at an existing .specterqa/
    # directory (or a path that already ends in .specterqa) use it directly;
    # otherwise append .specterqa to the target directory.
    if resolved.name == ".specterqa":
        project_dir = resolved
    else:
        project_dir = resolved / ".specterqa"
    scaffold_ios_project(project_dir=project_dir, app_slug=app_slug, display_name=display_name, force=force)


# ---------------------------------------------------------------------------
# specterqa ios smoke
# ---------------------------------------------------------------------------


@ios_command_group.command("smoke")
@click.option("--product", "-p", required=True, help="Product slug.")
@click.option("--device", "device_id", default=None, help="Simulator UDID.")
@click.option("--budget", "-b", default=1.00, type=float, show_default=True, help="Max spend in USD.")
@click.pass_context
def smoke(ctx: click.Context, product: str, device_id: str | None, budget: float) -> None:
    """Run a quick smoke test for a product.

    Runs the journey named 'smoke-test' (or the first journey tagged 'smoke'
    in the product config).  Uses a reduced budget cap of $1.00 by default.

    \b
    Example:
      specterqa ios smoke --product example-ios
    """
    # Determine which journey to run
    project_dir = _resolve_project_dir()
    smoke_journey = "smoke-test"

    # Try to find a smoke-tagged journey in the product config
    try:
        product_cfg = _load_product(project_dir, product)
        journeys_hint = product_cfg.get("journeys", [])
        if journeys_hint:
            smoke_journey = (
                journeys_hint[0] if isinstance(journeys_hint[0], str) else journeys_hint[0].get("id", smoke_journey)
            )
    except click.ClickException:
        pass

    # Delegate to the run command
    ctx.invoke(
        run,
        product=product,
        journey=smoke_journey,
        device_id=device_id,
        app_path=None,
        budget=budget,
        max_steps=10,
        verbose=False,
        plain=not sys.stdout.isatty(),
    )


# ---------------------------------------------------------------------------
# specterqa ios serve
# ---------------------------------------------------------------------------


@ios_command_group.command("validate")
@click.option("--product", "-p", required=True, help="Product slug (matches .specterqa/products/<slug>.yaml).")
@click.option("--journey", "-j", default=None, help="Journey ID to validate (optional).")
def validate(product: str, journey: str | None) -> None:
    """Validate product and journey config files for the iOS driver.

    Checks required fields, referenced files, and simulator/app availability.

    \b
    Example:
      specterqa-ios validate --product example-ios
      specterqa-ios validate --product example-ios --journey smoke-test
    """
    errors: list[str] = []
    warnings: list[str] = []
    checks: list[tuple[str, bool, str]] = []

    project_dir = _resolve_project_dir()

    # --- Product YAML ---
    product_path = project_dir / "products" / f"{product}.yaml"
    if not product_path.exists():
        errors.append(f"Product file not found: {product_path}")
        checks.append(("Product file exists", False, str(product_path)))
    else:
        checks.append(("Product file exists", True, str(product_path)))
        try:
            product_cfg = _load_product(project_dir, product)

            # Required fields
            has_bundle_id = bool(product_cfg.get("bundle_id"))
            checks.append(("bundle_id present", has_bundle_id, product_cfg.get("bundle_id", "MISSING")))
            if not has_bundle_id:
                errors.append("Product config missing required field: bundle_id")

            has_device = bool(product_cfg.get("device_name") or product_cfg.get("simulator_id"))
            device_val = product_cfg.get("simulator_id") or product_cfg.get("device_name") or "MISSING"
            checks.append(("device_name or simulator_id present", has_device, device_val))
            if not has_device:
                warnings.append("Product config has no device_name or simulator_id — will use booted simulator")

            # Validate simulator UDID exists in simctl if simulator_id is set
            sim_id = product_cfg.get("simulator_id")
            if sim_id and _xcrun_available():
                try:
                    data = _list_simulators()
                    all_udids = {dev.get("udid") for devs in data.get("devices", {}).values() for dev in devs}
                    udid_found = sim_id in all_udids
                    checks.append(("simulator_id found in simctl", udid_found, sim_id))
                    if not udid_found:
                        errors.append(f"simulator_id '{sim_id}' not found in 'xcrun simctl list devices'")

                    # Check bundle_id installed on simulator if it's booted
                    bundle_id = product_cfg.get("bundle_id")
                    if bundle_id and udid_found:
                        result = subprocess.run(
                            ["xcrun", "simctl", "listapps", sim_id],
                            capture_output=True,
                            text=True,
                        )
                        if result.returncode == 0:
                            app_installed = bundle_id in result.stdout
                            checks.append(("bundle_id installed on simulator", app_installed, bundle_id))
                            if not app_installed:
                                warnings.append(
                                    f"bundle_id '{bundle_id}' not found on simulator {sim_id} — "
                                    "install with: specterqa-ios install <app.app>"
                                )
                        else:
                            checks.append(
                                (
                                    "bundle_id installed on simulator",
                                    False,
                                    "simctl listapps failed — is simulator booted?",
                                )
                            )
                except click.ClickException as exc:
                    checks.append(("simctl query", False, str(exc)))

        except click.ClickException as exc:
            errors.append(f"Failed to load product config: {exc}")

    # --- Journey YAML (if specified) ---
    if journey:
        journey_path = project_dir / "journeys" / f"{journey}.yaml"
        if not journey_path.exists():
            errors.append(f"Journey file not found: {journey_path}")
            checks.append(("Journey file exists", False, str(journey_path)))
        else:
            checks.append(("Journey file exists", True, str(journey_path)))
            try:
                journey_cfg = _load_journey(project_dir, journey)

                steps = journey_cfg.get("steps", [])
                has_steps = len(steps) > 0
                checks.append(("Journey has steps", has_steps, f"{len(steps)} step(s)"))
                if not has_steps:
                    errors.append("Journey has no steps defined")

                # Every step needs a goal
                steps_missing_goal = [s.get("id", f"step-{i + 1}") for i, s in enumerate(steps) if not s.get("goal")]
                if steps_missing_goal:
                    errors.append(f"Steps missing 'goal' field: {', '.join(steps_missing_goal)}")
                    checks.append(("All steps have goal", False, f"missing: {', '.join(steps_missing_goal)}"))
                else:
                    checks.append(("All steps have goal", True, f"{len(steps)} step(s) OK"))

                # Check referenced personas exist
                personas_list = journey_cfg.get("personas", [])
                for persona_ref in personas_list:
                    ref = persona_ref.get("ref", "") if isinstance(persona_ref, dict) else str(persona_ref)
                    if ref:
                        persona_path = project_dir / "personas" / f"{ref}.yaml"
                        persona_exists = persona_path.exists()
                        checks.append((f"Persona '{ref}' exists", persona_exists, str(persona_path)))
                        if not persona_exists:
                            warnings.append(f"Referenced persona '{ref}' not found at {persona_path}")

            except click.ClickException as exc:
                errors.append(f"Failed to load journey config: {exc}")

    # --- Render results ---
    table = Table(title=f"SpecterQA iOS Config Validation — {product}", border_style="cyan")
    table.add_column("Check", style="bold")
    table.add_column("Status")
    table.add_column("Detail", style="dim")

    for label, ok, detail in checks:
        status = Text("PASS", style="bold green") if ok else Text("FAIL", style="bold red")
        table.add_row(label, status, detail)

    console.print()
    console.print(table)

    if warnings:
        console.print()
        for w in warnings:
            console.print(f"[yellow]WARN:[/yellow] {w}")

    if errors:
        console.print()
        for e in errors:
            console.print(f"[red]ERROR:[/red] {e}")
        console.print()
        console.print(
            Panel(
                f"[bold red]{len(errors)} error(s) found.[/bold red] Fix the issues above before running.",
                border_style="red",
            )
        )
        raise SystemExit(1)
    else:
        console.print()
        console.print(
            Panel(
                "[bold green]Config is valid.[/bold green]",
                border_style="green",
            )
        )


# ---------------------------------------------------------------------------
# specterqa ios runner  (build / status / clean)
# ---------------------------------------------------------------------------


@ios_command_group.group("runner")
def runner_group() -> None:
    """Manage the SpecterQA Swift XCTest runner.

    The runner is a compiled Swift XCUITest bundle that runs inside the iOS
    Simulator.  It provides pixel-perfect tap injection and accessibility tree
    access without stealing the mouse cursor.

    \b
    One-time setup:
      specterqa-ios runner build    # ~30s, requires Xcode
      specterqa-ios runner status   # verify build

    \b
    After building, ``specterqa-ios run`` uses the runner automatically.
    """


@runner_group.command("build")
@click.option(
    "--project",
    "project_path",
    default=None,
    type=click.Path(exists=True),
    help="Path to user's .xcodeproj for project-injection build (recommended).",
)
@click.option("--scheme", "scheme", default=None, help="Xcode scheme name (required when --project is used).")
@click.option(
    "--runner-dir",
    "runner_dir",
    default=None,
    help="Path to the Swift runner source (default: auto-detected). "
    "Used for standalone build when --project is not provided.",
)
@click.option("--verbose", "-v", is_flag=True, default=False, help="Stream xcodebuild output to stdout.")
def runner_build(
    project_path: str | None,
    scheme: str | None,
    runner_dir: str | None,
    verbose: bool,
) -> None:
    """Compile the Swift XCTest runner against your Xcode project.

    \b
    Project-injection build (recommended — inherits your signing settings):
      specterqa-ios runner build --project MyApp/MyApp.xcodeproj --scheme MyApp
      specterqa-ios runner build --project MyApp.xcodeproj --scheme MyApp --verbose

    \b
    Standalone build (uses SpecterQA's own signing):
      specterqa-ios runner build
      specterqa-ios runner build --verbose

    \b
    The project-injection build reads your project's signing identity, team ID,
    and deployment target so the runner bundle is signed consistently with your
    app.  This avoids simulator trust errors and is the recommended path.
    """
    if not _xcrun_available():
        raise click.ClickException("Xcode is required to build the runner. Install Xcode from the Mac App Store.")

    # --- Project-injection path ---
    if project_path is not None:
        if not scheme:
            raise click.ClickException(
                "--scheme is required when --project is used.\n"
                "Example: specterqa-ios runner build --project MyApp.xcodeproj --scheme MyApp"
            )
        from specterqa.ios.project_injector import ProjectInjector, ProjectInjectorError

        console.print("[bold]Building runner via project injection...[/bold]")
        console.print(f"  Project: {project_path}")
        console.print(f"  Scheme:  {scheme}")
        console.print()
        try:
            injector = ProjectInjector(project_path=project_path, scheme=scheme)
            xctestrun = injector.build(verbose=verbose)
        except ProjectInjectorError as exc:
            raise click.ClickException(str(exc))
        console.print(
            Panel(
                f"[bold green]Runner built successfully.[/bold green]\n\n"
                f"  Artifact: {xctestrun}\n\n"
                "Run [bold]specterqa-ios runner status[/bold] to verify,\n"
                "or [bold]specterqa-ios run --product <slug> --journey <id>[/bold] to start testing.",
                title="[green]Build Complete (project injection)[/green]",
                border_style="green",
            )
        )
        return

    # --- Standalone path (legacy / CI without user project) ---
    if runner_dir:
        source = Path(runner_dir).resolve()
    else:
        source = _runner_source_dir() or Path.cwd()

    build_dir = _runner_build_dir()
    build_dir.mkdir(parents=True, exist_ok=True)

    # Locate the Xcode project inside the runner source directory.
    xcodeproj = source / "SpecterQARunner.xcodeproj"
    if not xcodeproj.exists():
        # Fallback: accept any .xcodeproj in the source tree
        candidates = list(source.glob("*.xcodeproj"))
        xcodeproj = candidates[0] if candidates else source / "SpecterQARunner.xcodeproj"

    console.print("[bold]Building SpecterQA XCTest runner...[/bold]")
    console.print(f"  Source:    {source}")
    console.print(f"  Output:    {build_dir}")
    console.print()

    stdout = None if verbose else subprocess.DEVNULL
    stderr = None if verbose else subprocess.PIPE

    result = subprocess.run(
        [
            "xcodebuild",
            "build-for-testing",
            "-project",
            str(xcodeproj),
            "-scheme",
            "SpecterQARunner",
            "-sdk",
            "iphonesimulator",
            "-derivedDataPath",
            str(build_dir),
        ],
        cwd=str(source),
        stdout=stdout,
        stderr=stderr,
    )

    if result.returncode != 0:
        error_detail = ""
        if not verbose and result.stderr:
            error_detail = f"\n\nBuild output:\n{result.stderr.decode('utf-8', errors='replace')[-2000:]}"
        raise click.ClickException(
            f"Runner build failed (exit {result.returncode}).{error_detail}\n\n"
            "Re-run with --verbose to see full xcodebuild output."
        )

    # Verify the xctestrun was produced (advisory — build already succeeded).
    import glob as _glob

    pattern = str(build_dir / "Build" / "Products" / "*.xctestrun")
    matches = _glob.glob(pattern)
    artifact_line = f"  Artifact: {matches[0]}\n\n" if matches else ""
    if not matches:
        logger.warning("No .xctestrun found at %s — run 'runner status' to verify.", pattern)

    console.print(
        Panel(
            f"[bold green]Runner built successfully.[/bold green]\n\n"
            f"{artifact_line}"
            "Run [bold]specterqa-ios runner status[/bold] to verify,\n"
            "or [bold]specterqa-ios run --product <slug> --journey <id>[/bold] to start testing.",
            title="[green]Build Complete[/green]",
            border_style="green",
        )
    )


@runner_group.command("status")
def runner_status() -> None:
    """Check whether the Swift XCTest runner is compiled and ready.

    Looks for the .xctestrun artifact under ``~/.specterqa/runner-build/``.

    \b
    Example:
      specterqa-ios runner status
    """
    import glob as _glob

    build_dir = _runner_build_dir()
    pattern = str(build_dir / "Build" / "Products" / "*.xctestrun")
    matches = _glob.glob(pattern)

    table = Table(title="SpecterQA XCTest Runner Status", border_style="cyan")
    table.add_column("Item", style="bold")
    table.add_column("Status")
    table.add_column("Detail", style="dim")

    # Build dir exists?
    build_dir_ok = build_dir.exists()
    table.add_row(
        "Build directory",
        Text("OK", style="green") if build_dir_ok else Text("MISSING", style="red"),
        str(build_dir),
    )

    # .xctestrun found?
    if matches:
        xctestrun = Path(matches[0])
        mtime = xctestrun.stat().st_mtime
        import datetime

        built_at = datetime.datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M")
        table.add_row(
            "Runner artifact (.xctestrun)",
            Text("READY", style="bold green"),
            f"{xctestrun.name}  (built {built_at})",
        )
        runner_ready = True
    else:
        table.add_row(
            "Runner artifact (.xctestrun)",
            Text("NOT BUILT", style="bold red"),
            "Run: specterqa-ios runner build",
        )
        runner_ready = False

    console.print()
    console.print(table)
    console.print()

    if runner_ready:
        console.print("[green]Runner is ready.[/green] Tests will run headless without stealing the mouse cursor.")
    else:
        console.print(
            "[yellow]Runner is not built.[/yellow] "
            "Run [bold]specterqa-ios runner build[/bold] for non-blocking headless testing.\n"
            "Without the runner, tests fall back to CGEvents (blocks the cursor)."
        )


@runner_group.command("clean")
@click.option("--yes", "-y", is_flag=True, default=False, help="Skip confirmation prompt.")
def runner_clean(yes: bool) -> None:
    """Remove the compiled runner build artifacts.

    Deletes ``~/.specterqa/runner-build/`` and its contents.  The runner will
    need to be rebuilt with [bold]specterqa-ios runner build[/bold] before
    headless testing resumes.

    \b
    Example:
      specterqa-ios runner clean
      specterqa-ios runner clean --yes
    """
    import shutil

    build_dir = _runner_build_dir()

    if not build_dir.exists():
        console.print("[dim]Runner build directory does not exist — nothing to clean.[/dim]")
        return

    if not yes:
        click.confirm(
            f"Delete runner build artifacts at {build_dir}?",
            abort=True,
        )

    try:
        shutil.rmtree(build_dir)
        console.print(f"[green]Deleted:[/green] {build_dir}")
        console.print("Run [bold]specterqa-ios runner build[/bold] to rebuild the runner.")
    except Exception as exc:
        raise click.ClickException(f"Failed to remove {build_dir}: {exc}")


@ios_command_group.command("serve")
def serve() -> None:
    """Start the SpecterQA iOS MCP server (stdio transport).

    Exposes iOS simulator testing capabilities as MCP tools for Claude Code
    and other AI agent integrations.  Connect via the stdio transport by
    adding this server to your Claude Code MCP configuration.

    \b
    Example ~/.claude/mcp.json entry:
      {
        "mcpServers": {
          "specterqa-ios": {
            "command": "specterqa-ios-mcp",
            "env": {
              "SPECTERQA_IOS_LICENSE": "founder"
            }
          }
        }
      }
    """
    from specterqa.ios.mcp.server import serve as run_mcp

    run_mcp()


# ---------------------------------------------------------------------------
# specterqa ios wda  — manage WebDriverAgent
# ---------------------------------------------------------------------------


@ios_command_group.group("wda")
def wda_group() -> None:
    """Manage WebDriverAgent (WDA) — the headless touch-injection backend.

    WDA is an Appium XCTest bundle that runs inside the simulator and exposes
    a W3C WebDriver-compatible HTTP API on port 8100.  It delivers native touch
    events directly into the simulator process — no window detection, no title-bar
    offsets, works in headless CI environments.

    \b
    Typical workflow:
      specterqa-ios wda status          # check if WDA is running
      specterqa-ios wda start           # build + launch WDA on the booted sim
      specterqa-ios run --product ...   # run now uses WDA automatically
      specterqa-ios wda stop            # stop WDA when done
    """


@wda_group.command("status")
def wda_status() -> None:
    """Check whether WebDriverAgent is running and ready.

    Probes ``GET /status`` on port 8100 and reports the result.
    """
    from specterqa.ios.wda_driver import WDADriver

    available = WDADriver.is_available()
    if available:
        console.print("[bold green]WDA is running[/bold green] — port 8100 is ready.")
        console.print("[dim]The [bold]run[/bold] command will use WDA automatically.[/dim]")
    else:
        console.print("[bold yellow]WDA is not running.[/bold yellow]")
        console.print(
            "Start it with:  [bold]specterqa-ios wda start[/bold]\nOr run it manually via Xcode / xcodebuild."
        )


@wda_group.command("stop")
def wda_stop() -> None:
    """Stop WebDriverAgent.

    Sends ``DELETE /session`` to the active WDA session (if any), then kills
    any ``xcodebuild`` process that is serving WDA.  Safe to call even when
    WDA is not running.
    """
    # Close the active session gracefully
    try:
        from specterqa.ios.wda_driver import WDADriver
        import urllib.request

        req = urllib.request.Request("http://localhost:8100/status", method="GET")
        with urllib.request.urlopen(req, timeout=2) as resp:
            data = json.loads(resp.read())
        session_id = data.get("sessionId") or data.get("value", {}).get("sessionId")
        if session_id:
            try:
                driver = WDADriver()
                driver._session_id = session_id  # type: ignore[attr-defined]
                driver._request("DELETE", f"/session/{session_id}")
            except Exception:
                pass
    except Exception:
        pass  # WDA not running — nothing to close

    # Kill the xcodebuild process running WDA
    result = subprocess.run(
        ["pkill", "-f", "WebDriverAgentRunner"],
        capture_output=True,
    )
    if result.returncode == 0:
        console.print("[green]WDA stopped.[/green]")
    else:
        console.print("[dim]WDA was not running (nothing to stop).[/dim]")


@wda_group.command("start")
@click.option(
    "--device",
    "device_id",
    default=None,
    help="Simulator UDID. Defaults to currently-booted simulator.",
)
@click.option(
    "--wda-path",
    default=None,
    help="Path to a pre-built WebDriverAgent.xcodeproj. Defaults to ~/.specterqa/wda/WebDriverAgent.",
)
@click.option(
    "--port",
    default=8100,
    type=int,
    show_default=True,
    help="Port WDA should listen on.",
)
@click.option(
    "--timeout",
    default=60,
    type=int,
    show_default=True,
    help="Seconds to wait for WDA to become ready.",
)
def wda_start(
    device_id: str | None,
    wda_path: str | None,
    port: int,
    timeout: int,
) -> None:
    """Build and launch WebDriverAgent on the booted simulator.

    Clones the Appium WebDriverAgent repo to ``~/.specterqa/wda/`` if it has
    not already been downloaded, then builds the XCTest bundle with
    ``xcodebuild build-for-testing`` and launches it with
    ``xcodebuild test-without-building``.

    The process runs in the background.  Use ``specterqa-ios wda status`` to
    confirm WDA is ready.

    \b
    Prerequisites:
      - Xcode 15+ with the iOS Simulator SDK installed
      - At least one iOS Simulator booted (run: specterqa-ios boot)
      - git (to clone WebDriverAgent)

    \b
    Example:
      specterqa-ios wda start
      specterqa-ios wda start --device <UDID> --timeout 90
    """
    import glob

    from specterqa.ios.wda_driver import WDADriver

    # Resolve target simulator
    if device_id is None:
        device_id = _find_booted_udid()
        if device_id is None:
            raise click.ClickException("No booted simulator found. Boot one first with: specterqa-ios boot")
    console.print(f"[bold]Target simulator:[/bold] {device_id}")

    # Resolve WDA source path
    wda_dir = wda_path or os.path.expanduser("~/.specterqa/wda/WebDriverAgent")
    build_dir = os.path.expanduser("~/.specterqa/wda/build")
    xcodeproj = os.path.join(wda_dir, "WebDriverAgent.xcodeproj")

    # Clone WDA if not present
    if not os.path.exists(xcodeproj):
        console.print(f"[bold]Cloning WebDriverAgent[/bold] into {wda_dir} …\n[dim](this only happens once)[/dim]")
        clone_result = subprocess.run(
            [
                "git",
                "clone",
                "--depth",
                "1",
                "https://github.com/appium/WebDriverAgent.git",
                wda_dir,
            ],
            capture_output=False,
        )
        if clone_result.returncode != 0:
            raise click.ClickException(
                "Failed to clone WebDriverAgent. "
                "Check your internet connection or pass --wda-path to an existing clone."
            )

    # Build WDA for testing
    console.print("[bold]Building WebDriverAgent for simulator …[/bold]")
    build_result = subprocess.run(
        [
            "xcodebuild",
            "build-for-testing",
            "-project",
            xcodeproj,
            "-scheme",
            "WebDriverAgentRunner",
            "-destination",
            f"id={device_id}",
            "-derivedDataPath",
            build_dir,
            "CODE_SIGN_IDENTITY=-",
            "CODE_SIGNING_REQUIRED=NO",
            "GCC_TREAT_WARNINGS_AS_ERRORS=NO",
        ],
        capture_output=False,
    )
    if build_result.returncode != 0:
        raise click.ClickException(
            "xcodebuild build-for-testing failed. Check Xcode installation and simulator status."
        )

    # Find the .xctestrun file produced by the build
    xctestrun_pattern = os.path.join(build_dir, "Build", "Products", "*.xctestrun")
    xctestrun_files = glob.glob(xctestrun_pattern)
    if not xctestrun_files:
        raise click.ClickException(
            f"No .xctestrun file found at {xctestrun_pattern}. The build may have failed silently."
        )
    xctestrun = sorted(xctestrun_files)[-1]  # use most recent
    console.print(f"[dim]Using xctestrun: {xctestrun}[/dim]")

    # Launch WDA as a background process
    console.print(f"[bold]Launching WebDriverAgent on {device_id} …[/bold]")
    env = os.environ.copy()
    env["USE_PORT"] = str(port)
    subprocess.Popen(  # noqa: S603 — intentional background process
        [
            "xcodebuild",
            "test-without-building",
            "-xctestrun",
            xctestrun,
            "-destination",
            f"id={device_id}",
        ],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    # Poll until WDA is ready
    console.print(f"[dim]Waiting for WDA to be ready on port {port} …[/dim]")
    wda_url = f"http://localhost:{port}"
    for elapsed in range(timeout):
        if WDADriver.is_available(wda_url=wda_url):
            console.print(f"[bold green]WDA is ready[/bold green] on port {port}. ({elapsed + 1}s)")
            console.print("\nRun your tests now:\n  [bold]specterqa-ios run --product <slug> --journey <id>[/bold]")
            return
        time.sleep(1)

    console.print(
        f"[bold yellow]WDA did not become ready within {timeout}s.[/bold yellow]\n"
        "It may still be launching in the background. "
        "Check with: [bold]specterqa-ios wda status[/bold]"
    )
    raise SystemExit(1)


# ---------------------------------------------------------------------------
# specterqa ios replay
# ---------------------------------------------------------------------------


@ios_command_group.command("replay")
@click.argument("replay_file", type=click.Path(exists=True))
@click.option("--verbose", "-v", is_flag=True, default=False, help="Print per-step status.")
@click.option(
    "--var",
    "variables",
    multiple=True,
    metavar="KEY=VALUE",
    help=("Set a replay variable for ${VAR} substitution. May be repeated: --var USERNAME=alice --var ENV=staging"),
)
def replay(replay_file: str, verbose: bool, variables: tuple) -> None:
    """Replay a recorded test session without AI.

    REPLAY_FILE is the path to a .yaml file produced by ios_save_replay.

    Loads the replay, starts the XCTest runner, executes each recorded action,
    and verifies any checkpoints (expected visible elements) after each step.

    Exit codes:

    \b
      0  — all steps passed
      1  — one or more steps failed
      2  — UI changed since recording (element not found — re-record recommended)

    Example:

    \b
      specterqa-ios replay tests/settings-smoke.yaml
      specterqa-ios replay tests/settings-smoke.yaml --verbose
      specterqa-ios replay tests/login.yaml --var USERNAME=alice --var PASSWORD=secret
    """
    from specterqa.ios.replay import ReplayPlayer

    # Parse --var KEY=VALUE pairs into a dict
    vars_dict: dict = {}
    for item in variables:
        if "=" in item:
            k, _, v = item.partition("=")
            vars_dict[k.strip()] = v
        else:
            console.print(f"[yellow]Warning:[/yellow] --var {item!r} ignored (expected KEY=VALUE format)")

    player = ReplayPlayer(replay_file)

    if verbose:
        console.print(f"[bold]Replaying:[/bold] {player.name}")
        console.print(f"[dim]Bundle:[/dim]  {player.bundle_id}")
        console.print(f"[dim]Steps:[/dim]   {len(player.steps)}")
        if vars_dict:
            console.print(f"[dim]Variables:[/dim] {vars_dict}")
        console.print()

    result = player.run(verbose=verbose, variables=vars_dict if vars_dict else None)

    passed_count = sum(1 for s in result["steps"] if s["passed"])
    total = len(result["steps"])

    if result.get("error") and not result["steps"]:
        # Session-level failure (e.g. runner failed to start)
        console.print(f"[bold red]ERROR[/bold red] — {result['error']}")
        raise SystemExit(1)

    if result["passed"]:
        console.print(f"[bold green]PASS[/bold green] — {passed_count}/{total} steps")
    else:
        console.print(f"[bold red]FAIL[/bold red] — {passed_count}/{total} steps")
        for step in result["steps"]:
            if not step["passed"]:
                console.print(f"  [red]{step['action']}[/red]: {step['error']}")
        if result["exit_code"] == 2:
            console.print(
                "\n[yellow]Exit code 2:[/yellow] UI appears to have changed since "
                "this replay was recorded. Re-record the journey to update it."
            )

    raise SystemExit(result["exit_code"])


# ---------------------------------------------------------------------------
# specterqa ios ci
# ---------------------------------------------------------------------------


@ios_command_group.command("ci")
@click.argument("replay_dir", default=".specterqa/replays", type=click.Path())
@click.option("--verbose", "-v", is_flag=True)
@click.option("--fail-fast", is_flag=True, help="Stop on first failure")
@click.option("--rerecord", is_flag=True, help="Re-record failed replays (requires AI)")
@click.option(
    "--no-reuse-runner",
    is_flag=True,
    help="Disable shared XCTest runner — each replay gets its own session (slower, full isolation)",
)
@click.option(
    "--parallel",
    type=int,
    default=1,
    help="Run N replays simultaneously using cloned simulators (default: 1)",
)
@click.option(
    "--json-output",
    "json_output_path",
    default=None,
    metavar="PATH",
    help="Write structured JSON results to PATH (e.g. .specterqa/results.json).",
)
def ci(
    replay_dir: str,
    verbose: bool,
    fail_fast: bool,
    rerecord: bool,
    no_reuse_runner: bool,
    parallel: int,
    json_output_path: str | None,
) -> None:
    """Run all replay files in a directory. Exit 0 if all pass.

    Designed for CI/CD pipelines. Shared-runner mode is on by default (~10x
    faster). Use --no-reuse-runner for full per-replay isolation.

    \b
      specterqa-ios ci .specterqa/replays/
      specterqa-ios ci --fail-fast
      specterqa-ios ci --parallel 4                         # run 4 replays simultaneously
      specterqa-ios ci --no-reuse-runner                    # per-replay isolation (slower)
      specterqa-ios ci --rerecord                           # re-record UI-changed tests
      specterqa-ios ci --json-output .specterqa/results.json  # write structured results

    \b
    Exit codes:
      0 = all passed
      1 = failures
      2 = UI changed (re-record needed)
    """
    from specterqa.ios.replay import ReplayPlayer
    from specterqa.ios.session_manager import TestSession

    # --reuse-runner is now the default; --no-reuse-runner disables it.
    reuse_runner = not no_reuse_runner

    replay_path = Path(replay_dir)
    if not replay_path.exists():
        click.echo(f"No replays found at {replay_dir}")
        raise SystemExit(0)

    # Find all .yaml replay files
    files = sorted(replay_path.glob("*.yaml"))
    if not files:
        click.echo(f"No .yaml replay files in {replay_dir}")
        raise SystemExit(0)

    click.echo(f"Running {len(files)} replay(s) from {replay_dir}")

    total_passed = 0
    total_failed = 0
    total_ui_changed = 0
    results = []
    import threading

    _results_lock = threading.Lock()

    def _print_replay_header(player: ReplayPlayer) -> None:
        click.echo(f"\n{'=' * 60}")
        click.echo(f"  {player.name} ({len(player.steps)} steps)")
        click.echo(f"{'=' * 60}")

    def _record_result(result: dict, f: "Path") -> None:
        nonlocal total_passed, total_failed, total_ui_changed
        passed = sum(1 for s in result.get("steps", []) if s["passed"])
        total = len(result.get("steps", []))
        with _results_lock:
            if result["passed"]:
                click.echo(f"  PASS — {passed}/{total}  [{f.name}]")
                total_passed += 1
            elif result.get("exit_code") == 2:
                click.echo(f"  UI CHANGED — {passed}/{total} (re-record needed)  [{f.name}]")
                total_ui_changed += 1
            else:
                click.echo(f"  FAIL — {passed}/{total}  [{f.name}]")
                for s in result.get("steps", []):
                    if not s["passed"]:
                        click.echo(f"    {s['action']}: {s['error']}")
                total_failed += 1
            results.append({"file": str(f), **result})

    # ── Parallel mode ─────────────────────────────────────────────────────
    if parallel > 1:
        from concurrent.futures import ThreadPoolExecutor, as_completed

        click.echo(f"[parallel] Running {len(files)} replays across {parallel} workers")
        t_start = time.time()

        def run_one(replay_file: Path):
            try:
                player = ReplayPlayer(str(replay_file))
                return replay_file, player.run(verbose=False)
            except Exception as exc:
                return replay_file, {
                    "name": replay_file.stem,
                    "bundle_id": "",
                    "steps": [],
                    "passed": False,
                    "exit_code": 1,
                    "error": str(exc),
                }

        with ThreadPoolExecutor(max_workers=parallel) as executor:
            futures = {executor.submit(run_one, f): f for f in files}
            for future in as_completed(futures):
                replay_file, result = future.result()
                _record_result(result, replay_file)
                if fail_fast and not result["passed"]:
                    # Cancel remaining futures
                    for pending in futures:
                        pending.cancel()
                    break

        elapsed = time.time() - t_start
        click.echo(f"\n[parallel] Total time: {elapsed:.1f}s across {parallel} workers")

    elif reuse_runner:
        # ── Shared-runner mode (default) ──────────────────────────────────
        # Load first replay to obtain bundle_id / device_id, then start one
        # TestSession and reuse it for all replays.  This avoids the ~10 s
        # xcodebuild cold-start penalty on each replay.
        first_player = ReplayPlayer(str(files[0]))
        click.echo(f"[reuse-runner] Starting shared session for bundle '{first_player.bundle_id}'")

        # Kill any leftover xcodebuild processes before starting.
        TestSession._kill_stale_runners()

        session = TestSession(
            bundle_id=first_player.bundle_id,
            source_udid=first_player.device_id,
        )
        session.start()

        try:
            for f in files:
                try:
                    player = ReplayPlayer(str(f))
                    _print_replay_header(player)

                    result = player.run_with_session(session, verbose=verbose)
                    _record_result(result, f)

                    if fail_fast and not result["passed"]:
                        break

                except Exception as exc:
                    click.echo(f"  ERROR: {exc}")
                    with _results_lock:
                        total_failed += 1
                    results.append({"file": str(f), "passed": False, "error": str(exc)})
                    if fail_fast:
                        break
        finally:
            try:
                session.stop()
            except Exception:
                pass

    else:
        # ── Per-replay isolation mode (--no-reuse-runner) ─────────────────
        for f in files:
            # Kill stale xcodebuild processes between replays to prevent
            # port conflicts and runner state bleed-over.
            TestSession._kill_stale_runners()

            try:
                player = ReplayPlayer(str(f))
                _print_replay_header(player)

                result = player.run(verbose=verbose)
                _record_result(result, f)

                if fail_fast and not result["passed"]:
                    break

            except Exception as exc:
                click.echo(f"  ERROR: {exc}")
                total_failed += 1
                results.append({"file": str(f), "passed": False, "error": str(exc)})

    # Summary
    click.echo(f"\n{'=' * 60}")
    click.echo(f"SUMMARY: {total_passed} passed, {total_failed} failed, {total_ui_changed} UI changed")
    click.echo(f"{'=' * 60}")

    # --json-output: write structured results file for CI artifact collection
    if json_output_path:
        total_duration = sum(r.get("duration_seconds", 0.0) for r in results)
        replay_records = []
        for r in results:
            replay_records.append(
                {
                    "file": r.get("file", ""),
                    "name": r.get("name", Path(r.get("file", "")).stem),
                    "passed": r.get("passed", False),
                    "duration_seconds": r.get("duration_seconds", 0.0),
                    "steps": r.get("steps", []),
                    "error": r.get("error"),
                }
            )
        json_payload = {
            "summary": {
                "total": total_passed + total_failed + total_ui_changed,
                "passed": total_passed,
                "failed": total_failed,
                "ui_changed": total_ui_changed,
                "duration_seconds": round(total_duration, 3),
            },
            "replays": replay_records,
        }
        try:
            output_path = Path(json_output_path)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(json.dumps(json_payload, indent=2, default=str))
            click.echo(f"Results written to: {output_path}")
        except Exception as exc:
            click.echo(f"Warning: failed to write JSON output to {json_output_path}: {exc}", err=True)

    if total_failed > 0:
        raise SystemExit(1)
    elif total_ui_changed > 0:
        raise SystemExit(2)
    else:
        raise SystemExit(0)


# ---------------------------------------------------------------------------
# Validate command — YAML schema validation
# ---------------------------------------------------------------------------


@ios_command_group.command("validate-replay")
@click.argument("replay_file", type=click.Path(exists=True))
def validate_replay(replay_file: str) -> None:
    """Validate a replay YAML file's structure and references.

    Checks for:
    - Required top-level keys (bundle_id, steps)
    - Valid action types (including Maestro-compatible aliases)
    - Unknown step keys
    - Unresolved skip_to references

    \b
      specterqa-ios validate-replay .specterqa/replays/smoke.yaml
    """
    import yaml

    with open(replay_file) as f:
        data = yaml.safe_load(f)

    issues: list[str] = []

    if not data or "replay" not in data:
        issues.append("Missing top-level 'replay' key")
        click.echo(f"INVALID: {issues[0]}")
        raise SystemExit(1)

    r = data["replay"]
    if "bundle_id" not in r:
        issues.append("Missing 'bundle_id'")
    if "steps" not in r:
        issues.append("Missing 'steps' list")

    if issues:
        click.echo(f"INVALID — {len(issues)} issue(s):")
        for issue in issues:
            click.echo(f"  {issue}")
        raise SystemExit(1)

    valid_actions = {
        "tap",
        "swipe",
        "swipe_back",
        "type",
        "press_key",
        "long_press",
        "wait_for_element",
        "wait",
        "skip_to",
        "assert",
    }
    valid_keys = {
        "action",
        "label",
        "element_label",
        "type",
        "element_index",
        "x",
        "y",
        "direction",
        "text",
        "key",
        "duration",
        "timeout",
        "expect_elements",
        "expect_not_elements",
        "expect_element_value",
        "expect_element_count",
        "expect_element_state",
        "expect_screenshot",
        "screenshot_threshold",
        "wait_for",
        "step_id",
        "skip_to",
        "if_element_visible",
        "if_not_element_visible",
        "step_timeout",
        "baseline_dir",
        # Maestro aliases
        "tapOn",
        "assertVisible",
        "assertNotVisible",
        "inputText",
        "waitFor",
    }

    step_ids: set[str] = set()
    for i, step in enumerate(r.get("steps", [])):
        # Support bare string steps e.g. `- swipe_back` which YAML parses as str
        if isinstance(step, str):
            step = {"action": step}
        # Resolve effective action (native or Maestro alias)
        action = step.get("action")
        if not action:
            if "tapOn" in step:
                action = "tap"
            elif "inputText" in step:
                action = "type"
            elif "waitFor" in step:
                action = "wait_for_element"
            elif "assertVisible" in step or "assertNotVisible" in step:
                action = "assert"

        if action and action not in valid_actions:
            issues.append(f"Step {i}: invalid action '{action}'")

        sid = step.get("step_id")
        if sid:
            step_ids.add(sid)

        for key in step:
            if key not in valid_keys:
                issues.append(f"Step {i}: unknown key '{key}'")

    # Validate skip_to references
    for i, step in enumerate(r.get("steps", [])):
        if isinstance(step, str):
            step = {"action": step}
        target = step.get("skip_to")
        if target and target not in step_ids:
            issues.append(f"Step {i}: skip_to references unknown step_id '{target}'")

    if issues:
        click.echo(f"INVALID — {len(issues)} issue(s):")
        for issue in issues:
            click.echo(f"  {issue}")
        raise SystemExit(1)

    step_count = len(r.get("steps", []))
    click.echo(f"VALID — {step_count} steps, {len(step_ids)} step IDs")
    raise SystemExit(0)


# ---------------------------------------------------------------------------
# Standalone entry point
# ---------------------------------------------------------------------------


def main() -> None:
    """Standalone entry point for the specterqa-ios CLI.

    Invoked by the ``specterqa-ios`` console script registered in pyproject.toml.
    Runs the iOS command group directly — no dependency on the upstream
    ``specterqa`` Typer app or its entry-point loading machinery.
    """
    ios_command_group()
