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


# ── iOS-26 content-group probe (regression: dropped in specterqa→simdrive) ────
#
# On iOS 26 the Simulator collapses the app UI into one opaque AXGroup
# (subrole "iOSContentGroup") with ZERO AXChildren, so the plain window walk
# in set_text/perform_action sees nothing inside the app. The ported
# heuristic + AXUIElementCopyElementAtPosition position-probe expand it.
# These tests catch the regression: WITHOUT the resolver the field/action is
# not found; WITH it, it is.


class _Node:
    """Minimal fake AX element for the content-group tests."""

    def __init__(self, role="", subrole="", frame=None, children=None,
                 actions=None, ident="", desc="", title=""):
        self.role = role
        self.subrole = subrole
        self.frame = frame
        self.children = children or []
        self.actions = actions or []
        self.ident = ident
        self.desc = desc
        self.title = title


def _wire_fakes(monkeypatch, *, hit=None):
    """Route ax's low-level accessors at the _Node fakes."""
    def _attr(e, a):
        return {
            "AXRole": e.role,
            "AXSubrole": e.subrole,
            "AXIdentifier": e.ident,
            "AXDescription": e.desc,
            "AXTitle": e.title,
        }.get(a)

    monkeypatch.setattr(ax, "_attr", _attr)
    monkeypatch.setattr(ax, "_children", lambda e: e.children)
    monkeypatch.setattr(ax, "_frame", lambda e: e.frame)
    monkeypatch.setattr(ax, "_action_names", lambda e: e.actions)


# ── aspect-ratio heuristic ────────────────────────────────────────────────────


def test_find_ios_content_group_picks_portrait_largest(monkeypatch):
    # A wide chrome bar (landscape) + a portrait content group; the portrait,
    # larger-area child wins.
    chrome = _Node(role="AXToolbar", frame={"x": 0, "y": 0, "width": 400, "height": 40})
    content = _Node(role="AXGroup", subrole="iOSContentGroup",
                    frame={"x": 0, "y": 0, "width": 390, "height": 844},
                    children=[_Node(role="AXTextField")])
    window = _Node(role="AXWindow", children=[chrome, content])
    _wire_fakes(monkeypatch)
    assert ax._find_ios_content_group(window) is content


def test_find_ios_content_group_none_when_no_portrait_child(monkeypatch):
    window = _Node(role="AXWindow", children=[
        _Node(role="AXButton", frame={"x": 0, "y": 0, "width": 400, "height": 40}),
    ])
    _wire_fakes(monkeypatch)
    assert ax._find_ios_content_group(window) is None


# ── position-probe fallback (AXUIElementCopyElementAtPosition) ────────────────


def test_position_probe_hits_window_centre(monkeypatch):
    # The probe hit-tests the window centre and walks up to a group container —
    # this is the path that resolves real content when AXChildren is empty.
    real = _Node(role="AXStaticText", desc="hello")
    container = _Node(role="AXGroup", children=[real, _Node(role="AXTextField")])
    real.parent_container = container

    window = _Node(role="AXWindow", frame={"x": 100, "y": 100, "width": 400, "height": 800})
    _wire_fakes(monkeypatch)
    monkeypatch.setattr(ax, "_app_element", lambda: object())

    captured = {}

    def _copy_at(app, x, y, _none):
        captured["xy"] = (x, y)
        return 0, real

    # _attr must also vend AXParent for the up-walk.
    base_attr = ax._attr

    def _attr_with_parent(e, a):
        if a == "AXParent":
            return getattr(e, "parent_container", None)
        return base_attr(e, a)

    monkeypatch.setattr(ax, "_attr", _attr_with_parent)
    monkeypatch.setitem(
        __import__("sys").modules,
        "ApplicationServices",
        types.SimpleNamespace(AXUIElementCopyElementAtPosition=_copy_at),
    )
    grp = ax._position_probe_content_group(window)
    assert grp is container  # walked up from the hit element to the group
    assert captured["xy"] == (300.0, 500.0)  # exact window centre


def test_position_probe_none_without_window_frame(monkeypatch):
    window = _Node(role="AXWindow", frame=None)
    _wire_fakes(monkeypatch)
    monkeypatch.setattr(ax, "_app_element", lambda: object())
    monkeypatch.setitem(
        __import__("sys").modules,
        "ApplicationServices",
        types.SimpleNamespace(AXUIElementCopyElementAtPosition=lambda *a: (0, None)),
    )
    assert ax._position_probe_content_group(window) is None


# ── resolver: childless heuristic group MUST fall through to the probe ─────────


def test_resolve_content_group_falls_through_when_heuristic_childless(monkeypatch):
    # The heuristic matches the opaque, CHILDLESS iOSContentGroup (the iOS-26
    # failure shape). The resolver must NOT return it — it must fall through to
    # the position-probe, which surfaces the real (walkable) container.
    childless = _Node(role="AXGroup", subrole="iOSContentGroup",
                      frame={"x": 0, "y": 0, "width": 390, "height": 844},
                      children=[])
    window = _Node(role="AXWindow", frame={"x": 0, "y": 0, "width": 390, "height": 844},
                   children=[childless])
    probed = _Node(role="AXGroup", children=[_Node(role="AXTextField")])
    _wire_fakes(monkeypatch)
    monkeypatch.setattr(ax, "_position_probe_content_group", lambda w: probed)
    assert ax._resolve_content_group(window) is probed


def test_resolve_content_group_trusts_walkable_heuristic(monkeypatch):
    walkable = _Node(role="AXGroup", subrole="iOSContentGroup",
                     frame={"x": 0, "y": 0, "width": 390, "height": 844},
                     children=[_Node(role="AXTextField")])
    window = _Node(role="AXWindow", children=[walkable])
    _wire_fakes(monkeypatch)
    # Probe must NOT be consulted when the heuristic group is already walkable.
    monkeypatch.setattr(ax, "_position_probe_content_group",
                        lambda w: pytest.fail("probe should not run"))
    assert ax._resolve_content_group(window) is walkable


# ── wired-in regression: set_text finds the field only via the content group ──


def test_set_text_finds_field_via_content_group_probe(monkeypatch):
    """iOS 26: field is invisible to the window walk, reached via the probe.

    BEFORE (no probe): _find_text_field(window) is None -> 'not found'.
    AFTER (probe): the resolved content group exposes the AXTextField -> set.
    """
    field = _Node(role="AXTextField")
    window = _Node(role="AXWindow", children=[
        _Node(role="AXGroup", subrole="iOSContentGroup", children=[]),  # opaque
    ])
    content_group = _Node(role="AXGroup", children=[field])

    _wire_fakes(monkeypatch)
    monkeypatch.setattr(ax, "select_window", lambda dev: window)
    monkeypatch.setattr(ax, "_resolve_content_group", lambda w: content_group)

    set_calls = {}

    def _set(el, attr, val):
        set_calls["args"] = (el, attr, val)
        return 0

    monkeypatch.setitem(
        __import__("sys").modules,
        "ApplicationServices",
        types.SimpleNamespace(AXUIElementSetAttributeValue=_set),
    )

    # Sanity: the plain window walk alone does NOT find the field (regression).
    assert ax._find_text_field(window) is None

    res = ax.set_text("iPhone 16 Pro", "5")
    assert res == {"ok": True, "value": "5"}
    assert set_calls["args"] == (field, "AXValue", "5")


def test_set_text_still_not_found_without_content_group(monkeypatch):
    """Regression guard: with NO resolvable content group, still 'not found'."""
    window = _Node(role="AXWindow", children=[
        _Node(role="AXGroup", subrole="iOSContentGroup", children=[]),
    ])
    _wire_fakes(monkeypatch)
    monkeypatch.setattr(ax, "select_window", lambda dev: window)
    monkeypatch.setattr(ax, "_resolve_content_group", lambda w: None)
    monkeypatch.setitem(
        __import__("sys").modules,
        "ApplicationServices",
        types.SimpleNamespace(AXUIElementSetAttributeValue=lambda *a: 0),
    )
    res = ax.set_text("iPhone 16 Pro", "5")
    assert res["ok"] is False
    assert "no editable text field" in res["error"]


# ── wired-in regression: perform_action reaches a carrier via the probe ───────


def test_perform_action_finds_carrier_via_content_group_probe(monkeypatch):
    """iOS 26: the carrier lives behind the opaque content group.

    BEFORE: the window walk finds no carrier -> 'not found'.
    AFTER: the resolved content group exposes the carrier -> performed.
    """
    carrier = _Node(actions=["Name:Read summary\nTarget:0x0\nSelector:(null)"])
    window = _Node(role="AXWindow", children=[
        _Node(role="AXGroup", subrole="iOSContentGroup", children=[]),
    ])
    content_group = _Node(role="AXGroup", children=[carrier])

    _wire_fakes(monkeypatch)
    monkeypatch.setattr(ax, "select_window", lambda dev: window)
    monkeypatch.setattr(ax, "_resolve_content_group", lambda w: content_group)

    performed = {}

    def _perform(el, name):
        performed["args"] = (el, name)
        return 0

    monkeypatch.setitem(
        __import__("sys").modules,
        "ApplicationServices",
        types.SimpleNamespace(AXUIElementPerformAction=_perform),
    )

    # Sanity: the plain window walk alone finds no carrier (regression).
    assert ax._find_action_carrier(window, "Read summary") is None

    res = ax.perform_action("iPhone 16 Pro", "Read summary")
    assert res == {"ok": True, "action": "Read summary"}
    assert performed["args"][0] is carrier


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
