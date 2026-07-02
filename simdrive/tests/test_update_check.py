"""Unit + integration tests for the signed update-check consumer (WS-4).

Contract (docs/design/ws4-update-check-feed-consumer.md, coordinator-frozen):
  * Detached Ed25519 sig over the EXACT raw feed bytes; verify-before-parse.
  * Fail-CLOSED on bad/missing/unverifiable signature or absent trust anchor.
  * Fail-OPEN (silent skip) on network error or unknown schema major.
  * Gated by the single HEKA_TELEMETRY kill-switch + HEKA_OFFLINE.
  * The GET carries ZERO user data; every real call is logged locally.
  * Never auto-installs; advisory only.
"""
from __future__ import annotations

import base64
import json
from pathlib import Path

import pytest
from nacl.signing import SigningKey

from simdrive.update import check as uc


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _sign(raw: bytes, sk: SigningKey) -> str:
    sig = sk.sign(raw).signature
    return base64.urlsafe_b64encode(sig).rstrip(b"=").decode("ascii")


def _feed_bytes(latest="1.0.0", min_supported="1.0.0b8", schema=1) -> bytes:
    return json.dumps({
        "schema_version": schema,
        "product": "simdrive",
        "latest": latest,
        "min_supported": min_supported,
        "released_at": "2026-07-01T00:00:00Z",
        "notes_url": "https://simdrive.dev/changelog/",
    }).encode("utf-8")


@pytest.fixture
def keypair():
    sk = SigningKey.generate()
    return sk, [sk.verify_key]


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.delenv("HEKA_TELEMETRY", raising=False)
    monkeypatch.delenv("HEKA_OFFLINE", raising=False)
    monkeypatch.setenv("HEKA_STATE_DIR", str(tmp_path / "heka"))
    yield


# ---------------------------------------------------------------------------
# verify_feed — signature discipline (fail-closed)
# ---------------------------------------------------------------------------


class TestVerifyFeed:

    def test_valid_signature_accepts(self, keypair) -> None:
        sk, keys = keypair
        raw = _feed_bytes()
        feed = uc.verify_feed(raw, _sign(raw, sk), verify_keys=keys)
        assert feed is not None and feed["latest"] == "1.0.0"

    def test_tampered_body_rejected(self, keypair) -> None:
        sk, keys = keypair
        raw = _feed_bytes(latest="1.0.0")
        sig = _sign(raw, sk)
        tampered = raw.replace(b"1.0.0", b"9.9.9")
        assert uc.verify_feed(tampered, sig, verify_keys=keys) is None

    def test_wrong_key_rejected(self, keypair) -> None:
        sk, _ = keypair
        raw = _feed_bytes()
        other = SigningKey.generate()
        assert uc.verify_feed(raw, _sign(raw, sk), verify_keys=[other.verify_key]) is None

    def test_no_trust_anchor_rejects(self, keypair) -> None:
        sk, _ = keypair
        raw = _feed_bytes()
        # Empty trusted set → fail-closed even with an otherwise-valid sig.
        assert uc.verify_feed(raw, _sign(raw, sk), verify_keys=[]) is None

    def test_default_keys_empty_is_fail_closed(self, keypair) -> None:
        """With no production key embedded yet, the default path refuses."""
        sk, _ = keypair
        raw = _feed_bytes()
        # verify_keys=None → uses UPDATE_FEED_PUBLIC_KEYS (empty placeholder).
        assert uc.verify_feed(raw, _sign(raw, sk)) is None

    def test_garbage_signature_rejected(self, keypair) -> None:
        _sk, keys = keypair
        raw = _feed_bytes()
        assert uc.verify_feed(raw, "!!!not-base64!!!", verify_keys=keys) is None

    def test_valid_sig_but_bad_json_rejected(self, keypair) -> None:
        sk, keys = keypair
        raw = b"not json at all"
        assert uc.verify_feed(raw, _sign(raw, sk), verify_keys=keys) is None


# ---------------------------------------------------------------------------
# evaluate — PEP 440 comparison
# ---------------------------------------------------------------------------


class TestEvaluate:

    def test_up_to_date(self) -> None:
        adv = uc.evaluate("1.0.0", json.loads(_feed_bytes(latest="1.0.0")))
        assert adv.status == "up_to_date"

    def test_update_available(self) -> None:
        adv = uc.evaluate("1.0.0b12", json.loads(_feed_bytes(latest="1.0.0")))
        assert adv.status == "update_available"
        assert "pip install -U simdrive" in adv.message

    def test_below_min(self) -> None:
        adv = uc.evaluate("1.0.0b7", json.loads(_feed_bytes(min_supported="1.0.0b8")))
        assert adv.status == "below_min"

    def test_unparseable_current(self) -> None:
        adv = uc.evaluate("not-a-version", json.loads(_feed_bytes()))
        assert adv.status == "unknown"


# ---------------------------------------------------------------------------
# check_for_update — orchestration
# ---------------------------------------------------------------------------


def _patch_fetch(monkeypatch, raw: bytes | None, sig: str | None):
    def fake_get(url, timeout):  # noqa: ANN001
        from unittest.mock import MagicMock
        m = MagicMock()
        if raw is None:
            import requests
            raise requests.exceptions.ConnectionError("deny")
        m.status_code = 200
        if url.endswith(".sig"):
            m.text = sig
        else:
            m.content = raw
        return m
    monkeypatch.setattr(uc.requests, "get", fake_get)


class TestCheckForUpdate:

    def test_killswitch_disables(self, monkeypatch, keypair) -> None:
        monkeypatch.setenv("HEKA_TELEMETRY", "off")
        called = {"n": 0}
        monkeypatch.setattr(uc.requests, "get",
                            lambda *a, **k: called.__setitem__("n", called["n"] + 1))
        res = uc.check_for_update(current="1.0.0b12", verify_keys=keypair[1], force=True)
        assert res["status"] == "disabled"
        assert called["n"] == 0  # no fetch attempted

    def test_offline_disables(self, monkeypatch, keypair) -> None:
        monkeypatch.setenv("HEKA_OFFLINE", "1")
        res = uc.check_for_update(current="1.0.0b12", verify_keys=keypair[1], force=True)
        assert res["status"] == "disabled"

    def test_network_unreachable_is_skip(self, monkeypatch, keypair) -> None:
        _patch_fetch(monkeypatch, None, None)
        res = uc.check_for_update(current="1.0.0b12", verify_keys=keypair[1], force=True)
        assert res["status"] == "skipped" and res["reason"] == "network"

    def test_unverified_feed_refused(self, monkeypatch, keypair) -> None:
        sk, keys = keypair
        raw = _feed_bytes()
        other = SigningKey.generate()  # sign with a key NOT in the trusted set
        _patch_fetch(monkeypatch, raw, _sign(raw, other))
        res = uc.check_for_update(current="1.0.0b12", verify_keys=keys, force=True)
        assert res["status"] == "unverified" and res["reason"] == "signature"

    def test_ok_update_available(self, monkeypatch, keypair) -> None:
        sk, keys = keypair
        raw = _feed_bytes(latest="1.0.0")
        _patch_fetch(monkeypatch, raw, _sign(raw, sk))
        res = uc.check_for_update(current="1.0.0b12", verify_keys=keys, force=True)
        assert res["status"] == "update_available"

    def test_unknown_schema_major_skips_fail_open(self, monkeypatch, keypair) -> None:
        sk, keys = keypair
        raw = _feed_bytes(schema=99)  # signed, but a major we don't understand
        _patch_fetch(monkeypatch, raw, _sign(raw, sk))
        res = uc.check_for_update(current="1.0.0b12", verify_keys=keys, force=True)
        assert res["status"] == "skipped" and res["reason"] == "schema"

    def test_cadence_uses_cache(self, monkeypatch, keypair, tmp_path) -> None:
        sk, keys = keypair
        raw = _feed_bytes(latest="1.0.0")
        _patch_fetch(monkeypatch, raw, _sign(raw, sk))
        # First call at t=1000 populates cache.
        uc.check_for_update(current="1.0.0b12", verify_keys=keys, now=1000.0)
        # Second call 1h later (< 24h interval), not forced → cached, no fetch.
        monkeypatch.setattr(uc.requests, "get",
                            lambda *a, **k: pytest.fail("fetched despite cache"))
        res = uc.check_for_update(current="1.0.0b12", verify_keys=keys, now=1000.0 + 3600)
        assert res.get("reason") == "cached"

    def test_product_plane_log_has_no_user_data(self, monkeypatch, keypair, tmp_path) -> None:
        sk, keys = keypair
        raw = _feed_bytes(latest="1.0.0")
        _patch_fetch(monkeypatch, raw, _sign(raw, sk))
        uc.check_for_update(current="1.0.0b12", verify_keys=keys, force=True)
        log = Path(tmp_path / "heka" / "calls.jsonl")
        assert log.exists()
        rec = json.loads(log.read_text().strip().splitlines()[-1])
        assert rec["user_data"] is False
        assert rec["payload_shape"] == "none"
        assert "1.0.0b12" not in log.read_text()  # installed version not logged


class TestNoEgressWhenDisabled:
    """Adversarial: when disabled, no socket is ever opened."""

    def test_killswitch_makes_zero_egress(self, monkeypatch, keypair) -> None:
        import socket
        monkeypatch.setenv("HEKA_TELEMETRY", "off")
        monkeypatch.setattr(
            socket.socket, "connect",
            lambda *a, **k: (_ for _ in ()).throw(AssertionError("egress")),
        )
        res = uc.check_for_update(current="1.0.0b12", verify_keys=keypair[1], force=True)
        assert res["status"] == "disabled"  # no connect attempted → no AssertionError
