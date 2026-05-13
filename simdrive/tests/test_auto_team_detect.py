"""Tests for simdrive.wda.bootstrap.auto_detect_team_id.

These tests MUST fail on feat/v17-claude-native HEAD (function does not exist)
and PASS after feat/simdrive-a10-zero-config-bootstrap is merged.

Monkeypatches subprocess.run at the module boundary
(``simdrive.wda.bootstrap.subprocess.run``) so no real keychain or Xcode
access is required.
"""
from __future__ import annotations

import subprocess
from unittest.mock import MagicMock, patch, call


# ── helpers ───────────────────────────────────────────────────────────────────

def _make_proc(stdout: str = "", returncode: int = 0) -> MagicMock:
    m = MagicMock(spec=subprocess.CompletedProcess)
    m.returncode = returncode
    m.stdout = stdout
    m.stderr = ""
    return m


# Canned `security find-identity -p codesigning -v` outputs

_SINGLE_TEAM_OUTPUT = """\
  1) AABBCC112233445566778899AABBCC1122334455 "Apple Development: John Smith (E52N8732YT)"
  2) DDEEFF112233445566778899DDEEFF1122334455 "Apple Development: John Smith (E52N8732YT)"
     2 valid identities found
"""

_TWO_TEAMS_OUTPUT = """\
  1) AABBCC112233445566778899AABBCC1122334455 "Apple Development: Alice (E52N8732YT)"
  2) DDEEFF112233445566778899DDEEFF1122334455 "Apple Development: Bob (XYZ9876543)"
     2 valid identities found
"""

_EMPTY_KEYCHAIN_OUTPUT = """\
     0 valid identities found
"""

# Canned `defaults read` Xcode plist output
_XCODE_PLIST_WITH_TEAM = """\
{
    DVTDeveloperAccountManagerAppleIDLists = {
        "alice@example.com" = {
            teamID = "ABC1234567";
            identifier = "3E6A2FAB-C123-456D-789E-0F1A2B3C4D5E";
        };
    };
}
"""

_EMPTY_XCODE_PLIST = "{}"


# ── tests ─────────────────────────────────────────────────────────────────────


def test_single_team_keychain_returns_team_id():
    """Single unique team in keychain → return that team ID."""
    security_proc = _make_proc(stdout=_SINGLE_TEAM_OUTPUT)
    # defaults read should not be called if keychain succeeds
    with patch("simdrive.wda.bootstrap.subprocess.run", return_value=security_proc) as mock_run:
        from simdrive.wda.bootstrap import auto_detect_team_id
        result = auto_detect_team_id()

    assert result == "E52N8732YT", f"Expected 'E52N8732YT', got {result!r}"
    # Only one subprocess call should have been made (the security command)
    assert mock_run.call_count == 1


def test_multiple_teams_keychain_returns_none():
    """Two distinct teams in keychain → return None (ambiguous)."""
    security_proc = _make_proc(stdout=_TWO_TEAMS_OUTPUT)
    with patch("simdrive.wda.bootstrap.subprocess.run", return_value=security_proc):
        from simdrive.wda.bootstrap import auto_detect_team_id
        result = auto_detect_team_id()

    assert result is None, f"Expected None for multiple teams, got {result!r}"


def test_no_teams_anywhere_returns_none():
    """Empty keychain AND empty Xcode plist → return None."""
    empty_proc = _make_proc(stdout=_EMPTY_KEYCHAIN_OUTPUT, returncode=0)
    empty_plist = _make_proc(stdout=_EMPTY_XCODE_PLIST, returncode=0)

    with patch(
        "simdrive.wda.bootstrap.subprocess.run",
        side_effect=[empty_proc, empty_plist],
    ):
        from simdrive.wda.bootstrap import auto_detect_team_id
        result = auto_detect_team_id()

    assert result is None, f"Expected None for no teams, got {result!r}"


def test_xcode_fallback_empty_keychain():
    """Empty keychain, Xcode plist has teamID = 'ABC1234567' → return 'ABC1234567'."""
    # Keychain returns 0 identities (returncode 0 but empty)
    empty_keychain = _make_proc(stdout=_EMPTY_KEYCHAIN_OUTPUT, returncode=0)
    # defaults read returns successfully with the plist
    plist_proc = _make_proc(stdout=_XCODE_PLIST_WITH_TEAM, returncode=0)

    with patch(
        "simdrive.wda.bootstrap.subprocess.run",
        side_effect=[empty_keychain, plist_proc],
    ):
        from simdrive.wda.bootstrap import auto_detect_team_id
        result = auto_detect_team_id()

    assert result == "ABC1234567", f"Expected 'ABC1234567' from Xcode fallback, got {result!r}"


def test_xcode_fallback_keychain_fails():
    """Keychain command fails (returncode non-zero) → falls through to Xcode plist."""
    failed_security = _make_proc(stdout="", returncode=1)
    plist_proc = _make_proc(stdout=_XCODE_PLIST_WITH_TEAM, returncode=0)

    with patch(
        "simdrive.wda.bootstrap.subprocess.run",
        side_effect=[failed_security, plist_proc],
    ):
        from simdrive.wda.bootstrap import auto_detect_team_id
        result = auto_detect_team_id()

    assert result == "ABC1234567", f"Expected 'ABC1234567' from fallback after keychain failure, got {result!r}"
