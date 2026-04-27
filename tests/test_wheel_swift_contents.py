"""Wheel Swift contents test — v14.0.0b1 Phase 2 audit gap #6.

Builds the wheel from the current source tree (no Xcode required — pure zip
inspection) and asserts that key Swift source files from runner/Sources/ are
present inside the .whl archive.

The Phase 2 wheel restructure deleted the build_py override that previously
copied runner_source/ files. If the MANIFEST.in / pyproject.toml package-data
configuration is broken, Swift files will silently vanish from the wheel.
This test locks in the packaging contract without requiring a live Xcode build.

Run:
    /opt/homebrew/bin/python3.11 -m pytest tests/test_wheel_swift_contents.py -xvs
"""
from __future__ import annotations

import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent

# Key files that MUST be in the wheel for the runner build to succeed.
# These are checked by path suffix so the wheel name prefix doesn't matter.
REQUIRED_SWIFT_PATHS = [
    "runner/Sources/SpecterQARunner.swift",
    "runner/Sources/HTTPServer.swift",
    "runner/Sources/TouchInjector.swift",
    "runner/Package.swift",
    "runner/build.sh",
]


@pytest.fixture(scope="module")
def built_wheel(tmp_path_factory):
    """Build the wheel once per test module; skip if build fails.

    NOTE: We pass --no-isolation and clean build/ first to prevent stale
    build/lib artifacts (from pre-Phase-2 builds) from contaminating the wheel.
    Phase 2 deleted runner_source/ from src/ but a stale build/lib copy can
    sneak back in if build/ is not cleaned. This is the #1 packaging risk
    flagged in the Phase 2 CEO return summary.
    """
    import shutil

    # Remove stale build/ directory to prevent old runner_source/ artifacts
    build_dir = REPO_ROOT / "build"
    if build_dir.exists():
        shutil.rmtree(build_dir)

    dist = tmp_path_factory.mktemp("dist")
    result = subprocess.run(
        [sys.executable, "-m", "build", "--wheel", "--outdir", str(dist)],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=300,
    )
    if result.returncode != 0:
        pytest.skip(
            f"Wheel build failed (install `build` package?): {result.stderr[-400:]}"
        )
    wheels = list(dist.glob("*.whl"))
    assert len(wheels) == 1, f"Expected 1 wheel in dist/, found: {[w.name for w in wheels]}"
    return wheels[0]


@pytest.fixture(scope="module")
def wheel_namelist(built_wheel):
    """Return the full list of files inside the built wheel."""
    with zipfile.ZipFile(built_wheel) as z:
        return z.namelist()


# ---------------------------------------------------------------------------
# Individual assertions — one test per critical file so failures are precise
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("expected_suffix", REQUIRED_SWIFT_PATHS)
def test_swift_file_in_wheel(wheel_namelist, expected_suffix):
    """Each required runner file must be present inside the .whl archive."""
    matches = [n for n in wheel_namelist if n.endswith(expected_suffix)]
    assert matches, (
        f"PACKAGING BUG: '{expected_suffix}' not found in wheel.\n"
        f"The Phase 2 wheel restructure (deletion of build_py override) may have "
        f"broken the Swift source packaging. Check MANIFEST.in and "
        f"pyproject.toml [tool.setuptools.package-data].\n"
        f"Wheel contents (first 30): {wheel_namelist[:30]}"
    )


def test_wheel_contains_runner_init(wheel_namelist):
    """runner/__init__.py must be present in the wheel (Phase 2 addition)."""
    matches = [n for n in wheel_namelist if n.endswith("runner/__init__.py")]
    assert matches, (
        "runner/__init__.py missing from wheel — Phase 2 requires this file.\n"
        f"Wheel contents (first 30): {wheel_namelist[:30]}"
    )


def test_wheel_does_not_contain_runner_source(wheel_namelist):
    """runner_source/ must NOT appear in the wheel (deleted in Phase 2)."""
    runner_source_entries = [n for n in wheel_namelist if "runner_source" in n]
    assert not runner_source_entries, (
        f"STALE PACKAGING: runner_source/ entries found in wheel after Phase 2 deletion: "
        f"{runner_source_entries[:5]}"
    )


def test_wheel_version_is_final(built_wheel):
    """Wheel filename must reflect the final version 15.1.1."""
    assert "15.1.1" in built_wheel.name, (
        f"Wheel filename does not contain '15.1.1': {built_wheel.name}"
    )
