"""Unit tests for ``simdrive.ax`` — host-AX custom actions + announcements.

Pure unit tests: the macOS AX layer and ``simctl`` are mocked, so these run
without a booted simulator. End-to-end validation against a real sim + the
Example Reader app is covered by manual/live runs (see the module docstring).
"""
from __future__ import annotations

import types
from unittest.mock import patch

import pytest

from simdrive import ax


# ── custom-action name decoding ─────────────────────────────────────────────


def test_custom_action_label_decodes_encoded_name():
    assert (
        ax._custom_action_label("Name:Read summary\nTarget:0x0\nSelector:(null)")
        == "Read summary"
    )


def test_custom_action_label_ignores_builtins_and_none():
    assert ax._custom_action_label("AXPress") is None
    assert ax._custom_action_label("AXShowMenu") is None
    assert ax._custom_action_label(None) is None


# ── DFS action-carrier search (fix: action sits on a deep, unlabelled child) ──


class _FakeEl:
    def __init__(self, actions=None, children=None):
        self.actions = actions or []
        self.children = children or []


def test_find_text_field_recurses(monkeypatch):
    class FE:
        def __init__(self, role="", children=None):
            self.role = role
            self.children = children or []

    monkeypatch.setattr(ax, "_attr", lambda e, a: e.role if a == "AXRole" else None)
    monkeypatch.setattr(ax, "_children", lambda e: e.children)

    field = FE(role="AXTextField")
    root = FE(children=[FE(role="AXButton"), FE(children=[field])])
    assert ax._find_text_field(root) is field
    assert ax._find_text_field(FE(children=[FE(role="AXButton")])) is None


def test_find_action_carrier_recurses_to_deep_child(monkeypatch):
    monkeypatch.setattr(ax, "_action_names", lambda e: e.actions)
    monkeypatch.setattr(ax, "_children", lambda e: e.children)

    deep = _FakeEl(actions=["AXPress", "Name:Read summary\nTarget:0x0\nSelector:(null)"])
    root = _FakeEl(children=[_FakeEl(actions=["AXPress"]), _FakeEl(children=[deep])])

    assert ax._find_action_carrier(root, "Read summary") is deep
    assert ax._find_action_carrier(root, "Toggle toolbar") is None


# ── b11 FIX 2: WKWebView host-AX boundary hint when zero custom actions ───────


def test_window_has_any_custom_action_true_when_present(monkeypatch):
    monkeypatch.setattr(ax, "_action_names", lambda e: e.actions)
    monkeypatch.setattr(ax, "_children", lambda e: e.children)
    deep = _FakeEl(actions=["Name:Next page\nTarget:0x0\nSelector:(null)"])
    root = _FakeEl(children=[_FakeEl(actions=["AXPress"]), _FakeEl(children=[deep])])
    assert ax._window_has_any_custom_action(root) is True


def test_window_has_any_custom_action_false_when_only_builtins(monkeypatch):
    monkeypatch.setattr(ax, "_action_names", lambda e: e.actions)
    monkeypatch.setattr(ax, "_children", lambda e: e.children)
    # Only built-in AX actions (AXPress/AXShowMenu) — no custom actions at all,
    # the WKWebView/Readium signature where web-AX isn't bridged to host-AX.
    root = _FakeEl(
        actions=["AXPress"],
        children=[_FakeEl(actions=["AXShowMenu"]), _FakeEl(children=[_FakeEl()])],
    )
    assert ax._window_has_any_custom_action(root) is False


def test_perform_action_adds_wkwebview_hint_when_zero_actions(monkeypatch):
    """Zero custom actions window-wide => error carries the WKWebView hint."""
    window = _FakeEl(actions=["AXPress"], children=[_FakeEl(actions=["AXShowMenu"])])
    monkeypatch.setattr(ax, "select_window", lambda dev: window)
    monkeypatch.setattr(ax, "_action_names", lambda e: e.actions)
    monkeypatch.setattr(ax, "_children", lambda e: e.children)
    # AXUIElementPerformAction is imported lazily; provide a stub module so the
    # lazy `from ApplicationServices import ...` succeeds without pyobjc.
    monkeypatch.setitem(
        __import__("sys").modules,
        "ApplicationServices",
        types.SimpleNamespace(AXUIElementPerformAction=lambda *a: 0),
    )

    result = ax.perform_action("iPhone 15", "Next page")
    assert result["ok"] is False
    assert "not found" in result["error"]
    assert "WKWebView" in result["error"]
    assert "XCTest" in result["error"]


def test_perform_action_no_hint_when_other_actions_exist(monkeypatch):
    """A missing named action but other custom actions present => NO hint.

    Accuracy guard: the WKWebView hint must only fire when the window has zero
    custom actions, not when the requested label simply isn't among the (real)
    custom actions that DO exist.
    """
    other = _FakeEl(actions=["Name:Read summary\nTarget:0x0\nSelector:(null)"])
    window = _FakeEl(children=[other])
    monkeypatch.setattr(ax, "select_window", lambda dev: window)
    monkeypatch.setattr(ax, "_action_names", lambda e: e.actions)
    monkeypatch.setattr(ax, "_children", lambda e: e.children)
    monkeypatch.setitem(
        __import__("sys").modules,
        "ApplicationServices",
        types.SimpleNamespace(AXUIElementPerformAction=lambda *a: 0),
    )

    result = ax.perform_action("iPhone 15", "Next page")
    assert result["ok"] is False
    assert "not found" in result["error"]
    assert "WKWebView" not in result["error"]


def test_set_text_docstring_documents_swiftui_boundary():
    """b11 FIX 3: set_text docstring warns about SwiftUI @State binding."""
    doc = ax.set_text.__doc__ or ""
    assert "SwiftUI" in doc
    assert "@State" in doc
    assert "type_text" in doc


# ── announcement buffer: soft pid scoping (never drops to a false empty) ──────


def _observer_with(buf):
    obs = ax._AnnouncementObserver()
    for rec in buf:
        obs._buf.append(rec)
    obs.ensure_started = lambda: None  # don't spawn a real observer thread
    return obs


def test_announcements_scope_to_pid_when_a_match_exists():
    obs = _observer_with([
        {"text": "ours", "ts": 2.0, "pid": 111},
        {"text": "other sim", "ts": 3.0, "pid": 222},
    ])
    res = obs.get(since_ts=1.0, app_pid=111)
    assert res["pid_scoped"] is True
    assert [a["text"] for a in res["announcements"]] == ["ours"]


def test_announcements_fall_back_to_unscoped_when_pid_matches_none():
    # Real apps post via async paths whose AX-attributed pid can differ from the
    # launch pid; a hard filter would wrongly return empty. Soft fallback returns
    # the since_ts-scoped set instead.
    obs = _observer_with([
        {"text": "async post", "ts": 2.0, "pid": 999},
    ])
    res = obs.get(since_ts=1.0, app_pid=111)
    assert res["pid_scoped"] is False
    assert res["count"] == 1
    assert res["announcements"][0]["text"] == "async post"


def test_announcements_filter_by_since_ts():
    obs = _observer_with([
        {"text": "old", "ts": 1.0, "pid": 1},
        {"text": "new", "ts": 5.0, "pid": 1},
    ])
    res = obs.get(since_ts=3.0)
    assert [a["text"] for a in res["announcements"]] == ["new"]


# ── app-pid resolution from simctl ───────────────────────────────────────────


def test_resolve_app_pid_parses_launchctl_list():
    out = (
        "PID\tStatus\tLabel\n"
        "42\t0\tUIKitApplication:com.other.app[aaaa][rb-legacy]\n"
        "90778\t0\tUIKitApplication:com.example.reader[aeee][rb-legacy]\n"
        "-\t0\tcom.apple.notrunning\n"
    )
    with patch.object(ax.subprocess, "run", return_value=types.SimpleNamespace(stdout=out)):
        assert ax.resolve_app_pid("UDID", "com.example.reader") == 90778
        assert ax.resolve_app_pid("UDID", "not.installed") is None
    assert ax.resolve_app_pid("UDID", "") is None  # no bundle → no lookup


# ── MCP tool registration ────────────────────────────────────────────────────


def test_ax_tools_registered():
    from simdrive import server

    names = {t["name"] for t in server._TOOLS}
    assert {"perform_accessibility_action", "get_announcements", "set_text"} <= names
    # handlers are callable and listed without the handler key in list_tools()
    listed = {t["name"] for t in server.list_tools()}
    assert {"perform_accessibility_action", "get_announcements", "set_text"} <= listed
