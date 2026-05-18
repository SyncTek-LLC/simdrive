"""Tests for cloud/storage/r2_stub.py — local-disk-backed R2 interface stub.

TDD: written before implementation.
"""
from __future__ import annotations

import pytest
from pathlib import Path


class TestR2Stub:

    @pytest.fixture
    def stub(self, tmp_path: Path):
        from simdrive.cloud.storage.r2_stub import R2Stub
        return R2Stub(storage_root=tmp_path)

    def test_put_and_get_object(self, stub) -> None:
        stub.put_object("bucket/key.txt", b"hello world")
        result = stub.get_object("bucket/key.txt")
        assert result == b"hello world"

    def test_get_nonexistent_object_returns_none(self, stub) -> None:
        result = stub.get_object("nonexistent/key.txt")
        assert result is None

    def test_put_overwrites_existing(self, stub) -> None:
        stub.put_object("my/key", b"first")
        stub.put_object("my/key", b"second")
        assert stub.get_object("my/key") == b"second"

    def test_presigned_url_returns_string(self, stub) -> None:
        stub.put_object("a/b.png", b"imagedata")
        url = stub.presigned_url("a/b.png", expires_in=3600)
        assert isinstance(url, str)
        assert len(url) > 0

    def test_presigned_url_for_nonexistent_raises(self, stub) -> None:
        with pytest.raises(FileNotFoundError):
            stub.presigned_url("no/such/file", expires_in=3600)

    def test_delete_object(self, stub) -> None:
        stub.put_object("del/me", b"data")
        stub.delete_object("del/me")
        assert stub.get_object("del/me") is None

    def test_delete_nonexistent_is_noop(self, stub) -> None:
        # Should not raise
        stub.delete_object("nothing/here")

    def test_list_objects_by_prefix(self, stub) -> None:
        stub.put_object("user123/rec1.yaml", b"a")
        stub.put_object("user123/rec2.yaml", b"b")
        stub.put_object("user999/rec1.yaml", b"c")
        keys = stub.list_objects("user123/")
        assert "user123/rec1.yaml" in keys
        assert "user123/rec2.yaml" in keys
        assert "user999/rec1.yaml" not in keys
