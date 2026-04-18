"""
setup.py — build-time hook to populate runner_source/Sources from runner/Sources
and to copy CHANGELOG.md into the package so it ships in the wheel.

runner/Sources/ is the single source of truth for Swift files.
src/specterqa/ios/runner_source/Sources/ is a build-time copy so the wheel
contains the Swift sources that end-users need to compile the XCTest runner.

CHANGELOG.md lives at the repo root (not inside any package directory), so it
is copied into src/specterqa/ios/ at build time and declared in package-data.
This ensures it ships in the wheel and is accessible via
``importlib.resources`` or simply at ``specterqa/ios/CHANGELOG.md`` inside
the wheel archive.

This file is intentionally minimal. All project metadata lives in pyproject.toml.
"""

import os
import shutil
from setuptools import setup
from setuptools.command.build_py import build_py as _build_py


REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
RUNNER_SOURCES = os.path.join(REPO_ROOT, "runner", "Sources")
RUNNER_SOURCE_DEST = os.path.join(
    REPO_ROOT, "src", "specterqa", "ios", "runner_source", "Sources"
)
CHANGELOG_SRC = os.path.join(REPO_ROOT, "CHANGELOG.md")
CHANGELOG_DEST = os.path.join(REPO_ROOT, "src", "specterqa", "ios", "CHANGELOG.md")


class build_py(_build_py):
    """Sync runner/Sources/ → runner_source/Sources/ and copy CHANGELOG.md before packaging."""

    def run(self):
        self._sync_swift_sources()
        self._copy_changelog()
        super().run()

    def _sync_swift_sources(self):
        if os.path.exists(RUNNER_SOURCE_DEST):
            shutil.rmtree(RUNNER_SOURCE_DEST)
        shutil.copytree(RUNNER_SOURCES, RUNNER_SOURCE_DEST)
        swift_files = [f for f in os.listdir(RUNNER_SOURCE_DEST) if f.endswith(".swift")]
        print(
            f"[specterqa-ios] synced {len(swift_files)} Swift files: "
            f"runner/Sources/ → runner_source/Sources/"
        )

    def _copy_changelog(self):
        if os.path.exists(CHANGELOG_SRC):
            shutil.copy2(CHANGELOG_SRC, CHANGELOG_DEST)
            print(f"[specterqa-ios] copied CHANGELOG.md → src/specterqa/ios/CHANGELOG.md")
        else:
            print(f"[specterqa-ios] WARNING: CHANGELOG.md not found at {CHANGELOG_SRC}")


setup(cmdclass={"build_py": build_py})
