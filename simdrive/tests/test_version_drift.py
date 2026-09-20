"""Regression tests for Bug 2 — _disk_version() reads wrong package name.

server.py:_disk_version() calls importlib.metadata.version("specterqa-ios")
but the package is now named "simdrive".  In the upgrade-residue dogfood environment
the old specterqa-ios 16.0.0a3 wheel was still installed, so _disk_version()
returned "16.0.0a3" — a perpetual mismatch with _LOADED_VERSION "1.0.0a2",
causing a false-positive _simdrive_warning on every single tool call.

Fix required: change the metadata lookup from "specterqa-ios" to "simdrive".

TDD: written BEFORE the fix. All tests must FAIL on current code.
"""
from __future__ import annotations

import importlib.metadata
import json
import subprocess
from unittest.mock import patch

import pytest


class TestDiskVersionReadsSimdrivePackage:

    def test_disk_version_reads_simdrive_package(self) -> None:
        """_disk_version() must query the 'simdrive' package, not 'specterqa-ios'.

        This test patches importlib.metadata.version to intercept which package
        name is looked up. If the lookup uses 'specterqa-ios' (current buggy code),
        the patched function raises PackageNotFoundError for 'specterqa-ios' but
        returns '1.0.0a2' for 'simdrive'.

        After the fix (_disk_version queries 'simdrive'), the function returns
        '1.0.0a2' which matches _LOADED_VERSION.

        Currently FAILS because _disk_version() queries 'specterqa-ios', not 'simdrive'.
        """
        import importlib.metadata
        from simdrive import server
        import simdrive

        # Force a fresh lookup by invalidating the TTL cache
        server._DISK_VERSION_CACHE["checked_at"] = 0.0
        server._DISK_VERSION_CACHE["version"] = None

        original_version = importlib.metadata.version

        def patched_metadata_version(package_name: str) -> str:
            """Accept 'simdrive', reject 'specterqa-ios' (old name)."""
            if package_name == "simdrive":
                return simdrive.__version__
            elif package_name == "specterqa-ios":
                raise importlib.metadata.PackageNotFoundError("specterqa-ios")
            return original_version(package_name)

        with patch("importlib.metadata.version", side_effect=patched_metadata_version):
            # Also invalidate cache inside the patch so it re-queries
            server._DISK_VERSION_CACHE["checked_at"] = 0.0
            server._DISK_VERSION_CACHE["version"] = None
            disk = server._disk_version()

        # After the fix: _disk_version() calls version('simdrive') → '1.0.0a2'
        # Before the fix: _disk_version() calls version('specterqa-ios') → raises
        #   PackageNotFoundError → returns None → test fails the assertion below
        assert disk == simdrive.__version__, (
            f"_disk_version() returned {disk!r} but should return {simdrive.__version__!r}. "
            "This means _disk_version() is still querying 'specterqa-ios' instead of 'simdrive'. "
            "Fix: change `_md.version('specterqa-ios')` to `_md.version('simdrive')` "
            "in server.py:_disk_version()."
        )

    def test_disk_version_old_wheel_causes_false_positive_warning(self) -> None:
        """Simulate the upgrade-residue dogfood environment where specterqa-ios 16.0.0a3
        was installed alongside simdrive 1.0.0a2.

        When _disk_version() queries 'specterqa-ios' and gets '16.0.0a3', it
        returns a version string that doesn't match _LOADED_VERSION '1.0.0a2',
        so _check_version_drift() fires a warning on every tool call.

        This test asserts that _disk_version() does NOT return the old
        specterqa-ios version when the simdrive package is present.

        Currently FAILS: _disk_version() queries 'specterqa-ios' and would
        return '16.0.0a3' if that wheel is present — but even without the old
        wheel installed, the test demonstrates the exact wrong query is made by
        patching it to return the stale version.
        """
        import importlib.metadata
        from simdrive import server
        import simdrive

        # Simulate the upgrade-residue environment: specterqa-ios 16.0.0a3 is installed
        def old_env_metadata_version(package_name: str) -> str:
            if package_name == "specterqa-ios":
                return "16.0.0a3"  # old wheel still present
            elif package_name == "simdrive":
                return simdrive.__version__
            raise importlib.metadata.PackageNotFoundError(package_name)

        with patch("importlib.metadata.version", side_effect=old_env_metadata_version):
            server._DISK_VERSION_CACHE["checked_at"] = 0.0
            server._DISK_VERSION_CACHE["version"] = None
            disk = server._disk_version()

        # On current (buggy) code: disk == "16.0.0a3" (reads specterqa-ios)
        # After fix: disk == "1.0.0a2" (reads simdrive)
        assert disk != "16.0.0a3", (
            f"_disk_version() returned '16.0.0a3' (the old specterqa-ios wheel version). "
            "This confirms _disk_version() is reading the wrong package name. "
            "Fix: change the lookup from 'specterqa-ios' to 'simdrive'."
        )
        assert disk == simdrive.__version__, (
            f"_disk_version() must return the simdrive version ({simdrive.__version__!r}), "
            f"not the specterqa-ios version. Got: {disk!r}"
        )

    def test_check_version_drift_no_false_positive_in_old_wheel_env(self) -> None:
        """In the upgrade-residue dogfood environment (specterqa-ios 16.0.0a3 installed),
        _check_version_drift() must return None (no warning) when simdrive
        is correctly installed as 'simdrive'.

        Currently FAILS: _disk_version() returns '16.0.0a3' via the specterqa-ios
        lookup, causing a spurious version drift warning on every tool call.
        """
        import importlib.metadata
        from simdrive import server
        import simdrive

        def old_env_metadata_version(package_name: str) -> str:
            if package_name == "specterqa-ios":
                return "16.0.0a3"
            elif package_name == "simdrive":
                return simdrive.__version__
            raise importlib.metadata.PackageNotFoundError(package_name)

        with patch("importlib.metadata.version", side_effect=old_env_metadata_version):
            server._DISK_VERSION_CACHE["checked_at"] = 0.0
            server._DISK_VERSION_CACHE["version"] = None
            warning = server._check_version_drift()

        assert warning is None, (
            f"_check_version_drift() returned a false-positive warning in an "
            f"environment where simdrive {simdrive.__version__!r} is correctly installed:\n"
            f"  {warning!r}\n"
            "Root cause: _disk_version() reads 'specterqa-ios' (returned '16.0.0a3') "
            f"instead of 'simdrive' (which would return {simdrive.__version__!r}). "
            "Fix: change the package name in _disk_version()."
        )

    def test_call_tool_injects_warning_in_upgrade_residue_environment(self) -> None:
        """In the upgrade-residue dogfood environment (specterqa-ios 16.0.0a3 still installed),
        every call_tool() response INCORRECTLY gets _simdrive_warning injected
        because _disk_version() returns "16.0.0a3" via the stale package name.

        This test asserts that after the fix (querying 'simdrive' not 'specterqa-ios'),
        call_tool() does NOT inject a warning when the simdrive version matches.

        Currently FAILS: the buggy _disk_version() call gets "16.0.0a3" which
        != "1.0.0a2" (loaded version), so the warning IS injected — but it should
        not be. After the fix, _disk_version() correctly returns "1.0.0a2" from
        the 'simdrive' package, no drift is detected, no warning is injected.
        """
        import importlib.metadata
        from simdrive import server
        import simdrive

        # Simulate the upgrade-residue dogfood case: specterqa-ios 16.0.0a3 installed, simdrive 1.0.0a2 installed
        def residue_env_metadata_version(package_name: str) -> str:
            if package_name == "specterqa-ios":
                return "16.0.0a3"  # old wheel still present → buggy code returns this
            elif package_name == "simdrive":
                return simdrive.__version__  # correct wheel present → fix returns this
            raise importlib.metadata.PackageNotFoundError(package_name)

        with patch("importlib.metadata.version", side_effect=residue_env_metadata_version):
            server._DISK_VERSION_CACHE["checked_at"] = 0.0
            server._DISK_VERSION_CACHE["version"] = None
            result = server.call_tool("version", {})

        assert isinstance(result, dict), "call_tool('version', {}) must return a dict"
        # After the fix: _disk_version() returns '1.0.0a2' (from 'simdrive') == _LOADED_VERSION
        # → no warning injected.
        # Currently (before fix): _disk_version() returns '16.0.0a3' (from 'specterqa-ios')
        # != '1.0.0a2' → warning IS injected → test FAILS.
        assert "_simdrive_warning" not in result, (
            f"call_tool() injected a false-positive _simdrive_warning in the upgrade-residue "
            f"dogfood environment (specterqa-ios 16.0.0a3 + simdrive 1.0.0a2 installed). "
            f"Warning injected: {result.get('_simdrive_warning')!r}\n"
            "Root cause: _disk_version() queries 'specterqa-ios' (returned '16.0.0a3') "
            f"instead of 'simdrive' (which returns {simdrive.__version__!r}). "
            "Fix: change the package name lookup in server.py:_disk_version()."
        )


# ─── INIT-2026-641 Wave 0, item 3.3 ──────────────────────────────────────
#
# _disk_version() compares importlib.metadata.version("simdrive") against
# _LOADED_VERSION. For an EDITABLE install (`pip install -e .`), both reads
# hit the SAME static .dist-info version string, which is baked in at
# install time and never changes just because the working tree's commits
# move. Measured live: a checkout 8 commits behind origin/main, with no
# version bump, reported `drift: false` the whole time while driving every
# iOS repo.
#
# The fix reads git ground truth instead of package metadata for an
# editable install: PEP 610's direct_url.json points at the source tree,
# then `git rev-parse HEAD` / `git status --porcelain` / commits-behind
# `origin/main` tell the real story.
#
# Three states must be distinguishable, never collapsed to two:
#   1. behind by N            -> git_commits_behind: <int >= 0>
#   2. current, verified      -> git_commits_behind: 0
#   3. could not determine    -> git_commits_behind: None, git_state_error set
# State 3 must never render as state 2 (== 0) or as git_sha_drift: False.
#
# TDD: written BEFORE the fix. All tests in this section must FAIL on
# current code (no _editable_install_git_state, no _LOADED_GIT_SHA, no
# extra keys on tool_version's return dict).


def _git(cwd, *args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=str(cwd), capture_output=True, text=True, check=True,
    )
    return result.stdout.strip()


def _init_repo(path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    _git(path, "init", "-b", "main")
    _git(path, "config", "user.email", "test@example.com")
    _git(path, "config", "user.name", "Test")


def _commit_file(repo, name: str, content: str) -> str:
    (repo / name).write_text(content)
    _git(repo, "add", name)
    _git(repo, "commit", "-m", f"add {name}")
    return _git(repo, "rev-parse", "HEAD")


def _make_origin_and_clone(tmp_path):
    """A seeded origin repo plus a clone of it (the 'editable install' checkout)."""
    origin = tmp_path / "origin"
    _init_repo(origin)
    _commit_file(origin, "seed.txt", "seed")

    work = tmp_path / "work"
    _git(tmp_path, "clone", str(origin), str(work))
    _git(work, "config", "user.email", "test@example.com")
    _git(work, "config", "user.name", "Test")
    return origin, work


def _patch_direct_url(monkeypatch, source_dir, editable: bool = True) -> None:
    payload = json.dumps({"dir_info": {"editable": editable}, "url": source_dir.as_uri()})

    class _FakeDist:
        def read_text(self, name: str) -> str:
            if name == "direct_url.json":
                return payload
            raise FileNotFoundError(name)

    monkeypatch.setattr(importlib.metadata, "distribution", lambda _name: _FakeDist())


class TestEditableInstallGitState:
    """Unit tests for the new helper, `server._editable_install_git_state()`."""

    def test_returns_none_for_non_editable_install(self, monkeypatch) -> None:
        """A normal (non-editable) install has no direct_url.json editable flag;
        the existing importlib.metadata version comparison already covers it
        correctly, so this function has nothing to add and must return None."""
        class _FakeDist:
            def read_text(self, name: str) -> str:
                raise FileNotFoundError(name)

        monkeypatch.setattr(importlib.metadata, "distribution", lambda _n: _FakeDist())

        from simdrive import server
        assert server._editable_install_git_state() is None

    def test_reports_current_verified_when_checkout_matches_origin_main(
        self, tmp_path, monkeypatch,
    ) -> None:
        """State 2: current, genuinely verified. commits_behind must be
        exactly 0, not None, and no error must be reported."""
        _origin, work = _make_origin_and_clone(tmp_path)
        _patch_direct_url(monkeypatch, work)

        from simdrive import server
        state = server._editable_install_git_state()

        assert state["editable"] is True
        assert state["git_state_error"] is None
        assert state["git_commits_behind"] == 0
        assert state["working_tree_dirty"] is False
        assert state["git_sha_disk"] == _git(work, "rev-parse", "HEAD")

    def test_reports_behind_by_n_when_origin_moves_ahead(
        self, tmp_path, monkeypatch,
    ) -> None:
        """State 1: behind by N. The working tree does not pull; only
        `git fetch` updates local knowledge of origin/main."""
        origin, work = _make_origin_and_clone(tmp_path)
        for i in range(3):
            _commit_file(origin, f"file{i}.txt", str(i))
        _git(work, "fetch", "origin")
        _patch_direct_url(monkeypatch, work)

        from simdrive import server
        state = server._editable_install_git_state()

        assert state["git_state_error"] is None
        assert state["git_commits_behind"] == 3

    def test_working_tree_dirty_detected(self, tmp_path, monkeypatch) -> None:
        _origin, work = _make_origin_and_clone(tmp_path)
        (work / "seed.txt").write_text("modified, uncommitted")
        _patch_direct_url(monkeypatch, work)

        from simdrive import server
        state = server._editable_install_git_state()

        assert state["working_tree_dirty"] is True

    def test_undetermined_when_no_origin_remote(self, tmp_path, monkeypatch) -> None:
        """State 3: could not determine. No remote at all -- must not be
        reported as 0 commits behind."""
        work = tmp_path / "work"
        _init_repo(work)
        _commit_file(work, "a.txt", "a")
        _patch_direct_url(monkeypatch, work)

        from simdrive import server
        state = server._editable_install_git_state()

        assert state["git_commits_behind"] is None
        assert state["git_state_error"] is not None
        assert "origin" in state["git_state_error"].lower()
        # sha and dirty are independently knowable even with no remote
        assert state["git_sha_disk"] is not None
        assert state["working_tree_dirty"] is False

    def test_undetermined_when_no_origin_main_ref(self, tmp_path, monkeypatch) -> None:
        """State 3: an origin remote is configured but has never been
        fetched, so no local origin/main ref exists to compare against."""
        origin = tmp_path / "origin"
        _init_repo(origin)
        _commit_file(origin, "seed.txt", "seed")

        work = tmp_path / "work"
        _init_repo(work)
        _commit_file(work, "a.txt", "a")
        _git(work, "remote", "add", "origin", str(origin))
        # deliberately never fetched: no refs/remotes/origin/main locally
        _patch_direct_url(monkeypatch, work)

        from simdrive import server
        state = server._editable_install_git_state()

        assert state["git_commits_behind"] is None
        assert state["git_state_error"] is not None

    def test_undetermined_when_not_a_git_checkout(self, tmp_path, monkeypatch) -> None:
        """State 3: direct_url.json points somewhere real, but it is not a
        git checkout at all."""
        not_a_repo = tmp_path / "not_a_repo"
        not_a_repo.mkdir()
        _patch_direct_url(monkeypatch, not_a_repo)

        from simdrive import server
        state = server._editable_install_git_state()

        assert state["git_sha_disk"] is None
        assert state["git_commits_behind"] is None
        assert state["git_state_error"] is not None


class TestToolVersionGitDrift:
    """Integration tests: `tool_version()`'s extended return dict."""

    def test_editable_install_reports_git_drift(self, tmp_path, monkeypatch) -> None:
        """The core acceptance test named in the architecture plan. A process
        loaded at commit A, whose disk checkout has since moved to commit B,
        must report git_sha_drift: True -- not the version-string `drift`
        key, which stays False the whole time on an editable install since
        the version string never changes."""
        origin, work = _make_origin_and_clone(tmp_path)
        sha_a = _git(work, "rev-parse", "HEAD")

        from simdrive import server
        _patch_direct_url(monkeypatch, work)
        monkeypatch.setattr(server, "_LOADED_GIT_SHA", sha_a)

        _commit_file(origin, "b.txt", "b")
        _git(work, "pull", "origin", "main")
        sha_b = _git(work, "rev-parse", "HEAD")
        assert sha_b != sha_a

        result = server.tool_version({})

        assert result["editable"] is True
        assert result["git_sha_loaded"] == sha_a
        assert result["git_sha_disk"] == sha_b
        assert result["git_sha_drift"] is True
        assert result["working_tree_dirty"] is False
        assert result["git_commits_behind"] == 0
        assert result["git_state_error"] is None
        # The pre-fix behavior this regression-tests: version-string drift
        # stays false the whole time for an editable install.
        assert result["drift"] is False

    def test_no_drift_when_disk_matches_loaded_sha(self, tmp_path, monkeypatch) -> None:
        _origin, work = _make_origin_and_clone(tmp_path)
        sha = _git(work, "rev-parse", "HEAD")

        from simdrive import server
        _patch_direct_url(monkeypatch, work)
        monkeypatch.setattr(server, "_LOADED_GIT_SHA", sha)

        result = server.tool_version({})
        assert result["git_sha_drift"] is False
        assert result["git_commits_behind"] == 0

    def test_never_reports_current_or_false_drift_when_undetermined(
        self, tmp_path, monkeypatch,
    ) -> None:
        """The critical guard: a detector that cannot determine the answer
        must never render as 'current' (commits_behind == 0) or as
        'no drift' (git_sha_drift is False). Both must be None, with
        git_state_error explaining why."""
        not_a_repo = tmp_path / "not_a_repo"
        not_a_repo.mkdir()

        from simdrive import server
        _patch_direct_url(monkeypatch, not_a_repo)
        monkeypatch.setattr(server, "_LOADED_GIT_SHA", "deadbeefdeadbeefdeadbeefdeadbeefdeadbeef")

        result = server.tool_version({})

        assert result["editable"] is True
        assert result["git_commits_behind"] is None
        assert result["git_sha_drift"] is None
        assert result["git_state_error"] is not None
        assert result["git_commits_behind"] != 0
        assert result["git_sha_drift"] is not False

    def test_non_editable_install_reports_editable_false_with_no_error(
        self, monkeypatch,
    ) -> None:
        """A normal pip install: nothing new to report, and 'nothing to
        report' must not be confused with 'could not determine'."""
        from simdrive import server
        monkeypatch.setattr(server, "_editable_install_git_state", lambda: None)

        result = server.tool_version({})

        assert result["editable"] is False
        assert result["git_sha_disk"] is None
        assert result["git_commits_behind"] is None
        assert result["git_sha_drift"] is None
        assert result["git_state_error"] is None
