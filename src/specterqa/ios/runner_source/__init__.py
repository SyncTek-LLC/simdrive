"""SpecterQA XCTest runner source files.

This package bundles the Swift source code for the XCTest HTTP server
that runs on the iOS Simulator. The source is compiled at first session
start via xcodebuild.
"""
from pathlib import Path

RUNNER_SOURCE_DIR = Path(__file__).parent
SOURCES_DIR = RUNNER_SOURCE_DIR / "Sources"
BUILD_SCRIPT = RUNNER_SOURCE_DIR / "build.sh"
XCODEPROJ = RUNNER_SOURCE_DIR / "SpecterQARunner.xcodeproj"
