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

    def test_default_is_opted_out(self, tmp_path: Path) -> None:
        # TRUE OPT-IN: no config file → opted OUT (no POST without consent).
        assert telemetry.is_opted_out(tmp_path / "missing.toml") is True

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

    def test_malformed_track_value_fails_closed(self, tmp_path: Path) -> None:
        """A non-true/false track value → opted out (fail-closed for privacy)."""
        cfg = tmp_path / "telemetry.toml"
        cfg.write_text("track = maybe\n")
        assert telemetry.is_opted_out(cfg) is True

    def test_unreadable_config_fails_closed(self, tmp_path: Path) -> None:
        """An OSError while reading the config → opted out (fail-closed).

        A directory at the config path makes read_text raise IsADirectoryError
        (an OSError subclass), exercising the except-OSError branch.
        """
        cfg = tmp_path / "telemetry.toml"
        cfg.mkdir()  # path exists but reading it raises OSError
        assert telemetry.is_opted_out(cfg) is True

    def test_write_opt_out_persists(self, tmp_path: Path) -> None:
        cfg = tmp_path / "telemetry.toml"
        telemetry.write_opt_out(cfg)
        assert cfg.exists()
        assert telemetry.is_opted_out(cfg) is True

    def test_write_opt_in_persists(self, tmp_path: Path) -> None:
        """The documented durable opt-in: write_opt_in → is_opted_out False."""
        cfg = tmp_path / "telemetry.toml"
        telemetry.write_opt_in(cfg)
        assert cfg.exists()
        assert telemetry.is_opted_out(cfg) is False


# ---------------------------------------------------------------------------
# HEKA_TELEMETRY kill-switch — the single cross-Heka off signal
# ---------------------------------------------------------------------------


class TestKillSwitch:

    @pytest.mark.parametrize("val", ["off", "0", "false", "no", "none", "disabled", "OFF", "False"])
    def test_off_values_kill(self, val: str, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("HEKA_TELEMETRY", val)
        assert telemetry.telemetry_killed() is True

    def test_unset_is_not_killed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("HEKA_TELEMETRY", raising=False)
        assert telemetry.telemetry_killed() is False

    def test_on_value_is_not_killed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # "on" is not an off value; kill-switch is off-only (it never enables).
        monkeypatch.setenv("HEKA_TELEMETRY", "on")
        assert telemetry.telemetry_killed() is False

    def test_kill_switch_overrides_opt_in_config(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        cfg = tmp_path / "telemetry.toml"
        cfg.write_text("track = true\n")
        monkeypatch.setenv("HEKA_TELEMETRY", "off")
        # Even an explicit opt-in must be overridden by the kill-switch.
        assert telemetry.is_opted_out(cfg) is True

    def test_kill_switch_short_circuits_sender(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """send_trial_attribution must NOT touch the network when killed."""
        monkeypatch.setenv("HEKA_TELEMETRY", "off")
        with patch.object(telemetry.requests, "post") as mock_post:
            ok, msg = telemetry.send_trial_attribution("u@x.io", "hn")
        assert mock_post.call_count == 0
        assert ok is False
        assert "kill-switch" in msg.lower()


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
        assert "off" in notice.lower()

    def test_default_no_optin_skips_network(self, tmp_path: Path) -> None:
        """No opt-in on record → zero network calls, even with a --source."""
        with patch.object(telemetry.requests, "post") as mock_post:
            notice = telemetry.maybe_send_attribution(
                "u@x.io",
                source="hn",
                no_track=False,
                opt_out_path=tmp_path / "missing.toml",
            )
        assert mock_post.call_count == 0
        assert "off" in notice.lower()

    def test_kill_switch_skips_network_despite_opt_in(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """HEKA_TELEMETRY=off wins over an explicit --track opt-in."""
        monkeypatch.setenv("HEKA_TELEMETRY", "off")
        with patch.object(telemetry.requests, "post") as mock_post:
            notice = telemetry.maybe_send_attribution(
                "u@x.io",
                source="hn",
                no_track=False,
                track=True,
                opt_out_path=tmp_path / "missing.toml",
            )
        assert mock_post.call_count == 0
        assert "kill-switch" in notice.lower()

    def test_explicit_track_flag_enables_send(self, tmp_path: Path) -> None:
        """--track (track=True) is a valid per-invocation opt-in → POST fires."""
        with patch.object(telemetry.requests, "post") as mock_post:
            resp = MagicMock()
            resp.status_code = 204
            mock_post.return_value = resp
            notice = telemetry.maybe_send_attribution(
                "u@x.io",
                source="hn",
                no_track=False,
                track=True,
                opt_out_path=tmp_path / "missing.toml",
            )
        assert mock_post.call_count == 1
        assert "sent" in notice.lower()

    def test_persisted_opt_in_enables_send(self, tmp_path: Path) -> None:
        """track = true on disk is a valid durable opt-in → POST fires."""
        cfg = tmp_path / "telemetry.toml"
        cfg.write_text("track = true\n")
        with patch.object(telemetry.requests, "post") as mock_post:
            resp = MagicMock()
            resp.status_code = 200
            mock_post.return_value = resp
            notice = telemetry.maybe_send_attribution(
                "u@x.io",
                source="hn",
                no_track=False,
                opt_out_path=cfg,
            )
        assert mock_post.call_count == 1
        assert "sent" in notice.lower()


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
                track=True,  # explicit opt-in required for the POST to fire
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
                track=True,  # explicit opt-in required for the POST to fire
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
                track=True,
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
                track=True,
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
                track=True,
                opt_out_path=tmp_path / "missing.toml",
            )
        assert "skipped" in notice.lower()
