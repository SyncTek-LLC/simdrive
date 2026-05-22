"""`simdrive demo` — 30-second onboarding sanity check.

A brand-new user finishes ``pip install simdrive`` and immediately wants
proof the install works without learning MCP wiring, bundle ids, or
session lifecycle. This module is that 30-second proof:

  1. Verify Xcode + simctl are present (reuses :mod:`diagnostics` shape).
  2. Locate an iPhone simulator (prefers iPhone 17, falls back to 16 Pro,
     then any available iPhone).
  3. Boot the device if not already booted.
  4. Launch the always-present ``com.apple.Preferences`` (Settings) so we
     do not depend on the user having any app installed.
  5. Run a single :func:`observe.observe` pass to populate marks.
  6. Print a friendly, plain-text, ANSI-coloured summary (no emoji,
     brand accent only — ``#22D3EE`` cyan).

The function returns an exit code so the CLI dispatcher can ``sys.exit()``
on it; tests import :func:`run_demo` directly with a fake ``argparse.Namespace``.

Exit codes:
    0   success
    2   environment failure (no Xcode, no iPhone device, license gate)
"""
from __future__ import annotations

import argparse
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

# ANSI escape codes — only accent colour is brand cyan (#22D3EE).
_CYAN = "\033[36m"
_GREEN = "\033[32m"
_DIM = "\033[2m"
_RESET = "\033[0m"


# ---------------------------------------------------------------------------
# Preferred-device ordering — newest first, then sensible fallbacks.
# ---------------------------------------------------------------------------

_PREFERRED_DEVICE_NAMES: tuple[str, ...] = (
    "iPhone 17 Pro Max",
    "iPhone 17 Pro",
    "iPhone 17",
    "iPhone 16 Pro Max",
    "iPhone 16 Pro",
    "iPhone 16",
    "iPhone 15 Pro Max",
    "iPhone 15 Pro",
    "iPhone 15",
)


@dataclass
class _DemoTimings:
    """Per-step wall-clock timings, in seconds, for the summary line."""

    boot_seconds: float = 0.0
    launch_seconds: float = 0.0
    observe_seconds: float = 0.0
    total_seconds: float = 0.0
    boot_was_skipped: bool = False


# ---------------------------------------------------------------------------
# Helpers — kept module-private; the public surface is :func:`run_demo`.
# ---------------------------------------------------------------------------


def _pick_iphone_device(devices):
    """Return the best iPhone device from a list of :class:`sim.Device`.

    Preference order:
      1. An already-booted iPhone (any model).
      2. The first match against :data:`_PREFERRED_DEVICE_NAMES` (in order).
      3. Any device whose name starts with "iPhone ".
      4. ``None`` if no iPhone was found.
    """
    iphones = [d for d in devices if d.name.startswith("iPhone")]
    if not iphones:
        return None
    # 1. Already-booted iPhone wins — zero boot cost.
    for d in iphones:
        if d.is_booted:
            return d
    # 2. Walk the preferred-name list.
    by_name = {d.name: d for d in iphones}
    for name in _PREFERRED_DEVICE_NAMES:
        if name in by_name:
            return by_name[name]
    # 3. Fall back to the first available iPhone.
    return iphones[0]


def _band_counts(marks) -> tuple[int, int, int]:
    """Count marks per confidence band. Returns ``(high, medium, low)``."""
    high = medium = low = 0
    for m in marks:
        band = getattr(m, "confidence_band", "low")
        if band == "high":
            high += 1
        elif band == "medium":
            medium += 1
        else:
            low += 1
    return high, medium, low


def _resolve_out_dir(override: Optional[Path] = None) -> Path:
    """Resolve the screenshot output directory.

    Defaults to ``~/.simdrive/sessions/demo-<date>``. Override is used by
    tests so we never write into the developer's real home directory.
    """
    if override is not None:
        return override
    date = time.strftime("%Y-%m-%d")
    return Path.home() / ".simdrive" / "sessions" / f"demo-{date}"


def _print_section_header(stream) -> None:
    stream.write(f"{_CYAN}SimDrive demo{_RESET} {_DIM}— quick sanity check{_RESET}\n\n")


def _print_summary(
    stream,
    device_name: str,
    bundle_id: str,
    n_marks: int,
    band_counts: tuple[int, int, int],
    screenshot_path: Path,
    timings: _DemoTimings,
) -> None:
    """Render the structured summary section. Plain text + ANSI accents."""
    high, medium, low = band_counts
    boot_note = (
        "already booted"
        if timings.boot_was_skipped
        else f"booted in {timings.boot_seconds:.1f}s"
    )
    stream.write(f"  device      {device_name} ({boot_note})\n")
    stream.write(f"  app         {bundle_id} (launched in {timings.launch_seconds:.1f}s)\n")
    stream.write(
        f"  observed    {n_marks} elements "
        f"({high} high-confidence, {medium} medium, {low} low)\n"
    )
    stream.write(f"  screenshot  {screenshot_path}\n\n")
    stream.write(f"  {_DIM}Try the full flow:{_RESET}\n")
    stream.write(
        '    1. Add to .mcp.json: '
        '{ "mcpServers": { "simdrive": { "command": "simdrive" } } }\n'
    )
    stream.write(
        '    2. Tell your agent: "Open SimDrive, observe the screen, tap a setting"\n\n'
    )
    stream.write(f"{_GREEN}Total: {timings.total_seconds:.1f}s{_RESET}\n")


# ---------------------------------------------------------------------------
# Public entry point.
# ---------------------------------------------------------------------------


def run_demo(args: argparse.Namespace) -> int:
    """Boot iPhone sim, open Settings, observe, print summary. Returns exit code.

    The CLI dispatcher (in ``server.py``) builds the argparse Namespace and
    calls this function; the only argument it currently respects is
    ``args.out_dir`` (test-only override of the screenshot location).
    """
    # Lazy imports keep `simdrive --help` snappy and avoid pulling Pillow /
    # nacl for users who never touch the demo path.
    from simdrive import observe as observe_mod
    from simdrive import sim
    from simdrive.license.entitlement import check_entitlement
    from simdrive.license.errors import LicenseError

    stdout = getattr(args, "_stdout", sys.stdout)
    stderr = getattr(args, "_stderr", sys.stderr)
    out_dir_override = getattr(args, "out_dir", None)

    t0 = time.time()
    _print_section_header(stdout)

    # 1. License gate. Re-uses the same recovery message the rest of the
    # CLI surfaces so users get the canonical trial/auth/pricing hints.
    try:
        check_entitlement()
    except LicenseError as exc:
        stderr.write(f"Error: {exc.message}\n")
        return 2

    # 2. Xcode + simctl readiness — sim.list_devices() shells out to
    # `xcrun simctl`; if Xcode is missing this raises SimError.
    try:
        devices = sim.list_devices()
    except sim.SimError as exc:
        stderr.write(
            f"Error: {exc}\n"
            "Xcode + iOS simulator runtime required. "
            "Run `xcode-select --install` and then install an iOS runtime "
            "from Xcode > Settings > Components.\n"
        )
        return 2
    except FileNotFoundError:
        # `xcrun` itself missing — Xcode CLT not installed.
        stderr.write(
            "Error: xcrun not found. Xcode + iOS simulator runtime required. "
            "Run `xcode-select --install` and then install an iOS runtime "
            "from Xcode > Settings > Components.\n"
        )
        return 2

    # 3. Device selection.
    device = _pick_iphone_device(devices)
    if device is None:
        stderr.write(
            "Error: No iPhone simulator found. "
            "Create one in Xcode (Window > Devices and Simulators) and re-run.\n"
        )
        return 2

    # 4. Boot (idempotent — if already booted, `sim.boot` is a no-op
    # because `simctl bootstatus -b` short-circuits).
    boot_start = time.time()
    boot_was_skipped = device.is_booted
    if not boot_was_skipped:
        try:
            sim.boot(device.udid)
        except sim.SimError as exc:
            stderr.write(f"Error: failed to boot {device.name}: {exc}\n")
            return 2
    boot_seconds = time.time() - boot_start

    # 5. Launch Settings — always installed, no setup required from user.
    bundle_id = "com.apple.Preferences"
    launch_start = time.time()
    try:
        sim.launch_app(device.udid, bundle_id)
    except sim.SimError as exc:
        stderr.write(f"Error: failed to launch {bundle_id}: {exc}\n")
        return 2
    launch_seconds = time.time() - launch_start

    # 6. Single observe pass. annotate=False — the demo doesn't need the
    # SoM-annotated PNG, just the screenshot + mark count.
    out_dir = _resolve_out_dir(out_dir_override)
    observe_start = time.time()
    try:
        obs = observe_mod.observe(
            udid=device.udid,
            out_dir=out_dir,
            annotate=True,  # marks count is the headline number; need them
            capture_logs=False,
            target="simulator",
        )
    except Exception as exc:  # noqa: BLE001 — surface anything as a friendly error
        stderr.write(f"Error: observe failed: {exc}\n")
        return 2
    observe_seconds = time.time() - observe_start

    timings = _DemoTimings(
        boot_seconds=boot_seconds,
        launch_seconds=launch_seconds,
        observe_seconds=observe_seconds,
        total_seconds=time.time() - t0,
        boot_was_skipped=boot_was_skipped,
    )

    _print_summary(
        stream=stdout,
        device_name=device.name,
        bundle_id=bundle_id,
        n_marks=len(obs.marks),
        band_counts=_band_counts(obs.marks),
        screenshot_path=obs.screenshot_path,
        timings=timings,
    )
    return 0
