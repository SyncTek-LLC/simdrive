"""WDA-specific error constructors.

Each code follows the established SimdriveError pattern: machine-readable `.code`,
human-readable `.message` that ends with "Recovery: <actionable steps>", and a
`.details` dict with structured context for programmatic inspection.

All codes in this module are WDA-exclusive; they never appear in the simulator path.
"""
from __future__ import annotations

from typing import Optional

from ..errors import SimdriveError


def wda_host_tools_missing(tool: str) -> SimdriveError:
    """Raised when a required host-side CLI tool is not on PATH."""
    return SimdriveError(
        code="wda_host_tools_missing",
        message=(
            f"Required host tool {tool!r} not found on PATH. "
            "Recovery: install the missing tool — "
            "`xcodebuild` ships with Xcode (xcode-select --install), "
            "`idevicepair` via `brew install libimobiledevice`, "
            "`xcrun devicectl` ships with Xcode 16+."
        ),
        details={"tool": tool},
    )


def wda_device_not_ready(udid: str, missing: list[str]) -> SimdriveError:
    """Raised when the device is not in the required state for WDA bootstrap."""
    return SimdriveError(
        code="wda_device_not_ready",
        message=(
            f"Device {udid} is not ready for WDA bootstrap. "
            f"Missing conditions: {', '.join(missing)}. "
            "Recovery: ensure the device is paired (`xcrun devicectl device info details --device <udid>`), "
            "enable Developer Mode in Settings → Privacy & Security → Developer Mode, "
            "and mount the DDI by connecting the device and trusting it in Xcode."
        ),
        details={"udid": udid, "missing": missing},
    )


def wda_no_signing_identity() -> SimdriveError:
    """Raised when no codesigning identity is found in the keychain."""
    return SimdriveError(
        code="wda_no_signing_identity",
        message=(
            "No valid Apple Developer codesigning identity found in keychain. "
            "Recovery: create a free Apple Developer account at https://developer.apple.com, "
            "then in Xcode open Preferences → Accounts → add your Apple ID → "
            "Manage Certificates → + → Apple Development. "
            "Or provide --signing-identity \"Apple Development: Your Name (TEAMID)\"."
        ),
        details={},
    )


def wda_signing_ambiguous(identities: list[str]) -> SimdriveError:
    """Raised when multiple signing identities exist and none was explicitly selected."""
    return SimdriveError(
        code="wda_signing_ambiguous",
        message=(
            f"Multiple codesigning identities found ({len(identities)}); "
            "cannot auto-select. "
            f"Identities: {identities}. "
            "Recovery: re-run with --signing-identity \"<identity>\" or --team-id <TEAMID> "
            "to select the correct one explicitly."
        ),
        details={"identities": identities},
    )


def wda_build_failed(log_path: str) -> SimdriveError:
    """Raised when xcodebuild build-for-testing exits non-zero."""
    return SimdriveError(
        code="wda_build_failed",
        message=(
            f"xcodebuild failed building WebDriverAgentRunner. "
            f"Full log at: {log_path}. "
            "Recovery: open the log, look for the first BUILD FAILED section, "
            "verify your signing identity and team-id are correct, "
            "and ensure Xcode can locate the device (Xcode → Window → Devices and Simulators)."
        ),
        details={"log_path": log_path},
    )


def wda_install_failed(stderr: str) -> SimdriveError:
    """Raised when xcrun devicectl device install app exits non-zero."""
    return SimdriveError(
        code="wda_install_failed",
        message=(
            f"devicectl failed installing WebDriverAgentRunner on device. "
            f"devicectl output: {stderr[:400]}. "
            "Recovery: verify the device is unlocked, Developer Mode is on, "
            "the app bundle path is valid, and the signing identity matches "
            "a profile trusted on this device. "
            "On first install you may need to trust the developer cert: "
            "Settings → General → VPN & Device Management → "
            "[Apple Development: Your Name] → Trust."
        ),
        details={"stderr": stderr},
    )


def wda_port_discovery_timeout(udid: str) -> SimdriveError:
    """Raised when xcodebuild stdout does not emit the ServerURLHere pattern within the timeout."""
    return SimdriveError(
        code="wda_port_discovery_timeout",
        message=(
            f"WDA on device {udid} did not advertise its port within the discovery window. "
            "Recovery: check device console (`xcrun devicectl device console --device <udid>`) "
            "for WDA crash or signing errors; ensure the WDA bundle is correctly installed "
            "(`xcrun devicectl device info apps --device <udid>`); "
            "run `simdrive bootstrap-device <udid> --rebuild` to force a clean install."
        ),
        details={"udid": udid},
    )


def wda_device_locked(udid: str) -> SimdriveError:
    """Raised when xcodebuild reports the device is locked during WDA launch."""
    return SimdriveError(
        code="wda_device_locked",
        message=(
            f"Device {udid} is locked. xcodebuild cannot launch WebDriverAgentRunner "
            f"on a locked iOS device — iOS blocks code execution until the user authenticates.\n"
            f"\n"
            f"Recovery:\n"
            f"  1. Unlock {udid} with the passcode (or Face ID / Touch ID).\n"
            f"  2. Optional: extend Auto-Lock (Settings → Display → Auto-Lock) to give the test ~60s to launch.\n"
            f"  3. Re-run `simdrive bootstrap-device {udid} ...`"
        ),
        details={"udid": udid},
    )


def wda_smoke_failed(http_status: int, body: str) -> SimdriveError:
    """Raised when GET /status does not return {value: {ready: true}}."""
    return SimdriveError(
        code="wda_smoke_failed",
        message=(
            f"WDA /status smoke check failed (HTTP {http_status}). "
            f"Response body: {body[:300]}. "
            "Recovery: confirm the WDA process is running on the device, "
            "check the tunnel is alive (`xcrun devicectl device info details --device <udid>`), "
            "and retry. If the problem persists run `simdrive bootstrap-device <udid> --rebuild`."
        ),
        details={"http_status": http_status, "body": body},
    )


def wda_xcode_account_not_authenticated(team_id: str) -> SimdriveError:
    """Raised when Xcode has no Apple ID account session for the given team_id.

    The codesigning certificate in the keychain is necessary but not sufficient.
    xcodebuild's -allowProvisioningUpdates needs an Xcode Account session
    (Settings → Accounts) to call back to Apple's Developer Portal for profile
    downloads. The keychain has certs; Xcode's account storage has the auth tokens.
    They are separate state.
    """
    from pathlib import Path
    profiles_dir = str(Path.home() / "Library" / "MobileDevice" / "Provisioning Profiles")
    return SimdriveError(
        code="wda_xcode_account_not_authenticated",
        message=(
            f"Xcode is not signed in to an Apple ID for team {team_id!r}. "
            f"`xcodebuild -allowProvisioningUpdates` needs an Xcode Account session "
            f"to download provisioning profiles from Apple's Developer Portal. "
            f"The codesigning certificate in your keychain is not enough on its own. "
            f"\n\n"
            f"Recovery (one-time, ~30 seconds):\n"
            f"  1. Open Xcode.app\n"
            f"  2. ⌘, (Cmd+Comma) → Accounts tab\n"
            f"  3. Click + → Apple ID → sign in with the Apple ID for team {team_id}\n"
            f"  4. Enter your password + 2FA when prompted\n"
            f"  5. Re-run `simdrive bootstrap-device <udid> --team-id {team_id}`"
        ),
        details={"team_id": team_id, "profiles_dir": profiles_dir},
    )


def wda_not_bootstrapped(udid: str) -> SimdriveError:
    """Raised when no WDA registry entry exists for the given device UDID.

    The user must run ``simdrive bootstrap-device <udid>`` first to build and
    install WebDriverAgent on the device and write the registry entry.
    """
    return SimdriveError(
        code="wda_not_bootstrapped",
        message=(
            f"No WDA registry entry for device {udid}. "
            f"Run `simdrive bootstrap-device {udid} --team-id <id>` first to install "
            f"and launch WebDriverAgent on this device before starting a real-device session."
        ),
        details={"udid": udid},
    )


def wda_ui_automation_disabled(udid: str) -> SimdriveError:
    """Raised during bootstrap smoke when Settings → Developer → Enable UI Automation is OFF.

    XCTDaemonErrorDomain Code=41 is the wire-level signal. Surfaced early at
    bootstrap so users get an actionable message rather than opaque input-tool
    failures later.
    """
    return SimdriveError(
        code="wda_ui_automation_disabled",
        message=(
            f"UI Automation is disabled on device {udid}. "
            "WDA returned XCTDaemonErrorDomain Code=41 "
            "(\"Not authorized for performing UI testing actions.\"). "
            "\n\n"
            "Recovery: on the device, open Settings → Developer → "
            "Enable UI Automation = ON, then re-run "
            f"`simdrive bootstrap-device {udid}`. "
            "iOS pins this entitlement at runner-process launch; toggling it "
            "requires restarting WDA."
        ),
        details={"udid": udid, "xct_code": 41},
    )


def wda_session_lost(udid: str, last_seen_at: Optional[float] = None) -> SimdriveError:
    """Raised at runtime when the WDA tunnel drops mid-journey."""
    seen_msg = f" (last seen at {last_seen_at:.0f})" if last_seen_at is not None else ""
    return SimdriveError(
        code="wda_session_lost",
        message=(
            f"WDA session on device {udid} is no longer reachable{seen_msg}. "
            "Recovery: call GET /status on the WDA port to confirm it is down, "
            "then run `simdrive bootstrap-device <udid>` to re-bootstrap WDA "
            "and create a new simdrive session."
        ),
        details={"udid": udid, "last_seen_at": last_seen_at},
    )


def wda_recovery_exhausted(
    method: str,
    path: str,
    attempts: int,
    history: list[dict],
) -> SimdriveError:
    """Raised when the WDA auto-recovery loop hits its max-attempt cap.

    ``history`` is a list of per-attempt dicts (attempt index, trigger code,
    action taken, outcome, error excerpt) so callers can post-mortem the
    sequence of failures without re-running the request.
    """
    last = history[-1] if history else {}
    last_excerpt = last.get("error") or last.get("status") or "<unknown>"
    return SimdriveError(
        code="wda_recovery_exhausted",
        message=(
            f"WDA auto-recovery for {method} {path} gave up after {attempts} "
            f"attempts. Last failure: {last_excerpt}. "
            "Recovery: inspect details.history for the per-attempt log; if WDA "
            "is genuinely unreachable run `simdrive bootstrap-device <udid> "
            "--rebuild` to restart the runner, or set "
            "SIMDRIVE_NO_AUTO_REBUILD=1 to opt out and handle recovery yourself."
        ),
        details={
            "method": method,
            "path": path,
            "attempts": attempts,
            "history": history,
        },
    )
