"""Team ID comes from the signing certificate's subject OU, not the CN parenthetical.

"Apple Development: Name (RU29P3YK2K)" carries the developer's personal ID in
parentheses; the team (what DEVELOPMENT_TEAM needs) is the subject OU.
"""
from __future__ import annotations

import subprocess
from unittest.mock import MagicMock, patch

_FIND_IDENTITY = (
    '  1) ABA0B827E795158BC1FEEB542C4ACA2F6BCB9237 "Apple Development: Maurice Carrier (RU29P3YK2K)"\n'
    "     1 valid identities found\n"
)

# Two certs share the CN: an older one first (different SHA-1), then the valid one.
_FIND_CERT = (
    "SHA-256 hash: 05E1\nSHA-1 hash: 4CF4C511168CAC411676CA4DDF72F1459D7119D7\n"
    "-----BEGIN CERTIFICATE-----\nOLDCERT\n-----END CERTIFICATE-----\n"
    "SHA-256 hash: 0F00\nSHA-1 hash: ABA0B827E795158BC1FEEB542C4ACA2F6BCB9237\n"
    "-----BEGIN CERTIFICATE-----\nNEWCERT\n-----END CERTIFICATE-----\n"
)

_SUBJECTS = {
    "OLDCERT": "subject= /UID=N28T2U7R56/CN=Apple Development: Maurice Carrier (RU29P3YK2K)"
               "/OU=OLDTEAM000/O=Old/C=US\n",
    # LibreSSL form (macOS /usr/bin/openssl)
    "NEWCERT": "subject= /UID=N28T2U7R56/CN=Apple Development: Maurice Carrier (RU29P3YK2K)"
               "/OU=88CBA74T8K/O=LYRASIS, Inc./C=US\n",
}


def _proc(stdout="", rc=0):
    m = MagicMock(spec=subprocess.CompletedProcess)
    m.returncode, m.stdout, m.stderr = rc, stdout, ""
    return m


def _fake_run(subjects=_SUBJECTS, find_cert=_FIND_CERT):
    def run(cmd, *args, **kwargs):
        if cmd[:2] == ["security", "find-identity"]:
            return _proc(_FIND_IDENTITY)
        if cmd[:2] == ["security", "find-certificate"]:
            return _proc(find_cert)
        if cmd[:2] == ["openssl", "x509"]:
            pem = kwargs.get("input", "")
            for marker, subj in subjects.items():
                if marker in pem:
                    return _proc(subj)
            return _proc("", 1)
        return _proc("", 1)
    return run


def test_auto_detect_team_id_returns_subject_ou_not_cn_parenthetical():
    from simdrive.wda.bootstrap import auto_detect_team_id
    with patch("simdrive.wda.bootstrap.subprocess.run", side_effect=_fake_run()):
        assert auto_detect_team_id() == "88CBA74T8K"


def test_resolve_signing_identity_with_team_id_matches_on_ou():
    from simdrive.wda.bootstrap import resolve_signing_identity
    with patch("simdrive.wda.bootstrap.subprocess.run", side_effect=_fake_run()):
        name, team = resolve_signing_identity(team_id="88CBA74T8K")
    assert name == "Apple Development: Maurice Carrier (RU29P3YK2K)"
    assert team == "88CBA74T8K"


def test_resolve_signing_identity_cn_id_is_not_a_team_match():
    """Passing the personal ID finds no cert for that team (xcodebuild then provisions)."""
    from simdrive.wda.bootstrap import resolve_signing_identity
    with patch("simdrive.wda.bootstrap.subprocess.run", side_effect=_fake_run()):
        name, team = resolve_signing_identity(team_id="RU29P3YK2K")
    assert name is None
    assert team == "RU29P3YK2K"


def test_cert_subject_ou_picks_block_by_sha1():
    from simdrive.wda.bootstrap import _cert_subject_ou
    with patch("simdrive.wda.bootstrap.subprocess.run", side_effect=_fake_run()):
        assert _cert_subject_ou("Apple Development: Maurice Carrier (RU29P3YK2K)",
                                "ABA0B827E795158BC1FEEB542C4ACA2F6BCB9237") == "88CBA74T8K"
        assert _cert_subject_ou("Apple Development: Maurice Carrier (RU29P3YK2K)",
                                "4CF4C511168CAC411676CA4DDF72F1459D7119D7") == "OLDTEAM000"
        assert _cert_subject_ou("Apple Development: Maurice Carrier (RU29P3YK2K)",
                                "FFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFF") is None


def test_cert_subject_ou_parses_openssl3_form():
    from simdrive.wda.bootstrap import _cert_subject_ou
    subjects = {"NEWCERT": "subject=UID=N28T2U7R56, CN=Apple Development: M (RU29P3YK2K), "
                           "OU=88CBA74T8K, O=LYRASIS, C=US\n"}
    with patch("simdrive.wda.bootstrap.subprocess.run", side_effect=_fake_run(subjects=subjects)):
        assert _cert_subject_ou("x", "ABA0B827E795158BC1FEEB542C4ACA2F6BCB9237") == "88CBA74T8K"


def test_unreadable_cert_falls_back_to_cn_parenthetical():
    from simdrive.wda.bootstrap import auto_detect_team_id
    with patch("simdrive.wda.bootstrap.subprocess.run", side_effect=_fake_run(find_cert="")):
        assert auto_detect_team_id() == "RU29P3YK2K"
