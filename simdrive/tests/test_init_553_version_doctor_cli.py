"""INIT-2026-553 P2 — `simdrive version` / `simdrive doctor` CLI surface.

Covers the new flags added so consumer monorepos can pin a simdrive release
and get a clean diagnostic on mismatch instead of an opaque exit-3.

Scenarios:
  - `simdrive version`                       → "simdrive <ver>"
  - `simdrive version --json`                → JSON payload {version, package}
  - `simdrive version --required-version X`  → exit 0 when X == installed
                                              → exit 3 with pip hint when not
  - `simdrive doctor --json`                 → JSON {ok, version, checks}
  - `simdrive doctor --required-version X`   → exit 3 dominates check failures
  - Legacy `simdrive --version`              → still works, delegates to new
                                              handler (so --json/--required
                                              work via the short flag too)
"""
from __future__ import annotations

import io
import json
import sys
from contextlib import redirect_stderr, redirect_stdout
from unittest.mock import patch

import pytest

from simdrive import __version__, server


# ────────────────────── helpers ────────────────────── #


def _run_serve(argv: list[str]) -> tuple[int, str, str]:
    """Invoke `server.serve()` with a constructed argv and capture stdout/err.

    Returns ``(exit_code, stdout, stderr)``. Any non-zero ``sys.exit`` is
    captured; zero/None becomes exit 0.
    """
    out, err = io.StringIO(), io.StringIO()
    with patch.object(sys, "argv", ["simdrive", *argv]):
        with redirect_stdout(out), redirect_stderr(err):
            try:
                server.serve()
                code = 0
            except SystemExit as exc:
                code = int(exc.code) if exc.code is not None else 0
    return code, out.getvalue(), err.getvalue()


# ────────────────────── version subcommand ────────────────────── #


def test_version_plain_text():
    code, out, err = _run_serve(["version"])
    assert code == 0
    assert out.strip() == f"simdrive {__version__}"
    assert err == ""


def test_version_json_payload_shape():
    code, out, _ = _run_serve(["version", "--json"])
    assert code == 0
    payload = json.loads(out)
    assert payload == {"version": __version__, "package": "simdrive"}


def test_version_required_match_exit_zero():
    code, out, _ = _run_serve(["version", "--required-version", __version__])
    assert code == 0
    assert out.strip() == f"simdrive {__version__}"


def test_version_required_mismatch_exit_three_with_hint():
    code, out, err = _run_serve(["version", "--required-version", "9.9.9"])
    assert code == 3, "INIT-2026-553 P2: pin-mismatch must exit 3 for monorepo gates"
    # Stderr carries the diagnostic so stdout stays clean for log parsing.
    assert "simdrive version mismatch" in err
    assert __version__ in err
    assert "9.9.9" in err
    assert "pip install 'simdrive==9.9.9'" in err
    assert "docs.simdrive.dev/troubleshooting#version-mismatch" in err
    assert out == ""


def test_version_required_mismatch_json():
    code, out, _ = _run_serve(
        ["version", "--json", "--required-version", "9.9.9"]
    )
    assert code == 3
    payload = json.loads(out)
    assert payload["version"] == __version__
    assert payload["required"] == "9.9.9"
    assert payload["satisfies_required"] is False


def test_legacy_short_flag_delegates_to_new_handler():
    """`simdrive --version --json` must work (back-compat with the legacy
    spelling) and pick up the new --required-version flag too."""
    code, out, _ = _run_serve(["--version", "--json"])
    assert code == 0
    payload = json.loads(out)
    assert payload["version"] == __version__

    code, _, err = _run_serve(["--version", "--required-version", "0.0.0"])
    assert code == 3
    assert "pip install 'simdrive==0.0.0'" in err


# ────────────────────── doctor subcommand ────────────────────── #


_FAKE_DOCTOR_OK = {
    "ok": True,
    "checks": [
        {"name": "xcode_select", "ok": True, "detail": "/x/p"},
        {"name": "simctl_runtimes", "ok": True, "detail": "1 runtime(s)"},
    ],
}

_FAKE_DOCTOR_FAIL = {
    "ok": False,
    "checks": [
        {"name": "xcode_select", "ok": False, "detail": "no path"},
    ],
}


def test_doctor_json_ok():
    with patch.object(server.diagnostics, "doctor", return_value=_FAKE_DOCTOR_OK):
        code, out, _ = _run_serve(["doctor", "--json"])
    assert code == 0
    payload = json.loads(out)
    assert payload["ok"] is True
    assert payload["version"] == __version__
    assert payload["package"] == "simdrive"
    assert len(payload["checks"]) == 2


def test_doctor_human_readable_ok():
    with patch.object(server.diagnostics, "doctor", return_value=_FAKE_DOCTOR_OK):
        code, out, _ = _run_serve(["doctor"])
    assert code == 0
    assert f"simdrive {__version__}" in out
    assert "[ok ] xcode_select" in out
    assert "overall: ok" in out


def test_doctor_check_failure_exit_one():
    with patch.object(server.diagnostics, "doctor", return_value=_FAKE_DOCTOR_FAIL):
        code, out, _ = _run_serve(["doctor"])
    assert code == 1
    assert "[FAIL] xcode_select" in out
    assert "overall: FAIL" in out


def test_doctor_version_mismatch_overrides_check_failure():
    """If both a check fails AND the version doesn't match, exit 3 wins —
    operators need to fix the pin first; a wrong simdrive can produce
    spurious check failures."""
    with patch.object(server.diagnostics, "doctor", return_value=_FAKE_DOCTOR_FAIL):
        code, _, err = _run_serve(
            ["doctor", "--required-version", "0.0.0-not-real"]
        )
    assert code == 3, "version mismatch must dominate check failures"
    assert "pip install 'simdrive==0.0.0-not-real'" in err


def test_doctor_version_match_passes_through_to_checks():
    """When --required-version matches, exit code reflects diagnostic checks."""
    with patch.object(server.diagnostics, "doctor", return_value=_FAKE_DOCTOR_OK):
        code, _, _ = _run_serve(["doctor", "--required-version", __version__])
    assert code == 0


def test_doctor_json_includes_required_metadata():
    with patch.object(server.diagnostics, "doctor", return_value=_FAKE_DOCTOR_OK):
        code, out, _ = _run_serve(
            ["doctor", "--json", "--required-version", "0.0.0-not-real"]
        )
    assert code == 3
    payload = json.loads(out)
    assert payload["required_version"] == "0.0.0-not-real"
    assert payload["satisfies_required"] is False
    assert payload["ok"] is False  # ok rolls up satisfies_required + checks_ok


# ────────────────────── dispatch registry ────────────────────── #


def test_subcommands_registered():
    """Guard against accidental de-registration of the two new subcommands."""
    assert "doctor" in server._SUBCOMMANDS
    assert "version" in server._SUBCOMMANDS
    assert server._SUBCOMMANDS["doctor"] is server._cmd_doctor
    assert server._SUBCOMMANDS["version"] is server._cmd_version
