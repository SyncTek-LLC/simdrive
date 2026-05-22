"""Regression guard for the 1.0.0b2/b3 PyPI install bug.

In 1.0.0b2 the Wave 2 quota wire-up added::

    from simdrive.cloud.middleware.quotas import check_local_quota

to ``simdrive.server`` at module level. That module had a top-level
``from fastapi import ...`` — which is fine in development (the [dev]
extra installs fastapi) but a fresh ``pip install simdrive`` fails with
``ModuleNotFoundError: fastapi`` because fastapi only lives in the
[cloud] extra.

The fix (1.0.0b4): make the fastapi import lazy — move it inside the
server-side factory functions that actually need it. The client path
(``check_local_quota``, ``LocalQuotaSnapshot``) imports only stdlib +
``simdrive.cloud.errors``.

This test pins that invariant. It masks ``fastapi`` in a subprocess
(fully isolated) and tries the client-side imports there, so the
running pytest process's module table is not disturbed.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest


# Each entry is one assertion: subprocess command that must exit 0 with the
# stated import succeeding under a `fastapi` mask. Using subprocess gives us
# real import-time isolation; manipulating sys.modules in-process leaks
# wiped modules to every test that runs after this file (99 collateral
# failures observed pre-subprocess).
_PROBES = [
    "simdrive",
    "simdrive.server",
    "simdrive.cloud.middleware.quotas",
    "simdrive.cloud.errors",
]


def _src_env() -> dict[str, str]:
    """Build an env that puts the live `src/` ahead of any installed simdrive.

    Without this, the subprocess imports from site-packages, which on dev
    machines may be a stale wheel (pre-fix). Mirrors pytest's
    `pythonpath = ["src"]` setting from pyproject.toml.
    """
    env = os.environ.copy()
    repo_src = Path(__file__).resolve().parent.parent / "src"
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = f"{repo_src}{os.pathsep}{existing}" if existing else str(repo_src)
    return env


def _run_import_probe(target: str) -> subprocess.CompletedProcess:
    """Spawn a Python subprocess with fastapi masked, importing `target`."""
    script = (
        # Make fastapi unimportable in the child.
        "import sys\n"
        "sys.modules['fastapi'] = None\n"
        f"import {target}\n"
        "print('OK')\n"
    )
    return subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        timeout=30,
        env=_src_env(),
    )


@pytest.mark.parametrize("import_target", _PROBES)
def test_client_import_path_does_not_require_fastapi(import_target: str) -> None:
    """A subprocess with fastapi masked must successfully import `target`.

    Spawning a fresh Python process is the only way to get real import-time
    isolation. In-process `sys.modules` munging leaks wiped modules to every
    subsequent test in the session.
    """
    result = _run_import_probe(import_target)
    if result.returncode != 0:
        stderr = result.stderr.strip()
        if "fastapi" in stderr.lower():
            pytest.fail(
                f"`{import_target}` triggered a fastapi import on the client "
                f"path: {stderr}\n\n"
                "fastapi lives in the [cloud] extra; the client surface "
                "(MCP server + license + observe + act) must not require it. "
                "Move the fastapi import inside the function that needs it "
                "(see simdrive.cloud.middleware.quotas.make_quota_gate for "
                "the established pattern)."
            )
        pytest.fail(
            f"Subprocess import of `{import_target}` failed with "
            f"non-fastapi error (returncode={result.returncode}):\n{stderr}"
        )


def test_check_local_quota_callable_without_fastapi() -> None:
    """The Wave 2 dispatcher hook must remain importable AND callable in a
    fastapi-less environment. End-to-end smoke."""
    script = (
        "import sys\n"
        "sys.modules['fastapi'] = None\n"
        "from types import SimpleNamespace\n"
        "from simdrive.cloud.middleware.quotas import (\n"
        "    LocalQuotaSnapshot, check_local_quota,\n"
        ")\n"
        # No-snapshot session -> returns None (no raise).
        "check_local_quota('tap', SimpleNamespace())\n"
        # Construct a snapshot; verify the dataclass works.
        "snap = LocalQuotaSnapshot(tier='free', runs_used=0, runs_limit=10)\n"
        "assert snap.remaining == 10\n"
        "assert not snap.over_limit\n"
        "print('OK')\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True, text=True, timeout=30,
        env=_src_env(),
    )
    assert result.returncode == 0, (
        f"check_local_quota client path failed in fastapi-masked subprocess:\n"
        f"stdout: {result.stdout!r}\n"
        f"stderr: {result.stderr!r}"
    )
    assert result.stdout.strip() == "OK"
