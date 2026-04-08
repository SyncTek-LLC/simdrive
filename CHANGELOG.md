# Changelog

All notable changes to SpecterQA iOS are documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Version numbers follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [Unreleased]

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
- All dogfood gaps from the Palace Project 27-agent run resolved.

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
