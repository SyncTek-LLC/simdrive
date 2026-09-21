"""INIT-2026-641 Wave 0 — dependency hygiene regression tests.

Item 3.1 (the anyio CVE pin) landed in feat/641-wave0-version-and-anyio,
merged as PR #183, alongside the version() editable-install drift
detector, since that pair blocked a live red `Security baseline
(pip-audit)` gate and protected everything else merging that weekend.
test_anyio_pin_clears_known_cves and test_pip_audit_reports_no_high_or_
critical_findings below are that PR's tests, carried forward as-is.

Items 3.2/3.4 (this PR): the mcp/Pillow floor-tightening that keeps a
future `pip-compile` regen from re-opening the exact hole a lock-only CVE
bump leaves behind, and the retired "60 seconds" commercial performance
claim in the PyPI description.

TDD: the mcp/Pillow/description tests were written BEFORE their fix and
confirmed failing against then-current pyproject.toml. The anyio test was
already green on main going into this rebase (PR #183 merged first).
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

SIMDRIVE_ROOT = Path(__file__).parent.parent
PYPROJECT_PATH = SIMDRIVE_ROOT / "pyproject.toml"
LOCK_PATH = SIMDRIVE_ROOT / "requirements.lock"


def _version_tuple(v: str) -> tuple[int, ...]:
    """Parse a dotted version string's leading numeric components.

    Stdlib-only, no `packaging` dependency: `packaging` is not itself pinned
    in requirements.lock, so a hermetic test must not rely on an unpinned
    transitive package happening to be importable in the dev environment
    (see server.py::_is_upgrade, which has the same constraint and falls
    back conservatively when packaging is unavailable).
    """
    nums = re.findall(r"\d+", v.split("+")[0])
    return tuple(int(n) for n in nums) if nums else (0,)


def _load_project_dependencies() -> list[str]:
    """Raw dependency strings from [project.dependencies], stdlib-only regex."""
    text = PYPROJECT_PATH.read_text()
    m = re.search(r"\[project\]\s*.*?dependencies\s*=\s*\[([^\]]*)\]", text, re.DOTALL)
    assert m is not None, "Could not find [project] dependencies in pyproject.toml"
    block = m.group(1)
    deps: list[str] = []
    for line in block.splitlines():
        line = line.strip().strip('",').strip("',")
        line = line.split("#")[0].strip()
        if not line:
            continue
        deps.append(line)
    return deps


def _spec_for(deps: list[str], name: str) -> str:
    for d in deps:
        if re.match(rf"^{re.escape(name)}\s*[><=!\[]", d, re.IGNORECASE):
            return d
    raise AssertionError(f"{name!r} not found in [project.dependencies]: {deps}")


def test_anyio_pin_clears_known_cves() -> None:
    """requirements.lock must pin anyio >= 4.14.2.

    anyio 4.13.0 carried CVE-2026-64847 and CVE-2026-63374, both fixed in
    4.14.2. This was the scheduled `Security baseline (pip-audit)`
    workflow's live red finding on main; PR #183 cleared it.

    A separate @pytest.mark.live test below runs the real pip-audit
    command to also catch any other new HIGH/CRITICAL finding.
    """
    text = LOCK_PATH.read_text()
    m = re.search(r"^anyio==([\d.]+)", text, re.MULTILINE)
    assert m is not None, "anyio pin not found in requirements.lock"
    assert _version_tuple(m.group(1)) >= _version_tuple("4.14.2"), (
        f"anyio pinned at {m.group(1)}, which is below 4.14.2 and carries "
        "CVE-2026-64847 and CVE-2026-63374."
    )


def test_mcp_dependency_has_upper_bound() -> None:
    """pyproject.toml's mcp pin must have both a floor and a ceiling.

    `mcp>=1.0` with no ceiling lets pip-compile re-resolve a different mcp
    (and therefore a different transitive anyio) on the next lock
    regeneration, silently undoing the anyio CVE fix above. The floor must
    be at least 1.28.1: the version already in requirements.lock, and the
    one that carries the CVE-cleared transitive dependency range. Commit
    85d98fd cleared 24 HIGH CVEs by bumping mcp/pillow in the lock alone,
    without ever tightening this pin, which is exactly why a later
    editable-install reinstall could silently drift back down.
    """
    deps = _load_project_dependencies()
    spec = _spec_for(deps, "mcp")
    assert "<" in spec, f"mcp has no upper bound: {spec!r}"
    floor_match = re.search(r">=\s*([\d.]+)", spec)
    assert floor_match is not None, f"mcp has no lower bound: {spec!r}"
    assert _version_tuple(floor_match.group(1)) >= _version_tuple("1.28.1"), (
        f"mcp floor {floor_match.group(1)} is below 1.28.1, the CVE-cleared "
        f"version; got spec {spec!r}"
    )


def test_pillow_dependency_has_upper_bound_and_cve_floor() -> None:
    """Same defect, same fix, for Pillow.

    `Pillow>=10.0` with no ceiling is what let commit 85d98fd's lock-only
    bump to 12.3.0 (clearing PYSEC-2026-2253/2254/2255/2257/3451, all HIGH)
    go unenforced at the pyproject level; a plain `pip install -e .` still
    resolves whatever `>=10.0` allows, including the vulnerable versions
    that bump specifically moved away from.
    """
    deps = _load_project_dependencies()
    spec = _spec_for(deps, "Pillow")
    assert "<" in spec, f"Pillow has no upper bound: {spec!r}"
    floor_match = re.search(r">=\s*([\d.]+)", spec)
    assert floor_match is not None, f"Pillow has no lower bound: {spec!r}"
    assert _version_tuple(floor_match.group(1)) >= _version_tuple("12.3.0"), (
        f"Pillow floor {floor_match.group(1)} is below 12.3.0, the CVE-cleared "
        f"version; got spec {spec!r}"
    )


def test_pyproject_description_has_no_speed_claim() -> None:
    """The PyPI description must not carry the retired '60 seconds' pitch.

    This initiative's own measured baseline falsifies it: a single
    observe(annotate=true) call alone costs 6.36s server-side, and a
    10-step journey measures 45-64s. The product's commercial retirement
    (INIT-2026-605) makes this a leftover, not a design decision anyone
    defends.
    """
    text = PYPROJECT_PATH.read_text()
    m = re.search(r'description\s*=\s*"([^"]*)"', text)
    assert m is not None, "Could not find [project] description in pyproject.toml"
    description = m.group(1)
    assert not re.search(r"\d+\s*seconds?", description, re.IGNORECASE), (
        f"pyproject.toml description still carries a speed claim: {description!r}"
    )


@pytest.mark.live
def test_pip_audit_reports_no_high_or_critical_findings() -> None:
    """Regression check mirroring .github/workflows/security.yml exactly.

    Marked `live` (excluded from the default `not live` suite) because it
    shells out to the pip-audit binary and hits the network (OSV). Catches
    any new HIGH/CRITICAL CVE beyond the anyio pair PR #183 targeted.
    """
    result = subprocess.run(
        [
            sys.executable, "-m", "pip_audit",
            "-r", str(LOCK_PATH),
            "--vulnerability-service", "osv",
            "--ignore-vuln", "PYSEC-2025-183",
            "--ignore-vuln", "PYSEC-2026-161",
            "--strict",
        ],
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert result.returncode == 0, (
        f"pip-audit found blocking vulnerabilities:\nstdout:\n{result.stdout}\n"
        f"stderr:\n{result.stderr}"
    )
