# Changelog

## [1.0.0b2] — 2026-05-20

**Production-readiness hardening sprint.** Closes the daylight between b1 (first publishable beta) and customer-grade reliability. Focused on the three surfaces where racy I/O meets real simulators — HID dispatch, WDA bridge, and recording integrity — plus key rotation and defense-in-depth quota enforcement. No new MCP tools; no public-API breaks. Existing licenses validate unchanged.

### Added — resilience primitives

- **`simdrive._wait` polling helper** — `wait_until(predicate, timeout, …)` and async `await_until(...)` for condition-based waits. Sync + async variants, exponential backoff, structured `WaitTimeoutError` with a description so timeouts are diagnosable.
- **Typed HID/keyboard/focus errors** — new `HIDUnavailableError`, `KeyboardNotReadyError`, `FocusNotReadyError`, `WaitTimeoutError` subclasses of `SimdriveError`. Existing `hid_unavailable()` factory preserved for backward compat.
- **`wda_recovery_exhausted` error** — surfaces from `WdaClient` when exponential-backoff retries hit `max_transport_attempts` (default 3). Includes the full attempt history for diagnostics.

### Added — license & cloud paranoia

- **Multi-key license validator** — `TRUSTED_PUBLIC_KEYS: list[tuple[str, str]]` enables key rotation without forcing a client upgrade for existing licenses. Payloads carry an optional `key_id` field; legacy payloads route to the first trusted key. New `KeyRotationError` when an unknown key id appears.
- **Trial clock-skew gate** — `assert_trial_clock_trustworthy()` refuses to grant the 7-day offline grace window if the system clock moved backwards >6h or forward >30d from `last_known_server_time`. Forces a fresh cloud check instead of silently granting access on a tampered or wildly-drifted clock.
- **Cloud privacy scrub** — `cloud/privacy.py:scrub_body()` masks sensitive fields (`email`, `license_key`, `token`, `signature`, `bearer`) before logging or storing any HTTP response body. Wired into `cloud/auth.py`.
- **Defense-in-depth local quota check** — every MCP tool dispatch now runs `check_local_quota(tool_name, session)` before the handler body. Cheap, network-free; reads the session-local snapshot from the auth/refresh bootstrap. Cloud-side `make_quota_gate` remains the authoritative enforcer.

### Changed — WDA bridge

- **Per-phase `httpx.Timeout`** on the WDA HTTP client lifecycle (connect=5s, read=caller, write=10s, pool=5s). Session-level hangs no longer block forever.
- **Exponential backoff on transport errors** — `WdaClient(max_transport_attempts=3)` retries `httpx.TransportError` with backoff 0.2s → 5s ×1.6. Structured logs per attempt (attempt #, trigger code, action, outcome). Backward-compatible default; existing tests pin `max_transport_attempts=1` where they assert the legacy `wda_unreachable` code.
- **Tightened Code 41 detection** — regex `Code[= ]41(?!\d)` rejects false positives like `Code=410`. Previously a permissive `Code[= ]41` could trigger entitlement-revoke recovery on unrelated 410 Gone responses.
- **Body-truncation + scrub in error logs** — WDA error bodies are now capped at 256 chars in logs; bodies from `/wda/typing` and `/wda/keys` are scrubbed entirely (no user input leaks into observability output).

### Changed — recording integrity

- **No more partial steps** — `Recorder.add_step()` drops the step and emits a `recorder.dropped_step_partial_capture` WARNING when either pre- or post-action screenshot fails (None / missing / zero-byte all count as failure). Return type widened to `Optional[int]`.
- **Drift detection hysteresis** — replay drift halt now requires **2 consecutive sub-threshold SSIM frames** before stopping, defeating false positives from a single noisy frame under sim load. DEBUG-level `replay.ssim_compare` event logged on every comparison for diagnosability.

### Changed — server.py hygiene

- **Surface HID failures** — `tool_type_text(clear_first=True)` no longer silently swallows `hid_inject.chord` / `act.press_key` failures. They now raise `HIDUnavailableError` / `KeyboardNotReadyError` so the agent sees the real cause instead of typing into an unfocused field. `tool_clear_field` keeps its `cleared=False` fallback for caller branching but logs the cause at WARNING.
- **Schema enforcement** — vestigial `getattr(s, "pixel_per_point_scale", None) or 1.0` and `getattr(s, "target", "simulator")` fallbacks removed in favor of direct attribute access. The `Session` dataclass already declares both fields with defaults; the getattr fallbacks were noise.
- **Named sleep constants** — five `time.sleep(0.6)` / `0.5` / `0.2` magic numbers in `server.py` and five 0.15s / 0.05s rate-limit sleeps in `act.py` promoted to module-level constants (`_KEYBOARD_SETTLE_SEC`, `_FOCUS_SETTLE_SEC`, `_ALERT_DISMISS_INTERVAL_SEC`, `_WINDOW_ACTIVATE_SETTLE_SEC`, `_PASTEBOARD_SETTLE_SEC`) with comments documenting which race each value defeats.

### Tests + CI

- **+359 new tests** across the sprint, total **1308 passing** (was 949 at sprint start). Distribution: Wave 1 wait_until/HID errors 26, Wave 1 WDA resilience 16, Wave 1 recorder integrity 8, Wave 1 license+cloud 46, Wave 2 integration 6, Wave 3 chaos 7, Wave 3 coverage push 250.
- **Coverage 76% → 82% overall** with hot-path modules at 85–100%: `sim.py` 100%, `act.py` 100%, `session.py` 100%, `observe.py` 97%, `device.py` 94%, `wda/client.py` 85%, `recorder.py` 85%. `server.py` 67% → 70% (full 80% would require running the MCP server in tests — deferred).
- **CI ratchet floor raised 65% → 80%** in `.github/workflows/simdrive-ci.yml`. Climb-to-85 plan documented in `simdrive/docs/COVERAGE_RATCHET.md`.
- **Sprint structure** — six hardening branches merged via no-ff into `hardening/INIT-2026-549-prod-readiness`: `wait-until-helper`, `wda-resilience`, `recorder-integrity`, `license-cloud-paranoia`, `chaos-test`, `coverage-gate`, plus integrated Wave 2 work directly on the trunk.

### Backwards compatibility

- Existing licenses (no `key_id` field) validate against `TRUSTED_PUBLIC_KEYS[0]` unchanged.
- `verify_key=` parameter on `validate_license` preserved with identical semantics when `trusted_keys=` is not also passed.
- `WdaClient(host, port)` ctor unchanged; new `max_transport_attempts=3` is a keyword default.
- `Recorder.add_step()` return type widened (`int` → `Optional[int]`); the one in-tree caller in `server.py` already guarded for `None`.
- No MCP tool schema changes. The 32-tool surface is unchanged.

---

## [1.0.0b1] — 2026-05-18

**First beta release.** Trial+paywall model live; bug-reproduction positioning. Six months of alpha development consolidated into a publishable, monetizable beta.

### Added — Business model

- **14-day free trial** — `simdrive trial start --email you@example.com` issues an Ed25519-signed local license valid for 14 days, full Pro feature access. Email+machine SHA-256 de-dupe prevents infinite re-trials.
- **License authentication** — `simdrive auth <license-key>` redeems a Polar-issued production license. Writes to `~/.simdrive/license.json`, validates against the embedded public key.
- **Paywall enforcement on every MCP tool** — all 32 MCP tools now gate on `check_entitlement()`. Trial users get full access; after trial expiry, `LicenseError` is raised with a structured `license_required` envelope containing `pricing_url`, `auth_command_hint`, and `trial_command_hint` so the MCP client (Claude Code, Cursor, Continue) surfaces a copy-pasteable recovery path to the user.

### Added — Positioning

- README, PyPI description, and `llms.txt` rewritten around the bug-reproduction use case: "Reproduce and validate iOS bugs in 60 seconds with Claude." Supporting capabilities (record/replay, journey runner, real device, perf baselines) get co-equal real estate.
- `simdrive/docs/HERO_DEMO_SCRIPT.md` — 60-second hero demo storyboard (Linear ticket → Claude drives sim → captures failure → engineer fixes → validates) ready for recording.

### Added — Release engineering

- New publish workflow: triggers on `simdrive-v*` tag pattern (was `specterqa-ios-v*`), gates on version-match + CHANGELOG-head + non-live pytest + fresh-venv install smoke, publishes via PyPI Trusted Publisher (OIDC, no static token).
- Production license-signing keypair rotated. Public key embedded in client; private key Fernet-encrypted in vault and bound to the simdrive-license-api Cloudflare Worker for license issuance on Polar webhook events.

### Changed

- Test suite: added autouse dev-trial fixture (`simdrive/tests/conftest.py`) so paywall-gated tools work in CI without manual setup. 949 tests passing, 74.77% coverage on hot-path modules.
- CI: simdrive-ci runs the full non-live test suite with a 65% coverage ratchet floor; per-module climb-to-80 plan in `simdrive/docs/COVERAGE_RATCHET.md`.
- Security baseline: pinned `requirements.lock`, `pip-audit --strict`, CodeQL Python, gitleaks all on every PR.

### Removed

- "no API key required" framing in README/PyPI/llms.txt (contradicted the trial+paywall model).

## [1.0.0a13] — 2026-05-14

Ships the deferred a12 item: **record/replay parity on real device.** Sim
target had record_start / record_stop / replay / validate_replay / list_replays;
device target was explicitly stubbed (`recorder.py` line ~288: "Device: not
implemented"). a13 closes that gap with full parity, plus a per-target state
contract so replays refuse to run on the wrong device or OS major.

### Added — Device record/replay

**Recording on device**
- `record_start` on `target=device` sessions binds a recorder that intercepts
  every act tool call (tap/swipe/type_text/press_key/dismiss_sheet/clear_field)
  with a WDA screenshot via `s.wda_client.screenshot_any()` and a marks count
  via `annotate_device_screenshot`. Screenshots live under
  `~/.simdrive/recordings/<name>/screenshots/<step_id>.png` (same layout as sim).
- `record_stop` writes `recording.yaml` with the same schema as sim plus a
  device-specific `requires.device` block: `{udid, device_name, os_version, os_major}`.
- `Recorder.write_partial()` persists `recording.yaml.partial` if a mid-record
  step raises — preserves debug context across crashes.

**Replay on device**
- `replay <name>` against a device session: verifies the state contract first,
  then per-step takes a live WDA screenshot + observe, SSIM-compares against
  recorded screenshot, marks-count-compares against recorded marks_count, and
  only then dispatches the recorded action.
- SSIM threshold for device: **0.80** (sim stays at 0.85). Rationale: real
  device screenshots have hardware compositing jitter and anti-aliasing
  variance that sim doesn't; 0.80 still halts loudly on meaningful drift
  (the dogfood "23 blind taps at SSIM 0.014" failure mode fails by 57× margin).
- Marks-count drift halt: when `live_marks > 0` AND `live_marks / recorded_marks
  < 0.50`, halt with `marks_count_drift` in `drift_events`. The `> 0` guard
  prevents false-positives when observe annotation is unavailable (test envs).

**State contract enforcement**

| Field | Mismatch behavior |
|-------|-------------------|
| `requires.target` | **halt** (`replay_state_contract_failed`) |
| `requires.device.udid` | **halt** |
| `requires.device.os_major` | **halt** |
| `requires.device.os_version` (minor diff) | **warn** only, replay proceeds |
| `requires.device.device_name` | **warn** only (user may rename device) |
| `requires.app.bundle_id` | **halt** |

Closes the "23 blind taps at SSIM 0.014" failure mode — replays refuse to run
when the precondition isn't met (per `reference_simdrive_state_contract_request.md`).

### Changed — Tool schema markers

Seven tools flip from `(sim only)` → `(sim + device)`:
- `record_start`, `record_stop`, `replay`
- `validate_replay`, `list_replays`
- `lint_recordings`, `migrate_recording`

### New error codes

- `replay_drift_detected` — step `error` when SSIM < threshold or marks-count
  ratio drops below the floor.
- `replay_state_contract_failed` — listed in `reasons[]` when udid / os_major /
  target / bundle_id mismatch before step 1.
- `marks_count_drift` — appears in `drift_events[].kind`.

### Deferred to a14

- `cross_device_state_matches` journey criterion still scope-cut. The
  comparison API across two device-state snapshots isn't yet defined.

### Source

INIT-2026-542. Recording shape is a strict superset of a12 — pre-a13 sim
recordings still load via `RequiresBlock.from_dict` (forward-compatible).
20 new regression tests; 851 pass on the merged suite.

---

## [1.0.0a12] — 2026-05-14

Closes every item from the 2026-05-14 Example Reader iOS device-dogfood feedback —
five P0/P1 driver-path bugs and seven polish items. a12 turns the device
target into a first-class peer of the sim target.

### Fixed — Critical / High

**F-007 — `tap(stable_id=...)` on device no longer raises `AttributeError`**
Sim path emitted `Mark` dataclass instances; device path emitted dicts; the
resolver used attribute access (`m.stable_id`) which crashed on dicts. a12
canonicalises marks to `dict` end-to-end (sim path calls `.to_dict()` at every
write site) and the resolver uses a `_mark_attr` helper that handles both
shapes for safety. `tap(stable_id=)`, `tap(text=)`, `tap(mark=)`, and
`tap(stable_id_loose=)` all work uniformly on sim AND device.

**F-008 — observe coord-space invariant pinned to pixels on device**
The dogfood reported point/pixel flipping between consecutive observes on the
same screen. Root cause: `_ensure_screenshot_dims` on device sessions called
`observe.observe()` (sim Vision OCR path, unscaled points), then a subsequent
`tool_observe` returned pixel-scaled marks from `annotate_device_screenshot` —
two different coord spaces stored in the same session. `_ensure_screenshot_dims`
and the type_text post-observe now route through `tool_observe(target=s.target)`
so device sessions always go through the WDA `/screenshot` + pixel-scaling
path. Coord-space contract is documented at module level in `observe.py` and
`som_device.py`. Out-of-bounds marks (negative coords from system overlays
like `AdditionalDimmingOverlay`) are filtered with a debug log rather than
asserted, surfaced during Moes Max live validation.

**WDA Code 41 auto-recovery — mid-session entitlement loss**
If XCTDaemonErrorDomain returns `Code=41` or `Code 41` during any WDA call,
simdrive now logs a warning, calls `bootstrap.bootstrap_device(udid, ..., rebuild=True)`,
reloads the registry, updates the WDA client's host/port/session, and retries
the original request once. `SIMDRIVE_NO_AUTO_REBUILD=1` opts out. Per-call
retry counter `_recovery_attempt: int` prevents infinite loops.

**F-010 — orphan-session 404 auto re-acquire**
If WDA returns HTTP 404 on a `/session/<id>/...` path (an out-of-band script
called `POST /session` or `DELETE /session/<id>`), simdrive now calls
`open_session(self._last_bundle_id)` to acquire a fresh session id and retries
the original request once. Same per-call counter; same env opt-out.

**F-009 — `type_text` on device routes through WDA, never simctl**
Surfaced during code-tracing: `tool_type_text` device branch was correct, but
helpers it called (`_ensure_screenshot_dims`, `_record_act_step`, and two
explicit `observe.observe()` calls) defaulted to `target="simulator"` and
hit `simctl spawn <real-udid> screenshot` → "Invalid device". Four call
sites now pass `target=s.target` (or route through `tool_observe` for
device). Guard `assert s.target == "simulator"` added before every `act.*`
helper so a future device-leak fails loud instead of with a cryptic simctl error.

### Fixed — Medium / Low + new capabilities

**Per-target log-filter API (`predicate_kind`)**
`tool_logs` now accepts `predicate_kind: Literal["nspredicate", "regex", "substring"]`
defaulting to `"nspredicate"`. Sim NSPredicate routes to native `log show`;
device NSPredicate downgrades to substring with a WARNING log; regex and
substring kinds are explicit post-capture filters that work on both targets.
Closes the dogfood report that `processImagePath CONTAINS "Example Reader"` returned
zero lines on device.

**Tool-schema per-target parity markers**
Every MCP tool description now starts with `(sim only)`, `(device only)`, or
`(sim + device)` so an agent reading `tools/list` knows BEFORE calling
which target the tool supports. Twenty tools audited. `dismiss_sheet` is now
`(sim + device)` (a12 ships the device path). Record/replay/perf tools are
explicitly `(sim only)` until a13 record/replay-on-device lands.

**`SIMDRIVE_HTTP_DEBUG=1` verbose mode**
Set the env var to log every WDA HTTP call at INFO: method, path, request
body (truncated 2 KB), response status, response body (truncated 2 KB).
Module attr and env var both checked per call so monkeypatching from tests
and live env both work.

**`apps` includes `CFBundleVersion` as `build`**
`mcp__simdrive__apps` items now include `build` alongside `version`, matching
`xcrun devicectl device info apps` shape. Saves a round-trip when the agent
needs to identify a specific TestFlight build.

**`session_start(replace_existing=True)`**
Atomically end any existing session for the same UDID and start a fresh one
in a single round-trip. Without the flag, a UDID collision raises
`session_already_active` with the existing session id in the error details
(used to silently overwrite — now loud).

**`dismiss_sheet` on device via WDA swipe-down**
The "v0.2 coming" error is gone. Device branch performs the same 20% →
70% screen-height swipe-down as sim, routed through WDA with F-006 scale
conversion. Identical agent UX across both targets.

**devicectl "No provider was found" warning filter**
The cosmetic `No provider was found for this descriptor` line is stripped
from devicectl stderr when `returncode == 0`. Real failures still emit the
full stderr (the warning may be diagnostically relevant when the command
also failed).

### Deferred to a13

- Recording/replay-on-device — substantial standalone initiative.
- Cross-device-state-matches journey criterion — flagged at journey/criteria.py
  with `NotImplementedError` for now.

### Source

INIT-2026-542. Files: `simdrive/src/simdrive/server.py`, `som.py`,
`observe.py`, `device.py`, `diagnostics.py`, `session.py`,
`simdrive/src/simdrive/wda/client.py`, `wda/som_device.py`, `wda/bootstrap.py`,
`wda/errors.py`, plus 12 new test files under `simdrive/tests/test_a12_*.py`.
Total 64 new regression tests; 831 pass on the merged suite.

---

## [1.0.0a11] — 2026-05-13

Closes six findings from the 1.0.0a10 device-dogfood feedback (Example Reader iOS team,
Moes Max iPhone 17 Pro Max, iOS 26.4.2). a10's headline — "zero-config real-device
bootstrap" — gets to actually drive the device end-to-end in a11.

### Fixed — Critical / High

**F-005 — device input verbs are no longer broken**
After a successful `session_start(target="device")` in a10, every `tap` / `swipe` /
`type_text` / `press_key` / `observe` / `clear_field` call returned `wda_session_not_open`.
Root cause: each tool built a fresh `WdaClient` per call via `_wda_client_for(udid)`
instead of reusing the session-stored client that holds the open WDA HTTP session id.
Each tool now prefers `s.wda_client` and falls back to `_wda_client_for` only when no
session client exists. The whole `target=device` MCP input surface is unblocked.

**F-006 — WDA inputs now receive logical points, not pixel coords**
SimDrive's screenshot pipeline emits pixel coords (1320×2868 on Pro Max); WDA's
`/wda/tap` expects logical points (440×956). On 3× devices taps were silently absorbed
2680 px below the target. New `WdaClient.window_size_points()` is called once per
session and cached; `Session.pixel_per_point_scale` holds the px/pt ratio. Every device
input tool divides its coords by the scale before calling WDA. Simulator sessions
fast-path to scale=1.0 with no network call. HTTP errors on `/window/size` default to
scale=1.0 with a warning so tools never raise from coord conversion.

**F-002 — `observe(target=device, annotate=true)` finally returns marks**
The marks-list deferral from a8 is closed. New `simdrive/wda/som_device.py` walks the
XCUI accessibility tree from `GET /session/<sid>/source`, filters to leaf-ish text-bearing
elements (excludes Application / Window / >70% screen-area containers, invisible nodes,
zero-area, out-of-screen, empty-text, and parent duplicates), and emits marks in the
exact 9-key shape sim produces: `id`, `stable_id`, `stable_id_loose`, `bbox`, `center`,
`text`, `confidence`, `raw_confidence`, `confidence_band`. `stable_id` uses the same
blake2b 20px / 60px bucketing as the sim OCR path so cross-target recordings are
comparable. WDA `/source` failures (HTTP error, malformed XML, empty tree) return
`marks=[]` with a warning — never raise.

### Fixed — Medium / Low

**F-003 — `tool_logs` on device wired to `idevicesyslog`**
The device branch of `tool_logs` was silently-empty in a10 (returning `lines: 0`
even when the target app was logging). It now invokes `idevicesyslog -u <udid>` and
streams stdout into a bounded, time-capped buffer (default 5s timeout, N-line cap).
Predicates filter post-capture as Python substring matches. If `idevicesyslog` is
missing from PATH, the tool returns a structured `device_logs_unavailable` error
with `brew install libimobiledevice` in the message instead of a generic exception.
`TimeoutExpired` no longer drops partial output — the stdout buffer is drained
before the process is killed.

**F-004 — bootstrap smoke now catches UI Automation entitlement off**
After the existing `GET /status` check, bootstrap now also `POST /session` against
`com.apple.Preferences` to verify XCTDaemon authorization is actually granted. If
the response contains `XCTDaemonErrorDomain Code 41` (or `Code=41`), bootstrap
raises the new `wda_ui_automation_disabled` error with the exact `Settings → Developer →
Enable UI Automation` remedy plus a note that iOS pins this entitlement at runner
launch (toggling it requires re-running bootstrap-device). On success the probe session
is DELETE'd immediately so we don't leak. Non-41 HTTP errors warn but don't fail
bootstrap.

**F-001 — `__version__` constant resolves dynamically**
The a10 wheel shipped with `__version__ = "1.0.0a9"` hardcoded — every tool response
carried a misleading `_simdrive_warning: drift detected, restart...` even on a clean
install. `simdrive/__init__.py` now reads via `importlib.metadata.version("simdrive")`
with a `PackageNotFoundError` fallback to `"0.0.0+local"`. The manual-bump-`__init__`
step is removed from every future release.

### Source

INIT-2026-542 + INIT-2026-540 + INIT-2026-548. Files changed: new
`simdrive/wda/som_device.py`; `simdrive/wda/client.py` (new `source()`, `window_size_points()`),
`simdrive/wda/bootstrap.py` (Code 41 smoke probe), `simdrive/wda/errors.py`
(`wda_ui_automation_disabled`), `simdrive/server.py` (every device-branch input tool
+ corrected hid flag derivation), `simdrive/session.py` (`pixel_per_point_scale`),
`simdrive/device.py` (`get_log_tail` rewrite), `simdrive/__init__.py` (dynamic version),
`simdrive/pyproject.toml` (version bump). 39 new regression tests added; 795 total
pass after merge.

---

## [1.0.0a10] — 2026-05-13

### Added — Zero-config real-device bootstrap

**Auto-detect team ID (`auto_detect_team_id`)**
`bootstrap-device` no longer requires `--team-id`. A new `auto_detect_team_id() -> str | None`
function in `wda/bootstrap.py` queries `security find-identity -p codesigning -v` for Apple
Development certificates. If exactly one unique team ID appears, it is used automatically with a
`[simdrive] Auto-detected team: <TEAM>` log line. If multiple teams are found, a clear error lists
them and instructs the user to pass `--team-id <one of: A, B>`. Fallback to
`defaults read com.apple.dt.Xcode DVTDeveloperAccountManagerAppleIDLists` for older Xcode
installations with no keychain certs. The `--team-id` CLI flag is now optional.

**Per-team WDA bundle ID rewrite (`patch_wda_bundle_id`)**
Apple's auto-provisioning rejects the hardcoded `com.facebook.WebDriverAgentRunner.xctrunner`
bundle ID because Facebook already owns that prefix under their team. Before each build,
`patch_wda_bundle_id(source_dir, team_id)` rewrites every `PRODUCT_BUNDLE_IDENTIFIER` line
in `WebDriverAgent.xcodeproj/project.pbxproj` to `co.synctek.simdrive.wda.<team_lower>`.
The rewrite is idempotent (safe to run twice), narrow (only `PRODUCT_BUNDLE_IDENTIFIER` lines
are touched), and the scheme/PRODUCT_NAME remain "WebDriverAgentRunner" so xcodebuild's
scheme resolution is unaffected. The new bundle ID is persisted to the registry JSON.
`build_wda()` now returns `(derived_data_path, bundle_id)` instead of just `derived_data_path`.
`install_wda()` accepts an explicit `bundle_id` and uninstalls both the new and legacy Facebook IDs.

**Accurate `list_devices` HID flags**
`tool_list_devices` in `server.py` now sets `hid_supported=True` for each device that has a
WDA registry entry (`~/.simdrive/wda/<udid>.json`), and `False` otherwise. The `hid_note`
field is updated to accurate guidance: "run `simdrive bootstrap-device` once per device".
`session_start` and `list_devices` tool descriptions no longer mention a "v0.2 roadmap" —
the feature is implemented and live.

### Fixed — Xcode 16+ account check (B1+ relax)

`verify_xcode_account_for_team` strict B1 check was a false-negative on Xcode 16+:
`DVTDeveloperAccountManagerAppleIDLists` no longer stores `teamID = "..."` bindings
inline — newer Xcode caches team membership in keychain / IDEPersistentSettings.
Surfaced during the 2026-05-12 Moes Max dogfood: user was signed into the team's
Admin account and held the matching cert, yet bootstrap rejected with
"Xcode is not signed in for team X". When the strict check misses, we now confirm
any Apple-ID account is signed in (`identifier = "..."` probe), log the deferral,
and let `xcodebuild -allowProvisioningUpdates` own the final team verification.
The strict path remains primary for older Xcodes. (INIT-2026-548)

### Source

INIT-2026-540 + INIT-2026-548. Files changed: `wda/bootstrap.py` (new functions
`auto_detect_team_id`, `_wda_bundle_id_for_team`, `patch_wda_bundle_id`,
`_xcode_account_output_has_any_account`; updated `build_wda`, `install_wda`,
`bootstrap_device`, `verify_xcode_account_for_team`), `server.py` (`tool_list_devices`,
two schema description strings), `pyproject.toml` (version bump).

---

## [1.0.0a7] — 2026-05-05

### Added

- **Pre-flight Xcode account detection.** `bootstrap-device` now checks whether Xcode is signed in to an Apple ID for the supplied `--team-id` before invoking xcodebuild. When ~/Library/MobileDevice/Provisioning Profiles/ is empty, raises `wda_xcode_account_not_authenticated` with a 5-step recovery message (Xcode → Settings → Accounts → +). Replaces xcodebuild's terse "No Account for Team" error with actionable guidance.

### Improved

- **WDA port discovery timeout extended from 15s to 60s.** First-launch xcodebuild test-without-building can take 30s+ on real devices; 15s was too aggressive.
- **Locked-device detection.** When xcodebuild reports "Unlock \<device\> to Continue", bootstrap now raises `wda_device_locked` with explicit recovery steps (unlock + optionally extend Auto-Lock) instead of the generic `wda_port_discovery_timeout`.

### Fixed — WDA real-device bootstrap (6 bugs, INIT-2026-547)

All 6 bugs identified in the live-validation report are resolved in `simdrive/wda/bootstrap.py`:

**Bug 1 — `resolve_signing_identity` now filters by team_id before raising ambiguity.**
When multiple "Apple Development" certificates exist in the keychain and `--team-id` is supplied,
the function now filters to the matching certificate before raising `wda_signing_ambiguous`.
This handles the common case of two Apple Development certs (one per team/machine).

**Bug 2 — Hardware UDID vs CoreDevice UUID separation.**
`bootstrap_device()` now resolves the hardware UDID separately via
`xcrun devicectl device info details --json-output - | hardwareProperties.udid`.
The CoreDevice pairing UUID is used only for `devicectl` commands;
the hardware UDID is used for `xcodebuild -destination id=...`.
On iOS 17+ these are different identifiers for the same device.

**Bug 3 — Correct `CODE_SIGN_IDENTITY` form for automatic signing.**
`build_wda()` now uses the generic form (`CODE_SIGN_IDENTITY="Apple Development"`,
`CODE_SIGN_STYLE=Automatic`, `DEVELOPMENT_TEAM=<team-id>`) plus `-allowProvisioningUpdates`
instead of the full certificate string, which conflicted with the WDA project's
automatic signing setting.

**Bug 4 — `-Wreserved-identifier` compile errors suppressed.**
WDA v9.9.0's `PrivateHeaders/XCTest/CDStructures.h` and `XCTestCase.h` use `_XCT*`
identifiers that fail under clang's `-Weverything` with `-Wreserved-identifier=error`
in Xcode 16. Fixed by passing `OTHER_CFLAGS="-Wno-reserved-identifier"` to xcodebuild.

**Bugs 5+6 — WDA launched via `xcodebuild test-without-building` (not devicectl).**
`devicectl device console` does not exist in Xcode 16. `devicectl device process launch`
crashes WDA immediately (it's an XCTest bundle, not a plain app).
The correct mechanism is `xcodebuild test-without-building -xctestrun <path> -destination id=<hw-udid>`.
WDA announces `ServerURLHere->http://<ip>:<port><-ServerURLHere` to xcodebuild's stdout
within ~5 seconds. The `_SERVER_URL_RE` regex now captures both host (group 1) and port (group 2).
The device's WiFi IP (not `localhost`) is persisted to the registry as both `host` and `ip`.
The registry schema is extended with `ip`, `hardware_udid`, and `coredevice_uuid` fields.

### Changed — MCP surface: `tool_load_journey` replaces `run_journey`

**`run_journey` removed from MCP `_TOOLS` registry.**
The `tool_run_journey` function stays in the codebase (used by `simdrive run` / `simdrive ci`
standalone CLIs), but is no longer registered as an MCP tool. Reason: `sampling/createMessage`
is not implemented by Claude Code or most MCP clients, making `run_journey` unusable as an
MCP tool on the dominant host. The 1.0.0a4 claim "no API key needed via MCP" was false
for most deployments.

**`load_journey` added to MCP `_TOOLS` registry.**
Returns parsed YAML journey data (goals, success_criteria, budget, target, persona) so the
agent in the MCP host can drive the interaction loop using existing primitives
(`observe`, `tap`, `type_text`, `swipe`, etc.) — no LLM call inside simdrive,
no API key needed, no MCP sampling required. This makes the 1.0.0a4 agent-first claim
actually true on every MCP client.

The agent-first workflow is now:
1. `load_journey` → get journey goals + success_criteria + budget
2. `session_start` → start a simulator or device session
3. `observe` → see the current screen
4. `tap` / `type_text` / `swipe` → interact with the app
5. Repeat until success criteria are met

### Fixed — Packaging

`wda/PINNED_SHA.txt` is now declared in `[tool.setuptools.package-data]`.
Without this entry, `pip install simdrive` excluded the file from the wheel and
`simdrive bootstrap-device` raised `FileNotFoundError` when reading the pinned WDA SHA.

### Tests

New and updated tests covering all 6 WDA bugs plus the architectural change:

- `test_wda_bootstrap.py` — 10 new tests: `test_resolve_signing_identity_filters_by_team_id`,
  `test_bootstrap_resolves_hardware_udid_via_devicectl`, `test_resolve_hardware_udid_falls_back_*`,
  `test_build_wda_uses_correct_signing_flags`, `test_launch_uses_xcodebuild_test_without_building`,
  `test_port_discovery_parses_serverurlhere_from_xcodebuild_stdout`,
  `test_server_url_regex_captures_host_and_port` (group 1=host, group 2=port).
  Updated existing regex tests for new 2-group capture.
- `test_mcp_path_no_anthropic.py` — 2 new tests: `test_run_journey_not_in_mcp_tools`,
  `test_load_journey_in_mcp_tools`.
- `test_packaging_deps.py` — 2 new tests: `test_pinned_sha_in_package_data`,
  `test_no_path_file_data_undeclared`.
- `tests/test_tool_load_journey.py` (NEW) — 6 tests: happy path, with persona, field completeness,
  missing path, bad path, and `test_load_journey_no_anthropic_import`.

### Source

INIT-2026-547. First release with WDA real-device bootstrap correctly implemented.
536 unit tests pass; 0 failures.

---

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
