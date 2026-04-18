"""Gap test — B2: hash-based rebuild gate.

Tests that _needs_rebuild() uses a content-hash of Sources/ + pbxproj rather
than the version string.  Verifies the regression case: same hash but different
.specterqa-version MUST return False (no rebuild needed).

Run:
    pytest tests/regression/test_rebuild_trigger.py -v
"""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Helpers to locate the runner source tree shipped in the package
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).parent.parent.parent


# ---------------------------------------------------------------------------
# Import the functions under test
# ---------------------------------------------------------------------------

from specterqa.ios.session_manager import _needs_rebuild, _compute_runner_source_hash  # noqa: E402


def _compute_runner_hash() -> str:
    """Delegate to the session_manager's canonical hash function.

    Using the same function that _needs_rebuild() uses ensures the test always
    writes the correct hash (rather than a re-implementation that could drift).
    """
    return _compute_runner_source_hash()


# ---------------------------------------------------------------------------
# Test 1: matching hash → no rebuild (core invariant)
# ---------------------------------------------------------------------------


def test_no_rebuild_when_hash_matches(tmp_path: Path) -> None:
    """If .runner-hash matches current sources, _needs_rebuild() must return False."""
    build_dir = tmp_path / "runner-build"
    build_dir.mkdir()

    current_hash = _compute_runner_hash()
    (build_dir / ".runner-hash").write_text(current_hash + "\n")

    # Also create a fake xctestrun so the "missing xctestrun" branch doesn't fire
    fake_run = build_dir / "SpecterQARunnerTests.xctestrun"
    fake_run.write_text("<plist/>")

    assert not _needs_rebuild(build_dir), (
        "_needs_rebuild() must return False when .runner-hash matches current Sources/"
    )


# ---------------------------------------------------------------------------
# Test 2: different .specterqa-version but matching hash → no rebuild (regression)
# ---------------------------------------------------------------------------


def test_no_rebuild_when_version_differs_but_hash_matches(tmp_path: Path) -> None:
    """B2 regression: a patch version bump must NOT trigger a rebuild when sources are unchanged."""
    build_dir = tmp_path / "runner-build"
    build_dir.mkdir()

    current_hash = _compute_runner_hash()
    (build_dir / ".runner-hash").write_text(current_hash + "\n")
    # Simulate a stale version marker from the previous version
    (build_dir / ".specterqa-version").write_text("13.1.0\n")

    fake_run = build_dir / "SpecterQARunnerTests.xctestrun"
    fake_run.write_text("<plist/>")

    assert not _needs_rebuild(build_dir), (
        "B2 regression: version-string mismatch alone must NOT trigger rebuild "
        "when the runner-hash matches current Sources/. "
        "This is the exact bug in v13.2.0."
    )


# ---------------------------------------------------------------------------
# Test 3: hash mismatch (Swift source changed) → rebuild required
# ---------------------------------------------------------------------------


def test_rebuild_when_hash_mismatches(tmp_path: Path) -> None:
    """Modified sources → different hash → _needs_rebuild() must return True."""
    build_dir = tmp_path / "runner-build"
    build_dir.mkdir()

    # Write a deliberately wrong hash
    (build_dir / ".runner-hash").write_text("0" * 64 + "\n")

    fake_run = build_dir / "SpecterQARunnerTests.xctestrun"
    fake_run.write_text("<plist/>")

    assert _needs_rebuild(build_dir), (
        "_needs_rebuild() must return True when .runner-hash does not match current Sources/"
    )


# ---------------------------------------------------------------------------
# Test 4: missing .xctestrun → rebuild required
# ---------------------------------------------------------------------------


def test_rebuild_when_xctestrun_missing(tmp_path: Path) -> None:
    """Even if the hash matches, a missing .xctestrun must trigger a rebuild."""
    build_dir = tmp_path / "runner-build"
    build_dir.mkdir()

    current_hash = _compute_runner_hash()
    (build_dir / ".runner-hash").write_text(current_hash + "\n")
    # No .xctestrun file created

    assert _needs_rebuild(build_dir), (
        "_needs_rebuild() must return True when no .xctestrun file is present"
    )


# ---------------------------------------------------------------------------
# Test 5: missing .runner-hash (first run / migration) → rebuild required
# ---------------------------------------------------------------------------


def test_rebuild_when_no_hash_file(tmp_path: Path) -> None:
    """No .runner-hash file at all (fresh install or upgrade from 13.2.0) → rebuild."""
    build_dir = tmp_path / "runner-build"
    build_dir.mkdir()

    fake_run = build_dir / "SpecterQARunnerTests.xctestrun"
    fake_run.write_text("<plist/>")

    assert _needs_rebuild(build_dir), (
        "_needs_rebuild() must return True when .runner-hash is absent "
        "(new install or migration from version-marker scheme)"
    )
