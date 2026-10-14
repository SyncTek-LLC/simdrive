"""type_text reads the field back after typing and reports whether the
requested text landed (PP-5036).

Dispatch success is not delivery: a type_text call made just after the
field's screen changed lost the whole string while reporting
dispatch_succeeded=true. These tests pin the read-back contract with fakes;
no simulator is involved.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from simdrive import observe as observe_mod
from simdrive import readback, server, som
from simdrive import session as session_mod
from simdrive.sim import Device

_FAKE_UDID = "31471BBD-0000-PP5036-VERIFY-LANDED"


def _make_sim_session(tmp_path: Path, sid: str = "verify-landed-1"):
    session_mod._SESSIONS.pop(sid, None)
    d = Device(udid=_FAKE_UDID, name="Test Sim", os_version="26.0", state="active")
    workdir = tmp_path / "sessions" / sid
    workdir.mkdir(parents=True, exist_ok=True)
    s = session_mod.Session(
        session_id=sid, device=d, workdir=workdir, target="simulator",
        last_screenshot_w=1206, last_screenshot_h=2622,
    )
    session_mod._SESSIONS[sid] = s
    return s


def _obs(tmp_path: Path, marks=()) -> observe_mod.Observation:
    path = tmp_path / "frame.png"
    path.write_bytes(b"\x89PNG\r\n\x1a\n")
    return observe_mod.Observation(
        screenshot_path=path, annotated_path=None, screenshot_w=1206, screenshot_h=2622,
        window_bounds=None, captured_at=0.0, marks=list(marks), recent_logs=None,
    )


def _read(value, *, method="pasteboard", secure=False, copied=True, reason=None):
    return readback.FieldRead(value=value, method=method, secure=secure, copied=copied, reason=reason)


def _run(tmp_path, args, reads, marks=()):
    """Drive tool_type_text with a scripted sequence of field reads.

    hid_inject is patched by dotted path: server imports it lazily, and another
    test module purges simdrive.* from sys.modules, so a collection-time
    module reference can differ from the one server resolves.

    Returns (response, typed_strings, reads_consumed).
    """
    s = _make_sim_session(tmp_path)
    typed: list[str] = []
    queue = list(reads)
    consumed: list = []

    def fake_read(udid, device_name):
        r = queue.pop(0)
        consumed.append(r)
        return r

    with patch.object(server.act, "type_text", side_effect=lambda text, udid=None: typed.append(text)), \
         patch.object(server.act, "_backend", return_value="hid"), \
         patch.object(server.act, "press_key", return_value=None), \
         patch("simdrive.hid_inject.chord", return_value=None), \
         patch.object(server, "_compute_ssim", lambda pre, post: 1.0), \
         patch.object(observe_mod, "observe", side_effect=lambda *a, **kw: _obs(tmp_path, marks)), \
         patch.object(readback, "read_focused_field", side_effect=fake_read):
        resp = server.tool_type_text({"session_id": s.session_id, **args})
    return resp, typed, consumed


class TestTypeTextVerifyLanded:
    def test_exact_match_reports_verified_true(self, tmp_path):
        resp, typed, _ = _run(tmp_path, {"text": "hello", "clear_first": True}, [_read("hello")])
        assert resp["ok"] is True
        assert resp["verified"] is True
        assert resp["verification"]["match"] == "exact"
        assert resp["verification"]["attempts"] == 1
        assert typed == ["hello"]

    def test_first_letter_auto_capitalization_is_accepted(self, tmp_path):
        resp, typed, _ = _run(tmp_path, {"text": "hello world", "clear_first": True},
                              [_read("Hello world")])
        assert resp["verified"] is True
        assert resp["verification"]["match"] == "auto_capitalized"
        assert typed == ["hello world"]

    def test_capitalization_beyond_first_letter_is_a_mismatch(self, tmp_path):
        resp, _, _ = _run(tmp_path, {"text": "hello", "clear_first": True},
                          [_read("HEllo"), _read("HEllo")])
        assert resp["verified"] is False

    def test_secure_field_compares_length_only_and_does_not_echo_value(self, tmp_path):
        resp, typed, _ = _run(tmp_path, {"text": "hunter2", "clear_first": True},
                              [_read("•" * 7, method="ax", secure=True)])
        assert resp["verified"] is True
        assert resp["verification"]["match"] == "secure_length"
        assert "hunter2" not in repr(resp["verification"])
        assert resp["verification"]["landed_length"] == 7
        assert typed == ["hunter2"]

    def test_secure_field_wrong_length_is_a_mismatch(self, tmp_path):
        resp, typed, _ = _run(tmp_path, {"text": "hunter2", "clear_first": True},
                              [_read("•" * 3, method="ax", secure=True),
                               _read("•" * 3, method="ax", secure=True)])
        assert resp["verified"] is False
        assert "hunter2" not in repr(resp)
        assert typed == ["hunter2", "hunter2"]

    def test_dropped_text_is_cleared_and_retyped_once(self, tmp_path):
        resp, typed, consumed = _run(
            tmp_path, {"text": "hello", "clear_first": True},
            [_read("", copied=False), _read("hello")],
        )
        assert resp["ok"] is True
        assert resp["verified"] is True
        assert resp["verification"]["attempts"] == 2
        assert typed == ["hello", "hello"]
        assert len(consumed) == 2

    def test_dropped_twice_surfaces_failure_with_diff(self, tmp_path):
        resp, typed, _ = _run(
            tmp_path, {"text": "hello", "clear_first": True},
            [_read("hlo"), _read("hlo")],
        )
        assert resp["ok"] is False
        assert resp["verified"] is False
        assert typed == ["hello", "hello"]
        err = resp["error"]
        assert err["code"] == "text_not_landed"
        assert err["expected"] == "hello"
        assert err["landed"] == "hlo"
        missing = "".join(d["expected"] for d in err["diff"] if d["op"] in ("delete", "replace"))
        assert missing == "el"

    def test_unreadable_field_reports_verified_null_with_reason(self, tmp_path):
        resp, typed, _ = _run(
            tmp_path, {"text": "hello", "clear_first": True},
            [_read(None, method=None, reason="no focused field")],
        )
        assert resp["ok"] is True
        assert resp["verified"] is None
        assert "no focused field" in resp["verification"]["reason"]
        assert typed == ["hello"]

    def test_append_expects_previous_value_plus_text(self, tmp_path):
        resp, typed, _ = _run(tmp_path, {"text": "bar"}, [_read("foo"), _read("foobar")])
        assert resp["verified"] is True
        assert resp["verification"]["expected"] == "foobar"
        assert typed == ["bar"]

    def test_append_retry_restores_full_expected_value(self, tmp_path):
        resp, typed, _ = _run(tmp_path, {"text": "bar"},
                              [_read("foo"), _read("foo"), _read("foobar")])
        assert resp["verified"] is True
        assert typed == ["bar", "foobar"]

    def test_append_with_unreadable_previous_value_reports_null(self, tmp_path):
        resp, typed, consumed = _run(tmp_path, {"text": "bar"},
                                     [_read(None, method=None, reason="no focused field")])
        assert resp["verified"] is None
        assert typed == ["bar"]
        assert len(consumed) == 1

    def test_nothing_copied_but_text_visible_reports_null_without_retry(self, tmp_path):
        """A field that committed and lost focus copies nothing, yet OCR still
        shows the text; retyping into no responder would be wrong."""
        mark = som.Mark(id=1, x=10, y=10, w=100, h=20, text="freedom", confidence=0.9)
        resp, typed, _ = _run(tmp_path, {"text": "freedom", "clear_first": True},
                              [_read("", copied=False)], marks=[mark])
        assert resp["verified"] is None
        assert typed == ["freedom"]

    def test_verify_false_skips_read_back(self, tmp_path):
        resp, typed, consumed = _run(tmp_path, {"text": "hello", "verify": False}, [])
        assert resp["verified"] is None
        assert consumed == []
        assert typed == ["hello"]
        assert "disabled" in resp["verification"]["reason"]

    def test_response_guidance_no_longer_says_do_not_retry(self, tmp_path):
        resp, _, _ = _run(tmp_path, {"text": "hello", "clear_first": True}, [_read("hello")])
        assert "do NOT retry" not in repr(resp)

    def test_verify_declared_in_type_text_schema(self):
        tool = next(t for t in server._TOOLS if t["name"] == "type_text")
        assert "verify" in server._schema_declared_params(tool)


class TestCompare:
    @pytest.mark.parametrize("expected,landed,secure,match", [
        ("hello", "hello", False, "exact"),
        ("hello", "Hello", False, "auto_capitalized"),
        ("it's", "it’s", False, "smart_punctuation"),
        ("pw", "••", True, "secure_length"),
        ("", "", False, "exact"),
    ])
    def test_accepts(self, expected, landed, secure, match):
        assert readback.compare(expected, landed, secure=secure) == match

    @pytest.mark.parametrize("expected,landed,secure", [
        ("hello", "", False),
        ("hello", "hell", False),
        ("Hello", "hello", False),
        ("pw", "•", True),
    ])
    def test_rejects(self, expected, landed, secure):
        assert readback.compare(expected, landed, secure=secure) is None


class TestPasteboardRead:
    def _patched(self, pasteboard: list[str], copy_result: str | None):
        """Fake simctl pasteboard + HID. copy_result=None means Cmd-C copies nothing."""
        keys: list = []

        def chord(udid, mod, key):
            keys.append(("chord", mod, key))
            if key == "c" and copy_result is not None:
                pasteboard[0] = copy_result

        def set_pb(udid, text):
            pasteboard[0] = text

        return keys, [
            patch.object(readback.hid_inject, "chord", side_effect=chord),
            patch.object(readback.hid_inject, "press_key",
                         side_effect=lambda udid, code: keys.append(("key", code))),
            patch.object(readback.sim, "get_pasteboard", side_effect=lambda udid: pasteboard[0]),
            patch.object(readback.sim, "set_pasteboard", side_effect=set_pb),
            patch.object(readback.time, "sleep", lambda s: None),
        ]

    def _call(self, pasteboard, copy_result):
        keys, patches = self._patched(pasteboard, copy_result)
        for p in patches:
            p.start()
        try:
            return readback.read_pasteboard(_FAKE_UDID), keys
        finally:
            for p in patches:
                p.stop()

    def test_reads_value_restores_pasteboard_and_collapses_selection(self):
        pb = ["user clipboard"]
        r, keys = self._call(pb, "typed value")
        assert r.value == "typed value"
        assert r.copied is True
        assert pb[0] == "user clipboard"
        assert keys[-1] == ("key", readback._HID_ARROW_RIGHT)

    def test_nothing_copied_reads_as_empty_not_copied(self):
        pb = ["user clipboard"]
        r, _ = self._call(pb, None)
        assert r.value == ""
        assert r.copied is False
        assert pb[0] == "user clipboard"
