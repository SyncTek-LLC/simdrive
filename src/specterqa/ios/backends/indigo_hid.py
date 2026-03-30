"""IndigoHID Backend — Pure Python headless touch injection via Apple private APIs.

Uses ctypes to call SimulatorKit's SimDeviceLegacyHIDClient which sends
IndigoHID messages via Mach IPC directly to the simulator process.

No window required. No Accessibility permission. No external dependencies.
Only requires Xcode to be installed (for SimulatorKit.framework).

Coordinate system: device logical points (e.g. 390x844 for iPhone 16 Pro).
To convert from screenshot pixels: divide by device scale factor (2x or 3x).

WARNING: Uses Apple private APIs. May break across Xcode versions.
Tested against Xcode 16.x / iOS 18.x.

Architecture notes
------------------
The injection path is:

    Python (ctypes) → ObjC runtime → SimDeviceLegacyHIDClient
                                           │
                                           └─ Mach IPC → SimulatorBridge
                                                              │
                                                              └─ IndigoHID kernel driver

Key classes used (all private API):
  * ``SimServiceContext``  — singleton; factory for device sets
  * ``SimDeviceSet``       — the default set of all simulators on this host
  * ``SimDevice``          — represents one simulator; found by UDID
  * ``SimDeviceLegacyHIDClient`` — sends IndigoHID messages to a SimDevice

IndigoHID message layout (reverse-engineered from idb / libimobiledevice)
--------------------------------------------------------------------------
Each HID event is a C struct of ~320 bytes containing two ``IndigoPayload``
sub-structs at offset 0 and 0x90.  Each payload describes one touch point:

    struct IndigoPayload {
        uint32_t  event_type;   // 1=down, 2=move, 3=up
        float     x_norm;       // 0.0 … 1.0 — normalised to device width
        float     y_norm;       // 0.0 … 1.0 — normalised to device height
        float     pressure;     // 1.0 for a normal finger
        uint8_t   _pad[0x80 - 12];
    };

    struct IndigoMessage {
        uint32_t         header;    // always 0x0000001A
        uint32_t         pad1;
        uint64_t         ts_ns;     // mach_absolute_time (nanoseconds)
        IndigoPayload    payload[2];
    };

The message is wrapped in an NSData and handed to:

    [SimDeviceLegacyHIDClient sendWithDevice:data error:&error]

INIT-2026-500 — SpecterQA iOS Headless Driver.
"""

from __future__ import annotations

import ctypes
import ctypes.util
import logging
import os
import struct
import subprocess
import tempfile
import time
from typing import Optional, Tuple

logger = logging.getLogger("specterqa.ios.backends.indigo_hid")

# ---------------------------------------------------------------------------
# Framework paths
# ---------------------------------------------------------------------------

_OBJC_LIB = "/usr/lib/libobjc.A.dylib"
_CORE_SIM_PATH = (
    "/Library/Developer/PrivateFrameworks/CoreSimulator.framework/CoreSimulator"
)


def _xcode_developer_path() -> str:
    """Return the active Xcode developer directory (via xcode-select -p)."""
    try:
        result = subprocess.run(
            ["xcode-select", "-p"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        return result.stdout.strip() if result.returncode == 0 else ""
    except Exception:
        return ""


def _sim_kit_path() -> str:
    """Return the absolute path to SimulatorKit.framework/SimulatorKit."""
    dev = _xcode_developer_path()
    if not dev:
        return ""
    return os.path.join(
        dev,
        "Library",
        "PrivateFrameworks",
        "SimulatorKit.framework",
        "SimulatorKit",
    )


# ---------------------------------------------------------------------------
# Lazy-loaded framework handles (module-level singletons)
# ---------------------------------------------------------------------------

_objc: Optional[ctypes.CDLL] = None
_core_sim: Optional[ctypes.CDLL] = None
_sim_kit: Optional[ctypes.CDLL] = None

_frameworks_loaded: Optional[bool] = None  # None = not tried yet


def _load_frameworks() -> bool:
    """Attempt to load all required private frameworks.

    Returns True on success.  On failure logs a warning and returns False.
    Safe to call multiple times — cached after the first attempt.
    """
    global _objc, _core_sim, _sim_kit, _frameworks_loaded
    if _frameworks_loaded is not None:
        return _frameworks_loaded

    try:
        _objc = ctypes.cdll.LoadLibrary(_OBJC_LIB)
        _core_sim = ctypes.cdll.LoadLibrary(_CORE_SIM_PATH)

        sk_path = _sim_kit_path()
        if not sk_path or not os.path.exists(sk_path):
            logger.warning(
                "SimulatorKit not found at %r — is Xcode installed? "
                "(xcode-select -p returned: %r)",
                sk_path,
                _xcode_developer_path(),
            )
            _frameworks_loaded = False
            return False

        _sim_kit = ctypes.cdll.LoadLibrary(sk_path)
        _frameworks_loaded = True
        logger.debug("Private frameworks loaded: objc, CoreSimulator, SimulatorKit")
        return True

    except OSError as exc:
        logger.warning("Failed to load private frameworks: %s", exc)
        _frameworks_loaded = False
        return False


# ---------------------------------------------------------------------------
# ObjC runtime bridge
# ---------------------------------------------------------------------------

class _ObjCBridge:
    """Minimal wrapper around the ObjC runtime.

    Exposes only what IndigoHIDBackend needs:
      * class lookup  (``cls``)
      * selector registration  (``sel``)
      * message send  (``msg``)
      * NSData creation from raw bytes (``nsdata``)
      * NSString creation from Python str (``nsstr``)
    """

    def __init__(self, libobjc: ctypes.CDLL) -> None:
        self._lib = libobjc
        self._setup_signatures()

    def _setup_signatures(self) -> None:
        lib = self._lib

        # Class / selector lookups
        lib.objc_getClass.restype = ctypes.c_void_p
        lib.objc_getClass.argtypes = [ctypes.c_char_p]

        lib.sel_registerName.restype = ctypes.c_void_p
        lib.sel_registerName.argtypes = [ctypes.c_char_p]

        # objc_msgSend — variadic; set minimal argtypes for the base call.
        # Callers that need extra args must cast via CFUNCTYPE.
        lib.objc_msgSend.restype = ctypes.c_void_p
        lib.objc_msgSend.argtypes = [ctypes.c_void_p, ctypes.c_void_p]

    # ------------------------------------------------------------------
    # Low-level helpers
    # ------------------------------------------------------------------

    def cls(self, name: str) -> int:
        """Look up an ObjC class by name.  Returns an opaque pointer (int)."""
        ptr = self._lib.objc_getClass(name.encode())
        if not ptr:
            raise RuntimeError(f"ObjC class not found: {name!r}")
        return ptr

    def sel(self, name: str) -> int:
        """Register / retrieve a selector by name."""
        ptr = self._lib.sel_registerName(name.encode())
        if not ptr:
            raise RuntimeError(f"Could not register selector: {name!r}")
        return ptr

    def msg(self, receiver: int, selector_name: str, *args) -> int:
        """Send an ObjC message and return the result as an int (pointer)."""
        if not receiver:
            raise RuntimeError(
                f"Cannot send [{selector_name!r}] to nil receiver"
            )
        sel_ptr = self.sel(selector_name)

        # Build a typed CFUNCTYPE so ctypes passes extra args correctly.
        # All args are c_void_p (pointer-sized).
        arg_types = [ctypes.c_void_p, ctypes.c_void_p] + [
            ctypes.c_void_p for _ in args
        ]
        func_type = ctypes.CFUNCTYPE(ctypes.c_void_p, *arg_types)
        typed_msg = func_type(self._lib.objc_msgSend)
        return typed_msg(receiver, sel_ptr, *args) or 0

    def msg_double(self, receiver: int, selector_name: str) -> float:
        """Send a message whose return type is double (e.g. property accessors)."""
        sel_ptr = self.sel(selector_name)
        func_type = ctypes.CFUNCTYPE(
            ctypes.c_double, ctypes.c_void_p, ctypes.c_void_p
        )
        typed_msg = func_type(self._lib.objc_msgSend)
        return float(typed_msg(receiver, sel_ptr))

    # ------------------------------------------------------------------
    # Foundation helpers
    # ------------------------------------------------------------------

    def nsstr(self, s: str) -> int:
        """Create an NSString from a Python str.  Returns opaque pointer."""
        cls_ptr = self.cls("NSString")
        sel_alloc = self.sel("alloc")
        sel_init = self.sel("initWithUTF8String:")

        alloc_ptr = self._lib.objc_getClass(b"NSString")
        # [NSString alloc]
        alloc_func = ctypes.CFUNCTYPE(ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p)
        instance = alloc_func(self._lib.objc_msgSend)(alloc_ptr, self.sel("alloc"))
        if not instance:
            raise RuntimeError("NSString alloc returned nil")
        # [instance initWithUTF8String: cstr]
        init_func = ctypes.CFUNCTYPE(
            ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_char_p
        )
        result = init_func(self._lib.objc_msgSend)(
            instance, self.sel("initWithUTF8String:"), s.encode("utf-8")
        )
        return result or 0

    def nsdata(self, raw: bytes) -> int:
        """Wrap *raw* bytes in an NSData object.  Returns opaque pointer."""
        buf = ctypes.create_string_buffer(raw)
        buf_ptr = ctypes.cast(buf, ctypes.c_void_p)

        alloc_ptr = self._lib.objc_getClass(b"NSData")
        alloc_func = ctypes.CFUNCTYPE(ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p)
        instance = alloc_func(self._lib.objc_msgSend)(alloc_ptr, self.sel("alloc"))
        if not instance:
            raise RuntimeError("NSData alloc returned nil")

        init_func = ctypes.CFUNCTYPE(
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_uint64,
        )
        result = init_func(self._lib.objc_msgSend)(
            instance,
            self.sel("initWithBytes:length:"),
            buf_ptr,
            ctypes.c_uint64(len(raw)),
        )
        return result or 0


# ---------------------------------------------------------------------------
# IndigoHID message construction
# ---------------------------------------------------------------------------

# Struct layout constants (reverse-engineered from idb and CoreSimulator)
_INDIGO_PAYLOAD_SIZE = 0x60          # 96 bytes per payload
_INDIGO_PAYLOAD_STRIDE = 0x90        # 144 bytes: payload + 48 bytes padding
_INDIGO_MSG_HEADER_SIZE = 0x18       # 24 bytes: 4 magic + 4 pad + 8 ns + 8 reserved
_INDIGO_MSG_TOTAL = _INDIGO_MSG_HEADER_SIZE + 2 * _INDIGO_PAYLOAD_STRIDE  # 312 bytes

# Event type constants
_INDIGO_EVENT_DOWN = 0x00000001
_INDIGO_EVENT_MOVE = 0x00000002
_INDIGO_EVENT_UP   = 0x00000003

# Header magic
_INDIGO_MAGIC = 0x0000001A


def _mach_time_ns() -> int:
    """Return a monotonic timestamp in nanoseconds (good enough for HID events)."""
    return int(time.monotonic_ns())


def _build_indigo_payload(
    event_type: int,
    x_norm: float,
    y_norm: float,
    pressure: float = 1.0,
) -> bytes:
    """Build a single 144-byte IndigoPayload + padding block.

    Layout (offsets relative to payload start):
      0x00  uint32  event_type
      0x04  uint32  touch_id (always 1 for single-touch)
      0x08  float   x_norm   (0.0 – 1.0)
      0x0C  float   y_norm   (0.0 – 1.0)
      0x10  float   pressure
      0x14  float   major_radius (0.05 = small finger)
      0x18  float   minor_radius (0.05)
      0x1C–0x8F  padding zeros

    Total bytes returned: _INDIGO_PAYLOAD_STRIDE (144)
    """
    payload = struct.pack(
        "<IIffffff",
        event_type,          # event_type
        1,                   # touch_id
        x_norm,              # x normalised
        y_norm,              # y normalised
        pressure,            # pressure
        0.05,                # major_radius
        0.05,                # minor_radius
        0.0,                 # reserved
    )
    # Pad to stride
    payload += b"\x00" * (_INDIGO_PAYLOAD_STRIDE - len(payload))
    return payload


def _build_indigo_message(
    event_type: int,
    x_norm: float,
    y_norm: float,
    pressure: float = 1.0,
) -> bytes:
    """Build a complete IndigoHID message ready for NSData wrapping.

    The message contains one active touch in slot 0 and a zeroed-out slot 1.
    """
    ts = _mach_time_ns()
    header = struct.pack("<IIQ", _INDIGO_MAGIC, 0, ts)
    # Pad header to _INDIGO_MSG_HEADER_SIZE
    header += b"\x00" * (_INDIGO_MSG_HEADER_SIZE - len(header))

    slot0 = _build_indigo_payload(event_type, x_norm, y_norm, pressure)
    slot1 = b"\x00" * _INDIGO_PAYLOAD_STRIDE   # second slot — unused

    return header + slot0 + slot1


# ---------------------------------------------------------------------------
# Device resolution helpers
# ---------------------------------------------------------------------------

def _find_sim_device(bridge: "_ObjCBridge", udid: str) -> int:
    """Locate and return the SimDevice object for *udid*.

    Chain:
      [SimServiceContext sharedServiceContext]
        → [ctx defaultDeviceSetWithError: nil]
        → [deviceSet devices]
        → find device where [device UDID] == udid  (or first booted if "booted")

    Returns an ObjC pointer (int).  Raises RuntimeError if not found.
    """
    # Shared context
    ctx_cls = bridge.cls("SimServiceContext")
    ctx = bridge.msg(ctx_cls, "sharedServiceContext")
    if not ctx:
        raise RuntimeError("SimServiceContext sharedServiceContext returned nil")

    # Default device set
    err_ptr = ctypes.c_void_p(0)
    err_addr = ctypes.addressof(err_ptr)

    # Try defaultDeviceSetWithError: first; fall back to defaultDeviceSet
    try:
        device_set = bridge.msg(
            ctx,
            "defaultDeviceSetWithError:",
            ctypes.c_void_p(err_addr),
        )
    except Exception:
        device_set = bridge.msg(ctx, "defaultDeviceSet")

    if not device_set:
        raise RuntimeError("Could not obtain SimDeviceSet")

    # Enumerate devices
    devices = bridge.msg(device_set, "devices")
    if not devices:
        raise RuntimeError("SimDeviceSet.devices returned nil")

    count_func = ctypes.CFUNCTYPE(
        ctypes.c_uint64, ctypes.c_void_p, ctypes.c_void_p
    )
    count = count_func(bridge._lib.objc_msgSend)(devices, bridge.sel("count"))
    logger.debug("SimDeviceSet contains %d device(s)", count)

    target_udid = udid.lower().strip()

    for i in range(count):
        obj_func = ctypes.CFUNCTYPE(
            ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint64
        )
        device = obj_func(bridge._lib.objc_msgSend)(
            devices, bridge.sel("objectAtIndex:"), ctypes.c_uint64(i)
        )
        if not device:
            continue

        try:
            dev_udid_obj = bridge.msg(device, "UDID")
            # Convert NSString → Python str via UTF8String
            str_func = ctypes.CFUNCTYPE(
                ctypes.c_char_p, ctypes.c_void_p, ctypes.c_void_p
            )
            dev_udid_cstr = str_func(bridge._lib.objc_msgSend)(
                dev_udid_obj, bridge.sel("UTF8String")
            )
            dev_udid = (dev_udid_cstr or b"").decode("utf-8", errors="replace").lower()
        except Exception:
            dev_udid = ""

        if target_udid == "booted":
            # Return the first booted device
            try:
                state = bridge.msg(device, "state")
                # SimDeviceState: 3 = Booted
                if state == 3:
                    logger.debug("Found booted device: %s", dev_udid)
                    return device
            except Exception:
                pass
        elif dev_udid == target_udid:
            logger.debug("Found device by UDID: %s", dev_udid)
            return device

    raise RuntimeError(
        f"Simulator device not found for UDID={udid!r}. "
        "Make sure the simulator is booted: xcrun simctl boot <udid>"
    )


def _create_hid_client(bridge: "_ObjCBridge", device: int) -> int:
    """Create and return a SimDeviceLegacyHIDClient for *device*.

    Uses:
        [[SimDeviceLegacyHIDClient alloc] initWithDevice:device error:nil]
    """
    cls = bridge.cls("SimDeviceLegacyHIDClient")

    alloc_func = ctypes.CFUNCTYPE(
        ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p
    )
    instance = alloc_func(bridge._lib.objc_msgSend)(cls, bridge.sel("alloc"))
    if not instance:
        raise RuntimeError("SimDeviceLegacyHIDClient alloc returned nil")

    err_ptr = ctypes.c_void_p(0)
    init_func = ctypes.CFUNCTYPE(
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
    )
    client = init_func(bridge._lib.objc_msgSend)(
        instance,
        bridge.sel("initWithDevice:error:"),
        ctypes.c_void_p(device),
        ctypes.c_void_p(0),   # error pointer — nil; we check return value
    )
    if not client:
        raise RuntimeError(
            "SimDeviceLegacyHIDClient initWithDevice:error: returned nil. "
            "Is the simulator booted? Does this Xcode version support this API?"
        )
    return client


def _send_hid_message(
    bridge: "_ObjCBridge",
    client: int,
    msg_bytes: bytes,
) -> None:
    """Send a raw IndigoHID message via the HID client.

    Calls: [client sendWithData:nsdata error:nil]

    If the selector is not found (API version mismatch) falls back to
    [client send:nsdata error:nil].
    """
    data = bridge.nsdata(msg_bytes)
    if not data:
        raise RuntimeError("Failed to create NSData for IndigoHID message")

    # Try sendWithData:error: first, then send:error:
    for sel_name in ("sendWithData:error:", "send:error:"):
        try:
            send_func = ctypes.CFUNCTYPE(
                ctypes.c_bool,
                ctypes.c_void_p,
                ctypes.c_void_p,
                ctypes.c_void_p,
                ctypes.c_void_p,
            )
            result = send_func(bridge._lib.objc_msgSend)(
                client,
                bridge.sel(sel_name),
                ctypes.c_void_p(data),
                ctypes.c_void_p(0),  # error — nil
            )
            logger.debug("sendHID via [%s] → %s", sel_name, result)
            return
        except Exception as exc:
            logger.debug("Selector %r failed: %s — trying next", sel_name, exc)

    raise RuntimeError(
        "No compatible send selector found on SimDeviceLegacyHIDClient. "
        "The private API may have changed in this Xcode version."
    )


# ---------------------------------------------------------------------------
# Public backend class
# ---------------------------------------------------------------------------

class IndigoHIDBackend:
    """Headless touch injection via Apple's IndigoHID protocol.

    Sends HID events directly to the simulator process via Mach IPC using
    Apple's private ``SimDeviceLegacyHIDClient``.  No Simulator window is
    required.  No Accessibility permission is needed.  Only Xcode must be
    installed.

    Args:
        udid: Simulator UDID (or ``"booted"`` to target the first booted device).
        device_width: Device width in logical points (e.g. 393 for iPhone 16 Pro).
        device_height: Device height in logical points (e.g. 852 for iPhone 16 Pro).
        scale_factor: Retina scale factor (2 or 3).  Used only for documentation;
            all coordinates passed to this backend are already in logical points.

    Raises:
        RuntimeError: On construction if SimulatorKit cannot be loaded, or if the
            target device cannot be found.

    Example::

        backend = IndigoHIDBackend(udid="booted", device_width=393, device_height=852)
        backend.tap(196.5, 400.0)
        backend.swipe(196.5, 700.0, 196.5, 200.0, duration=0.4)
    """

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

    def __init__(
        self,
        udid: str = "booted",
        device_width: int = 393,
        device_height: int = 852,
        scale_factor: int = 3,
    ) -> None:
        self._udid = udid
        self._device_width = device_width
        self._device_height = device_height
        self._scale_factor = scale_factor

        # Lazy-initialised state — populated on first use
        self._bridge: Optional[_ObjCBridge] = None
        self._device: Optional[int] = None
        self._hid_client: Optional[int] = None

    # ------------------------------------------------------------------
    # Availability check
    # ------------------------------------------------------------------

    @classmethod
    def is_available(cls) -> bool:
        """Return ``True`` if SimulatorKit and CoreSimulator can be loaded.

        This is a lightweight probe — it loads the frameworks but does not
        attempt to connect to any simulator device.

        Returns:
            bool: ``True`` when all required private frameworks are loadable.
        """
        return _load_frameworks()

    # ------------------------------------------------------------------
    # Internal setup
    # ------------------------------------------------------------------

    def _ensure_ready(self) -> None:
        """Initialise the ObjC bridge, locate the SimDevice, and create the
        HID client on demand (lazy initialisation).

        Raises:
            RuntimeError: If frameworks fail to load or the device is not found.
        """
        if self._hid_client is not None:
            return   # already initialised

        if not _load_frameworks():
            raise RuntimeError(
                "IndigoHIDBackend requires Xcode to be installed. "
                "Install Xcode and run: sudo xcode-select --switch "
                "/Applications/Xcode.app/Contents/Developer"
            )

        assert _objc is not None
        self._bridge = _ObjCBridge(_objc)

        self._device = _find_sim_device(self._bridge, self._udid)
        self._hid_client = _create_hid_client(self._bridge, self._device)
        logger.debug(
            "IndigoHIDBackend ready: udid=%r device_ptr=0x%x client_ptr=0x%x",
            self._udid,
            self._device,
            self._hid_client,
        )

    def _norm(self, x: float, y: float) -> Tuple[float, float]:
        """Normalise device-point coordinates to the 0.0–1.0 range.

        Args:
            x: Horizontal position in device logical points.
            y: Vertical position in device logical points.

        Returns:
            ``(x_norm, y_norm)`` — values clamped to [0.0, 1.0].
        """
        x_n = max(0.0, min(1.0, x / self._device_width))
        y_n = max(0.0, min(1.0, y / self._device_height))
        return x_n, y_n

    def _send(self, event_type: int, x: float, y: float, pressure: float = 1.0) -> None:
        """Build and send a single IndigoHID event.

        Args:
            event_type: One of _INDIGO_EVENT_DOWN, _INDIGO_EVENT_MOVE, _INDIGO_EVENT_UP.
            x: Horizontal position in device logical points.
            y: Vertical position in device logical points.
            pressure: Touch pressure (1.0 = normal finger contact).
        """
        self._ensure_ready()
        assert self._bridge is not None
        assert self._hid_client is not None

        x_n, y_n = self._norm(x, y)
        msg_bytes = _build_indigo_message(event_type, x_n, y_n, pressure)
        _send_hid_message(self._bridge, self._hid_client, msg_bytes)

    # ------------------------------------------------------------------
    # Public gesture API
    # ------------------------------------------------------------------

    def tap(self, x: float, y: float) -> None:
        """Tap at device-point coordinates.

        Sends a DOWN event followed by an UP event with an 80 ms hold,
        matching the timing that most iOS touch recognisers expect.

        Args:
            x: Horizontal position in device logical points (e.g. 196.5).
            y: Vertical position in device logical points (e.g. 400.0).

        Raises:
            RuntimeError: If IndigoHID frameworks are unavailable or the
                HID message cannot be sent.
        """
        logger.debug("tap(%.1f, %.1f)", x, y)
        self._send(_INDIGO_EVENT_DOWN, x, y)
        time.sleep(0.08)
        self._send(_INDIGO_EVENT_UP, x, y)
        time.sleep(0.1)

    def swipe(
        self,
        x1: float,
        y1: float,
        x2: float,
        y2: float,
        duration: float = 0.3,
        steps: int = 20,
    ) -> None:
        """Swipe gesture from (x1, y1) to (x2, y2) in device-point coordinates.

        Sends a DOWN event, then *steps* MOVE events interpolated linearly
        across *duration* seconds, then an UP event.

        Args:
            x1: Start horizontal position in logical points.
            y1: Start vertical position in logical points.
            x2: End horizontal position in logical points.
            y2: End vertical position in logical points.
            duration: Total gesture duration in seconds (default: 0.3).
            steps: Number of intermediate move events (default: 20).

        Raises:
            RuntimeError: If IndigoHID frameworks are unavailable.
        """
        logger.debug("swipe(%.1f,%.1f → %.1f,%.1f) %.2fs", x1, y1, x2, y2, duration)

        self._send(_INDIGO_EVENT_DOWN, x1, y1)
        time.sleep(0.02)  # brief settle before dragging

        step_sleep = duration / max(steps, 1)
        for i in range(1, steps + 1):
            t = i / steps
            mx = x1 + (x2 - x1) * t
            my = y1 + (y2 - y1) * t
            self._send(_INDIGO_EVENT_MOVE, mx, my)
            time.sleep(step_sleep)

        self._send(_INDIGO_EVENT_UP, x2, y2)
        time.sleep(0.1)

    def long_press(self, x: float, y: float, duration: float = 3.0) -> None:
        """Long press at device-point coordinates.

        Sends a DOWN event, holds for *duration* seconds, then sends an UP
        event.  iOS context-menu and selection recognisers typically trigger
        after ~0.5 s; use ``duration=3.0`` for the default hold behaviour.

        Args:
            x: Horizontal position in device logical points.
            y: Vertical position in device logical points.
            duration: How long to hold the press in seconds (default: 3.0).

        Raises:
            RuntimeError: If IndigoHID frameworks are unavailable.
        """
        logger.debug("long_press(%.1f, %.1f) %.1fs", x, y, duration)
        self._send(_INDIGO_EVENT_DOWN, x, y)
        time.sleep(duration)
        self._send(_INDIGO_EVENT_UP, x, y)
        time.sleep(0.2)

    def screenshot(self, output_path: Optional[str] = None) -> str:
        """Capture a screenshot of the simulator using ``xcrun simctl io``.

        Does NOT require the IndigoHID bridge to be active — works even if
        :meth:`is_available` returns False (only ``simctl`` is needed).

        Args:
            output_path: Optional path where the PNG should be written.
                If ``None``, a temporary file is created and its path is returned.

        Returns:
            Absolute path to the saved PNG file.

        Raises:
            RuntimeError: If ``xcrun simctl io screenshot`` fails.
        """
        if output_path is None:
            fd, output_path = tempfile.mkstemp(suffix=".png", prefix="specterqa_")
            os.close(fd)

        result = subprocess.run(
            ["xcrun", "simctl", "io", self._udid, "screenshot", output_path],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"xcrun simctl io screenshot failed (rc={result.returncode}): "
                f"{result.stderr.strip()}"
            )

        logger.debug("screenshot saved to %s", output_path)
        return output_path

    # ------------------------------------------------------------------
    # Dunder helpers
    # ------------------------------------------------------------------

    def __repr__(self) -> str:
        status = "ready" if getattr(self, "_hid_client", None) else "lazy"
        udid = getattr(self, "_udid", "?")
        w = getattr(self, "_device_width", 0)
        h = getattr(self, "_device_height", 0)
        sf = getattr(self, "_scale_factor", 0)
        return (
            f"IndigoHIDBackend(udid={udid!r}, "
            f"{w}x{h}pt @{sf}x, {status})"
        )
