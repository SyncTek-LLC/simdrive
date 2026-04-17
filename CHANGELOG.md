# Changelog

All notable changes to SpecterQA iOS are documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Version numbers follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## v13.0.1 (2026-04-16)

### Hotfix
- fix: Default backend reverted to XCTest — AX backend sees only ~15 elements on SwiftUI views vs XCTest's 58+
- iOS 26 Simulator AX bridge doesn't fully expose SwiftUI's deep element tree
- AX backend still available via `backend="ax"` for simple apps, but XCTest is the production default
- The XCTest sheet/modal crash is a known XCTest framework limitation, not a SpecterQA regression

---

## v13.0.0 (2026-04-16)

### BREAKING: AXUIElement Backend — No XCTest Runner
- Replaces the fragile on-device XCTest runner with host-side macOS Accessibility APIs
- All automation runs from the Mac — no process on the simulator, no deployment, no SIGABRT
- Session start is instant (find Simulator PID) vs ~30s (build + deploy + health wait)
- Zero crashes across all 37 smoke tests — impossible to crash because there's no runner to crash
- Auto-detection: backend="auto" (default) uses AX if available, falls back to XCTest

### AXBackend Implementation
- Element tree: AXUIElement tree walk with AXRole/AXDescription/AXIdentifier extraction
- Tap: AXPress (element-based) or CGEvent (coordinate-based)
- Type: AXSetValue (instant, no keystroke injection) or CGEvent fallback
- Screenshot: simctl io screenshot → PNG→JPEG conversion via PIL
- Swipe: CGEvent backend
- Perf: ps on Simulator.app PID
- Logs: simctl spawn log stream
- Tab bar: probed via AXUIElementCopyElementAtPosition for iOS 26 radio buttons

### AXHTTPServer
- Thin HTTP wrapper on localhost:8222 for backward compatibility
- All smoke tests work unchanged against both XCTest and AX backends

### 37/37 Smoke Tests Passing (Zero Crashes)
- 19 crash pattern scenarios
- 13 functional scenarios
- 5 Example Reader-specific state mutation scenarios

### Requirements
- macOS Accessibility permission for the Python process (one-time system dialog)
- pyobjc-framework-ApplicationServices (added to dependencies)

---

## v12.6.1 (2026-04-15)

### Physical Device Infrastructure (WIP)
- SpecterQAHost thin app target added — builds, signs, installs on physical devices via devicectl
- iproxy USB port forwarding integrated in session manager
- Device discovery via xcrun devicectl (3 devices detected)
- BLOCKED: xcodebuild test-without-building CLI broken on iOS 26 beta ("Root install style not supported") — tracked in #46, awaiting Xcode 26 GM
- Runner builds for iphoneos with automatic provisioning (-allowProvisioningUpdates)
- SUPPORTED_PLATFORMS expanded to "iphoneos iphonesimulator" across all configs

## v12.6.0 (2026-04-15)

### Example ReaderPatternTab — Real-World Crash Pattern Reproduction
- New TestKitApp tab reproducing exact Example Reader Library crash patterns:
  - Borrow/download/return state machine with NotificationCenter cascade (5+ rapid posts)
  - Combine PassthroughSubject rapid progress updates (simulated download)
  - UIViewControllerRepresentable library switcher in SwiftUI sheet modal
  - 10-notification burst trigger button
- All 5 Example Reader pattern tests pass — runner survives notification floods

### Dogfood Issue Regression Tests
- Screenshot parsing: valid JPEG verification + navigation screenshot survival
- Notification flood: rapid tap sequence, rapid element queries, mixed operation burst
- 37 total live smoke tests (up from 27)

### Test Coverage
- 19 crash pattern scenarios across 6 categories
- 13 functional scenarios (form, list, tabs, sheets, perf, accessibility)
- 5 Example Reader-specific state mutation scenarios
- 28/28 UAT tool verification tests
- 101 regression/integration tests

---

## v12.5.1 (2026-04-14)

### Cleanup
- Physical device references removed from MCP API and documentation (blocked by Xcode 26 beta — implementation preserved for GM)
- MCP instructions clarified: simulator-only for now, xctrace recommended for device profiling
- Runner hardening: removed allElementsBoundByIndex from web view + dismiss-alert paths
- 200ms idle settle before element queries prevents stale snapshot during transitions

### Verified
- 27/27 live smoke tests passing (14 crash patterns + 13 functional)
- 101 regression/integration tests passing
- 29 MCP tools, agent-first instructions

---

## v12.5.0 (2026-04-14)

### Agent-First: Complete MCP Instructions + Perf Workflow Tools
- MCP server `instructions` rewritten as a complete agent guide: workflow, perf testing, debugging, form typing, common pitfalls
- `ios_perf_baseline` — capture reference metrics before testing
- `ios_perf_compare` — compare current vs baseline with delta calculation and severity assessment (HIGH/MEDIUM/OK)
- Agents can now run structured performance tests without human guidance
- 29 MCP tools total

### Performance Testing Guide (in MCP instructions)
- RSS thresholds: <100MB good, 100-200MB normal, >300MB investigate, >500MB critical
- Memory leak detection: monotonic RSS growth across repeated actions = leak
- CPU time interpretation: >2s delta for simple action = perf issue
- Thread count: <20 normal, >50 = thread leak

---

## v12.4.0 (2026-04-14)

### Crash-Proof: 27/27 Live Smoke Tests
- 14 new crash pattern scenarios covering every known iOS 26 XCTest crash trigger
- StressTab: LazyVStack recycling, List + 10 TextFields, nested 3-level Form, alert-over-field
- UIKitBridgeTab: UIViewRepresentable TextField/Label/Button, NavigationLink to hybrid detail view
- All crash patterns survived: List+TextField, LazyVStack scroll, nested Form, UIKit↔SwiftUI bridge, rapid tab switching, keyboard during transition, sheet over field, element query during animation, screenshot during animation

### Runner Hardening
- Removed `allElementsBoundByIndex` from web view element query — derives hittable from frame geometry
- Removed `allElementsBoundByIndex` from dismiss-alert handler — uses `buttons.firstMatch` subscript
- Added 200ms idle settle before element queries to prevent stale snapshot during view transitions
- `findByLabel` and `findByIdentifier` use XCTest subscript exclusively (no element iteration)

### Test Suite: 27 smoke + 47 integration + 13 regression + 4 packaging = 91 real tests

---

## v12.3.0 (2026-04-14)

### TestKit: Example Reader Sign-In Pattern
- ListTab added to TestKitApp: SwiftUI List with TextField + SecureField rows mirroring the Example Reader Library sign-in form
- 3 new smoke tests: List navigation, List element discovery, multi-field List typing with sign-in verification
- Tab navigation uses safe coordinate tap to avoid element-based tap crash during view transitions

### Test Suite
- 13/13 live smoke tests passing against real simulator (up from 10)
- 4-tier architecture: smoke (13), integration (47), regression (13), packaging (4)
- Test architecture overhaul: 19,932 lines of mock theater deleted, 0% mock usage

### 13 Smoke Test Scenarios
1. Single field typing ✅
2. Multi-field form (Form) ✅
3. SecureField (password) ✅
4. Tab navigation + cache refresh ✅
5. Sheet open/close ✅
6. Screenshot JPEG under 1MB ✅
7. Element list structure ✅
8. Perf via XCTest bridge ✅
9. Health endpoint ✅
10. E2E Form fill + submit ✅
11. List tab navigation (crash-safe) ✅
12. List element discovery ✅
13. List multi-field typing + sign-in ✅

---

## v12.2.1 (2026-04-14)

### Critical Fix
- fix(runner): Restore snapshot-based element query — per-element iteration via `allElementsBoundByIndex` hangs/crashes on iOS 26
- `findByLabel`/`findByIdentifier` use XCTest subscript lookup (no element iteration)
- Safe fallback to per-element query only when snapshot throws
- 10/10 live smoke tests verified against real simulator ✅

---

## v12.2.0 (2026-04-14)

### Test Architecture Overhaul
- Deleted 38 mock test files (19,932 lines) that caught 0 bugs across 10 production regressions
- 4-tier architecture: smoke (live sim), integration (real code paths), regression (pattern guards), packaging (wheel verification)
- 9 test files, 2,472 lines, 102 tests — every one exercises real behavior
- Mock-to-real ratio: 81% → 0%
- 10/10 live smoke tests remain the release gate

---

## v12.1.0 (2026-04-14)

### Test Harness — Live Simulator Testing Infrastructure
- TestKitApp: SwiftUI test target with TextField, SecureField, tabs, sheets (io.synctek.specterqa.testkit)
- 10 live smoke tests passing against real iOS simulator — the quality gate for every release
- Packaging tests verify wheel contents (Swift source, build scripts)
- 8 regression tests verify source patterns for every historical bug
- GitHub Actions CI workflow for every PR
- No mock tests — all new tests exercise real behavior

### Critical Fix: Multi-Field Form Typing
- Dismiss keyboard → tap target field → app.typeText() — the ONLY approach that works on SwiftUI Forms
- /dismiss_keyboard endpoint: taps above keyboard, swipe fallback
- Element query depth 10 → 50 for deep SwiftUI Form nesting
- Default element types expanded: secureTextField, searchField, cell, tabBar, etc.
- Identifier resolution via Python cache avoids slow 10s findByIdentifier tree walk

### 27 MCP tools, 10/10 smoke tests, 0 regressions

---

## v12.0.0 (2026-04-13)

### Breaking: ios_type now accepts target field parameters
- `ios_type(text, label=, identifier=, element_index=, x=, y=)` — specify WHICH field to type into
- The runner taps the target field first (using element-relative coordinate tap), then types
- Solves multi-field form typing: `ios_type(text="mypass", label="Password")`
- Without a target, types into whatever has focus (legacy behavior preserved)
- This is the systemic fix for the Example Reader sign-in form focus issue

### Fixes
- fix(client): HTTP timeout bumped 5s → 10s for element-based operations (element lookup + settle delays exceed 5s)
- fix(runner): Element-relative coordinate tap (`el.coordinate(withNormalizedOffset:).tap()`) instead of `el.tap()` — prevents iOS 26 SIGABRT crashes
- Live tested: Safari Address bar — targeted type by label, text verified in element tree ✅

---

## v11.9.5 (2026-04-13)

### Critical Fix
- fix(runner): Use element-relative coordinate tap instead of `el.tap()` — prevents SIGABRT crash on iOS 26
- `el.tap()` throws ObjC NSExceptions that Swift cannot catch, killing the runner process
- `el.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.5)).tap()` is element-aware (proper focus transfer) but goes through XCTest's coordinate system (no crash)
- Live tested: Safari — element tap → type → verify, no crashes ✅

---

## v11.9.4 (2026-04-13)

### Critical Fix
- fix(runner): Safe element-based tap — prevents runner crash from `el.tap()` on non-hittable elements
- When element is hittable: uses XCTest `el.tap()` (proper focus transfer for SecureField)
- When element is NOT hittable: uses coordinate tap on element center (safe fallback, no crash)
- Response includes `mode: "element"` or `"element_coord_fallback"` for transparency
- Live tested: Safari Address bar — tap, navigate away, tap back, type, verify ✅ (2 cycles, 0 crashes)

---

## v11.9.3 (2026-04-13)

### Critical Fix
- fix(runner): Element-based tap via XCTest `element.tap()` — fixes SecureField focus transfer (#43)
- `POST /tap` now accepts `label` or `identifier` params for element-based tapping
- When label/identifier is provided, runner uses `elementQuery.findByLabel()` → `element.tap()` instead of coordinate tap
- This properly transfers first-responder focus even on SwiftUI SecureField inside List/Form cells
- Python `handle_tap` prefers element-based tap, falls back to coordinate tap on failure
- Response includes `tap_mode: "element"` or `"coordinate"` for transparency
- Live smoke test verified: Safari URL bar — element tap → type → text confirmed in element tree ✅

---

## v11.9.2 (2026-04-13)

### Critical Fix
- fix(runner): `ios_type` focus transfer bug (#43) — `typeText()` no longer steals focus from the user's selected field
- When a field already has `hasFocus`, skip the redundant `tap()` that was resetting focus to the first field
- Only tap to focus when NO field has focus (strategy 2 fallback)
- Live simulator smoke test: Safari URL bar — tap, type, text verified in element tree ✅

### Process
- First release with mandatory live simulator smoke test before publish

---

## v11.9.1 (2026-04-13)

### Critical Fix
- fix(runner): `ios_type` regression — typeText now throws on failure instead of silently returning success
- Focus detection upgraded: scans all input types (textFields, secureTextFields, searchFields) for `hasFocus` before falling back to firstMatch
- HTTP `/type` handler propagates errors (returns 500 with message instead of false 200 OK)
- `ios_wait_idle` 404 was stale runner binary — auto-rebuild on version change resolves this

---

## v11.9.0 (2026-04-13)

### Critical Fix
- fix(packaging): Bundle XCTest runner Swift source in wheel — `pip install specterqa-ios` now includes the runner, auto-builds on first session
- Previous versions only included runner in sdist (tar.gz), not the wheel (.whl) that pip installs

### Runner Build Pipeline
- Runner source now packaged at `specterqa.ios.runner_source` with `RUNNER_SOURCE_DIR`, `SOURCES_DIR`, `BUILD_SCRIPT` constants
- `session_manager._rebuild_runner()` resolves source from installed package first, falls back to repo root for development
- Version marker ensures runner rebuilds automatically after `pip install --upgrade`

### Test Suite
- fix(test): Click stderr access in test_build_prints_progress_message
- 962 passed, 0 failed, 0 "pre-existing" exceptions

---

## v11.8.1 (2026-04-12)

### Fixes
- fix(test): Fix Click stderr access in test_build_prints_progress_message — test suite now 962 passed, 0 failed

---

## v11.8.0 (2026-04-12)

### Critical Fix
- fix(bridge): Route ALL observability through XCTest HTTP bridge — fixes ios_logs, ios_perf, ios_crashes, ios_network returning empty during XCTest sessions
- Root cause: `simctl spawn` reports "device not booted" during active XCTest sessions; HTTP bridge is the only working channel

### New Swift Runner Endpoints
- GET /perf — mach_task_basic_info + task_threads (RSS, virtual memory, thread count, CPU time)
- GET /logs — in-process ring buffer (500 entries) with UIApplication lifecycle notifications
- GET /crashes — XCUIApplication.state + responsiveness probe + error log buffer
- GET /network — reachability probe (cross-process URL interception is an iOS limitation)

### Runner Build Pipeline
- fix(build): Auto-rebuild runner when package version changes — version marker + staleness check
- build.sh checks Swift source timestamps against cached binary
- Eliminates stale xctestrun causing 404s on new endpoints

### Python Bridge-First Fallback
- All MCP handlers try runner HTTP bridge first, fall back to Python-side monitors
- Response includes source: "bridge" or source: "simctl" for transparency
- 961 tests passing, 24 new tests for bridge + cache invalidation

---

## v11.7.0 (2026-04-12)

### Features
- feat(mcp): `ios_perf` tool — real-time CPU %, RSS memory, thread count for app under test
- feat(mcp): `ios_memory` tool — detailed memory breakdown via footprint (dirty, swapped, clean, physical footprint)
- feat(mcp): `ios_network` tool — network activity from CFNetwork log parsing (URL, method, status) + nettop bandwidth (bytes in/out, throughput)
- feat(network): `NetworkInspector` upgraded from stub to real implementation with CFNetwork log watcher + nettop background thread

### Fixes
- fix(perf): Thread count on macOS — replaced `ps -o nlwp=` (Linux-only) with `ps -M` line counting
- Tool count: 24 → 27
- 16 new tests (942 total passing)

---

## v11.6.0 (2026-04-12)

### Features
- feat(mcp): `ios_logs` tool — real-time app console logs from iOS Simulator (level, category, pattern filters, 100-entry cap, summary stats)
- feat(mcp): `ios_crashes` tool — crash detection from .ips files in DiagnosticReports (exception type, backtrace, app running status)
- Both monitors auto-start/stop with session lifecycle
- Tool count: 22 → 24

---

## [Unreleased]

## v11.5.0 (2026-04-12)

### Critical
- fix(runner): `ios_press_key("tab")` no longer crashes the XCTest runner — two-strategy mitigation (label scan + coordinate fallback) mirroring the existing return key fix

### High
- feat(mcp): Element Resolver v2 — auto-refresh on cache miss eliminates 29% stale-cache tap failures; scored matching (exact > prefix > substring) prevents greedy label mismatches
- fix(mcp): `ios_screenshot()` outputs JPEG (quality=85) instead of lossless PNG — 3-5x payload reduction, fits within MCP message limits
- feat(runner): `isHittable` tracked in ElementDescriptor — non-hittable elements auto-fallback to coordinate tap with warning

### Medium
- feat(runner): Auto-recover from app backgrounding after tap (Safari link trap no longer kills sessions)
- feat(mcp): Session state machine (idle → running → crashed) with health probes and clear recovery instructions
- feat(mcp): New tools — `ios_wait_idle` (element tree stabilization), `ios_app_state` (lifecycle check), `ios_dismiss_sheet` (swipe-down dismiss)
- feat(runner): `POST /appearance` endpoint via XCUIDevice — `ios_set_appearance` works during active sessions
- feat(runner): `POST /idle` endpoint — polls element tree stability for idle-wait
- feat(runner): `GET /app_state` endpoint — exposes app lifecycle state

### Low
- fix(mcp): `ios_start_recording` creates fresh ReplayRecorder (recording scope now works correctly)
- fix(mcp): Session state machine prevents BrowserStack zombie state after runner crash
- feat(replay): `_exec_long_press` uses identifier-first resolution matching `_exec_tap`
- 46 new tests (891 total passing)

---

## [11.4.0] — 2026-04-10

### Added
- `feat(mcp)`: `accessibilityIdentifier` support in `ios_tap` — find elements by exact identifier match
- `feat(mcp)`: coordinate-based tap in `ios_tap` — tap at explicit x,y screen coordinates
- `feat(replay)`: `element_identifier` field, `_find_by_identifier()`, `tapOnIdentifier` Maestro shortcut
- `test`: 32 new tests for identifier and coordinate tap features

---

## [11.3.0] — 2026-04-08

### Changed
- All diagnostic `print()` calls in production source replaced with
  `logging.getLogger()` / `logger.debug()` / `logger.info()` — no more
  debug noise to stdout unless the caller configures a handler.
  Affected: `som_runner`, `sim_driver`, `wda_driver`, `project_injector`,
  `drivers/simulator/driver`, `replay`, `cli/commands` (internal paths only;
  user-facing CLI output via `console.print` and `plain` mode `print(file=stderr)`
  is unchanged).
- Bare `except Exception:` catches audited across all 22 affected modules.
  Catches that could be narrowed now use specific exception types
  (`OSError`, `json.JSONDecodeError`, `ET.ParseError`, `ImportError`, etc.).
  Intentionally broad catches (plugin boundaries, ObjC bridges, background threads,
  capability probes) retain `except Exception:` with a `# noqa: BLE001` annotation,
  a comment explaining why, and `logger.debug` to surface failures.
- `pyproject.toml` now includes `classifiers`, `keywords`, and `[project.urls]`.
- `conftest.py` added: session-scoped `fresh_install` fixture replacing the
  hardcoded `/tmp/specterqa-ios-fresh` path used by integration tests.

### Fixed
- `tests/test_integration_smoke.py`: `TestMaestroExampleParses` and
  `TestMCPServerProtocol` tests now use the `fresh_install` fixture path
  instead of a hardcoded `/tmp` directory that only worked after an external
  manual install step.

---

## [11.2.2] — 2026-04-08

### Fixed
- `replay.py`: `validate-replay` no longer crashes on bare-string Maestro steps
  (e.g. `- swipe_back` without a mapping key).
- `mcp/server.py`: `ios_start_session` and `ios_stop_session` no longer raise
  on missing optional keys in the session registry.
- `pyproject.toml`: missing `mcp` extra dependency restored so `specterqa-ios-mcp`
  entry point is installable.

---

## [11.2.1] — 2026-04-08

### Changed
- Full lint and format sweep (ruff check + ruff format) — no logic changes.

### Fixed
- 24 pre-existing test failures resolved (mock signature mismatches, import paths,
  assertion typos introduced in prior test additions).

---

## [11.2.0] — 2026-04-07

### Added
- 179-test verification suite across three new test files:
  - `tests/test_v11_features.py` — 68 tests covering every v11 feature.
  - `tests/test_adversarial.py` — 68 tests for malformed input, races, and
    resource exhaustion.
  - `tests/test_integration_smoke.py` — 43 cross-module integration tests
    (record → save → replay, MCP stdio protocol, JSON output, package structure).

### Fixed
- `replay.py`: `handle_wait` clamped to `[0, 30]` seconds to prevent `ValueError`
  from `time.sleep` on negative durations.
- `replay.py`: `validate-replay` now handles bare-string Maestro step aliases.

---

## [11.1.0] — 2026-04-07

### Added
- `specterqa-ios ci --parallel N` flag: run N replays simultaneously via
  `ThreadPoolExecutor`.
- `specterqa-ios ci --json-output PATH`: structured `results.json` for CI dashboards.
- `specterqa-ios doctor`: full environment diagnostics (Xcode, Python, simulator,
  runner, WDA, API key, license).
- Example replay YAML files (`examples/01-smoke-test.yaml` through
  `04-visual-regression.yaml`).
- Maestro YAML compatibility aliases in `replay.py`:
  `tapOn`, `assertVisible`, `assertNotVisible`, `inputText`, `waitFor`.

### Changed
- `--reuse-runner` is now the default in CI mode (opt out with `--no-reuse-runner`).
- Stale runner cleanup runs between replays in CI mode.

---

## [11.0.0] — 2026-04-07

### Added
- WKWebView support: hybrid apps with embedded web content now testable.
- Conditional branching in replay YAML:
  `if_element_visible`, `if_not_element_visible`, `skip_to`.
- Visual regression diffing: `visual_diff` checkpoint action stores before/after
  screenshots in evidence and reports pixel-change percentage.
- Runner reuse across replay steps (avoids redundant XCTest runner restarts).
- 19 MCP tools exposed (up from 14 in v10).
- `ios_execute_shell` MCP tool for running arbitrary simctl commands.

### Fixed
- All dogfood gaps from the Example Reader Project 27-agent run resolved.

---

## [10.1.0] — 2026-04-06

### Fixed
- Return key delayed crash: `press_key("return")` no longer triggers a
  post-action crash on simulators with slow keyboard animation.
- `simctl` calls during an active session no longer raise `RuntimeError`.

---

## [10.0.0] — 2026-04-06

### Added
- PoolIQ v2 runner improvements merged:
  - BSD sockets transport for XCTest runner IPC.
  - Snapshot query API for element-tree polling.
  - Crash guards in runner process management.

---

[Unreleased]: https://github.com/SyncTek-LLC/specterqa-ios/compare/v11.3.0...HEAD
[11.3.0]: https://github.com/SyncTek-LLC/specterqa-ios/compare/v11.2.2...v11.3.0
[11.2.2]: https://github.com/SyncTek-LLC/specterqa-ios/compare/v11.2.1...v11.2.2
[11.2.1]: https://github.com/SyncTek-LLC/specterqa-ios/compare/v11.2.0...v11.2.1
[11.2.0]: https://github.com/SyncTek-LLC/specterqa-ios/compare/v11.1.0...v11.2.0
[11.1.0]: https://github.com/SyncTek-LLC/specterqa-ios/compare/v11.0.0...v11.1.0
[11.0.0]: https://github.com/SyncTek-LLC/specterqa-ios/compare/v10.1.0...v11.0.0
[10.1.0]: https://github.com/SyncTek-LLC/specterqa-ios/compare/v10.0.0...v10.1.0
[10.0.0]: https://github.com/SyncTek-LLC/specterqa-ios/compare/v9.0.0...v10.0.0
