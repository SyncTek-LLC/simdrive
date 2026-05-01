# simdrive — Known Limitations

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

`session_start({target: "device"})` supports observe + logs + app lifecycle on
paired physical iPhones/iPads. It does **not** yet support tap / swipe /
type_text / press_key — those route through WebDriverAgent (WDA), which is on
the v0.3 roadmap but not shipped. Use simulators for input-driven flows.

## Background-mode caveats

Under the HID injection backend (the default when the bundled `simdrive-input`
binary is present), simdrive runs in "background mode" — your foreground app
keeps focus while the simulator receives synthetic UITouch events. This works
well for tap/swipe/type, but the iOS soft keyboard isn't drawn (the system
treats the inputs as a hardware keyboard). The `keyboard_visible` field on
`type_text` responses will report `false` even though the keystrokes landed.
The `injection_method` and `dispatch_succeeded` fields are the reliable signals
on the HID path.
