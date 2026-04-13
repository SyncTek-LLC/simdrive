# Changelog

All notable changes to SpecterQA iOS are documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Version numbers follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
