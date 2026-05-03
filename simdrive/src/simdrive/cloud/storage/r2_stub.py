"""R2Stub — local-disk-backed stub implementing the R2 storage interface.

WHY a stub: Cycle 1 doesn't need real R2/Cloudflare integration. The stub
matches the same interface so cycle 2 can swap in a real R2 client without
changing callers. All blob data is stored as files under `storage_root`.

Interface contract (must be preserved in the real R2 client):
  put_object(key: str, data: bytes) -> None
  get_object(key: str) -> bytes | None
  delete_object(key: str) -> None
  list_objects(prefix: str) -> list[str]
  presigned_url(key: str, expires_in: int) -> str
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional
import hashlib
import time


class R2Stub:
    """Local-filesystem-backed stub of the R2 object storage interface.

    All objects are stored as files under `storage_root / <key-path>`.
    The key is used as a relative file path (slashes become subdirectories).

    This class is safe for single-process use; no locking.
    Thread-safety is not required for the cycle-1 use case.
    """

    def __init__(self, storage_root: Path) -> None:
        self._root = Path(storage_root)
        self._root.mkdir(parents=True, exist_ok=True)

    def _path_for(self, key: str) -> Path:
        """Convert an object key to an absolute filesystem path.

        WHY: strip leading slashes so the key is always relative to root,
        preventing path-traversal attacks if keys come from user input.
        """
        safe_key = key.lstrip("/")
        return self._root / safe_key

    def put_object(self, key: str, data: bytes) -> None:
        """Store `data` at `key`. Creates parent directories as needed."""
        p = self._path_for(key)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)

    def get_object(self, key: str) -> Optional[bytes]:
        """Return stored bytes for `key`, or None if the key doesn't exist."""
        p = self._path_for(key)
        if not p.exists():
            return None
        return p.read_bytes()

    def delete_object(self, key: str) -> None:
        """Delete the object at `key`. No-op if it doesn't exist."""
        p = self._path_for(key)
        if p.exists():
            p.unlink()

    def list_objects(self, prefix: str) -> list[str]:
        """Return all object keys that start with `prefix`.

        WHY recursive glob: mirrors R2 ListObjectsV2 with a prefix filter.
        Returns keys relative to storage_root (matching the key format used
        in put_object/get_object).
        """
        prefix_path = self._path_for(prefix)
        if not prefix_path.exists():
            return []

        results: list[str] = []
        if prefix_path.is_dir():
            for p in prefix_path.rglob("*"):
                if p.is_file():
                    rel = str(p.relative_to(self._root))
                    results.append(rel)
        else:
            # Treat as file or non-existent prefix directory
            results.append(str(prefix_path.relative_to(self._root)))

        return sorted(results)

    def presigned_url(self, key: str, expires_in: int) -> str:
        """Return a stub presigned URL for the given key.

        WHY: In the real R2 client, this creates a temporary signed URL
        that clients can download directly from R2 without going through
        the API server. The stub returns a deterministic fake URL.

        Raises FileNotFoundError if the object does not exist.
        """
        p = self._path_for(key)
        if not p.exists():
            raise FileNotFoundError(f"R2Stub: object not found for key {key!r}")

        # Deterministic stub URL: encodes key + expiry timestamp
        expires_ts = int(time.time()) + expires_in
        fingerprint = hashlib.sha256(f"{key}:{expires_ts}".encode()).hexdigest()[:16]
        return f"https://stub.r2.simdrive.dev/{key}?X-Expires={expires_ts}&X-Sig={fingerprint}"
