"""Unit tests for the local-first, scrubbed crash sink (WS-4).

Contract enforced in `simdrive/src/simdrive/observability/crash_sink.py`:

  * Records capture only the *shape* of a crash — class + module + stack
    frames (basename/line/func) + version/os/python. No PII.
  * Absolute paths (which leak the username) are reduced to basenames.
  * The exception *message* is never stored (it can embed user data).
  * The sink is local and offline — no network code, ever.
  * record_crash / install_crash_sink are best-effort and never raise.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from simdrive.observability import crash_sink


@pytest.fixture(autouse=True)
def _reset_hook_state(monkeypatch: pytest.MonkeyPatch):
    """Isolate the excepthook global state + env between tests."""
    monkeypatch.setattr(crash_sink, "_INSTALLED", False, raising=False)
    monkeypatch.setattr(crash_sink, "_PREV_HOOK", None, raising=False)
    monkeypatch.delenv("HEKA_CRASH_SINK_DISABLED", raising=False)
    monkeypatch.delenv("HEKA_CRASH_DIR", raising=False)
    yield


def _raise_and_capture() -> BaseException:
    """Raise an exception whose message embeds PII, return the caught object."""
    try:
        raise ValueError("secret path /Users/somebody/inbox leaked@example.com")
    except ValueError as exc:
        return exc


class TestBuildCrashRecord:

    def test_has_expected_keys(self) -> None:
        rec = crash_sink.build_crash_record(_raise_and_capture())
        assert set(rec.keys()) == {
            "schema_version", "ts", "exc_type", "exc_module",
            "frames", "package_version", "os", "python",
        }

    def test_captures_class_not_message(self) -> None:
        rec = crash_sink.build_crash_record(_raise_and_capture())
        assert rec["exc_type"] == "ValueError"
        # The message (with PII) must NOT appear anywhere in the record.
        blob = str(rec)
        assert "leaked@example.com" not in blob
        assert "/Users/somebody" not in blob

    def test_frames_are_basenames_only(self) -> None:
        rec = crash_sink.build_crash_record(_raise_and_capture())
        assert rec["frames"], "expected at least one frame"
        for frame in rec["frames"]:
            assert set(frame.keys()) == {"file", "line", "func"}
            # basename only — no directory separators (no username leak).
            assert "/" not in frame["file"]

    def test_os_is_family(self) -> None:
        rec = crash_sink.build_crash_record(_raise_and_capture())
        assert rec["os"] in ("darwin", "linux", "other")

    def test_python_is_major_minor_only(self) -> None:
        rec = crash_sink.build_crash_record(_raise_and_capture())
        assert rec["python"].count(".") == 1

    def test_ts_is_iso_utc(self) -> None:
        rec = crash_sink.build_crash_record(_raise_and_capture(), now=1716681600.0)
        assert rec["ts"] == "2024-05-26T00:00:00Z"


class TestRecordCrash:

    def test_writes_file_and_returns_path(self, tmp_path: Path) -> None:
        path = crash_sink.record_crash(_raise_and_capture(), crash_dir=tmp_path)
        assert path is not None
        assert path.exists()
        assert path.parent == tmp_path
        assert path.name.startswith("crash-")

    def test_written_file_has_no_pii(self, tmp_path: Path) -> None:
        path = crash_sink.record_crash(_raise_and_capture(), crash_dir=tmp_path)
        assert path is not None
        text = path.read_text(encoding="utf-8")
        assert "leaked@example.com" not in text
        assert "/Users/somebody" not in text

    def test_disabled_env_writes_nothing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("HEKA_CRASH_SINK_DISABLED", "1")
        path = crash_sink.record_crash(_raise_and_capture(), crash_dir=tmp_path)
        assert path is None
        assert list(tmp_path.glob("crash-*.json")) == []

    def test_never_raises_on_bad_dir(self, tmp_path: Path) -> None:
        # Point at a path whose parent is a file → mkdir would fail; must be swallowed.
        bad_parent = tmp_path / "afile"
        bad_parent.write_text("x")
        result = crash_sink.record_crash(
            _raise_and_capture(), crash_dir=bad_parent / "sub"
        )
        assert result is None


class TestListCrashes:

    def test_empty_when_no_dir(self, tmp_path: Path) -> None:
        assert crash_sink.list_crashes(tmp_path / "missing") == []

    def test_lists_newest_first(self, tmp_path: Path) -> None:
        crash_sink.record_crash(
            _raise_and_capture(), crash_dir=tmp_path, now=1716681600.0
        )
        crash_sink.record_crash(
            _raise_and_capture(), crash_dir=tmp_path, now=1716768000.0
        )
        recs = crash_sink.list_crashes(tmp_path)
        assert len(recs) == 2
        assert recs[0]["ts"] >= recs[1]["ts"]

    def test_skips_corrupt_files(self, tmp_path: Path) -> None:
        crash_sink.record_crash(_raise_and_capture(), crash_dir=tmp_path)
        (tmp_path / "crash-garbage.json").write_text("{not json")
        recs = crash_sink.list_crashes(tmp_path)
        assert len(recs) == 1


class TestInstallCrashSink:

    def test_disabled_env_returns_false(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("HEKA_CRASH_SINK_DISABLED", "1")
        assert crash_sink.install_crash_sink() is False

    def test_install_is_idempotent_and_chains(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import sys

        called = {"prev": 0}

        def prev_hook(*a):  # noqa: ANN002
            called["prev"] += 1

        monkeypatch.setattr(sys, "excepthook", prev_hook)
        assert crash_sink.install_crash_sink(crash_dir=tmp_path) is True
        # Second call is a no-op (idempotent) — hook not re-wrapped.
        assert crash_sink.install_crash_sink(crash_dir=tmp_path) is True

        exc = _raise_and_capture()
        sys.excepthook(type(exc), exc, exc.__traceback__)

        # The crash was recorded AND the previous hook was still called.
        assert called["prev"] == 1
        assert len(crash_sink.list_crashes(tmp_path)) == 1
