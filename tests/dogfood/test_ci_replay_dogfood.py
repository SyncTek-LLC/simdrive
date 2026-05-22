"""Dogfood Test 1 — CI Replay Workflow.

Mirrors the real user workflow for recording a flow and replaying it in CI.
Two portions:

CI-ALWAYS (runs on every PR, no simulator required):
  - Fresh venv creation
  - pip install of the local package
  - ``specterqa-ios runner build`` exits with code 0 (Xcode required but skipped
    gracefully when not available)

LIVE-SIM (SPECTERQA_LIVE_SIM=1 + Xcode + booted simulator required):
  - Boot simulator, record a 3-step flow against TestKitApp
  - Save YAML, validate, replay, assert all steps pass

Marked ``@pytest.mark.dogfood`` for the full module.
Marked ``@pytest.mark.live`` for the live-sim portions only.

If the CI-always portion fails, the happy-path install flow is broken.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.dogfood

REPO_ROOT = Path(__file__).parent.parent.parent
# pyproject.toml moved into simdrive/ subdirectory at commit a0abf0b
SIMDRIVE_ROOT = REPO_ROOT / "simdrive"
TESTKIT_BUNDLE_ID = "io.synctek.specterqa.testkit"


def _get_local_version() -> str:
    """Return the package version from simdrive/pyproject.toml or importlib.metadata."""
    try:
        from importlib.metadata import version

        return version("simdrive")
    except Exception:
        pass
    try:
        import tomllib

        with open(SIMDRIVE_ROOT / "pyproject.toml", "rb") as f:
            data = tomllib.load(f)
        return data["project"]["version"]
    except Exception:
        return "0.0.0"


def _xcode_available() -> bool:
    try:
        r = subprocess.run(["xcodebuild", "-version"], capture_output=True, timeout=10)
        return r.returncode == 0
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Fixture: fresh venv with specterqa-ios installed
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def fresh_install_venv(tmp_path_factory):
    """Create a fresh venv at /tmp/df-replay-venv and install specterqa-ios."""
    venv_dir = Path("/tmp/df-replay-venv")

    # Create the venv
    subprocess.run(
        [sys.executable, "-m", "venv", str(venv_dir), "--clear"],
        check=True,
        capture_output=True,
    )

    venv_python = venv_dir / "bin" / "python"
    # CLI was renamed from specterqa-ios to simdrive when the project became SimDrive
    venv_specterqa = venv_dir / "bin" / "simdrive"

    # Install from simdrive/ subdirectory (pyproject.toml moved there at commit a0abf0b)
    # Use --no-cache-dir to simulate a fresh user install
    install_result = subprocess.run(
        [
            str(venv_python), "-m", "pip", "install",
            "--no-cache-dir", "--quiet",
            str(SIMDRIVE_ROOT),
        ],
        capture_output=True,
        text=True,
        timeout=120,
    )
    if install_result.returncode != 0:
        pytest.fail(
            f"pip install failed — happy path install is broken.\n"
            f"stderr: {install_result.stderr[-500:]}"
        )

    return {"venv_dir": venv_dir, "python": venv_python, "cli": venv_specterqa}


# ---------------------------------------------------------------------------
# CI-ALWAYS: install + runner build (no simulator needed)
# ---------------------------------------------------------------------------


def test_fresh_venv_created(fresh_install_venv):
    """Venv must exist and simdrive CLI must be importable."""
    venv_dir = fresh_install_venv["venv_dir"]
    assert venv_dir.exists(), f"Venv not found at {venv_dir}"
    cli = fresh_install_venv["cli"]
    assert cli.exists(), f"simdrive CLI not found at {cli}"


def test_cli_help_exits_zero(fresh_install_venv):
    """``simdrive --help`` must exit 0 — confirms entry point wiring."""
    result = subprocess.run(
        [str(fresh_install_venv["cli"]), "--help"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, (
        f"simdrive --help returned non-zero.\nstdout: {result.stdout}\n"
        f"stderr: {result.stderr}"
    )


def test_runner_build_ci(fresh_install_venv):
    """``simdrive runner build`` must exit 0 from a fresh install.

    Skipped when xcodebuild is not available — this test is still gated
    by Xcode, but the install itself (test_fresh_venv_created) is CI-always.
    """
    if not _xcode_available():
        pytest.skip("xcodebuild not available — runner build requires Xcode")

    result = subprocess.run(
        [str(fresh_install_venv["cli"]), "runner", "build"],
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert result.returncode == 0, (
        f"simdrive runner build failed from fresh venv install.\n"
        f"This means the wheel is broken — the runner xcodeproj was not found.\n"
        f"stdout: {result.stdout[-500:]}\nstderr: {result.stderr[-500:]}"
    )


# ---------------------------------------------------------------------------
# LIVE-SIM: record → save → validate → replay
# ---------------------------------------------------------------------------


@pytest.mark.live
def test_ci_replay_full_flow(fresh_install_venv):
    """Full CI replay workflow: record 3-step flow, save YAML, validate, replay.

    Requires SPECTERQA_LIVE_SIM=1 environment variable + booted simulator.
    """
    if not os.environ.get("SPECTERQA_LIVE_SIM"):
        pytest.skip("requires SPECTERQA_LIVE_SIM=1 + Xcode + booted simulator")

    if not _xcode_available():
        pytest.skip("Xcode / xcodebuild not available")

    # Import MCP tools directly (they exercise the real session manager)
    try:
        from specterqa.ios.mcp.server import create_server
    except ImportError as exc:
        pytest.skip(f"MCP server not importable: {exc}")

    import asyncio

    mcp = create_server()

    async def _run():
        # Start session
        start = await mcp.call_tool("ios_start_session", {"bundle_id": TESTKIT_BUNDLE_ID, "backend": "xctest"})
        assert not getattr(start, "isError", False), f"ios_start_session failed: {start}"

        # Begin recording
        rec = await mcp.call_tool("ios_start_recording", {})
        assert not getattr(rec, "isError", False), f"ios_start_recording failed: {rec}"

        # 3-step flow (tap visible elements)
        await mcp.call_tool("ios_wait_idle", {})
        await mcp.call_tool("ios_tap", {"label": "FormTab"})
        await mcp.call_tool("ios_wait_idle", {})
        await mcp.call_tool("ios_tap", {"label": "ListTab"})
        await mcp.call_tool("ios_wait_idle", {})
        await mcp.call_tool("ios_tap", {"label": "FormTab"})
        await mcp.call_tool("ios_wait_idle", {})

        # Save replay
        stop = await mcp.call_tool("ios_stop_recording", {"name": "ci_dogfood_flow"})
        assert not getattr(stop, "isError", False), f"ios_stop_recording failed: {stop}"

        # Validate
        validate = await mcp.call_tool("ios_validate_replay", {"name": "ci_dogfood_flow"})
        assert not getattr(validate, "isError", False), f"ios_validate_replay failed: {validate}"

        # Replay
        replay = await mcp.call_tool("ios_replay", {"name": "ci_dogfood_flow"})
        assert not getattr(replay, "isError", False), f"ios_replay failed: {replay}"

        # Stop session
        await mcp.call_tool("ios_stop_session", {})

    asyncio.run(_run())
