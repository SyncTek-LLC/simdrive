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
RUNNER_SRC = os.path.join(REPO_ROOT, "runner")
RUNNER_DEST = os.path.join(REPO_ROOT, "src", "specterqa", "ios", "runner_source")
CHANGELOG_SRC = os.path.join(REPO_ROOT, "CHANGELOG.md")
CHANGELOG_DEST = os.path.join(REPO_ROOT, "src", "specterqa", "ios", "CHANGELOG.md")
PRESERVED = {"__init__.py"}
SYNCED_SUBPATHS = ["Sources", "SpecterQARunner.xcodeproj", "HostApp",
                   "Package.swift", "build.sh", "launch.sh"]


def _sync_runner_tree():
    """Copy runner/ → runner_source/ so wheel + sdist package-data can include it.

    Runs at setup.py IMPORT TIME (not just build_py.run) so the files exist
    before setuptools enumerates package-data globs. Without this the wheel
    ships incomplete sources and end-user `runner build` fails.
    """
    for sub in SYNCED_SUBPATHS:
        src = os.path.join(RUNNER_SRC, sub)
        dest = os.path.join(RUNNER_DEST, sub)
        if not os.path.exists(src):
            continue
        if os.path.isdir(src):
            if os.path.exists(dest):
                shutil.rmtree(dest)
            shutil.copytree(src, dest)
        else:
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            shutil.copy2(src, dest)


def _copy_changelog_module_level():
    if os.path.exists(CHANGELOG_SRC):
        shutil.copy2(CHANGELOG_SRC, CHANGELOG_DEST)


# Sync at import time so package-data scanning sees the files.
if os.path.isdir(RUNNER_SRC):
    _sync_runner_tree()
    _copy_changelog_module_level()


class build_py(_build_py):
    """Re-sync runner/ → runner_source/ and CHANGELOG.md at build time.

    The import-time sync above ensures package-data globs work; this re-runs
    at build_py time as a belt-and-suspenders against any stale state and
    keeps the CHANGELOG copy fresh on rebuild.
    """

    def run(self):
        if os.path.isdir(RUNNER_SRC):
            _sync_runner_tree()
            self._copy_changelog()
        super().run()

    def _copy_changelog(self):
        if os.path.exists(CHANGELOG_SRC):
            shutil.copy2(CHANGELOG_SRC, CHANGELOG_DEST)
            print(f"[specterqa-ios] copied CHANGELOG.md → src/specterqa/ios/CHANGELOG.md")
        else:
            print(f"[specterqa-ios] WARNING: CHANGELOG.md not found at {CHANGELOG_SRC}")


setup(cmdclass={"build_py": build_py})
