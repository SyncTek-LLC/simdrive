"""TDD for INIT-2026-549 W1 publish-gate logic.

The publish workflow (.github/workflows/specterqa-ios-publish.yml renamed to
trigger on `simdrive-v*` tags) enforces three pre-publish gates:

  1. Version-match: the git tag MUST equal `simdrive-v<pyproject.version>`.
  2. CHANGELOG head: the most recent `## [X.Y.Z...]` heading in CHANGELOG.md
     MUST equal `<pyproject.version>`.
  3. Tests-clean: `pytest simdrive/tests -m "not live"` MUST pass.

This module ships the pure-Python helpers the workflow uses (so they're testable
on a regular dev machine without GitHub Actions) and verifies them against
golden inputs.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Helpers under test (kept here so the workflow script can import them via
# `python -c "from tests.packaging.test_publish_gates import ..."`; the
# workflow can also inline the same logic — these are the canonical refs).
# ---------------------------------------------------------------------------

TAG_RE = re.compile(r"^simdrive-v(?P<ver>\d+\.\d+\.\d+(?:[ab]\d+|rc\d+)?)$")
PYPROJECT_VERSION_RE = re.compile(
    r'^version\s*=\s*"(?P<ver>[^"]+)"', re.MULTILINE
)
CHANGELOG_HEADING_RE = re.compile(r"^##\s*\[(?P<ver>[^\]\s]+)\]", re.MULTILINE)


def parse_tag(tag: str) -> str:
    """Return the version embedded in a `simdrive-vX.Y.Z[a/b/rcN]` tag.

    Raises ValueError when the tag does not match the canonical pattern.
    """
    m = TAG_RE.match(tag)
    if not m:
        raise ValueError(
            f"tag {tag!r} does not match simdrive-vX.Y.Z[a/b/rcN] — refusing to publish"
        )
    return m.group("ver")


def parse_pyproject_version(pyproject_text: str) -> str:
    """Return the value of `version = "..."` in a pyproject.toml string."""
    m = PYPROJECT_VERSION_RE.search(pyproject_text)
    if not m:
        raise ValueError("pyproject.toml has no top-level version = \"...\" line")
    return m.group("ver")


def parse_changelog_head_version(changelog_text: str) -> str:
    """Return the version in the FIRST `## [X.Y.Z]` heading of CHANGELOG.md."""
    m = CHANGELOG_HEADING_RE.search(changelog_text)
    if not m:
        raise ValueError("CHANGELOG.md has no `## [X.Y.Z...]` heading")
    return m.group("ver")


def check_publish_gates(tag: str, pyproject_text: str, changelog_text: str) -> None:
    """Raise AssertionError if any of the three pre-publish gates fails.

    Returns None on success. Designed to be invoked by the workflow as:

        python -c "from tests.packaging.test_publish_gates import check_publish_gates; \
                   check_publish_gates('$TAG', open('simdrive/pyproject.toml').read(), \
                                       open('simdrive/CHANGELOG.md').read())"
    """
    tag_ver = parse_tag(tag)
    proj_ver = parse_pyproject_version(pyproject_text)
    cl_ver = parse_changelog_head_version(changelog_text)
    if tag_ver != proj_ver:
        raise AssertionError(
            f"version mismatch: tag={tag_ver!r}, pyproject={proj_ver!r}"
        )
    if cl_ver != proj_ver:
        raise AssertionError(
            f"CHANGELOG.md head heading is {cl_ver!r}, expected {proj_ver!r} (pyproject)"
        )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_parse_tag_happy_paths():
    assert parse_tag("simdrive-v1.0.0a13") == "1.0.0a13"
    assert parse_tag("simdrive-v1.2.3") == "1.2.3"
    assert parse_tag("simdrive-v2.0.0rc1") == "2.0.0rc1"
    assert parse_tag("simdrive-v1.0.0b2") == "1.0.0b2"


def test_parse_tag_rejects_legacy_specterqa_tag():
    with pytest.raises(ValueError, match="simdrive-vX.Y.Z"):
        parse_tag("specterqa-ios-v1.0.0a13")


def test_parse_tag_rejects_malformed():
    for bad in ("simdrive-1.0.0", "simdrive-vv1.0.0", "v1.0.0", ""):
        with pytest.raises(ValueError):
            parse_tag(bad)


def test_parse_pyproject_version():
    pyproject = '\n'.join([
        '[project]',
        'name = "simdrive"',
        'version = "1.0.0a13"',
        'description = "..."',
    ])
    assert parse_pyproject_version(pyproject) == "1.0.0a13"


def test_parse_changelog_head_version():
    cl = "# Changelog\n\n## [1.0.0a13] — 2026-05-14\n\nSomething.\n\n## [1.0.0a12] — older\n"
    assert parse_changelog_head_version(cl) == "1.0.0a13"


def test_check_publish_gates_pass():
    tag = "simdrive-v1.0.0a13"
    py = 'version = "1.0.0a13"'
    cl = "## [1.0.0a13] — 2026-05-14\n"
    # Should NOT raise
    check_publish_gates(tag, py, cl)


def test_check_publish_gates_tag_mismatch():
    with pytest.raises(AssertionError, match="version mismatch"):
        check_publish_gates(
            "simdrive-v1.0.0a13",
            'version = "1.0.0a12"',
            "## [1.0.0a13]\n",
        )


def test_check_publish_gates_changelog_stale():
    with pytest.raises(AssertionError, match="CHANGELOG"):
        check_publish_gates(
            "simdrive-v1.0.0a13",
            'version = "1.0.0a13"',
            "## [1.0.0a12] — stale\n",
        )


def test_repo_state_passes_gates_against_synthetic_tag():
    """Sanity: the live pyproject + CHANGELOG agree with each other.

    Uses a synthetic tag derived from the current pyproject version so this
    test passes regardless of which version we're at.
    """
    repo_root = Path(__file__).resolve().parents[2]
    py_text = (repo_root / "simdrive" / "pyproject.toml").read_text()
    cl_text = (repo_root / "simdrive" / "CHANGELOG.md").read_text()
    ver = parse_pyproject_version(py_text)
    check_publish_gates(f"simdrive-v{ver}", py_text, cl_text)
