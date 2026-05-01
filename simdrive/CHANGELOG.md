# Changelog

## 0.3.0a3 — 2026-05-01

Dogfood fixes from Example Reader's v0.3.0a2 run. One HIGH-severity issue (type_text was reporting wrong focus signal under HID), plus four quality-of-life additions and a docs starter set.

### Fixed
- **`type_text` reports `injection_method` and `dispatch_succeeded`.** Soft-keyboard heuristic was the wrong signal under HID dispatch — the keystrokes always land but the keyboard isn't drawn. New fields are reliable; the legacy `keyboard_visible` and `focused_field` stay for cliclick-path debugging.
- **OCR confidence is dictionary-gated.** Stylized covers used to OCR as "Sary of the Canadan liothest" with confidence 1.0. New `confidence_band` ("high" / "medium" / "low") and a clamped legacy `confidence` field flag misreads even when the OCR engine reports high internal confidence. Existing `raw_confidence` exposes the unclamped score.
- **Stale-MCP detection.** When the loaded simdrive version differs from the version on disk (after a `pip install --upgrade` without restarting), every tool response carries `_simdrive_warning` flagging the drift.

### Added
- **`version` MCP tool.** Zero-arg → `{version, loaded_at, disk_version, drift}`. No more guessing whether the running server matches the on-disk package.
- **`clear_field` MCP tool + `type_text(clear_first: true)` flag.** Sends Cmd-A then delete via HID. Replaces the five-press_key idiom for clearing search fields.
- **Icon-glyph semantic-name aliases.** `find_by_text(marks, "search")` now matches the magnifying-glass OCR-misread "Q/". Initial whitelist covers search, back, forward, settings, menu, close, add.
- **`docs/LIMITATIONS.md` and `docs/BEST_PRACTICES.md`.** First-pass docs covering the documentation-only items from Example Reader's dogfood: Dynamic Island modals, xctrace ceiling, MFA hard-wall, HID + debounce-window rule, text-resolution rapid-cycle fallback.

## 0.3.0a2 — 2026-05-01

Closes the two partials from the v0.2.0a2 maintainer feedback round.

### Added
- **`list_devices` reports `last_seen` and `unavailable_reason`.** Each real-device entry now carries `last_seen` (ISO-8601 from `devicectl`'s `lastConnectionDate`, when present) and `unavailable_reason` — a composed one-line diagnosis from `pairingState` / `tunnelState` / `transportType` / `developerModeStatus`. No more guessing why a device shows `state: unavailable`.
- **`recording.yaml` captures `app_version`.** `recorder.finalize()` calls a new `sim.get_app_version(udid, bundle_id)` helper that pulls `CFBundleShortVersionString` (or `CFBundleVersion` fallback) out of `simctl listapps`. Replays now carry the exact app version they were recorded against — diagnosing "passed yesterday, fails today" against a newer build is one field away.

## 0.3.0a1 — 2026-04-30

SpecterQA parity sprint, round 1. simdrive grows from 13 to 27 MCP tools, closing the major capability gaps that kept Example Reader's full SpecterQA migration from being a clean cut. Headline: native performance monitoring on simulators, no XCTest required.

### Added — performance monitoring
- **`perf`** — CPU%, memory RSS, thread count for the active app. simctl + ps-based; no XCTest bridge needed.
- **`perf_baseline`** — capture a labeled baseline; stored per-session for compare.
- **`perf_compare`** — diff a current snapshot against a baseline; reports per-axis delta and severity (`high` / `medium` / `low`).
- **`memory`** — detailed memory breakdown (footprint, dirty, swapped, clean) via the macOS `footprint` tool; reports `available: false` gracefully if the binary is missing.

### Added — diagnostics
- **`doctor`** — environment readiness check: Xcode CLT, simctl, runtimes, booted devices, native HID helper presence.
- **`app_state`** — foreground / background / suspended / not-running for the session app.
- **`apps`** — list installed apps on a sim (bundle id, name, version, path).
- **`crashes`** — `.ips` crash report retrieval from `~/Library/Logs/DiagnosticReports`, filterable by session-start time and bundle id.

### Added — robustness
- **`dismiss_first_launch_alerts`** — taps Allow/Don't Allow on permission alerts. Includes the 1-in-4 alert-race fix from the v0.1 dogfood backlog: re-observes 200 ms post-tap and retries once if the alert text persists.
- **`pre_grant_permissions`** — pre-grant location / camera / photos / etc. via `simctl privacy grant` before launch.
- **`set_appearance`** — toggle the simulator into light or dark mode.
- **`dismiss_sheet`** — dismiss a sheet/modal by swiping down 50 % of screen height.
- **`list_replays`** — list saved replay recordings with metadata (steps, created_at, simdrive_version, tags).
- **`validate_replay`** — structural validation of a recording YAML without executing it.

### Deferred
- `network` — large port (CFNetwork log parsing + nettop merge); needs its own sprint.
- `accessibility_audit`, `webview_elements` — XCTest-only; do not fit simdrive's vision-first model.
- `app_relaunch` — iOS 26.3 teardown recovery is fragile; deferred to a stability-focused cut.

## 0.2.0a2 — 2026-04-30

Example Reader v0.2.0a1 dogfood feedback round. simdrive is now Example Reader's canonical iOS sim driver (SpecterQA archived). Three rough edges patched plus a maintainer-feedback follow-up: SSIM region masking, stable_id_loose, step_id correlation, list_devices HID truth, richer recording metadata, CLI flags, and richer replay halt context.

### Fixed
- **Recordings serialize `stable_id` alongside pixel coords.** Replays now prefer stable_id resolution against the live observe and fall back to the recorded pixel only when the stable_id can't be found in the current screen. Previous behavior: layout shifts of even one pixel would silently tap the wrong place.
- **`observe(annotate=false)` no longer wipes the mark cache.** Subsequent `tap text=` / `mark=` / `stable_id=` calls now resolve against the most recent annotated observe, instead of failing with "no marks available."

### Added
- **`type_text` response now includes `keyboard_visible` and `focused_field`.** Removes the need to follow every type_text with an extra `observe` to verify focus. `focused_field` carries the `stable_id` of the `tap_first` target when one was supplied.
- **SSIM region masking via `mask_regions` on `replay` + `ssim_masks` in `recording.yaml`.** Blank rectangles in both screenshots before the similarity compute so the iOS status-bar clock (and any other dynamic chrome) stops dragging same-screen SSIM into the 0.6s. Accepts `[x, y, w, h]` tuples or `{x, y, w, h, label?}` dicts. YAML field is consulted only when the caller passes nothing.
- **`Mark.stable_id_loose` companion.** 60px bucket (3× the tight 20px) tolerates the >3px layout shifts that re-bucket the tight `stable_id`. Surfaced on `Mark.to_dict()`, accepted by `tap`, persisted alongside `stable_id` in recordings, and tried by replay when tight resolution misses before falling through to pixel coords.
- **`step_id` returned by act tools while recording.** `tap` / `swipe` / `type_text` / `press_key` responses include the recorder step index when a recording is active (omitted otherwise) so callers can correlate live actions with the recording's step list.
- **`list_devices` reports `hid_supported` + `hid_note`.** Each device entry now carries `hid_supported: false` (real-device input still routes through WDA, which is on the v0.3 roadmap), and the response carries a top-level `hid_note` string explaining what to use instead. No more guessing whether tap will work.
- **Richer recording metadata.** `recording.yaml` now captures `simdrive_version`, `created_by_session`, `screenshot_size_pixels`, and a `tags: []` list. `record_start({tags: [...]})` lets callers pin free-form tags into the recording.
- **`simdrive --version` / `--help`.** The CLI no longer launches an MCP server when invoked with a flag — `--version` / `-V` prints `simdrive <version>`, `--help` / `-h` prints a one-screen usage blurb.
- **Replay halt context.** `replay()` returns now include `halt_reason` (`"drift"` | `"execute_error"` | `null`), `threshold` (the value passed in), and `steps_planned` (total steps in the recording) on every response so callers can render a useful halt message without re-loading the YAML.

## 0.2.0a1 — 2026-04-29

First slice of real-device support. **Observe + logs + app lifecycle** work against connected iPhones and iPads. Touch input still requires WebDriverAgent (v0.2.x roadmap; see `docs/REAL_DEVICE_FEASIBILITY.md`).

### Added
- **`target` parameter on `session_start`**: `"simulator"` (default) or `"device"` to attach to a paired iPhone/iPad by UDID.
- **`list_devices` MCP tool** — enumerates all paired real devices via `xcrun devicectl`. Returns udid, name, model, transport, state.
- **`device.py` backend module** — `idevicescreenshot` for screenshots, `idevicesyslog` for logs, `xcrun devicectl device install/process launch/process signal` for app lifecycle.
- **`device_input_unavailable` error code** — clear, actionable error for tap/swipe/type_text/press_key on real-device sessions, pointing at the v0.2 WDA roadmap.

### Requirements (real device)
- macOS with Xcode (provides `devicectl`)
- `brew install libimobiledevice` (provides `idevicescreenshot`, `idevicesyslog`)
- Device paired with this Mac via Xcode (one-time)
- Developer Disk Image mounted on the device — error message names the exact `ideviceimagemounter` command if missing

## 0.1.0a2 — 2026-04-29

Example Reader dogfood feedback round 1 (Maurice / internal-ticket regression workload).

### Fixed
- **`type_text` now correctly uppercases** — sends the Shift HID modifier for `A-Z` and shifted symbols (`!@#$%^&*()_+{}|:"<>?~`). Previous behavior typed `"Test Library"` as `"Test Library"`. Credentialed flows (basic auth, SAML, OIDC) now work.
- `swipe` warns when the end y-coordinate falls in the iOS home-indicator zone (bottom ~80px). Saves an accidental "exit to home screen" gesture.

### Added
- **Sidecar JSON per observation** — every screenshot now writes `<screenshot>.json` next to the PNG with the full structured observation (marks, bounds, captured_at, logs). A session directory is now a complete artifact for downstream test infrastructure; no need to capture MCP responses by hand.
- **`actions.jsonl` per session** — every tap / swipe / type_text / press_key call appends to `<session_workdir>/actions.jsonl`. Replay-ready without `record_start`.
- **`Mark.stable_id`** — short hash of `(text + bucketed-position)`. Survives mark-id reshuffling between observes. New tap form: `tap({stable_id: "abc123"})`.

### Investigation notes (not changed)
- The "candidate-build app exits on `< Back` tap" log signature (`Failed to create a bundle instance representing '...Example ReaderTests.xctest'`) is iOS looking up a *Example Reader*-side test bundle, not anything simdrive ships. simdrive does not run XCTest. Likely candidate-side regression in scene-lifecycle teardown.
- The 1-in-4 first-launch-alert miss is being investigated — likely a SpringBoard PID-handoff race during permission-alert ownership transition.

## 0.1.0a1 — 2026-04-27

Initial alpha. simdrive is a fresh package, born from the ashes of `specterqa-ios` after a hard pivot away from XCTest.

### What's in
- 12-tool MCP surface: lifecycle (3) + observe (1) + act (4) + record/replay (3) + logs (1)
- **Real UITouch input**: bundled native helper (`simdrive-input`) drives the simulator through CoreSimulator's HID port. Triggers UITextField first-responder (synthetic mouse events do not on iOS 26). Background dispatch — your foreground app keeps focus.
- **Set-of-Mark observe**: every observe returns the screenshot plus an annotated copy with numbered red boxes drawn over each detected text region. The agent never has to compute pixels.
- **Hybrid tap targets**: `tap` (and `swipe` endpoints, `type_text` `tap_first`) accept `{x, y}` coords, `{mark: <id>}` from the latest observe, or `{text: "..."}` matched against detected text.
- Screenshot capture, log tail (with NSPredicate filter), app launch
- YAML+PNG recording format with drift-aware replay (SSIM)
- 22 unit tests + comprehensive live E2E harness against TestKitApp

### Known limitations
- macOS only; Simulator only. Real-device support is post-v0.1.

### Hard breaks from `specterqa-ios`
- Different package name (`pip install simdrive`)
- No Swift runner, no XCTest, no accessibility-tree selectors
- No HTTP daemon — pure subprocess + AppleScript
- Recording format is incompatible with v16
