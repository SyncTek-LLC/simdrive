"""Tests verifying the P0 Xcode 16 / injector fixes are correctly applied.

[internal-tracker]
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent.parent


# ---------------------------------------------------------------------------
# 1. AccessibilityTree.swift — guard let removed
# ---------------------------------------------------------------------------


def test_accessibility_tree_no_guard_let_snapshot():
    """AccessibilityTree.swift must not contain the spurious guard-let on a non-optional."""
    src = (REPO_ROOT / "runner" / "Sources" / "AccessibilityTree.swift").read_text()
    assert "guard let snapshot = snapshot" not in src, (
        "guard let snapshot = snapshot should have been removed — snapshot() is non-optional"
    )


# ---------------------------------------------------------------------------
# 2. TouchInjector.swift — simulator guard
# ---------------------------------------------------------------------------


def test_touch_injector_volume_simulator_guard():
    """TouchInjector.swift must wrap volume button cases with #if targetEnvironment(simulator)."""
    src = (REPO_ROOT / "runner" / "Sources" / "TouchInjector.swift").read_text()
    assert "targetEnvironment(simulator)" in src, (
        "volumeUp/volumeDown cases must be guarded with #if targetEnvironment(simulator)"
    )


def test_touch_injector_volume_throws_in_simulator():
    """The simulator branch must throw, not call XCUIDevice."""
    src = (REPO_ROOT / "runner" / "Sources" / "TouchInjector.swift").read_text()
    assert "volumeUp (unavailable in simulator)" in src
    assert "volumeDown (unavailable in simulator)" in src


# ---------------------------------------------------------------------------
# 3. project.pbxproj — GENERATE_INFOPLIST_FILE
# ---------------------------------------------------------------------------


def test_pbxproj_has_generate_infoplist_file():
    """All buildSettings blocks in project.pbxproj must include GENERATE_INFOPLIST_FILE = YES."""
    src = (REPO_ROOT / "runner" / "SpecterQARunner.xcodeproj" / "project.pbxproj").read_text()
    assert "GENERATE_INFOPLIST_FILE = YES;" in src, (
        "project.pbxproj must contain GENERATE_INFOPLIST_FILE = YES; for Xcode 16 compat"
    )
    # Expect it in all 4 buildSettings blocks (project Debug/Release + target Debug/Release)
    count = src.count("GENERATE_INFOPLIST_FILE = YES;")
    assert count >= 4, f"Expected GENERATE_INFOPLIST_FILE in all 4 buildSettings blocks, found {count}"


# ---------------------------------------------------------------------------
# 4. project_injector.py — builds OUR .xcodeproj, not the user's
# ---------------------------------------------------------------------------


def test_project_injector_uses_runner_project():
    """ProjectInjector.build() must use SpecterQARunner.xcodeproj, not self.project_path."""
    src = (REPO_ROOT / "src" / "specterqa" / "ios" / "project_injector.py").read_text()
    assert "SpecterQARunner.xcodeproj" in src, "build() must reference SpecterQARunner.xcodeproj"
    assert '"SpecterQARunner"' in src, 'build() must use scheme "SpecterQARunner"'


def test_project_injector_validates_runner_project_exists():
    """ProjectInjector.build() must raise ProjectInjectorError if runner .xcodeproj is missing."""
    src = (REPO_ROOT / "src" / "specterqa" / "ios" / "project_injector.py").read_text()
    assert "Runner Xcode project not found" in src, (
        "build() must guard that the runner .xcodeproj exists before proceeding"
    )


def test_project_injector_xcconfig_has_generate_infoplist():
    """build() must append GENERATE_INFOPLIST_FILE = YES to the xcconfig for Xcode 16."""
    src = (REPO_ROOT / "src" / "specterqa" / "ios" / "project_injector.py").read_text()
    assert "GENERATE_INFOPLIST_FILE = YES" in src, (
        "build() must write GENERATE_INFOPLIST_FILE = YES into the generated xcconfig"
    )
