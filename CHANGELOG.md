# Changelog

All notable changes to SpecterQA iOS are documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Version numbers follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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

---

## [16.0.0a5] — 2026-05-02 (alpha — pydantic runtime dep correctness)

### Fixed
- **P1: `pydantic` declared as runtime dependency** (was dev-only). Journey + license packages import pydantic at module load; a fresh `pip install specterqa-ios` would `ModuleNotFoundError` without this fix.

---

## [16.0.0a4] — 2026-05-02 (alpha — SimDrive 1.0 Cycle 1: journey runner + license/cloud + LapsApp scaffold)

**Status:** Cycle 1 of the SimDrive 1.0 build. Three parallel coding agents
delivered 251 Python tests and 38 Xcode tests. Atlas integration pass merged
25 new error codes, registered the `run_journey` MCP tool, wired `simdrive run`
and `simdrive ci` CLI subcommands, and bumped the version.

### Added

- **Journey runner package** (`simdrive/src/specterqa_ios/journey/`) — Components
  1, 2, 3, 8 of SimDrive spec: YAML schema + validator, Persona model, AI agent
  loop with vision-first observe/act, success-criteria evaluator, CI orchestrator.
  Exposes `run_journey()` as the core execution entry point.
- **License package** (`simdrive/src/specterqa_ios/license/`) — NaCl-signed license
  keys, trial activation, 7-day offline grace, entitlement tier model
  (trial/solo/pro/team/enterprise), cloud CLI helpers. Adds `pynacl>=1.6.2`
  dependency.
- **Cloud API scaffold** (`simdrive/src/specterqa_ios/cloud/`) — FastAPI-based
  license + recording API with R2 stub storage, JWT auth, Stripe webhook skeleton,
  and rate-limiting groundwork.
- **LapsApp Xcode scaffold** (`LapsApp/`) — SwiftUI test-host app (iOS 17+,
  XcodeGen) with 4 feature areas: Search, Settings, Appearance, CrashTrigger.
  Build and test verified against iPhone 16e iOS 26.2 sim.
- **`run_journey` MCP tool** registered in `server.py:_TOOLS` — drives a
  YAML journey against any active session via Claude. License-gated.
- **`simdrive run` and `simdrive ci` CLI subcommands** — wired in `server.py:serve()`.
  Both call `check_entitlement()` before proceeding. `run` dispatches to
  `run_journey()`; `ci` dispatches to `journey.ci.run_ci()`.
- **`ClaudeLLMClient`** (`journey/claude_client.py`) — Anthropic-SDK-backed
  implementation of the `LLMClient` Protocol. Uses `claude-opus-4-7`, tracks
  cumulative cost via `cost_usd` property.

### Test additions

- 137 Python tests — journey package (schema, persona, criteria, runner, CI)
- 76 Python tests — license + cloud packages
- 6 Python tests — ClaudeLLMClient (all mocked, no real API calls)
- 38 Xcode/Swift tests — LapsApp feature unit + UI tests
- **Total Cycle 1 new tests: 257 (219 Python + 38 Swift)**

### Pending for Atlas before Cycle 2

- Real `SIMDRIVE_PUBLIC_KEY_HEX` keypair needs Chairman generation and injection
  into `license/public_key.py`. Current public key is a placeholder — license
  signing/verification will fail in production until this is set.
- Live smoke against TestKitApp deferred to Cycle 4 dogfood pass.
- Cloud API requires database + R2 credentials before deployment.

---

## [16.0.0a3] — 2026-04-28 (alpha — Maurice's a2 dogfood feedback, P0 plumbing fixes)

**Status:** plumbing fixes from `.specterqa/dogfood/v16.0.0a2-maurice.md`. The
v16 vision-first design was right; a2's runner-lifecycle and screenshot-delivery
layer was not. a3 fixes the load-bearing plumbing.

### Fixed (P0)

- **P0-3: `ios_observe` no longer ships inline base64.** The screenshot is now
  written to `/tmp/specterqa-observe-<uuid>.jpg` and the tool returns
  `screenshot_path` instead. Drops the 188 KB / 64 KB JSON envelope problem
  that exceeded the MCP cap (~25 KB) at every quality level. Vision agents read
  the path with their native file-read tool. (`server.py` handle_observe)
- **P0-4: One coord space — logical points.** `ios_observe.device_w` and
  `device_h` are now LOGICAL POINTS (e.g. 390×844 for iPhone 12), matching
  the runner's `:8222/health` and what UIKit hit-tests against. Pixel
  dimensions of the saved JPEG are surfaced separately as
  `screenshot_w` / `screenshot_h`. `ios_act` `normalized=true` denormalizes to
  the same point space via a new `_resolve_device_logical_points` helper that
  consults a known-device → points map (iPhone 12/13/14/15/16/17 families)
  with a 390×844 default fallback.
- **P0-1: `handle_start_session` ensures the sim is booted AND blocks on
  health.** Previously a Shutdown sim was handed straight to runner deploy,
  and a healthcheck timeout was warning-logged while the function fell through
  to BackendSelector and returned `status:ok` with a dead `runner_url`.
  v16.0.0a3 (a) calls `_ensure_sim_booted` before deploy and returns a
  structured `sim_boot_failed` error if boot fails, (b) returns a structured
  `runner_deploy_health_timeout` error on healthcheck failure with an iOS
  26.0 SDK-mismatch hint and `retryable=False`. `_ensure_sim_booted` was
  promoted to module level (was nested inside `handle_app_relaunch`).
- **P0-1.5: orphan MCP daemon reaping on serve() startup.** `_reap_orphan_daemons`
  pgrep's `specterqa-ios-mcp`, SIGKILLs any PID other than the current process
  before binding the MCP transport. Eliminates the silent piggyback Maurice
  identified where a fresh daemon was reading state from an orphan's
  port-bound runner. Override via `SPECTERQA_ALLOW_MULTI_DAEMON=1` for
  intentional multi-daemon setups.

### Fixed (P1)

- **P1-1: `ios_session_status` now reflects daemon reality.**  v16.0.0a2 lied:
  any daemon with a non-None `_backend` reported `"healthy"` even when the
  runner had silently died. v16.0.0a3 distinguishes:
  `idle` (no session configured this lifetime) | `deploying` (in-flight) |
  `healthy` (live `/health` returned 200 just now) | `degraded` (backend
  exists but `/health` failing) | `failed` (last deploy errored). Probe is
  live, not cached. Also exposes `daemon_pid` so agents can correlate with
  pgrep output.

### Deferred (still — Maurice's a2 §P0-2, §P1-2, §P1-3)

- **P0-2 — `ios_observe` returns frozen pixels when runner is stale.** Likely
  resolved by the orphan-daemon reaping (P0-1.5) — the cached-frame
  scenario depended on an orphan runner. If it persists in a3 dogfood,
  add a refuse-on-stale check to the runner Swift `/screenshot` route.
- **P1-2 — `ios_logs` returns count:0 on a dead sim** instead of erroring.
- **P1-3 — `backend="xctest"` returns "not available" while `backend="auto"`
  deploys xctest fine.** Code-path unification.

### Verified before ship

- 519 unit tests pass / 31 skipped / 0 fail
- Live verification on iPhone 17 Pro / iOS 26.3 — see `.specterqa/v16-handoff.md` for the heavy-test record

---

## [16.0.0a2] — 2026-04-28 (alpha — Maurice/Example Reader dogfood feedback)

**Status:** integration-bug fixes from Maurice's v16.0.0a1 dogfood
(`.specterqa/dogfood/v16.0.0a1-maurice.md`). The design landed correctly in
a1; a2 is plumbing fixes.

### Fixed

- **Bug #1 (P0): `RunnerProcess` no longer terminal in FAILED state.**
  After a failed xctest deploy (e.g. iOS 26.2 `Unknown application` bug
  causing healthcheck timeout), every subsequent `ios_start_session(backend='xctest')`
  re-raised the cached error until MCP server restart, because
  `ios_stop_session` only cleared the registry on success paths. v16.0.0a2
  auto-recovers: on `deploy()` against a FAILED instance, kill any stale
  child process, clear `_last_error`, transition to IDLE, fall through to
  the normal deploy flow. One MCP-restart round-trip per session avoided.
  (`runner_process.py:233-262`)

- **Bug #2 (P1): `ios_dismiss_first_launch_alerts` moved back to trial tier.**
  The tool was at indie tier in v16.0.0a1, but on iOS 26+ simctl cannot
  pre-grant `notifications` (OS-restricted) and `ios_act` cannot reach
  SpringBoard alert windows (outside target-app coord scope). Trial users
  on v16 + iOS 26.x had NO path past the first-launch notifications prompt
  — a hard regression vs v15.x. The capability is a workaround for an
  Apple limitation, not a premium feature; trial tier restored.
  (`tier_gate.py`)

- **Bug #3 partial (P1): runner Swift now bails out fast and continues in
  degraded mode when XCUIApplication binding fails.** On iOS 26.2 the
  `Unknown application` LaunchServices bug causes XCTest's internal
  `isApplicationStateKnown` waiter to never fulfill; previously the runner
  burned ~30-60s on retries before the Python `_wait_for_health(90s)`
  gave up. v16.0.0a2 attempts launch ONCE with a 5s wait, logs the failure
  as `NSLog ERROR` (not buried in `XCTDebug`), and falls through to start
  the HTTP server anyway. Coordinate-based `/tap` (cg_event_direct in
  TouchInjector) does not depend on app binding and continues to work in
  degraded mode. (`runner/Sources/SpecterQARunner.swift:82-115`)

### Deferred to v16.0.0a3 / v16.0.0 stable

- Bug #3 full: the `/elements` and `/source` endpoints currently return
  host-chrome only (Action / Volume Up / Sleep-Wake) when the runner is
  in degraded mode. v16.0.0a3 will add an explicit 503 + degraded marker
  and surface the bind-failure reason via `/health`.
- AX-backend `ios_act` for SwiftUI list cells (Maurice §P2)
- Apple radar for the iOS 26.2 `Unknown application` regression
- Replay v2 (visual SSIM diff) + recording v2

### Verified

- 526 unit tests pass / 24 skipped / 0 fail (a2 changes are runner-Swift
  + tier-map + Python state-machine; existing tests cover all three).

---

## [16.0.0a1] — 2026-04-28 (alpha pre-release; replay rewrite + recording rewrite still pending)

**Status:** Maurice/Example Reader dogfoodable. The vision-first primitives work
end-to-end on iOS 26.x; the AX-tree selector layer is gone from the MCP
surface. Replay v2 (visual SSIM diff) + recording v2 are deferred to
v16.0.0a2 / v16.0.0; pre-v16 replay YAMLs do not run on this alpha.

Install: `pip install specterqa-ios==16.0.0a1` (note the `a1` suffix —
this is a PEP 440 alpha pre-release; `pip install specterqa-ios`
without the version pin still installs v15.2.0 stable).

## [16.0.0] — IN PROGRESS (Phase A + B-Lite landed; Phases C–F pending)

**This is a strategic redirection, not a tweak.** The accessibility-tree
selector layer that v15.x driving was built around has been doing negative
work for vision-capable agents on iOS 26.x SwiftUI — lossy, brittle, and
crash-prone (Maurice/Example Reader v15.2.0 dogfood, three runner deaths in one
session via `XCUIElementQuery[label]` ambiguous-match `NSException`). v16.0
pivots to **vision-first**: the agent reads the screenshot, picks
coordinates, dispatches via a unified action verb. Same shape as Anthropic
Computer Use, OpenAI Operator, claude-in-chrome.

See `.specterqa/dogfood/v15.2.0-direction-proposal-maurice.md` for the
strategic rationale and `.specterqa/dogfood/v15.2.0-runner-stability-patch-maurice.md`
for the tactical patches that v16 makes redundant by deletion.

### Added (Phase A — landed)

- **`ios_observe`** — vision-first observation primitive. Returns
  `screenshot` (base64 PNG), `device_w`/`device_h`, `app_state`,
  `captured_at`, and `reliable_targets`: the small set of elements with
  explicit `accessibilityIdentifier` set by the developer. Everything
  without an explicit identifier is intentionally absent — the screenshot
  is the truthful representation; `reliable_targets` is an opt-in
  semantic helper for the rare elements the developer marked scriptable.
- **`ios_act`** — unified action dispatcher. Single tool with
  `action.kind ∈ {tap, type, swipe, key, scroll, long_press, drag}`.
  Coordinate-primary; `identifier` permitted on `tap`/`long_press` as an
  opt-in semantic helper. `normalized=true` treats coordinates in
  [0.0, 1.0] as fractions of device dimensions. Label-based selectors
  are NOT supported.
- **ObjC bridge for `runOnMain` defense-in-depth.**
  `runner/Sources/SpecterQAObjCBridge.{h,m}` adds a Swift-callable
  `@try`/`@catch` shim. Phase B will delete the dominant throw site
  (XCUIElementQuery selector layer); the bridge stays as a safety net
  for any other XCTest API that can throw.
- **Tier enforcement on the new primitives.** `ios_observe` and
  `ios_act` are both `trial`-tier — observation and input are free.

### Folded in from v15.x branches (un-shipped, now part of v16)

- **PR #79 — Tier enforcement across the MCP tool surface.** Every tool
  now declares a minimum license tier; bypass via
  `SPECTERQA_LICENSE_BYPASS=1` (CI/dev) with module-level WARNING.
- **PR #78 — SEC-HIGH-005 JWT offline grace decoder hardening.**
  Hoisted imports, 2KB payload size cap, `TypeError` added to
  `_check_offline_grace` exception tuple.

### Pending (Phases B–F)

- **Phase B — demolition.** Delete `runner/Sources/SpecterQAElementQuery.swift`,
  strip `findByLabel`/`findByIdentifier`/`waitForElement` call paths from
  `TapRoute`/`TypeRoute`/`SwipeRoute`. Delete legacy MCP tools:
  `ios_screenshot`, `ios_elements`, `ios_tap`, `ios_long_press`, `ios_swipe`,
  `ios_swipe_back`, `ios_type`, `ios_press_key`, `ios_dismiss_keyboard`,
  `ios_wait_idle`, `ios_wait_for_element`, `ios_capture_state`,
  `ios_action_with_logs`. Net 49 → ~22 tools.
- **Phase C — replay rewrite.** New YAML schema based on coordinate
  actions + visual SSIM diff. PNG references stored alongside YAML.
  Per-step region mask + threshold override. Migration tool for
  pre-v16 replay YAMLs.
- **Phase D — recording rewrite.** Capture screenshot + tap coordinate
  per step; optional OCR'd text near tap for human readability.
- **Phase E — README rewrite, migration guide, real-sim integration tests
  for the new primitives.**
- **Phase F — PR, QualityAtlas certification, DeployAtlas tag/PyPI publish.**

### Out-of-band signals (unchanged in v16)

`ios_logs`, `ios_logs_tail`, `ios_perf`, `ios_memory`, `ios_network`,
`ios_crashes`, `ios_app_state`, `ios_session_status`, `ios_doctor`,
`ios_devices`, `ios_apps`, `ios_get_capabilities`, `ios_app_relaunch`,
`ios_dismiss_first_launch_alerts`, `ios_pre_grant_permissions`,
`ios_set_appearance`, `ios_dismiss_springboard_alert` — all kept as-is.
These are operational primitives that don't depend on the selector layer.

### Breaking changes

- Pre-v16 replay YAMLs (asserting `expect_elements: [...]`) will not run
  under v16 once Phase C lands. A migration tool will be provided.
- v15.x label-based `ios_tap(label=...)` callers will see `tool not found`
  once Phase B deletes `ios_tap`. Migrate to `ios_act({kind: 'tap', x, y})`.

---

## [15.2.0] — 2026-04-27

### Fixed (iOS 26.x XCTest runner survives — Maurice/Example Reader dogfood cure)

**The cure for v15.1.0 Issue #2 — replaces detection-only mitigation with the
actual root-cause fix.**

- **Runner test method now uses XCTWaiter pattern instead of CFRunLoopRunInMode
  polling.** Maurice's v15.1.0 dogfood showed the in-sim runner test process
  was killed by iOS 26's XCTest infrastructure within seconds of entering its
  run loop, even though the HTTP server was healthy and replied to `/health`
  twice. Root cause: iOS 26's `XCTRuntimeIssueDetectionManager` flags a test
  method that polls on the main thread for tens of seconds without emitting
  test events as "stuck/hung" and SIGKILLs it. The xcresult footer
  (`*** If you believe this error represents a bug…`) is xcodebuild's
  signature for that termination.

  v15.2.0 swaps the `while … { CFRunLoopRunInMode(.defaultMode, 2.0, false) }`
  pattern in `runner/Sources/SpecterQARunner.swift` for an
  `XCTWaiter.wait(for: [XCTestExpectation], timeout: maxDuration)` pattern.
  A background dispatch queue fulfills the expectation on any of:
  (a) `HTTPServer.stopSemaphore` signaled (POST `/shutdown` or `/stop`),
  (b) the stop-sentinel file `/tmp/specterqa_runner_stop` appears, or
  (c) `server.isRunning` flips false. `XCTWaiter.wait` pumps the main run
  loop while waiting — so `runOnMain()` (CFRunLoopPerformBlock +
  CFRunLoopWakeUp) continues to dispatch XCUITest calls without changes —
  but XCTest sees the test method as legitimately blocked on an
  expectation, not as a stuck polling loop, so the runtime-issue detector
  no longer fires.

  This is the same pattern WebDriverAgent uses; battle-tested across iOS
  versions including iOS 26.

- **Issue #1: redundant BackendSelector probe race after successful deploy.**
  When `backend='xctest'` was explicitly requested AND the deploy block had
  completed a successful healthcheck, the subsequent
  `BackendSelector(...).choose(requested='xctest')` was re-probing
  `:8222/health` and could observe a runner that died in the millisecond gap
  between deploy success and BackendSelector probe — producing a misleading
  `"Requested backend 'xctest' is not available on this system"` error
  immediately after a successful deploy. v15.2.0 instantiates `XCTestBackend`
  directly when `_mcp_runner_ref` is set; the BackendSelector path is
  reserved for the auto-select / non-xctest cases.

### Verified

- iPhone 17 Pro / iOS 26.2 (UDID `955B199B-4F30-47F7-84E2-A9EE39E46D99`) —
  `ios_start_session(backend='xctest')` returned `status:ok`, runner stayed
  healthy through a 60s `/health` poll loop (12/12 polls returned 200).
  This is Maurice's exact suggested release-gate test from his dogfood §5.1.
  See `tests/integration/test_xctest_runner_stability_live.py`.

### Known caveats (carried from v15.1.0; remain open)

- Issues #3 (replay hardwired to xctest port irrespective of session backend),
  #4 (sibling sim shutdown), and #5 (no `--sdk` flag on `runner build`) from
  the v15.1.0 dogfood remain open and will land in v15.3+.

---

## [15.1.0] — 2026-04-21

### Changed (UX philosophy)

- **Retry-first, forgive-transients:** `ios_capture_state`, `ios_tap`, `ios_action_with_logs`,
  and `ios_app_state` now retry once transparently on Apple-side transient failures (runner HTTP
  5xx, connection refused, sim state flicker) before surfacing any error to the caller. A 2s sleep
  separates the first and second attempts. Only the second failure is returned as a user-visible error.
- **`_verify_sim_alive` polls for 15s before declaring a session dead.** On first Shutdown
  detection the function enters a retry-poll loop (1s sleep between checks). This gives SpringBoard
  5-10s to respawn before returning `sim_shutdown_during_session`. Detection is forgiving by default.
- **`_restart_runner_for_relaunch` pre-checks runner HTTP health for up to 10s** before kicking the
  36-42s recovery path. If the runner becomes healthy during the pre-check window, recovery is
  skipped entirely — the sim-Shutdown signal was transient.

### Added

- **`sim_settle_timeout: float = 10.0`** param on `ios_start_session`. Smart wait only when the
  sim just booted: reads `lastBootedAt` from `simctl list devices --json`, sleeps only the
  remaining delta (e.g. if the sim booted 3s ago, waits 7s). No wait when sim has been booted
  longer than `sim_settle_timeout` seconds. Mitigates the SpringBoard startup race on fresh sim boot.
- **`retryable: bool`** field on transient error payloads. Errors representing Apple-side transients
  (`sim_shutdown_during_session`, `installcoordinationd`, `Runner did not become healthy`, etc.) now
  carry `retryable: true`. Fatal errors (bad UDID, permissions denied) do not set this field.

### Known caveats

- **`ios_tap` is not idempotent under retry.** When the first tap dispatches successfully but the
  runner returns a transient error before the result reaches the caller, the auto-retry will
  fire the tap a second time. The tradeoff is deliberate — short-lived sim hiccups recover on
  retry — but callers performing irreversible actions (delete, send, confirm purchase) should
  probe state first rather than relying on `ios_tap` alone.
- **`ios_action_with_logs` is not idempotent under retry, and the first attempt's log window is
  discarded.** If the first attempt executes the UI action but a transient error is returned,
  the retry re-executes the action and re-collects logs from a fresh cursor — the original
  log window is lost. Same destructive-action caveat as `ios_tap` applies. Callers needing a
  guaranteed-once-only action should call the underlying primitive without the retry wrapper.

---

## [15.0.0] — 2026-04-20

### BREAKING CHANGES

- **`ios_start_session` gains `wait: bool = True` param.** Default behavior (synchronous, blocks
  until runner is healthy) is unchanged. Async callers should adopt `wait=False` +
  `ios_wait_for_session` for sub-2s response. (Maurice Issue 3)

### Added

- **Env propagation fallback via `~/.specterqa/config.toml` (Maurice Issue 1):**
  New CLI command `specterqa-ios mcp enable-physical` writes `[mcp] allow_physical_device = true`
  to `~/.specterqa/config.toml`. The MCP server reads this config on every gate check, so physical
  device support works even when Claude Code doesn't propagate the MCP server's `env:` block.
  New Python module `specterqa.ios.config` with `_check_physical_opt_in()` (env OR config OR
  keychain), `write_physical_opt_in()`, and `_read_physical_opt_in()`.

- **Diagnostics block in `ios_get_capabilities` (Maurice Issue 1):**
  The `physical` device entry now includes `"diagnostics": {"env_var_seen_by_process": bool,
  "config_file_value": bool, "keychain_value": bool}` so users can see exactly where the gate
  is blocking when `opt_in_active` is false.

- **Async session start: `wait=False` + `ios_wait_for_session` + `ios_session_status`
  (Maurice Issue 3):**
  `ios_start_session(wait=False)` returns immediately with `{status: "deploying", deploy_id,
  health_url, estimated_ready_in_s: 45}`. Call `ios_wait_for_session(deploy_id, timeout_s=120)`
  to block until healthy. `ios_session_status()` returns `{status, elapsed_ms, udid}` without
  blocking — useful for progress polling.

- **`auto_recover: bool = False` session option (Maurice Issue 9):**
  When True on `ios_start_session`, a detected mid-session simulator shutdown triggers automatic
  re-boot + runner re-deploy. Documented in tool description.

- **Sim shutdown detection (Maurice Issue 9):**
  Every MCP tool that hits a `ConnectionError` from the runner now checks simulator state via
  `_check_sim_state_for_udid()`. When Shutdown is detected, returns structured
  `{error: "sim_shutdown_during_session", action_needed: "boot_and_reauth", sim_state,
  recovery_hint}` instead of a generic timeout error.

- **`ios_dismiss_first_launch_alerts` MCP tool (Maurice methodology section 4):**
  Coordinate-taps the "Don't Allow" or "Allow" button on iOS permission alerts.
  `decline=True` (default) taps "Don't Allow" at `(120, 500)` scaled to actual screen size.
  `permissions=["notifications", ...]` iterates through multiple alerts.

- **`specterqa-ios install-clean <app-path> [--udid <udid>]` CLI command
  (Maurice methodology section 3):**
  Copies the app to a temp dir, strips `PlugIns/*.xctest`, `Frameworks/XCTest*.framework`,
  `Frameworks/Testing.framework`, and `Frameworks/libXCTest*.dylib`, then calls `simctl install`.
  Prevents `libXCTestBundleInject` from loading bundled unit tests into the host process.

- **Orphan xcodebuild reaper (Maurice Issue 6):**
  `_reap_orphan_xcodebuild(port=8222)` scans for xcodebuild processes holding port 8222 via
  `lsof -i :8222 -t`, sends SIGTERM then SIGKILL with 5s grace. Called on `ios_start_session`
  entry before deploying a new runner.

- **`_kill_runner_graceful(process, grace_s=5)` helper (Maurice Issue 6):**
  Used in `ios_stop_session` and all deploy-error paths to ensure TERM → KILL cleanup.

### Fixed

- **Issue 3 / Maurice Issue 4 — runner_source/ rebuild path:** `_rebuild_runner` now uses
  `importlib.resources.files('runner')` (v14+ wheel layout) with correct fallback chain. No more
  `SessionError: Runner Xcode project not found` on version bump.

- **Issue 4 / Maurice Issue 5 — AX backend iOS 26 content-group heuristic:** `_init_content_group`
  already had the position-probe fallback from a prior fix; this release ensures
  `_content_group_failed = True` is set when both heuristic and probe fail, so `get_elements()`
  raises `AXContentGroupNotFoundError` instead of silently returning hardware chrome (mute/volume
  buttons).

- **Issue 5 / Maurice Issue 6 — Stale xcodebuild processes on session failure:** Added
  `_reap_orphan_xcodebuild` call on `ios_start_session` entry. `_kill_runner_graceful` used on
  stop/error paths.

- **Issue 6 / Maurice Issue 7 — `ios_app_relaunch` Shutdown handling:** Already present from
  14.0.3; `auto_recover` option added for session-level automatic recovery.

- **Issue 7 / Maurice Issue 8 — `ios_apps` plist parser:** Changed default to
  `simctl listapps -j <udid>` (JSON); plist fallback retained for older Xcode.

### Removed

- Nothing. All v14.x MCP tool surface preserved.

---

## [14.0.3] — 2026-04-20

### Added

- **Physical device opt-in via `device_type="physical"` + `SPECTERQA_ALLOW_PHYSICAL_DEVICE=1`:**
  `ios_start_session` now accepts `device_type` as an explicit parameter (default `"simulator"`).
  When `device_type="physical"` is passed without the env var set, the tool returns an opt-in
  error with instructions rather than silently failing or proceeding. When the env var is set to
  a truthy value (`1`, `true`, or `yes`), the call proceeds to the existing physical device path
  in `session_manager`. Simulator path is unchanged.

- **`ios_get_capabilities()` discovery tool:** New MCP tool that returns the SpecterQA version,
  supported backends (`xctest`, `ax`), and a `device_types` array. The `physical` entry includes
  `available: true`, `default: false`, `opt_in_env: "SPECTERQA_ALLOW_PHYSICAL_DEVICE"`, and
  `opt_in_active` reflecting the current env state. Agents should call this before starting a
  session to discover what device targets are available.

### Changed

- **`_restart_runner_for_relaunch` has a 120s outer timeout:** A `time.monotonic()` ceiling is
  checked at each recovery phase. If the total recovery time exceeds 120s, the function returns
  an error string with recovery instructions and stops the runner in a `finally` block. Previously
  worst-case stalls (simctl/xcodebuild hang) could consume 370s+ with no ceiling.

### Fixed

- **Concurrent MCP call race during recovery:** `_restart_runner_for_relaunch` now acquires
  `_session_lock` on entry, so only one recovery runs at a time per MCP server. The three-global
  update sequence (`_mcp_runner_ref`, `_session`, `_backend`) happens inside the lock, preventing
  concurrent callers from observing partial state.

- **`import json as _json_w` moved out of Shutdown poll loop:** The import was inside the
  per-iteration loop body; it is now at function top, colocated with the other module-level
  imports in `_restart_runner_for_relaunch`.

### Docs

- **`recovery` field documented in `handle_app_relaunch` docstring:** Callers now know to expect
  `recovery: "runner-restart"` on the recovery path (~30-45s) vs absence of the key on the happy
  path (<2s).

- **README `Physical device support (experimental)` section:** Covers what it does, how to opt in
  (env var + `device_type="physical"`), and known limitations (xcodebuild rough edges, no
  stability guarantee, simulator is the supported path).

---

## [14.0.2] — 2026-04-19

### Fixed

- **app_relaunch fails with "No devices are booted" after capture_state (P1):**
  `ios_start_session(backend="xctest")` deployed a `RunnerProcess` on `:8222`
  (stored in `_mcp_runner_ref`), then created a `TestSession` which called
  `_find_free_port()` — returning `:8223` because `:8222` was occupied — and
  launched a *second* xcodebuild process. Two xcodebuild instances targeting the
  same simulator caused the first to die; its teardown shut down the simulator;
  subsequent `simctl` calls (app_relaunch, capture_state) failed with
  `"No devices are booted."` Fix: when `_mcp_runner_ref` is RUNNING and `clone=False`,
  the xctest path reuses it directly as `_session` (skips `TestSession._deploy_runner`).
  `_mcp_runner_ref` is cleared on `ios_stop_session`. New regression tests in
  `tests/test_mcp_session_persistence.py` assert no teardown fires between calls.

- **4 pre-existing live-state test failures gated properly:**
  `TestBackendBehavioralContract` tests now handle `XCTestBackend.is_available()`
  as an instance method (not classmethod) and gracefully skip when `AXBackend`
  cannot be instantiated (missing `pyobjc-framework-ApplicationServices`). Discovery
  tools tests already had `pytest.skip` guards; they now skip correctly when no
  booted simulator is present.

- **`_NamespacePath.insert` error on Python 3.11+ namespace packages:**
  `specterqa.ios.__init__._ensure_namespace()` now falls back to `.append()` when
  `_NamespacePath` doesn't support `.insert()`, fixing isolated imports of
  `specterqa.ios.cli.commands` (e.g. in standalone test runs, `specterqa-ios --version`).

### Added

- **`specterqa-ios --version` flag:** `ios_command_group` now has
  `@click.version_option(package_name="specterqa-ios")`. Output: `specterqa-ios, version X.Y.Z`.
  Unit tests in `tests/test_cli_version.py` (CliRunner, hermetic).

### Docs

- **`RELEASES.md`:** Release sequence table for v14.x including the `v14.0.0b1` tag
  gap (publish workflow correctly failed; fix folded into v14.0.0).

---

## [14.0.1] — 2026-04-19

### Fixed

- **P0 deploy conflict in MCP xctest path (v14.0.0 regression):** `ios_start_session(backend="xctest")` was broken end-to-end. The MCP layer pre-deployed a `RunnerProcess` on `:8222` (healthy, logged `v14: MCP runner deployed and healthy`), but `session_manager._kill_stale_runners()` immediately killed it (treating the owned process as an orphan). The session then waited 60 s for health and timed out. Fix (Option A): `_kill_stale_runners` now calls `RunnerProcess.owned_pids()` and skips any xcodebuild PID that belongs to a live registry entry. The new `owned_pids()` classmethod is the only addition to `RunnerProcess`.

---

## [14.0.0] — 2026-04-19

**Major release — MCP-first consolidation.** Consolidates three parallel XCTest-runner deployment paths into a single `RunnerProcess` lifecycle class, introduces 5 AI-debugging MCP primitives, restructures the wheel mechanics to eliminate the B1.x regression surface, and adds 2 end-to-end CI dogfood tests.

### Added

- `RunnerProcess` class — single owner of build/deploy/start/stop/healthcheck/port-alloc. Thread-safe (state machine + lock). Shared per `(udid, port)` via class-level registry.
- `RunnerDeployError` exception — loud XCTest failure with actionable `suggested_fix`. No silent fallback to AX.
- 5 new MCP tools for the AI debugging loop:
  - `ios_app_relaunch(bundle_id, app_path?)` — reinstall/relaunch user app without tearing down runner. No `app_path`: terminate+launch (<2s, `mode="terminate-launch"`). With `app_path`: simctl install+terminate+launch (~15s, `mode="reinstall-launch"`). Returns `{bundle_id, udid, elapsed_ms, foreground_verified, mode}`.
  - `ios_logs_tail(since_last_call, filters…)` — incremental log stream with per-session ISO timestamp cursor. First call returns the last ~50 entries as the initial boundary. Returns `{logs, cursor, since_ms, count}`.
  - `ios_capture_state(include?)` — bundles screenshot + elements + logs + app_state + perf in one MCP call. `include=["screenshot","elements","logs"]` slims the payload. Returns `{screenshot?, elements?, logs?, app_state?, perf?, captured_at}`.
  - `ios_action_with_logs(action, log_window_ms)` — atomic: action + logs fired during it. Supports `tap`, `long_press`, `type`, `swipe`, `press_key`. Returns `{action_result, logs, log_window_ms, action_elapsed_ms}`.
  - `ios_promote_session_to_test(name, path?)` — saves session as replay YAML + auto-validates; in-repo `./replays/` default. `validation="passed"` + `can_replay=true` = ready for CI.
- `runner/__init__.py` — `runner/` is now a proper Python package discovered by setuptools. Eliminates the B1.x class of wheel-packaging bugs.
- Two E2E CI dogfood tests (`tests/dogfood/`):
  - `test_ci_replay_dogfood.py` — CI replay workflow: fresh install + `runner build` (CI-always); record/save/validate/replay against TestKitApp (live-sim only).
  - `test_ai_debug_dogfood.py` — AI debugging workflow: tool registration + count >= 43 (CI-always); full 5-tool exercise against TestKitApp (live-sim only).
- `dogfood-ci.yml` GitHub Actions workflow — `dogfood-ci-always` job runs on every PR and push to main.
- `rm -rf build dist` step in publish workflow.

### Changed

- `session_manager._deploy_runner` delegates to `RunnerProcess`.
- `_runner_source_dir()` in `cli/commands.py` now uses `importlib.resources.files('runner')` as primary resolution — works in both installed wheels and editable installs. Eliminates B1.5 regression class.
- `_compute_runner_source_hash()` in `session_manager.py` updated to use `importlib.resources` for runner source discovery.
- MCP tool count: 41 → 43 (net; removed 3 and added 5).
- `pyproject.toml` uses `packages.find` (replaces long package-data glob list). Version bumped to `14.0.0`.
- `MANIFEST.in` simplified (no more `runner_source` mirror).
- `setup.py` slimmed to a minimal shim (build_py override removed).

### Removed — BREAKING

- `ios_start_runner` — shut down target simulator 100% of the time. No replacement: use `ios_start_session(backend="xctest")` which auto-deploys runner.
- `ios_stop_runner` — same sim-kill defect. No replacement: runner teardown is handled by session lifecycle.
- `ios_save_replay` — deprecated since v13.2.0. Use `ios_stop_recording(name=...)`.
- `src/specterqa/ios/runner_source/` (build-artifact mirror — source of B1.x bugs).
- `setup.py build_py` override.

### Security

- `ios_promote_session_to_test` sanitizes `name` (whitelist `[a-zA-Z0-9._-]+`; rejects slashes, `..`, leading dot) and resolves `path=` against `Path.cwd()` (rejects escapes).

### Migration

Users on v13.x upgrading to v14.0.0:

- Replace any `ios_start_runner` call with `ios_start_session(backend="xctest")`. The session manager handles runner deploy correctly.
- Replace any `ios_stop_runner` call with `ios_stop_session()`.
- Replace any `ios_save_replay(name)` with `ios_stop_recording(name=name)`.
- Internal imports of `specterqa.ios.runner_source.*` will break. These were never public API — use the top-level `runner/` package.

---

---

## [13.2.1] — 2026-04-18

Hotfix release addressing 5 release blockers in v13.2.0 surfaced by Example Reader dogfood (Maurice Carrier, 2026-04-18).

### Fixed
- **B1**: Removed stale `RequestParser.swift` references from `src/specterqa/ios/runner_source/SpecterQARunner.xcodeproj/project.pbxproj` — fresh `pip install` users no longer hit "Build input file cannot be found" on first `runner build`.
- **B2**: `_needs_rebuild()` now uses a SHA-256 content-hash of `Sources/` + `project.pbxproj` instead of the version-string match. Patch releases that don't change Swift sources skip the rebuild.
- **B3+B4**: CLI `validate-replay` now accepts `element_identifier` and `tapOnIdentifier` (the recorder already writes them; the engine already reads them; MCP `ios_validate_replay` already accepted them — only CLI was out of sync).
- **B9**: MCP `ios_start_session(backend="xctest")` now deploys the runner via `xcodebuild test-without-building` before probing `:8222/health`. Restores 13.1.0 behavior. Without this fix, MCP recording was offline in 13.2.0.
- **B1.5**: `_runner_source_dir()` now finds the runner inside installed wheels (`pkg/runner_source/`), not just the dev-tree layout (`pkg_root/runner/`). Without this, every fresh `pip install` user's `specterqa-ios runner build` failed with "xcodebuild: error: '<cwd>/SpecterQARunner.xcodeproj' does not exist".

### Added
- `CHANGELOG.md` now ships in the wheel (was missing in 13.2.0).
- 5 new gap tests that would have caught these blockers pre-release: `test_wheel_buildable.py` (fresh-venv wheel install + runner build), `test_rebuild_trigger.py` (hash-based rebuild gate), `test_recorder_validator_roundtrip.py` (recorder→validator schema sync), `test_mcp_xctest_session.py` (end-to-end MCP backend deploy), `test_packaging.py::test_changelog_in_wheel` + `test_pbxproj_no_requestparser_reference`.

---

## [13.2.0] — 2026-04-17

### Added
- 7 new MCP tools: `ios_list_replays`, `ios_replay`, `ios_validate_replay` (record-once, replay-free workflow reachable from MCP); `ios_doctor`, `ios_devices`, `ios_apps`, `ios_license_status` (zero-arg environment observation — first tool to call when a session fails unexpectedly).
- `IOSBackend` Protocol (`backends/protocol.py`) — every backend now conforms to one interface.
- `RetryPolicy` (`backends/retry_policy.py`) with FAST/ACTION/IDLE route classes + circuit breaker that replaces the per-call health probe.
- `ios_dismiss_springboard_alert` + `ios_pre_grant_permissions` tools for SpringBoard-level permission prompts (reported from Example Reader dogfood).
- `docs/troubleshooting.md` — compatibility matrix for simctl privacy grants and known iOS 18.4 limitations.
- `scripts/generate_llms_txt.py` + `make llms` target + instructions-sync regression test to prevent tool-surface drift.

### Changed
- `HTTPServer.swift` refactored from a 1,196-LOC god class into `HTTPServer.swift` (socket + dispatch only, ~470 LOC) + 23 per-route files under `runner/Sources/Routes/` implementing a `Route` protocol.
- Backend selection consolidated behind `BackendSelector.choose()` — the previous parallel selection path in `mcp/server.py::handle_start_session` is gone (~150 LOC deleted from server.py).
- AX backend now walks sibling AXWindows so SwiftUI `.sheet`-presented UIKit content (e.g. Example Reader Add Library flow) enumerates correctly (reported from Example Reader dogfood).
- Agent instructions rewritten: first-session 5-call loop, failure recovery decision tree, AX-vs-XCTest guidance, consolidated wait-tool decision tree, removed stale coordinate-fallback typing advice.
- `ios_dismiss_keyboard` is now a real registered MCP tool (runner's `/dismiss_keyboard` endpoint was already implemented; the Python wiring was missing).
- Runner Swift sources dedup: `runner/Sources/` is the authoritative source; `src/specterqa/ios/runner_source/Sources/` is populated at build time via `setup.py` `build_py` override.
- `.github/workflows/publish.yml` uses `PYPI_API_TOKEN` token auth instead of broken trusted-publishing / GitHub Packages paths.

### Fixed
- AX hydration race: first `ios_elements()` right after `ios_start_session` no longer returns empty under race (warmup poll added to session start).
- Tool-count mismatches in README/llms.txt/landing-page (was "19 tools"; actual count now surfaced; regression test enforces sync going forward).

### Removed
- ~3,600 LOC of unreachable code (`engine/`, `exploratory/`, `parallel/`, `webhooks/`, dead backend modules).
- `runner/Sources/RequestParser.swift` and dead `RouterV2` stub (unused).
- `HANDOFF.md` moved out of repo root (now `.specterqa/internal/HANDOFF.md`).

### Deprecated
- `ios_save_replay` — prefer `ios_stop_recording(name, keep_buffer=False)` or `ios_replay(name)`. Will be removed in v14.0.0.

### Package extras
- `pip install specterqa-ios[browserstack]` — BrowserStack device provider (optional).
- `pip install specterqa-ios[orchestration]` — WebDriverAgent + Set-of-Marks CLI commands (optional).
- Default install no longer eager-loads these heavy modules.

### Tests
- +107 regression tests covering: MCP tool layer arg validation + return shape, runner HTTP endpoint edge cases, AX/XCTest Protocol behavioral contracts, replay MCP tools, and discovery tools.
- 40/40 live smoke on CI (`iPhone 16 Pro` sim, macos-14).

### Known limitation
- iOS 18.4 SpringBoard notification alerts remain unreachable from the AX tree — `ios_pre_grant_permissions` covers location/camera/etc. but NOT notifications on 18.4. Documented in `docs/troubleshooting.md`.

### Credits
- Example Reader dogfood report (Maurice Carrier, 2026-04-17) surfaced the AX sheet-enumeration gap, SpringBoard alert limitation, and the AXHTTPServer port leak that shipped in v13.1.1.

---

## v13.1.1 (2026-04-16)

### Fix AXHTTPServer port 8222 socket leak

#### Fixed

- `AXHTTPServer.stop()` now calls `server_close()` after `shutdown()` — port 8222 is released cleanly so subsequent `ios_start_session` calls can re-bind. Without this fix, a second session with `backend="ax"` would fail with `[Errno 48] Address already in use`. Reported from Example Reader dogfood report 2026-04-17.

#### Regression test added

- `tests/regression/test_ax_server_restart.py` — three tests covering port release after stop, three consecutive restart cycles, and a static source guard asserting `server_close()` is called.

---

## v13.1.0 (2026-04-16)

### Fix Xcode 26 XCTest runner crash — 40/40 smoke tests passing

#### Fixed

- **XCTest runner crash on Xcode 26 during UI transitions** (sheets, modals, keyboard+tab switches, notification cascades, `app.snapshot()` during transitions). Root cause: `XCSetDebugLogger` symbol lives in `XCTestCore.framework`, re-exported by `XCTest.framework`; `dlsym` on the shim handle does not walk re-exports. Fixed by resolving via `RTLD_DEFAULT`.
- Added WDA-proven `XCUIApplication.doesNotHandleUIInterruptions` method swizzle via new `SpecterQASwizzler.{h,m}` ObjC bridge.
- Added `XCTDisableAttributeKeyPathAnalysis = true`.
- Hoisted `applyCrashMitigations()` to `class func setUp()` so mitigations fire before XCTest initializes loggers.
- Smoke test isolation: `_tap_tab()` y-coordinate corrected (822 → 840); added `_ensure_tab()` helper with sentinel-element verification and retry; per-class `_restart_app()` cleanup for tests that leave the app in a dirty state.

#### Impact

- Was 22/40 live smoke tests passing. Now 40/40.
- 5/5 new `SpecterQACrashMitigationTests` Swift unit tests pass.
- Runner survives previously-crashing scenarios: `TestKeyboardDuringTabSwitch`, `TestSheetOverTextField`, `TestExample ReaderNotificationCascade`, `TestNotificationFloodResilience`, `TestXCTestCrashMitigation`.

#### Verified on

- Xcode 26.2, iOS 26.3 Simulator, iPhone 17 Pro
- Runner PID stable across all 40 tests (no crashes)

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
