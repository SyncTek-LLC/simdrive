"""Integration tests for CLI commands — real Click CliRunner invocations.

Extracted from test_runner_cli.py (TestExistingCLIUnbroken) and
test_v11_features.py (TestCIRunnerReuse, TestDoctorCommand, TestInitCommand,
TestValidateReplayCommand).

These tests exercise real command invocations via Click's CliRunner. All
mocked subprocess tests are excluded. The runner sub-command tests that use
subprocess.run mocks are also excluded — only pure CLI surface tests remain.

Run:
    pytest tests/integration/test_cli_commands.py -v --tb=short
"""

from __future__ import annotations

from pathlib import Path

import pytest

try:
    from click.testing import CliRunner
    from specterqa.ios.cli.commands import ios_command_group
except ImportError as _e:
    pytest.skip(f"CLI not available: {_e}", allow_module_level=True)


# ===========================================================================
# TestExistingCLIUnbroken — verifies all existing commands still work
# ===========================================================================


class TestExistingCLIUnbroken:
    """Verify existing commands are not broken by additions."""

    def test_ios_help_available(self):
        result = CliRunner().invoke(ios_command_group, ["--help"])
        assert result.exit_code == 0

    def test_ios_setup_help_available(self):
        result = CliRunner().invoke(ios_command_group, ["setup", "--help"])
        assert result.exit_code == 0

    def test_ios_devices_help_available(self):
        result = CliRunner().invoke(ios_command_group, ["devices", "--help"])
        assert result.exit_code == 0

    def test_ios_run_help_available(self):
        result = CliRunner().invoke(ios_command_group, ["run", "--help"])
        assert result.exit_code == 0

    def test_ios_smoke_help_available(self):
        result = CliRunner().invoke(ios_command_group, ["smoke", "--help"])
        assert result.exit_code == 0


# ===========================================================================
# TestDoctorCommand — real invocation, no mocks
# ===========================================================================


class TestDoctorCommand:
    """Verify doctor command runs and produces expected output."""

    def test_doctor_runs_successfully(self):
        result = CliRunner().invoke(ios_command_group, ["doctor"])
        assert result.exit_code == 0

    def test_doctor_mentions_python_version(self):
        result = CliRunner().invoke(ios_command_group, ["doctor"])
        assert "Python" in result.output

    def test_doctor_mentions_specterqa_version(self):
        result = CliRunner().invoke(ios_command_group, ["doctor"])
        assert "specterqa-ios" in result.output.lower() or "specterqa" in result.output.lower()

    def test_doctor_mentions_license_status(self):
        result = CliRunner().invoke(ios_command_group, ["doctor"])
        assert "license" in result.output.lower() or "License" in result.output


# ===========================================================================
# TestInitCommand — real invocation using isolated_filesystem
# ===========================================================================


class TestInitCommand:
    """Verify init creates the expected .specterqa directory structure."""

    def test_init_creates_specterqa_directory(self):
        runner = CliRunner()
        with runner.isolated_filesystem():
            result = runner.invoke(ios_command_group, ["init"])
            assert result.exit_code == 0
            assert Path(".specterqa").exists()

    def test_init_creates_products_directory(self):
        runner = CliRunner()
        with runner.isolated_filesystem():
            runner.invoke(ios_command_group, ["init"])
            assert Path(".specterqa/products").exists()

    def test_init_creates_journeys_directory(self):
        runner = CliRunner()
        with runner.isolated_filesystem():
            runner.invoke(ios_command_group, ["init"])
            assert Path(".specterqa/journeys").exists()

    def test_init_creates_template_product_yaml(self):
        runner = CliRunner()
        with runner.isolated_filesystem():
            runner.invoke(ios_command_group, ["init", "--slug", "my-app"])
            yaml_files = list(Path(".specterqa/products").glob("*.yaml"))
            assert len(yaml_files) >= 1

    def test_init_is_idempotent_with_force(self):
        runner = CliRunner()
        with runner.isolated_filesystem():
            r1 = runner.invoke(ios_command_group, ["init"])
            r2 = runner.invoke(ios_command_group, ["init", "--force"])
            assert r1.exit_code == 0
            assert r2.exit_code == 0


# ===========================================================================
# TestValidateReplayCommand — real file I/O + CliRunner
# ===========================================================================


class TestValidateReplayCommand:
    """Verify replay validation catches errors and passes valid files."""

    def _write_replay(self, tmp_path: Path, content: str) -> Path:
        p = tmp_path / "replay.yaml"
        p.write_text(content)
        return p

    def test_valid_replay_passes(self, tmp_path):
        f = self._write_replay(
            tmp_path,
            'replay:\n  name: test\n  bundle_id: com.example\n  steps:\n    - tapOn: "Save"\n',
        )
        runner = CliRunner()
        result = runner.invoke(ios_command_group, ["validate-replay", str(f)])
        assert result.exit_code == 0

    def test_unknown_action_caught(self, tmp_path):
        f = self._write_replay(
            tmp_path,
            "replay:\n  name: test\n  bundle_id: com.example\n  steps:\n    - action: not_a_real_action\n",
        )
        runner = CliRunner()
        result = runner.invoke(ios_command_group, ["validate-replay", str(f)])
        assert result.exit_code == 1

    def test_missing_bundle_id_caught(self, tmp_path):
        f = self._write_replay(
            tmp_path,
            'replay:\n  name: test\n  steps:\n    - tapOn: "OK"\n',
        )
        runner = CliRunner()
        result = runner.invoke(ios_command_group, ["validate-replay", str(f)])
        assert result.exit_code == 1
        assert "bundle_id" in result.output.lower()

    def test_all_maestro_aliases_are_valid(self, tmp_path):
        """assertVisible, tapOn, inputText, waitFor must all pass validation."""
        content = """\
replay:
  name: maestro-compat
  bundle_id: com.example
  steps:
    - tapOn: "Button"
    - assertVisible: "Screen"
    - assertNotVisible: "Error"
    - inputText: "hello"
    - waitFor: "Done"
"""
        f = self._write_replay(tmp_path, content)
        runner = CliRunner()
        result = runner.invoke(ios_command_group, ["validate-replay", str(f)])
        assert result.exit_code == 0, f"Maestro aliases should be valid. Output: {result.output}"

    def test_valid_skip_to_with_resolved_step_id(self, tmp_path):
        content = """\
replay:
  name: test
  bundle_id: com.example
  steps:
    - action: skip_to
      skip_to: end_step
    - action: tap
      step_id: end_step
      element_label: Done
"""
        f = self._write_replay(tmp_path, content)
        runner = CliRunner()
        result = runner.invoke(ios_command_group, ["validate-replay", str(f)])
        assert result.exit_code == 0
