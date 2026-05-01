"""Build hook: compile the native HID injection helper before packaging.

This runs `make` in `native/` whenever setup builds the wheel. The output
binary lands at `src/simdrive/_bin/simdrive-input` and is picked up by
[tool.setuptools.package-data] in pyproject.toml.

setup.py is intentionally minimal — pyproject.toml is the source of truth
for project metadata.
"""
from __future__ import annotations

import os
import platform
import subprocess
import sys
from pathlib import Path

from setuptools import setup
from setuptools.command.build_py import build_py


HERE = Path(__file__).parent
NATIVE_DIR = HERE / "native"
BINARY_PATH = HERE / "src" / "specterqa_ios" / "_bin" / "simdrive-input"


class BuildPyWithNative(build_py):
    """build_py subclass that compiles the native HID helper as a pre-step."""

    def run(self) -> None:
        if platform.system() == "Darwin" and NATIVE_DIR.exists():
            self._build_native()
        else:
            print(
                f"simdrive: skipping native build (system={platform.system()}, "
                f"native_dir_exists={NATIVE_DIR.exists()})",
                file=sys.stderr,
            )
        super().run()

    def _build_native(self) -> None:
        if BINARY_PATH.exists() and os.environ.get("SIMDRIVE_SKIP_NATIVE_BUILD"):
            print(f"simdrive: SIMDRIVE_SKIP_NATIVE_BUILD set; using existing {BINARY_PATH}")
            return
        print("simdrive: building native helper via make...")
        try:
            subprocess.run(
                ["make", "clean", "all"],
                cwd=str(NATIVE_DIR),
                check=True,
            )
        except subprocess.CalledProcessError as exc:
            raise RuntimeError(
                f"Native build failed (rc={exc.returncode}). "
                "simdrive requires Xcode + macOS to compile its HID helper."
            ) from exc
        if not BINARY_PATH.exists():
            raise RuntimeError(
                f"Native build reported success but binary not found at {BINARY_PATH}"
            )
        print(f"simdrive: built {BINARY_PATH}")


setup(cmdclass={"build_py": BuildPyWithNative})
