# SpecterQA iOS — Troubleshooting Guide

## AX Backend

### Sheet / presented-ViewController content not enumerated

**Symptom:** `ios_elements()` returns only root-view elements (e.g. tab-bar buttons)
when a SwiftUI `.sheet` with a `UIViewControllerRepresentable`-wrapped UIKit screen
is visible.

**Root cause (fixed in v13.2.0):** The AX tree walk only descended into the primary
`AXWindow` of the Simulator process.  When a modal sheet is presented, its content
appears in a *sibling* `AXWindow` that the original traversal skipped.

**Fix:** Upgrade to SpecterQA iOS v13.2.0+.  The backend now calls
`_walk_sibling_windows()` as a second pass, enumerating every `AXWindow` of the
Simulator process and merging their elements (de-duplicated) into the flat result
list returned by `ios_elements()`.

---

### SpringBoard permission alerts not interactable (iOS 18.4)

**Symptom:** iOS system permission alerts (Notifications, Location, Camera, etc.)
appear on screen but:
- `ios_elements()` does not return the alert buttons.
- `ios_tap(x=..., y=...)` with correct coordinates returns `{"status":"ok"}` but
  the alert does not dismiss.
- AppleScript `tell process "Simulator"` fails with `Invalid index`.

**Root cause:** SpringBoard alerts are rendered by the SpringBoard OS process, which
lives *inside* the simulator guest OS — not inside the Mac `Simulator.app` process.
The Simulator.app AX tree therefore does not expose SpringBoard's alert buttons.

**Workaround 1 — `ios_dismiss_springboard_alert` (best effort):**
```
ios_dismiss_springboard_alert(label="Allow")
```
This tool walks all `AXWindows` of the Simulator process looking for a modal window
with the matching button.  It uses `AXPress` first, then CGEvent coordinate tap as
fallback.  This works for some alert types but is not guaranteed on iOS 18.4 for
`notifications`.

**Workaround 2 — `ios_pre_grant_permissions` (recommended):**
Grant permissions *before* the app launches so no runtime alert appears:
```
ios_pre_grant_permissions(
    bundle_id="com.example.myapp",
    permissions=["notifications", "location", "camera"],
    device_id="booted",
)
```
Then call `ios_start_session`.

**iOS version compatibility matrix:**

| Permission      | iOS 17.x | iOS 18.4 |
|-----------------|----------|----------|
| notifications   | ✅ grant  | ❌ Operation not permitted |
| location        | ✅        | ✅        |
| camera          | ✅        | ✅        |
| microphone      | ✅        | ✅        |
| contacts        | ✅        | ✅        |
| photos          | ✅        | ✅        |
| bluetooth       | ✅        | ✅        |
| health          | ✅        | ✅        |

For `notifications` on iOS 18.4, the only reliable path is to test with a build that
does not request the permission on first launch, or to run on an iOS 17.x simulator
runtime.

---

### Element tree empty immediately after `ios_start_session`

**Symptom:** The first `ios_elements()` call right after `ios_start_session` returns
`count: 0` while `ios_app_state` reports `foreground`.  Retrying after ~1 s succeeds.

**Root cause:** The AX tree hydration in the Simulator process has a short latency
after the app becomes frontmost.

**Fix (v13.2.0+):** `ios_start_session` with `backend="ax"` now polls `ios_elements()`
every 200 ms for up to 2 s before returning, so the agent never sees a zero-count
response from the session-start call itself.

---

### Multiple simulators booted — AX reads wrong device

**Symptom:** `ios_elements()` returns elements from a *different* app than expected.

**Root cause:** When two or more simulator windows are booted, the AX backend reads
the *frontmost* window.  There is no OS API to target a specific simulator via the
AX tree; `xcrun simctl` device selection does not affect which window is frontmost.

**Fix:**
1. Close all simulator windows except the one you want to test.
2. Check the `frontmost_udid` field in the `ios_start_session` response — if it
   differs from your `device_id`, the wrong simulator is in front.
3. Bring the correct device window to front: click it in the Simulator dock or
   use `xcrun simctl bootstatus <udid>` to confirm it is booted, then click it.

**`ios_start_session` now returns `frontmost_udid` (v13.2.0+)** so misconfigurations
are immediately visible in the response:
```json
{
  "status": "ok",
  "backend": "ax",
  "target_udid": "ABC-123",
  "frontmost_udid": "DEF-456",   // ← mismatch means wrong device is in front
  ...
}
```

---

### `open -a Simulator --args -CurrentDeviceUDID` does not select device on iOS 18.4

**Symptom:** Running `open -a Simulator --args -CurrentDeviceUDID <udid>` opens
Simulator.app but focuses the *last used* device rather than the specified one.

**Root cause:** This is an Apple Simulator.app bug on iOS 18.4 / macOS 26.  The
`-CurrentDeviceUDID` argument is ignored when Simulator is already running.

**Working pattern:**
```bash
# 1. Quit Simulator.app completely (important — must be fully closed).
osascript -e 'quit app "Simulator"'
sleep 1

# 2. Boot the target device (if not already booted).
xcrun simctl boot <udid>

# 3. Open Simulator — it will open to the last booted device.
open -a Simulator

# 4. If multiple devices are booted, bring the right one front by UDID
#    using the Simulator window menu, or use:
xcrun simctl bootstatus <udid> -b
```

Note: `xcrun simctl bootstatus -b` blocks until the device is fully booted, which
is the most reliable way to confirm readiness before calling `ios_start_session`.
