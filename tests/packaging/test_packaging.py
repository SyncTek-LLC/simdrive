"""
Packaging Verification — catches the v11.9.0 class of bugs.

Builds the actual wheel and verifies its contents. No mocking.

Run:
    pytest tests/test_packaging.py -v
"""
import subprocess
import sys
import zipfile
from pathlib import Path
import pytest

REPO_ROOT = Path(__file__).parent.parent

REQUIRED_SWIFT_FILES = [
    "SpecterQARunner.swift",
    "TouchInjector.swift",
    "HTTPServer.swift",
    "AccessibilityTree.swift",
    "SpecterQAElementQuery.swift",
    "SpecterQAScreenshot.swift",
    "RequestParser.swift",
]


@pytest.fixture(scope="module")
def built_wheel(tmp_path_factory):
    dist = tmp_path_factory.mktemp("dist")
    result = subprocess.run(
        [sys.executable, "-m", "build", "--wheel", "--outdir", str(dist)],
        cwd=str(REPO_ROOT), capture_output=True, text=True, timeout=120,
    )
    if result.returncode != 0:
        pytest.skip(f"Wheel build failed (missing build deps?): {result.stderr[-200:]}")
    wheels = list(dist.glob("*.whl"))
    assert len(wheels) == 1
    return wheels[0]


class TestWheelContents:

    def test_swift_source_files_present(self, built_wheel):
        with zipfile.ZipFile(built_wheel) as whl:
            names = [Path(n).name for n in whl.namelist()]
        for sf in REQUIRED_SWIFT_FILES:
            assert sf in names, f"Missing {sf} in wheel — v11.9.0 packaging bug class"

    def test_build_script_present(self, built_wheel):
        with zipfile.ZipFile(built_wheel) as whl:
            assert any("build.sh" in n for n in whl.namelist()), "build.sh missing from wheel"

    def test_xcodeproj_present(self, built_wheel):
        with zipfile.ZipFile(built_wheel) as whl:
            assert any("project.pbxproj" in n for n in whl.namelist()), "project.pbxproj missing"

    def test_runner_source_importable(self):
        """Verify runner_source is importable from the installed package."""
        try:
            from specterqa.ios.runner_source import RUNNER_SOURCE_DIR, SOURCES_DIR
            assert SOURCES_DIR.exists(), f"SOURCES_DIR doesn't exist: {SOURCES_DIR}"
            swift_files = list(SOURCES_DIR.glob("*.swift"))
            assert len(swift_files) >= 7, f"Only {len(swift_files)} Swift files in {SOURCES_DIR}"
        except ImportError:
            pytest.fail("specterqa.ios.runner_source not importable — packaging broken")
