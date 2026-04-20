"""Unit test — specterqa-ios --version flag.

Added in v14.0.2: ios_command_group exposes @click.version_option(package_name="specterqa-ios").
Asserts --version exits 0 and prints the canonical package version.
"""
from __future__ import annotations

import importlib.metadata

import pytest
from click.testing import CliRunner


def _get_canonical_version() -> str:
    """Return the installed specterqa-ios version from importlib.metadata."""
    try:
        return importlib.metadata.version("specterqa-ios")
    except importlib.metadata.PackageNotFoundError:
        return "unknown"


class TestCLIVersion:
    """--version flag on ios_command_group."""

    def test_version_exits_zero(self):
        """specterqa-ios --version must exit with code 0."""
        from specterqa.ios.cli.commands import ios_command_group

        runner = CliRunner()
        result = runner.invoke(ios_command_group, ["--version"])
        assert result.exit_code == 0, (
            f"--version exited {result.exit_code}: {result.output}"
        )

    def test_version_output_contains_package_name(self):
        """Output must contain a recognizable identifier (prog name or package)."""
        from specterqa.ios.cli.commands import ios_command_group

        runner = CliRunner()
        result = runner.invoke(ios_command_group, ["--version"])
        # Click formats as "<prog_name>, version X.Y.Z".  The group name is "ios"
        # (mounted as `specterqa ios`); package is specterqa-ios.  Accept either.
        output_lower = result.output.lower()
        assert any(s in output_lower for s in ("specterqa-ios", "specterqa", "ios")), (
            f"Expected package/prog identifier in version output: {result.output!r}"
        )

    def test_version_output_contains_version_number(self):
        """Output must contain the canonical version from importlib.metadata."""
        from specterqa.ios.cli.commands import ios_command_group

        canonical = _get_canonical_version()
        if canonical == "unknown":
            pytest.skip("specterqa-ios not installed via pip — cannot check version string")

        runner = CliRunner()
        result = runner.invoke(ios_command_group, ["--version"])
        assert canonical in result.output, (
            f"Expected version {canonical!r} in output: {result.output!r}"
        )
