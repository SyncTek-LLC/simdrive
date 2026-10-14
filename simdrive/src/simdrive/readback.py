"""Read back what a text field holds after type_text (simulator only).

Two readers, tried in order:

1. Host AX: the focused editable element's ``AXValue``. Needs Simulator.app
   running with the device window on-screen and Accessibility permission, so
   it is unavailable for headless (``simctl boot``) sims. Used only when
   exactly one Simulator window matches the device name, so a fleet of
   same-named sims cannot answer for each other. Distinguishes secure fields.
2. Pasteboard: write a sentinel to the device pasteboard, Cmd-A, Cmd-C,
   ``simctl pbpaste``, then Right-arrow to collapse the selection (the caret
   lands at the end, where it was after typing) and restore the previous
   pasteboard text. Keyed by UDID, works headless. Limits: iOS blocks copy
   from secure fields, and an empty field, a secure field and a field that
   lost focus all leave the sentinel in place; non-text pasteboard contents
   cannot be restored (``simctl pbpaste`` returns text only).
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

_HID_ARROW_RIGHT = 79
# Settle between a HID chord and the pasteboard read; 0.3 s was enough in
# live runs on iOS 26.1 for the copy to reach `simctl pbpaste`.
_CHORD_SETTLE_SEC = 0.25

_SMART_PUNCTUATION = str.maketrans({
    "‘": "'", "’": "'", "“": '"', "”": '"',
})


@dataclass
class FieldRead:
    """One read of the focused field.

    ``value`` is None when the field could not be read (``reason`` says why).
    ``copied`` is False when the pasteboard reader copied nothing, which reads
    as an empty field but may also be a secure field or lost focus.
    """

    value: str | None
    method: str | None
    secure: bool = False
    copied: bool = True
    reason: str | None = None


def compare(expected: str, landed: str, *, secure: bool = False) -> str | None:
    """Return how *landed* matches *expected*, or None on mismatch.

    Secure fields expose only bullets, so only the length is comparable.
    iOS may capitalize the field's first letter and substitute curly quotes;
    both are accepted.
    """
    if secure:
        return "secure_length" if len(landed) == len(expected) else None
    if landed == expected:
        return "exact"
    if expected[:1].islower() and landed == expected[:1].upper() + expected[1:]:
        return "auto_capitalized"
    if landed.translate(_SMART_PUNCTUATION) == expected.translate(_SMART_PUNCTUATION):
        return "smart_punctuation"
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
        role = str(ax._attr(field, "AXRole") or "")
        return FieldRead(
            value="" if value is None else str(value),
            method="ax",
            secure=role == "AXSecureTextField",
        )
    except Exception as exc:  # noqa: BLE001 — AX is best-effort; fall back
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
    """Copy the focused field's text through the device pasteboard."""
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
        try:
            hid_inject.press_key(udid, _HID_ARROW_RIGHT)
        except (OSError, subprocess.SubprocessError, RuntimeError) as exc:
            _log.debug("readback.collapse_selection_failed: %s", exc)
        if saved is not None:
            try:
                sim.set_pasteboard(udid, saved)
            except (OSError, subprocess.SubprocessError, RuntimeError) as exc:
                _log.debug("readback.pasteboard_restore_failed: %s", exc)
    if copied == sentinel:
        return FieldRead(value="", method="pasteboard", copied=False)
    return FieldRead(value=copied, method="pasteboard")


def read_focused_field(udid: str, device_name: str) -> FieldRead:
    """Read the focused field: host AX when it can see the device, else the pasteboard."""
    via_ax = read_ax(device_name)
    if via_ax is not None:
        return via_ax
    if not udid:
        return FieldRead(value=None, method=None, reason="no device UDID for pasteboard read-back")
    return read_pasteboard(udid)
