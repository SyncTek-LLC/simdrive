"""Build the SpecterQA XCTest runner against a user's Xcode project.

Instead of a standalone .xcodeproj with hardcoded signing, this module:
1. Reads the user's project build settings
2. Compiles our Swift runner sources using THEIR signing/SDK/deployment target
3. Generates a .xctestrun plist pairing their app with our test bundle
4. Stores the result in ~/.specterqa/runner-build/<bundle_id>/

INIT-2026-506 — SpecterQA iOS v3 project-injection runner build.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path


class ProjectInjectorError(Exception):
    """Raised when the project injection build fails."""


class ProjectInjector:
    """Build the XCTest runner against a user's Xcode project.

    Reads the user's project build settings (signing identity, deployment
    target, team ID) and compiles our Swift runner sources under those
    settings so the resulting .xctest bundle can be installed on their
    simulator without signing conflicts.

    Args:
        project_path: Path to the user's .xcodeproj file.
        scheme: Xcode scheme name to read build settings from.
        destination: xcodebuild destination string (default: generic iOS Simulator).
    """

    def __init__(
        self,
        project_path: str,
        scheme: str,
        destination: str = "generic/platform=iOS Simulator",
    ) -> None:
        self.project_path = Path(project_path)
        self.scheme = scheme
        self.destination = destination
        self._runner_sources = self._find_runner_sources()

    # ------------------------------------------------------------------
    # Source location
    # ------------------------------------------------------------------

    def _find_runner_sources(self) -> Path:
        """Locate our Swift runner source files.

        Checks:
        1. Relative to the installed specterqa package (development layout).
        2. ``~/.specterqa/runner-sources`` (user-installed layout).
        3. ``SPECTERQA_RUNNER_SOURCES`` environment variable override.

        Returns:
            Path to the directory containing ``SpecterQARunner.swift``.

        Raises:
            FileNotFoundError: When the sources cannot be located.
        """
        env_override = os.environ.get("SPECTERQA_RUNNER_SOURCES")
        candidates: list[Path] = []
        if env_override:
            candidates.append(Path(env_override))

        # Package-relative: src/specterqa/ios → repo root → runner/Sources
        try:
            candidates.append(Path(__file__).parent.parent.parent.parent / "runner" / "Sources")
        except Exception:
            pass

        candidates.append(Path.home() / ".specterqa" / "runner-sources")

        for p in candidates:
            if p.is_dir() and (p / "SpecterQARunner.swift").exists():
                return p

        raise FileNotFoundError(
            "Runner Swift sources not found. "
            "Reinstall specterqa-ios or set SPECTERQA_RUNNER_SOURCES env var "
            "to the directory containing SpecterQARunner.swift."
        )

    # ------------------------------------------------------------------
    # Build settings
    # ------------------------------------------------------------------

    def _read_build_settings(self) -> dict[str, str]:
        """Read build settings from the user's Xcode project.

        Runs ``xcodebuild -showBuildSettings`` and parses the key=value output
        into a dict.

        Returns:
            Dict of build setting name → value strings.

        Raises:
            ProjectInjectorError: When xcodebuild exits non-zero.
        """
        result = subprocess.run(
            [
                "xcodebuild",
                "-showBuildSettings",
                "-project",
                str(self.project_path),
                "-scheme",
                self.scheme,
                "-sdk",
                "iphonesimulator",
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            raise ProjectInjectorError(
                f"xcodebuild -showBuildSettings failed (exit {result.returncode}):\n{result.stderr}"
            )

        settings: dict[str, str] = {}
        for line in result.stdout.splitlines():
            line = line.strip()
            if " = " in line:
                key, _, value = line.partition(" = ")
                settings[key.strip()] = value.strip()
        return settings

    # ------------------------------------------------------------------
    # xcconfig generation
    # ------------------------------------------------------------------

    def _generate_xcconfig(self, settings: dict[str, str]) -> str:
        """Generate an xcconfig file that inherits the user's signing settings.

        Only signing-relevant settings are propagated so we don't accidentally
        override unrelated build options in our test bundle.

        Args:
            settings: Build settings dict from ``_read_build_settings()``.

        Returns:
            xcconfig file contents as a string.
        """
        lines: list[str] = [
            "// Auto-generated by specterqa-ios ProjectInjector — do not edit.",
            f"CODE_SIGN_IDENTITY = {settings.get('CODE_SIGN_IDENTITY', '-')}",
            f"DEVELOPMENT_TEAM = {settings.get('DEVELOPMENT_TEAM', '')}",
            f"CODE_SIGNING_REQUIRED = {settings.get('CODE_SIGNING_REQUIRED', 'NO')}",
            f"IPHONEOS_DEPLOYMENT_TARGET = {settings.get('IPHONEOS_DEPLOYMENT_TARGET', '15.0')}",
            "SDKROOT = iphonesimulator",
            "SUPPORTED_PLATFORMS = iphonesimulator",
            "ARCHS = $(ARCHS_STANDARD)",
            "GENERATE_INFOPLIST_FILE = YES",
        ]
        return "\n".join(lines) + "\n"

    # ------------------------------------------------------------------
    # xctestrun discovery
    # ------------------------------------------------------------------

    def _find_xctestrun(self, derived_data: Path) -> Path:
        """Find the .xctestrun file produced by ``build-for-testing``.

        Performs a recursive search under *derived_data* so it works regardless
        of the exact sub-directory xcodebuild chooses.

        Args:
            derived_data: Derived data root passed to ``-derivedDataPath``.

        Returns:
            Path to the first .xctestrun found.

        Raises:
            FileNotFoundError: When no .xctestrun is present.
        """
        for f in derived_data.rglob("*.xctestrun"):
            return f
        raise FileNotFoundError(
            f"No .xctestrun file found under {derived_data}. "
            "The build may have failed or the scheme may not have a test target."
        )

    # ------------------------------------------------------------------
    # Public build entry point
    # ------------------------------------------------------------------

    def build(self, verbose: bool = False) -> Path:
        """Build the XCTest runner against the user's project.

        Steps:
        1. Read build settings to obtain signing identity, team, deployment target.
        2. Write an xcconfig propagating those settings to our test bundle.
        3. Run ``xcodebuild build-for-testing`` with the user's project.
        4. Locate and return the produced .xctestrun file.

        The output is stored under ``~/.specterqa/runner-build/<bundle_id>/``
        so each project gets its own isolated build cache.

        Args:
            verbose: When True, stream xcodebuild output to stdout and print
                progress lines.

        Returns:
            Path to the produced .xctestrun file.

        Raises:
            ProjectInjectorError: When any build step fails.
        """
        settings = self._read_build_settings()

        bundle_id = settings.get("PRODUCT_BUNDLE_IDENTIFIER", "unknown")
        build_dir = Path.home() / ".specterqa" / "runner-build" / bundle_id
        build_dir.mkdir(parents=True, exist_ok=True)

        # Build OUR runner project with their signing — never compile their code.
        runner_project = self._runner_sources.parent / "SpecterQARunner.xcodeproj"
        if not runner_project.is_dir():
            raise ProjectInjectorError(f"Runner Xcode project not found at {runner_project}. Reinstall specterqa-ios.")

        # Write xcconfig with user's signing settings.
        xcconfig = build_dir / "specterqa-runner.xcconfig"
        # Add GENERATE_INFOPLIST_FILE to xcconfig for Xcode 16 compat
        xcconfig_content = self._generate_xcconfig(settings)
        xcconfig_content += "GENERATE_INFOPLIST_FILE = YES\n"
        xcconfig.write_text(xcconfig_content)

        derived_data = build_dir / "DerivedData"

        cmd = [
            "xcodebuild",
            "build-for-testing",
            "-project",
            str(runner_project),
            "-scheme",
            "SpecterQARunner",
            "-sdk",
            "iphonesimulator",
            "-destination",
            self.destination,
            "-derivedDataPath",
            str(derived_data),
            "-xcconfig",
            str(xcconfig),
        ]

        if verbose:
            print(f"[specterqa] Building runner for {bundle_id}...")
            print(f"[specterqa] Using project: {runner_project}")
            print("[specterqa] Scheme:        SpecterQARunner")
            print(f"[specterqa] Command:       {' '.join(cmd)}")

        result = subprocess.run(
            cmd,
            capture_output=not verbose,
            text=True,
            timeout=300,
        )
        if result.returncode != 0:
            detail = result.stderr if not verbose else "see output above"
            raise ProjectInjectorError(f"Runner build failed (exit {result.returncode}): {detail}")

        xctestrun = self._find_xctestrun(derived_data)

        if verbose:
            print(f"[specterqa] Runner built: {xctestrun}")

        return xctestrun
