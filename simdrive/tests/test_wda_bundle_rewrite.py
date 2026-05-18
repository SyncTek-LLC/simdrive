"""Tests for simdrive.wda.bootstrap.patch_wda_bundle_id.

These tests MUST fail on feat/v17-claude-native HEAD (function does not exist)
and PASS after feat/simdrive-a10-zero-config-bootstrap is merged.

Uses tmp_path fixtures — no real WDA source required.
"""
from __future__ import annotations

from pathlib import Path

import pytest


# ── fixtures ──────────────────────────────────────────────────────────────────

# A minimal project.pbxproj that mirrors the real WDA format.
# Both WebDriverAgentRunner targets are represented:
#   - The main runner target (used by xcodebuild build-for-testing)
#   - The .xctrunner bundle (auto-created by xcodebuild for -testing artefacts)
#
# Real WDA uses tabs for indentation; we match that here.
_MINIMAL_PBXPROJ = """\
// !$*UTF8*$!
{
	archiveVersion = 1;
	classes = {
	};
	objectVersion = 56;
	objects = {

/* Begin XCBuildConfiguration section */
		A1B2C3D4E5F60001 /* Debug */ = {
			isa = XCBuildConfiguration;
			buildSettings = {
				PRODUCT_BUNDLE_IDENTIFIER = com.facebook.WebDriverAgentRunner;
				PRODUCT_NAME = "$(TARGET_NAME)";
				SDKROOT = iphoneos;
			};
			name = Debug;
		};
		A1B2C3D4E5F60002 /* Release */ = {
			isa = XCBuildConfiguration;
			buildSettings = {
				PRODUCT_BUNDLE_IDENTIFIER = com.facebook.WebDriverAgentRunner;
				PRODUCT_NAME = "$(TARGET_NAME)";
				SDKROOT = iphoneos;
			};
			name = Release;
		};
/* End XCBuildConfiguration section */
	};
	rootObject = A1B2C3D4E5F60000 /* Project object */;
}
"""


def _make_wda_source(base: Path, content: str = _MINIMAL_PBXPROJ) -> Path:
    """Create a minimal WDA source directory with project.pbxproj."""
    xcodeproj = base / "WebDriverAgent.xcodeproj"
    xcodeproj.mkdir(parents=True, exist_ok=True)
    pbxproj = xcodeproj / "project.pbxproj"
    pbxproj.write_text(content, encoding="utf-8")
    return base


# ── tests ─────────────────────────────────────────────────────────────────────


def test_rewrite_patches_both_targets(tmp_path):
    """patch_wda_bundle_id rewrites all PRODUCT_BUNDLE_IDENTIFIER lines for WebDriverAgentRunner."""
    source_dir = _make_wda_source(tmp_path)

    from simdrive.wda.bootstrap import patch_wda_bundle_id
    new_id = patch_wda_bundle_id(source_dir, "E52N8732YT")

    # The returned ID must follow the agreed scheme
    assert new_id == "co.synctek.simdrive.wda.e52n8732yt", (
        f"Expected 'co.synctek.simdrive.wda.e52n8732yt', got {new_id!r}"
    )

    pbxproj = (source_dir / "WebDriverAgent.xcodeproj" / "project.pbxproj").read_text(encoding="utf-8")

    # All old facebook bundle IDs must be gone from PRODUCT_BUNDLE_IDENTIFIER lines
    import re
    old_lines = [
        line for line in pbxproj.splitlines()
        if "PRODUCT_BUNDLE_IDENTIFIER" in line and "com.facebook.WebDriverAgentRunner" in line
    ]
    assert len(old_lines) == 0, (
        f"Found {len(old_lines)} unrewritten com.facebook.WebDriverAgentRunner lines: {old_lines}"
    )

    # All targets must now use the new bundle ID
    new_lines = [
        line for line in pbxproj.splitlines()
        if "PRODUCT_BUNDLE_IDENTIFIER" in line and new_id in line
    ]
    # Both Debug and Release configs must be patched
    assert len(new_lines) == 2, (
        f"Expected 2 patched PRODUCT_BUNDLE_IDENTIFIER lines, found {len(new_lines)}: {new_lines}"
    )


def test_rewrite_idempotent(tmp_path):
    """Calling patch_wda_bundle_id twice with same team produces identical output."""
    source_dir = _make_wda_source(tmp_path)

    from simdrive.wda.bootstrap import patch_wda_bundle_id
    first_id = patch_wda_bundle_id(source_dir, "E52N8732YT")
    content_after_first = (source_dir / "WebDriverAgent.xcodeproj" / "project.pbxproj").read_text(encoding="utf-8")

    second_id = patch_wda_bundle_id(source_dir, "E52N8732YT")
    content_after_second = (source_dir / "WebDriverAgent.xcodeproj" / "project.pbxproj").read_text(encoding="utf-8")

    assert first_id == second_id, "Second call returned a different bundle ID"
    assert content_after_first == content_after_second, (
        "File contents differ after second call — rewrite is not idempotent"
    )


def test_different_team_overwrites_previous(tmp_path):
    """patch_wda_bundle_id with a different team overwrites the prior team's value."""
    source_dir = _make_wda_source(tmp_path)

    from simdrive.wda.bootstrap import patch_wda_bundle_id
    # First team
    first_id = patch_wda_bundle_id(source_dir, "E52N8732YT")
    assert first_id == "co.synctek.simdrive.wda.e52n8732yt"

    # Second (different) team
    second_id = patch_wda_bundle_id(source_dir, "XYZ9876543")
    assert second_id == "co.synctek.simdrive.wda.xyz9876543"

    pbxproj = (source_dir / "WebDriverAgent.xcodeproj" / "project.pbxproj").read_text(encoding="utf-8")

    # No trace of the first team's value should remain in PRODUCT_BUNDLE_IDENTIFIER lines
    stale_lines = [
        line for line in pbxproj.splitlines()
        if "PRODUCT_BUNDLE_IDENTIFIER" in line and first_id in line
    ]
    assert len(stale_lines) == 0, (
        f"First team bundle ID still present after second rewrite: {stale_lines}"
    )

    # All PRODUCT_BUNDLE_IDENTIFIER lines for WebDriverAgentRunner should now reference second_id
    new_lines = [
        line for line in pbxproj.splitlines()
        if "PRODUCT_BUNDLE_IDENTIFIER" in line and second_id in line
    ]
    assert len(new_lines) == 2, (
        f"Expected 2 lines with second team bundle ID, found {len(new_lines)}: {new_lines}"
    )


def test_missing_pbxproj_returns_expected_id(tmp_path):
    """If project.pbxproj does not exist, patch_wda_bundle_id returns the expected ID without crashing."""
    # Source dir without WebDriverAgent.xcodeproj
    source_dir = tmp_path / "empty_source"
    source_dir.mkdir()

    from simdrive.wda.bootstrap import patch_wda_bundle_id
    result = patch_wda_bundle_id(source_dir, "ABSENT123X")
    assert result == "co.synctek.simdrive.wda.absent123x", (
        f"Expected graceful fallback ID, got {result!r}"
    )
