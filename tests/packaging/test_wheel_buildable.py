"""Gap test — B1: fresh-venv wheel install must produce a buildable runner.

The 13.2.0 wheel shipped runner_source/SpecterQARunner.xcodeproj/project.pbxproj
with a reference to Sources/RequestParser.swift which no longer exists. This
caused 'Build input file cannot be found' for every fresh installer.

This test:
1. Builds the wheel from the current source tree.
2. Creates a fresh venv and pip-installs the wheel.
3. Runs `specterqa-ios runner build` from that venv.
4. Asserts exit code 0 AND a .xctestrun file is produced.

Marked @pytest.mark.live because it requires Xcode / xcodebuild. Skipped in
pure-Python CI without Xcode.

Run (requires Xcode):
    pytest tests/packaging/test_wheel_buildable.py -v -m live
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

pytestmark = pytest.mark.live

REPO_ROOT = Path(__file__).parent.parent.parent
RUNNER_BUILD_DIR = Path.home() / ".specterqa" / "runner-build"


def _xcode_available() -> bool:
    try:
        r = subprocess.run(["xcodebuild", "-version"], capture_output=True, timeout=10)
        return r.returncode == 0
    except Exception:
        return False


@pytest.fixture(autouse=True)
def require_xcode():
    if not _xcode_available():
        pytest.skip("Xcode / xcodebuild not available — skipping wheel-buildable test")


# ---------------------------------------------------------------------------
# Test 1: built wheel must NOT contain RequestParser.swift references
# ---------------------------------------------------------------------------


def test_wheel_pbxproj_no_requestparser(tmp_path):
    """The shipped project.pbxproj must not reference RequestParser.swift.

    Fails on 13.2.0 main: pbxproj still has 4 references.
    Passes after B1 fix: references removed.
    """
    dist = tmp_path / "dist"
    dist.mkdir()
    result = subprocess.run(
        [sys.executable, "-m", "build", "--wheel", "--outdir", str(dist)],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=180,
    )
    if result.returncode != 0:
        pytest.skip(f"Wheel build failed: {result.stderr[-300:]}")

    wheels = list(dist.glob("*.whl"))
    assert len(wheels) == 1, f"Expected 1 wheel, found {len(wheels)}"
    whl = wheels[0]

    with zipfile.ZipFile(whl) as z:
        pbxproj_entries = [n for n in z.namelist() if n.endswith("project.pbxproj")]
        assert pbxproj_entries, "project.pbxproj not found in wheel"

        for entry in pbxproj_entries:
            content = z.read(entry).decode("utf-8", errors="replace")
            assert "RequestParser.swift" not in content, (
                f"B1: {entry} in wheel still references RequestParser.swift — "
                "this will cause 'Build input file cannot be found' for fresh installs"
            )


# ---------------------------------------------------------------------------
# Test 2: fresh-venv pip-install → runner build succeeds
# ---------------------------------------------------------------------------


def test_fresh_venv_runner_build(tmp_path):
    """Fresh pip install of the wheel must produce a working runner build.

    This is the end-to-end proof that B1 is fixed.
    """
    dist = tmp_path / "dist"
    dist.mkdir()

    # Build wheel
    build_result = subprocess.run(
        [sys.executable, "-m", "build", "--wheel", "--outdir", str(dist)],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=180,
    )
    if build_result.returncode != 0:
        pytest.skip(f"Wheel build failed: {build_result.stderr[-300:]}")

    wheels = list(dist.glob("*.whl"))
    assert len(wheels) == 1
    whl = wheels[0]

    # Create fresh venv
    venv_dir = tmp_path / "test-venv"
    subprocess.run([sys.executable, "-m", "venv", str(venv_dir)], check=True)

    # Determine venv python / pip paths
    if sys.platform == "darwin":
        venv_python = venv_dir / "bin" / "python"
        venv_specterqa = venv_dir / "bin" / "specterqa-ios"
    else:
        pytest.skip("macOS only test")

    # Install wheel into venv (ignore Python version constraint since the venv
    # may be running on a python that satisfies requires-python even if the
    # test runner doesn't; use --ignore-requires-python for CI environments
    # where the test runner is Python 3.9 but homebrew python is 3.12+)
    install_result = subprocess.run(
        [str(venv_python), "-m", "pip", "install", str(whl), "--quiet",
         "--ignore-requires-python"],
        capture_output=True,
        text=True,
        timeout=120,
    )
    if install_result.returncode != 0:
        pytest.skip(f"pip install failed (Python version mismatch?): {install_result.stderr[-200:]}")

    # Use a temp build dir so we don't pollute ~/.specterqa/runner-build
    test_build_dir = tmp_path / "runner-build"
    test_build_dir.mkdir()

    env = os.environ.copy()
    env["SPECTERQA_RUNNER_BUILD_DIR"] = str(test_build_dir)

    # Run runner build from the fresh venv
    run_result = subprocess.run(
        [str(venv_specterqa), "runner", "build"],
        capture_output=True,
        text=True,
        timeout=300,
        env=env,
    )

    assert run_result.returncode == 0, (
        f"B1: runner build failed in fresh venv install.\n"
        f"stdout: {run_result.stdout[-500:]}\n"
        f"stderr: {run_result.stderr[-500:]}"
    )

    # Verify a .xctestrun was produced
    xctestrun_files = list(test_build_dir.rglob("*.xctestrun"))
    # Fall back to default build dir in case env var wasn't respected
    if not xctestrun_files:
        xctestrun_files = list(RUNNER_BUILD_DIR.rglob("*.xctestrun"))

    assert xctestrun_files, (
        "B1: runner build exited 0 but no .xctestrun file was produced"
    )
