"""specterqa init --platform ios — scaffold an iOS testing project.

Creates or extends a .specterqa/ project directory with iOS-specific
template files:
  - .specterqa/products/<app>.yaml       iOS product config template
  - .specterqa/personas/ios-tester.yaml  iOS test persona template
  - .specterqa/journeys/smoke-test.yaml  iOS smoke test journey template
  - .specterqa/evidence/                 evidence output directory

This module is called by the main specterqa CLI when the user passes
``--platform ios`` to ``specterqa init``.  It can also be invoked directly
from the iOS CLI group via ``specterqa ios init``.
"""

from __future__ import annotations

import os
from pathlib import Path

import click
from rich.console import Console
from rich.panel import Panel
from rich.tree import Tree

console = Console()


# ---------------------------------------------------------------------------
# Template content
# ---------------------------------------------------------------------------

_IOS_PRODUCT_TEMPLATE = """\
product:
  name: {slug}
  display_name: "{display_name}"
  platform: ios

  # iOS-specific fields
  bundle_id: "com.example.{slug}"          # replace with your app's bundle identifier
  device_name: "iPhone 15"                  # preferred simulator model
  ios_version: "17"                         # minimum iOS version

  # Optional: path to .app bundle (set at runtime or via --app flag)
  # app_path: "./build/Debug-iphonesimulator/{display_name}.app"

  # Screenshot capture settings
  screenshot_resize_width: 1024

  # Cost limits
  cost_limits:
    per_run_usd: 5.00
    per_step_usd: 1.00

  # Journeys associated with this product
  journeys:
    - smoke-test
"""

_IOS_PERSONA_TEMPLATE = """\
persona:
  name: ios_tester
  display_name: "iOS Tester"
  role: "QA Engineer — iOS"
  age: 28
  tech_comfort: high
  patience: medium
  preferred_device: iphone

  goals:
    - "Verify the app launches and core flows work on iOS simulator"
    - "Detect crashes, hangs, and broken UI states"
    - "Ensure accessibility and readability on iPhone screen size"

  frustrations:
    - "App crashes without a useful error message"
    - "UI elements that are too small to tap"
    - "Slow navigation or loading spinners that never resolve"

  # SECURITY: Use environment variable references for credentials.
  # Never store real credentials as literal values in persona YAML files.
  # Set TEST_EMAIL and TEST_PASSWORD in your environment before running.
  credentials:
    email: "${{TEST_EMAIL}}"
    password: "${{TEST_PASSWORD}}"
"""

_IOS_SMOKE_JOURNEY_TEMPLATE = """\
scenario:
  id: smoke-test
  name: "iOS Smoke Test"
  description: "Verify the app launches and the home screen renders correctly."
  tags: [smoke, ios, critical_path]

  personas:
    - ref: ios_tester
      role: primary

  steps:
    - id: launch_and_verify_home
      description: "Launch app and verify home screen loads"
      goal: "Confirm the app has launched and the main home screen is visible with no crash dialogs"
      checkpoint: home_screen_visible
      max_iterations: 15

    - id: core_navigation
      description: "Navigate through primary tabs or menu items"
      goal: "Tap the main navigation tabs and verify each section loads without errors"
      checkpoint: navigation_complete
      max_iterations: 20

    - id: check_no_errors
      description: "Verify no error states or crash dialogs are present"
      goal: "Confirm the app is in a healthy state — no error alerts, no crash dialogs, no blank screens"
      checkpoint: no_errors_detected
      max_iterations: 10
"""


# ---------------------------------------------------------------------------
# Public entry-point
# ---------------------------------------------------------------------------


def scaffold_ios_project(
    project_dir: Path,
    app_slug: str = "my-ios-app",
    display_name: str = "My iOS App",
    force: bool = False,
) -> None:
    """Create iOS-specific scaffold files under *project_dir*.

    Called by ``specterqa init --platform ios`` and ``specterqa ios init``.

    Args:
        project_dir: Path to the ``.specterqa/`` directory (must already exist
            or will be created).
        app_slug: Short identifier used for file names and YAML keys.
        display_name: Human-readable app name embedded in templates.
        force: If True, overwrite existing files.
    """
    # Ensure base directories exist
    subdirs = ["products", "personas", "journeys", "evidence"]
    for sub in subdirs:
        (project_dir / sub).mkdir(parents=True, exist_ok=True)

    files_written: list[Path] = []
    files_skipped: list[Path] = []

    def _write(path: Path, content: str) -> None:
        if path.exists() and not force:
            files_skipped.append(path)
            return
        path.write_text(content, encoding="utf-8")
        files_written.append(path)

    # Product config
    product_yaml = project_dir / "products" / f"{app_slug}.yaml"
    _write(
        product_yaml,
        _IOS_PRODUCT_TEMPLATE.format(slug=app_slug, display_name=display_name),
    )

    # Persona config
    persona_yaml = project_dir / "personas" / "ios-tester.yaml"
    _write(persona_yaml, _IOS_PERSONA_TEMPLATE)

    # Journey config
    journey_yaml = project_dir / "journeys" / "smoke-test.yaml"
    _write(journey_yaml, _IOS_SMOKE_JOURNEY_TEMPLATE)

    # .gitignore — protect persona files
    parent_dir = project_dir.parent
    gitignore_path = parent_dir / ".gitignore"
    _personas_gitignore_entry = ".specterqa/personas/"
    if gitignore_path.exists():
        existing = gitignore_path.read_text(encoding="utf-8")
        if _personas_gitignore_entry not in existing:
            gitignore_path.write_text(
                existing.rstrip("\n")
                + f"\n\n# SpecterQA — persona files may contain credential references\n{_personas_gitignore_entry}\n",
                encoding="utf-8",
            )
    else:
        gitignore_path.write_text(
            f"# SpecterQA — persona files may contain credential references\n{_personas_gitignore_entry}\n",
            encoding="utf-8",
        )

    # --- Rich output ---
    tree = Tree(f"[bold green]{project_dir}[/bold green]", guide_style="dim")
    for sub in subdirs:
        branch = tree.add(f"[blue]{sub}/[/blue]")
        for child in sorted((project_dir / sub).iterdir()):
            if child.is_file():
                style = "bold" if child in files_written else "dim"
                branch.add(f"[{style}]{child.name}[/{style}]")

    console.print()
    console.print(
        Panel(
            tree,
            title="[bold green]SpecterQA iOS Project Initialized[/bold green]",
            border_style="green",
        )
    )

    if files_skipped:
        console.print(
            f"[dim]Skipped {len(files_skipped)} existing file(s). Use --force to overwrite.[/dim]"
        )

    api_key_set = bool(os.environ.get("ANTHROPIC_API_KEY"))

    console.print()
    console.print("[bold]Next steps:[/bold]")
    console.print(f"  1. Edit [cyan].specterqa/products/{app_slug}.yaml[/cyan] — set your bundle_id and app_path")
    console.print("  2. Optionally customize the persona in [cyan].specterqa/personas/ios-tester.yaml[/cyan]")
    console.print("  3. Edit the journey in [cyan].specterqa/journeys/smoke-test.yaml[/cyan]")
    console.print("  4. Boot a simulator:  [bold]specterqa ios boot[/bold]")
    console.print(f"  5. Run:  [bold]specterqa ios run --product {app_slug} --journey smoke-test[/bold]")

    if not api_key_set:
        console.print()
        console.print(
            Panel(
                "[bold yellow]Set your API key before running tests:[/bold yellow]\n\n"
                "  export ANTHROPIC_API_KEY=sk-ant-...\n\n"
                "Get a key at: https://console.anthropic.com/",
                title="[yellow]API Key Required[/yellow]",
                border_style="yellow",
            )
        )
    else:
        console.print("  [green]ANTHROPIC_API_KEY already set[/green]")

    console.print()
