"""Unit + integration tests for trial source attribution (INIT-2026-556 W1).

Covers the contract enforced in `simdrive/src/simdrive/license/telemetry.py`:

  * SHA-256(email.lower().strip()) is deterministic and never reveals raw
    email in the POST body.
  * Default source = "direct" when --source is omitted.
  * --no-track makes zero network calls.
  * A persisted opt-out config (~/.simdrive/telemetry.toml) makes zero
    network calls.
  * Worker POST shape matches the contract negotiated with the W1-W
    sibling agent: {hashed_email, source, ts, package_version, os}.
  * Network failure is non-fatal — never raises, returns "skipped" notice.

These tests intentionally never reach a real network.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import requests

from simdrive.license import telemetry


# ---------------------------------------------------------------------------
# Hash function — deterministic, normalized, never the raw email
# ---------------------------------------------------------------------------


class TestHashEmail:

    def test_hash_is_deterministic(self) -> None:
        a = telemetry.hash_email("user@example.com")
        b = telemetry.hash_email("user@example.com")
        assert a == b
        assert len(a) == 64  # SHA-256 hex digest length

    def test_hash_normalizes_case(self) -> None:
        a = telemetry.hash_email("USER@example.com")
        b = telemetry.hash_email("user@example.com")
        assert a == b

    def test_hash_normalizes_whitespace(self) -> None:
        a = telemetry.hash_email("  user@example.com  \n")
        b = telemetry.hash_email("user@example.com")
        assert a == b

    def test_hash_matches_standalone_sha256(self) -> None:
        """Documenting that the algorithm is exactly stdlib SHA-256 of utf-8."""
        email = "u@x.io"
        expected = hashlib.sha256(b"u@x.io").hexdigest()
        assert telemetry.hash_email(email) == expected

    def test_different_emails_produce_different_hashes(self) -> None:
        assert telemetry.hash_email("a@x.com") != telemetry.hash_email("b@x.com")


# ---------------------------------------------------------------------------
# Payload shape — what hits the wire
# ---------------------------------------------------------------------------


class TestBuildPayload:

    def test_default_source_when_none(self) -> None:
        p = telemetry.build_payload("user@example.com", None)
        assert p["source"] == "direct"

    def test_default_source_when_empty(self) -> None:
        p = telemetry.build_payload("user@example.com", "")
        assert p["source"] == "direct"

    def test_explicit_source_preserved(self) -> None:
        p = telemetry.build_payload("user@example.com", "hn")
        assert p["source"] == "hn"

    def test_source_with_subchannel_preserved(self) -> None:
        # Per the M2 spec: reddit:iOSProgramming, cursor.directory etc.
        p = telemetry.build_payload("user@example.com", "reddit:iOSProgramming")
        assert p["source"] == "reddit:iOSProgramming"

    def test_payload_keys_are_exact_contract(self) -> None:
        """Worker contract is fixed — adding/removing keys breaks the Worker."""
        p = telemetry.build_payload("user@example.com", "hn")
        assert set(p.keys()) == {
            "hashed_email", "source", "ts", "package_version", "os"
        }

    def test_payload_contains_hashed_not_raw_email(self) -> None:
        p = telemetry.build_payload("leak@example.com", "hn")
        assert "leak@example.com" not in str(p)
        assert p["hashed_email"] == telemetry.hash_email("leak@example.com")

    def test_payload_os_is_family_not_fingerprint(self) -> None:
        p = telemetry.build_payload("u@x.io", "hn")
        assert p["os"] in ("darwin", "linux", "other")

    def test_payload_ts_is_iso_utc(self) -> None:
        p = telemetry.build_payload("u@x.io", "hn", now=1716681600.0)
        # 2024-05-26T00:00:00Z
        assert p["ts"] == "2024-05-26T00:00:00Z"


# ---------------------------------------------------------------------------
# Opt-out — env var, config file
# ---------------------------------------------------------------------------


class TestOptOut:

    def test_default_is_opt_in(self, tmp_path: Path) -> None:
        # No config file → tracking allowed.
        assert telemetry.is_opted_out(tmp_path / "missing.toml") is False

    def test_env_var_opts_out(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("SIMDRIVE_TELEMETRY_OFF", "1")
        assert telemetry.is_opted_out(tmp_path / "missing.toml") is True

    def test_track_false_opts_out(self, tmp_path: Path) -> None:
        cfg = tmp_path / "telemetry.toml"
        cfg.write_text("track = false\n")
        assert telemetry.is_opted_out(cfg) is True

    def test_track_true_opts_in(self, tmp_path: Path) -> None:
        cfg = tmp_path / "telemetry.toml"
        cfg.write_text("track = true\n")
        assert telemetry.is_opted_out(cfg) is False

    def test_file_present_no_key_is_opt_out(self, tmp_path: Path) -> None:
        """User created the file — default-deny their intent."""
        cfg = tmp_path / "telemetry.toml"
        cfg.write_text("# I am here on purpose\n")
        assert telemetry.is_opted_out(cfg) is True

    def test_quoted_value_parsed(self, tmp_path: Path) -> None:
        cfg = tmp_path / "telemetry.toml"
        cfg.write_text('track = "false"\n')
        assert telemetry.is_opted_out(cfg) is True

    def test_write_opt_out_persists(self, tmp_path: Path) -> None:
        cfg = tmp_path / "telemetry.toml"
        telemetry.write_opt_out(cfg)
        assert cfg.exists()
        assert telemetry.is_opted_out(cfg) is True


# ---------------------------------------------------------------------------
# maybe_send_attribution — orchestration: no-track / opt-out short-circuit
# ---------------------------------------------------------------------------


class TestMaybeSendAttributionShortCircuit:

    def test_no_track_skips_network(self, tmp_path: Path) -> None:
        """--no-track must make zero network calls."""
        with patch.object(telemetry.requests, "post") as mock_post:
            notice = telemetry.maybe_send_attribution(
                "u@x.io",
                source="hn",
                no_track=True,
                opt_out_path=tmp_path / "missing.toml",
            )
        assert mock_post.call_count == 0
        assert "opted out" in notice.lower()

    def test_opt_out_file_skips_network(self, tmp_path: Path) -> None:
        cfg = tmp_path / "telemetry.toml"
        cfg.write_text("track = false\n")
        with patch.object(telemetry.requests, "post") as mock_post:
            notice = telemetry.maybe_send_attribution(
                "u@x.io",
                source="hn",
                no_track=False,
                opt_out_path=cfg,
            )
        assert mock_post.call_count == 0
        assert "opted out" in notice.lower()


# ---------------------------------------------------------------------------
# Integration: when tracking is on, we POST exactly the contracted shape
# ---------------------------------------------------------------------------


class TestSendTrialAttributionIntegration:

    def test_post_sent_with_contract_shape(self, tmp_path: Path) -> None:
        captured: dict = {}

        def fake_post(url: str, json: dict, timeout: float) -> MagicMock:
            captured["url"] = url
            captured["json"] = json
            captured["timeout"] = timeout
            resp = MagicMock()
            resp.status_code = 204
            return resp

        with patch.object(telemetry.requests, "post", side_effect=fake_post):
            notice = telemetry.maybe_send_attribution(
                "user@example.com",
                source="hn",
                no_track=False,
                opt_out_path=tmp_path / "missing.toml",
                worker_url="https://api.simdrive.dev/trial",
            )

        assert captured["url"] == "https://api.simdrive.dev/trial"
        body = captured["json"]
        assert set(body.keys()) == {
            "hashed_email", "source", "ts", "package_version", "os"
        }
        assert body["source"] == "hn"
        assert body["hashed_email"] == telemetry.hash_email("user@example.com")
        assert "user@example.com" not in str(body)
        assert "sent" in notice.lower()

    def test_post_default_source_when_omitted(self, tmp_path: Path) -> None:
        captured: dict = {}

        def fake_post(url: str, json: dict, timeout: float) -> MagicMock:
            captured["json"] = json
            resp = MagicMock()
            resp.status_code = 200
            return resp

        with patch.object(telemetry.requests, "post", side_effect=fake_post):
            telemetry.maybe_send_attribution(
                "user@example.com",
                source=None,
                no_track=False,
                opt_out_path=tmp_path / "missing.toml",
            )
        assert captured["json"]["source"] == "direct"

    def test_network_failure_is_non_fatal(self, tmp_path: Path) -> None:
        """Connection error must NOT raise; must return a 'skipped' notice."""
        with patch.object(
            telemetry.requests,
            "post",
            side_effect=requests.exceptions.ConnectionError("DNS"),
        ):
            notice = telemetry.maybe_send_attribution(
                "u@x.io",
                source="hn",
                no_track=False,
                opt_out_path=tmp_path / "missing.toml",
            )
        # Did not raise → assertion is reaching this line.
        assert "skipped" in notice.lower()

    def test_non_2xx_is_non_fatal(self, tmp_path: Path) -> None:
        resp = MagicMock()
        resp.status_code = 500
        with patch.object(telemetry.requests, "post", return_value=resp):
            notice = telemetry.maybe_send_attribution(
                "u@x.io",
                source="hn",
                no_track=False,
                opt_out_path=tmp_path / "missing.toml",
            )
        assert "skipped" in notice.lower()
        assert "500" in notice

    def test_timeout_is_non_fatal(self, tmp_path: Path) -> None:
        with patch.object(
            telemetry.requests,
            "post",
            side_effect=requests.exceptions.Timeout("slow"),
        ):
            notice = telemetry.maybe_send_attribution(
                "u@x.io",
                source="hn",
                no_track=False,
                opt_out_path=tmp_path / "missing.toml",
            )
        assert "skipped" in notice.lower()
