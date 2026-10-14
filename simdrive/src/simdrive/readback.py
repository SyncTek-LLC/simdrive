"""Read back what a text field holds after type_text (simulator only).

Two readers:

* Host AX (``verify="auto"``, the default): the focused editable element's
  ``AXValue``. Sends no input, so it cannot change the field. Needs
  Simulator.app running with the device window on-screen and Accessibility
  permission, so headless (``simctl boot``) sims have no AX reader. Used only
  when exactly one Simulator window matches the device name, so same-named
  sims in a pool cannot answer for each other. Distinguishes secure fields.
* Pasteboard (``verify="pasteboard"``, opt-in): write a sentinel to the
  device pasteboard, Cmd-A, Cmd-C, ``simctl pbpaste``. Works headless, but has
  side effects: the field's text ends up selected (see
  :func:`collapse_selection`), and the pasteboard is overwritten. Previous
  pasteboard *text* is restored; non-text contents (images, URLs without a
  text form) are lost, because ``simctl pbpaste`` returns text only. iOS
  blocks copy from secure fields, so an empty field, a secure field and a
  field without focus all copy nothing (``copied=False``).
"""
from __future__ import annotations

import difflib
import logging
import subprocess
import time
import uuid
from dataclasses import dataclass

from . import ax, hid_inject, sim

_log = logging.getLogger("simdrive.readback")

MODES = ("auto", "pasteboard")

_HID_ARROW_RIGHT = 79
# Settle between a HID chord and the pasteboard read. Live runs on iOS 26.1
# used 0.3 s per step; 0.25 s was also enough there.
_CHORD_SETTLE_SEC = 0.25

_SMART_PUNCTUATION = str.maketrans({
    "‘": "'", "’": "'", "“": '"', "”": '"',
})

NO_SIDE_EFFECT_FREE_READER = (
    "no side-effect-free reader: host AX cannot see this device's field "
    "(headless sim, Simulator window not on-screen or ambiguous, or no "
    "Accessibility permission). Pass verify=\"pasteboard\" to read it by copying."
)


@dataclass
class FieldRead:
    """One read of the focused field.

    ``value`` is None when the field could not be read (``reason`` says why).
    ``copied`` is False when the pasteboard reader copied nothing: the field
    is empty, secure, or has no focus, and the reader cannot tell which.
    ``plain`` is True when the reader knows the field is a plain (non-secure)
    text field: AX reported its role, or a pasteboard copy succeeded.
    ``selected`` is True when the reader left the field's text selected
    (pasteboard reader after a successful copy).
    """

    value: str | None
    method: str | None
    secure: bool = False
    copied: bool = True
    plain: bool = False
    selected: bool = False
    reason: str | None = None


def compare(expected: str, landed: str, *, secure: bool = False,
            previous: str | None = None, text: str | None = None) -> str | None:
    """Return how *landed* matches *expected*, or None on mismatch.

    Secure fields expose only bullets, so only the length is comparable.
    iOS may capitalize the field's first letter and substitute curly quotes;
    both are accepted. When *previous* and *text* are given (append mode) the
    text may also have been inserted at the caret rather than at the end.
    """
    if secure:
        return "secure_length" if len(landed) == len(expected) else None
    if landed == expected:
        return "exact"
    if expected[:1].islower() and landed == expected[:1].upper() + expected[1:]:
        return "auto_capitalized"
    if landed.translate(_SMART_PUNCTUATION) == expected.translate(_SMART_PUNCTUATION):
        return "smart_punctuation"
    if previous and text and len(landed) == len(previous) + len(text):
        for i in range(len(previous) + 1):
            if landed[i:i + len(text)] == text and landed[:i] + landed[i + len(text):] == previous:
                return "inserted_at_caret"
    return None


def diff(expected: str, landed: str) -> list[dict]:
    """Character-level differences: what was expected vs what landed, by position."""
    out = []
    matcher = difflib.SequenceMatcher(a=expected, b=landed, autojunk=False)
    for op, i1, i2, j1, j2 in matcher.get_opcodes():
        if op == "equal":
            continue
        out.append({"op": op, "at": i1, "expected": expected[i1:i2], "landed": landed[j1:j2]})
    return out


def read_ax(device_name: str) -> FieldRead | None:
    """Read the focused editable element via host AX, or None if AX cannot."""
    if not ax.is_available():
        return None
    try:
        app = ax._app_element()
        windows = [
            w for w in (ax._attr(app, "AXWindows") or [])
            if device_name and device_name in str(ax._attr(w, "AXTitle") or "")
        ]
        if len(windows) != 1:
            return None
        window = windows[0]
        field = _find_focused_editable(window)
        if field is None:
            group = ax._resolve_content_group(window)
            if group is not None and group is not window:
                field = _find_focused_editable(group)
        if field is None:
            return None
        value = ax._attr(field, "AXValue")
        secure = str(ax._attr(field, "AXRole") or "") == "AXSecureTextField"
        return FieldRead(
            value="" if value is None else str(value),
            method="ax",
            secure=secure,
            plain=not secure,
        )
    except Exception as exc:  # noqa: BLE001 — AX is best-effort
        _log.debug("readback.ax_failed: %s", exc)
        return None


def _find_focused_editable(root, depth: int = 0, maxdepth: int = 60):
    if depth > maxdepth:
        return None
    if str(ax._attr(root, "AXRole") or "") in ax._EDITABLE_ROLES and ax._attr(root, "AXFocused"):
        return root
    for child in ax._children(root):
        hit = _find_focused_editable(child, depth + 1, maxdepth)
        if hit is not None:
            return hit
    return None


def read_pasteboard(udid: str) -> FieldRead:
    """Copy the focused field's text through the device pasteboard.

    Leaves the copied text selected; the caller decides whether to collapse
    it (:func:`collapse_selection`) or replace it (clear and retype).
    """
    try:
        saved: str | None = sim.get_pasteboard(udid)
    except (OSError, subprocess.SubprocessError, sim.SimError) as exc:
        _log.debug("readback.pbpaste_save_failed: %s", exc)
        saved = None
    sentinel = f"simdrive-readback-{uuid.uuid4().hex}"
    try:
        sim.set_pasteboard(udid, sentinel)
        hid_inject.chord(udid, "cmd", "a")
        time.sleep(_CHORD_SETTLE_SEC)
        hid_inject.chord(udid, "cmd", "c")
        time.sleep(_CHORD_SETTLE_SEC)
        copied = sim.get_pasteboard(udid)
    except (OSError, subprocess.SubprocessError, RuntimeError) as exc:
        return FieldRead(value=None, method=None, reason=f"pasteboard read-back failed: {exc}")
    finally:
        # An empty saved value is skipped: there is no text to put back, and
        # `pbcopy ""` would only replace the sentinel with another empty item.
        if saved:
            try:
                sim.set_pasteboard(udid, saved)
            except (OSError, subprocess.SubprocessError, RuntimeError) as exc:
                _log.debug("readback.pasteboard_restore_failed: %s", exc)
    if copied == sentinel:
        return FieldRead(value="", method="pasteboard", copied=False)
    return FieldRead(value=copied, method="pasteboard", plain=True, selected=True)


def collapse_selection(udid: str) -> None:
    """Collapse a Cmd-A selection to its end with Right-arrow.

    Right-arrow on a selection moves the caret to the selection's end and
    inserts or deletes nothing. type_text only calls this when the caret was
    already at the end of the text before the Cmd-A (see server
    ``_verify_typed_text``), so the caret returns to where it was.
    """
    hid_inject.press_key(udid, _HID_ARROW_RIGHT)


def read_focused_field(udid: str, device_name: str, mode: str = "auto") -> FieldRead:
    """Read the focused field with the reader *mode* allows.

    ``auto``: host AX only; no input is sent. ``pasteboard``: host AX when it
    can see the field, else the pasteboard reader.
    """
    via_ax = read_ax(device_name)
    if via_ax is not None:
        return via_ax
    if mode != "pasteboard":
        return FieldRead(value=None, method=None, reason=NO_SIDE_EFFECT_FREE_READER)
    if not udid:
        return FieldRead(value=None, method=None, reason="no device UDID for pasteboard read-back")
    return read_pasteboard(udid)
