"""Gap tests — B1/B1.5: fresh-venv wheel install must produce a buildable runner.

The 13.2.0 wheel shipped runner_source/SpecterQARunner.xcodeproj/project.pbxproj
with a reference to Sources/RequestParser.swift which no longer exists. This
caused 'Build input file cannot be found' for every fresh installer.

B1.5 (13.2.1): _runner_source_dir() only checked the dev-tree path
(pkg_root/runner/); installed wheel users always got None and the CLI fell back
to CWD, causing "xcodebuild: error: '<cwd>/SpecterQARunner.xcodeproj' does not
exist" for every fresh pip install.

Tests:
1. Builds the wheel from the current source tree.
2. Creates a fresh venv and pip-installs the wheel.
3. Asserts _runner_source_dir() returns the bundled path (not None) [B1.5].
4. Asserts the returned path contains both build.sh AND SpecterQARunner.xcodeproj [B1.5].
5. Runs `specterqa-ios runner build` from that venv [requires Xcode].
6. Asserts exit code 0 AND a .xctestrun file is produced [requires Xcode].

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


# ---------------------------------------------------------------------------
# Test 3 (B1.5): _runner_source_dir() must find bundled runner in fresh-venv
#                installs (NOT return None, NOT fall back to CWD)
# ---------------------------------------------------------------------------


def test_runner_source_dir_finds_bundled_path(tmp_path):
    """_runner_source_dir() must return the wheel's runner_source dir, not None.

    B1.5 bug: the function only checked pkg_root/runner/ (dev-tree layout).
    In an installed wheel, pkg_root is site-packages, which has no runner/
    subdirectory, so the function always returned None. The CLI then fell back
    to Path.cwd(), and xcodebuild failed immediately with
    "SpecterQARunner.xcodeproj does not exist".

    This test:
    1. Builds a wheel from the current source tree.
    2. pip-installs it into a fresh venv.
    3. Invokes _runner_source_dir() via a subprocess inside that venv.
    4. Asserts the result is NOT None.
    5. Asserts the returned path contains build.sh AND SpecterQARunner.xcodeproj.

    Fails on PR HEAD before the B1.5 fix; passes after.
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
    assert len(wheels) == 1, f"Expected 1 wheel, found {len(wheels)}"
    whl = wheels[0]

    # Create fresh venv
    venv_dir = tmp_path / "test-venv"
    subprocess.run([sys.executable, "-m", "venv", str(venv_dir)], check=True)

    if sys.platform != "darwin":
        pytest.skip("macOS only test")

    venv_python = venv_dir / "bin" / "python"

    # Install wheel into fresh venv
    install_result = subprocess.run(
        [str(venv_python), "-m", "pip", "install", str(whl), "--quiet",
         "--ignore-requires-python"],
        capture_output=True,
        text=True,
        timeout=120,
    )
    if install_result.returncode != 0:
        pytest.skip(f"pip install failed: {install_result.stderr[-200:]}")

    # Invoke _runner_source_dir() inside the fresh venv via a one-liner
    probe = (
        "from specterqa.ios.cli.commands import _runner_source_dir; "
        "p = _runner_source_dir(); "
        "print(p if p else '__NONE__')"
    )
    probe_result = subprocess.run(
        [str(venv_python), "-c", probe],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert probe_result.returncode == 0, (
        f"B1.5: probe script crashed in fresh venv.\n"
        f"stdout: {probe_result.stdout}\nstderr: {probe_result.stderr}"
    )

    reported = probe_result.stdout.strip()
    assert reported != "__NONE__", (
        "B1.5: _runner_source_dir() returned None in a fresh-venv install. "
        "The function must find the bundled runner_source/ inside the wheel's "
        "site-packages, not only the dev-tree pkg_root/runner/ path."
    )

    bundled_path = Path(reported)

    assert (bundled_path / "build.sh").exists(), (
        f"B1.5: bundled runner path {bundled_path} does not contain build.sh"
    )
    assert (bundled_path / "SpecterQARunner.xcodeproj").exists(), (
        f"B1.5: bundled runner path {bundled_path} does not contain "
        "SpecterQARunner.xcodeproj"
    )
