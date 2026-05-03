"""Tests for simdrive.wda.registry — load/save/delete per-UDID registry files."""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

import pytest


# ── fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture()
def registry_dir(tmp_path, monkeypatch):
    """Redirect registry storage to a temp directory."""
    monkeypatch.setenv("WDA_REGISTRY_DIR", str(tmp_path))
    return tmp_path


# ── tests ─────────────────────────────────────────────────────────────────────


def test_save_creates_file(registry_dir):
    from simdrive.wda.registry import save, registry_path

    udid = "00008150-TESTUDID001"
    entry = {"host": "localhost", "port": 8100, "wda_bundle_id": "com.test.wda"}
    saved = save(udid, entry)

    assert saved.exists()
    assert saved == registry_path(udid)
    loaded = json.loads(saved.read_text())
    assert loaded == entry


def test_load_returns_entry(registry_dir):
    from simdrive.wda.registry import save, load

    udid = "00008150-TESTUDID002"
    entry = {"host": "127.0.0.1", "port": 8200, "signing_identity": "Apple Development: Test (ABCD1234)"}
    save(udid, entry)

    result = load(udid)
    assert result == entry


def test_load_returns_none_if_missing(registry_dir):
    from simdrive.wda.registry import load

    assert load("NO-SUCH-UDID") is None


def test_load_returns_none_on_corrupted_json(registry_dir):
    from simdrive.wda.registry import registry_path, load

    udid = "CORRUPT-UDID"
    path = registry_path(udid)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("not valid json {{{{", encoding="utf-8")

    assert load(udid) is None


def test_delete_removes_file(registry_dir):
    from simdrive.wda.registry import save, delete, registry_path

    udid = "00008150-DELETEME"
    save(udid, {"host": "localhost", "port": 8100})
    assert registry_path(udid).exists()

    delete(udid)
    assert not registry_path(udid).exists()


def test_delete_is_noop_if_missing(registry_dir):
    from simdrive.wda.registry import delete

    # Should not raise
    delete("DOES-NOT-EXIST-UDID")


def test_save_creates_parent_dirs(tmp_path, monkeypatch):
    """Registry path parents must be created automatically."""
    nested = tmp_path / "deep" / "nested"
    monkeypatch.setenv("WDA_REGISTRY_DIR", str(nested))

    from simdrive.wda import registry as _reg
    # Reload to pick up env change
    import importlib
    importlib.reload(_reg)

    path = _reg.save("TEST-UDID-DIRS", {"port": 8100})
    assert path.exists()


def test_save_pretty_prints_json(registry_dir):
    from simdrive.wda.registry import save, registry_path

    udid = "PRETTY-JSON-UDID"
    save(udid, {"host": "localhost", "port": 8100})
    raw = registry_path(udid).read_text()
    # Pretty-printed JSON has newlines.
    assert "\n" in raw


def test_registry_path_uses_env_override(tmp_path, monkeypatch):
    monkeypatch.setenv("WDA_REGISTRY_DIR", str(tmp_path))
    from simdrive.wda import registry as _reg
    import importlib
    importlib.reload(_reg)

    p = _reg.registry_path("MY-UDID")
    assert str(tmp_path) in str(p)
