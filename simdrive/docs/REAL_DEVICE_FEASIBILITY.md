# simdrive on real iOS devices — feasibility scoping (2026-04-29)

Per the SpecterQA-replacement gap analysis: simdrive v0.1 is simulator-only. SpecterQA shipped real-device support via WebDriverAgent (XCUITest). This document captures what works, what doesn't, and the implementation path for v0.2.

## Tooling inventory (what's already on this Mac)

| Tool | Capability | Real-device status |
|------|------------|---------------------|
| `xcrun devicectl` (Apple CoreDevice) | enumerate, info, install, launch, process, notification, orientation, reboot | ✓ available; covers app lifecycle |
| `idevicescreenshot` (libimobiledevice) | full-screen PNG capture | ✓ available; requires Developer Disk Image mounted |
| `idevicesyslog` (libimobiledevice) | live syslog tail | ✓ available; works without DDI |
| `ideviceimagemounter` (libimobiledevice) | mount Developer Disk Image | ✓ available; one-time-per-boot on the device |
| `xcrun simctl` | (sim only) | ✗ does not target real devices |
| **Touch input** | tap, swipe, gestures | ✗ no system-provided path. Apple deliberately doesn't expose synthetic UITouch on real devices. |
| **Keyboard input** | typed text, modifier keys | ✗ same gap |

## What works on real device today (with our existing helper architecture)

- `observe` — call `idevicescreenshot` instead of `simctl io screenshot`
- `logs` — call `idevicesyslog` (filter via grep / NSPredicate-equivalent)
- App lifecycle — `devicectl device install`, `devicectl device process launch`, `devicectl device process kill`
- Recording (the read-only half) — capture screenshots + logs into the same YAML+PNG format

This is genuinely useful as **inspection mode**: an agent can drive a debugging session against a connected device, see what's on screen, read logs, attach screenshots to bug reports. About half the simdrive surface, available with no XCUITest dependency.

## What does NOT work without further infrastructure

`tap`, `swipe`, `type_text`, `press_key` — all require synthesizing UITouch / UIKey events. On the simulator, we drive these through CoreSimulator's `SimDeviceLegacyHIDClient`. No equivalent exists for physical devices; Apple deliberately gates synthetic input behind XCUITest (which runs *inside* the app under test).

### Three viable paths to add input

1. **WebDriverAgent (WDA)** — Apple's `XCUIApplication` exposed over an HTTP server inside the test app. simdrive starts WDA on the connected device, opens an HTTP client to it, dispatches taps/swipes/text via WDA's `/wda/*` endpoints. Heavy but proven; this is what Appium and v15 SpecterQA used.
   - **Pros:** Full input parity with simulator. Battle-tested. Open source (Facebook/Apple maintained).
   - **Cons:** Requires (a) building WDA against the user's signing identity, (b) installing the WDA bundle on the device, (c) keeping the WDA process alive during the test session. ~3–5 sessions of work to integrate.

2. **Custom XCTest target packaged with simdrive** — strip WDA down to just the input-injection bits + a thin HTTP server. Smaller binary footprint than WDA, simdrive-shaped API surface.
   - **Pros:** Tighter dependency than WDA; we own the wire format.
   - **Cons:** Same signing/install/keepalive complexity as WDA; we eat the maintenance.

3. **MFi/HID over USB-C** — physical hardware (a USB-C device that emits HID reports) drives the phone. Real-touch fidelity, no XCUITest needed, no signing.
   - **Pros:** Indistinguishable from human touch from iOS's perspective.
   - **Cons:** Hardware. Doesn't fit simdrive's "pip install + go" UX. Niche.

**Recommended:** path #1 (WDA). It's the one Appium chose, the one v15 SpecterQA chose, and the one that keeps simdrive a single-package install. WDA's overhead lives in a one-time provisioning step the user does once per device.

## Connected devices on this Mac (as of 2026-04-29)

| Name | Identifier | Transport | Tunnel | Notes |
|------|------------|-----------|--------|-------|
| Maurice's iPad | `00008112-000C50CE1A08C01E` | wired (USB) | disconnected | iPad Pro 12.9" 6th gen — paired |
| Moes Max | `00008150-00142D540A87801C` | localNetwork | disconnected | iPhone 17 Pro Max — paired |
| Moes Tester | `00008110-001018CE3C44801E` | none | n/a | iPhone 13 Pro Max — currently unavailable |

`screenshotr` service requires the Developer Disk Image to be mounted on each device after a reboot. That's a one-line invocation of `ideviceimagemounter` simdrive can do automatically when a real-device session starts.

## v0.2 implementation plan (sketch)

1. **`backend.py`** abstraction in simdrive — current `sim.py` becomes `simulator_backend`, add `device_backend`. `Session` carries a backend reference; tools dispatch through it.
2. **`device_backend`** initial slice (no input):
   - `screenshot(udid)` → `idevicescreenshot`
   - `logs(udid)` → `idevicesyslog`
   - `install/launch/terminate` → `devicectl device install/process launch/process kill`
   - `mount_developer_disk(udid)` → `ideviceimagemounter` if not already mounted
3. **`session_start`** accepts `target: "simulator" | "device"` (default `"simulator"`); on `"device"` it boots a `device_backend` session and surfaces `observe` + `logs` immediately. `tap/swipe/type_text/press_key` raise `SimdriveError(code="device_input_unavailable", message="real-device input requires WDA; install via 'simdrive bootstrap-wda'")`.
4. **`simdrive bootstrap-wda` CLI** (separate command) — clones WDA at a known SHA, builds against the user's signing identity (or `xcodebuild build-for-testing` against a dev team), installs to the target device, leaves it ready for runtime use. Documented one-time setup.
5. **WDA HTTP client** — small Python wrapper around the WDA REST API; called by `tap/swipe/type_text/press_key` when in `device_backend`.

Estimated effort for the input-less slice (steps 1–3): ~1 day.
Estimated effort for full input via WDA (steps 4–5): ~3–5 days.

## What I'd ship first

A `device_backend` with **observe + logs only**, gated behind a flag (`session_start({target: "device", udid: "..."})`), with crystal-clear errors when an input tool is called. That's a real shippable feature for the inspection use case, gets device support out the door this week, and gives WDA integration a clean home to land into.

Want me to start on that?
