"""INIT-2026-641 Wave 0 — anyio CVE dependency hygiene regression tests.

Item 3.1: the anyio CVE pin. This is Priority 1 alongside the version()
git-drift detector in the same PR (test_version_drift.py): it clears a live
red `Security baseline (pip-audit)` gate on main before anything else lands
this weekend.

The pyproject.toml mcp/Pillow floor-tightening and the retired "60 seconds"
commercial description string land in a separate, lower-priority PR that
does not block anything shipping today.

TDD: written BEFORE the fix. All non-live tests in this file must FAIL on
current code.
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

SIMDRIVE_ROOT = Path(__file__).parent.parent
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


def test_anyio_pin_clears_known_cves() -> None:
    """requirements.lock must pin anyio >= 4.14.2.

    anyio 4.13.0 carries CVE-2026-64847 and CVE-2026-63374, both fixed in
    4.14.2. This is the scheduled `Security baseline (pip-audit)` workflow's
    live red finding on main. Confirmed live before this fix:

        pip-audit -r requirements.lock --vulnerability-service osv
          --ignore-vuln PYSEC-2025-183 --ignore-vuln PYSEC-2026-161 --strict
        -> Found 2 known vulnerabilities in 1 package
           anyio 4.13.0  CVE-2026-64847  Fix: 4.14.2
           anyio 4.13.0  CVE-2026-63374  Fix: 4.14.2

    A separate @pytest.mark.live test below runs the real pip-audit command
    to also catch any other new HIGH/CRITICAL finding beyond this pair.
    """
    text = LOCK_PATH.read_text()
    m = re.search(r"^anyio==([\d.]+)", text, re.MULTILINE)
    assert m is not None, "anyio pin not found in requirements.lock"
    assert _version_tuple(m.group(1)) >= _version_tuple("4.14.2"), (
        f"anyio pinned at {m.group(1)}, which is below 4.14.2 and carries "
        "CVE-2026-64847 and CVE-2026-63374."
    )


@pytest.mark.live
def test_pip_audit_reports_no_high_or_critical_findings() -> None:
    """Regression check mirroring .github/workflows/security.yml exactly.

    Marked `live` (excluded from the default `not live` suite) because it
    shells out to the pip-audit binary and hits the network (OSV). Catches
    any new HIGH/CRITICAL CVE beyond the anyio pair this Wave targets.
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
