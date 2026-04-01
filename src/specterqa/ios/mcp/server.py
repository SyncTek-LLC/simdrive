"""SpecterQA iOS MCP Server — Model Context Protocol server for Claude Code integration.

Exposes SpecterQA iOS simulator capabilities as MCP tools so that AI agents
(Claude Code and others) can discover simulators, run iOS tests, and retrieve
results programmatically via stdio transport.

Usage:
    specterqa-ios-mcp            # stdio transport (console_scripts entry point)
    python -m specterqa.ios.mcp  # alternative invocation
    specterqa ios serve          # via CLI serve command

The server provides eleven tools:

    ios_setup             Check Xcode, simulator, and API key environment
    ios_list_devices      List available iOS simulators via xcrun simctl
    ios_boot_device       Boot a simulator by name or UDID
    ios_install_app       Install a .app bundle on a simulator
    ios_run_test          Run a full test journey (main testing entry point)
    ios_run_smoke         Quick smoke test for a product (reduced budget)
    ios_run_exploratory   Persona-driven AI exploration of an app
    ios_get_results       Retrieve results from a previous run by run_id
    ios_screenshot        Take a screenshot of the current simulator state
    ios_list_products     List configured products from .specterqa/products/
    ios_list_journeys     List configured journeys from .specterqa/journeys/

INIT-2026-492 — SpecterQA iOS Simulator Driver.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import subprocess
import time
import uuid
from pathlib import Path
from typing import Any

logger = logging.getLogger("specterqa.ios.mcp")


# ---------------------------------------------------------------------------
# Project-directory helpers (mirrors CLI helpers)
# ---------------------------------------------------------------------------


def _resolve_project_dir(directory: str | None = None) -> Path:
    """Find the .specterqa/ project directory.

    Searches upward from *directory* (default: cwd) for a .specterqa/ folder.
    Returns the path to the .specterqa directory, or a cwd-based default.
    """
    start = Path(directory).resolve() if directory else Path.cwd()

    # Check the given directory itself
    candidate = start / ".specterqa"
    if candidate.is_dir():
        return candidate

    # Walk up the parent chain
    for parent in start.parents:
        candidate = parent / ".specterqa"
        if candidate.is_dir():
            return candidate

    # Fallback: return a default (may not exist yet)
    return start / ".specterqa"


def _load_yaml(path: Path) -> dict[str, Any]:
    """Load a YAML file, returning a dict. Returns empty dict on failure."""
    try:
        import yaml  # type: ignore[import-untyped]

        with path.open("r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
        return data or {}
    except Exception as exc:
        logger.warning("Failed to load %s: %s", path, exc)
        return {}


def _list_simulator_devices() -> list[dict[str, Any]]:
    """Run ``xcrun simctl list devices --json`` and return flat device list."""
    proc = subprocess.run(
        ["xcrun", "simctl", "list", "devices", "--json"],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        return []
    try:
        data = json.loads(proc.stdout)
    except (json.JSONDecodeError, ValueError):
        return []

    devices: list[dict[str, Any]] = []
    for runtime_id, device_list in data.get("devices", {}).items():
        # Normalise runtime key: com.apple.CoreSimulator.SimRuntime.iOS-17-2 → iOS 17.2
        runtime_label = (
            runtime_id
            .replace("com.apple.CoreSimulator.SimRuntime.", "")
            .replace("-", " ")
        )
        for dev in device_list:
            devices.append({**dev, "runtime": runtime_label})
    return devices


def _find_booted_udid() -> str | None:
    """Return the UDID of a currently booted simulator, or None."""
    for dev in _list_simulator_devices():
        if dev.get("state") == "Booted":
            return dev.get("udid")
    return None


def _find_simulator_by_name(name_fragment: str) -> dict[str, Any] | None:
    """Find the first simulator whose name contains *name_fragment* (case-insensitive)."""
    needle = name_fragment.lower()
    for dev in _list_simulator_devices():
        if needle in dev.get("name", "").lower():
            return dev
    return None


def _json_serialize(obj: Any) -> str:
    """JSON serializer for non-standard types (Path, etc.)."""
    if isinstance(obj, Path):
        return str(obj)
    raise TypeError(f"Object of type {type(obj)} is not JSON serializable")


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


# ---------------------------------------------------------------------------
# In-memory result store (lives for the duration of the server process)
# ---------------------------------------------------------------------------

_RESULT_STORE: dict[str, Any] = {}


# ---------------------------------------------------------------------------
# MCP server factory
# ---------------------------------------------------------------------------


def create_server() -> Any:
    """Create and configure the SpecterQA iOS MCP server.

    Returns:
        A FastMCP server instance with all iOS tools registered.

    Raises:
        ImportError: if the ``mcp`` package is not installed.
    """
    try:
        from mcp.server.fastmcp import FastMCP
    except ImportError:
        raise ImportError(
            "The 'mcp' package is required for the SpecterQA iOS MCP server.\n\n"
            "Install it:\n"
            "  pip install 'specterqa-ios[mcp]'\n"
            "  # or: pip install mcp>=1.0.0"
        )

    mcp = FastMCP(
        "specterqa-ios",
        instructions=(
            "SpecterQA iOS is an AI-powered testing tool for iOS apps running in "
            "the Xcode Simulator. It uses Claude Computer Use to drive the simulator, "
            "execute test journeys, and surface UX issues and bugs. "
            "Before running tests, call ios_setup to verify the environment, "
            "then ios_list_products to see what's configured. "
            "Use ios_run_test as the primary testing tool. "
            "Requires macOS with Xcode 15+ and ANTHROPIC_API_KEY."
        ),
    )

    # ── Tool: ios_setup ────────────────────────────────────────────────────

    @mcp.tool(
        name="ios_setup",
        description=(
            "Check the SpecterQA iOS environment: Xcode/xcrun availability, "
            "iOS simulator presence, ANTHROPIC_API_KEY, and package importability. "
            "Returns a status dict with pass/fail for each check. "
            "Call this first to verify the environment is ready for testing."
        ),
    )
    async def ios_setup() -> str:
        """Check Xcode, simulators, and API key.

        Returns:
            JSON dict with 'all_ok' bool and 'checks' list of {name, ok, detail} dicts.
        """
        checks: list[dict[str, Any]] = []

        # 1. xcrun / Xcode
        xcrun_result = subprocess.run(
            ["xcrun", "--version"], capture_output=True, text=True
        )
        xcrun_ok = xcrun_result.returncode == 0
        checks.append({
            "name": "Xcode / xcrun",
            "ok": xcrun_ok,
            "detail": xcrun_result.stdout.strip() if xcrun_ok else "xcrun not found — install Xcode",
        })

        # 2. iOS simulators
        sim_ok = False
        sim_detail = ""
        if xcrun_ok:
            devices = _list_simulator_devices()
            booted = [d for d in devices if d.get("state") == "Booted"]
            sim_ok = len(devices) > 0
            sim_detail = (
                f"{len(devices)} simulators, {len(booted)} booted"
                if devices
                else "no simulators found"
            )
        checks.append({"name": "iOS Simulators", "ok": sim_ok, "detail": sim_detail})

        # 3. ANTHROPIC_API_KEY
        api_key = os.environ.get("ANTHROPIC_API_KEY", "")
        api_ok = bool(api_key)
        checks.append({
            "name": "ANTHROPIC_API_KEY",
            "ok": api_ok,
            "detail": "set" if api_ok else "NOT SET — export ANTHROPIC_API_KEY=sk-ant-...",
        })

        # 4. Package importability
        pkg_ok = False
        pkg_detail = ""
        try:
            from specterqa.ios.sim_driver import SimDriver  # noqa: F401
            pkg_ok = True
            pkg_detail = "specterqa.ios importable"
        except ImportError as exc:
            pkg_detail = f"import error: {exc}"
        checks.append({"name": "specterqa-ios package", "ok": pkg_ok, "detail": pkg_detail})

        all_ok = all(c["ok"] for c in checks)
        return json.dumps({"all_ok": all_ok, "checks": checks})

    # ── Tool: ios_list_devices ─────────────────────────────────────────────

    @mcp.tool(
        name="ios_list_devices",
        description=(
            "List available iOS Simulator devices via xcrun simctl. "
            "Returns a JSON array of device objects with name, UDID, state, "
            "and runtime. Use this to find a device name or UDID for other tools."
        ),
    )
    async def ios_list_devices() -> str:
        """List available iOS simulators.

        Returns:
            JSON array of device dicts with name, udid, state, and runtime.
        """
        devices = _list_simulator_devices()
        return json.dumps(devices)

    # ── Tool: ios_boot_device ──────────────────────────────────────────────

    @mcp.tool(
        name="ios_boot_device",
        description=(
            "Boot an iOS Simulator by name fragment or UDID. "
            "If device_name is not specified, boots the first available iPhone simulator. "
            "Accepts a name fragment (e.g. 'iPhone 15') or a full UDID. "
            "Returns the booted device's name and UDID."
        ),
    )
    async def ios_boot_device(device_name: str | None = None) -> str:
        """Boot an iOS simulator.

        Args:
            device_name: Simulator name fragment or full UDID to boot.
                         If omitted, boots the first available iPhone simulator.

        Returns:
            JSON dict with 'ok', 'device_name', 'device_id', and optional 'error'.
        """
        devices = _list_simulator_devices()

        # Resolve target device
        target: dict[str, Any] | None = None

        if device_name is None:
            # Pick the first unbooted iPhone from the most recent runtime
            for dev in reversed(devices):
                if "iphone" in dev.get("name", "").lower() and dev.get("state") != "Booted":
                    target = dev
                    break
            if target is None:
                # Maybe there's already a booted one — use it
                for dev in devices:
                    if dev.get("state") == "Booted":
                        return json.dumps({
                            "ok": True,
                            "device_name": dev.get("name"),
                            "device_id": dev.get("udid"),
                            "note": "simulator already booted",
                        })
                return json.dumps({
                    "ok": False,
                    "error": "No iPhone simulator found. Pass device_name.",
                })
        elif len(device_name) == 36 and device_name.count("-") == 4:
            # Looks like a UDID
            for dev in devices:
                if dev.get("udid") == device_name:
                    target = dev
                    break
            if target is None:
                target = {"udid": device_name, "name": device_name}
        else:
            needle = device_name.lower()
            for dev in devices:
                if needle in dev.get("name", "").lower():
                    target = dev
                    break
            if target is None:
                return json.dumps({
                    "ok": False,
                    "error": f"No simulator matching '{device_name}' found.",
                })

        device_id = target["udid"]
        dev_label = target.get("name", device_id)

        result = subprocess.run(
            ["xcrun", "simctl", "boot", device_id],
            capture_output=True,
            text=True,
        )

        already_booted = (
            result.returncode != 0
            and "Unable to boot device in current state: Booted" in result.stderr
        )

        if result.returncode == 0 or already_booted:
            return json.dumps({
                "ok": True,
                "device_name": dev_label,
                "device_id": device_id,
                "note": "already booted" if already_booted else "booted successfully",
            })

        return json.dumps({
            "ok": False,
            "device_name": dev_label,
            "device_id": device_id,
            "error": result.stderr.strip(),
        })

    # ── Tool: ios_install_app ──────────────────────────────────────────────

    @mcp.tool(
        name="ios_install_app",
        description=(
            "Install a .app bundle on an iOS Simulator. "
            "app_path should be the path to the .app directory produced by a debug build "
            "(e.g. DerivedData/.../Debug-iphonesimulator/MyApp.app). "
            "If device_id is not provided, uses the currently booted simulator."
        ),
    )
    async def ios_install_app(
        app_path: str,
        device_id: str | None = None,
    ) -> str:
        """Install a .app bundle on a simulator.

        Args:
            app_path: Path to the .app directory.
            device_id: Simulator UDID. Defaults to currently booted simulator.

        Returns:
            JSON dict with 'ok', 'app_name', 'device_id', and optional 'error'.
        """
        resolved = Path(app_path).resolve()
        if not resolved.exists():
            return json.dumps({"ok": False, "error": f"App not found: {app_path}"})

        if device_id is None:
            device_id = _find_booted_udid()
            if device_id is None:
                return json.dumps({
                    "ok": False,
                    "error": (
                        "No booted simulator found. "
                        "Call ios_boot_device first, or pass device_id."
                    ),
                })

        result = subprocess.run(
            ["xcrun", "simctl", "install", device_id, str(resolved)],
            capture_output=True,
            text=True,
        )

        if result.returncode == 0:
            return json.dumps({
                "ok": True,
                "app_name": resolved.name,
                "device_id": device_id,
            })

        return json.dumps({
            "ok": False,
            "app_name": resolved.name,
            "device_id": device_id,
            "error": result.stderr.strip(),
        })

    # ── Tool: ios_run_test ─────────────────────────────────────────────────

    @mcp.tool(
        name="ios_run_test",
        description=(
            "Run a SpecterQA iOS test journey against an app in the simulator. "
            "This is the primary testing tool. Loads YAML configs from .specterqa/, "
            "creates a SimulatorDriver, runs each step with Claude Computer Use, "
            "and returns structured results with pass/fail, findings, and cost. "
            "Requires: Xcode, a booted simulator, and ANTHROPIC_API_KEY. "
            "product_slug must match a .specterqa/products/<slug>.yaml file. "
            "journey_name must match a .specterqa/journeys/<name>.yaml file. "
            "preferred_backend: 'xctest' (headless, port 8222), 'indigo' (headless, "
            "ctypes), 'cgevents' (requires visible window), or omit for auto-selection."
        ),
    )
    async def ios_run_test(
        product_slug: str,
        journey_name: str,
        device_name: str | None = None,
        budget: float = 5.0,
        max_steps: int = 20,
        directory: str | None = None,
        preferred_backend: str | None = None,
    ) -> str:
        """Run a test journey against an iOS app in the simulator.

        Args:
            product_slug: Product slug (matches .specterqa/products/<slug>.yaml).
            journey_name: Journey ID (matches .specterqa/journeys/<name>.yaml).
            device_name: Simulator name fragment or UDID. Defaults to booted simulator.
            budget: Maximum AI spend in USD for this run. Default: $5.00.
            max_steps: Max AI iterations per journey step. Default: 20.
            directory: Working directory to find .specterqa/ project.
                       Defaults to the server's working directory.
            preferred_backend: Force a specific touch backend — 'xctest', 'indigo',
                or 'cgevents'.  Omit (or pass None) for auto-selection.

        Returns:
            JSON string with run_id, passed, step_reports, findings, backend_used,
            and cost.
        """
        import sys

        api_key = os.environ.get("ANTHROPIC_API_KEY", "")
        if not api_key:
            return json.dumps({
                "ok": False,
                "error": "ANTHROPIC_API_KEY is not set.",
                "error_code": "API_KEY_MISSING",
            })

        project_dir = _resolve_project_dir(directory)
        if not project_dir.is_dir():
            return json.dumps({
                "ok": False,
                "error": (
                    f"SpecterQA project not initialized at {project_dir.parent}. "
                    "Run 'specterqa ios init' first."
                ),
                "error_code": "PROJECT_NOT_INITIALIZED",
            })

        # Load product and journey configs
        product_cfg_raw = _load_yaml(project_dir / "products" / f"{product_slug}.yaml")
        product_cfg = product_cfg_raw.get("product", product_cfg_raw)

        journey_cfg_raw = _load_yaml(project_dir / "journeys" / f"{journey_name}.yaml")
        journey_cfg = journey_cfg_raw.get("scenario", journey_cfg_raw)

        if not product_cfg:
            return json.dumps({
                "ok": False,
                "error": f"Product config not found: .specterqa/products/{product_slug}.yaml",
                "error_code": "PRODUCT_NOT_FOUND",
            })

        if not journey_cfg:
            return json.dumps({
                "ok": False,
                "error": f"Journey config not found: .specterqa/journeys/{journey_name}.yaml",
                "error_code": "JOURNEY_NOT_FOUND",
            })

        # Import SimDriver and AI decider
        try:
            from specterqa.ios.sim_driver import SimDriver
        except ImportError as exc:
            return json.dumps({
                "ok": False,
                "error": f"Failed to import SimDriver: {exc}",
                "error_code": "IMPORT_ERROR",
            })

        try:
            from specterqa.engine.computer_use_decider import ComputerUseDecider
        except ImportError as exc:
            return json.dumps({
                "ok": False,
                "error": f"Failed to import ComputerUseDecider: {exc}",
                "error_code": "IMPORT_ERROR",
            })

        # Resolve device
        device_id: str | None = None
        if device_name:
            if len(device_name) == 36 and device_name.count("-") == 4:
                device_id = device_name
            else:
                found = _find_simulator_by_name(device_name)
                device_id = found["udid"] if found else None
        if device_id is None:
            device_id = _find_booted_udid()
        if device_id is None:
            return json.dumps({
                "ok": False,
                "error": "No booted simulator found. Call ios_boot_device first.",
                "error_code": "NO_SIMULATOR",
            })

        bundle_id: str = product_cfg.get("bundle_id", product_cfg.get("name", product_slug))

        run_id = f"IOS-RUN-{time.strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex[:8].upper()}"
        evidence_dir = project_dir / "evidence" / run_id
        evidence_dir.mkdir(parents=True, exist_ok=True)

        # Create driver
        udid = product_cfg.get("simulator_id", device_id)
        driver = SimDriver(udid=udid, verbose=False)
        driver.device_info()

        # Launch app
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

        steps = journey_cfg.get("steps", [])
        all_passed = True
        step_reports: list[dict[str, Any]] = []
        start_time = time.monotonic()

        for i, step in enumerate(steps, 1):
            step_id = step.get("id", f"step-{i}")
            description = step.get("description", step.get("goal", step_id))
            goal = step.get("goal", description)
            checkpoint = step.get("checkpoint", None)
            step_max_iter = step.get("max_iterations", max_steps)

            step_start = time.monotonic()
            step_passed = False
            step_error = None
            full_goal = f"{goal}\nCheckpoint: {checkpoint}" if checkpoint else goal

            for iter_idx in range(step_max_iter):
                b64, w, h = driver.screenshot()
                decision = decider.decide(
                    goal=full_goal,
                    screenshot_base64=b64,
                    display_width=w,
                    display_height=h,
                )
                if decision.goal_achieved:
                    step_passed = True
                    break
                try:
                    action_dict = _decision_to_action(decision)
                    driver.execute(action_dict)
                except Exception as exc:
                    step_error = str(exc)
                    logger.error("Action failed at step %s iter %d: %s", step_id, iter_idx, exc)
                    break
            else:
                step_error = f"Max iterations ({step_max_iter}) reached"

            if not step_passed:
                all_passed = False

            step_reports.append({
                "step_id": step_id,
                "description": description,
                "passed": step_passed,
                "duration_seconds": round(time.monotonic() - step_start, 3),
                "error": step_error,
                "findings": [],
            })

        total_duration = round(time.monotonic() - start_time, 3)
        passed_count = sum(1 for sr in step_reports if sr.get("passed"))

        run_result: dict[str, Any] = {
            "run_id": run_id,
            "product": product_slug,
            "journey": journey_name,
            "device_id": device_id,
            "bundle_id": bundle_id,
            "passed": all_passed,
            "step_count": len(steps),
            "passed_count": passed_count,
            "step_reports": step_reports,
            "findings": [],
            "duration_seconds": total_duration,
            "evidence_dir": str(evidence_dir),
        }

        # Persist to evidence directory and in-memory store
        try:
            (evidence_dir / "run-result.json").write_text(
                json.dumps(run_result, indent=2, default=_json_serialize),
                encoding="utf-8",
            )
        except Exception as exc:
            logger.warning("Failed to write run-result.json: %s", exc)

        _RESULT_STORE[run_id] = run_result

        return json.dumps(run_result, default=_json_serialize)

    # ── Tool: ios_run_smoke ────────────────────────────────────────────────

    @mcp.tool(
        name="ios_run_smoke",
        description=(
            "Run a quick smoke test for an iOS product. "
            "Uses the 'smoke-test' journey (or the first journey in the product config) "
            "with a reduced budget of $1.00 and fewer max steps. "
            "Thin wrapper over ios_run_test — fast sanity check."
        ),
    )
    async def ios_run_smoke(
        product_slug: str,
        device_name: str | None = None,
        directory: str | None = None,
    ) -> str:
        """Run a quick smoke test for a product.

        Args:
            product_slug: Product slug (matches .specterqa/products/<slug>.yaml).
            device_name: Simulator name fragment or UDID.
            directory: Working directory to find .specterqa/ project.

        Returns:
            JSON run result (same schema as ios_run_test).
        """
        project_dir = _resolve_project_dir(directory)
        smoke_journey = "smoke-test"

        # Check the product config for a preferred first journey
        product_raw = _load_yaml(project_dir / "products" / f"{product_slug}.yaml")
        product_cfg = product_raw.get("product", product_raw)
        journeys_hint = product_cfg.get("journeys", [])
        if journeys_hint:
            first = journeys_hint[0]
            smoke_journey = first if isinstance(first, str) else first.get("id", smoke_journey)

        return await ios_run_test(
            product_slug=product_slug,
            journey_name=smoke_journey,
            device_name=device_name,
            budget=1.0,
            max_steps=10,
            directory=directory,
        )

    # ── Tool: ios_run_exploratory ──────────────────────────────────────────

    @mcp.tool(
        name="ios_run_exploratory",
        description=(
            "Run persona-driven AI exploration of an iOS app. "
            "The AI adopts the specified persona (from .specterqa/personas/<name>.yaml) "
            "and explores the app autonomously, surfacing UX issues and bugs "
            "without a predefined script. "
            "product_slug is used to resolve bundle_id and device config. "
            "persona_name must match a .specterqa/personas/<name>.yaml file."
        ),
    )
    async def ios_run_exploratory(
        product_slug: str,
        persona_name: str,
        max_steps: int = 20,
        device_name: str | None = None,
        directory: str | None = None,
    ) -> str:
        """Run persona-driven exploratory testing.

        Args:
            product_slug: Product slug (matches .specterqa/products/<slug>.yaml).
            persona_name: Persona name (matches .specterqa/personas/<name>.yaml).
            max_steps: Maximum exploration iterations. Default: 20.
            device_name: Simulator name fragment or UDID.
            directory: Working directory to find .specterqa/ project.

        Returns:
            JSON exploration result with run_id, findings, and action trace.
        """
        project_dir = _resolve_project_dir(directory)

        product_raw = _load_yaml(project_dir / "products" / f"{product_slug}.yaml")
        product_cfg = product_raw.get("product", product_raw)

        persona_raw = _load_yaml(project_dir / "personas" / f"{persona_name}.yaml")
        persona_cfg = persona_raw.get("persona", persona_raw)

        if not persona_cfg:
            return json.dumps({
                "ok": False,
                "error": f"Persona config not found: .specterqa/personas/{persona_name}.yaml",
                "error_code": "PERSONA_NOT_FOUND",
            })

        # Import exploratory agent
        try:
            from specterqa.ios.exploratory.agent import ExploratoryAgent
            from specterqa.ios.sim_driver import SimDriver
            from specterqa.ios.engine.ai_step_runner import IOSAIStepRunner
        except ImportError as exc:
            return json.dumps({
                "ok": False,
                "error": f"Import error: {exc}",
                "error_code": "IMPORT_ERROR",
            })

        try:
            from specterqa.engine.computer_use_decider import ComputerUseDecider
        except ImportError as exc:
            return json.dumps({
                "ok": False,
                "error": f"Import error: {exc}",
                "error_code": "IMPORT_ERROR",
            })

        api_key = os.environ.get("ANTHROPIC_API_KEY", "")
        if not api_key:
            return json.dumps({
                "ok": False,
                "error": "ANTHROPIC_API_KEY is not set.",
                "error_code": "API_KEY_MISSING",
            })

        # Resolve device
        device_id: str | None = None
        if device_name:
            if len(device_name) == 36 and device_name.count("-") == 4:
                device_id = device_name
            else:
                found = _find_simulator_by_name(device_name)
                device_id = found["udid"] if found else None
        if device_id is None:
            device_id = _find_booted_udid()
        if device_id is None:
            return json.dumps({
                "ok": False,
                "error": "No booted simulator. Call ios_boot_device first.",
                "error_code": "NO_SIMULATOR",
            })

        bundle_id: str = product_cfg.get("bundle_id", product_cfg.get("name", product_slug))

        # Create SimDriver
        udid = product_cfg.get("simulator_id", device_id)
        driver = SimDriver(udid=udid, verbose=False)
        driver.device_info()

        try:
            driver.launch_app(bundle_id)
        except Exception as exc:
            logger.warning("launch_app failed (non-fatal): %s", exc)

        # Create decider with actual screenshot dimensions
        b64, w, h = driver.screenshot()
        decider = ComputerUseDecider(api_key=api_key, display_width=w, display_height=h)

        run_id = f"IOS-EXP-{time.strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex[:8].upper()}"
        evidence_dir = project_dir / "evidence" / run_id
        evidence_dir.mkdir(parents=True, exist_ok=True)

        runner = IOSAIStepRunner(
            decider=decider,
            executor=driver,
            evidence_dir=str(evidence_dir),
        )

        try:
            agent = ExploratoryAgent(
                step_runner=runner,
                persona=persona_cfg,
            )
            app_context = product_cfg.get("description", f"{product_slug} iOS app")
            result = agent.explore(app_context=app_context, max_steps=max_steps)
        except Exception as exc:
            logger.exception("Exploratory run failed")
            return json.dumps({
                "ok": False,
                "error": f"Exploration failed: {exc}",
                "error_code": "EXPLORATION_ERROR",
            })
        finally:
            pass  # SimDriver has no stop() — resources are cleaned up automatically

        result["run_id"] = run_id
        result["product"] = product_slug
        result["persona"] = persona_name
        result["evidence_dir"] = str(evidence_dir)

        _RESULT_STORE[run_id] = result

        try:
            (evidence_dir / "run-result.json").write_text(
                json.dumps(result, indent=2, default=_json_serialize),
                encoding="utf-8",
            )
        except Exception as exc:
            logger.warning("Failed to write run-result.json: %s", exc)

        return json.dumps(result, default=_json_serialize)

    # ── Tool: ios_get_results ──────────────────────────────────────────────

    @mcp.tool(
        name="ios_get_results",
        description=(
            "Retrieve stored results for a previous iOS test run by run_id. "
            "Checks the in-memory store first, then falls back to the evidence directory. "
            "run_id is returned by ios_run_test, ios_run_smoke, and ios_run_exploratory."
        ),
    )
    async def ios_get_results(
        run_id: str,
        directory: str | None = None,
    ) -> str:
        """Get results for a completed run.

        Args:
            run_id: The run ID returned by ios_run_test or ios_run_exploratory.
            directory: Working directory to find .specterqa/ project evidence.

        Returns:
            JSON string with the full run result, or an error with available run IDs.
        """
        # Check in-memory store first (fast path for same-session results)
        if run_id in _RESULT_STORE:
            return json.dumps(_RESULT_STORE[run_id], default=_json_serialize)

        # Fall back to evidence directory on disk
        project_dir = _resolve_project_dir(directory)
        evidence_dir = project_dir / "evidence"

        run_dir = evidence_dir / run_id
        result_file = run_dir / "run-result.json"

        if result_file.is_file():
            try:
                return result_file.read_text(encoding="utf-8")
            except OSError as exc:
                return json.dumps({"error": f"Failed to read results: {exc}", "run_id": run_id})

        # Not found — list available runs to help
        available: list[str] = []
        if evidence_dir.is_dir():
            available = sorted(
                [d.name for d in evidence_dir.iterdir() if d.is_dir() and d.name.startswith("IOS-")],
                reverse=True,
            )

        return json.dumps({
            "error": f"Run ID not found: {run_id}",
            "error_code": "RUN_NOT_FOUND",
            "available_run_ids": available[:20],
        })

    # ── Tool: ios_screenshot ───────────────────────────────────────────────

    @mcp.tool(
        name="ios_screenshot",
        description=(
            "Take a screenshot of the current state of the iOS Simulator. "
            "Returns a base64-encoded PNG image. "
            "Useful for inspecting the simulator state without running a full test. "
            "Requires a booted simulator."
        ),
    )
    async def ios_screenshot(device_id: str | None = None) -> str:
        """Take a screenshot of the current simulator state.

        Args:
            device_id: Simulator UDID. Defaults to the currently booted simulator.

        Returns:
            JSON dict with 'ok', 'image_base64' (PNG), and optional 'error'.
        """
        if device_id is None:
            device_id = _find_booted_udid()
            if device_id is None:
                return json.dumps({
                    "ok": False,
                    "error": "No booted simulator found. Call ios_boot_device first.",
                })

        # Write to a temp file then read back
        import tempfile

        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
            tmp_path = tmp.name

        try:
            result = subprocess.run(
                ["xcrun", "simctl", "io", device_id, "screenshot", tmp_path],
                capture_output=True,
                text=True,
                timeout=15,
            )

            if result.returncode != 0:
                return json.dumps({
                    "ok": False,
                    "device_id": device_id,
                    "error": result.stderr.strip() or "Screenshot failed",
                })

            with open(tmp_path, "rb") as f:
                image_bytes = f.read()

            image_b64 = base64.b64encode(image_bytes).decode("ascii")
            return json.dumps({
                "ok": True,
                "device_id": device_id,
                "image_base64": image_b64,
                "format": "png",
                "size_bytes": len(image_bytes),
            })
        except subprocess.TimeoutExpired:
            return json.dumps({
                "ok": False,
                "device_id": device_id,
                "error": "Screenshot timed out after 15 seconds.",
            })
        except Exception as exc:
            return json.dumps({"ok": False, "device_id": device_id, "error": str(exc)})
        finally:
            try:
                Path(tmp_path).unlink(missing_ok=True)
            except Exception:
                pass

    # ── Tool: ios_list_products ────────────────────────────────────────────

    @mcp.tool(
        name="ios_list_products",
        description=(
            "List configured SpecterQA iOS products from .specterqa/products/. "
            "Returns product slugs, display names, bundle IDs, and the list of "
            "journeys configured for each product."
        ),
    )
    async def ios_list_products(directory: str | None = None) -> str:
        """List configured products.

        Args:
            directory: Working directory to find .specterqa/ project.

        Returns:
            JSON array of product dicts with slug, name, bundle_id, and journeys.
        """
        project_dir = _resolve_project_dir(directory)
        products_dir = project_dir / "products"

        if not products_dir.is_dir():
            return json.dumps({
                "error": f"Products directory not found: {products_dir}",
                "error_code": "PROJECT_NOT_INITIALIZED",
            })

        results: list[dict[str, Any]] = []

        for product_file in sorted(products_dir.glob("*.yaml")):
            raw = _load_yaml(product_file)
            cfg = raw.get("product", raw)
            slug = product_file.stem
            results.append({
                "slug": slug,
                "name": cfg.get("name", slug),
                "bundle_id": cfg.get("bundle_id", ""),
                "description": cfg.get("description", ""),
                "journeys": cfg.get("journeys", []),
            })

        return json.dumps(results, indent=2)

    # ── Tool: ios_list_journeys ────────────────────────────────────────────

    @mcp.tool(
        name="ios_list_journeys",
        description=(
            "List configured SpecterQA iOS journeys from .specterqa/journeys/. "
            "Returns journey IDs, names, tags, and step counts. "
            "Use the journey ID with ios_run_test's journey_name parameter."
        ),
    )
    async def ios_list_journeys(directory: str | None = None) -> str:
        """List configured journeys.

        Args:
            directory: Working directory to find .specterqa/ project.

        Returns:
            JSON array of journey dicts with id, name, tags, and step_count.
        """
        project_dir = _resolve_project_dir(directory)
        journeys_dir = project_dir / "journeys"

        if not journeys_dir.is_dir():
            return json.dumps({
                "error": f"Journeys directory not found: {journeys_dir}",
                "error_code": "PROJECT_NOT_INITIALIZED",
            })

        results: list[dict[str, Any]] = []

        for journey_file in sorted(journeys_dir.glob("*.yaml")):
            raw = _load_yaml(journey_file)
            cfg = raw.get("scenario", raw)
            journey_id = cfg.get("id", journey_file.stem)
            results.append({
                "id": journey_id,
                "name": cfg.get("name", journey_id),
                "tags": cfg.get("tags", []),
                "step_count": len(cfg.get("steps", [])),
                "description": cfg.get("description", ""),
            })

        return json.dumps(results, indent=2)

    return mcp


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def serve() -> None:
    """Start the SpecterQA iOS MCP server on stdio transport.

    This is the entry point for:
      - the ``specterqa-ios-mcp`` console script
      - ``python -m specterqa.ios.mcp``
      - ``specterqa ios serve``
    """
    logging.basicConfig(
        level=logging.WARNING,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    server = create_server()
    server.run(transport="stdio")


if __name__ == "__main__":
    serve()
