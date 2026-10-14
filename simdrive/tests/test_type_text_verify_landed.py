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

from simdrive import errors, readback, server, som
from simdrive import observe as observe_mod
from simdrive import session as session_mod
from simdrive.sim import Device

_FAKE_UDID = "31471BBD-0000-PP5036-VERIFY-LANDED"
_BULLET = "•"


def _make_sim_session(tmp_path: Path, sid: str = "verify-landed-1", target: str = "simulator"):
    session_mod._SESSIONS.pop(sid, None)
    d = Device(udid=_FAKE_UDID, name="Test Sim", os_version="26.0", state="active")
    workdir = tmp_path / "sessions" / sid
    workdir.mkdir(parents=True, exist_ok=True)
    s = session_mod.Session(
        session_id=sid, device=d, workdir=workdir, target=target,
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


def _pb(value, copied=True):
    """A pasteboard read: a successful copy proves a plain field and leaves it selected."""
    return readback.FieldRead(value=value, method="pasteboard", copied=copied,
                              plain=copied, selected=copied)


def _ax(value, secure=False):
    return readback.FieldRead(value=value, method="ax", secure=secure, plain=not secure)


def _none(reason="no focused field"):
    return readback.FieldRead(value=None, method=None, reason=reason)


class _Run:
    def __init__(self, resp, typed, consumed, events):
        self.resp, self.typed, self.consumed, self.events = resp, typed, consumed, events


def _run(tmp_path, args, reads, marks=(), chord_error=None, type_error_on_call=None) -> _Run:
    """Drive tool_type_text with a scripted sequence of field reads.

    hid_inject is patched by dotted path: server imports it lazily, and another
    test module purges simdrive.* from sys.modules, so a collection-time
    module reference can differ from the one server resolves.
    """
    s = _make_sim_session(tmp_path)
    typed: list[str] = []
    events: list[tuple] = []
    queue = list(reads)
    consumed: list = []

    def fake_read(udid, device_name, mode="auto"):
        r = queue.pop(0)
        consumed.append((mode, r))
        events.append(("read", mode))
        return r

    def fake_type(text, udid=None):
        typed.append(text)
        events.append(("type", text))
        if type_error_on_call is not None and len(typed) == type_error_on_call:
            raise RuntimeError("simdrive-input text failed")

    def fake_chord(udid, mod, key):
        events.append(("chord", mod, key))
        if chord_error is not None and any(e[0] == "type" for e in events):
            raise chord_error

    with patch.object(server.act, "type_text", side_effect=fake_type), \
         patch.object(server.act, "_backend", return_value="hid"), \
         patch.object(server.act, "press_key", side_effect=lambda key, udid=None: events.append(("key", key))), \
         patch("simdrive.hid_inject.chord", side_effect=fake_chord), \
         patch.object(server, "_compute_ssim", lambda pre, post: 1.0), \
         patch.object(observe_mod, "observe", side_effect=lambda *a, **kw: _obs(tmp_path, marks)), \
         patch.object(readback, "read_focused_field", side_effect=fake_read), \
         patch.object(readback, "collapse_selection", create=True,
                      side_effect=lambda udid: events.append(("collapse",))):
        resp = server.tool_type_text({"session_id": s.session_id, **args})
    return _Run(resp, typed, consumed, events)


PB = {"verify": "pasteboard"}


class TestVerifyModes:
    def test_default_auto_reads_with_ax_only_and_reports_null_without_it(self, tmp_path):
        r = _run(tmp_path, {"text": "hello"}, [_none(readback.NO_SIDE_EFFECT_FREE_READER)])
        assert r.resp["verified"] is None
        assert "side-effect-free" in r.resp["verification"]["reason"]
        assert [mode for mode, _ in r.consumed] == ["auto"]
        assert not [e for e in r.events if e[0] in ("chord", "collapse", "key")]
        assert r.typed == ["hello"]

    def test_verify_true_is_auto(self, tmp_path):
        r = _run(tmp_path, {"text": "hello", "clear_first": True, "verify": True}, [_ax("hello")])
        assert r.resp["verified"] is True
        assert [mode for mode, _ in r.consumed] == ["auto"]

    def test_verify_false_skips_read_back(self, tmp_path):
        r = _run(tmp_path, {"text": "hello", "verify": False}, [])
        assert r.resp["verified"] is None
        assert r.consumed == []
        assert "disabled" in r.resp["verification"]["reason"]
        assert r.typed == ["hello"]

    def test_unknown_verify_mode_is_rejected(self, tmp_path):
        with pytest.raises(errors.SimdriveError):
            _run(tmp_path, {"text": "hello", "verify": "ocr"}, [])

    def test_verify_declared_in_type_text_schema(self):
        tool = next(t for t in server._TOOLS if t["name"] == "type_text")
        assert "verify" in server._schema_declared_params(tool)

    def test_device_session_reports_null(self, tmp_path):
        s = _make_sim_session(tmp_path, sid="verify-device", target="device")

        class _Wda:
            def type_text(self, text):
                pass

        s.wda_client = _Wda()
        with patch.object(server, "tool_observe", return_value={}), \
             patch.object(observe_mod, "observe", return_value=None):
            resp = server.tool_type_text({"session_id": s.session_id, "text": "hi"})
        assert resp["verified"] is None
        assert "real devices" in resp["verification"]["reason"]


class TestVerdicts:
    def test_exact_match_reports_verified_true(self, tmp_path):
        r = _run(tmp_path, {"text": "hello", "clear_first": True, **PB}, [_pb("old"), _pb("hello")])
        assert r.resp["ok"] is True
        assert r.resp["verified"] is True
        assert r.resp["verification"]["match"] == "exact"
        assert r.resp["verification"]["attempts"] == 1
        assert r.typed == ["hello"]

    def test_first_letter_auto_capitalization_is_accepted(self, tmp_path):
        r = _run(tmp_path, {"text": "hello world", "clear_first": True}, [_ax("Hello world")])
        assert r.resp["verified"] is True
        assert r.resp["verification"]["match"] == "auto_capitalized"

    def test_capitalization_beyond_first_letter_is_a_mismatch(self, tmp_path):
        r = _run(tmp_path, {"text": "hello", "clear_first": True}, [_ax("HEllo"), _ax("HEllo")])
        assert r.resp["verified"] is False

    def test_secure_ax_field_compares_length_only_and_does_not_echo_value(self, tmp_path):
        r = _run(tmp_path, {"text": "hunter2", "clear_first": True}, [_ax(_BULLET * 7, secure=True)])
        assert r.resp["verified"] is True
        assert r.resp["verification"]["match"] == "secure_length"
        assert "hunter2" not in repr(r.resp["verification"])
        assert r.typed == ["hunter2"]

    def test_secure_ax_field_wrong_length_is_retried_then_fails_without_echo(self, tmp_path):
        r = _run(tmp_path, {"text": "hunter2", "clear_first": True},
                 [_ax(_BULLET * 3, secure=True), _ax(_BULLET * 3, secure=True)])
        assert r.resp["verified"] is False
        assert "hunter2" not in repr(r.resp)
        assert r.typed == ["hunter2", "hunter2"]

    def test_dropped_twice_surfaces_failure_with_diff(self, tmp_path):
        r = _run(tmp_path, {"text": "hello", "clear_first": True, **PB},
                 [_pb("x"), _pb("hlo"), _pb("hlo")])
        assert r.resp["ok"] is False
        assert r.resp["verified"] is False
        assert r.typed == ["hello", "hello"]
        err = r.resp["error"]
        assert err["code"] == "text_not_landed"
        assert (err["expected"], err["landed"]) == ("hello", "hlo")
        missing = "".join(d["expected"] for d in err["diff"] if d["op"] in ("delete", "replace"))
        assert missing == "el"

    def test_unreadable_field_reports_verified_null_with_reason(self, tmp_path):
        r = _run(tmp_path, {"text": "hello", "clear_first": True, **PB}, [_none(), _none()])
        assert r.resp["ok"] is True
        assert r.resp["verified"] is None
        assert "no focused field" in r.resp["verification"]["reason"]
        assert r.typed == ["hello"]

    def test_append_expects_previous_value_plus_text(self, tmp_path):
        r = _run(tmp_path, {"text": "bar", **PB}, [_pb("foo"), _pb("foobar")])
        assert r.resp["verified"] is True
        assert r.resp["verification"]["expected"] == "foobar"
        assert r.typed == ["bar"]

    def test_append_retry_restores_full_expected_value(self, tmp_path):
        r = _run(tmp_path, {"text": "bar", **PB}, [_pb("foo"), _pb("foo"), _pb("foobar")])
        assert r.resp["verified"] is True
        assert r.typed == ["bar", "foobar"]

    def test_append_at_mid_field_caret_is_accepted_without_retry(self, tmp_path):
        """AX sends no keys, so the caret stays where tap_first put it."""
        r = _run(tmp_path, {"text": "bar"}, [_ax("foo"), _ax("fbaroo")])
        assert r.resp["verified"] is True
        assert r.resp["verification"]["match"] == "inserted_at_caret"
        assert r.typed == ["bar"]

    def test_response_guidance_no_longer_says_do_not_retry(self, tmp_path):
        r = _run(tmp_path, {"text": "hello", "clear_first": True}, [_ax("hello")])
        assert "do NOT retry" not in repr(r.resp)


class TestNoRetryWhenUnsafe:
    def test_pasteboard_secure_field_copies_nothing_reports_null_without_retry(self, tmp_path):
        r = _run(tmp_path, {"text": "hunter2", "clear_first": True, **PB},
                 [_pb("", copied=False), _pb("", copied=False)])
        assert r.resp["verified"] is None
        assert r.resp["ok"] is True
        assert r.typed == ["hunter2"]
        assert [e for e in r.events if e[0] == "chord"] == [("chord", "cmd", "a")]

    def test_pasteboard_append_secure_field_reports_null_and_never_clears(self, tmp_path):
        r = _run(tmp_path, {"text": "2", **PB}, [_pb("", copied=False)])
        assert r.resp["verified"] is None
        assert r.typed == ["2"]
        assert not [e for e in r.events if e[0] in ("chord", "key")]

    def test_append_with_unreadable_previous_value_reports_null(self, tmp_path):
        r = _run(tmp_path, {"text": "bar", **PB}, [_none()])
        assert r.resp["verified"] is None
        assert r.typed == ["bar"]
        assert len(r.consumed) == 1

    def test_append_to_secure_ax_field_mismatch_is_not_retyped(self, tmp_path):
        """The previous content of a secure field is only bullets; retyping it would corrupt it."""
        r = _run(tmp_path, {"text": "ab"}, [_ax(_BULLET * 3, secure=True), _ax(_BULLET * 4, secure=True)])
        assert r.resp["verified"] is False
        assert r.typed == ["ab"]
        assert not [e for e in r.events if e[0] == "chord"]

    @pytest.mark.parametrize("text", ["query\n", "line one\rline two"])
    def test_text_with_newline_is_not_verified_or_retyped(self, tmp_path, text):
        r = _run(tmp_path, {"text": text, "clear_first": True, **PB}, [])
        assert r.resp["verified"] is None
        assert "newline" in r.resp["verification"]["reason"]
        assert r.consumed == []
        assert r.typed == [text]

    def test_retry_chord_failure_is_reported_not_raised(self, tmp_path):
        r = _run(tmp_path, {"text": "bar"}, [_ax("foo"), _ax("fo")], chord_error=RuntimeError("hid gone"))
        assert r.resp["ok"] is False
        assert r.resp["verified"] is False
        assert r.resp["error"]["code"] == "retry_failed"
        assert "hid gone" in r.resp["error"]["message"]
        assert r.typed == ["bar"]

    def test_retry_type_failure_is_reported_not_raised(self, tmp_path):
        r = _run(tmp_path, {"text": "hello", "clear_first": True}, [_ax(""), _ax("")],
                 type_error_on_call=2)
        assert r.resp["ok"] is False
        assert r.resp["error"]["code"] == "retry_failed"


class TestWholeStringDrop:
    def test_empty_known_plain_field_is_retried_even_when_text_is_visible_elsewhere(self, tmp_path):
        """The text on screen (e.g. a results header) is not the field."""
        mark = som.Mark(id=1, x=10, y=10, w=100, h=20, text="freedom", confidence=0.9)
        r = _run(tmp_path, {"text": "freedom", "clear_first": True, **PB},
                 [_pb("old"), _pb("", copied=False), _pb("freedom")], marks=[mark])
        assert r.resp["verified"] is True
        assert r.resp["verification"]["attempts"] == 2
        assert r.typed == ["freedom", "freedom"]

    def test_empty_ax_plain_field_is_retried(self, tmp_path):
        r = _run(tmp_path, {"text": "hello", "clear_first": True}, [_ax(""), _ax("hello")])
        assert r.resp["verified"] is True
        assert r.resp["verification"]["attempts"] == 2
        assert r.typed == ["hello", "hello"]


class TestSelectionHandling:
    def test_append_pre_read_selection_is_collapsed_before_typing(self, tmp_path):
        r = _run(tmp_path, {"text": "bar", **PB}, [_pb("foo"), _pb("foobar")])
        order = [e[0] for e in r.events]
        assert order.index("collapse") < order.index("type")
        assert order.count("collapse") == 2

    def test_clear_first_pre_read_selection_is_replaced_not_collapsed(self, tmp_path):
        r = _run(tmp_path, {"text": "hello", "clear_first": True, **PB}, [_pb("old"), _pb("hello")])
        before_type = r.events[:[e[0] for e in r.events].index("type")]
        assert ("collapse",) not in before_type
        assert ("chord", "cmd", "a") in before_type

    def test_nothing_copied_is_not_followed_by_arrow_keys(self, tmp_path):
        r = _run(tmp_path, {"text": "pw", "clear_first": True, **PB},
                 [_pb("", copied=False), _pb("", copied=False)])
        assert not [e for e in r.events if e[0] in ("collapse", "key") and e != ("key", "delete")]


class TestCompare:
    @pytest.mark.parametrize("expected,landed,secure,match", [
        ("hello", "hello", False, "exact"),
        ("hello", "Hello", False, "auto_capitalized"),
        ("it's", "it’s", False, "smart_punctuation"),
        ("pw", _BULLET * 2, True, "secure_length"),
        ("", "", False, "exact"),
    ])
    def test_accepts(self, expected, landed, secure, match):
        assert readback.compare(expected, landed, secure=secure) == match

    @pytest.mark.parametrize("expected,landed,secure", [
        ("hello", "", False),
        ("hello", "hell", False),
        ("Hello", "hello", False),
        ("pw", _BULLET, True),
    ])
    def test_rejects(self, expected, landed, secure):
        assert readback.compare(expected, landed, secure=secure) is None

    def test_insertion_at_caret_needs_previous_and_text(self):
        assert readback.compare("foobar", "fbaroo", previous="foo", text="bar") == "inserted_at_caret"
        assert readback.compare("foobar", "fbaroo") is None
        assert readback.compare("foobar", "fbxroo", previous="foo", text="bar") is None


class TestPasteboardReader:
    def _call(self, pasteboard: list[str], copy_result: str | None):
        """Fake simctl pasteboard + HID. copy_result=None means Cmd-C copies nothing."""
        keys: list = []
        pb_writes: list = []

        def chord(udid, mod, key):
            keys.append(("chord", mod, key))
            if key == "c" and copy_result is not None:
                pasteboard[0] = copy_result

        def set_pb(udid, text):
            pb_writes.append(text)
            pasteboard[0] = text

        with patch.object(readback.hid_inject, "chord", side_effect=chord), \
             patch.object(readback.hid_inject, "press_key",
                          side_effect=lambda udid, code: keys.append(("key", code))), \
             patch.object(readback.sim, "get_pasteboard", side_effect=lambda udid: pasteboard[0]), \
             patch.object(readback.sim, "set_pasteboard", side_effect=set_pb), \
             patch.object(readback.time, "sleep", lambda s: None):
            return readback.read_pasteboard(_FAKE_UDID), keys, pb_writes

    def test_reads_value_restores_pasteboard_and_leaves_selection_to_caller(self):
        pb = ["user clipboard"]
        r, keys, _ = self._call(pb, "typed value")
        assert (r.value, r.copied, r.plain, r.selected) == ("typed value", True, True, True)
        assert pb[0] == "user clipboard"
        assert not [k for k in keys if k[0] == "key"]

    def test_nothing_copied_reads_as_empty_not_copied(self):
        pb = ["user clipboard"]
        r, _, _ = self._call(pb, None)
        assert (r.value, r.copied, r.plain, r.selected) == ("", False, False, False)
        assert pb[0] == "user clipboard"

    def test_empty_saved_pasteboard_is_not_written_back(self):
        pb = [""]
        _, _, writes = self._call(pb, "typed value")
        assert len(writes) == 1
        assert writes[0].startswith("simdrive-readback-")


@pytest.mark.real_readback
class TestReaderSelection:
    def _spy(self):
        calls: list = []
        return calls, [
            patch.object(readback, "read_ax", return_value=None),
            patch.object(readback.hid_inject, "chord", side_effect=lambda *a: calls.append(("chord", a))),
            patch.object(readback.hid_inject, "press_key", side_effect=lambda *a: calls.append(("key", a))),
            patch.object(readback.sim, "set_pasteboard", side_effect=lambda *a: calls.append(("pbcopy", a))),
            patch.object(readback.sim, "get_pasteboard", side_effect=lambda *a: calls.append(("pbpaste", a)) or ""),
            patch.object(readback.time, "sleep", lambda s: None),
        ]

    def test_auto_without_ax_sends_no_input_and_touches_no_pasteboard(self):
        calls, patches = self._spy()
        for p in patches:
            p.start()
        try:
            r = readback.read_focused_field(_FAKE_UDID, "Test Sim", "auto")
        finally:
            for p in patches:
                p.stop()
        assert r.value is None
        assert r.reason == readback.NO_SIDE_EFFECT_FREE_READER
        assert calls == []

    def test_pasteboard_mode_without_ax_copies(self):
        calls, patches = self._spy()
        for p in patches:
            p.start()
        try:
            readback.read_focused_field(_FAKE_UDID, "Test Sim", "pasteboard")
        finally:
            for p in patches:
                p.stop()
        assert ("chord", (_FAKE_UDID, "cmd", "c")) in calls

    def test_ax_is_preferred_in_pasteboard_mode(self):
        with patch.object(readback, "read_ax", return_value=_ax("x")), \
             patch.object(readback, "read_pasteboard") as pb:
            r = readback.read_focused_field(_FAKE_UDID, "Test Sim", "pasteboard")
        assert r.method == "ax"
        pb.assert_not_called()


@pytest.mark.real_readback
class TestAXWindowMatch:
    """The AX reader must read the target sim's window, not a sim whose name
    merely starts with the target's (a headless "iPhone 16" next to a visible
    "iPhone 16 Pro")."""

    def _read(self, titles, device_name):
        windows = [{"AXTitle": t, "field": {"AXValue": f"value of {t}", "AXRole": "AXTextField"}}
                   for t in titles]
        app = {"AXWindows": windows}

        def attr(elem, name):
            return elem.get(name)

        with patch.object(readback.ax, "is_available", return_value=True), \
             patch.object(readback.ax, "_app_element", return_value=app), \
             patch.object(readback.ax, "_attr", side_effect=attr), \
             patch.object(readback, "_find_focused_editable", side_effect=lambda w: w["field"]):
            return readback.read_focused_field(_FAKE_UDID, device_name, "auto")

    def test_longer_name_with_same_prefix_is_not_matched(self):
        r = self._read(["iPhone 16 Pro – iOS 26.1"], "iPhone 16")
        assert r.value is None
        assert r.reason == readback.NO_SIDE_EFFECT_FREE_READER

    def test_exact_name_before_title_separator_is_matched(self):
        r = self._read(["iPhone 16 Pro – iOS 26.1", "iPhone 16 – iOS 26.1"], "iPhone 16")
        assert r.method == "ax"
        assert r.value == "value of iPhone 16 – iOS 26.1"

    def test_bare_title_equal_to_name_is_matched(self):
        r = self._read(["iPhone 16"], "iPhone 16")
        assert r.value == "value of iPhone 16"


def test_off_is_declared_in_the_verify_schema_and_accepted(tmp_path):
    tool = next(t for t in server._TOOLS if t["name"] == "type_text")
    string_enum = next(o["enum"] for o in tool["inputSchema"]["properties"]["verify"]["oneOf"] if "enum" in o)
    assert "off" in string_enum
    r = _run(tmp_path, {"text": "hello", "verify": "off"}, [])
    assert r.consumed == []
    assert r.resp["verified"] is None
