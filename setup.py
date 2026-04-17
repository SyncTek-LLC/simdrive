"""
setup.py — build-time hook to populate runner_source/Sources from runner/Sources.

runner/Sources/ is the single source of truth for Swift files.
src/specterqa/ios/runner_source/Sources/ is a build-time copy so the wheel
contains the Swift sources that end-users need to compile the XCTest runner.

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


class build_py(_build_py):
    """Sync runner/Sources/ → runner_source/Sources/ before packaging."""

    def run(self):
        self._sync_swift_sources()
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


setup(cmdclass={"build_py": build_py})
