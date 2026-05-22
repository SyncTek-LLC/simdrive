"""R2Client — boto3-backed R2 object storage client.

R2 is S3-compatible; boto3 targets the R2 endpoint using the standard
S3 API. The client matches the same interface as R2Stub so callers can
swap backends without changing code.

Interface contract (mirrors R2Stub):
  put_object(key: str, data: bytes) -> None
  get_object(key: str) -> bytes | None
  delete_object(key: str) -> None
  list_objects(prefix: str) -> list[str]
  presigned_url(key: str, expires_in: int) -> str

Configuration (via env vars):
  R2_ACCOUNT_ID       — Cloudflare account ID
  R2_ACCESS_KEY_ID    — R2 API token key ID
  R2_SECRET_ACCESS_KEY— R2 API token secret
  R2_BUCKET           — bucket name (e.g. "simdrive-cloud-prod")
  STORAGE_BACKEND     — set to "stub" to force R2Stub even when env vars present

WHY boto3 over r2-specific SDK: boto3 is battle-tested, well-supported,
and R2's S3 compatibility is documented and stable. No new dependency
surface beyond boto3 + botocore.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

class R2Client:
    """boto3-backed Cloudflare R2 object storage client.

    WHY endpoint_url: R2 exposes an S3-compatible endpoint at
    https://<account_id>.r2.cloudflarestorage.com. boto3's S3 client
    uses this instead of the default AWS endpoint.

    WHY region_name="auto": R2 uses "auto" as a special region token
    that routes to the nearest Cloudflare PoP.
    """

    def __init__(
        self,
        *,
        account_id: str,
        access_key_id: str,
        secret_access_key: str,
        bucket: str,
        endpoint_url: Optional[str] = None,
        region_name: str = "auto",
    ) -> None:
        # WHY lazy import: boto3 is a production cloud dep, not a dev dep.
        # Importing at module level breaks `from simdrive.cloud.storage.r2 import
        # create_storage_backend` in test environments where boto3 is absent.
        # R2Client is only instantiated when R2 env vars are present, so the
        # import is deferred until it is actually needed.
        try:
            import boto3 as _boto3
            from botocore.exceptions import ClientError as _ClientError
        except ImportError as exc:  # pragma: no cover
            raise ImportError(
                "boto3 is required to use R2Client. "
                "Install it with: pip install boto3"
            ) from exc
        self._boto3 = _boto3
        self._ClientError = _ClientError
        self._bucket = bucket

        # endpoint_url resolution:
        # - None     → use the real R2 Cloudflare endpoint (production default)
        # - ""       → omit endpoint_url entirely (lets moto/tests intercept via AWS default)
        # - non-empty string → use as-is (e.g. localstack, custom proxy)
        if endpoint_url is None:
            resolved_endpoint: Optional[str] = (
                f"https://{account_id}.r2.cloudflarestorage.com"
            )
        elif endpoint_url == "":
            resolved_endpoint = None  # boto3 uses default AWS S3 — interceptable by moto
        else:
            resolved_endpoint = endpoint_url

        self._s3 = self._boto3.client(
            "s3",
            endpoint_url=resolved_endpoint,
            aws_access_key_id=access_key_id,
            aws_secret_access_key=secret_access_key,
            region_name=region_name,
        )

    def put_object(self, key: str, data: bytes) -> None:
        """Upload bytes to R2 at `key`.

        WHY no ContentType: callers own content-type decisions; the generic
        storage layer stays agnostic to MIME types.
        """
        self._s3.put_object(Bucket=self._bucket, Key=key, Body=data)

    def get_object(self, key: str) -> Optional[bytes]:
        """Fetch bytes from R2. Returns None if key does not exist."""
        try:
            response = self._s3.get_object(Bucket=self._bucket, Key=key)
            return response["Body"].read()
        except self._ClientError as exc:
            if exc.response["Error"]["Code"] in ("NoSuchKey", "404"):
                return None
            raise

    def delete_object(self, key: str) -> None:
        """Delete an object from R2. No-op if key does not exist.

        WHY no existence check: S3/R2 delete is idempotent — deleting a
        non-existent key returns 204, not an error.
        """
        self._s3.delete_object(Bucket=self._bucket, Key=key)

    def list_objects(self, prefix: str) -> list[str]:
        """Return all object keys that start with `prefix`.

        WHY paginate: S3/R2 ListObjectsV2 returns up to 1000 keys per call.
        Pagination ensures correctness for customers with large corpora.
        """
        keys: list[str] = []
        paginator = self._s3.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self._bucket, Prefix=prefix):
            for obj in page.get("Contents", []):
                keys.append(obj["Key"])
        return keys

    def presigned_url(self, key: str, expires_in: int) -> str:
        """Generate a presigned download URL for `key`.

        WHY check existence first: presigned_url on S3 does not fail for
        missing keys — it generates a URL that will 404 when followed.
        We match R2Stub's contract (raise FileNotFoundError) so callers
        get an early error rather than a broken URL.

        Raises FileNotFoundError if key does not exist.
        """
        # Verify existence before generating URL
        try:
            self._s3.head_object(Bucket=self._bucket, Key=key)
        except self._ClientError as exc:
            if exc.response["Error"]["Code"] in ("404", "NoSuchKey"):
                raise FileNotFoundError(f"R2Client: object not found for key {key!r}") from exc
            raise

        return self._s3.generate_presigned_url(
            "get_object",
            Params={"Bucket": self._bucket, "Key": key},
            ExpiresIn=expires_in,
        )


def create_storage_backend(storage_root: Optional[Path] = None):
    """Factory: returns R2Client when env vars present, R2Stub otherwise.

    Callers should use this factory rather than instantiating either class
    directly so the backend selection is centralized and testable.

    Decision tree:
      1. STORAGE_BACKEND=stub → always R2Stub (local dev override)
      2. All four R2_* env vars present → R2Client
      3. Otherwise → R2Stub with storage_root (defaults to /tmp/simdrive-cloud)

    WHY check all four vars: partial R2 config is worse than no config —
    a missing secret causes a confusing boto3 auth error instead of a clean
    "using stub" fallback.
    """
    from simdrive.cloud.storage.r2_stub import R2Stub

    # Explicit override
    if os.environ.get("STORAGE_BACKEND", "").lower() == "stub":
        root = storage_root or Path(os.environ.get("SIMDRIVE_STORAGE_ROOT", "/tmp/simdrive-cloud"))
        return R2Stub(storage_root=root)

    account_id = os.environ.get("R2_ACCOUNT_ID")
    access_key_id = os.environ.get("R2_ACCESS_KEY_ID")
    secret_access_key = os.environ.get("R2_SECRET_ACCESS_KEY")
    bucket = os.environ.get("R2_BUCKET")

    if account_id and access_key_id and secret_access_key and bucket:
        return R2Client(
            account_id=account_id,
            access_key_id=access_key_id,
            secret_access_key=secret_access_key,
            bucket=bucket,
        )

    # Fallback to stub
    root = storage_root or Path(os.environ.get("SIMDRIVE_STORAGE_ROOT", "/tmp/simdrive-cloud"))
    return R2Stub(storage_root=root)
