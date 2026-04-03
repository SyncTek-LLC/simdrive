"""Tests for ProjectInjector — project-injection runner build (INIT-2026-509).

All tests use stdlib mocking only — no Xcode, no simulator required.
"""

from __future__ import annotations

import subprocess
import textwrap
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

from specterqa.ios.project_injector import ProjectInjector, ProjectInjectorError


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_injector(
    project_path: str = "/fake/MyApp.xcodeproj",
    scheme: str = "MyApp",
    runner_sources: Path | None = None,
) -> ProjectInjector:
    """Build a ProjectInjector with _find_runner_sources mocked out."""
    if runner_sources is None:
        # Default: a tmp path that "looks" like a valid sources dir
        runner_sources = Path("/fake/runner/Sources")

    with patch.object(ProjectInjector, "_find_runner_sources", return_value=runner_sources):
        return ProjectInjector(project_path=project_path, scheme=scheme)


_SAMPLE_BUILD_SETTINGS_OUTPUT = textwrap.dedent("""\
    Build settings for action build and target "MyApp":
        CODE_SIGN_IDENTITY = Apple Development
        DEVELOPMENT_TEAM = ABCDEF1234
        CODE_SIGNING_REQUIRED = YES
        IPHONEOS_DEPLOYMENT_TARGET = 16.0
        PRODUCT_BUNDLE_IDENTIFIER = com.example.myapp
        SDKROOT = iphonesimulator
""")


def _mock_show_build_settings(returncode: int = 0, stdout: str = _SAMPLE_BUILD_SETTINGS_OUTPUT) -> MagicMock:
    proc = MagicMock()
    proc.returncode = returncode
    proc.stdout = stdout
    proc.stderr = ""
    return proc


# ---------------------------------------------------------------------------
# _read_build_settings
# ---------------------------------------------------------------------------


class TestReadBuildSettings:
    def test_read_build_settings_parses_output(self):
        """_read_build_settings returns a dict of key→value pairs."""
        injector = _make_injector()
        with patch("subprocess.run", return_value=_mock_show_build_settings()) as mock_run:
            settings = injector._read_build_settings()

        assert settings["CODE_SIGN_IDENTITY"] == "Apple Development"
        assert settings["DEVELOPMENT_TEAM"] == "ABCDEF1234"
        assert settings["IPHONEOS_DEPLOYMENT_TARGET"] == "16.0"
        assert settings["PRODUCT_BUNDLE_IDENTIFIER"] == "com.example.myapp"

    def test_read_build_settings_raises_on_nonzero_exit(self):
        """Raises ProjectInjectorError when xcodebuild exits non-zero."""
        injector = _make_injector()
        failing = _mock_show_build_settings(returncode=1)
        failing.stderr = "error: scheme not found"
        with patch("subprocess.run", return_value=failing):
            with pytest.raises(ProjectInjectorError, match="showBuildSettings failed"):
                injector._read_build_settings()

    def test_read_build_settings_calls_correct_command(self):
        """xcodebuild -showBuildSettings is called with the correct arguments."""
        injector = _make_injector(project_path="/my/App.xcodeproj", scheme="AppScheme")
        with patch("subprocess.run", return_value=_mock_show_build_settings()) as mock_run:
            injector._read_build_settings()

        cmd = mock_run.call_args[0][0]
        assert "xcodebuild" in cmd
        assert "-showBuildSettings" in cmd
        assert "-project" in cmd
        assert "/my/App.xcodeproj" in cmd
        assert "-scheme" in cmd
        assert "AppScheme" in cmd

    def test_read_build_settings_ignores_non_kv_lines(self):
        """Lines without ' = ' are silently skipped (header lines, blank lines)."""
        injector = _make_injector()
        output = "Build settings for action build:\n    KEY = value\n    BLANK_LINE\n"
        with patch("subprocess.run", return_value=_mock_show_build_settings(stdout=output)):
            settings = injector._read_build_settings()
        assert "KEY" in settings
        assert "BLANK_LINE" not in settings


# ---------------------------------------------------------------------------
# _generate_xcconfig
# ---------------------------------------------------------------------------


class TestGenerateXcconfig:
    def test_generate_xcconfig_inherits_signing(self):
        """Generated xcconfig contains signing settings from the user's project."""
        injector = _make_injector()
        settings = {
            "CODE_SIGN_IDENTITY": "Apple Development",
            "DEVELOPMENT_TEAM": "TEAM123",
            "CODE_SIGNING_REQUIRED": "YES",
            "IPHONEOS_DEPLOYMENT_TARGET": "16.0",
        }
        config = injector._generate_xcconfig(settings)

        assert "CODE_SIGN_IDENTITY = Apple Development" in config
        assert "DEVELOPMENT_TEAM = TEAM123" in config
        assert "CODE_SIGNING_REQUIRED = YES" in config
        assert "IPHONEOS_DEPLOYMENT_TARGET = 16.0" in config

    def test_generate_xcconfig_no_hardcoded_dash_when_identity_present(self):
        """When CODE_SIGN_IDENTITY is set in the project, it is used (not '-')."""
        injector = _make_injector()
        config = injector._generate_xcconfig({"CODE_SIGN_IDENTITY": "iPhone Developer"})
        assert "CODE_SIGN_IDENTITY = iPhone Developer" in config

    def test_generate_xcconfig_falls_back_to_dash_when_identity_missing(self):
        """When CODE_SIGN_IDENTITY is not in settings, '-' is the default."""
        injector = _make_injector()
        config = injector._generate_xcconfig({})
        assert "CODE_SIGN_IDENTITY = -" in config

    def test_generate_xcconfig_deployment_target_default(self):
        """Default deployment target is 15.0 when not present in settings."""
        injector = _make_injector()
        config = injector._generate_xcconfig({})
        assert "IPHONEOS_DEPLOYMENT_TARGET = 15.0" in config

    def test_generate_xcconfig_empty_team_when_missing(self):
        """Missing DEVELOPMENT_TEAM produces an empty value (not 'None')."""
        injector = _make_injector()
        config = injector._generate_xcconfig({})
        # Should be 'DEVELOPMENT_TEAM = ' with an empty (or blank) value
        assert "DEVELOPMENT_TEAM = " in config
        assert "None" not in config


# ---------------------------------------------------------------------------
# _find_runner_sources
# ---------------------------------------------------------------------------


class TestFindRunnerSources:
    def _make_bare_injector(self, tmp_path: Path) -> ProjectInjector:
        """Return an injector instance with __file__ pointing into tmp_path.

        This ensures the package-relative candidate (Path(__file__).parent.parent.parent.parent
        / "runner" / "Sources") resolves inside tmp_path so we control whether
        SpecterQARunner.swift exists there or not.
        """
        injector = ProjectInjector.__new__(ProjectInjector)
        injector.project_path = Path("/fake/App.xcodeproj")
        injector.scheme = "App"
        injector.destination = "generic/platform=iOS Simulator"
        return injector

    def test_find_runner_sources_raises_when_missing(self, tmp_path):
        """FileNotFoundError when no candidate directory has SpecterQARunner.swift."""
        injector = self._make_bare_injector(tmp_path)

        # Provide a fake __file__ whose parent^4/runner/Sources does NOT contain the swift file
        fake_file = tmp_path / "src" / "specterqa" / "ios" / "project_injector.py"
        fake_file.parent.mkdir(parents=True, exist_ok=True)
        fake_file.touch()

        with (
            patch.dict("os.environ", {}, clear=True),
            patch("specterqa.ios.project_injector.Path.home", return_value=tmp_path),
            patch("specterqa.ios.project_injector.__file__", str(fake_file)),
        ):
            with pytest.raises(FileNotFoundError, match="Runner Swift sources not found"):
                injector._find_runner_sources()

    def test_find_runner_sources_respects_env_override(self, tmp_path):
        """SPECTERQA_RUNNER_SOURCES env var is checked first."""
        sources_dir = tmp_path / "my-runner-sources"
        sources_dir.mkdir()
        (sources_dir / "SpecterQARunner.swift").touch()

        injector = self._make_bare_injector(tmp_path)

        with patch.dict("os.environ", {"SPECTERQA_RUNNER_SOURCES": str(sources_dir)}):
            result = injector._find_runner_sources()

        assert result == sources_dir

    def test_find_runner_sources_finds_home_candidate(self, tmp_path):
        """~/.specterqa/runner-sources is a valid fallback location."""
        sources_dir = tmp_path / ".specterqa" / "runner-sources"
        sources_dir.mkdir(parents=True)
        (sources_dir / "SpecterQARunner.swift").touch()

        injector = self._make_bare_injector(tmp_path)

        # fake __file__ pointing into a location where the relative candidate does NOT exist
        fake_file = tmp_path / "nowhere" / "src" / "specterqa" / "ios" / "project_injector.py"
        fake_file.parent.mkdir(parents=True, exist_ok=True)
        fake_file.touch()

        with (
            patch.dict("os.environ", {}, clear=True),
            patch("specterqa.ios.project_injector.Path.home", return_value=tmp_path),
            patch("specterqa.ios.project_injector.__file__", str(fake_file)),
        ):
            result = injector._find_runner_sources()

        assert result == sources_dir


# ---------------------------------------------------------------------------
# _find_xctestrun
# ---------------------------------------------------------------------------


class TestFindXctestrun:
    def test_find_xctestrun_locates_file(self, tmp_path):
        """Returns path to the first .xctestrun found under derived_data."""
        derived = tmp_path / "DerivedData" / "Build" / "Products" / "Debug-iphonesimulator"
        derived.mkdir(parents=True)
        xctestrun = derived / "MyApp.xctestrun"
        xctestrun.touch()

        injector = _make_injector()
        result = injector._find_xctestrun(tmp_path / "DerivedData")
        assert result == xctestrun

    def test_find_xctestrun_raises_when_missing(self, tmp_path):
        """FileNotFoundError when no .xctestrun exists under the given path."""
        injector = _make_injector()
        with pytest.raises(FileNotFoundError, match=r"\.xctestrun"):
            injector._find_xctestrun(tmp_path)


# ---------------------------------------------------------------------------
# build()
# ---------------------------------------------------------------------------


class TestBuild:
    def _make_successful_build_proc(self) -> MagicMock:
        proc = MagicMock()
        proc.returncode = 0
        proc.stdout = ""
        proc.stderr = ""
        return proc

    def test_build_calls_xcodebuild_with_project(self, tmp_path):
        """build() invokes xcodebuild with -project pointing at the user's .xcodeproj."""
        injector = _make_injector(project_path="/user/MyApp.xcodeproj", scheme="MyApp")
        fake_xctestrun = tmp_path / "test.xctestrun"
        fake_xctestrun.touch()

        with (
            patch("subprocess.run") as mock_run,
            patch.object(injector, "_read_build_settings", return_value={
                "PRODUCT_BUNDLE_IDENTIFIER": "com.example.app",
                "CODE_SIGN_IDENTITY": "-",
                "DEVELOPMENT_TEAM": "",
                "CODE_SIGNING_REQUIRED": "NO",
                "IPHONEOS_DEPLOYMENT_TARGET": "16.0",
            }),
            patch.object(injector, "_find_xctestrun", return_value=fake_xctestrun),
            patch("pathlib.Path.mkdir"),
            patch("pathlib.Path.write_text"),
        ):
            mock_run.return_value = self._make_successful_build_proc()
            result = injector.build()

        cmd = mock_run.call_args[0][0]
        assert "-project" in cmd
        assert "/user/MyApp.xcodeproj" in cmd
        assert result == fake_xctestrun

    def test_build_passes_xcconfig(self, tmp_path):
        """build() passes -xcconfig to xcodebuild with the generated config file."""
        injector = _make_injector()
        fake_xctestrun = tmp_path / "test.xctestrun"
        fake_xctestrun.touch()

        with (
            patch("subprocess.run") as mock_run,
            patch.object(injector, "_read_build_settings", return_value={
                "PRODUCT_BUNDLE_IDENTIFIER": "com.test.app",
            }),
            patch.object(injector, "_find_xctestrun", return_value=fake_xctestrun),
            patch("pathlib.Path.mkdir"),
            patch("pathlib.Path.write_text"),
        ):
            mock_run.return_value = self._make_successful_build_proc()
            injector.build()

        cmd = mock_run.call_args[0][0]
        assert "-xcconfig" in cmd

    def test_build_raises_on_xcodebuild_failure(self):
        """build() raises ProjectInjectorError when xcodebuild exits non-zero."""
        injector = _make_injector()
        failing_proc = MagicMock()
        failing_proc.returncode = 1
        failing_proc.stderr = "FAILED: code signing error"
        failing_proc.stdout = ""

        with (
            patch("subprocess.run", return_value=failing_proc),
            patch.object(injector, "_read_build_settings", return_value={
                "PRODUCT_BUNDLE_IDENTIFIER": "com.test.app",
            }),
            patch("pathlib.Path.mkdir"),
            patch("pathlib.Path.write_text"),
        ):
            with pytest.raises(ProjectInjectorError, match="build failed"):
                injector.build()

    def test_build_uses_bundle_id_in_output_path(self, tmp_path):
        """build() creates the output directory under ~/.specterqa/runner-build/<bundle_id>/."""
        injector = _make_injector()
        fake_xctestrun = tmp_path / "test.xctestrun"
        fake_xctestrun.touch()

        created_dirs: list[Path] = []

        with (
            patch("subprocess.run") as mock_run,
            patch.object(injector, "_read_build_settings", return_value={
                "PRODUCT_BUNDLE_IDENTIFIER": "com.my.special.app",
            }),
            patch.object(injector, "_find_xctestrun", return_value=fake_xctestrun),
            patch("pathlib.Path.write_text"),
        ):
            mock_run.return_value = self._make_successful_build_proc()
            # Capture mkdir calls to verify bundle_id is used in path
            original_mkdir = Path.mkdir

            def tracking_mkdir(self, **kwargs):
                created_dirs.append(self)

            with patch.object(Path, "mkdir", tracking_mkdir):
                injector.build()

        assert any("com.my.special.app" in str(d) for d in created_dirs), (
            f"Expected 'com.my.special.app' in one of {created_dirs}"
        )

    def test_build_verbose_prints_output(self, capsys, tmp_path):
        """build(verbose=True) prints progress lines without raising."""
        injector = _make_injector()
        fake_xctestrun = tmp_path / "test.xctestrun"
        fake_xctestrun.touch()

        with (
            patch("subprocess.run") as mock_run,
            patch.object(injector, "_read_build_settings", return_value={
                "PRODUCT_BUNDLE_IDENTIFIER": "com.test.app",
                "CODE_SIGN_IDENTITY": "-",
            }),
            patch.object(injector, "_find_xctestrun", return_value=fake_xctestrun),
            patch("pathlib.Path.mkdir"),
            patch("pathlib.Path.write_text"),
        ):
            mock_run.return_value = self._make_successful_build_proc()
            injector.build(verbose=True)

        captured = capsys.readouterr()
        assert "specterqa" in captured.out.lower()
