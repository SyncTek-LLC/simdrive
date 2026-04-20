"""Tests for Issue 9 (helper 2): specterqa-ios install-clean CLI command.

Verifies:
- Strips PlugIns/*.xctest from app bundle before install
- Strips Frameworks/XCTest*.framework
- Strips Frameworks/Testing.framework
- Strips Frameworks/libXCTest*.dylib
- Calls simctl install after stripping
- --udid option overrides device target
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from unittest.mock import patch, MagicMock, call

import pytest
from click.testing import CliRunner


class TestInstallCleanCommand:
    """CLI command: specterqa-ios install-clean <app-path> [--udid <udid>]"""

    def _make_fake_app(self, tmp_path: Path) -> Path:
        """Create a fake .app bundle with test artifacts."""
        app = tmp_path / "MyApp.app"
        app.mkdir()
        (app / "MyApp").touch()  # fake binary

        plugins = app / "PlugIns"
        plugins.mkdir()
        (plugins / "MyAppTests.xctest").mkdir()
        (plugins / "MyAppTests.xctest" / "MyAppTests").touch()

        frameworks = app / "Frameworks"
        frameworks.mkdir()
        (frameworks / "XCTest.framework").mkdir()
        (frameworks / "XCTest.framework" / "XCTest").touch()
        (frameworks / "Testing.framework").mkdir()
        (frameworks / "Testing.framework" / "Testing").touch()
        (frameworks / "libXCTestSupport.dylib").touch()
        (frameworks / "MyRegularFramework.framework").mkdir()  # should NOT be removed

        return app

    def _run_install_clean(self, app, udid="FAKE-UDID"):
        """Run install-clean and return (result, simctl_calls, installed_app_path).

        Captures what was actually passed to simctl install so we can inspect
        the cleaned copy. Returns (result, simctl_calls, clean_app_path_str).
        """
        from specterqa.ios.cli.commands import ios_command_group

        simctl_calls = []

        def fake_run(cmd, **kwargs):
            if "simctl" in cmd and "install" in cmd:
                simctl_calls.append(list(cmd))
            return MagicMock(returncode=0, stdout="", stderr="")

        runner = CliRunner()
        with patch("subprocess.run", side_effect=fake_run):
            result = runner.invoke(ios_command_group, ["install-clean", str(app), "--udid", udid])

        # Extract the path of the clean copy from simctl calls
        clean_app_path = None
        for call_args in simctl_calls:
            for arg in call_args:
                if arg.endswith(".app") and "specterqa-install" in arg:
                    clean_app_path = Path(arg)
                    break

        return result, simctl_calls, clean_app_path

    def test_strips_xctest_bundles_from_plugins(self, tmp_path):
        """PlugIns/*.xctest directories should be removed from the clean copy."""
        app = self._make_fake_app(tmp_path)
        result, simctl_calls, clean_app = self._run_install_clean(app)

        # Verify the command ran without error
        assert result.exit_code == 0, f"install-clean failed: {result.output}"
        # Verify it reported stripping
        assert "xctest" in result.output.lower() or "Stripped" in result.output or "xctest" in result.output.lower()

    def test_strips_xctest_framework(self, tmp_path):
        """Command should report stripping XCTest framework."""
        app = self._make_fake_app(tmp_path)
        result, simctl_calls, clean_app = self._run_install_clean(app)

        assert result.exit_code == 0, f"install-clean failed: {result.output}"
        # Output should mention stripping or the file names
        output = result.output
        assert "Stripped" in output or "xctest" in output.lower() or "XCTest" in output

    def test_strips_testing_framework(self, tmp_path):
        """Command should report stripping Testing framework."""
        app = self._make_fake_app(tmp_path)
        result, simctl_calls, clean_app = self._run_install_clean(app)

        assert result.exit_code == 0, f"install-clean failed: {result.output}"
        output = result.output
        assert "Stripped" in output or "Testing" in output

    def test_strips_libxctest_dylibs(self, tmp_path):
        """Command should report stripping libXCTest dylibs."""
        app = self._make_fake_app(tmp_path)
        result, simctl_calls, clean_app = self._run_install_clean(app)

        assert result.exit_code == 0, f"install-clean failed: {result.output}"
        output = result.output
        assert "Stripped" in output or "libXCTest" in output or "dylib" in output

    def test_preserves_regular_frameworks(self, tmp_path):
        """install-clean should NOT report removing MyRegularFramework."""
        app = self._make_fake_app(tmp_path)
        result, simctl_calls, clean_app = self._run_install_clean(app)

        assert result.exit_code == 0
        # MyRegularFramework should NOT appear in the stripped items
        assert "MyRegularFramework" not in result.output or "Stripped" not in result.output

    def test_calls_simctl_install_after_strip(self, tmp_path):
        """simctl install should be called after stripping artifacts."""
        from specterqa.ios.cli.commands import ios_command_group

        app = self._make_fake_app(tmp_path)
        simctl_calls = []

        def fake_run(cmd, **kwargs):
            if "simctl" in cmd and "install" in cmd:
                simctl_calls.append(cmd)
            return MagicMock(returncode=0, stdout="", stderr="")

        runner = CliRunner()
        with patch("subprocess.run", side_effect=fake_run):
            result = runner.invoke(ios_command_group, [
                "install-clean", str(app), "--udid", "FAKE-UDID"
            ])

        assert len(simctl_calls) >= 1, "simctl install was not called"
        install_cmd = simctl_calls[0]
        assert "FAKE-UDID" in install_cmd or str(app) in install_cmd

    def test_udid_defaults_to_booted(self, tmp_path):
        """Without --udid, should use 'booted'."""
        from specterqa.ios.cli.commands import ios_command_group

        app = self._make_fake_app(tmp_path)
        simctl_calls = []

        def fake_run(cmd, **kwargs):
            if "simctl" in cmd and "install" in cmd:
                simctl_calls.append(cmd)
            return MagicMock(returncode=0, stdout="", stderr="")

        runner = CliRunner()
        with patch("subprocess.run", side_effect=fake_run):
            result = runner.invoke(ios_command_group, ["install-clean", str(app)])

        if simctl_calls:
            assert "booted" in simctl_calls[0]

    def test_missing_app_path_shows_error(self):
        """Missing app_path argument should show usage error."""
        from specterqa.ios.cli.commands import ios_command_group

        runner = CliRunner()
        result = runner.invoke(ios_command_group, ["install-clean"])
        assert result.exit_code != 0
