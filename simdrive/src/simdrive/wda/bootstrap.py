"""WDA bootstrap: clone, build, install, and smoke-test WebDriverAgent on a real device.

Implements `simdrive bootstrap-device <udid>` — the full sequence from a clean
Mac to "WDA ready" on the target device. Each step is a distinct function so
tests can patch at the subprocess boundary without needing a real device.

Algorithm overview:
  1. Verify host tools (xcodebuild, idevicepair, xcrun devicectl)
  2. Verify device paired + Developer Mode + DDI services
  3. Clone WDA at pinned SHA into ~/.simdrive/wda/<udid>/source/
  4. Resolve signing identity from keychain or explicit flags
  5. xcodebuild build-for-testing (streams stdout)
  6. Launch WDA via xcodebuild test-without-building (correct XCTest mechanism)
  7. Tail xcodebuild stdout for ServerURLHere port announcement (60 s)
  8. Persist ~/.simdrive/wda/<udid>.json registry (with both ip and port)
  9. Smoke GET /status → {value: {ready: true}}
 10. Print "WDA ready" summary with any manual Trust prompts

Bug fixes:
  Bug 1 — resolve_signing_identity now filters by team_id before raising ambiguity.
  Bug 2 — hardware UDID resolved via devicectl; coredevice UUID used only for devicectl cmds.
  Bug 3 — CODE_SIGN_IDENTITY="Apple Development" + CODE_SIGN_STYLE=Automatic + -allowProvisioningUpdates.
  Bug 4 — OTHER_CFLAGS="-Wno-reserved-identifier" prevents clang -Wreserved-identifier errors.
  Bug 5+6 — WDA launched via xcodebuild test-without-building (not devicectl device process launch).
             Port + IP captured from xcodebuild stdout (WDA announces WiFi IP, not localhost).

All subprocess.run calls are direct (not wrapped) so tests can patch via
unittest.mock.patch("simdrive.wda.bootstrap.subprocess.run").
"""
from __future__ import annotations

import glob
import json
import logging
import os
import re
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Optional

import httpx

_LOG = logging.getLogger("simdrive.wda.bootstrap")

from .. import errors as _errors_module
from . import registry
from .errors import (
    wda_build_failed,
    wda_device_locked,
    wda_device_not_ready,
    wda_host_tools_missing,
    wda_install_failed,
    wda_no_signing_identity,
    wda_not_bootstrapped,
    wda_port_discovery_timeout,
    wda_signing_ambiguous,
    wda_smoke_failed,
    wda_ui_automation_disabled,
    wda_xcode_account_not_authenticated,
)


# ─── constants ───────────────────────────────────────────────────────────────

_PINNED_SHA_FILE = Path(__file__).parent / "PINNED_SHA.txt"

# How long to tail xcodebuild stdout waiting for the ServerURLHere announcement.
_PORT_DISCOVERY_TIMEOUT_S = 60

# WDA default port (user may override via --wda-port).
_WDA_DEFAULT_PORT = 8100

# Pattern emitted by WDA when it binds its HTTP listener (xcodebuild stdout).
# Captures both host/IP (group 1) and port (group 2).
# Example: ServerURLHere->http://192.168.1.26:8100<-ServerURLHere
_SERVER_URL_RE = re.compile(r"ServerURLHere->http://([^:]+):(\d+)<-")

# Pattern emitted by xcodebuild when the target device is locked.
# Example: Error Domain=com.apple.dt.deviceprep Code=-3 "Unlock Moes Max to Continue"
# Example: Xcode cannot launch WebDriverAgentRunner on <device> because the device is locked.
_LOCKED_DEVICE_RE = re.compile(r"Unlock .+ to Continue|device is locked", re.IGNORECASE)

# WDA bundle identifier (Appium fork default, matches xcodebuild scheme).
# Note: this is the fallback — auto-bootstrap rewrites it to a per-team ID to
# dodge Apple's reservation of the com.facebook namespace.
_WDA_BUNDLE_ID = "com.facebook.WebDriverAgentRunner.xctrunner"

# Regex that matches the WDA bundle ID assignment in project.pbxproj.
# Matches the Facebook default AND any previously-patched co.synctek value.
# Only targets PRODUCT_BUNDLE_IDENTIFIER lines so unrelated occurrences are safe.
_PBXPROJ_BUNDLE_RE = re.compile(
    r"(PRODUCT_BUNDLE_IDENTIFIER\s*=\s*)"
    r"(?:com\.facebook\.WebDriverAgentRunner[^\s;]*|co\.synctek\.simdrive\.wda\.[^\s;]*)"
    r"(;)"
)


# ─── daemon paths ────────────────────────────────────────────────────────────


def _wda_home() -> Path:
    """Return the per-UDID WDA state directory (override via WDA_REGISTRY_DIR)."""
    return Path(os.environ.get("WDA_REGISTRY_DIR", Path.home() / ".simdrive" / "wda"))


def _log_path(udid: str) -> Path:
    """Path of the per-UDID xcodebuild stdout/stderr log."""
    return _wda_home() / f"{udid}.log"


def _pid_path(udid: str) -> Path:
    """Path of the per-UDID WDA daemon pidfile."""
    return _wda_home() / f"{udid}.pid"


# ─── team auto-detection ─────────────────────────────────────────────────────


def auto_detect_team_id() -> Optional[str]:
    """Detect the Apple Developer Team ID from the local keychain or Xcode prefs.

    Resolution order:
      a. ``security find-identity -p codesigning -v`` — parse team IDs from
         "Apple Development: <name> (<TEAM>)" lines. If exactly one unique team
         appears across all valid identities, return it. If multiple unique
         teams, return None (caller must ask the user to pass --team-id
         explicitly).
      b. Fallback: ``defaults read com.apple.dt.Xcode
         DVTDeveloperAccountManagerAppleIDLists`` — extract ``teamID = "..."``
         entries (older Xcode). Same single-team rule.

    Returns the 10-character team ID string, or None if detection fails or
    the result is ambiguous.
    """
    # ── approach (a): keychain identities ──────────────────────────────────
    result = subprocess.run(
        ["security", "find-identity", "-p", "codesigning", "-v"],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    if result.returncode == 0 and result.stdout:
        identities = _parse_identities(result.stdout)
        teams = {i["team_id"] for i in identities if i["team_id"]}
        if len(teams) == 1:
            return next(iter(teams))
        if len(teams) > 1:
            # Multiple teams — caller must disambiguate.
            return None

    # ── approach (b): Xcode preferences plist ──────────────────────────────
    prefs = subprocess.run(
        ["defaults", "read", "com.apple.dt.Xcode",
         "DVTDeveloperAccountManagerAppleIDLists"],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    if prefs.returncode == 0 and prefs.stdout:
        # Match `teamID = "ABCD123456";` or `DVTDeveloperAccountTeamID = "..."`.
        found = set(re.findall(
            r'(?:teamID|DVTDeveloperAccountTeamID)\s*=\s*"([A-Z0-9]{10})"',
            prefs.stdout,
        ))
        if len(found) == 1:
            return next(iter(found))

    return None


# ─── host-tool verification ───────────────────────────────────────────────────


def verify_host_tools() -> None:
    """Raise wda_host_tools_missing for each absent required tool.

    Checks: xcodebuild, idevicepair, xcrun (devicectl is a subcommand of xcrun).
    """
    required = ["xcodebuild", "idevicepair", "xcrun"]
    for tool in required:
        if shutil.which(tool) is None:
            raise wda_host_tools_missing(tool)


# ─── device state verification ───────────────────────────────────────────────


def verify_device_ready(udid: str) -> None:
    """Parse `xcrun devicectl device info details --json-output -` and assert pairing + DDI state.

    Uses the structured JSON output (--json-output -) so parsing is reliable
    across macOS / Xcode versions regardless of bullet-point formatting changes.

    JSON paths (confirmed against iPhone 17 Pro Max, iOS 26.3.1):
      result.connectionProperties.pairingState         -> "paired"
      result.connectionProperties.tunnelState          -> "connected"
      result.deviceProperties.developerModeStatus      -> "enabled"
      result.deviceProperties.ddiServicesAvailable     -> true (bool)

    Raises wda_device_not_ready with the list of unmet conditions.
    """
    result = subprocess.run(
        ["xcrun", "devicectl", "device", "info", "details", "--device", udid, "--json-output", "-"],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    # A non-zero exit almost always means the device is not visible at all.
    if result.returncode != 0:
        raise wda_device_not_ready(udid, ["device_not_found_or_not_connected"])

    missing: list[str] = []
    try:
        data = json.loads(result.stdout)
        # devicectl JSON envelope: {"result": {...}} — fields are directly on result,
        # not inside a result.devices[] array.
        info = data.get("result") or {}

        conn_props = info.get("connectionProperties") or {}
        dev_props = info.get("deviceProperties") or {}

        pairing = conn_props.get("pairingState", "")
        if pairing.lower() != "paired":
            missing.append(f"pairingState={pairing!r} (need 'paired')")

        dev_mode = dev_props.get("developerModeStatus", "")
        if dev_mode.lower() != "enabled":
            missing.append(f"developerModeStatus={dev_mode!r} (need 'enabled')")

        ddi = dev_props.get("ddiServicesAvailable", False)
        if not ddi:
            missing.append("ddiServicesAvailable=False (mount DDI by connecting device in Xcode)")

    except (json.JSONDecodeError, IndexError, KeyError):
        # Can't parse — treat as unverified rather than crashing; surface a
        # parseable condition so the user can debug.
        missing.append("devicectl_output_unparseable")

    if missing:
        raise wda_device_not_ready(udid, missing)


# ─── Hardware UDID resolution (Bug 2) ────────────────────────────────────────


def resolve_hardware_udid(coredevice_uuid: str) -> str:
    """Resolve the hardware UDID from a CoreDevice pairing UUID.

    On iOS 17+ / Xcode 16+, `xcrun devicectl` commands accept the CoreDevice
    pairing UUID, but `xcodebuild -destination id=...` requires the hardware
    UDID. These are different identifiers for the same physical device.

    Parses `xcrun devicectl device info details --device <uuid> --json-output -`
    and extracts result.hardwareProperties.udid.

    Returns the hardware UDID string. Falls back to coredevice_uuid if the JSON
    field cannot be read (e.g. older Xcode — where the UDIDs may be the same).
    """
    result = subprocess.run(
        ["xcrun", "devicectl", "device", "info", "details",
         "--device", coredevice_uuid, "--json-output", "-"],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    if result.returncode != 0:
        # Can't resolve — fall back to the supplied UUID and let xcodebuild
        # handle any mismatch.
        print(
            f"[simdrive] Warning: could not resolve hardware UDID via devicectl "
            f"(exit {result.returncode}); using coredevice UUID for xcodebuild.",
            flush=True,
        )
        return coredevice_uuid

    try:
        data = json.loads(result.stdout)
        hw_udid = data["result"]["hardwareProperties"]["udid"]
        if hw_udid:
            print(f"[simdrive] Hardware UDID: {hw_udid}", flush=True)
            return hw_udid
    except (json.JSONDecodeError, KeyError, TypeError):
        pass

    print(
        "[simdrive] Warning: hardwareProperties.udid not found in devicectl output; "
        "using coredevice UUID for xcodebuild.",
        flush=True,
    )
    return coredevice_uuid


# ─── WDA clone ───────────────────────────────────────────────────────────────


def _parse_pinned_sha() -> tuple[str, str]:
    """Return (repo_url, sha) from PINNED_SHA.txt."""
    repo = ""
    sha = ""
    for line in _PINNED_SHA_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line.startswith("REPO="):
            repo = line.split("=", 1)[1].strip()
        elif line.startswith("SHA="):
            sha = line.split("=", 1)[1].strip()
    if not repo or not sha:
        raise RuntimeError(f"PINNED_SHA.txt is malformed — expected REPO= and SHA= lines in {_PINNED_SHA_FILE}")
    return repo, sha


def clone_wda(udid: str, rebuild: bool = False) -> Path:
    """Clone WDA at the pinned SHA into ~/.simdrive/wda/<udid>/source/.

    Returns the source directory path. If the directory already exists and
    rebuild=False, skips the clone and returns immediately.
    """
    wda_home = Path(os.environ.get("WDA_REGISTRY_DIR", Path.home() / ".simdrive" / "wda"))
    source_dir = wda_home / udid / "source"

    if source_dir.exists() and not rebuild:
        print(f"[simdrive] WDA source already present at {source_dir} (skip --rebuild to reuse)", flush=True)
        return source_dir

    if source_dir.exists() and rebuild:
        shutil.rmtree(source_dir)

    source_dir.parent.mkdir(parents=True, exist_ok=True)
    repo_url, sha = _parse_pinned_sha()

    print(f"[simdrive] Cloning WebDriverAgent {sha[:12]} from {repo_url} ...", flush=True)
    result = subprocess.run(
        ["git", "clone", repo_url, str(source_dir)],
        capture_output=False,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise wda_build_failed(str(source_dir / "clone.log"))

    # Check out the exact pinned SHA so we're not on an unknown HEAD.
    checkout = subprocess.run(
        ["git", "-C", str(source_dir), "checkout", sha],
        capture_output=True,
        text=True,
        check=False,
    )
    if checkout.returncode != 0:
        # SHA not found can mean the repo was cloned but SHA is wrong;
        # still try to continue with whatever HEAD is.
        print(f"[simdrive] Warning: could not checkout SHA {sha}: {checkout.stderr.strip()}", flush=True)

    # Bootstrap CocoaPods if needed (WDA uses Pods for its test runner deps).
    if (source_dir / "Podfile").exists():
        print("[simdrive] Running `bundle exec pod install` (WDA CocoaPods deps) ...", flush=True)
        pod_result = subprocess.run(
            ["bundle", "exec", "pod", "install", "--project-directory", str(source_dir)],
            capture_output=False,
            text=True,
            check=False,
            cwd=str(source_dir),
        )
        if pod_result.returncode != 0:
            # pod install failure is non-fatal — xcodebuild may still succeed if
            # a Pods/ directory already exists from a previous run.
            print("[simdrive] Warning: pod install returned non-zero; continuing anyway.", flush=True)

    return source_dir


# ─── signing identity resolution ─────────────────────────────────────────────

# Pattern in `security find-identity -v -p codesigning` output:
#   1) ABCDEF1234... "Apple Development: Name (TEAMID)"
_IDENTITY_RE = re.compile(r'\d+\)\s+([A-F0-9]{40})\s+"([^"]+)"')
_TEAM_ID_RE = re.compile(r'\(([A-Z0-9]{10})\)')


def _parse_identities(output: str) -> list[dict]:
    """Parse `security find-identity -v -p codesigning` stdout.

    Returns list of {sha1, name, team_id} dicts. Skips expired identities
    (security marks them with CSSMERR_TP_CERT_EXPIRED in the output).
    """
    result = []
    for line in output.splitlines():
        # Skip expired / revoked entries
        if "CSSMERR" in line or "REVOKED" in line:
            continue
        m = _IDENTITY_RE.search(line)
        if m:
            sha1, name = m.group(1), m.group(2)
            tm = _TEAM_ID_RE.search(name)
            team_id = tm.group(1) if tm else ""
            result.append({"sha1": sha1, "name": name, "team_id": team_id})
    return result


def _cert_not_before(name: str) -> Optional[str]:
    """Return the cert's `notBefore` date as a sortable string, or None.

    Shells out to ``security find-certificate -c <name> -p`` (PEM) → openssl
    ``x509 -noout -startdate`` (e.g. ``notBefore=Apr  9 12:34:56 2026 GMT``).
    The raw string is returned and we sort lexicographically with a small
    parse fallback — month names sort wrong as plain text, so the parse
    fallback below converts to ISO-8601 when openssl's output is recognised.
    """
    pem = subprocess.run(
        ["security", "find-certificate", "-c", name, "-p"],
        capture_output=True, text=True, timeout=10, check=False,
    )
    if pem.returncode != 0 or not pem.stdout:
        return None
    info = subprocess.run(
        ["openssl", "x509", "-noout", "-startdate"],
        input=pem.stdout, capture_output=True, text=True, timeout=10, check=False,
    )
    if info.returncode != 0 or not info.stdout:
        return None
    line = info.stdout.strip()
    # Expect: "notBefore=Apr  9 12:34:56 2026 GMT"
    prefix = "notBefore="
    if not line.startswith(prefix):
        return None
    raw = line[len(prefix):].strip()
    try:
        from datetime import datetime
        # openssl emits with double-space day padding for single-digit days.
        dt = datetime.strptime(raw, "%b %d %H:%M:%S %Y %Z")
        return dt.strftime("%Y-%m-%dT%H:%M:%S")
    except ValueError:
        return raw  # opaque sort key — better than dropping the entry


def _pick_newest_identity(identities: list[dict]) -> dict:
    """Return the most-recently-issued identity from ``identities``.

    Queries each cert's notBefore date via ``security find-certificate`` +
    ``openssl x509 -noout -startdate``. Identities whose date can't be
    resolved sort to the bottom; if no dates are resolvable we return the
    first entry (preserves previous deterministic behaviour).
    """
    dated: list[tuple[str, dict]] = []
    for ident in identities:
        d = _cert_not_before(ident["name"])
        if d is not None:
            dated.append((d, ident))
    if not dated:
        return identities[0]
    dated.sort(key=lambda pair: pair[0], reverse=True)
    return dated[0][1]


def resolve_signing_identity(
    signing_identity: Optional[str] = None,
    team_id: Optional[str] = None,
) -> tuple[Optional[str], Optional[str]]:
    """Return (signing_identity, team_id) to use for xcodebuild.

    Resolution order:
      1. If signing_identity is supplied directly, use it + extract team_id.
      2. If team_id is supplied:
         a. Filter keychain identities to those matching team_id.
         b. Exactly one match → return it + team_id.
         c. Multiple matches → pick the most-recently-issued cert (B2). All
            matches share team_id and are therefore equivalent for codesigning
            purposes; picking the newest avoids spurious ambiguity errors for
            users who accumulate certs across machine refreshes.
         d. Zero matches → return (None, team_id). This is the Apple Personal
            Team case: a free Apple ID team has no cert in the keychain yet, but
            xcodebuild's -allowProvisioningUpdates will download one on demand
            when the Xcode Account is signed in for that team.
      3. No team_id, no signing_identity → exactly one keychain cert: use it.
      4. Multiple certs, no team_id → raise wda_signing_ambiguous.

    Returns:
      (signing_identity_string_or_None, team_id_string_or_None)
      When signing_identity_string is None, the caller passes the generic
      CODE_SIGN_IDENTITY="Apple Development" and lets xcodebuild fetch a cert
      via -allowProvisioningUpdates.

    Bug 1 fix: when multiple certs exist and team_id is supplied, filter by
    team_id before raising ambiguity. This handles the common case of having
    two "Apple Development" certs (e.g. one per machine) with different team IDs.

    Personal Team fix: when team_id is supplied but no cert matches (e.g.
    B3HE38966G — a free Apple ID personal team), return (None, team_id) instead
    of raising ambiguous. xcodebuild downloads the cert via -allowProvisioningUpdates.
    """
    result = subprocess.run(
        ["security", "find-identity", "-v", "-p", "codesigning"],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    identities = _parse_identities(result.stdout)

    # Branch 1: explicit signing_identity overrides everything.
    if signing_identity:
        if not team_id:
            tm = _TEAM_ID_RE.search(signing_identity)
            team_id = tm.group(1) if tm else ""
        return signing_identity, team_id

    # Branch 2: team_id supplied → filter keychain certs by team_id.
    if team_id:
        matching = [i for i in identities if i["team_id"] == team_id]
        if len(matching) == 1:
            return matching[0]["name"], team_id
        if len(matching) > 1:
            # B2: all matches share team_id, so they're equivalent for
            # codesigning purposes — picking the newest "Apple Development"
            # cert is safe (older ones may be revoked). Falling through to
            # ambiguity here would block bootstraps for users who routinely
            # accumulate certs (e.g. machine refreshes).
            picked = _pick_newest_identity(matching)
            return picked["name"], team_id
        # Zero matches: Apple Personal Team case (or new paid team with no local cert).
        # Return (None, team_id) — xcodebuild + -allowProvisioningUpdates will
        # download a cert on demand when an Xcode Account is signed in for this team.
        return None, team_id

    # Branch 3+4: no team_id, no signing_identity → fall back to keychain enumeration.
    if not identities:
        raise wda_no_signing_identity()

    if len(identities) == 1:
        identity = identities[0]
        return identity["name"], identity["team_id"] or None

    # Multiple identities — filter to Apple Development certs.
    apple_dev = [i for i in identities if "Apple Development" in i["name"]]

    if len(apple_dev) == 1:
        identity = apple_dev[0]
        return identity["name"], identity["team_id"] or None

    # Still ambiguous — raise with the full list.
    raise wda_signing_ambiguous([i["name"] for i in identities])


# ─── Xcode account verification ──────────────────────────────────────────────


def verify_xcode_account_for_team(team_id: str) -> None:
    """Verify Xcode has an Apple Account bound to ``team_id``.

    A signed-in account is necessary for ``xcodebuild -allowProvisioningUpdates``
    to download provisioning profiles from Apple's Developer Portal. The
    codesigning cert in the keychain is necessary but not sufficient — Xcode's
    account session is separate state, stored in com.apple.dt.Xcode preferences.

    Implementation (B1): we parse the
    ``DVTDeveloperAccountManagerAppleIDLists`` plist and look for ``team_id``
    explicitly inside it. The previous substring grep for ``"identifier"``
    passed even when the only signed-in account was bound to a different team
    (the literal token "identifier" appears in any non-empty entry). We now
    require the team id itself to appear in the plist; absent that, raise
    ``wda_xcode_account_not_authenticated``.
    """
    result = subprocess.run(
        ["defaults", "read", "com.apple.dt.Xcode", "DVTDeveloperAccountManagerAppleIDLists"],
        capture_output=True,
        text=True,
        check=False,
    )
    # defaults exits non-zero when the key doesn't exist (no account ever signed in)
    if result.returncode != 0:
        raise wda_xcode_account_not_authenticated(team_id)

    # ``defaults read`` emits old-style plist text. plistlib only accepts XML or
    # binary plists, so parse via a string scan that matches the actual team
    # binding. The plist serialises team membership as nested entries that
    # include lines like ``teamID = "ABC1234567";`` (paid teams) or
    # ``teamIDs = ( "ABC1234567" )`` (account list payload). Match either.
    stdout = result.stdout
    if not stdout.strip() or stdout.strip() in ("{\n}", "{}", "(\n)", "()"):
        raise wda_xcode_account_not_authenticated(team_id)

    if not _xcode_account_output_has_team(stdout, team_id):
        # B1+ relax (Xcode 16+ compatibility): DVTDeveloperAccountManagerAppleIDLists
        # no longer always contains team_id bindings — newer Xcode keeps team
        # membership in keychain / IDEPersistentSettings instead. If at least one
        # Apple ID account is signed in here, trust it and let xcodebuild fail
        # later with a meaningful error if the requested team isn't accessible.
        if not _xcode_account_output_has_any_account(stdout):
            raise wda_xcode_account_not_authenticated(team_id)
        print(
            f"[simdrive] Xcode account present but team {team_id} not visible in "
            "DVTDeveloperAccountManagerAppleIDLists; deferring final team check to "
            "xcodebuild -allowProvisioningUpdates.",
            flush=True,
        )


def _xcode_account_output_has_any_account(stdout: str) -> bool:
    """Return True if at least one Apple-ID account entry is signed in.

    Xcode 16+ stores each signed-in account as ``{ identifier = "<UUID>"; }``
    inside the plist with team membership cached elsewhere. The presence of
    such an entry is sufficient to know that ``xcodebuild -allowProvisioningUpdates``
    has a session it can use; the strict per-team check stays as the primary
    path for older Xcode versions where the team IDs are inline.
    """
    return bool(re.search(r'identifier\s*=\s*"[^"]+"', stdout))


def _xcode_account_output_has_team(stdout: str, team_id: str) -> bool:
    """Return True if ``team_id`` appears as a real team binding in ``stdout``.

    Looks for the team id as a quoted token associated with one of the team
    keys Xcode emits: ``teamID``, ``teamIDs``, ``DVTDeveloperAccountTeamID``,
    or as a quoted entry in a ``teamIDs = ( ... )`` array. A bare substring
    match would false-positive on UUIDs and identifier strings that happen to
    contain the same 10 chars; we require either the key/value pair form or
    the array-element form to be present.
    """
    if not team_id:
        return False
    # Form 1: `teamID = "ABCDEF1234";` or `DVTDeveloperAccountTeamID = "ABCDEF1234";`
    kv = re.compile(
        r'(?:teamID|teamIDs|DVTDeveloperAccountTeamID)\s*=\s*"' + re.escape(team_id) + r'"',
        re.IGNORECASE,
    )
    if kv.search(stdout):
        return True
    # Form 2: array element inside a `teamIDs = ( "X", "Y" )` block.
    array_block = re.search(r"teamIDs\s*=\s*\(([^)]*)\)", stdout, re.IGNORECASE | re.DOTALL)
    if array_block and re.search(r'"' + re.escape(team_id) + r'"', array_block.group(1)):
        return True
    return False


# ─── WDA bundle ID rewrite ───────────────────────────────────────────────────


def _wda_bundle_id_for_team(team_id: str) -> str:
    """Return the per-team WDA bundle ID used for provisioning.

    Format: ``co.synctek.simdrive.wda.<team_id_lower>``

    This scheme is globally unique (scoped under our domain), readable, and
    idempotent — running patch_wda_bundle_id twice yields the same result.
    Apple's com.facebook namespace is avoided so auto-provisioning succeeds.
    """
    return f"co.synctek.simdrive.wda.{team_id.lower()}"


def patch_wda_bundle_id(source_dir: Path, team_id: str) -> str:
    """Rewrite PRODUCT_BUNDLE_IDENTIFIER in WebDriverAgent.xcodeproj/project.pbxproj.

    Replaces the Facebook default (``com.facebook.WebDriverAgentRunner...``) and
    any previously-written ``co.synctek.simdrive.wda.*`` value with the per-team
    bundle ID. Only PRODUCT_BUNDLE_IDENTIFIER lines are touched; the scheme name
    and PRODUCT_NAME remain "WebDriverAgentRunner" so xcodebuild's scheme lookup
    is unaffected.

    Idempotent: running twice on the same source_dir produces the same file.

    Returns the new bundle ID string.
    """
    pbxproj = source_dir / "WebDriverAgent.xcodeproj" / "project.pbxproj"
    if not pbxproj.exists():
        _LOG.warning("project.pbxproj not found at %s — skipping bundle ID patch", pbxproj)
        return _wda_bundle_id_for_team(team_id)

    new_bundle_id = _wda_bundle_id_for_team(team_id)
    original = pbxproj.read_text(encoding="utf-8")
    patched = _PBXPROJ_BUNDLE_RE.sub(
        lambda m: f"{m.group(1)}{new_bundle_id}{m.group(2)}",
        original,
    )
    if patched != original:
        pbxproj.write_text(patched, encoding="utf-8")
        print(f"[simdrive] Patched WDA bundle ID → {new_bundle_id}", flush=True)
    else:
        print(f"[simdrive] WDA bundle ID already set to {new_bundle_id} (idempotent)", flush=True)
    return new_bundle_id


# ─── xcodebuild ──────────────────────────────────────────────────────────────


def build_wda(
    coredevice_uuid: str,
    source_dir: Path,
    team_id: str,
    hardware_udid: str,
) -> tuple[Path, str]:
    """Run xcodebuild build-for-testing for WebDriverAgentRunner.

    Streams stdout live (so the user can see progress). Returns
    ``(derived_data_path, bundle_id)``. Raises wda_build_failed with the log
    path on non-zero exit.

    Bug 3 fix: uses CODE_SIGN_IDENTITY="Apple Development" + CODE_SIGN_STYLE=Automatic
               instead of the full certificate string, and passes -allowProvisioningUpdates.
    Bug 4 fix: passes OTHER_CFLAGS="-Wno-reserved-identifier" to suppress clang
               -Wreserved-identifier errors in WDA v9.9.0 PrivateHeaders on Xcode 16.
    Bug 2 fix: uses hardware_udid for xcodebuild -destination (not coredevice UUID).
    a10: patches project.pbxproj bundle ID to a per-team value before building,
         dodging Apple's reservation of the com.facebook namespace.

    B4 (FAILED-before-SUCCEEDED retry): the very first build that touches a new
    team often emits a single `** BUILD FAILED **` line before xcodebuild
    fetches the provisioning profile via -allowProvisioningUpdates and
    immediately retries to a `** BUILD SUCCEEDED **`. The overall returncode is
    zero. Naive log scrapers panic on the FAILED token. When we detect that
    pattern we emit a single calming INFO line so users + downstream tooling
    don't misread the recoverable retry as a real failure.
    """
    wda_home = Path(os.environ.get("WDA_REGISTRY_DIR", Path.home() / ".simdrive" / "wda"))
    derived_data = wda_home / coredevice_uuid / "derived"
    log_path = wda_home / coredevice_uuid / "build.log"
    derived_data.mkdir(parents=True, exist_ok=True)

    # a10: rewrite bundle ID in xcodeproj before building so Apple auto-provisioning
    # works for any team (com.facebook is reserved under their team, not ours).
    bundle_id = patch_wda_bundle_id(source_dir, team_id)

    project = source_dir / "WebDriverAgent.xcodeproj"
    cmd = [
        "xcodebuild",
        "-project", str(project),
        "-scheme", "WebDriverAgentRunner",
        "-destination", f"id={hardware_udid}",
        "-derivedDataPath", str(derived_data),
        "build-for-testing",
        # Bug 3 fix: generic signing form — no full cert string
        "CODE_SIGN_IDENTITY=Apple Development",
        "CODE_SIGN_STYLE=Automatic",
        f"DEVELOPMENT_TEAM={team_id}",
        # Bug 4 fix: suppress -Wreserved-identifier in WDA v9.9.0 PrivateHeaders
        "OTHER_CFLAGS=-Wno-reserved-identifier",
        # Bug 3 fix: allow Xcode to update provisioning profiles automatically
        "-allowProvisioningUpdates",
    ]
    print("[simdrive] Building WebDriverAgentRunner ...", flush=True)
    print(f"[simdrive] xcodebuild command: {' '.join(cmd)}", flush=True)

    with log_path.open("w", encoding="utf-8") as log_file:
        proc = subprocess.run(
            cmd,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            text=True,
            check=False,
        )

    if proc.returncode != 0:
        raise wda_build_failed(str(log_path))

    # B4: the first invocation against a new team often logs `** BUILD FAILED **`
    # before -allowProvisioningUpdates fetches the profile and the retry hits
    # `** BUILD SUCCEEDED **`. Returncode is 0; the log just looks scary.
    try:
        _classify_build_log(log_path.read_text(encoding="utf-8", errors="replace"))
    except OSError:
        pass

    print(f"[simdrive] Build succeeded. Derived data: {derived_data}", flush=True)
    return derived_data, bundle_id


_BUILD_FAILED_RE = re.compile(r"\*\*\s*BUILD FAILED\s*\*\*", re.IGNORECASE)
_BUILD_SUCCEEDED_RE = re.compile(r"\*\*\s*BUILD SUCCEEDED\s*\*\*", re.IGNORECASE)


def _classify_build_log(log_text: str) -> None:
    """Distinguish the recoverable FAILED→SUCCEEDED retry from a real failure.

    Called only when xcodebuild's overall returncode is 0. If the log shows a
    BUILD FAILED line followed (later in the same stream) by a BUILD SUCCEEDED
    line, the whole sequence was the -allowProvisioningUpdates round-trip:
    log the FAILED tokens at DEBUG and emit a single INFO line so naive log
    scrapers don't misread the retry as a fatal error.
    """
    failed = _BUILD_FAILED_RE.search(log_text)
    if not failed:
        return
    succeeded = _BUILD_SUCCEEDED_RE.search(log_text, pos=failed.end())
    if not succeeded:
        return
    _LOG.debug("xcodebuild emitted BUILD FAILED before BUILD SUCCEEDED (recoverable retry)")
    _LOG.info(
        "First attempt failed pending provisioning fetch; retry succeeded after "
        "-allowProvisioningUpdates round-trip (expected)."
    )


# ─── install ─────────────────────────────────────────────────────────────────


def _find_wda_app_bundle(derived_data: Path) -> Optional[Path]:
    """Find WebDriverAgentRunner.app inside the derived data directory."""
    # xcodebuild places the .app here:
    # <derived>/Build/Products/Debug-iphoneos/WebDriverAgentRunner-Runner.app
    # Appium WDA uses: WebDriverAgentRunner-Runner.app or similar
    patterns = [
        "Build/Products/*-iphoneos/WebDriverAgentRunner*.app",
        "Build/Products/*-iphoneos/*.xctrunner",
    ]
    for pat in patterns:
        matches = glob.glob(str(derived_data / pat))
        if matches:
            return Path(matches[0])
    return None


def _find_xctestrun(derived_data: Path) -> Optional[Path]:
    """Find the .xctestrun file produced by xcodebuild build-for-testing.

    xcodebuild writes:
      <derived>/Build/Products/WebDriverAgentRunner_iphoneos*.xctestrun
    """
    matches = glob.glob(str(derived_data / "Build/Products/WebDriverAgentRunner_iphoneos*.xctestrun"))
    if matches:
        return Path(matches[0])
    # Fallback: any .xctestrun in Build/Products
    matches = glob.glob(str(derived_data / "Build/Products/*.xctestrun"))
    if matches:
        return Path(matches[0])
    return None


def install_wda(
    coredevice_uuid: str,
    derived_data: Path,
    bundle_id: str = _WDA_BUNDLE_ID,
) -> str:
    """Install the WDA app bundle via xcrun devicectl.

    bundle_id: the per-team bundle identifier written into project.pbxproj
               by patch_wda_bundle_id(). Defaults to the Appium Facebook value
               for backwards compatibility when called outside bootstrap_device.

    Returns bundle_id (the identifier of the installed app).
    Raises wda_install_failed on non-zero exit.

    Uses coredevice_uuid (not hardware UDID) for devicectl commands.
    """
    app_bundle = _find_wda_app_bundle(derived_data)
    if app_bundle is None:
        raise wda_install_failed(
            f"Could not find WebDriverAgentRunner.app in {derived_data}. "
            "Run with --rebuild to trigger a fresh build."
        )

    # Uninstall both the per-team bundle ID AND the legacy Facebook ID to avoid
    # signing/team conflicts (devices previously bootstrapped with the old default).
    for old_bid in {bundle_id, _WDA_BUNDLE_ID}:
        print(
            f"[simdrive] Uninstalling old WDA ({old_bid}) from device {coredevice_uuid} if present ...",
            flush=True,
        )
        subprocess.run(
            ["xcrun", "devicectl", "device", "uninstall", "app",
             "--device", coredevice_uuid, "--bundle-id", old_bid],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,  # Non-zero is OK — WDA may not be installed yet
        )

    print(f"[simdrive] Installing {app_bundle.name} on device {coredevice_uuid} ...", flush=True)
    result = subprocess.run(
        ["xcrun", "devicectl", "device", "install", "app",
         "--device", coredevice_uuid, str(app_bundle)],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    if result.returncode != 0:
        raise wda_install_failed(result.stderr or result.stdout)

    print("[simdrive] Install succeeded.", flush=True)
    return bundle_id


# ─── launch + port discovery (Bug 5+6) ───────────────────────────────────────


def launch_and_discover_port(
    coredevice_uuid: str,
    derived_data: Path,
    hardware_udid: str,
    bundle_id: str = _WDA_BUNDLE_ID,
    wda_port: int = _WDA_DEFAULT_PORT,
) -> tuple[str, int]:
    """Launch WDA via xcodebuild test-without-building and discover host+port.

    Bug 5+6 fix:
    - devicectl device console does not exist in Xcode 16.
    - devicectl device process launch crashes WDA (it's an XCTest bundle).
    - Correct mechanism: xcodebuild test-without-building -xctestrun <path>
    - WDA announces "ServerURLHere->http://<ip>:<port><-" to xcodebuild stdout.
    - The IP is the device's WiFi IP (NOT localhost) — captured from the announcement.

    Returns (host, port) where host is the device's WiFi IP.
    Raises wda_port_discovery_timeout if WDA doesn't announce within _PORT_DISCOVERY_TIMEOUT_S.
    """
    xctestrun = _find_xctestrun(derived_data)
    if xctestrun is None:
        raise wda_port_discovery_timeout(
            coredevice_uuid,
        )

    print(f"[simdrive] Using xctestrun: {xctestrun}", flush=True)
    print(
        f"[simdrive] Launching WDA via xcodebuild test-without-building "
        f"(target hardware UDID: {hardware_udid}) ...",
        flush=True,
    )

    cmd = [
        "xcodebuild", "test-without-building",
        "-xctestrun", str(xctestrun),
        "-destination", f"id={hardware_udid}",
    ]

    log_file = _log_path(coredevice_uuid)
    pid_file = _pid_path(coredevice_uuid)
    log_file.parent.mkdir(parents=True, exist_ok=True)
    log_fh = log_file.open("w", encoding="utf-8")

    # start_new_session detaches xcodebuild from this CLI's process group so it
    # survives bootstrap-device exiting (otherwise SIGHUP cascade kills WDA — B3).
    proc = subprocess.Popen(
        cmd,
        stdout=log_fh,
        stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL,
        text=True,
        start_new_session=True,
    )
    pid_file.write_text(str(proc.pid), encoding="utf-8")

    host: Optional[str] = None
    port: Optional[int] = None
    device_locked: bool = False
    deadline = time.monotonic() + _PORT_DISCOVERY_TIMEOUT_S

    def _tail_log() -> None:
        nonlocal host, port, device_locked
        with log_file.open("r", encoding="utf-8") as fh:
            while time.monotonic() < deadline:
                line = fh.readline()
                if not line:
                    if proc.poll() is not None:
                        return
                    time.sleep(0.1)
                    continue
                m = _SERVER_URL_RE.search(line)
                if m:
                    host = m.group(1)
                    port = int(m.group(2))
                    return
                if _LOCKED_DEVICE_RE.search(line):
                    device_locked = True
                    return

    t = threading.Thread(target=_tail_log, daemon=True)
    t.start()
    t.join(timeout=_PORT_DISCOVERY_TIMEOUT_S + 2.0)

    if device_locked or host is None or port is None:
        # Kill the xcodebuild process before raising.
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
        log_fh.close()
        try:
            pid_file.unlink()
        except FileNotFoundError:
            pass
        if device_locked:
            raise wda_device_locked(coredevice_uuid)
        raise wda_port_discovery_timeout(coredevice_uuid)

    # xcodebuild keeps running in its own session; WDA stays alive after this
    # process exits. Teardown via `simdrive wda-down <udid>`.
    print(f"[simdrive] WDA listening on http://{host}:{port}", flush=True)
    print(f"[simdrive] WDA log:  {log_file}", flush=True)
    print(f"[simdrive] WDA pid:  {pid_file} ({proc.pid})", flush=True)
    return host, port


# ─── reachable-host resolution (iOS 17+ RemoteServiceTunnel) ─────────────────
#
# WDA announces the first IP it finds on-device via "ServerURLHere->http://<ip>:<port>",
# falling back to `localhost` when the device has no routable Wi-Fi IP on the
# host's network. But `localhost` on the *Mac* does not reach the *device*, so
# the /status smoke (and every later tool call) fails with "Connection refused".
# On iOS 17+ a USB-attached device is reachable from the host only via the
# CoreDevice RemoteServiceTunnel, whose address devicectl reports as
# connectionProperties.tunnelIPAddress (an IPv6 ULA, e.g. fd35:ed24:fc2::1).
# WDA binds 0.0.0.0:<port> on the device, so the tunnel address reaches it even
# when the announced host is non-routable. We probe candidates and use the first
# that actually answers.


def _resolve_tunnel_ip(coredevice_uuid: str) -> Optional[str]:
    """Return the device's CoreDevice tunnel IP (IPv6 ULA), or None."""
    try:
        result = subprocess.run(
            ["xcrun", "devicectl", "device", "info", "details",
             "--device", coredevice_uuid, "--json-output", "-"],
            capture_output=True, text=True, timeout=30, check=False,
        )
        if result.returncode != 0:
            return None
        data = json.loads(result.stdout)
        conn = (data.get("result") or {}).get("connectionProperties") or {}
        return (conn.get("tunnelIPAddress") or "").strip() or None
    except Exception:
        return None


def _fmt_host(host: str) -> str:
    """Bracket a bare IPv6 literal so it is valid in a URL authority."""
    if host and ":" in host and not host.startswith("["):
        return f"[{host}]"
    return host


def _probe_status(host: str, port: int, timeout: float = 4.0) -> bool:
    """Lightweight GET /status reachability probe — True iff WDA answers ready."""
    try:
        resp = httpx.get(f"http://{host}:{port}/status", timeout=timeout)
        return resp.is_success and bool((resp.json().get("value") or {}).get("ready"))
    except Exception:
        return False


def choose_reachable_host(announced_host: str, port: int, coredevice_uuid: str) -> str:
    """Return a host that actually reaches WDA from this Mac.

    Probes, in priority order: the CoreDevice tunnel IP (reliable for USB on
    iOS 17+), the host WDA announced (works when device + Mac share a network),
    then localhost. Returns the first that answers /status. If none answer,
    returns the best candidate (tunnel IP when available) so the subsequent
    smoke_test raises a clear error naming the address actually tried.
    """
    candidates: list[str] = []
    tunnel_ip = _resolve_tunnel_ip(coredevice_uuid)
    if tunnel_ip:
        candidates.append(_fmt_host(tunnel_ip))
    for h in (announced_host, "localhost"):
        fh = _fmt_host(h)
        if fh and fh not in candidates:
            candidates.append(fh)

    for h in candidates:
        if _probe_status(h, port):
            if h != _fmt_host(announced_host):
                print(
                    f"[simdrive] WDA announced {announced_host!r} (not reachable from host); "
                    f"using {h} via CoreDevice tunnel instead.",
                    flush=True,
                )
            return h

    return candidates[0] if candidates else _fmt_host(announced_host)


# ─── smoke test ──────────────────────────────────────────────────────────────


def smoke_test(host: str, port: int) -> dict:
    """GET /status then POST /session probe to validate WDA is fully operational.

    Step 1 — GET /status: asserts {value: {ready: true}}. Raises wda_smoke_failed
    on mismatch or HTTP error.

    Step 2 — POST /session probe (F-004): attempts to create a transient session
    using com.apple.Preferences as the target (always installed). If the response
    contains XCTDaemonErrorDomain Code=41, UI Automation is disabled on the device
    and we raise wda_ui_automation_disabled immediately with a clear remedy.
    Any session created is immediately deleted (DELETE /session/<id>) so no session
    leaks from bootstrap. If POST /session fails for non-41 reasons (network, 5xx),
    we log a warning and let bootstrap succeed — /status already confirmed WDA is
    reachable; future tool calls will surface the real error.

    Returns the parsed /status body so callers can extract OS / device info (D8).
    host is the device's WiFi IP (captured from WDA's ServerURLHere announcement).
    """
    base_url = f"http://{host}:{port}"
    status_url = f"{base_url}/status"
    print(f"[simdrive] Smoke testing WDA at {status_url} ...", flush=True)

    # ── Step 1: GET /status ──────────────────────────────────────────────────
    try:
        resp = httpx.get(status_url, timeout=10.0)
    except httpx.TransportError as exc:
        raise wda_smoke_failed(0, str(exc))

    if not resp.is_success:
        raise wda_smoke_failed(resp.status_code, resp.text)

    try:
        body = resp.json()
    except Exception:
        raise wda_smoke_failed(resp.status_code, resp.text)

    ready = (body.get("value") or {}).get("ready")
    if not ready:
        raise wda_smoke_failed(resp.status_code, json.dumps(body))

    print("[simdrive] WDA /status smoke test passed — ready=True.", flush=True)

    # ── Step 2: POST /session probe for UI Automation entitlement ────────────
    # Probe using com.apple.Preferences which is always installed on real devices.
    # A Code=41 error means Settings → Developer → Enable UI Automation is OFF.
    # On success we immediately delete the session so nothing leaks.
    session_url = f"{base_url}/session"
    probe_payload = {"capabilities": {"alwaysMatch": {"bundleId": "com.apple.Preferences"}}}
    try:
        print("[simdrive] Probing WDA UI Automation entitlement via POST /session ...", flush=True)
        sess_resp = httpx.post(session_url, json=probe_payload, timeout=15.0)
        try:
            sess_body = sess_resp.json()
        except Exception:
            sess_body = {}

        # Detect XCTDaemonErrorDomain Code 41 (UI Automation disabled).
        # WDA surfaces this in the error value at: {value: {error: "...", message: "..."}}
        # or as a top-level {"status": 13, "value": "XCTDaemonErrorDomain Code=41 ..."}
        # WDA versions differ: some emit "Code=41", others "Code 41" (space).
        # We scan the raw text for the canonical marker with both separators.
        raw_text = sess_resp.text or ""
        if "XCTDaemonErrorDomain" in raw_text and (
            "Code=41" in raw_text or "Code 41" in raw_text
        ):
            # Attempt teardown of any partial session that may have been created.
            _try_delete_wda_session(base_url, sess_body)
            raise wda_ui_automation_disabled(f"{host}:{port}")

        # POST /session succeeded — immediately delete the probe session.
        if sess_resp.is_success:
            _try_delete_wda_session(base_url, sess_body)
            print("[simdrive] UI Automation entitlement confirmed. Probe session cleaned up.", flush=True)
        else:
            # Non-41 failure (5xx, network, capability mismatch, etc.) — not a
            # hard blocker since /status already succeeded. Log and continue.
            _LOG.warning(
                "WDA POST /session probe returned HTTP %s — skipping entitlement check. "
                "Raw body: %.200s",
                sess_resp.status_code,
                raw_text,
            )
            print(
                f"[simdrive] Warning: POST /session probe failed (HTTP {sess_resp.status_code}); "
                "WDA is reachable but UI Automation entitlement unverified.",
                flush=True,
            )

    except _errors_module.SimdriveError:
        # Re-raise wda_ui_automation_disabled (a SimdriveError) without catching it here.
        raise
    except httpx.TransportError as exc:
        # Network error during the probe — WDA may have just started; don't fail bootstrap.
        _LOG.warning("WDA POST /session probe transport error: %s — skipping entitlement check.", exc)
        print(
            f"[simdrive] Warning: POST /session probe transport error ({exc}); "
            "WDA is reachable (/status OK) but UI Automation entitlement unverified.",
            flush=True,
        )

    return body


def _try_delete_wda_session(base_url: str, sess_body: dict) -> None:
    """Best-effort DELETE /session/<id> to clean up a probe session.

    Silently ignores all errors — this is cleanup, not a correctness path.
    """
    session_id = (
        (sess_body.get("sessionId"))
        or (sess_body.get("value") or {}).get("sessionId")
        or None
    )
    if not session_id:
        return
    try:
        httpx.delete(f"{base_url}/session/{session_id}", timeout=5.0)
        _LOG.debug("Deleted probe session %s", session_id)
    except Exception as exc:  # noqa: BLE001
        _LOG.debug("Could not delete probe session %s: %s", session_id, exc)


# ─── D8: device metadata extraction from WDA /status + devicectl ────────────


def extract_device_metadata_from_status(status_body: dict) -> dict:
    """Pull device_name + os_version out of a WDA /status payload.

    Appium WDA v9.9.0 status response (real device) typically contains:
        {
          "value": {
            "build": {...},
            "ios": {"ip": "192.168.x.y"},
            "os":  {"name": "iOS", "version": "26.3.1", "sdkVersion": "..."},
            "ready": true
          }
        }

    Device name (the user-visible "Moes Max") is NOT in /status — it lives in
    devicectl's deviceProperties.name. Callers should layer the devicectl
    lookup on top via fetch_device_name_via_devicectl().

    Returns a dict with the keys actually populated; missing fields default to
    "" so callers get a stable shape.
    """
    value = (status_body or {}).get("value") or {}
    os_block = value.get("os") or {}
    ios_block = value.get("ios") or {}
    os_version = os_block.get("version") or ios_block.get("version") or ""
    return {"os_version": str(os_version) if os_version else ""}


def fetch_device_name_via_devicectl(udid: str) -> str:
    """Return the user-visible device name (e.g. "Moes Max") via devicectl.

    devicectl JSON path: result.deviceProperties.name. Returns "" on any
    failure so callers can fall back to a sentinel without a try/except.
    """
    result = subprocess.run(
        ["xcrun", "devicectl", "device", "info", "details",
         "--device", udid, "--json-output", "-"],
        capture_output=True, text=True, timeout=30, check=False,
    )
    if result.returncode != 0:
        return ""
    try:
        data = json.loads(result.stdout)
        name = data.get("result", {}).get("deviceProperties", {}).get("name", "")
        return str(name) if name else ""
    except (json.JSONDecodeError, AttributeError, TypeError):
        return ""


# ─── user-facing Trust guidance ──────────────────────────────────────────────


def _print_trust_guidance(team_id: str) -> None:
    """Print the device-side Trust prompt instructions to stdout.

    Called before install so the user knows to watch their device screen.
    """
    print("", flush=True)
    print("=" * 72, flush=True)
    print("DEVICE ACTION MAY BE REQUIRED", flush=True)
    print("=" * 72, flush=True)
    print(
        f"If this is the first time installing a build signed with team {team_id}:\n"
        f"\n"
        f"iOS will show an 'Untrusted Developer' alert on the device.\n"
        f"To trust the certificate:\n"
        f"\n"
        f"  Settings → General → VPN & Device Management\n"
        f"    → Your developer certificate\n"
        f"    → Trust\n"
        f"\n"
        f"After tapping Trust, re-run:\n"
        f"  simdrive bootstrap-device <udid> --team-id {team_id}\n",
        flush=True,
    )
    print("=" * 72, flush=True)
    print("", flush=True)


# ─── main bootstrap entry-point ──────────────────────────────────────────────


def bootstrap_device(
    udid: str,
    signing_identity: Optional[str] = None,
    team_id: Optional[str] = None,
    wireless: bool = False,
    wda_port: int = _WDA_DEFAULT_PORT,
    rebuild: bool = False,
) -> dict:
    """Full WDA bootstrap sequence. Prints progress to stdout.

    udid: CoreDevice pairing UUID (as shown by `xcrun devicectl list devices`).
          The hardware UDID for xcodebuild is resolved automatically via devicectl.

    team_id: Apple Developer Team ID. When omitted (None), auto_detect_team_id()
             is called first. If exactly one team is found it is used silently.
             If multiple teams are detected, a clear error is raised listing them.

    Returns the registry dict that was persisted to ~/.simdrive/wda/<udid>.json.
    All steps raise a typed SimdriveError subclass on failure.
    """
    # 1. Host tools
    verify_host_tools()
    print("[simdrive] Host tools OK.", flush=True)

    # 1b. Auto-detect team when not explicitly supplied (a10).
    if team_id is None and signing_identity is None:
        detected = auto_detect_team_id()
        if detected is not None:
            print(f"[simdrive] Auto-detected team: {detected}", flush=True)
            team_id = detected
        else:
            # Could not resolve to a single team — surface a clear error.
            # Run security again to list what we found so the user can pick.
            _sec = subprocess.run(
                ["security", "find-identity", "-p", "codesigning", "-v"],
                capture_output=True, text=True, timeout=10, check=False,
            )
            found_teams: list[str] = []
            if _sec.returncode == 0:
                found_teams = sorted({
                    i["team_id"] for i in _parse_identities(_sec.stdout) if i["team_id"]
                })
            if found_teams:
                teams_str = ", ".join(found_teams)
                raise RuntimeError(
                    f"[simdrive] Multiple Developer Teams found in keychain: {teams_str}\n"
                    f"Pass --team-id <one of: {teams_str}> to disambiguate."
                )
            else:
                raise RuntimeError(
                    "[simdrive] No Developer signing identities found.\n"
                    "To fix:\n"
                    "  1. Open Xcode → Settings → Accounts → add your Apple ID, OR\n"
                    "  2. Install a Developer certificate from developer.apple.com/account,\n"
                    "     then re-run without --team-id.\n"
                    "  3. Or pass --team-id <TEAM_ID> explicitly."
                )

    # 2. Device state
    verify_device_ready(udid)
    print("[simdrive] Device ready (paired, Developer Mode, DDI).", flush=True)

    # 2b. Resolve hardware UDID (Bug 2 fix).
    # udid here is the CoreDevice pairing UUID; xcodebuild needs the hardware UDID.
    hardware_udid = resolve_hardware_udid(udid)

    # 3. Clone WDA
    source_dir = clone_wda(udid, rebuild=rebuild)

    # 4. Signing identity
    resolved_identity, resolved_team = resolve_signing_identity(signing_identity, team_id)
    # resolved_identity may be None for Apple Personal Team (no cert in keychain yet);
    # xcodebuild will download one via -allowProvisioningUpdates.
    print(
        f"[simdrive] Signing identity: "
        f"{resolved_identity or '(none — xcodebuild will fetch via -allowProvisioningUpdates)'}",
        flush=True,
    )
    print(f"[simdrive] Team ID:          {resolved_team}", flush=True)

    # 4b. Xcode account check — must happen BEFORE xcodebuild so we surface the
    # "No Account for Team" error with actionable recovery instead of xcodebuild's
    # terse message. Certs in keychain ≠ Xcode Account session for portal access.
    verify_xcode_account_for_team(resolved_team)
    print("[simdrive] Xcode account check passed (Apple Account signed in).", flush=True)

    # Trust guidance before install (device screen may prompt).
    _print_trust_guidance(resolved_team)

    # 5. Build (Bug 2, 3, 4 + a10 bundle-ID-rewrite fixes applied inside build_wda).
    # build_wda now returns (derived_data, bundle_id).
    derived_data, build_bundle_id = build_wda(udid, source_dir, resolved_team, hardware_udid)

    # 6. Install (uses coredevice UUID for devicectl; passes per-team bundle_id
    # so devicectl uninstalls the right app, not the stale com.facebook one).
    bundle_id = install_wda(udid, derived_data, build_bundle_id)

    # 7. Launch + port discovery (Bug 5+6 fix: xcodebuild test-without-building)
    host, port = launch_and_discover_port(udid, derived_data, hardware_udid, bundle_id, wda_port)

    # 7b. Resolve a host that actually reaches WDA from this Mac. On iOS 17+ the
    # announced host is often `localhost` (no routable Wi-Fi IP), which the Mac
    # cannot reach — fall back to the CoreDevice tunnel IP. (iOS 17+ tunnel fix)
    host = choose_reachable_host(host, port, udid)

    # 8. Smoke test using the device's WiFi IP — pulls /status which carries
    # os.version (D8). Run before persisting so the registry write captures
    # the current OS / device-name fields in a single operation.
    status_body = smoke_test(host, port)
    metadata = extract_device_metadata_from_status(status_body)
    device_name = fetch_device_name_via_devicectl(udid)

    # 9. Persist registry (includes both ip and port — Bug 6 fix; D8 adds
    # device_name + os_version so tool_session_start can populate them.)
    import time as _time
    entry = {
        "wda_bundle_id": bundle_id,
        "install_path": str(_find_wda_app_bundle(derived_data) or ""),
        "derived_data": str(derived_data),
        "xctestrun_path": str(_find_xctestrun(derived_data) or ""),
        "last_built_at": _time.strftime("%Y-%m-%dT%H:%M:%SZ", _time.gmtime()),
        "host": host,
        "ip": host,   # explicit ip field for device WiFi address
        "port": port,
        "signing_identity": resolved_identity,
        "team_id": resolved_team,
        "hardware_udid": hardware_udid,
        "coredevice_uuid": udid,
        "device_name": device_name,
        "os_version": metadata.get("os_version", ""),
    }
    registry_path = registry.save(udid, entry)
    print(f"[simdrive] Registry written to {registry_path}", flush=True)

    # 10. Success summary
    print("", flush=True)
    print("=" * 72, flush=True)
    print("WDA READY", flush=True)
    print("=" * 72, flush=True)
    print(f"  CoreDevice UUID:  {udid}", flush=True)
    print(f"  Hardware UDID:    {hardware_udid}", flush=True)
    print(f"  WDA endpoint:     http://{host}:{port}", flush=True)
    print(f"  Bundle ID:        {bundle_id}", flush=True)
    print(f"  Team ID:          {resolved_team}", flush=True)
    print(f"  Registry:         {registry_path}", flush=True)
    print("", flush=True)
    print("Next step: start a simdrive session with target=device:", flush=True)
    print(f'  simdrive start-session --udid {udid} --target device', flush=True)
    print("=" * 72, flush=True)

    return entry


# ─── companion daemon controls (B3) ──────────────────────────────────────────


def wda_up(udid: str) -> dict:
    """Re-launch a previously-bootstrapped WDA daemon for ``udid``.

    Reads ``~/.simdrive/wda/<udid>.json`` to recover the cached xctestrun and
    hardware UDID; skips the build/install steps. Use after a phone reboot or
    after ``simdrive wda-down`` to bring WDA back without a full bootstrap.

    Raises ``wda_not_bootstrapped`` if the registry entry is absent or the
    cached xctestrun is missing.
    """
    entry = registry.load(udid)
    if entry is None:
        raise wda_not_bootstrapped(udid)

    xctestrun = entry.get("xctestrun_path") or ""
    hardware_udid = entry.get("hardware_udid")
    derived_data_str = entry.get("derived_data") or ""
    if not xctestrun or not Path(xctestrun).exists() or not hardware_udid:
        raise wda_not_bootstrapped(udid)

    bundle_id = entry.get("wda_bundle_id", _WDA_BUNDLE_ID)
    derived_data = Path(derived_data_str) if derived_data_str else Path(xctestrun).parent.parent.parent
    wda_port = int(entry.get("port") or _WDA_DEFAULT_PORT)

    host, port = launch_and_discover_port(udid, derived_data, hardware_udid, bundle_id, wda_port)
    # iOS 17+ tunnel fix: prefer a host that actually reaches WDA from this Mac.
    host = choose_reachable_host(host, port, udid)

    entry["host"] = host
    entry["ip"] = host
    entry["port"] = port
    # D8: refresh device_name / os_version on re-up so post-reboot OS upgrades
    # land in the registry without a full re-bootstrap.
    status_body = smoke_test(host, port)
    metadata = extract_device_metadata_from_status(status_body)
    if metadata.get("os_version"):
        entry["os_version"] = metadata["os_version"]
    name = fetch_device_name_via_devicectl(udid)
    if name:
        entry["device_name"] = name
    registry.save(udid, entry)
    print(f"[simdrive] WDA back up on http://{host}:{port}", flush=True)
    return entry


def wda_down(udid: str) -> bool:
    """SIGTERM the running WDA daemon for ``udid`` (read PID from pidfile).

    Returns True if a process was signalled, False if no pidfile/process found.
    Removes the pidfile on success.
    """
    pid_file = _pid_path(udid)
    if not pid_file.exists():
        print(f"[simdrive] No WDA pidfile at {pid_file} — nothing to stop.", flush=True)
        return False

    try:
        pid = int(pid_file.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        pid_file.unlink(missing_ok=True)
        return False

    import signal
    try:
        os.kill(pid, signal.SIGTERM)
        print(f"[simdrive] Sent SIGTERM to WDA daemon pid={pid}", flush=True)
        signalled = True
    except ProcessLookupError:
        print(f"[simdrive] WDA daemon pid={pid} already gone.", flush=True)
        signalled = False

    pid_file.unlink(missing_ok=True)
    return signalled
