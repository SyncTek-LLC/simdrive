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
import json
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
_FOUNDATION_PATH = "/System/Library/Frameworks/Foundation.framework/Foundation"
_CORE_FOUNDATION_PATH = "/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation"
_CORE_SIM_PATH = "/Library/Developer/PrivateFrameworks/CoreSimulator.framework/CoreSimulator"

# kCFStringEncodingUTF8 — used when creating CFString / NSString via CoreFoundation
_kCFStringEncodingUTF8 = 0x08000100


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
    except OSError:
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


def _setup_framework_paths() -> None:
    """Prepend private framework directories to DYLD_FRAMEWORK_PATH.

    CoreSimulator's internal ObjC classes (e.g. SimServiceContext) depend on
    additional private frameworks that must be on the dyld load path before any
    message is sent to those classes.  Setting DYLD_FRAMEWORK_PATH here covers
    the case where the process was not launched with these paths already set.

    Note: DYLD_FRAMEWORK_PATH is read by dyld at ``dlopen`` time.  Setting it
    here only helps for frameworks loaded *after* this call.  Frameworks already
    mapped into the process are unaffected, which is fine — we call this before
    loading CoreSimulator.
    """
    dev = _xcode_developer_path()
    if not dev:
        return

    candidates = [
        f"{dev}/Library/PrivateFrameworks",
        "/Library/Developer/PrivateFrameworks",
        f"{dev}/Platforms/iPhoneOS.platform/Library/Developer/CoreSimulator/Profiles/Runtimes",
    ]

    new_dirs = ":".join(p for p in candidates if os.path.isdir(p))
    if not new_dirs:
        return

    existing = os.environ.get("DYLD_FRAMEWORK_PATH", "")
    if existing:
        merged = f"{new_dirs}:{existing}"
    else:
        merged = new_dirs
    os.environ["DYLD_FRAMEWORK_PATH"] = merged
    logger.debug("DYLD_FRAMEWORK_PATH set to: %s", merged)


# ---------------------------------------------------------------------------
# Lazy-loaded framework handles (module-level singletons)
# ---------------------------------------------------------------------------

_objc: Optional[ctypes.CDLL] = None
_cf: Optional[ctypes.CDLL] = None  # CoreFoundation — for CFString creation
_core_sim: Optional[ctypes.CDLL] = None
_sim_kit: Optional[ctypes.CDLL] = None

_frameworks_loaded: Optional[bool] = None  # None = not tried yet


def _load_frameworks() -> bool:
    """Attempt to load all required private frameworks.

    Returns True on success.  On failure logs a warning and returns False.
    Safe to call multiple times — cached after the first attempt.

    Load order:
      1. Set DYLD_FRAMEWORK_PATH so CoreSimulator's dependencies are resolvable.
      2. Load libobjc (always available on macOS).
      3. Load CoreSimulator — try the system PrivateFrameworks path first, then
         fall back to the Xcode developer tree (path varies by Xcode version).
      4. Load SimulatorKit from the active Xcode developer tree.
    """
    global _objc, _cf, _core_sim, _sim_kit, _frameworks_loaded
    if _frameworks_loaded is not None:
        return _frameworks_loaded

    # Step 1 — configure DYLD paths before any LoadLibrary call so that
    # CoreSimulator's transitive dependencies can be found by dyld.
    _setup_framework_paths()

    try:
        _objc = ctypes.cdll.LoadLibrary(_OBJC_LIB)
        # CoreFoundation is needed for CFStringCreateWithCString (used in nsstr()).
        # Foundation must also be loaded so NSString class methods work correctly.
        _cf = ctypes.cdll.LoadLibrary(_CORE_FOUNDATION_PATH)
        _cf.CFStringCreateWithCString.restype = ctypes.c_void_p
        _cf.CFStringCreateWithCString.argtypes = [
            ctypes.c_void_p,  # allocator (NULL = kCFAllocatorDefault)
            ctypes.c_char_p,  # cStr
            ctypes.c_uint32,  # encoding
        ]
        ctypes.cdll.LoadLibrary(_FOUNDATION_PATH)

        # Step 2 — load CoreSimulator, with Xcode-tree fallback
        try:
            _core_sim = ctypes.cdll.LoadLibrary(_CORE_SIM_PATH)
        except OSError:
            dev = _xcode_developer_path()
            if not dev:
                raise
            alt_core_sim = os.path.join(
                dev,
                "Library",
                "PrivateFrameworks",
                "CoreSimulator.framework",
                "CoreSimulator",
            )
            logger.debug(
                "CoreSimulator not found at system path; trying Xcode path: %s",
                alt_core_sim,
            )
            _core_sim = ctypes.cdll.LoadLibrary(alt_core_sim)

        # Step 3 — load SimulatorKit
        sk_path = _sim_kit_path()
        if not sk_path or not os.path.exists(sk_path):
            logger.warning(
                "SimulatorKit not found at %r — is Xcode installed? (xcode-select -p returned: %r)",
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

    def __init__(self, libobjc: ctypes.CDLL, cf: Optional[ctypes.CDLL] = None) -> None:
        self._lib = libobjc
        self._cf = cf
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

        # class_respondsToSelector — used to guard against "unrecognized selector" crashes
        lib.class_respondsToSelector.restype = ctypes.c_bool
        lib.class_respondsToSelector.argtypes = [ctypes.c_void_p, ctypes.c_void_p]

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

    def responds_to_selector(self, obj_ptr: int, selector_name: str) -> bool:
        """Return True if *obj_ptr* (class or instance) responds to *selector_name*.

        Uses ``class_respondsToSelector`` — a pure C runtime query that does NOT
        send an ObjC message.  Safe to call for unknown selectors without risk of
        triggering the process-terminating ``NSInvalidArgumentException``.

        For class objects, this checks class-method dispatch.
        For instance objects, this checks instance-method dispatch.
        In both cases we use the metaclass for the lookup because
        ``class_respondsToSelector`` always operates on the metaclass chain.
        """
        if not obj_ptr:
            return False
        sel_ptr = self._lib.sel_registerName(selector_name.encode())
        if not sel_ptr:
            return False
        # Use class_respondsToSelector on the object's class (works for both
        # class objects and instances — ObjC runtime handles the metaclass chain).
        self._lib.object_getClass.restype = ctypes.c_void_p
        self._lib.object_getClass.argtypes = [ctypes.c_void_p]
        isa = self._lib.object_getClass(ctypes.c_void_p(obj_ptr)) or obj_ptr
        return bool(self._lib.class_respondsToSelector(ctypes.c_void_p(isa), ctypes.c_void_p(sel_ptr)))

    def msg(self, receiver: int, selector_name: str, *args) -> int:
        """Send an ObjC message and return the result as an int (pointer)."""
        if not receiver:
            raise RuntimeError(f"Cannot send [{selector_name!r}] to nil receiver")
        sel_ptr = self.sel(selector_name)

        # Build a typed CFUNCTYPE so ctypes passes extra args correctly.
        # All args are c_void_p (pointer-sized).
        arg_types = [ctypes.c_void_p, ctypes.c_void_p] + [ctypes.c_void_p for _ in args]
        func_type = ctypes.CFUNCTYPE(ctypes.c_void_p, *arg_types)
        typed_msg = func_type(self._lib.objc_msgSend)
        return typed_msg(receiver, sel_ptr, *args) or 0

    def msg_double(self, receiver: int, selector_name: str) -> float:
        """Send a message whose return type is double (e.g. property accessors)."""
        sel_ptr = self.sel(selector_name)
        func_type = ctypes.CFUNCTYPE(ctypes.c_double, ctypes.c_void_p, ctypes.c_void_p)
        typed_msg = func_type(self._lib.objc_msgSend)
        return float(typed_msg(receiver, sel_ptr))

    # ------------------------------------------------------------------
    # Foundation helpers
    # ------------------------------------------------------------------

    def nsstr(self, s: str) -> int:
        """Create an NSString from a Python str.  Returns opaque pointer.

        Preferred path: ``CFStringCreateWithCString`` (CoreFoundation C API,
        fixed signature — no ARM64 variadic-call issues with ctypes).
        Fallback: ``[NSString alloc] initWithUTF8String:`` via objc_msgSend
        (may fail on ARM64 with ctypes due to variadic ABI issues).
        """
        encoded = s.encode("utf-8")

        # --- Preferred: use CoreFoundation (toll-free bridged to NSString) ---
        if self._cf is not None:
            result = self._cf.CFStringCreateWithCString(None, encoded, _kCFStringEncodingUTF8)
            if result:
                return result
            # Fall through to ObjC path on failure

        # --- Fallback: ObjC alloc/init ---
        alloc_ptr = self._lib.objc_getClass(b"NSString")
        # [NSString alloc]
        alloc_func = ctypes.CFUNCTYPE(ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p)
        instance = alloc_func(self._lib.objc_msgSend)(alloc_ptr, self.sel("alloc"))
        if not instance:
            raise RuntimeError("NSString alloc returned nil")
        # [instance initWithUTF8String: cstr] — pass bytes via c_void_p + buffer
        # to avoid ARM64 ctypes variadic-call ABI issues with c_char_p.
        buf = ctypes.create_string_buffer(encoded + b"\x00")
        buf_ptr = ctypes.cast(buf, ctypes.c_void_p)
        init_func = ctypes.CFUNCTYPE(ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p)
        result = init_func(self._lib.objc_msgSend)(instance, self.sel("initWithUTF8String:"), buf_ptr)
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
_INDIGO_PAYLOAD_SIZE = 0x60  # 96 bytes per payload
_INDIGO_PAYLOAD_STRIDE = 0x90  # 144 bytes: payload + 48 bytes padding
_INDIGO_MSG_HEADER_SIZE = 0x18  # 24 bytes: 4 magic + 4 pad + 8 ns + 8 reserved
_INDIGO_MSG_TOTAL = _INDIGO_MSG_HEADER_SIZE + 2 * _INDIGO_PAYLOAD_STRIDE  # 312 bytes

# Event type constants
_INDIGO_EVENT_DOWN = 0x00000001
_INDIGO_EVENT_MOVE = 0x00000002
_INDIGO_EVENT_UP = 0x00000003

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
        event_type,  # event_type
        1,  # touch_id
        x_norm,  # x normalised
        y_norm,  # y normalised
        pressure,  # pressure
        0.05,  # major_radius
        0.05,  # minor_radius
        0.0,  # reserved
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
    slot1 = b"\x00" * _INDIGO_PAYLOAD_STRIDE  # second slot — unused

    return header + slot0 + slot1


# ---------------------------------------------------------------------------
# Device resolution helpers
# ---------------------------------------------------------------------------


def _resolve_booted_udid() -> str:
    """Use ``xcrun simctl list devices booted -j`` to find the actual UDID of the
    currently booted simulator.

    Returns the UDID string on success, or ``""`` on any failure.
    """
    try:
        result = subprocess.run(
            ["xcrun", "simctl", "list", "devices", "booted", "-j"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode != 0:
            return ""
        data = json.loads(result.stdout)
        for _runtime, devices in data.get("devices", {}).items():
            for d in devices:
                if d.get("state") == "Booted" and d.get("udid"):
                    return d["udid"]
    except Exception as exc:
        logger.debug("simctl booted UDID lookup failed: %s", exc)
    return ""


def _extract_udid_str(bridge: "_ObjCBridge", udid_obj: int) -> str:
    """Extract a lowercase UDID string from an ObjC UDID object.

    Handles both:
    - ``NSString`` (older CoreSimulator) — responds to ``UTF8String``
    - ``NSUUID``  (Xcode 16+)           — responds to ``UUIDString`` → ``UTF8String``
    """
    if not udid_obj:
        return ""
    str_func = ctypes.CFUNCTYPE(ctypes.c_char_p, ctypes.c_void_p, ctypes.c_void_p)
    try:
        if bridge.responds_to_selector(udid_obj, "UTF8String"):
            # NSString path
            cstr = str_func(bridge._lib.objc_msgSend)(udid_obj, bridge.sel("UTF8String"))
            return (cstr or b"").decode("utf-8", errors="replace").lower()
        if bridge.responds_to_selector(udid_obj, "UUIDString"):
            # NSUUID path — call UUIDString to get an NSString first
            nsstr = bridge.msg(udid_obj, "UUIDString")
            if nsstr and bridge.responds_to_selector(nsstr, "UTF8String"):
                cstr = str_func(bridge._lib.objc_msgSend)(nsstr, bridge.sel("UTF8String"))
                return (cstr or b"").decode("utf-8", errors="replace").lower()
    except Exception as exc:  # noqa: BLE001 — ObjC bridge may raise arbitrary errors
        logger.debug("_extract_udid_str failed: %s", exc)
    return ""


def _enum_devices_from_set(bridge: "_ObjCBridge", device_set: int, target_udid: str) -> int:
    """Walk the ``[SimDeviceSet devices]`` NSArray looking for *target_udid*.

    *target_udid* must already be a lowercase concrete UDID (not ``"booted"``).

    Uses ``objectEnumerator`` + ``nextObject`` rather than ``objectAtIndex:``
    because passing ``NSUInteger`` via ctypes CFUNCTYPE on ARM64 is unreliable
    (the index argument is misinterpreted and the same object is returned for
    every index).

    Returns the SimDevice pointer (int) or 0 if not found.
    """
    devices = bridge.msg(device_set, "devices")
    if not devices:
        return 0

    enumerator = bridge.msg(devices, "objectEnumerator")
    if not enumerator:
        # Fall back to count + objectAtIndex: — less reliable on ARM64 but better
        # than returning nothing.
        count_func = ctypes.CFUNCTYPE(ctypes.c_uint64, ctypes.c_void_p, ctypes.c_void_p)
        count = count_func(bridge._lib.objc_msgSend)(devices, bridge.sel("count"))
        logger.debug("SimDeviceSet contains %d device(s) (no enumerator)", count)
        obj_func = ctypes.CFUNCTYPE(ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ulong)
        for i in range(count):
            device = obj_func(bridge._lib.objc_msgSend)(devices, bridge.sel("objectAtIndex:"), ctypes.c_ulong(i)) or 0
            if not device:
                continue
            dev_udid = _extract_udid_str(bridge, bridge.msg(device, "UDID"))
            if dev_udid == target_udid:
                logger.debug("Found device by UDID (fallback enum): %s", dev_udid)
                return device
        return 0

    # Preferred path: NSEnumerator — avoids NSUInteger passing issues.
    next_func = ctypes.CFUNCTYPE(ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p)
    seen = 0
    while True:
        device = next_func(bridge._lib.objc_msgSend)(enumerator, bridge.sel("nextObject")) or 0
        if not device:
            break
        seen += 1
        try:
            udid_obj = bridge.msg(device, "UDID")
            dev_udid = _extract_udid_str(bridge, udid_obj)
        except Exception as exc:  # noqa: BLE001 — ObjC bridge may raise arbitrary errors
            logger.debug("Device UDID extraction failed: %s", exc)
            dev_udid = ""

        if dev_udid == target_udid:
            logger.debug("Found device by UDID: %s (enumerated %d)", dev_udid, seen)
            return device

    logger.debug("Enumerated %d device(s), target %r not found", seen, target_udid)
    return 0


def _get_sim_service_context(bridge: "_ObjCBridge") -> int:
    """Return a ``SimServiceContext`` instance using whatever selector this
    Xcode version exposes.

    Xcode ≤ 15 exposed ``+sharedServiceContext``.
    Xcode 16+ uses ``+sharedServiceContextForDeveloperDir:error:`` or
    ``+serviceContextForDeveloperDir:error:``.

    Every selector is guarded by ``responds_to_selector`` before use, so an
    unrecognised selector cannot terminate the process.

    Returns the context pointer (int), or 0 on failure.
    """
    ctx_cls = bridge.cls("SimServiceContext")

    # Xcode 16+: sharedServiceContextForDeveloperDir:error:
    for sel_name in (
        "sharedServiceContextForDeveloperDir:error:",
        "serviceContextForDeveloperDir:error:",
    ):
        if bridge.responds_to_selector(ctx_cls, sel_name):
            dev = _xcode_developer_path()
            if not dev:
                continue
            ns_dev = bridge.nsstr(dev)
            if not ns_dev:
                continue
            call_func = ctypes.CFUNCTYPE(
                ctypes.c_void_p,
                ctypes.c_void_p,
                ctypes.c_void_p,
                ctypes.c_void_p,
                ctypes.c_void_p,
            )
            ctx = (
                call_func(bridge._lib.objc_msgSend)(
                    ctx_cls,
                    bridge.sel(sel_name),
                    ctypes.c_void_p(ns_dev),
                    ctypes.c_void_p(0),  # error — nil
                )
                or 0
            )
            if ctx:
                logger.debug("Got SimServiceContext via %r", sel_name)
                return ctx

    # Xcode ≤ 15: sharedServiceContext (no-arg class method)
    if bridge.responds_to_selector(ctx_cls, "sharedServiceContext"):
        ctx = bridge.msg(ctx_cls, "sharedServiceContext") or 0
        if ctx:
            logger.debug("Got SimServiceContext via sharedServiceContext")
            return ctx

    return 0


def _find_sim_device(bridge: "_ObjCBridge", udid: str) -> int:
    """Locate and return the SimDevice ObjC object for *udid*.

    Strategy (tried in order, stops at first success):

    1. Resolve ``"booted"`` to an actual UDID via ``xcrun simctl``.
    2. Ensure private-framework paths are on DYLD_FRAMEWORK_PATH.
    3. Try ``SimServiceContext`` (Xcode 16+: ``sharedServiceContextForDeveloperDir:error:``;
       Xcode ≤ 15: ``sharedServiceContext``) → ``defaultDeviceSetWithError:`` → enumerate.
    4. Fall back to ``SimServiceContext.deviceSetWithPath:error:`` with the default path.

    Every selector is guarded by ``responds_to_selector`` to prevent the
    process-terminating ``NSInvalidArgumentException`` ("unrecognised selector")
    that ctypes cannot catch.

    Returns an ObjC pointer (int).  Raises ``RuntimeError`` if not found.
    """
    # --- Step 1: resolve "booted" alias ---
    actual_udid = udid.strip()
    if actual_udid.lower() == "booted":
        resolved = _resolve_booted_udid()
        if resolved:
            logger.debug("Resolved 'booted' → %s via simctl", resolved)
            actual_udid = resolved

    target_udid = actual_udid.lower()

    # --- Step 2: set up framework paths (idempotent) ---
    _setup_framework_paths()

    # --- Step 3: SimServiceContext → defaultDeviceSetWithError: ---
    try:
        ctx = _get_sim_service_context(bridge)
        if ctx:
            device_set = 0
            # Prefer defaultDeviceSetWithError: (Xcode 16+)
            if bridge.responds_to_selector(ctx, "defaultDeviceSetWithError:"):
                device_set = bridge.msg(ctx, "defaultDeviceSetWithError:", ctypes.c_void_p(0)) or 0
            # Fall back to deviceSetWithPath: using default path
            if not device_set:
                devices_dir = os.path.expanduser("~/Library/Developer/CoreSimulator/Devices")
                if bridge.responds_to_selector(ctx, "deviceSetWithPath:error:"):
                    ns_path = bridge.nsstr(devices_dir)
                    if ns_path:
                        call_func = ctypes.CFUNCTYPE(
                            ctypes.c_void_p,
                            ctypes.c_void_p,
                            ctypes.c_void_p,
                            ctypes.c_void_p,
                            ctypes.c_void_p,
                        )
                        device_set = (
                            call_func(bridge._lib.objc_msgSend)(
                                ctx,
                                bridge.sel("deviceSetWithPath:error:"),
                                ctypes.c_void_p(ns_path),
                                ctypes.c_void_p(0),
                            )
                            or 0
                        )

            if device_set:
                device = _enum_devices_from_set(bridge, device_set, target_udid)
                if device:
                    return device
                logger.warning(
                    "SimServiceContext path: device %r not found in device set",
                    actual_udid,
                )
            else:
                logger.warning("SimServiceContext obtained but no device set selector responded")
        else:
            logger.warning("Could not obtain SimServiceContext — all selectors unavailable or returned nil")
    except Exception as exc:
        logger.warning("SimServiceContext path failed: %s", exc)

    raise RuntimeError(
        f"Simulator device not found for UDID={udid!r}. Make sure the simulator is booted: xcrun simctl boot <udid>"
    )


_HID_CLIENT_CLASS_NAMES = (
    # Xcode 16+: Swift-module-qualified name
    "SimulatorKit.SimDeviceLegacyHIDClient",
    # Xcode ≤ 15: unqualified
    "SimDeviceLegacyHIDClient",
)


def _create_hid_client(bridge: "_ObjCBridge", device: int) -> int:
    """Create and return a SimDeviceLegacyHIDClient for *device*.

    Tries both the Swift-module-qualified class name (Xcode 16+) and the
    unqualified name (Xcode ≤ 15).

    Uses:
        [[SimulatorKit.SimDeviceLegacyHIDClient alloc] initWithDevice:device error:nil]
    """
    cls = None
    for class_name in _HID_CLIENT_CLASS_NAMES:
        try:
            cls = bridge.cls(class_name)
            logger.debug("HID client class found: %r", class_name)
            break
        except RuntimeError:
            continue
    if not cls:
        raise RuntimeError(
            "SimDeviceLegacyHIDClient not found (tried: "
            + ", ".join(_HID_CLIENT_CLASS_NAMES)
            + "). Is SimulatorKit loaded?"
        )

    alloc_func = ctypes.CFUNCTYPE(ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p)
    instance = alloc_func(bridge._lib.objc_msgSend)(cls, bridge.sel("alloc"))
    if not instance:
        raise RuntimeError("SimDeviceLegacyHIDClient alloc returned nil")

    # Guard: on Xcode 16+, ``initWithDevice:error:`` triggers a Swift
    # preconditionFailure (SIGTRAP) if called from outside Simulator.app's
    # process.  Detect this by checking whether the class name is module-qualified
    # (Swift-namespaced) — that's the Xcode 16+ form.  When detected, raise a
    # clear RuntimeError instead of crashing the process.
    #
    # In Xcode ≤ 15 the class is ``SimDeviceLegacyHIDClient`` (no module prefix)
    # and ``initWithDevice:error:`` works fine from external processes.
    for name in _HID_CLIENT_CLASS_NAMES:
        try:
            if bridge.cls(name) == cls and "." in name:
                # This is the Swift-namespaced (Xcode 16+) variant.
                # Calling initWithDevice:error: would SIGTRAP — raise cleanly.
                raise RuntimeError(
                    "SimulatorKit.SimDeviceLegacyHIDClient.initWithDevice:error: "
                    "cannot be called from outside Simulator.app in Xcode 16+.  "
                    "IndigoHID direct injection is not available in this Xcode version.  "
                    "Use XCTestBackend for automation on Xcode 16+ / iOS 26+."
                )
        except RuntimeError:
            raise
        except Exception as exc:  # noqa: BLE001 — ObjC bridge probe: any error = path not supported
            logger.debug("IndigoHID Xcode 16+ init path failed: %s", exc)

    # Try initWithDevice:error: (Xcode ≤ 15 path)
    init_func = ctypes.CFUNCTYPE(
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
    )
    client = (
        init_func(bridge._lib.objc_msgSend)(
            instance,
            bridge.sel("initWithDevice:error:"),
            ctypes.c_void_p(device),
            ctypes.c_void_p(0),  # error pointer — nil; we check return value
        )
        or 0
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

    Supports two calling conventions depending on Xcode version:

    **Xcode 16+ (SimulatorKit.SimDeviceLegacyHIDClient):**
        ``sendWithMessage:freeWhenDone:completionQueue:completion:``
        Takes a ``const IndigoHIDMessageStruct *`` (raw C pointer), a BOOL
        ``freeWhenDone``, an optional ``dispatch_queue_t``, and an optional
        completion block.  We pass the raw bytes buffer directly and use
        ``freeWhenDone=NO`` so the buffer can be managed by Python.

    **Xcode ≤ 15 (SimDeviceLegacyHIDClient):**
        ``sendWithData:error:`` or ``send:error:`` — wraps bytes in NSData.
    """
    # --- Xcode 16+ path: sendWithMessage:freeWhenDone:completionQueue:completion: ---
    if bridge.responds_to_selector(client, "sendWithMessage:freeWhenDone:completionQueue:completion:"):
        buf = ctypes.create_string_buffer(msg_bytes)
        buf_ptr = ctypes.cast(buf, ctypes.c_void_p)
        send_func = ctypes.CFUNCTYPE(
            ctypes.c_void_p,  # return void
            ctypes.c_void_p,  # self
            ctypes.c_void_p,  # SEL
            ctypes.c_void_p,  # IndigoHIDMessageStruct *message
            ctypes.c_bool,  # BOOL freeWhenDone
            ctypes.c_void_p,  # dispatch_queue_t completionQueue (nil)
            ctypes.c_void_p,  # completion block (nil)
        )
        send_func(bridge._lib.objc_msgSend)(
            client,
            bridge.sel("sendWithMessage:freeWhenDone:completionQueue:completion:"),
            buf_ptr,
            False,  # freeWhenDone = NO — Python owns the buffer
            ctypes.c_void_p(0),  # completionQueue = nil
            ctypes.c_void_p(0),  # completion = nil
        )
        logger.debug("sendHID via sendWithMessage:freeWhenDone:completionQueue:completion:")
        return

    # --- Xcode ≤ 15 path: NSData-based send ---
    data = bridge.nsdata(msg_bytes)
    if not data:
        raise RuntimeError("Failed to create NSData for IndigoHID message")

    for sel_name in ("sendWithData:error:", "send:error:"):
        if not bridge.responds_to_selector(client, sel_name):
            continue
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
    def _can_create_hid_client(cls) -> bool:
        """Probe whether ``SimDeviceLegacyHIDClient alloc initWithDevice:error:``
        can actually be called on this machine.

        On Xcode 16+, ``initWithDevice:error:`` is a Swift ``preconditionFailure``
        when invoked outside Simulator.app — the class is namespaced as
        ``SimulatorKit.SimDeviceLegacyHIDClient`` (dot in the name).  We detect
        this early and return ``False`` so ``is_available()`` tells the truth.

        The check is intentionally *cheap*: it only loads frameworks, looks up
        the class, and inspects the class name — it does NOT try to find a
        booted device or send any IPC.

        Returns:
            ``True`` only when HID client creation is expected to succeed.
        """
        if not _load_frameworks():
            return False
        assert _objc is not None
        try:
            bridge = _ObjCBridge(_objc, cf=_cf)
            for name in _HID_CLIENT_CLASS_NAMES:
                try:
                    cls_ptr = bridge.cls(name)
                except RuntimeError:
                    continue
                if cls_ptr and "." in name:
                    # Swift-namespaced → Xcode 16+ → initWithDevice:error: will SIGTRAP
                    logger.debug(
                        "_can_create_hid_client: detected Xcode 16+ class %r — "
                        "initWithDevice:error: is blocked outside Simulator.app",
                        name,
                    )
                    return False
            # Unqualified class name (Xcode ≤ 15) — initWithDevice:error: is safe
            return True
        except Exception as exc:
            logger.debug("_can_create_hid_client probe failed: %s", exc)
            return False

    @classmethod
    def is_available(cls) -> bool:
        """Return ``True`` only when IndigoHID injection will actually work.

        Loads private frameworks *and* verifies that
        ``SimDeviceLegacyHIDClient.initWithDevice:error:`` can be called on
        this machine.  On Xcode 16+, the Swift-namespaced class raises a
        ``preconditionFailure`` (SIGTRAP) when instantiated outside
        Simulator.app, so this method returns ``False`` in that environment.

        Previously this method returned ``True`` as soon as frameworks loaded,
        which caused :class:`BackendSelector` to pick IndigoHID on Xcode 16+
        machines and then crash on first use.  This fix was added in
        INIT-2026-493 (auto-closeout pipeline).

        Returns:
            bool: ``True`` only when HID client creation is expected to succeed.
        """
        return cls._can_create_hid_client()

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
            return  # already initialised

        if not _load_frameworks():
            raise RuntimeError(
                "IndigoHIDBackend requires Xcode to be installed. "
                "Install Xcode and run: sudo xcode-select --switch "
                "/Applications/Xcode.app/Contents/Developer"
            )

        assert _objc is not None
        self._bridge = _ObjCBridge(_objc, cf=_cf)

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
            raise RuntimeError(f"xcrun simctl io screenshot failed (rc={result.returncode}): {result.stderr.strip()}")

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
        return f"IndigoHIDBackend(udid={udid!r}, {w}x{h}pt @{sf}x, {status})"
