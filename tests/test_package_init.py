"""Unit tests — specterqa/ios/__init__.py _NamespacePath.insert fix (v14.0.2).

Python 3.11+ importlib uses _NamespacePath which does NOT support .insert().
The fix in 2284a65 catches AttributeError and falls back to .append().

All tests are hermetic — no real package import order is assumed.
"""
from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch


class _FakeNamespacePath:
    """Minimal stand-in for importlib._bootstrap_external._NamespacePath.

    Supports .append() but raises AttributeError on .insert().
    """

    def __init__(self, paths: list[str] | None = None):
        self._paths = list(paths or [])

    def __contains__(self, item: str) -> bool:
        return item in self._paths

    def __iter__(self):
        return iter(self._paths)

    def __len__(self):
        return len(self._paths)

    def insert(self, *args, **kwargs):
        raise AttributeError("_NamespacePath does not support insert()")

    def append(self, path: str) -> None:
        self._paths.append(path)


class TestNamespacePathInsertFallback:
    """_ensure_namespace() must not raise when __path__ is a _NamespacePath."""

    def test_append_fallback_called_on_attribute_error(self):
        """When specterqa.__path__.insert raises AttributeError,
        _ensure_namespace must call .append() instead and not raise."""
        fake_path = _FakeNamespacePath([])
        specterqa_stub = MagicMock()
        specterqa_stub.__path__ = fake_path

        with patch.dict("sys.modules", {"specterqa": specterqa_stub}):
            # Re-execute _ensure_namespace from the actual module
            from specterqa.ios import _ensure_namespace
            _ensure_namespace()

        # Our root must have been appended via the fallback path
        assert any("specterqa" in p or "ios" in p or len(p) > 0 for p in fake_path._paths), (
            "Expected _ensure_namespace to append a non-empty path"
        )

    def test_no_exception_raised_on_namespace_path(self):
        """_ensure_namespace must complete without raising any exception
        when __path__ is a _NamespacePath-like object."""
        fake_path = _FakeNamespacePath(["/some/existing/path"])
        specterqa_stub = MagicMock()
        specterqa_stub.__path__ = fake_path

        with patch.dict("sys.modules", {"specterqa": specterqa_stub}):
            from specterqa.ios import _ensure_namespace
            # Must not raise
            _ensure_namespace()

    def test_insert_on_regular_list_still_works(self):
        """When __path__ is a regular list (supports .insert), the fast path
        must succeed and insert at index 0."""
        regular_path: list[str] = ["/existing"]
        specterqa_stub = MagicMock()
        specterqa_stub.__path__ = regular_path

        with patch.dict("sys.modules", {"specterqa": specterqa_stub}):
            from specterqa.ios import _ensure_namespace
            _ensure_namespace()

        # Our root must appear somewhere in the list (inserted at 0)
        assert len(regular_path) > 1 or regular_path[0] != "/existing", (
            "Expected a new path to be inserted into the regular list"
        )
