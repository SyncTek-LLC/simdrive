"""Gap test — B3+B4: recorder→validator schema sync.

The MCP recorder writes `element_identifier` into every step.
The CLI validator at cli/commands.py must accept the same keys the recorder emits.

TDD sequence:
1. Run against current main → test_recorder_step_passes_cli_validator FAILS (proves bug)
2. Apply fix (add element_identifier / tapOnIdentifier to valid_keys)
3. Re-run → PASSES

Run:
    pytest tests/regression/test_recorder_validator_roundtrip.py -v
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).parent.parent.parent

# ---------------------------------------------------------------------------
# Step dict that exactly mirrors what mcp/server.py:2404 records
# (element_identifier is the key the recorder writes, validator rejects)
# ---------------------------------------------------------------------------

RECORDED_STEP = {
    "action": "tap",
    "element_identifier": "LibrariesButton",   # recorder writes this
    "element_label": "Libraries",
    "x": 195.0,
    "y": 786.0,
    "expect_elements": ["Libraries", "Search Catalog"],
}

MAESTRO_STEP_WITH_ALIAS = {
    "tapOnIdentifier": "LibrariesButton",       # Maestro alias (un-normalised)
}

UNKNOWN_KEY_STEP = {
    "action": "tap",
    "element_xyz": "should_fail",               # truly unknown key — must still fail
    "x": 195.0,
    "y": 786.0,
}


def _write_replay(steps: list[dict], tmp: Path) -> Path:
    replay = {
        "replay": {
            "name": "roundtrip-test",
            "bundle_id": "io.synctek.test",
            "steps": steps,
        }
    }
    p = tmp / "replay.yaml"
    p.write_text(yaml.dump(replay))
    return p


def _run_validate(replay_path: Path) -> subprocess.CompletedProcess:
    # Use the installed CLI entry point; fall back to module invocation with
    # the repo src/ on the path so the test works in both installed and
    # editable-install environments.
    cli = shutil.which("specterqa-ios")
    if cli:
        cmd = [cli, "validate-replay", str(replay_path)]
        env = None
    else:
        # Editable / dev install path: add src/ to PYTHONPATH
        import os
        env = os.environ.copy()
        env["PYTHONPATH"] = str(REPO_ROOT / "src") + os.pathsep + env.get("PYTHONPATH", "")
        cmd = [sys.executable, "-m", "specterqa.ios.cli.commands", "validate-replay", str(replay_path)]

    return subprocess.run(cmd, capture_output=True, text=True, env=env)


# ---------------------------------------------------------------------------
# Test 1: element_identifier must be accepted (was rejected before fix)
# ---------------------------------------------------------------------------


def test_recorder_step_passes_cli_validator(tmp_path: Path) -> None:
    """A step dict that the MCP recorder emits must pass CLI validate-replay.

    Fails on main (before fix): 'unknown key element_identifier'
    Passes after fix: element_identifier added to valid_keys.
    """
    replay_path = _write_replay([RECORDED_STEP], tmp_path)
    result = _run_validate(replay_path)
    assert result.returncode == 0, (
        f"B3/B4 regression: CLI validate-replay rejected a recorder-emitted step.\n"
        f"stdout: {result.stdout}\n"
        f"stderr: {result.stderr}"
    )


# ---------------------------------------------------------------------------
# Test 2: tapOnIdentifier (Maestro alias) must also be accepted
# ---------------------------------------------------------------------------


def test_tap_on_identifier_alias_accepted(tmp_path: Path) -> None:
    """tapOnIdentifier (Maestro alias) must pass CLI validate-replay."""
    replay_path = _write_replay([MAESTRO_STEP_WITH_ALIAS], tmp_path)
    result = _run_validate(replay_path)
    assert result.returncode == 0, (
        f"tapOnIdentifier (Maestro alias) rejected by CLI validator.\n"
        f"stdout: {result.stdout}\n"
        f"stderr: {result.stderr}"
    )


# ---------------------------------------------------------------------------
# Test 3: truly unknown keys must still fail validation (regression guard)
# ---------------------------------------------------------------------------


def test_unknown_key_still_fails(tmp_path: Path) -> None:
    """An unknown key like element_xyz must still fail CLI validate-replay.

    Ensures the fix adds specific keys, not a blanket allow-all.
    """
    replay_path = _write_replay([UNKNOWN_KEY_STEP], tmp_path)
    result = _run_validate(replay_path)
    assert result.returncode != 0, (
        "element_xyz should have been rejected by CLI validate-replay but was accepted"
    )


# ---------------------------------------------------------------------------
# Test 4: multi-step replay with element_identifier throughout
# ---------------------------------------------------------------------------


def test_multi_step_replay_all_element_identifier(tmp_path: Path) -> None:
    """A multi-step replay where every step uses element_identifier must validate."""
    steps = [
        {"action": "tap", "element_identifier": f"button_{i}", "x": float(i * 10), "y": 200.0}
        for i in range(5)
    ]
    replay_path = _write_replay(steps, tmp_path)
    result = _run_validate(replay_path)
    assert result.returncode == 0, (
        f"Multi-step replay with element_identifier failed validation.\n"
        f"stdout: {result.stdout}\n"
        f"stderr: {result.stderr}"
    )
