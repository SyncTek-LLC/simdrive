# Changelog

## [1.0.0a6] — 2026-05-04

### Documentation
- **README front-door rewrite.** First 200 lines now lead with: one-line summary, 30-second `--offline-dev` quickstart, agent-first MCP framing, key differentiators. The "what is this" question now answers itself in the first scroll.
- **Known limitations + workarounds section.** Documents four behaviors observed in the 1.0.0a2 Example Reader dogfood: `type_text` first-character drop (workaround: `tap_first`), SSIM threshold advisory vs `structural_checks` (the actual regression gate), `dismiss_sheet` system-sheets-only limitation (workaround: `swipe` for SwiftUI half-sheets), `set_appearance` respring caveat.
- **`docs/LIMITATIONS.md` extended** with full detail on all four dogfood-observed limitations.
- **Migration note** (`docs/MIGRATION.md`) for users landing from `specterqa-ios` references: tool name mapping, PyPI history, new 1.0 additions.

### Packaging / metadata
- PyPI `description`, `keywords`, and `classifiers` audited for **agent-discoverability**. The agent-first MCP framing now surfaces in package metadata so MCP clients scanning for "iOS automation" can find simdrive.
- Added keywords: `automation`, `xcuitest`, `appium-alternative`, `ai-testing`, `sampling`, `xcode`.
- Added classifier: `Topic :: System :: Testing`.
- Updated `description` to lead with "MCP-native iOS simulator + real-device automation. Agent-first" framing.

### Added
- `tests/test_readme_quickstart.py` — regression test pinning quickstart commands' presence in README first 100 lines, and absence of stale/misleading strings.

### Source
INIT-2026-546. Closes the polish loop after 1.0.0a3 (dogfood fixes), 1.0.0a4 (MCP sampling), 1.0.0a5 (httpx defensive pin).

---

## [1.0.0a5] — 2026-05-04

### Fixed (defensive)
- **Pin `httpx<1.0` to defend against `mcp` ecosystem pre-release leak.** The published `mcp==1.27.0` declares `httpx>=0.27.1` with no upper bound. `pip install --pre simdrive` would resolve to `httpx 1.0.dev3` (a real pre-release on PyPI) which breaks `httpx-sse` and the MCP transport layer. Caught by DeployAtlas pre-publish smoke for 1.0.0a4 (INIT-2026-544). Pin removable once upstream `mcp` adds its own upper bound.

### Added
- Regression test `tests/test_packaging_deps.py::test_httpx_pinned_below_1_0` so this defensive pin can't be silently removed.

### Source
INIT-2026-545. Defensive follow-up to 1.0.0a4 from DeployAtlas's smoke.

---

## [1.0.0a4] — 2026-05-04

### Changed (BREAKING for direct journey-runner consumers)
- **`run_journey` is now `async`** and **`LLMClient.call` is now `async`**. Direct API consumers must `await run_journey(...)` (or wrap with `asyncio.run(...)`). The standalone `simdrive run` and `simdrive ci` CLIs handle this internally — no user-visible change for CLI users.
- **`ClaudeLLMClient.call` is now `async`** and wraps the blocking Anthropic SDK call in `asyncio.to_thread(...)`.

### Added
- **`MCPSamplingLLMClient`** (`simdrive.journey.mcp_sampling_client`) — new LLM client that delegates to the connected MCP client via `session.create_message(...)`. simdrive's MCP `tool_run_journey` now uses this — **no `ANTHROPIC_API_KEY` required when called via MCP** (Claude Code, Cline, or any sampling-capable MCP client supplies its own LLM and credentials).
- **`SimdriveError(code="mcp_sampling_unavailable")`** raised when `tool_run_journey` is invoked outside an MCP context (e.g. an MCP client that doesn't support sampling). Recovery hint points to `simdrive run` standalone CLI.

### Fixed
- **MCP flow no longer requires an Anthropic API key.** All 31 MCP tools, including `run_journey`, work with `pip install simdrive` (no extras) when the driving agent supports MCP sampling. Per Chairman directive 2026-05-04.

### Packaging
- `anthropic>=0.30` confirmed in `[project.optional-dependencies]` only. `pip install simdrive` (no extras) works for MCP. `pip install simdrive[claude]` adds the Anthropic SDK for the standalone `simdrive run` / `simdrive ci` CLI paths.

### Source
INIT-2026-544. Architectural follow-up to 1.0.0a3 (INIT-2026-543) — agent-first per Chairman directive.

---

## [1.0.0a3] — 2026-05-04

### Fixed
- **`run_journey` license gate (P0):** Wired `simdrive trial` and `simdrive license` subcommands into the CLI dispatcher (previously `cmd_trial_start` was defined but unreachable). Added `--offline-dev` flag (and `SIMDRIVE_OFFLINE_DEV=1` env var) that issues a 14-day Ed25519-signed local dev license without contacting `cloud.simdrive.dev`. Cloud unreachable now raises a clear `LicenseError(code="cloud_unreachable")` with a recovery hint pointing to `--offline-dev`. Dogfooders are no longer blocked when cloud infra is offline.
- **`version` drift false positive (P1):** `_disk_version()` was reading `importlib.metadata.version("specterqa-ios")` (old wheel name from before the rename) and triggering `_simdrive_warning` on every tool response. Changed to `simdrive`. The drift detector now compares apples to apples.
- **`tool_run_journey` contract divergence (P1):** `LicenseError` now inherits from `SimdriveError`, so the MCP server's existing exception wrapper catches it and returns a proper `{ok: false, error: {code, message, details}}` envelope instead of wrapping it as a generic `internal` error. Direct-Python and MCP callers now see the same shape.
- **Stale rename strings (P2):** Swept `ios_observe` → `observe`, `ios_start_session` → `start_session`, `ios_devices` → `devices`, `ios_stop_recording` → `stop_recording`, `ios_start_recording` → `start_recording`, `ios_list_replays` → `list_replays` across error recovery messages. Replaced `_HELP_TEXT` banner `"specterqa-ios — SpecterQA for iOS MCP server. (codename: simdrive)"` with `"simdrive — MCP-native iOS simulator driver"`. `--version` now prints `simdrive <version>`. Module docstrings drop the "(Internal codename: simdrive.)" framing.

### Added
- **`simdrive trial start`** subcommand: `simdrive trial start --email <e> [--offline-dev] [--license-path <p>]`
- **`simdrive license show`** / **`simdrive license path`** subcommands.
- **`SIMDRIVE_OFFLINE_DEV=1`** env var for sandboxed/CI use.
- **Dev Ed25519 keypair** embedded in package (`license/public_key.py:DEV_VERIFY_KEY_HEX` + `DEV_SIGNING_KEY_HEX`). Validator only accepts dev-key-signed licenses with `subject == "dev-trial"` — dev key cannot self-issue prod licenses.

### Source
Reported by Maurice Carrier (Example Reader iOS), 2026-05-04 dogfood report. INIT-2026-543.

---

## [1.0.0a2] — 2026-05-02 (alpha — post-WDA cleanup + audit-driven fixes)

### Fixed
- **P1: `run_ci()` call-arg mismatch in `server.py`** — API drift from cycle 1 integration; runtime error when CI journey endpoint hit
- **P1: missing test deps in [dev]** — `anthropic`, `fastapi`, `sqlalchemy`, `hypothesis`, `moto[s3]`, `pytest-cov` were used in tests but not declared; `pip install simdrive[dev]` now collects all tests
- **5 asserts in `journey/criteria.py`** converted to explicit `raise ValueError` (asserts get stripped under `PYTHONOPTIMIZE`)
- **105 ruff F401/E501 errors** auto-fixed across `simdrive/src/`, `simdrive/tests/`, `scripts/`
- **Stray `print()` calls** in `server.py` converted to logger calls
- License metadata aligned to Elastic-2.0: pyproject.toml previously
  declared `license = "MIT"` but `simdrive/LICENSE` was MIT and root
  `LICENSE` was Elastic-2.0 — three files in three states. Standardized
  on Elastic License 2.0 across pyproject, simdrive/LICENSE, and root
  LICENSE. SimDrive 1.0 ships as a commercial product: free for
  personal/internal use, prohibits offering as a competing managed
  service. (LapsApp at repo root remains separately MIT-licensed.)

### Added
- `Python 3.13` classifier in `pyproject.toml`

### Security
- pip CVE-2026-3219 — upgrade venv pip to 26.1

---

## [1.0.0a1] — 2026-05-02 (alpha — SimDrive 1.0 first alpha)

This is the first alpha of the **SimDrive 1.0** line. It supersedes the
former `specterqa-ios` 16.x line: PyPI distribution name reverted to
`simdrive` (matching the public brand) and Python import path is now
`from simdrive.X import Y`. **Migration for existing installs:**
`pip uninstall specterqa-ios && pip install simdrive`.

### Added — Journey runner + license + cloud foundation (Cycle 1)
- **`run_journey` MCP tool + `simdrive run` / `simdrive ci` CLI** — agent loop with persona + journey YAML, budget enforcement, faked or real `LLMClient` (Anthropic SDK wrapper at `simdrive.journey.claude_client`)
- **License system** — Ed25519-signed offline-verifiable keys with 7-day grace, `simdrive trial start`, `simdrive license activate`, `simdrive license status`
- **Cloud private API skeleton** — FastAPI app with `/v1/trials`, `/v1/licenses/{activate,status}`, `/v1/recordings`, R2Stub storage

### Added — LapsApp dogfood platform (Cycle 1+2+3)
- New `LapsApp/` Xcode project at repo root: 12 feature areas (Settings, Light/Dark, Crash-Trigger, Search, OAuth-mocked, WebView reader, Activities infinite-scroll, Forms async-validation, Sheets+modals, PerfStress 1000-row, Offline mode toggle, Multi-app launcher), 5 primary tabs, 98 Swift tests
- 20-journey YAML corpus + 3 personas under `LapsApp/.simdrive/`

### Added — Cloud production-ready (Cycle 2)
- **Real R2 storage** — boto3-backed `R2Client` (env-driven), R2Stub fallback for local dev
- **Per-tier monthly run quotas** — Solo 50 / Pro 250 / Team 1000; `POST /v1/runs/increment` enforces with 429+`Retry-After`
- **`GET /v1/licenses/usage`** — returns runs_used / runs_limit / percent_used / period dates
- **`GET /health`** for Railway healthcheck
- **Auth hardening** — expired/tampered/missing-bearer rejection paths tested; per-route required-tier gates
- **Railway deploy config** — `simdrive/cloud_deploy/{Procfile, railway.toml, .env.example, README.md}`

### Added — Production hardening (Cycle 3)
- **Observability package** `simdrive.observability.{logger, metrics, tracing}` — `SIMDRIVE_DEBUG=1` toggles JSON-shaped logs; counters + histograms (`journey_runs_total`, `tap_latency_ms`, `observe_latency_ms`, `claude_call_cost_usd`); span-context tracing
- **Perf benchmark suite** at `simdrive/tests/perf/` with 2× regression CI gate; baselines committed
- **Edge-case coverage** for runner, validator, recordings boundaries
- **`Recovery:` line audit** — every error constructor across `errors.py` and per-package modules carries a copyable next-step
- **Docs** — `OBSERVABILITY.md`, `PERFORMANCE.md`, `RECOVERY.md`

### Added — Production credentials
- **Production Ed25519 license-signing public key** injected (private key held in Chairman's secure storage; configured as `SIMDRIVE_LICENSE_PRIVATE_KEY` env var on the Railway license server)

### Fixed
- `recordings.py` DELETE 204 + response-model `AssertionError` at router init (introduced and fixed in Cycle 2+3)
- `pydantic`, `email-validator`, `pynacl` declared as runtime deps (were missing from previous pyproject)
- Stale repo-root `pyproject.toml` removed (named the package `specterqa-ios@16.0.0a5` and shadowed the canonical `simdrive/pyproject.toml`)

### Changed
- Public brand and PyPI distribution name: `specterqa-ios` → `simdrive`
- Python import path: `from specterqa_ios.X` → `from simdrive.X`
- Major version reset to `1.0.0a1` to match the SimDrive 1.0 launch trajectory (the 17.x line was never published to PyPI)
- 5-tab navigation in LapsApp (Home / Activities / Search / Blog / Settings) — avoids the iOS TabView "More" overflow

### Test totals at this alpha
- Python: ~488 tests pass + 3 perf benchmarks with 2× regression gates
- Swift (LapsApp): 95 unit + 3 UI = 98 tests pass on iPhone 16e iOS 26.2
- Smoke: `scripts/smoke_journey_cycle1.py` exits 0 plain and with `SIMDRIVE_DEBUG=1`

### Pending for 1.0.0 (next alphas)
- Real-device input via WebDriverAgent (full parity scope; in-flight)
- Stripe webhook signature verification on `/v1/licenses/activate`
- Cycle 4 dogfood-to-perfection (5 passes including Example Reader re-validation)

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
