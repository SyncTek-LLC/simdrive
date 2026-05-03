"""Tests for cloud/storage/r2.py — real R2 backend via moto mock.

TDD: written before implementation. Uses moto to mock AWS S3
(R2 is S3-compatible; boto3 targets an S3 endpoint).

All tests run offline — moto intercepts boto3 calls in-process.
No real R2/Cloudflare/S3 credentials required.

WHY we pass endpoint_url=None in tests: when using moto's mock_aws(),
the mock only intercepts calls to the default AWS endpoints. Passing a
custom endpoint (the real R2 URL) bypasses moto. By passing None we get
the default AWS endpoint, which moto intercepts. In production, the real
endpoint is constructed from R2_ACCOUNT_ID.
"""
from __future__ import annotations

import os
import pytest
from moto import mock_aws
import boto3


# ---------------------------------------------------------------------------
# Helper: create a moto-backed R2Client with a pre-created bucket
# ---------------------------------------------------------------------------

def _make_client_in_mock(bucket: str = "test-simdrive-bucket"):
    """Create an R2Client that uses moto's patched boto3 (no real endpoint).

    WHY endpoint_url=None: moto intercepts calls to the default AWS S3
    endpoint. Custom endpoints (like the real R2 URL) bypass the mock.
    Tests pass `_test_endpoint_override=True` so R2Client creates the
    boto3 client without a custom endpoint_url.
    """
    from simdrive.cloud.storage.r2 import R2Client
    # Pre-create the bucket in the moto mock
    s3 = boto3.client(
        "s3",
        region_name="us-east-1",
        aws_access_key_id="test-key",
        aws_secret_access_key="test-secret",
    )
    s3.create_bucket(Bucket=bucket)

    # Create R2Client with no endpoint_url so moto intercepts
    client = R2Client(
        account_id="test-account",
        access_key_id="test-key",
        secret_access_key="test-secret",
        bucket=bucket,
        endpoint_url="",  # empty string → boto3 uses default AWS endpoint (moto-intercepted)
    )
    return client


# ---------------------------------------------------------------------------
# R2Client tests (all run inside mock_aws context)
# ---------------------------------------------------------------------------

class TestR2Client:
    """Test the boto3-backed R2Client against moto-mocked S3."""

    @mock_aws
    def test_put_and_get_object(self) -> None:
        client = _make_client_in_mock()
        client.put_object("test/key.txt", b"hello r2")
        result = client.get_object("test/key.txt")
        assert result == b"hello r2"

    @mock_aws
    def test_get_nonexistent_returns_none(self) -> None:
        client = _make_client_in_mock()
        assert client.get_object("no/such/key") is None

    @mock_aws
    def test_delete_object(self) -> None:
        client = _make_client_in_mock()
        client.put_object("del/key", b"data")
        client.delete_object("del/key")
        assert client.get_object("del/key") is None

    @mock_aws
    def test_delete_nonexistent_is_noop(self) -> None:
        """Delete of non-existent key must not raise."""
        client = _make_client_in_mock()
        # Should not raise
        client.delete_object("nothing/here")

    @mock_aws
    def test_list_objects_by_prefix(self) -> None:
        client = _make_client_in_mock()
        client.put_object("user/rec1.yaml", b"a")
        client.put_object("user/rec2.yaml", b"b")
        client.put_object("other/rec1.yaml", b"c")

        keys = client.list_objects("user/")
        assert "user/rec1.yaml" in keys
        assert "user/rec2.yaml" in keys
        assert "other/rec1.yaml" not in keys

    @mock_aws
    def test_list_objects_empty_prefix(self) -> None:
        client = _make_client_in_mock()
        keys = client.list_objects("no/such/prefix/")
        assert keys == []

    @mock_aws
    def test_presigned_url_returns_string(self) -> None:
        client = _make_client_in_mock()
        client.put_object("img/test.png", b"pngdata")
        url = client.presigned_url("img/test.png", expires_in=3600)
        assert isinstance(url, str)
        assert len(url) > 10

    @mock_aws
    def test_presigned_url_nonexistent_raises(self) -> None:
        client = _make_client_in_mock()
        with pytest.raises(FileNotFoundError):
            client.presigned_url("no/such/file.png", expires_in=3600)

    @mock_aws
    def test_put_overwrites_existing(self) -> None:
        client = _make_client_in_mock()
        client.put_object("key", b"first")
        client.put_object("key", b"second")
        assert client.get_object("key") == b"second"

    @mock_aws
    def test_list_returns_all_under_prefix(self) -> None:
        client = _make_client_in_mock()
        for i in range(5):
            client.put_object(f"batch/{i:03d}.png", b"img")
        keys = client.list_objects("batch/")
        assert len(keys) == 5

    @mock_aws
    def test_put_and_delete_all_objects_under_prefix(self) -> None:
        client = _make_client_in_mock()
        for i in range(3):
            client.put_object(f"rec/step_{i}.png", b"img")

        # Delete all
        for key in client.list_objects("rec/"):
            client.delete_object(key)

        assert client.list_objects("rec/") == []


# ---------------------------------------------------------------------------
# Storage backend selection tests
# ---------------------------------------------------------------------------

class TestStorageBackendSelection:
    """Test that create_storage_backend selects R2Client or R2Stub correctly."""

    def test_stub_when_no_env(self, monkeypatch) -> None:
        """Without R2 env vars, should return R2Stub."""
        for var in ("R2_ACCOUNT_ID", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY", "R2_BUCKET"):
            monkeypatch.delenv(var, raising=False)
        monkeypatch.delenv("STORAGE_BACKEND", raising=False)

        import importlib
        import simdrive.cloud.storage.r2 as r2_mod
        importlib.reload(r2_mod)

        from simdrive.cloud.storage.r2_stub import R2Stub
        backend = r2_mod.create_storage_backend()
        assert isinstance(backend, R2Stub)

    def test_stub_when_forced_via_env(self, monkeypatch) -> None:
        """STORAGE_BACKEND=stub forces R2Stub even if R2 env vars are present."""
        monkeypatch.setenv("STORAGE_BACKEND", "stub")
        monkeypatch.setenv("R2_ACCOUNT_ID", "x")
        monkeypatch.setenv("R2_ACCESS_KEY_ID", "x")
        monkeypatch.setenv("R2_SECRET_ACCESS_KEY", "x")
        monkeypatch.setenv("R2_BUCKET", "x")

        import importlib
        import simdrive.cloud.storage.r2 as r2_mod
        importlib.reload(r2_mod)

        from simdrive.cloud.storage.r2_stub import R2Stub
        backend = r2_mod.create_storage_backend()
        assert isinstance(backend, R2Stub)

    @mock_aws
    def test_r2client_when_all_env_set(self, monkeypatch) -> None:
        """With all R2 env vars set, should return R2Client."""
        monkeypatch.setenv("R2_ACCOUNT_ID", "account-123")
        monkeypatch.setenv("R2_ACCESS_KEY_ID", "key-id")
        monkeypatch.setenv("R2_SECRET_ACCESS_KEY", "secret")
        monkeypatch.setenv("R2_BUCKET", "test-bucket")
        monkeypatch.delenv("STORAGE_BACKEND", raising=False)

        # Pre-create bucket
        s3 = boto3.client(
            "s3",
            region_name="us-east-1",
            aws_access_key_id="key-id",
            aws_secret_access_key="secret",
        )
        s3.create_bucket(Bucket="test-bucket")

        import importlib
        import simdrive.cloud.storage.r2 as r2_mod
        importlib.reload(r2_mod)

        from simdrive.cloud.storage.r2 import R2Client
        backend = r2_mod.create_storage_backend()
        assert isinstance(backend, R2Client)
