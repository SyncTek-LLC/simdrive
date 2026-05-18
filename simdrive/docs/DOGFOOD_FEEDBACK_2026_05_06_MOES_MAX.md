---
name: simdrive 1.0.0a7 device dogfood (Moes Max)
description: First end-to-end dogfood of a7 against a real iPhone 17 Pro Max — WDA bootstraps and runs, but session_start can't drive it; severe device-mode bugs catalogued
type: reference
originSessionId: 5b3c807c-9a6f-4fb3-bf63-c7dfa690fd2e
---
# simdrive 1.0.0a7 — physical device dogfood, Moes Max (iPhone 17 Pro Max), 2026-05-06

Target: `00008150-00142D540A87801C` "Moes Max", iPhone 17 Pro Max, iOS 26.3.1, localNetwork (wireless).
Host: macOS, Xcode 26.3 (build 17C529), simdrive 1.0.0a7.

## What works

1. `version` reports `disk_version=1.0.0a7`, `drift=false` after `/mcp` reload.
2. `list_devices` enumerates all paired devices with state/transport/hid_supported/last_seen — 11 devices listed, 3 available.
3. `bootstrap-device` CLI exists with sensible flags (`--team-id`, `--signing-identity`, `--wireless`, `--wda-port`, `--rebuild`).
4. `wda/PINNED_SHA.txt` is present in the wheel (a5 packaging defect fixed). Pinned to Appium WDA v9.9.0 (commit 99c52473), Xcode 16-compatible.
5. WDA bootstrap eventually succeeds: clones, builds (after `-allowProvisioningUpdates` profile fetch), installs via devicectl, launches via `xcodebuild test-without-building`, smoke-tests `/status` ready=true, writes registry to `~/.simdrive/wda/<udid>.json`.
6. Manual relaunch of WDA via `xcodebuild test-without-building -xctestrun <path> -destination id=<udid>` works and `/status` returns `{ready: true, ios.version: 26.3.1, build.version: 9.9.0}`.
7. `session_start target=device udid=<udid>` (no bundle) returns a clean session with `target: device`.
8. `press_key` / `tap` on device return clean `wda_session_not_open` errors with recovery hints (vs a5 simctl-error leak — real improvement).

## What's broken

### Bootstrap-side

**B1.** Pre-flight Xcode-account check (`verify_xcode_account_for_team` in `wda/bootstrap.py:396`) is a substring grep for `"identifier"` in `defaults read com.apple.dt.Xcode DVTDeveloperAccountManagerAppleIDLists`. Passes when no real team binding exists. Should at minimum match the team id explicitly, or query `xcrun -find` / Xcode plist for the per-team account state.

**B2.** `--team-id` ambiguity bug: keychain with multiple Apple Development certs under the same team raises `wda_signing_ambiguous` even though all matches are equivalent. Workaround: `--signing-identity "<full string>"`. Fix: when all matches share the same team_id, auto-pick the most-recently-issued cert.

**B3.** `bootstrap-device` does NOT background-detach the WDA `xcodebuild test-without-building` process. WDA dies the instant bootstrap exits. Subsequent `session_start` gets `wda_unreachable: Connection refused`. User must manually `nohup` the runner. Fix: bootstrap should daemonize WDA (or require an always-on `simdrive wda-up <udid>` companion command).

**B4.** WDA project's vendored pbxproj has stale settings from Appium upstream — `DEVELOPMENT_TEAM = B3HE38966G` (a hardcoded personal team), `PRODUCT_BUNDLE_IDENTIFIER = com.facebook.*`, `CODE_SIGN_IDENTITY[sdk=iphoneos*] = "iPhone Developer"`. xcodebuild's command-line `DEVELOPMENT_TEAM=` override DOES propagate (verified), but the first build still fails with "No Account for Team X" because xcodebuild has to round-trip `-allowProvisioningUpdates` to fetch a cert. The fact that the FAILED line is emitted before SUCCEEDED makes monitor/log scraping noisy. Document the retry pattern and treat the FAILED line as not authoritative.

### Device session-side

**D1. observe on device overflows MCP token budget.** Returns 101k chars because the response unconditionally embeds a `screenshot_b64` field (in addition to `screenshot_path`). `annotate=false` does NOT suppress the b64 — observe is unusable from MCP on real devices. Fix: gate `screenshot_b64` behind an explicit param (default `false`) since `screenshot_path` is already returned.

**D2. session_start without `app_bundle_id` does not open a WDA test session.** Every input verb (`tap`, `swipe`, `type_text`, `press_key`) returns `wda_session_not_open`. Fix: open a default WDA session (no app focus) so primitives that don't need an app context still work, OR document that bundle id is required and fix D3.

**D3. session_start WITH `app_bundle_id` fails on a stale devicectl flag.** `device.launch_app()` (line 221) passes `--start-stopped=false` but modern devicectl rejects with "option does not take any value, but 'false' was specified." Drop `=false`; use `--start-stopped` as a boolean flag, or omit entirely when caller wants the app started normally.

**D4. Device-launch failure is wrapped as `no_device` with a simctl recovery hint.** The error message tells the user to `xcrun simctl boot <udid>` — simctl is sim-only. Use `device_launch_failed` code with a devicectl-aware hint.

**D5. `apps` on device returns silent `{apps: []}` (no error, no data).** a5 bug NOT fixed. Should error explicitly or query installed apps via `xcrun devicectl device info applications --device <udid>`.

**D6. `logs` on device returns silent `{ok: true, lines: 0, logs: ""}`.** a5 bug NOT fixed. The implementation tails simulator-only `xcrun simctl spawn log` output. Fix: route to `idevicesyslog` / Console / OSLog or surface `logs_unsupported_on_device` cleanly.

**D7. `app_state` on device leaks simctl "Invalid device: <udid>" detail.** Returns `state: not-running, detail: "Invalid device: ..."` — clearly a simctl path. a5 partial regression: a7 still routes app_state to simctl on device sessions.

**D8. session_start returns generic `device: "Real Device"`, `os_version: ""`** — losing info that's already in the registry (hardware model, iOS version from WDA `/status`). Cosmetic but eats orientation context.

## Severity

a7 device dogfood verdict: **bootstrap is shippable, MCP-driven device session is not.** Once WDA is up, simdrive's MCP surface can't actually drive it in any useful way — observe overflows, input gated on a session that's never opened or fails on a flag bug, read tools silent-empty. To use a real device with simdrive 1.0.0a7 today, you must drive WDA's HTTP API directly, bypassing simdrive's session manager.

## Workaround recipe

Until a8:
1. `simdrive bootstrap-device <udid> --team-id <personal_team> --wireless` (use `--signing-identity` if you have multiple certs per team)
2. After bootstrap exits, `nohup xcodebuild test-without-building -xctestrun <path> -destination id=<udid> > /tmp/wda.log 2>&1 &` to keep WDA alive
3. Curl `http://<device-ip>:8100/status` to confirm WDA is ready
4. Drive WDA via the HTTP API directly (POST `/session`, `/element`, `/wda/tap`, etc.) — bypass simdrive's MCP for device tap/swipe/type until the session-management bugs land

## Filing

Findings should be attached to a simdrive a8 milestone or whatever the next pre-release is. Eight bugs documented (B1–B4, D1–D8 = 12 total) with file:line references where I have them.
