"""SpecterQA iOS CLI commands.

Provides the ``ios_command_group`` (mounted as ``specterqa ios``) with
sub-commands for setting up and running AI-driven iOS Simulator tests.

Commands:
  specterqa ios setup              — verify Xcode and environment
  specterqa ios devices            — list available iOS simulators
  specterqa ios boot               — boot a simulator by name
  specterqa ios install            — install a .app bundle on a simulator
  specterqa ios run                — run a test journey on iOS Simulator
  specterqa ios smoke              — quick smoke test for a product
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
        raise click.ClickException(
            "PyYAML is required to read config files.\n"
            "Install it with: pip install pyyaml"
        )
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
        raise click.ClickException(
            f"xcrun simctl list devices failed:\n{result.stderr}"
        )
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
        raise click.ClickException(
            f"Failed to boot simulator {device_id}:\n{result.stderr}"
        )


def _install_app(device_id: str, app_path: str) -> None:
    """Install a .app bundle on a simulator."""
    result = subprocess.run(
        ["xcrun", "simctl", "install", device_id, app_path],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise click.ClickException(
            f"Failed to install {app_path} on {device_id}:\n{result.stderr}"
        )


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
            all_devices = [
                dev
                for devices in data.get("devices", {}).values()
                for dev in devices
            ]
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
            raise click.ClickException(
                "No unbooted iPhone simulator found. Pass --device <name or UDID>."
            )
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

    # Resolve / discover device
    if device_id is None:
        device_id = _find_booted_udid()
        if device_id is None:
            _print("[bold]No booted simulator — booting default iPhone simulator...[/bold]")
            try:
                data = _list_simulators()
            except click.ClickException as exc:
                _err(str(exc), "Simulator Error")
                raise SystemExit(2)
            target_dev: dict[str, Any] | None = None
            for _rt, devs in sorted(data.get("devices", {}).items(), reverse=True):
                for d in devs:
                    if "iphone" in d.get("name", "").lower():
                        target_dev = d
                        break
                if target_dev:
                    break
            if target_dev is None:
                _err("No iPhone simulator found. Install Xcode and run 'specterqa-ios setup'.", "Simulator Error")
                raise SystemExit(2)
            device_id = target_dev["udid"]
            _print(f"Booting: {target_dev['name']} ({device_id})")
            _boot_simulator(device_id)
            time.sleep(3)

    # Install app if provided
    if app_path:
        resolved_app = Path(app_path).resolve()
        if not resolved_app.exists():
            _err(f"App not found: {app_path}", "Install Error")
            raise SystemExit(2)
        _print(f"Installing {resolved_app.name} on {device_id}...")
        _install_app(device_id, str(resolved_app))

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
                "\n".join([
                    f"[bold]Product:[/bold]    {product}",
                    f"[bold]Journey:[/bold]    {journey}",
                    f"[bold]Device ID:[/bold]  {device_id}",
                    f"[bold]Bundle ID:[/bold]  {bundle_id}",
                    f"[bold]Budget:[/bold]     ${budget:.2f}",
                    f"[bold]Run ID:[/bold]     {run_id}",
                    f"[bold]Evidence:[/bold]   {evidence_dir}",
                ]),
                title="[bold cyan]SpecterQA iOS Run[/bold cyan]",
                border_style="cyan",
            )
        )
        console.print()

    # Import SimDriver and AI decider
    try:
        from specterqa.ios.sim_driver import SimDriver
    except ImportError as exc:
        _err(f"Failed to import SimDriver: {exc}", "Import Error")
        raise SystemExit(3)

    try:
        from specterqa.engine.computer_use_decider import ComputerUseDecider
    except ImportError as exc:
        _err(f"Failed to import ComputerUseDecider: {exc}", "Import Error")
        raise SystemExit(3)

    # Create driver
    udid = product_cfg.get("simulator_id", device_id or "booted")
    driver = SimDriver(udid=udid, verbose=verbose)
    driver.device_info()

    # Launch app
    _print(f"Launching {bundle_id}...")
    try:
        driver.launch_app(bundle_id)
    except Exception as exc:
        logger.warning("launch_app failed (non-fatal): %s", exc)

    # Create AI decider with ACTUAL screenshot dimensions
    b64, w, h = driver.screenshot()
    decider = ComputerUseDecider(
        api_key=api_key,
        display_width=w,
        display_height=h,
    )

    # Execute journey steps
    steps = journey_cfg.get("steps", [])
    if not steps:
        _print("[yellow]Warning: journey has no steps defined.[/yellow]")

    all_passed = True
    step_results = []
    start_time = time.monotonic()

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

        step_start = time.monotonic()
        step_passed = False
        step_error = None

        full_goal = f"{goal}\nCheckpoint: {checkpoint}" if checkpoint else goal

        for iter_idx in range(step_max_iter):
            # Screenshot
            b64, w, h = driver.screenshot()

            # Ask Claude
            decision = decider.decide(
                goal=full_goal,
                screenshot_base64=b64,
                display_width=w,
                display_height=h,
            )

            if verbose or not plain:
                _print(f"  [{iter_idx}] action={decision.action} target={decision.target} achieved={decision.goal_achieved}")

            if decision.goal_achieved:
                step_passed = True
                if plain:
                    print(f"  PASS ({round(time.monotonic() - step_start, 1)}s)", file=sys.stderr, flush=True)
                else:
                    console.print(f"    [green]PASS[/green] ({round(time.monotonic() - step_start, 1):.1f}s)")
                break

            # Execute the action
            try:
                action_dict = _decision_to_action(decision)
                driver.execute(action_dict)
            except Exception as exc:
                step_error = str(exc)
                logger.error("Action failed at step %s iter %d: %s", step_id, iter_idx, exc)
                break
        else:
            step_error = f"Max iterations ({step_max_iter}) reached"
            if plain:
                print(f"  FAIL — {step_error}", file=sys.stderr, flush=True)
            else:
                console.print(f"    [red]FAIL[/red] — {step_error}")

        if not step_passed:
            all_passed = False

        step_results.append({
            "step_id": step_id,
            "description": description,
            "passed": step_passed,
            "duration_seconds": round(time.monotonic() - step_start, 3),
            "error": step_error,
            "findings": [],
        })

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
                "\n".join([
                    verdict,
                    "",
                    f"  Steps:     {passed_count}/{len(steps)} passed",
                    f"  Duration:  {total_duration:.1f}s",
                    f"  Run ID:    {run_id}",
                    f"  Evidence:  {evidence_dir}",
                ]),
                border_style=border,
            )
        )
        console.print()

    raise SystemExit(0 if all_passed else 1)


# ---------------------------------------------------------------------------
# specterqa ios init
# ---------------------------------------------------------------------------


@ios_command_group.command("init")
@click.option("--slug", "app_slug", default="my-ios-app", show_default=True, help="Short app identifier for file names.")
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
            smoke_journey = journeys_hint[0] if isinstance(journeys_hint[0], str) else journeys_hint[0].get("id", smoke_journey)
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
                    all_udids = {
                        dev.get("udid")
                        for devs in data.get("devices", {}).values()
                        for dev in devs
                    }
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
                            checks.append(("bundle_id installed on simulator", False, "simctl listapps failed — is simulator booted?"))
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
                steps_missing_goal = [
                    s.get("id", f"step-{i+1}") for i, s in enumerate(steps) if not s.get("goal")
                ]
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
        console.print(Panel(
            f"[bold red]{len(errors)} error(s) found.[/bold red] Fix the issues above before running.",
            border_style="red",
        ))
        raise SystemExit(1)
    else:
        console.print()
        console.print(Panel(
            "[bold green]Config is valid.[/bold green]",
            border_style="green",
        ))


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
# Standalone entry point
# ---------------------------------------------------------------------------


def main() -> None:
    """Standalone entry point for the specterqa-ios CLI.

    Invoked by the ``specterqa-ios`` console script registered in pyproject.toml.
    Runs the iOS command group directly — no dependency on the upstream
    ``specterqa`` Typer app or its entry-point loading machinery.
    """
    ios_command_group()
