# simdrive — Known Limitations

> **Quick reference:** The four limitations most commonly encountered during the 1.0.0a2 Example Reader iOS dogfood
> (`type_text` HID timing, SSIM advisory, `dismiss_sheet` scope, `set_appearance` respring) are documented
> in the [README Known limitations](../README.md#known-limitations--workarounds) section for visibility.
> The full canonical list is below.

# simdrive — Known Limitations (full)

simdrive is a vision-first iOS simulator driver. There are corners it doesn't
reach — by design, by platform constraint, or by deferred scope. This document
is the canonical list. If you hit one of these, the workaround is upstream of
simdrive (different tool, human-in-the-loop, or test-tier credentials), not a
config flag we forgot to mention.

## Modal dismissal via Dynamic Island

Dynamic Island modals (the pill-shaped notifications and live activities at the
top of the screen) are not auto-dismissable through simdrive. The Dynamic
Island accepts swipe-up gestures from the user's finger, but the gesture
recognizer rejects synthetic touches that don't originate from a real
hardware-side touch event.

**Workaround:** users dismiss them manually with a swipe-up gesture. For
automated test flows, factor the assertion to occur after the activity ends or
mock the activity provider away in your test build.

## xctrace Allocations parsing

simdrive's `perf` and `memory` tools surface CPU%, RSS, footprint, dirty,
swapped, clean, and thread count via `simctl` + `ps` + the macOS `footprint`
binary. They do **not** parse allocation-by-allocation traces from
`xctrace record --template Allocations`.

For deeper analysis (per-class allocation hotspots, leak detection, retain-cycle
visualization) use Instruments.app's GUI directly or shell out to
`xctrace record --template Allocations` and parse the resulting `.trace`
bundle yourself. simdrive will surface the data when xctrace exposes a
machine-readable export — until then, the snapshot tools are intentionally
simpler.

## MFA / 2FA codes

simdrive can drive any visible UI but cannot intercept SMS messages, push
notifications, authenticator app codes, or hardware security keys.

**Workarounds:**
- **Test-tier credentials:** keep a dedicated test account with MFA disabled.
  Most providers permit this on staging.
- **Static OTP seeds:** if the system supports TOTP, share the seed with your
  test runner and compute the code in the test harness, then `type_text` it.
- **Human-in-the-loop:** for one-off flows, pause the run, surface the screen
  to a human, and resume after they enter the code.

## Real-device input

`session_start({target: "device"})` supports observe + tap + swipe + type_text +
press_key + clear_field on paired physical iPhones/iPads via WebDriverAgent (WDA).
Bootstrap the device first with `simdrive bootstrap-device <udid> --team-id <id>`.

### `tool_observe` annotate=True on real device returns no SOM marks

**`tool_observe` annotate=True on real device returns no SOM marks.** SOM
(Set-of-Marks) annotation requires a UI element tree source. On simulators we
get this from accessibility services. On real devices via WDA, the equivalent —
WDA's `/source` endpoint — is not yet wired into the SOM annotator. Real-device
observations return the screenshot only; primitives can still be driven by
`{x, y}` coordinates or `text` (when WDA-based text matching lands). Tracked
for 1.0.0a8.

### Real-device bootstrap requires Xcode Account authentication

`simdrive bootstrap-device <udid> --team-id <id>` requires Xcode itself to be signed in to an Apple ID for the specified team — the codesigning certificate in your keychain (visible via `security find-identity -v`) is not sufficient. xcodebuild's provisioning-profile download requires an Xcode Account session.

**One-time setup (~30 seconds):**
1. Open Xcode.app
2. ⌘, (Cmd+Comma) → Accounts tab
3. Click + → Apple ID → sign in with the Apple ID associated with your developer team
4. Enter password + 2FA

After this, `~/Library/MobileDevice/Provisioning Profiles/` will populate as needed when `xcodebuild -allowProvisioningUpdates` runs.

`simdrive bootstrap-device` checks for this state pre-flight and raises `wda_xcode_account_not_authenticated` with this same recovery if the profiles directory is empty.

## Background-mode caveats

Under the HID injection backend (the default when the bundled `simdrive-input`
binary is present), simdrive runs in "background mode" — your foreground app
keeps focus while the simulator receives synthetic UITouch events. This works
well for tap/swipe/type, but the iOS soft keyboard isn't drawn (the system
treats the inputs as a hardware keyboard). The `keyboard_visible` field on
`type_text` responses will report `false` even though the keystrokes landed.
The `injection_method` and `dispatch_succeeded` fields are the reliable signals
on the HID path.

## `type_text` first-character drop (HID timing)

The first character occasionally drops when typing into a fresh text field (e.g.
`simdrive` typed → `Smdrive`). Cause: HID injection beats the field's
keyboard-focus settle time.

**Workaround:** pass `tap_first=True` to `type_text`, or call `tap` on the
target field immediately before typing. The keyboard focus will settle, then
injection lands cleanly.

```python
# Safe pattern for any text field where the first character matters
type_text({text: "simdrive", tap_first: True})
```

Observed consistently during the 1.0.0a2 Example Reader iOS dogfood (2026-05-04).

## SSIM threshold is advisory; `structural_checks` is the regression gate

Recordings store an SSIM threshold (default 0.85). Replay drift below the SSIM
threshold is **reported but does NOT fail a step** — the journey YAML's
`structural_checks` (element presence, content assertions) are the actual
regression gate.

**Why:** OPDS content, time-of-day clocks, library-list ordering, and appearance
changes all shift pixels without changing app behavior. SSIM was designed as a
visual decoration signal; structural assertions are what actually catch
regressions. In the 1.0.0a2 Example Reader dogfood, 76% of replay steps drifted — but
`struct-check` passed on all of them because the behavioral assertions were
correct. Don't chase pixel drift as if it were a behavioral regression.

For replay-driven regression to be meaningful across environments, ensure the
recording and replay share the same appearance mode, library/account state,
locale, and (where possible) time-of-day masks on status-bar clocks.

## `dismiss_sheet` covers system sheets only

`dismiss_sheet` swipes down on system-presented modal sheets
(`UIPresentationController`-backed). It does **not** dismiss SwiftUI half-sheets
(`.sheet` modifier with `.presentationDetents([.medium])`) — those use a different
presentation backend and don't respond to the synthetic downward swipe.

**Workaround:** for SwiftUI half-sheets, use `swipe` from a point near the top
of the sheet's drag handle to a point well below the bottom of the screen — the
sheet's own gesture recognizer drives the dismissal. Or tap the sheet's explicit
close button if one exists.

```python
# SwiftUI half-sheet: swipe from drag handle downward
swipe({from_x: 390, from_y: 300, to_x: 390, to_y: 800})
```

Confirmed via the Example Reader iOS half-sheet during the 1.0.0a2 dogfood.

## `set_appearance` may need an app respring

`set_appearance` (`light` / `dark`) tells the simulator to switch appearance mode,
but in-flight UI may not redraw until the app respringboards. Most apps observe
`traitCollectionDidChange` correctly; some apps with custom theme handling or
color caches set at launch don't pick up the change mid-session.

**Workaround:** if the appearance change doesn't propagate visually, call
`session_end` then `session_start` to relaunch the app. A fresh launch will
observe the correct appearance mode.

```python
set_appearance({appearance: "dark"})
# If the app's UI doesn't update:
session_end()
session_start({app_bundle_id: "...", device: "..."})  # launches into dark mode
```
