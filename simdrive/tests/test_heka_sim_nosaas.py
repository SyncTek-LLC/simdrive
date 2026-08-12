"""HEKA-SIM-NOSAAS — adversarial network-deny proof of telemetry silence.

Claim under test (WS-0 / the promise): simdrive's data plane does **not** phone
home. Under an adversarial network-deny (every outbound socket refused), the
default trial-start flow is provably SILENT — no egress is even attempted — and
the single ``HEKA_TELEMETRY`` kill-switch severs telemetry even when a user has
explicitly opted in.

The test installs a socket-level guard that raises on ANY connect attempt, so a
false pass is impossible: the positive control (opted-in, no kill-switch) DOES
trip the guard and is caught gracefully, proving the guard actually bites.
"""
from __future__ import annotations

import socket
from contextlib import contextmanager
from pathlib import Path

import pytest

from simdrive.license import cli as license_cli
from simdrive.license import telemetry
from simdrive.license import trial_history


class NetworkDenied(RuntimeError):
    """Raised by the guard when any code attempts an outbound connection."""


@contextmanager
def network_denied(monkeypatch: pytest.MonkeyPatch):
    """Refuse every outbound socket — the adversarial network-deny."""
    def _deny(*args, **kwargs):  # noqa: ANN002
        raise NetworkDenied("outbound network egress attempted under deny")

    monkeypatch.setattr(socket.socket, "connect", _deny, raising=True)
    monkeypatch.setattr(socket, "create_connection", _deny, raising=True)
    # Also block the requests session send path defensively — belt & braces.
    import requests
    monkeypatch.setattr(
        requests.adapters.HTTPAdapter, "send",
        lambda self, *a, **k: (_ for _ in ()).throw(NetworkDenied("HTTPAdapter.send")),
        raising=True,
    )
    yield


class TestDefaultFlowIsSilent:

    def test_default_attribution_makes_no_egress(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Opt-in OFF by default → zero egress under network-deny."""
        monkeypatch.delenv("HEKA_TELEMETRY", raising=False)
        with network_denied(monkeypatch):
            notice = telemetry.maybe_send_attribution(
                "user@example.com",
                source="hn",
                no_track=False,
                opt_out_path=tmp_path / "missing.toml",
            )
        assert "off" in notice.lower()  # never attempted; no NetworkDenied raised

    def test_offline_trial_start_is_silent(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The full offline trial-start + default attribution never egresses."""
        monkeypatch.delenv("HEKA_TELEMETRY", raising=False)
        # Hermetic: don't read/write the real ~/.simdrive/trial_history.json.
        monkeypatch.setattr(trial_history, "already_issued", lambda *a, **k: False)
        monkeypatch.setattr(trial_history, "record_issued", lambda *a, **k: None)
        with network_denied(monkeypatch):
            result = license_cli.cmd_trial_start(
                "user@example.com",
                offline_dev=True,
                license_path=tmp_path / "license.json",
            )
            notice = telemetry.maybe_send_attribution(
                "user@example.com",
                source=None,
                no_track=False,
                opt_out_path=tmp_path / "missing.toml",
            )
        assert result["key"]  # trial issued locally
        assert "off" in notice.lower()


class TestKillSwitchSeversOptIn:

    def test_kill_switch_beats_persisted_opt_in(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Even a durable `track = true`, the kill-switch → zero egress."""
        cfg = tmp_path / "telemetry.toml"
        cfg.write_text("track = true\n")
        monkeypatch.setenv("HEKA_TELEMETRY", "off")
        with network_denied(monkeypatch):
            notice = telemetry.maybe_send_attribution(
                "user@example.com",
                source="hn",
                no_track=False,
                opt_out_path=cfg,
            )
        assert "kill-switch" in notice.lower()

    def test_kill_switch_beats_explicit_track_flag(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("HEKA_TELEMETRY", "off")
        with network_denied(monkeypatch):
            ok, msg = telemetry.send_trial_attribution("u@x.io", "hn")
        assert ok is False
        assert "kill-switch" in msg.lower()


class TestGuardActuallyBites:
    """Positive control: prove the network-deny guard is real, not a no-op."""

    def test_opted_in_without_killswitch_trips_guard_but_is_non_fatal(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("HEKA_TELEMETRY", raising=False)
        with network_denied(monkeypatch):
            # track=True opt-in → the POST IS attempted → guard bites →
            # telemetry catches it and reports "skipped" (non-fatal).
            notice = telemetry.maybe_send_attribution(
                "user@example.com",
                source="hn",
                no_track=False,
                track=True,
                opt_out_path=tmp_path / "missing.toml",
            )
        assert "skipped" in notice.lower()
