"""App preferences (NSUserDefaults) - read off disk, write into the app's own domain.

Why this module exists
----------------------
An agent with no preferences primitive reaches for
``xcrun simctl spawn <udid> defaults read <bundle> <key>``. During the
2026-08-10 chaos-QA campaign that improvisation manufactured a
confidently-wrong "major" defect - a settings toggle reported as never
persisting, which in fact persisted fine - and cost an agent roughly half its
budget before a second agent traced the symptom back to the tooling.

Measured on a booted iOS 26 simulator, the reason is sharper than a stale
cache. A simulator holds **two different files for the same domain**::

    <device>/data/Containers/Data/Application/<uuid>/Library/Preferences/<bundle>.plist
    <device>/data/Library/Preferences/<bundle>.plist

The first is the app's sandboxed domain - the one ``NSUserDefaults.standard``
reads and writes. The second is device-wide, and it is where a ``defaults``
process spawned *outside* the sandbox by ``simctl spawn`` reads and writes.
They do not sync: a key the app wrote is invisible to
``simctl spawn defaults read <bundle>``, and a key that command wrote is
invisible to the app. An agent that sets a toggle that way and reads it back
sees its own value echoed out of a file the app has never opened.

So both directions here target the app's own container explicitly:

* **Read** parses ``Library/Preferences/<bundle>.plist`` from the data
  container. The file is what survived; nothing can be stale about it. When a
  requested key is absent there but *present* in the device-wide domain, that
  is reported as ``device_domain_only`` - the trap, named.
* **Write** invokes ``defaults`` inside the simulator against the container
  plist as a *path domain*, so the simulator's cfprefsd - the same daemon a
  live app reads through - mediates the write into the correct file. The value
  is then verified by re-reading the file rather than by trusting an exit code.
"""
from __future__ import annotations

import base64
import datetime
import plistlib
import time
from pathlib import Path
from typing import Any, Optional

from . import sim
from .sim import SimError

# A path-domain write is committed by the time `defaults` exits, but poll a
# couple of times anyway: verification that occasionally races is worse than
# useless, because an "unverified" verdict is exactly the false signal this
# module exists to eliminate.
_VERIFY_POLL_INTERVAL_SEC = 0.15
_VERIFY_POLL_ATTEMPTS = 3

_READ_NOTE = (
    "Read from the app's container plist on disk. Do NOT cross-check with "
    "`simctl spawn defaults read <bundle>` - that reads the device-wide domain, a "
    "different file the app never opens. A value a live app has set but not yet "
    "flushed may still be absent here; relaunch or background the app and re-read."
)


def prefs_plist_path(udid: str, bundle_id: str) -> Path:
    """Absolute path to the app's own preferences plist inside its data container."""
    container = sim.get_app_container(udid, bundle_id, "data")
    return container / "Library" / "Preferences" / f"{bundle_id}.plist"


def device_domain_plist_path(udid: str, bundle_id: str) -> Optional[Path]:
    """Path to the device-wide plist for the same domain, or None if underivable.

    This is the file `simctl spawn defaults` reads and writes. We look at it
    only to *diagnose* the confusion, never as a source of truth.
    """
    container = sim.get_app_container(udid, bundle_id, "data")
    for parent in container.parents:
        if parent.name == "Containers":
            return parent.parent / "Library" / "Preferences" / f"{bundle_id}.plist"
    return None


def _jsonable(value: Any) -> Any:
    """Coerce plist types into something the MCP JSON envelope can carry.

    Real app plists routinely hold NSDate and NSData (Palace stores its reader
    settings as a JSON blob in a Data value), and both are fatal to json.dumps.
    Dates become ISO-8601 strings; data becomes a tagged base64 object so the
    caller can tell a blob from a string that happens to look like base64.
    """
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, (int, float, str)):
        return value
    if isinstance(value, (bytes, bytearray)):
        return {
            "__type__": "data",
            "bytes": len(value),
            "base64": base64.b64encode(bytes(value)).decode("ascii"),
        }
    if isinstance(value, datetime.datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    return str(value)


def _load_plist(path: Path) -> tuple[dict, Optional[str]]:
    """Load a plist dict from disk. Returns (values, error_note)."""
    try:
        with path.open("rb") as fh:
            raw = plistlib.load(fh)
    except Exception as exc:  # noqa: BLE001 - a corrupt plist is a finding, not a crash
        return {}, (
            f"preferences plist at {path} is unreadable ({type(exc).__name__}: {exc}). "
            "It may be mid-write; retry, or inspect it with `plutil -p`."
        )
    if not isinstance(raw, dict):
        return {}, f"preferences plist at {path} is a {type(raw).__name__}, not a dictionary"
    return raw, None


def _device_domain_values(udid: str, bundle_id: str, keys: list[str]) -> dict:
    """Values for `keys` found in the device-wide domain (best effort, diagnostic only)."""
    try:
        path = device_domain_plist_path(udid, bundle_id)
    except SimError:
        return {}
    if path is None or not path.exists():
        return {}
    raw, err = _load_plist(path)
    if err:
        return {}
    return {k: _jsonable(raw[k]) for k in keys if k in raw}


def read_defaults(
    udid: str,
    bundle_id: str,
    keys: Optional[list[str]] = None,
) -> dict:
    """Read an app's preferences from disk.

    Returns ``{values, exists, plist_path, missing_keys, device_domain_only,
    note}``. A missing plist is data, not an error: the app may simply never
    have written a default. Only an app that is not installed raises (SimError,
    from get_app_container) - that is a question the caller asked wrong.
    """
    path = prefs_plist_path(udid, bundle_id)
    out: dict = {
        "values": {},
        "exists": path.exists(),
        "plist_path": str(path),
        "missing_keys": [],
        "device_domain_only": {},
        "note": _READ_NOTE,
    }

    if out["exists"]:
        raw, err = _load_plist(path)
        if err:
            out["note"] = err
            if keys:
                out["missing_keys"] = list(keys)
            return out
        if keys:
            out["values"] = {k: _jsonable(raw[k]) for k in keys if k in raw}
            out["missing_keys"] = [k for k in keys if k not in raw]
        else:
            out["values"] = {str(k): _jsonable(v) for k, v in raw.items()}
    else:
        out["note"] = (
            f"no preferences plist at {path} - the app has not written any defaults "
            "yet (or writes them to an app-group/suite domain instead of its own). "
            + _READ_NOTE
        )
        if keys:
            out["missing_keys"] = list(keys)

    # Name the trap: a key missing from the app's domain but sitting in the
    # device-wide one was almost certainly set by a `simctl spawn defaults
    # write`, and the app has never seen it.
    if out["missing_keys"]:
        shadow = _device_domain_values(udid, bundle_id, out["missing_keys"])
        if shadow:
            out["device_domain_only"] = shadow
            out["note"] += (
                f" WARNING: {sorted(shadow)} exist in the DEVICE-WIDE domain "
                f"({device_domain_plist_path(udid, bundle_id)}) but not in the app's own "
                "domain. That is where `simctl spawn defaults write` puts things; the app "
                "does not read it. Use set_app_defaults to write the app's domain instead."
            )
    return out


def _defaults_write_argv(domain: str, key: str, value: Any) -> list[str]:
    """Build the `defaults write` tail for one key/value pair.

    `domain` is the container plist path *without* the .plist extension, so
    `defaults` treats it as a path domain and writes the app's own file rather
    than the device-wide one.

    The type flag is not cosmetic: `defaults write dom key 1` stores the string
    "1", which reads back as a non-nil string and makes a `boolForKey:` check
    behave in ways nobody expects.
    """
    if isinstance(value, bool):
        return ["defaults", "write", domain, key, "-bool", "true" if value else "false"]
    if isinstance(value, int):
        return ["defaults", "write", domain, key, "-int", str(value)]
    if isinstance(value, float):
        return ["defaults", "write", domain, key, "-float", repr(value)]
    if isinstance(value, str):
        return ["defaults", "write", domain, key, "-string", value]
    if isinstance(value, (list, dict)):
        # defaults(1) parses its value argument as a property list, so an XML
        # plist literal covers arrays/dicts without hand-rolling old-style
        # plist syntax.
        literal = plistlib.dumps(value, fmt=plistlib.FMT_XML).decode("utf-8")
        return ["defaults", "write", domain, key, literal]
    raise SimError(
        f"cannot write preference {key!r}: unsupported value type "
        f"{type(value).__name__}. Supported: bool, int, float, str, list, dict."
    )


def _matches(disk_value: Any, requested: Any) -> bool:
    """Compare a requested write against the JSON-coerced value read from disk."""
    return disk_value == _jsonable(requested)


def write_defaults(udid: str, bundle_id: str, values: dict) -> dict:
    """Write preferences into the app's own domain and verify them against disk.

    Returns ``{written, verified, unverified, all_verified, plist_path, note}``.
    ``verified`` is the subset actually observed on disk afterwards - a key in
    ``unverified`` was written without proof, and the caller should treat it as
    such rather than as a success.
    """
    if not values:
        raise SimError("set_app_defaults requires at least one key/value pair")

    plist_path = prefs_plist_path(udid, bundle_id)
    # `defaults` wants the path domain without the trailing .plist.
    domain = str(plist_path)[: -len(".plist")]

    # Validate every value before issuing any write, so a bad third key does
    # not leave the first two half-applied.
    argvs = [_defaults_write_argv(domain, str(k), v) for k, v in values.items()]

    for argv in argvs:
        res = sim._simctl("spawn", udid, *argv, timeout=15.0)
        if res.returncode != 0:
            raise SimError(
                f"`defaults write` failed for {bundle_id}: "
                f"{(res.stderr or res.stdout).strip()[:300]}"
            )

    keys = [str(k) for k in values]

    def _verify() -> dict:
        disk = read_defaults(udid, bundle_id, keys=keys)["values"]
        return {k: disk[k] for k in keys if k in disk and _matches(disk[k], values[k])}

    verified = _verify()
    for _ in range(_VERIFY_POLL_ATTEMPTS - 1):
        if len(verified) == len(keys):
            break
        time.sleep(_VERIFY_POLL_INTERVAL_SEC)
        verified = _verify()

    unverified = [k for k in keys if k not in verified]
    note = (
        "Written with `defaults write` against the app's container plist as a path "
        "domain, so the change lands in the domain the app actually reads (a plain "
        "`defaults write <bundle>` would land in the device-wide domain instead), then "
        "verified by re-reading that plist from disk. Relaunch the app if it only reads "
        "the value at startup."
    )
    if unverified:
        note += (
            f" {len(unverified)} key(s) could not be confirmed on disk: {unverified}. "
            "Treat those as NOT written."
        )
    return {
        "written": keys,
        "verified": verified,
        "unverified": unverified,
        "all_verified": not unverified,
        "plist_path": str(plist_path),
        "note": note,
    }
