"""Host-side macOS Accessibility (AXUIElement) bridge for the iOS Simulator.

Lets simdrive drive accessibility primitives that HID + OCR can't touch:

  - **Custom actions** — fire a ``UIAccessibilityCustomAction`` by name (e.g.
    Reader2's "Where am I?", "Next page"), normally only reachable via the
    on-device VoiceOver rotor. iOS surfaces them to the macOS host AX layer as
    encoded action names (``"Name:<label>\\nTarget:..\\nSelector:.."``) on the
    owning element; we enumerate and perform via the macOS AX API.
  - **Announcements** — capture ``UIAccessibility.post(.announcement)`` strings
    (invisible to OCR) via a macOS ``AXObserver`` for
    ``kAXAnnouncementRequestedNotification``.

Host-only: requires macOS Accessibility permission for the running process and
the target simulator's window to be on-screen (host AX vends only on-screen
Simulator windows — a headless ``simctl boot`` device is invisible).

The mechanics here were proven against a booted sim + a fixture and against the
real Palace app (org.thepalaceproject.palace) before being ported into simdrive.
"""
from __future__ import annotations

import logging
import subprocess
import threading
import time
from collections import deque
from typing import Any

logger = logging.getLogger("simdrive.ax")


class AXError(RuntimeError):
    """Raised when a host-AX operation cannot be completed."""


# ---------------------------------------------------------------------------
# Low-level AX helpers (pyobjc imported lazily — optional dependency)
# ---------------------------------------------------------------------------


def is_available() -> bool:
    """True when host AX can be used: pyobjc present, permission granted, sim up."""
    try:
        from ApplicationServices import AXIsProcessTrusted  # type: ignore[import]

        if not AXIsProcessTrusted():
            return False
        return _sim_pid() is not None
    except Exception:  # noqa: BLE001
        return False


def _require_trusted() -> None:
    from ApplicationServices import AXIsProcessTrusted  # type: ignore[import]

    if not AXIsProcessTrusted():
        raise AXError(
            "macOS Accessibility permission is required. Grant it under System "
            "Settings → Privacy & Security → Accessibility for this process."
        )


def _sim_pid() -> int | None:
    """PID of the running Simulator.app process (hosts every device window)."""
    try:
        out = subprocess.run(
            ["pgrep", "-f", "Simulator.app/Contents/MacOS/Simulator"],
            capture_output=True,
            text=True,
            timeout=5,
        ).stdout
    except Exception:  # noqa: BLE001
        return None
    pids = [int(p) for p in out.split() if p.strip().isdigit()]
    return pids[0] if pids else None


def _attr(elem: Any, name: str) -> Any:
    from ApplicationServices import AXUIElementCopyAttributeValue  # type: ignore[import]

    try:
        err, value = AXUIElementCopyAttributeValue(elem, name, None)
        return value if err == 0 else None
    except Exception:  # noqa: BLE001 — element may be stale
        return None


def _children(elem: Any) -> list[Any]:
    kids = _attr(elem, "AXChildren")
    if kids is None:
        return []
    try:
        return list(kids)
    except TypeError:
        return [kids]


def _action_names(elem: Any) -> list[str]:
    from ApplicationServices import AXUIElementCopyActionNames  # type: ignore[import]

    try:
        err, names = AXUIElementCopyActionNames(elem, None)
        return list(names) if (err == 0 and names) else []
    except Exception:  # noqa: BLE001
        return []


def _custom_action_label(action_name: Any) -> str | None:
    """Decode an iOS custom-action AX name → its label.

    iOS ``UIAccessibilityCustomAction``s surface as
    ``"Name:<label>\\nTarget:0x0\\nSelector:(null)"``. Returns ``<label>``, or
    ``None`` for built-in actions (``AXPress``, ``AXShowMenu``, …).
    """
    if isinstance(action_name, str) and action_name.startswith("Name:"):
        return action_name.split("\n", 1)[0][len("Name:"):]
    return None


# ---------------------------------------------------------------------------
# App-under-test pid resolution (for announcement attribution)
# ---------------------------------------------------------------------------


def resolve_app_pid(udid: str, bundle_id: str) -> int | None:
    """Host pid of *bundle_id* on *udid* via simctl, or None if not running.

    ``xcrun simctl spawn <udid> launchctl list`` lists processes as
    ``"<pid>\\t<status>\\tUIKitApplication:<bundle>[..]"``; ``<pid>`` is the host
    pid that AX announcement userInfo reports. Never raises.
    """
    if not bundle_id:
        return None
    try:
        out = subprocess.run(
            ["xcrun", "simctl", "spawn", udid or "booted", "launchctl", "list"],
            capture_output=True,
            text=True,
            timeout=10,
        ).stdout
    except Exception as exc:  # noqa: BLE001
        logger.debug("resolve_app_pid failed: %s", exc)
        return None
    for line in out.splitlines():
        if bundle_id not in line:
            continue
        head = line.split("\t", 1)[0].strip()
        if head.lstrip("-").isdigit() and int(head) > 0:
            return int(head)
    return None


# ---------------------------------------------------------------------------
# Window selection by device (host AX vends every on-screen sim window)
# ---------------------------------------------------------------------------


def _app_element() -> Any:
    pid = _sim_pid()
    if pid is None:
        raise AXError("iOS Simulator is not running (no Simulator.app process).")
    from ApplicationServices import AXUIElementCreateApplication  # type: ignore[import]

    return AXUIElementCreateApplication(pid)


def raise_window(device_name: str) -> bool:
    """Bring the target device's Simulator window on-screen + frontmost.

    Host AX only vends *on-screen* Simulator windows, and with several sims
    booted another may hold the front. Selecting the device from the Simulator
    "Window" menu (which lists every booted device, even headless ones) raises
    it. Best-effort — returns False if the menu item isn't found.
    """
    if not device_name:
        return False
    # Pass device_name as an osascript argument (argv) rather than interpolating
    # it into the script — sim names are user-settable, so interpolation would be
    # an AppleScript-injection vector.
    script = (
        "on run argv\n"
        "  set deviceName to item 1 of argv\n"
        '  tell application "Simulator" to activate\n'
        '  tell application "System Events" to tell process "Simulator" to '
        'click (first menu item of menu "Window" of menu bar 1 '
        "whose title contains deviceName)\n"
        "end run"
    )
    try:
        subprocess.run(
            ["osascript", "-e", script, device_name],
            capture_output=True, text=True, timeout=5,
        )
        return True
    except Exception as exc:  # noqa: BLE001 — best-effort
        logger.debug("raise_window(%r) failed: %s", device_name, exc)
        return False


def select_window(device_name: str, auto_raise: bool = True) -> Any:
    """Return the AXWindow for the simulator whose title matches *device_name*.

    The Simulator process hosts one AXWindow per on-screen device; the title is
    e.g. ``"iPhone 16 Pro (pool-3) – iOS 26.0"``. Scoping element/action lookups
    to this window keeps a multi-sim fleet from cross-targeting. When
    *auto_raise* and the window isn't already visible, the target is raised via
    the Simulator Window menu first.

    Raises AXError (actionable) when no window matches even after raising —
    typically a sim that isn't actually booted.
    """
    _require_trusted()

    def _match() -> Any:
        app = _app_element()
        windows = _attr(app, "AXWindows") or []
        if device_name:
            for w in windows:
                if device_name in str(_attr(w, "AXTitle") or ""):
                    return w
        return windows[0] if len(windows) == 1 else None

    found = _match()
    if found is not None:
        return found
    if auto_raise and raise_window(device_name):
        time.sleep(1.0)
        found = _match()
        if found is not None:
            return found

    app = _app_element()
    titles = [str(_attr(w, "AXTitle") or "") for w in (_attr(app, "AXWindows") or [])]
    raise AXError(
        f"No on-screen Simulator window for device {device_name!r} "
        f"(visible windows: {titles or 'none'}). Host AX only sees on-screen "
        "windows; ensure the target sim is booted and shown in the Simulator "
        "'Window' menu."
    )


# ---------------------------------------------------------------------------
# Element + action-carrier resolution
# ---------------------------------------------------------------------------


def _find_by(root: Any, attr: str, value: str, depth: int = 0, maxdepth: int = 60) -> Any:
    if depth > maxdepth:
        return None
    if str(_attr(root, attr) or "") == value:
        return root
    for child in _children(root):
        hit = _find_by(child, attr, value, depth + 1, maxdepth)
        if hit is not None:
            return hit
    return None


def _find_action_carrier(root: Any, action_label: str, depth: int = 0, maxdepth: int = 60) -> Any:
    """DFS for the first element whose decoded custom-action labels include
    *action_label*.

    Fix for real apps: the custom action often sits on a deep child with no
    accessibilityLabel/identifier (e.g. Palace's ``navigator.view``), so probing
    only the content-group root returns "no actions" — we must recurse.
    """
    if depth > maxdepth:
        return None
    for name in _action_names(root):
        if _custom_action_label(name) == action_label:
            return root
    for child in _children(root):
        hit = _find_action_carrier(child, action_label, depth + 1, maxdepth)
        if hit is not None:
            return hit
    return None


def perform_action(
    device_name: str,
    name: str,
    *,
    identifier: str | None = None,
    label: str | None = None,
) -> dict[str, Any]:
    """Perform the custom action *name* on the target simulator's window.

    Resolution order:
      1. Scope to the device's on-screen window (multi-sim safe).
      2. If *identifier*/*label* given, descend into that element's subtree;
         otherwise search the whole window.
      3. DFS for the first element carrying a custom action whose label == *name*
         and invoke it.

    Returns ``{"ok": True, "action": name}`` or ``{"ok": False, "error": ...}``
    (the latter includes ``available_actions`` when an explicit target was found
    but lacked the action).
    """
    from ApplicationServices import AXUIElementPerformAction  # type: ignore[import]

    window = select_window(device_name)

    search_root = window
    if identifier:
        search_root = _find_by(window, "AXIdentifier", identifier) or window
    elif label:
        search_root = (
            _find_by(window, "AXDescription", label)
            or _find_by(window, "AXTitle", label)
            or window
        )

    carrier = _find_action_carrier(search_root, name)
    if carrier is None and search_root is not window:
        # Explicit target didn't carry it — widen to the whole window.
        carrier = _find_action_carrier(window, name)

    if carrier is None:
        return {
            "ok": False,
            "error": f"custom action {name!r} not found in the target window",
        }

    matched = next(
        (a for a in _action_names(carrier) if _custom_action_label(a) == name), None
    )
    if matched is None:  # pragma: no cover — carrier implies a match
        return {"ok": False, "error": f"custom action {name!r} vanished on carrier"}

    err = AXUIElementPerformAction(carrier, matched)
    if err != 0:
        return {"ok": False, "action": name, "error": f"AXUIElementPerformAction err={err}"}
    return {"ok": True, "action": name}


_EDITABLE_ROLES = {"AXTextField", "AXSecureTextField", "AXTextArea"}


def _find_text_field(root, depth=0, maxdepth=60):
    if depth > maxdepth:
        return None
    if str(_attr(root, "AXRole") or "") in _EDITABLE_ROLES:
        return root
    for child in _children(root):
        hit = _find_text_field(child, depth + 1, maxdepth)
        if hit is not None:
            return hit
    return None


def set_text(
    device_name: str,
    text: str,
    *,
    identifier: str | None = None,
    label: str | None = None,
) -> dict[str, Any]:
    """Set a text field's value directly via host AX (`AXValue`).

    The fix for fields HID `type_text` can't reach — notably `UIAlertController`
    prompts (e.g. a "Go to Page" dialog), whose field never receives synthesized
    keystrokes. Setting `AXValue` propagates to the field's binding (verified:
    the app reads the value), so the app receives the input.

    Resolution: scope to the device's window, then the field by *identifier* /
    *label*, else the first editable field (text field / secure field / text
    area) in the window — which is the alert's field when a prompt is up.

    Returns ``{"ok": True, "value": text}`` or ``{"ok": False, "error": ...}``.
    """
    from ApplicationServices import AXUIElementSetAttributeValue  # type: ignore[import]

    window = select_window(device_name)
    if identifier:
        field = _find_by(window, "AXIdentifier", identifier)
    elif label:
        field = _find_by(window, "AXDescription", label) or _find_by(window, "AXTitle", label)
    else:
        field = _find_text_field(window)

    if field is None:
        return {"ok": False, "error": "no editable text field found in the target window"}

    err = AXUIElementSetAttributeValue(field, "AXValue", text)
    if err != 0:
        return {"ok": False, "error": f"AXUIElementSetAttributeValue err={err}"}
    return {"ok": True, "value": text}


# ---------------------------------------------------------------------------
# Announcement observer (module singleton — one Simulator process)
# ---------------------------------------------------------------------------


class _AnnouncementObserver:
    """Captures kAXAnnouncementRequestedNotification on the Simulator process.

    One observer covers every device window (the notification carries the
    originating app pid in userInfo). Buffers ``{text, priority, pid, ts}``.
    """

    def __init__(self) -> None:
        self._buf: deque[dict[str, Any]] = deque(maxlen=512)
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._observer: Any = None
        self._cb: Any = None  # keep the pyobjc closure alive
        self._started = threading.Event()
        self._stop = False
        self._pid: int | None = None

    def ensure_started(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop = False
        self._started.clear()
        self._thread = threading.Thread(target=self._run, name="ax-announcements", daemon=True)
        self._thread.start()
        self._started.wait(timeout=2.0)

    def _run(self) -> None:
        try:
            import objc  # type: ignore[import]
            from ApplicationServices import (  # type: ignore[import]
                AXObserverAddNotification,
                AXObserverCreateWithInfoCallback,
                AXObserverGetRunLoopSource,
                AXUIElementCreateApplication,
                kAXAnnouncementRequestedNotification,
            )
            from CoreFoundation import (  # type: ignore[import]
                CFRunLoopAddSource,
                CFRunLoopGetCurrent,
                CFRunLoopRunInMode,
                kCFRunLoopDefaultMode,
            )
        except ImportError as exc:
            logger.warning("announcement observer unavailable: %s", exc)
            self._started.set()
            return

        pid = _sim_pid()
        if pid is None:
            self._started.set()
            return
        self._pid = pid

        @objc.callbackFor(AXObserverCreateWithInfoCallback)
        def _cb(observer, element, notification, user_info, refcon):  # noqa: ANN001
            try:
                info = dict(user_info) if user_info else {}
            except Exception:  # noqa: BLE001
                info = {}
            text = info.get("AXAnnouncementKey")
            if text is None:
                return
            rec = {
                "text": str(text),
                "priority": info.get("AXPriorityKey"),
                "pid": info.get("pid"),
                "ts": time.time(),
            }
            with self._lock:
                self._buf.append(rec)

        err, observer = AXObserverCreateWithInfoCallback(pid, _cb, None)
        if err != 0 or observer is None:
            logger.warning("AXObserverCreateWithInfoCallback failed (err=%s)", err)
            self._started.set()
            return
        self._observer = observer
        self._cb = _cb
        AXObserverAddNotification(
            observer, AXUIElementCreateApplication(pid),
            kAXAnnouncementRequestedNotification, None,
        )
        CFRunLoopAddSource(
            CFRunLoopGetCurrent(), AXObserverGetRunLoopSource(observer), kCFRunLoopDefaultMode
        )
        self._started.set()
        while not self._stop:
            CFRunLoopRunInMode(kCFRunLoopDefaultMode, 0.25, True)

    def get(
        self,
        since_ts: float | None = None,
        timeout_s: float = 0.0,
        app_pid: int | None = None,
        clear: bool = False,
    ) -> dict[str, Any]:
        """Return captured announcements.

        Filtering is a SOFT preference, not a hard gate: announcements are scoped
        by ``since_ts`` (always) and, when ``app_pid`` is known AND at least one
        buffered announcement matches it, by pid too. If none match the pid we
        return the unscoped (since_ts-only) set rather than an empty result —
        real apps post via async paths whose AX-attributed pid can differ from
        the launch pid, and a hard pid filter silently drops those.
        """
        self.ensure_started()
        deadline = time.time() + max(0.0, timeout_s)
        while True:
            with self._lock:
                window = [
                    a for a in self._buf if since_ts is None or a["ts"] > since_ts
                ]
                if clear:
                    self._buf.clear()
            scoped = [a for a in window if app_pid is not None and a.get("pid") == app_pid]
            items = scoped if scoped else window
            if items or time.time() >= deadline:
                return {
                    "announcements": items,
                    "count": len(items),
                    "app_pid": app_pid,
                    "pid_scoped": bool(scoped),
                }
            time.sleep(0.1)

    def stop(self) -> None:
        self._stop = True
        t = self._thread
        if t is not None:
            t.join(timeout=1.5)
        self._thread = None
        self._observer = None
        self._cb = None
        self._started.clear()


_OBSERVER = _AnnouncementObserver()


def start_announcement_observer() -> None:
    """Begin capturing announcements (idempotent). Call early in a session."""
    _require_trusted()
    _OBSERVER.ensure_started()


def get_announcements(
    since_ts: float | None = None,
    timeout_s: float = 0.0,
    app_pid: int | None = None,
    clear: bool = False,
) -> dict[str, Any]:
    """Return captured VoiceOver announcements (starts the observer if needed)."""
    return _OBSERVER.get(since_ts=since_ts, timeout_s=timeout_s, app_pid=app_pid, clear=clear)


def stop_announcement_observer() -> None:
    _OBSERVER.stop()
