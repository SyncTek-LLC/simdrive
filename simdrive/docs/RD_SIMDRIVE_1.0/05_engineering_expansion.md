# SimDrive 1.0 — Engineering Expansion Plan (Workstream A)

**Author:** EngineeringExpansionAtlas
**Date:** 2026-04-29
**Status:** Execution-ready engineering plan, BIS round (full-ambitious-scope branch)
**Inputs cited:** `00a_VALIDATED_FACTS.md` (validated-code source of truth), `01_product_engineering.md` (journey/persona schemas, CLI surface), `REAL_DEVICE_FEASIBILITY.md` (WDA scoping), `simdrive/src/specterqa_ios/server.py` (29-tool surface), `simdrive/src/specterqa_ios/recorder.py` (record/replay foundation), `simdrive/src/specterqa_ios/errors.py` (error code pattern).

This document is the engineer's day-1 build instruction set. It assumes `01_product_engineering.md` for *what* the journey/persona surface is and `00a_VALIDATED_FACTS.md` §A as the inventory of *what already exists*. Anything not traceable to §A is greenfield and labelled accordingly.

It does **not** cover Workstream B (post-1.0 moat features) or Workstream C (test app spec).

---

## §1. The five-component build, ranked

The chairman's "five components" expand into nine engineering deliverables once you decompose schemas/runner separately and split WDA bootstrap from the WDA HTTP client. Build order:

| # | Component | Depends on | Effort | Greenfield? |
|---|---|---|---|---|
| 1 | Journey YAML schema + validator | none | **S** (1-3d) | Greenfield |
| 2 | Persona YAML schema + validator | none | **S** (1-3d) | Greenfield |
| 3 | Journey runner core | (1)+(2) + `tool_observe` + act tools (`tool_tap` / `tool_swipe` / `tool_type_text` / `tool_press_key`) — see `00a_VALIDATED_FACTS.md` §A rows 1, 2, 4 | **L** (3-4w) | Greenfield (extends recorder pattern from `recorder.py`) |
| 4 | License key + trial system | none | **M** (1-2w) | Greenfield |
| 5 | WDA bootstrap CLI (`simdrive bootstrap-device`) | none | **M** (1-2w) | Greenfield |
| 6 | WDA HTTP client wired to act tools | (5) + `act.py` (`00a_VALIDATED_FACTS.md` §A row 4) | **M** (1-2w) | Greenfield client; extends `act.py` dispatch |
| 7 | Cloud private API (replay archive) | none | **M** (1-2w) | Greenfield |
| 8 | `simdrive ci` orchestrator | (3) | **S** (1-3d) | Greenfield (thin wrapper over runner) |
| 9 | Production hardening pass | everything | **M** (1-2w) | Audit/extension across the 29-tool surface |

Sequencing summary: two engineers, ten weeks, parallel tracks. Engineer A on runner/CLI/docs; Engineer B on device/license/cloud. Detailed week-by-week calendar in §5. Calendar: today (2026-04-29) → **2026-07-08** for tagged 1.0 cut.

---

## §2. Per-component build spec

### Component 1 — Journey YAML schema + validator (S, greenfield)

**File path:** `simdrive/src/specterqa_ios/journey/schema.py` (new package).

**Inputs (public surface):**
- File path: `.simdrive/journeys/<slug>.yaml`
- CLI: `simdrive validate [--journeys-dir <path>]` exits non-zero on first failure.
- Programmatic: `from specterqa_ios.journey.schema import Journey, load_journey`.

**Outputs:** validated `Journey` dataclass (pydantic v2 `BaseModel`); `simdrive validate` writes `{file, line, error_code, message}` JSONL on stderr.

**Key types.** Pydantic v2 model mirroring `01_product_engineering.md §1.2`:

```python
class SuccessCriterion(BaseModel):
    text_visible: str | None = None
    screen_matches: str | None = None  # stable_id
    perf_under: dict[str, float] | None = None  # {cpu_pct, memory_mb}
    no_crash: bool | None = None
    cross_device_state_matches: dict | None = None

class Journey(BaseModel):
    schema_version: int  # MUST equal 1 in v1.0
    name: str
    persona: str  # slug -> .simdrive/personas/<slug>.yaml
    target: Literal["simulator", "device"]
    device_selector: DeviceSelector | None
    preconditions: Preconditions | None
    goals: list[str]  # min 1
    success_criteria: list[SuccessCriterion]  # min 1
    budget: Budget = Budget()  # max_steps=30, max_seconds=180, max_llm_calls=40
    replay_id: str | None = None
    tags: list[str] = []
```

**Algorithm:** `load_journey(path) -> Journey` does (1) yaml.safe_load, (2) pydantic validate, (3) cross-ref persona slug exists in `.simdrive/personas/`, (4) when `target=device`, `device_selector` required.

**Tests:** ≥10 unit tests — each schema field's happy path, missing-required field, wrong type, persona-slug-not-found, schema_version mismatch, success-criteria empty list.

**Failure modes.** New error codes (extend `errors.py` pattern):
- `journey_schema_invalid` — pydantic validation failed
- `journey_persona_not_found` — persona slug unresolved
- `journey_schema_version_unsupported` — `schema_version != 1`
- `journey_device_selector_missing` — `target=device` without `device_selector`

**Existing code to extend:** `simdrive/src/specterqa_ios/errors.py` (add error constructors). Nothing else; this is greenfield.

---

### Component 2 — Persona YAML schema + validator (S, greenfield)

**File path:** `simdrive/src/specterqa_ios/journey/persona.py`.

Same shape as (1). Schema per `01_product_engineering.md §1.1`. New error codes: `persona_schema_invalid`, `persona_schema_version_unsupported`. Same test pattern (≥8 unit tests).

---

### Component 3 — Journey runner core (L, greenfield)

**File path:** `simdrive/src/specterqa_ios/journey/runner.py`.

**Inputs:** `simdrive run --journey <slug> [--persona-override <slug>] [--target simulator|device] [--budget-override max_steps=N,max_seconds=N]`. Programmatic: `run_journey(journey, persona, session) -> RunResult`.

**Outputs:** `RunResult` mirroring the `summary.json` schema in `01_product_engineering.md §1.5`. Writes the full artifact directory `.simdrive/runs/<slug>-<ts>/` (`summary.json`, `summary.md`, `recording.yaml`, `screenshots/step_NNN.{png,json}`, `agent_trace.jsonl`, `perf/{baseline,end,compare}.json`, `crashes/`).

**Key types:**

```python
@dataclass
class StepDecision:
    tool: Literal["tap","swipe","type_text","press_key","clear_field","done","fail"]
    args: dict
    rationale: str
    confidence: float

@dataclass
class RunResult:
    outcome: Literal["passed","failed","budget_exceeded","crashed","error"]
    steps_executed: int
    llm_calls: int
    llm_cost_usd: float
    duration_seconds: float
    success_criteria: list[CriterionEval]
    replay_id: str
    artifact_dir: Path
```

**Algorithm.** Loop until success-criteria met or budget exhausted:

```
session_start(target, device_selector)        # extends VFR §A row 1
recorder.start(session, name=journey.slug)    # extends VFR §A row 5
baseline = tool_perf_baseline(session)        # VFR §A row 7
while step_idx < budget.max_steps and elapsed < budget.max_seconds and llm_calls < budget.max_llm_calls:
    obs = tool_observe(session_id)            # VFR §A row 2
    if all_criteria_pass(obs, perf_now): outcome="passed"; break
    decision = claude_vision_call(assemble_prompt(persona, journey, obs, last_3_steps), obs.screenshot)
    if decision.tool == "done": break
    if decision.tool == "fail": outcome="failed"; break
    dispatch_act_tool(decision)                # tool_tap / tool_swipe / tool_type_text / tool_press_key — VFR §A row 4
    if crash_detected(): outcome="crashed"; break
    step_idx += 1
recorder.stop(session)                         # VFR §A rows 5, 12
write_artifacts()
```

**Load-bearing imports (from validated code):**
- `from specterqa_ios.server import tool_observe, tool_tap, tool_swipe, tool_type_text, tool_press_key, tool_clear_field, tool_perf_baseline, tool_perf_compare, tool_crashes`
- `from specterqa_ios import recorder`
- `from specterqa_ios.session import Session`

**Persona-aware prompt assembly** (`journey/prompt.py`): concatenates persona (role, technical_comfort, patience, goals, frustrations, accessibility_needs) + journey goals + last-3-step history + observe payload (text + marks). System prompt held stable across steps for Claude prompt-cache reuse (cost-mitigation per `01_product_engineering.md §2.5 risk #2`).

**Success-criteria evaluators** (`journey/criteria.py`), one per type:
- `text_visible` — substring scan over `obs["text"]`
- `screen_matches: <stable_id>` — lookup in `obs["marks"]`
- `perf_under: {cpu_pct, memory_mb}` — compare against fresh `tool_perf` snapshot (VFR §A row 7)
- `no_crash` — `tool_crashes` returned empty since journey start
- `cross_device_state_matches` — parallel session against second device. **Flagged as 1.0 stretch**; if cut, criterion warns in `agent_trace.jsonl` and pass-through (don't fail-closed — would surprise users).

**Tests.** Unit: prompt determinism (≥5), criterion evaluators (one per type), budget enforcement, cost math. Integration vs TestKitApp: happy-path pass; budget exhaustion; crash-mid-journey; criteria-fail; recording finalized; replay-id present. Target ≥30 journey-level integration tests per `01_product_engineering.md §2.2 item 14`.

**Failure modes (new error codes):** `journey_budget_exceeded` (reported in outcome, not raised), `claude_call_failed` (network/auth), `claude_cost_cap_hit` ($5/day trial cap), `act_tool_failed` (wraps inner SimdriveError, preserves code in `details.inner_code`), `success_criterion_unevaluable`.

**Existing code to extend:** `recorder.py` (`start/stop/replay` — VFR §A row 5), `session.py` (session lifecycle). Runner imports; no edits to existing files.

---

### Component 4 — License key + trial system (M, greenfield)

Two-part: offline-verifiable signed key (client-side), license server (Railway).

**Client (`simdrive/src/specterqa_ios/licensing/`):** `~/.simdrive/license.json` on disk. CLI: `simdrive trial start`, `simdrive license activate <key>`, `simdrive license status`. Programmatic: `check_entitlement() -> Entitlement(tier, expires_at, seats)` or raises.

**Crypto.** Ed25519 (`pynacl`). Keypair generated once by SyncTek via `SigningKey.generate()`. The public key is a hex constant in `simdrive/src/specterqa_ios/licensing/public_key.py`; the private key lives only as Railway env var `SIMDRIVE_LICENSE_PRIVATE_KEY`. Key format: `base64url({tier,seats,issued_at,expires_at,customer_email}) + "." + base64url(ed25519_signature)`. ~200 chars; self-contained; user pastes into `license activate`.

**Clock skew.** Compare `expires_at` against `max(time.time(), last_known_server_time)` (cached from each status check) to defeat clock backdating. 7-day offline grace per `01_product_engineering.md §2.2 item 10`.

**Server (`license_server/main.py`, FastAPI on Railway):**
- `POST /v1/trials {email} → {key, expires_at}` (14-day key, rate-limited 5/IP/day)
- `POST /v1/licenses/activate {stripe_subscription_id, email} → {key, tier, seats}` (called by Stripe webhook)
- `GET /v1/licenses/status?key=<...> → {valid, expires_at, server_time}` (returns server_time for skew)
- SQLite on Railway disk for 1.0; Postgres in 1.1.

**Entitlement gate.** `simdrive run`/`ci` call `check_entitlement()` first; expiry degrades gracefully (`validate` + `doctor` still work) but raises `LicenseError(code="license_expired")` for run/ci with copyable upgrade URL.

**Tests:** sig verify (good/bad/expired/wrong-tier), clock-skew, offline-grace, key roundtrip; license-server happy path, trial rate-limit, double-spend on activated key. ≥15 unit + 5 integration.

**Failure modes:** `license_invalid`, `license_expired`, `license_offline_grace_exhausted`, `license_tier_insufficient`, `trial_already_used`.

**Existing code to extend:** `errors.py` (new codes). Otherwise a new package; CLI dispatch wired in (5).

---

### Component 5 — WDA bootstrap CLI (M, greenfield)

**File:** `simdrive/src/specterqa_ios/wda/bootstrap.py` + CLI subcommand.

**Inputs:** `simdrive bootstrap-device <udid> [--team-id <ABC123>] [--signing-identity "iPhone Developer: ..."] [--wireless]`.

**Outputs:** WDA bundle installed on device; `~/.simdrive/wda/<udid>.json` (`{wda_bundle_id, install_path, last_built_at, host, port}`); streaming stdout that calls out user-visible Xcode prompts (e.g. `"Trust this developer → Settings → General → VPN & Device Management"`).

**Algorithm.** (1) verify `xcodebuild`, `idevicepair`, `ios-deploy` (or `xcrun devicectl`); (2) clone WDA at pinned SHA from `simdrive/src/specterqa_ios/wda/PINNED_SHA.txt`; (3) resolve signing identity (`security find-identity -v -p codesigning` + prompt; raise `wda_no_signing_identity` with copyable Apple-Dev-Center link if none); (4) `xcodebuild -workspace WebDriverAgent.xcworkspace -scheme WebDriverAgentRunner -destination "id=<udid>" -derivedDataPath ~/.simdrive/wda/<udid>/derived build-for-testing`; (5) install via `xcrun devicectl device install app`; (6) port discovery from syslog (`idevicesyslog | grep "ServerURLHere"`); (7) persist registry; (8) smoke `GET http://<host>:<port>/status` → `{ready: true}`.

**Tests.** Unit: SHA-pin resolution, signing-identity parser, syslog port-discovery regex. Integration: gated-local-only against Maurice's iPhone 17 Pro Max (`00008150-00142D540A87801C` per `REAL_DEVICE_FEASIBILITY.md:50`). Ship a `make wda-smoke` target.

**Failure modes:** `wda_no_signing_identity`, `wda_build_failed` (with xcodebuild log pointer), `wda_install_failed`, `wda_port_discovery_timeout` (15s), `wda_smoke_failed`.

**Existing code to extend:** `device.py` (VFR §A row 11 — discovery knows about WDA-bootstrapped devices), `errors.py`.

**Schedule risk.** This is the swamp (§6 risk #1). Allocate 30% slack. If WDA isn't installing on the 17 Pro Max by week 2 day 4, escalate for gated-beta scope cut.

---

### Component 6 — WDA HTTP client wired to act tools (M, extends validated)

**File:** `simdrive/src/specterqa_ios/wda/client.py`. Internal — called from `act.py` when `session.target == "device"`.

**Outputs:** taps/swipes/type/press_key dispatched to the device with return shape identical to the simulator path (the MCP tool contract — VFR §A row 4 — is preserved).

**Client surface.** Thin wrapper around WDA REST: `WdaClient(host, port).open_session(bundle_id)`, `.tap(x, y)` (`POST /session/<id>/wda/tap`), `.swipe(from, to, duration)` (`/wda/dragfromtoforduration`), `.type_text(text)` (`/wda/keys`), `.press_key(name)` (`/wda/pressButton`), `.status()`.

**Wiring to `act.py`.** Branch on `session.backend` in each of `tool_tap`/`tool_swipe`/`tool_type_text`/`tool_press_key` (currently raises `device_input_unavailable` per `errors.py:124`):
```python
if session.backend == "device":
    return wda_client_for(session.udid).tap(x, y)   # reads ~/.simdrive/wda/<udid>.json
else:
    return _hid_inject_tap(...)                      # existing path — VFR §A row 3
```

Backend abstraction is the cleanest insertion point per `01_product_engineering.md §2.2 item 1`.

**Tests.** Unit: client with mocked HTTP; backend dispatch (sim vs device) routing. Integration: gated-local against Maurice's iPhone 17 Pro Max + bootstrapped WDA, `tap_then_observe` smoke.

**Failure modes:** `wda_session_not_open`, `wda_http_error` (preserves WDA response body in `details`), `wda_unreachable`.

**Existing code to extend:** `act.py` (VFR §A row 4) — branch on backend; `session.py` — add `backend: Literal["simulator","device"]`; `errors.py`. The `device_input_unavailable` raise is deleted.

---

### Component 7 — Cloud private API (M, greenfield)

See §3 below for full specification.

---

### Component 8 — `simdrive ci` orchestrator (S, greenfield)

**File path:** `simdrive/src/specterqa_ios/cli/ci.py`.

**Inputs:** `simdrive ci [--tag smoke,p0] [--journeys <slug,slug,...>] [--bail] [--junit <path>] [--corpus-out <path>]`

**Outputs:**
- JUnit XML at `--junit` path (default `.simdrive/runs/junit.xml`)
- Replay corpus directory at `--corpus-out` (default `.simdrive/runs/corpus/`)
- Summary JSON at `.simdrive/runs/ci_summary.json` aggregating every `summary.json`
- Exit code: 0 if all pass, 1 if any fail, 2 on internal error

**Algorithm:**
1. Discover journeys: `glob('.simdrive/journeys/*.yaml')`; filter by tag/explicit slug.
2. Validate all (component 1) — bail with exit 2 if any invalid.
3. Loop journeys: `RunResult = run_journey(j, p, fresh_session)`; collect.
4. Emit JUnit XML — one `<testcase>` per journey, `<failure>` on outcome != "passed", `<system-out>` = `agent_trace.jsonl` content.
5. Aggregate `ci_summary.json`: pass/fail counts, total LLM cost, total duration, list of failed journeys.

**Tests:** Integration: 4 mocked journeys (3 pass, 1 fail) → exit 1, JUnit has 1 failure, ci_summary correct.

**Failure modes:** `ci_no_journeys_matched`, `ci_invalid_journey` (proxies the schema error from component 1).

**Existing code to extend:** Component 3 (runner). Nothing else.

---

### Component 9 — Production hardening pass (M, audit + extension)

See §4 below for full specification.

---

## §3. Cloud private API — actual specification

Greenfield. No existing Cloud code in `simdrive/`. Chairman's framing: private API for first 5 paying customers, **not** a public Cloud product.

**Hosting: Railway.** Reasons: ForgeOS already runs on Railway (`forgeos-api.synctek.io`) — same DeployAtlas runbook, same secrets pattern. Railway + boto3 against R2 is boring; Workers + R2-binding-from-outside is fiddly at 5-customer scale. Migration path to Workers is open if costs ramp (risk §6.3). Stack: FastAPI + uvicorn on Railway, R2 bucket `simdrive-cloud-prod`, Railway Postgres for metadata. Domain: `cloud.simdrive.dev` (chairman to confirm).

**Auth: bearer = the signed license key from component (4).** No OAuth, no separate API tokens, no signup form. `Authorization: Bearer <license-key>`. Server verifies Ed25519 signature against the same public key the client uses; extracts `tier`/`seats`/`expires_at`/`customer_email`; rejects expired or invalid. Zero new identity surface.

**Endpoints (v1):**

| Method | Path | Purpose | Quota |
|---|---|---|---|
| `POST` | `/v1/recordings` | Multipart upload (recording.yaml + screenshots/*) | Storage check |
| `GET` | `/v1/recordings` | List caller's recordings: `[{id, journey_slug, created_at, size_bytes, screenshot_count}]` | — |
| `GET` | `/v1/recordings/<id>` | Download as tar.gz | Bandwidth |
| `DELETE` | `/v1/recordings/<id>` | Delete | — |
| `GET` | `/v1/storage` | `{used_bytes, quota_bytes, tier}` | — |

No team/sharing endpoints in 1.0 per chairman's "first 5 customers" framing. Add team scoping in 1.1+.

**Tiers (starting points; flag for revision):**

| Tier | Quota | Retention |
|---|---|---|
| Solo ($49/mo) | 100 MB | 90 days |
| Pro ($149/mo) | 1 GB | 1 year |
| Team ($499/mo) | 10 GB | 1 year |
| Enterprise | unlimited | custom |

100 MB Solo ≈ 30 typical recordings (1-3 MB each: yaml + 8-15 PNGs). Recommendation: bump Solo to 250 MB after first design-partner usage data lands, pre-launch.

**Privacy / encryption.** Customer screenshots may carry PII.
- **At rest:** R2 server-side encryption (default).
- **In transit:** TLS 1.3 only.
- **Optional client-side encryption:** `simdrive cloud upload --encrypt` derives key from license-key + per-customer salt; server stores ciphertext, cannot decrypt for support. Documented trade-off.
- **Retention:** R2 lifecycle policy auto-deletes past tier window.
- **Delete:** `DELETE /v1/recordings/<id>` immediate; full account purge via support email (manual until 1.1).
- **No content inspection.** Server stores blobs + metadata; never OCRs or analyzes pixels.
- **Privacy policy must be live before first design-partner upload** — gate in §7.

**Billing in 1.0: none.** Manual entitlement. Stripe webhook on subscription creation calls `/v1/licenses/activate`; tier read from the license at every request. **1.1 needs:** Stripe usage-based metering for over-quota, self-serve account UI, self-serve purge.

**Effort:** ~300 LOC FastAPI + ~150 LOC R2 client + ~200 LOC tests + ~200 LOC `simdrive cloud upload/list/download` client. **M** (1-2w) realistic given Railway pattern is established.

---

## §4. Hardening pass — what we ship in production-grade 1.0

Premium pricing demands stability. The 29-tool MCP surface is solid (VFR §A: 91 unit + 26 live tests passing) but uneven on UX edges.

**4.1 Error UX audit.** Today's bar from `errors.py`: good ones (recovery copy in message) include `hid_unavailable` (gives `cd simdrive/native && make`), `target_not_found` (lists available targets), `sim_unhealthy` (gives the recovery shell command), `device_input_unavailable` (doc link). Need work: `no_session`, `missing_target`, `invalid_argument`, `recording_not_found`, `replay_drift_halt`, `already_recording`, `not_recording` — these state the problem without the next action. Deliverable: every error code's message ends `"...Recovery: <copyable command or doc link>."`. ~25 sites to update; new error codes from components 1-8 (~20 net new) follow the pattern from day one. Test: `tests/test_error_recovery_copy.py` — for every constructor in `errors.py`, assert the message literal `"Recovery:"`.

**4.2 Observability.** (a) Replace ad-hoc `print` (~30 sites in `server.py`, `recorder.py`, `act.py`) with `logging.getLogger("simdrive.<module>")`, default level `WARNING`. (b) `SIMDRIVE_DEBUG=1` sets `DEBUG` and emits per-tool latency to `~/.simdrive/debug.log`. (c) Every `tool_*` writes `{tool_name, duration_ms, started_at}` into its sidecar JSON next to the primary return shape.

**4.3 Performance benchmarks** (P50 / P95 targets):

| Operation | P50 | P95 |
|---|---|---|
| `tool_observe` (sim, 1024×768) | < 600 ms | < 1.2 s |
| `tool_tap` (sim) | < 80 ms | < 150 ms |
| `tool_type_text` (sim, 20 chars) | < 1.5 s | < 2.5 s |
| Journey replay step | < 800 ms | < 1.6 s |
| Journey runner step (incl. Claude call) | < 4 s | < 8 s |

`tests/perf/test_benchmarks.py` runs against TestKitApp on a CI-mac runner; fails on >25% P95 regression vs `tests/perf/baseline.json`. Per-tool latency comes from sidecar JSON (4.2) for free.

**4.4 Edge cases to harden** — each with a deliberate test:

| Edge case | Hardening | Error code |
|---|---|---|
| Sim not booted at session_start | Auto-boot when `--auto-boot` (default true) | `sim_not_booted` |
| App crashed mid-journey | Runner polls `tool_crashes`; outcome="crashed" with `.ips` path | existing |
| Screenshot capture failed | 3× retry, 200 ms backoff | `screenshot_failed` |
| OCR returned empty text[] | Warn in observe sidecar; don't fail | `observe_text_empty` (warn) |
| Network down during Claude call | 3× exponential backoff | `claude_call_failed` |
| `simctl` returns unexpected JSON | Schema-validate; raise with version + raw payload | `simctl_schema_drift` |
| Two journeys race same sim | Per-udid filelock at `~/.simdrive/locks/<udid>.lock` (`fcntl.flock`); `--lock-mode wait\|fail` | `sim_busy` |
| WDA process died during journey | 5s heartbeat; auto-restart once; then escalate | `wda_unreachable` |

**4.5 Documentation.** README v2 leads with `simdrive run --journey ...` (port from `02_brand_marketing.md`); per-tool reference docs auto-generated via `scripts/gen_tool_docs.py` from `server.py:_TOOLS` to `docs/tools/<tool>.md` (CI fails if regen-diff is uncommitted); 5 recipes in `docs/recipes/01_first_journey.md..05_debugging_a_failed_journey.md` covering first-journey, CI integration, real-device WDA, Cloud upload, and reading `agent_trace.jsonl`; `docs/TROUBLESHOOTING.md` auto-generated from `errors.py` with every code's recovery copy + worked example.

---

## §5. Parallel-engineer plan (10-week calendar)

Two engineers, ten weeks, mid-July 1.0 cut. Tracks: A = runner/CLI/docs, B = device/license/cloud.

| Week | Engineer A | Engineer B | Integration milestone |
|---|---|---|---|
| 1 | (1) Journey schema + validator; (2) Persona schema + validator; CLI scaffold (`simdrive validate`) | (4a) License client crypto + `~/.simdrive/license.json` format; `simdrive trial start` happy path | — |
| 2 | (3a) Journey runner skeleton — `run_journey()` calling `tool_observe` once, returning stub `RunResult` | (5) WDA bootstrap CLI — clone, build, install on Maurice's 17 Pro Max | **Palace check-in #1** — review locked journey/persona schemas before wider use |
| 3 | (3b) Prompt assembly, Claude vision call, decision dispatch | (6) WDA HTTP client + backend dispatch in `act.py` | — |
| 4 | (3c) Success-criteria evaluators; budget enforcement; recording integration | (4b) License server (Railway FastAPI) — `/v1/trials`, `/v1/licenses/activate`, `/v1/licenses/status` | — |
| 5 | (3d) Journey-level integration tests against TestKitApp | (7a) Cloud API skeleton on Railway + R2 bucket, `POST /v1/recordings` | **End-to-end #1: journey runner works against simulator AND device. Palace check-in #2 — drive `sign_in_first_page` from journey YAML.** |
| 6 | (8) `simdrive ci` orchestrator; JUnit XML emitter; CHANGELOG ongoing | (7b) Cloud API complete: `GET/DELETE`, quotas, license-bearer auth | — |
| 7 | (9.1) Error UX audit — all 25+ error sites get Recovery: copy | (9.4) Cloud privacy/encryption; lifecycle policy; rate limiting; **Privacy policy drafted (block on legal review)** | **Palace check-in #3 — first design-partner Cloud upload** |
| 8 | (9.5) README v2; per-tool reference auto-gen; 5 recipes | (5b) WDA gated-beta polish — `simdrive doctor` reports WDA, banner copy, 2 pre-recorded GIFs | — |
| 9 | (9.4) Edge-case hardening — sim-not-booted, OCR-empty, screenshot retry, sim filelock | (9.2/9.3) Observability rollout (logging, SIMDRIVE_DEBUG, sidecar latency); perf benchmarks + CI gate | **Palace check-in #4 — full PR-gating use against Palace iOS** |
| 10 | Launch readiness: 1.0 CHANGELOG, version bump in `__init__.py`, TestPyPI publish dress rehearsal, troubleshooting guide auto-gen | Production deploys: license server on Railway prod, Cloud API on Railway prod, R2 bucket prod policy | **Palace check-in #5 — sign-off + 1.0 PyPI publish** |

**Integration syncs.** 15-min M/W/F standups weeks 3-9. Protocol convention (§6 risk #5) avoids `server.py` merge conflicts. Critical handoffs: week 2→3 B gives A the WDA-bootstrap output spec; week 3→4 A gives B the `RunResult` schema; week 5→6 both freeze journey YAML schema after Palace #2; week 7→8 B hands A the `simdrive cloud upload` client SDK for recipes/04.

**Palace check-ins** are the critical-path quality gate — written feedback at the end of every odd week, integrated before the next milestone. Same loop that worked v0.2.0a1 → v0.3.0a3 (VFR §B: three dogfood reports across 5 days closed all feedback).

---

## §6. Risks and mitigations

Five highest, build-specific:

**Risk 1 — WDA provisioning UX eats >5 sessions.** `01_product_engineering.md §2.5 risk #1` + `REAL_DEVICE_FEASIBILITY.md:34` agree the 3-5 session estimate is the *code path* only — signing-identity discovery, dev-team selection, cert-trust prompts, DDI mounting on top. Mitigation: ship WDA real-device as **gated beta** — license flag `realdevice: beta`, banner in `simdrive doctor`, one-pager on known issues, Maurice's iPhone 17 Pro Max as floor. Preserves premium-pricing story without holding launch hostage. Un-flagging is a 1.1 milestone.

**Risk 2 — Journey YAML schema needs breaking change in 1.1.** Only "stable" user-facing surface in 1.0; getting persona fields or success-criterion types wrong breaks every customer's files in 1.1. Mitigation: `schema_version: 1` reserved through 1.x; lock only after Palace check-in #1 (week 2) AND check-in #2 (week 5); recruit one more design partner (chairman to nominate) so sample size > 1.

**Risk 3 — Cloud R2 costs balloon at first-customer scale.** 5 customers × 10 MB/journey × 100 journeys/month = 5 GB/month nominal, but a Pro customer running CI on every PR could push 50 GB/month. R2 egress is free; storage scales linearly. Mitigation: per-tier quotas enforced server-side (component 7); lifecycle deletion (90d Solo, 1y Pro/Team); quota-near-cap email at 80%; billing review at first $50/mo R2 invoice; revise quotas if ramp outpaces revenue.

**Risk 4 — License system gets reverse-engineered.** Public key in client; tampered binary skipping the check is trivial for a motivated pirate (asymmetric crypto prevents forgery, not patching). Mitigation: offline-first signed Ed25519 + weekly online refresh; accept the patched-binary attack vector — buyers we lose to piracy are not the $149/mo buyers. Revisit at 100 paying customers.

**Risk 5 — Two-engineer parallelism creates merge conflicts on `server.py` (1369 lines; centralized `_TOOLS` registry).** Mitigation: **protocol convention** — every new 1.0 MCP tool lands in its own module under `simdrive/src/specterqa_ios/tools/` (e.g., `tools/journey_run.py`); `server.py:_TOOLS` registration is a one-line import per tool. Single-line list-append edits resolve cleanly. Daily integration syncs M/W/F; PRs require both engineers' review.

---

## §7. The "definition of done" for 1.0

Specific, file-path-citable checklist. 1.0 ships when every box is checked:

- [ ] Components 1-8 shipped + tested per §2
- [ ] All 91 existing unit + 26 live E2E still green; plus: ≥10 unit (component 1), ≥8 unit (component 2), ≥30 integration (component 3, per `01_product_engineering.md §2.2 item 14`), ≥15 unit + 5 integration (component 4), ≥10 unit + 1 device-smoke (components 5+6), ≥20 unit + 5 integration (component 7), ≥3 integration (component 8)
- [ ] CI perf-regression test (§4.3) and error-recovery-copy test (§4.1) green
- [ ] Every error code in `errors.py` has `"Recovery:"` copy
- [ ] `SIMDRIVE_DEBUG=1` debug mode + structured logging shipped (§4.2)
- [ ] License/trial: `simdrive trial start` issues 14-day key against test-Stripe; `license activate` binds against production-Stripe; expiry fails closed; 7-day offline grace works
- [ ] Cloud: first design-partner upload via `simdrive cloud upload` end-to-end; privacy policy reviewed and live at `simdrive.dev/privacy` before that upload; quotas enforced; tier gating verified
- [ ] WDA real-device input works for Maurice's iPhone 17 Pro Max (`00008150-00142D540A87801C`) — gated-beta floor; `simdrive doctor` reports WDA status; 2 pre-recorded GIFs (USB + wireless) ship in `docs/`; `realdevice: beta` license flag gates correctly
- [ ] Docs: README v2 (per `02_brand_marketing.md`) leads with `simdrive run`; per-tool reference auto-generated from `_TOOLS`; 5 recipes in `docs/recipes/01..05`; `docs/TROUBLESHOOTING.md` auto-generated from `errors.py`
- [ ] CI perf benchmarks green at <25% P95 regression vs `tests/perf/baseline.json`
- [ ] CHANGELOG 1.0 entry written in the project's voice (terse, imperative, file-path-cited — matching v0.2.0a1 / v0.3.0a2)
- [ ] `__version__ = "1.0.0"` in `simdrive/src/specterqa_ios/__init__.py`
- [ ] TestPyPI dress rehearsal — clean-venv install, run `simdrive run --journey sign_in_first_page` against Palace iOS, `outcome: passed`
- [ ] Palace sign-off (check-in #5) — written maintainer report confirming production-ready, same shape as `~/Downloads/dogfood.rtf` v0.3.0a2

When this checklist is green, `simdrive 1.0.0` ships to PyPI. Anything not green at cut date → 1.0-RC, not 1.0.

---

*This document is the engineering plan for Workstream A. Workstream B (post-1.0 moat features) and Workstream C (test app spec) feed into the same BIS round but are scoped separately.*
