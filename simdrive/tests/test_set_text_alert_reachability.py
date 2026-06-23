"""Regression tests for ``ax.set_text`` reachability of editable fields.

Covers the iOS-26 ``UIAlertController`` host-AX wall found in live validation
(iPhone 16 Pro / iOS 26.0, Palace "Go to Page" prompt): on iOS 26 the Simulator
collapses the on-device UI into one opaque ``iOSContentGroup`` and does NOT vend
a presented alert's ``UITextField`` to the host-AX tree via any access path. The
tests assert:

  * the broadened search (window → focused element → sibling windows) finds a
    field wherever host AX vends one;
  * when host AX vends NO editable field anywhere (the wall), ``set_text``
    returns a clear, actionable error pointing at the on-device backend instead
    of the old terse "no editable text field found in the target window";
  * explicit identifier/label misses keep a field-scoped error (not the wall
    message), so the two failure modes stay distinguishable.

Pure unit tests — the macOS AX layer is faked via monkeypatch, so they run
without a booted simulator. Live validation of the host-AX wall is in the PR
description (deferred: the MCP runs the installed build, not this branch).
"""
from __future__ import annotations

from simdrive import ax


class _El:
    """Minimal fake AXUIElement: a role plus children."""

    def __init__(self, role: str = "", children=None):
        self.role = role
        self.children = children or []


def _install_fake_tree(monkeypatch, *, windows, focused=None):
    """Patch ax's AX helpers so the search operates over fake ``_El`` trees.

    ``windows`` is the list returned for the app element's ``AXWindows``;
    ``focused`` is returned for ``AXFocusedUIElement``. ``select_window`` is
    stubbed to return the first window so ``set_text`` doesn't touch real AX.
    """
    app = _El(role="AXApplication", children=list(windows))

    def fake_attr(elem, name):
        if elem is app and name == "AXWindows":
            return list(windows)
        if elem is app and name == "AXFocusedUIElement":
            return focused
        if name == "AXRole":
            return elem.role
        return None

    monkeypatch.setattr(ax, "_attr", fake_attr)
    monkeypatch.setattr(ax, "_children", lambda e: e.children)
    monkeypatch.setattr(ax, "_app_element", lambda: app)
    return app


# ── _find_editable_field: the broadened search ──────────────────────────────


def test_find_editable_field_in_window_subtree(monkeypatch):
    field = _El(role="AXTextField")
    window = _El(children=[_El(role="AXButton"), _El(children=[field])])
    _install_fake_tree(monkeypatch, windows=[window])
    assert ax._find_editable_field(window) is field


def test_find_editable_field_via_focused_element(monkeypatch):
    """No field in the window subtree, but the app's focused element is one."""
    window = _El(children=[_El(role="AXButton")])
    focused = _El(role="AXSecureTextField")
    _install_fake_tree(monkeypatch, windows=[window], focused=focused)
    assert ax._find_editable_field(window) is focused


def test_find_editable_field_via_sibling_window(monkeypatch):
    """Field is vended on a different Simulator window, not the device window."""
    window = _El(children=[_El(role="AXGroup")])
    sibling_field = _El(role="AXTextField")
    sibling = _El(children=[sibling_field])
    _install_fake_tree(monkeypatch, windows=[window, sibling])
    assert ax._find_editable_field(window) is sibling_field


def test_find_editable_field_returns_none_on_host_ax_wall(monkeypatch):
    """iOS-26 wall: opaque content group, no editable field anywhere."""
    window = _El(children=[_El(role="AXGroup", children=[_El(role="AXGroup")])])
    _install_fake_tree(monkeypatch, windows=[window], focused=window)
    assert ax._find_editable_field(window) is None


# ── set_text: error surface ─────────────────────────────────────────────────


def _patch_set_value_ok(monkeypatch):
    """Make AXUIElementSetAttributeValue (imported lazily inside set_text) a no-op."""
    import ApplicationServices  # type: ignore[import]

    monkeypatch.setattr(
        ApplicationServices, "AXUIElementSetAttributeValue",
        lambda elem, attr, value: 0, raising=False,
    )


def test_set_text_returns_wall_error_when_no_field(monkeypatch):
    """The iOS-26 alert case: actionable error, not the old terse message."""
    window = _El(children=[_El(role="AXGroup")])
    _install_fake_tree(monkeypatch, windows=[window], focused=window)
    monkeypatch.setattr(ax, "select_window", lambda name, auto_raise=True: window)

    result = ax.set_text("iPhone 16 Pro", "5")
    assert result["ok"] is False
    err = result["error"]
    # Names the iOS-26 cause and the on-device remedy — the regression fix.
    assert "iOS 26" in err
    assert "target='device'" in err
    assert "UIAlertController" in err
    # Must NOT regress to the old terse string.
    assert err != "no editable text field found in the target window"


def test_set_text_succeeds_when_field_reachable(monkeypatch):
    field = _El(role="AXTextField")
    window = _El(children=[field])
    _install_fake_tree(monkeypatch, windows=[window])
    monkeypatch.setattr(ax, "select_window", lambda name, auto_raise=True: window)
    _patch_set_value_ok(monkeypatch)

    result = ax.set_text("iPhone 16 Pro", "5")
    assert result == {"ok": True, "value": "5"}


def test_set_text_identifier_miss_keeps_scoped_error(monkeypatch):
    """An explicit identifier miss is distinct from the host-AX wall."""
    window = _El(children=[_El(role="AXGroup")])
    _install_fake_tree(monkeypatch, windows=[window])
    monkeypatch.setattr(ax, "select_window", lambda name, auto_raise=True: window)
    monkeypatch.setattr(ax, "_find_by", lambda root, attr, value: None)

    result = ax.set_text("iPhone 16 Pro", "5", identifier="page-field")
    assert result["ok"] is False
    assert "page-field" in result["error"]
    # The wall message is reserved for the no-target default path.
    assert "iOS 26" not in result["error"]
