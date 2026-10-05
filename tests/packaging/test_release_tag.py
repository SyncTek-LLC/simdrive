"""FU-2026-072: publish workflow tag derivation (push tag vs workflow_dispatch)."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import release_tag  # noqa: E402  (RED: module does not exist on main)

PY = 'version = "1.0.0a13"\n'


def test_push_tag_is_taken_from_ref():
    assert (
        release_tag.derive_tag("push", "refs/tags/simdrive-v1.0.0a13", PY)
        == "simdrive-v1.0.0a13"
    )


def test_workflow_dispatch_derives_tag_from_pyproject():
    # GITHUB_REF is a branch under workflow_dispatch; must not leak into the tag.
    assert (
        release_tag.derive_tag("workflow_dispatch", "refs/heads/main", PY)
        == "simdrive-v1.0.0a13"
    )


def test_push_tag_mismatching_pyproject_fails():
    with pytest.raises(ValueError, match="mismatch"):
        release_tag.derive_tag("push", "refs/tags/simdrive-v1.0.0a12", PY)


def test_push_of_non_tag_ref_fails():
    with pytest.raises(ValueError):
        release_tag.derive_tag("push", "refs/heads/main", PY)


def test_unsupported_event_fails():
    with pytest.raises(ValueError, match="event"):
        release_tag.derive_tag("pull_request", "refs/pull/1/merge", PY)


def test_cli_prints_tag(tmp_path):
    pp = tmp_path / "pyproject.toml"
    pp.write_text(PY)
    out = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "release_tag.py"),
         "--event", "workflow_dispatch", "--ref", "refs/heads/main",
         "--pyproject", str(pp)],
        capture_output=True, text=True, check=True,
    )
    assert out.stdout.strip() == "simdrive-v1.0.0a13"


def test_workflow_uses_script_and_guards_publish():
    wf = (ROOT / ".github/workflows/specterqa-ios-publish.yml").read_text()
    assert "scripts/release_tag.py" in wf
    assert "#refs/tags/" not in wf  # no unconditional prefix-strip of GITHUB_REF
    assert "removeprefix('refs/tags/')" not in wf
    assert "!inputs.dry_run" in wf
