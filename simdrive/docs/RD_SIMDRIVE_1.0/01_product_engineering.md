# SimDrive 1.0 — Product Surface + Engineering Plan

**Author:** ProductEngineeringAtlas
**Date:** 2026-04-29
**Status:** R&D memo, BIS feasibility round
**Supersedes:** `docs/PRODUCTIZATION_PLAN.md` (sim-only-1.0 framing — withdrawn)

This memo is the product+engineering half of the SimDrive 1.0 BIS round. The
strategic frame: **public brand reverts to SimDrive**, the SpecterQA-iOS rename
is thrown away, and 1.0 ships as a **premium-from-day-one product with a free
trial**, not as MIT-engine + paid-cloud. The current 29 MCP tools become
*internal primitives* under a new journey-driven user-facing layer that
mirrors the original `synctek.io/products/specterqa/` browser product but for
iOS and works against simulators **and** physical devices.

The 0.1.0a1–0.3.0a3 PyPI history under the `simdrive` name stays for
reproducibility; 1.0 ships under a new license decided in the PricingAtlas
memo.

---

# §1. Product surface — the journey-driven layer

The 29 MCP tools today (`server.py:788-1278`) speak in the agent's vocabulary:
`tap`, `swipe`, `observe`, `perf`. That's the right shape for an *engine*, the
wrong shape for a *product*. A user installing SimDrive 1.0 should describe
**who** is using their app and **what they're trying to do**, not script a
sequence of taps. The journey-driven layer is that user-facing surface.

## §1.1 Persona spec

Personas describe the kind of user driving the app. They're consumed by the
journey runner, which compiles them into the system prompt sent to Claude on
every observe→decide→act loop iteration. A persona is a YAML file under
`.simdrive/personas/<slug>.yaml`.

### Schema

```yaml
# .simdrive/personas/<slug>.yaml
schema_version: 1                       # required; integer
name: string                            # required; human-readable
role: string                            # required; one-line job description
technical_comfort: low | medium | high  # required; affects retry tolerance
patience: low | medium | high           # required; affects timeout tolerance
goals:                                  # required; list of strings
  - "..."
frustrations:                           # required; list of strings
  - "..."
accessibility_needs:                    # optional; list of strings
  - large_text
  - voiceover
  - reduce_motion
device_profile:                         # optional; informs target selection
  prefers: simulator | device
  os_floor: "16.0"                      # min OS the persona is on
notes: string                           # optional; freeform context for the LLM
```

### Real examples

```yaml
# .simdrive/personas/first_time_reader.yaml
schema_version: 1
name: First-time reader
role: New library patron picking up a digital book
technical_comfort: low
patience: medium
goals:
  - find a book by title
  - sign in with library card
  - read the first page within 60 seconds of opening the app
frustrations:
  - modals that interrupt the reading flow
  - sign-in screens that lose context after a back-button
  - bookmarks that don't sync
accessibility_needs:
  - large_text
notes: |
  This persona is the public-library acquisition path. They are not technical.
  If a screen is ambiguous they will tap the most prominent button. If they
  hit two consecutive errors they give up and close the app — the runner
  should treat that as a journey failure, not a retry signal.
```

```yaml
# .simdrive/personas/power_user.yaml
schema_version: 1
name: Power user
role: Returning user with 50+ bookmarks across 3 devices
technical_comfort: high
patience: high
goals:
  - sync bookmarks across devices in under 5 seconds
  - search local catalog without network
  - export annotations to a file
frustrations:
  - search results ranked by recency instead of relevance
  - any flow requiring more than 3 taps to reach a saved book
device_profile:
  prefers: device
  os_floor: "18.0"
```

```yaml
# .simdrive/personas/recovery_user.yaml
schema_version: 1
name: Recovery mode user
role: User whose previous session crashed mid-checkout
technical_comfort: medium
patience: low
goals:
  - confirm the previous transaction did not double-charge
  - resume the in-progress checkout
frustrations:
  - apps that lose cart state on relaunch
  - error toasts that disappear before they can be read
notes: |
  Drives error-path coverage. The runner should *expect* the app to be in a
  partially-corrupted state at journey start (use `preconditions.app_state:
  recovering`) and verify the recovery UX, not the happy path.
```

## §1.2 Journey spec

Journeys are sequences of goals tied to a persona and a target. Stored at
`.simdrive/journeys/<slug>.yaml`. A journey is replayable when the runner
finalizes a recording bound to its `replay_id`.

### Schema

```yaml
# .simdrive/journeys/<slug>.yaml
schema_version: 1
name: string                            # required
persona: string                         # required; persona slug
target: simulator | device              # required
device_selector:                        # required-when-device
  udid: "..."                           # one of udid OR name
  name: "iPhone 17 Pro"
  os_version: "26.0"
preconditions:                          # optional
  app_bundle_id: string                 # required-when-set
  app_state: clean | recovering | logged_in | offline
  pre_grant_permissions: [location, camera, photos]
  appearance: light | dark
  set_clock_to: ISO-8601 string         # for time-sensitive flows
goals:                                  # required; ordered list
  - "..."
success_criteria:                       # required; ordered list of asserts
  - text_visible: "..."
  - screen_matches: <stable_id>
  - perf_under: { cpu_pct: 25, memory_mb: 200 }
  - no_crash: true
budget:                                 # optional
  max_steps: int                        # default 30
  max_seconds: int                      # default 180
  max_llm_calls: int                    # default 40
replay_id: string                       # auto-assigned on first finalize
tags: [smoke, ci, p0]                   # optional; for CI filtering
```

### Real examples

```yaml
# .simdrive/journeys/sign_in_first_page.yaml
schema_version: 1
name: Sign-in then read first page
persona: first_time_reader
target: simulator
device_selector:
  name: iPhone 17 Pro
  os_version: "26.0"
preconditions:
  app_bundle_id: com.example.reader
  app_state: clean
  pre_grant_permissions: [location]
goals:
  - sign in with provided library card credentials
  - find "The Great Gatsby" in the catalog
  - open the book and reach the first page of content
success_criteria:
  - text_visible: "Chapter 1"
  - no_crash: true
  - perf_under: { cpu_pct: 30 }
budget:
  max_steps: 18
  max_seconds: 120
tags: [smoke, p0]
```

```yaml
# .simdrive/journeys/bookmark_sync.yaml
schema_version: 1
name: Bookmark a book and verify cross-device sync
persona: power_user
target: device
device_selector:
  udid: "00008150-00142D540A87801C"   # Moes Max, iPhone 17 Pro Max
preconditions:
  app_bundle_id: com.example.reader
  app_state: logged_in
goals:
  - bookmark page 12 of "Frankenstein"
  - foreground the iPad paired to the same account
  - verify the bookmark appears on the iPad within 5 seconds
success_criteria:
  - text_visible: "Page 12"
  - cross_device_state_matches: { device: "00008112-000C50CE1A08C01E", screen: bookmarks }
budget:
  max_steps: 12
  max_seconds: 60
tags: [p1, multi_device]
```

## §1.3 CLI surface

Commands the user actually types. The CLI is the front door; the MCP server
becomes one of several execution targets.

| Command | Purpose | First shipped |
|---|---|---|
| `simdrive init` | Scaffold `.simdrive/` (personas/, journeys/, replays/, .gitignore) with two starter personas + one starter journey + a `simdrive.toml` config | 1.0 |
| `simdrive doctor` | Env readiness check — wraps existing `doctor` MCP tool plus license + WDA bootstrap status | exists in MCP, exposed to CLI in 1.0 |
| `simdrive validate` | Schema-validate every `personas/*.yaml` and `journeys/*.yaml`; non-zero exit on first failure | 1.0 |
| `simdrive run --journey <slug>` | Execute one journey, stream agent thoughts to stderr, emit JSON summary on stdout | 1.0 |
| `simdrive ci` | Run every journey tagged `ci` (or `--tag <tag>`); emit JUnit XML + replay corpus + summary JSON | 1.0 |
| `simdrive replay <recording-path>` | Re-run a saved YAML+PNG recording deterministically (no LLM calls), report SSIM drift | exists in MCP, exposed to CLI in 1.0 |
| `simdrive bootstrap-device <udid>` | Clone WDA at pinned SHA, build with user signing identity, install to device | 1.0 |
| `simdrive trial start` | Start the 14-day free trial; writes `~/.simdrive/license.json` with trial key + expiry | 1.0 |
| `simdrive license activate <key>` | Bind a paid license; supersedes trial | 1.0 |
| `simdrive license status` | Print `{state, expires_at, seats}` | 1.0 |
| `simdrive serve` | Start the underlying MCP server (legacy entry point — the same binary `simdrive 0.3.0a3` shipped) | exists, kept for back-compat |

## §1.4 MCP tool surface in 1.0

**Recommendation: keep all 29 tools MCP-callable. Demote them to "internal /
power-user" status in the docs. Do not break the surface.**

Trade-off honestly:

- **Pro of demoting (hiding) the tools:** simpler product story ("write a
  journey, not a script"), reduces support surface, lets us evolve internals
  without versioning agonies.
- **Pro of keeping (what I recommend):** Example Reader dogfood proved the MCP surface
  has organic agent demand outside our journey runner — Atlas drives Example Reader
  iOS today via raw `tap`/`observe` MCP calls and that's the load-bearing
  validation that this whole product works. Killing that pathway in 1.0 burns
  the strongest piece of dogfood evidence we have. Plus, a journey runner that
  internally calls its own public MCP tools is identical in cost to one that
  calls private functions; the public-API tax is near zero.

What changes is **positioning**, not surface area:

- README leads with `simdrive run --journey ...`, not with the tool list.
- The MCP tools are documented under `docs/internal-primitives.md` with a one-
  line heading: *"Use these directly only when the journey layer doesn't fit
  your problem."*
- The `STABILITY.md` (still required for 1.0 — see §2.2) declares the
  journey-spec YAML as the **stable user-facing contract**; the MCP surface is
  declared "stable but power-user", and reserves a smaller break window.

## §1.5 Output format

Each `simdrive run` produces a single artifact directory under
`.simdrive/runs/<journey-slug>-<timestamp>/`:

```
.simdrive/runs/sign_in_first_page-20260601T103015Z/
  summary.json              # machine-consumable, see schema below
  summary.md                # human-readable
  junit.xml                 # only when invoked from `simdrive ci`
  recording.yaml            # the replay artifact (existing format)
  screenshots/              # one PNG + sidecar JSON per observe step
    step_001.png
    step_001.json
    ...
  perf/                     # one snapshot per perf checkpoint
    baseline.json
    end.json
    compare.json
  crashes/                  # any .ips files written during the run
  agent_trace.jsonl         # one line per LLM call: {step, prompt_tokens, completion_tokens, cost_usd, decision}
```

`summary.json` schema:

```json
{
  "schema_version": 1,
  "journey": "sign_in_first_page",
  "persona": "first_time_reader",
  "target": "simulator",
  "device": {"udid": "...", "name": "iPhone 17 Pro", "os_version": "26.0"},
  "started_at": "2026-06-01T10:30:15Z",
  "ended_at": "2026-06-01T10:32:08Z",
  "duration_seconds": 113,
  "outcome": "passed | failed | budget_exceeded | crashed | error",
  "steps_executed": 14,
  "llm_calls": 18,
  "llm_cost_usd": 0.041,
  "success_criteria": [
    {"criterion": "text_visible: Chapter 1", "passed": true},
    {"criterion": "no_crash", "passed": true}
  ],
  "observations": ["..."],
  "bugs_filed": ["BUG-2026-001"],
  "ux_issues": ["modal blocked the back gesture for ~800ms"],
  "replay_id": "rep_20260601_103015"
}
```

JUnit XML maps each journey to a `<testcase>`, the `outcome` to pass/fail/skip,
and `agent_trace.jsonl` to attached system-out. Standard CI integrations
(GitHub Actions, GitLab CI, CircleCI) consume this directly without
adapters.

---

# §2. Engineering plan — current state → 1.0

## §2.1 Capability gap analysis

State today is `simdrive 0.3.0a3` (PyPI), 4,118 LOC Python + ~600 LOC ObjC HID
helper, 117 tests (91 unit + 26 live). Scored against 1.0 readiness:

| Axis | Today | Target | Gap |
|---|---|---|---|
| **MCP tool primitives** (29 tools) | 4 | 5 | 1 known stability bug (`perf` stale cache, `PRODUCTIZATION_PLAN.md:80`); a `type_text` async-focus race; otherwise solid |
| **Journey orchestration layer** | 1 | 5 | Does not exist. The legacy `src/specterqa/ios/som_runner.py:374` had `run_journey` but its journey spec is scenario-shaped, not persona+goal-shaped |
| **Persona-driven AI behavior** | 1 | 5 | Does not exist. Each MCP call carries no persona context |
| **Real-device input via WDA** | 1 | 5 | Does not exist. Read-only device backend exists (observe + logs + lifecycle, `device.py`); input raises `device_input_unavailable` per `REAL_DEVICE_FEASIBILITY.md:64` |
| **License/trial/entitlement system** | 1 | 4 | Does not exist anywhere in `simdrive/`. Legacy `src/specterqa/ios/license/` exists but is unrelated infrastructure |
| **CLI command surface** | 2 | 5 | Today: `simdrive`, `simdrive --version`, `simdrive --help` only (see CHANGELOG 0.2.0a2). Need 10 subcommands |
| **CI integration (JUnit XML)** | 1 | 5 | Does not exist |
| **Recording-to-replay format** | 4 | 5 | Exists and is solid (`recorder.py`, SSIM-masked, stable_id-resolved). Needs persona+journey wrapping |
| **Documentation** | 2 | 4 | CHANGELOG is current; `LIMITATIONS.md` + `BEST_PRACTICES.md` first-pass; README still says "12 tools" per `PRODUCTIZATION_PLAN.md:44`; no journey cookbook |
| **Test coverage** | 4 | 4 | 117 tests good for primitives; need ~30 journey-level integration tests for 1.0 |

## §2.2 Engineering work breakdown

Effort: **S** = ≤2d, **M** = 3-5d, **L** = 1-2 weeks, **XL** = 2-4 weeks. All
estimates are engineer-weeks of one focused person.

| # | Item | Effort | Depends on |
|---|---|---|---|
| 1 | **Persona + journey YAML schema + validators** — pydantic models, `simdrive validate`, schema-version field with forward-compat reservation | S | — |
| 2 | **Journey runner** — orchestration layer that loops `observe → assemble persona-aware prompt → Claude vision call → translate decision to act tool → observe again` until goals met or budget exhausted. Reuses MCP tools as in-process function calls (skip the JSON-RPC round-trip when running in-process; `from specterqa_ios.server import tool_tap`); falls through to MCP when target is the user's external agent. | L | 1 |
| 3 | **Persona-driven prompting** — system prompt assembly module that injects persona role/goals/frustrations/accessibility_needs into every Claude call; emits a stable trace for cost auditing | M | 2 |
| 4 | **Success-criteria evaluator** — `text_visible`, `screen_matches: <stable_id>`, `perf_under`, `no_crash`, `cross_device_state_matches`. Each evaluator wraps an existing MCP tool | M | 2 |
| 5 | **CLI scaffold (`simdrive init`, `validate`, `run`, `ci`, `doctor`, `replay`)** — click-based, mirrors `src/specterqa/ios/cli/commands.py:23-40` for layout but in the new `simdrive/cli/` package | M | 2, 4 |
| 6 | **JUnit XML + summary.json emitter** — wraps run output | S | 5 |
| 7 | **WDA bootstrap CLI (`simdrive bootstrap-device`)** — clone WDA at pinned SHA, build with user's signing identity (`xcodebuild -derivedDataPath ... build-for-testing`), install to device, leave bundle ready. The provisioning UX is the killer; per `REAL_DEVICE_FEASIBILITY.md:34`, signing-identity discovery + dev-team selection + cert-trust prompts ate ~3-5 sessions in past projects. Budget for that pain explicitly. | L | — |
| 8 | **WDA HTTP client + dispatcher** — wires `tap`/`swipe`/`type_text`/`press_key` to WDA REST endpoints when `target=device`. Replaces the `device_input_unavailable` raise with a real path | M | 7 |
| 9 | **License server (minimal)** — `POST /trials` (issue 14-day key), `POST /licenses/activate` (validate paid), `GET /licenses/<key>` (status). FastAPI on Railway. ForgeOS-hosted is also viable. ~300 LOC | M | — |
| 10 | **Trial flow + entitlement gate** — `simdrive trial start`, `license activate`, `license status`. CLI bootstrap reads `~/.simdrive/license.json`, checks expiry on every `simdrive run`/`ci` call, gracefully degrades to `simdrive validate` + `simdrive doctor` only when expired. Offline grace: 7 days after last successful server contact | S | 9 |
| 11 | **`perf` stale-cache fix** (carried over from 1.0 must-have list) | S | — |
| 12 | **`type_text` async-focus race fix** (carried over) | S | — |
| 13 | **README rewrite + journey cookbook** — README leads with the journey shape, not the tool table; cookbook has 5 worked examples | M | 5 |
| 14 | **Journey-level integration tests** (~30 tests against TestKitApp covering happy path, budget exhaustion, crash mid-journey, cross-device sync, success-criteria evaluators) | M | 2, 4 |
| 15 | **Telemetry hook (opt-in, off by default)** — emits `{event, journey_slug_hash, outcome, llm_cost, duration}` to a SimDrive-hosted endpoint. Privacy posture: no screenshots, no app names, journey-slug hashed | S design + M impl | 9 |
| 16 | **Cloud-hosted replay archive** | L | 9 |

Total budget for 1.0 essentials (items 1-14, excluding 15 and 16):
≈ 1S + 1L + 1M + 1M + 1M + 1S + 1L + 1M + 1M + 1S + 1S + 1S + 1M + 1M
= 3S + 5M + 2L = roughly **8.5 engineer-weeks**.

## §2.3 Recommended 1.0 scope vs deferred

To justify premium pricing on day one, 1.0 **must ship**:

1. The journey-driven layer (items 1, 2, 3, 4) — without it, "premium" is just
   a license fence around the alpha tool surface
2. The CLI surface (item 5) — `pip install` + `simdrive run --journey foo` is
   the demo
3. JUnit + JSON output (item 6) — CI integration is table stakes
4. WDA real-device input (items 7, 8) — chairman directive: real-device must
   ship in 1.0 for premium pricing
5. License + trial (items 9, 10) — the conversion mechanism
6. The two open `0.3.0a3` quality bugs (items 11, 12)
7. README + cookbook (item 13) and journey-level tests (item 14)

**Defer to 1.1:**

- **Cloud-hosted replay archive** (item 16) — local-first 1.0; cloud as the
  Pro/Team upsell in 1.1
- **Telemetry** (item 15) — opt-in is fine but dropping it from 1.0 saves a
  privacy-policy review cycle
- **Multi-sim parallelism license enforcement** — single-seat-runs-one-sim
  for 1.0; parallelism in a Pro tier later
- **`accessibility_audit`, `webview_elements`** — XCTest-bridge-blocked, cut
  per the prior plan and not undone here
- **`network` MCP tool** (was deferred from 0.3.0a1) — defer again. Premium
  buyers won't notice in 1.0; will notice if everything else feels rushed

## §2.4 ETA from 0.3.0a3 → 1.0

**Honest estimate: 10-12 calendar weeks of one focused engineer**, or ≈8.5
engineer-weeks of pure work + ~30% slack for the WDA provisioning pain (which
the `PRODUCTIZATION_PLAN.md` explicitly under-budgeted last cycle) + pricing /
license / trial design loops with PricingAtlas + the round-trip on copy and
positioning with MarketingAtlas.

This is **5-6× the prior plan's** "2 weeks sim-only 1.0" because the prior plan
deliberately deferred WDA, journey layer, and the license system. None of
those are deferrable here.

Calendar: today (2026-04-29) → **mid-July 2026** for a credible 1.0 release.
That puts a paid-trial-converting product on PyPI roughly aligned with the
chairman's $5K-MRR-by-July target — but **only if** journey-runner work
(item 2) starts immediately and runs in parallel with WDA bootstrap (item 7),
which means two engineers, not one. With one engineer, ETA slips to late
August.

## §2.5 Top 3 risks

1. **WDA provisioning UX is the swamp.** Past projects ate 3-5 sessions on
   signing-identity discovery, dev-team selection, cert-trust prompts, DDI
   mounting. The `REAL_DEVICE_FEASIBILITY.md:34` "3-5 days" estimate is the
   pure code; the UX-glue work is on top. **Mitigation:** build `simdrive
   bootstrap-device` on Maurice's three test devices (`REAL_DEVICE_FEASIBILITY.md:50`)
   first; document every prompt the user sees; ship with two pre-recorded
   bootstrap GIFs (USB and wireless); commit to a 30-minute first-device
   experience or revisit positioning.
2. **Journey runner cost spirals.** Each step is a Claude vision call; a
   30-step journey with retries can be 60+ vision calls. At ~$0.015 per
   sonnet vision call that's $0.90/journey-run. A user running 20 journeys/day
   spends $18/day on Claude. Easy to underprice the trial.
   **Mitigation:** publish per-journey cost in `summary.json` (already in
   schema above); ship `budget.max_llm_calls` as a hard ceiling (default 40);
   in trial, cap total Claude spend per account at $5/day server-side.
3. **The journey YAML schema is wrong on the first try.** This is the only
   surface we're calling "stable" in 1.0. If we get persona fields wrong, or
   miss a critical success-criterion type, we break everyone's journeys in
   1.1. **Mitigation:** schema-version every YAML file, ship a forward-compat
   layer (`schema_version: 1` reserved through 1.x), draft v1 with two design
   partners (Example Reader + one TBD) before the public 1.0 cut, and treat journey
   YAML the same way we'll treat the MCP tool surface — minor-cycle
   deprecation rules.

---

## Where I disagree with the chairman's direction

Two items to flag for synthesis:

1. **Real-device input in 1.0 is the single biggest schedule risk and I'd
   suggest a hedged plan, not a hard "must ship".** The directive is right
   that premium pricing demands real-device — but the WDA provisioning swamp
   has eaten past projects. Concrete recommendation: ship 1.0 with
   real-device input as a **gated beta** (license-flag `realdevice: beta`,
   warning banner in `simdrive doctor`, written one-pager on known issues),
   not as a stability-equivalent feature. That keeps the launch date,
   preserves the premium-pricing story (real-device is *available*, just
   labeled), and gives us a 1.1 graduation milestone. If the chairman wants
   it un-flagged in 1.0, add 3-4 weeks to the calendar.
2. **The 29 MCP tools should remain user-facing, not be made internal.** The
   chairman's framing (MCP tools become *internal* in 1.0) is the right
   product-marketing instinct but the wrong code-architecture call. Demote
   them in *documentation*, not in *visibility*. Reasoning: Example Reader dogfood is
   today's only paying-attention validation, and Example Reader consumes the MCP
   tools directly through Atlas, not through `simdrive run`. Hiding the tools
   in 1.0 risks burning that loop before the journey runner has its own
   independent dogfood. Keep the tools public; make the docs lead with
   journeys.

Everything else in the chairman's framing — premium-from-day-one, journey-
driven layer, real-device target, `simdrive` brand revert — is well-founded
and the plan above ladders up to it.
