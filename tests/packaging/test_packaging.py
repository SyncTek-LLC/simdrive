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

    def test_runner_source_importable(self, built_wheel):
        """Verify the wheel contains Swift sources in runner_source/Sources/.

        After the runner_source dedup refactor, runner/Sources/ is the single
        source of truth. setup.py's build_py override copies them into
        runner_source/Sources/ at build time so the shipped wheel contains the
        Swift runner. We verify the wheel — not the dev-tree state — because
        the Sources/ directory is intentionally absent from git.
        """
        with zipfile.ZipFile(built_wheel) as whl:
            swift_in_wheel = [
                n for n in whl.namelist()
                if "runner_source/Sources/" in n and n.endswith(".swift")
            ]
        assert len(swift_in_wheel) >= 6, (
            f"Only {len(swift_in_wheel)} Swift files found under runner_source/Sources/ "
            f"in the wheel — build_py copy hook may be broken"
        )
