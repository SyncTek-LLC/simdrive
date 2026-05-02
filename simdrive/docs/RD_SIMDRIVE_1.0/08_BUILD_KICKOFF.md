# SimDrive 1.0 — Build Kickoff

**Status:** Build-launch reference. Read this first when starting any SimDrive 1.0 implementation session.
**Date:** 2026-05-02
**Predecessors (read in order):**
1. `00a_VALIDATED_FACTS.md` — what exists today vs hypothesis
2. `00_SIMDRIVE_1.0_PLAN.md` — the canonical plan-of-record (v2)
3. `05_engineering_expansion.md` — the per-component spec for the 1.0 build
4. `07_test_app_spec.md` — LapsApp dogfood platform spec

---

## 1. What SimDrive 1.0 IS

**One sentence:** A premium MCP-native iOS testing tool where engineers define personas + journeys in YAML, and an AI agent drives an iOS simulator (or paired physical device) through them — using vision-first observe + real UITouch HID — producing replay-able recordings, perf snapshots, and JUnit-ready CI output.

### Two interfaces, same engine

| Path | Who uses it | How |
|---|---|---|
| **MCP tool surface** (29 + 1 new) | An AI agent (Claude Code, Cursor, Cline) calling tools directly | The existing 29 tools, plus a new `run_journey` tool that orchestrates the agent loop |
| **CLI surface** | A human developer or CI runner | `simdrive init` / `run --journey <name>` / `ci` / `replay` / `trial start` / `license activate` |

### The agent loop (one journey step)

```
read journey.yaml → resolve next goal
  observe()                         # screenshot + OCR marks
  Claude(persona + goal + obs) → action
  dispatch via MCP primitive        # tap/swipe/type/press_key
  record_step()                     # pre/post screenshots
  check goal success criteria       # continue / next goal / fail
```

### Persistence

- `.simdrive/personas/<name>.yaml` — user-defined personas
- `.simdrive/journeys/<name>.yaml` — user-defined journeys
- `.simdrive/replays/<name>/` — recordings + per-step PNGs + sidecar JSON
- `~/.simdrive/sessions/<id>/` — per-session actions.jsonl + observations
- License key — Ed25519-signed, offline-first verification, weekly online refresh

### 1.0 vs deferred

| In 1.0 | Deferred to 1.1+ |
|---|---|
| 29 MCP tools (validated) | Replay Corpus Cloud (v1.1, the compounding moat) |
| Journey runner + persona layer | Perf budgets + trend dashboards (v1.2) |
| License/trial system | SOC 2 signed action ledger (v1.2 parallel build) |
| WDA real-device input — **gated beta** | Crash-report → journey (v1.3) |
| Cloud private API (replay archive, scoped to first 5 paid customers) | Production Session Capture SDK (v1.4 → 2.0, the category bet) |
| `simdrive ci` orchestrator | |
| Production hardening pass | |

**Cut entirely:** App-specific journey corpora (Slack/Notion vetted packs) — derivative-works + ToS risk.

---

## 2. Engineering discipline (non-negotiable)

Per chairman directive — every component ships with this discipline:

| Rule | What it means here |
|---|---|
| **Tests first** | Every new module ships with unit tests written before the implementation. Every PR has a failing test that the implementation makes pass. |
| **Type hints everywhere** | Full Python `typing` annotations, mypy-clean. The existing 91 unit tests are typed; we extend the standard. |
| **Single-responsibility modules** | One file per concern. `journey/loader.py`, `journey/runner.py`, `journey/schema.py`, `journey/result.py` — not one 1500-line `journey.py`. |
| **No magic, no globals** | Dependencies passed via constructors / function arguments. Easy to test, easy to mock. |
| **Comment the WHY only** | Names carry the WHAT (per existing CHANGELOG voice). Comments explain non-obvious decisions and constraints. |
| **Property-based tests where they fit** | License-key generation, stable_id hashing, YAML validation — Hypothesis. |
| **Coverage gates** | 100% on schema validators (parsing untrusted YAML is risky); ≥90% elsewhere. |
| **Performance benchmarks with regression gates** | Observe latency, tap latency, journey throughput. CI fails on regression > threshold. |
| **Live E2E for killer paths** | Vision-first observe, HID injection, record/replay, journey runner end-to-end against LapsApp. |

---

## 3. Dogfood-to-perfection phase (post-build, pre-launch)

Five passes, all must succeed before tagging v1.0:

1. **Self-dogfood week** — every SimDrive engineering agent runs SimDrive against LapsApp's full 20-journey corpus daily. Bugs go straight to backlog.
2. **Palace re-validation** — re-run Palace's existing recording corpus against 1.0. Any regression is P0.
3. **Beyond LapsApp + Palace** — drive SimDrive against 2-3 additional real iOS apps. Surface unknown failure modes.
4. **Adversarial testing** — break things on purpose: corrupted YAML, network-down mid-journey, sim killed mid-tap, simctl malformed JSON, OCR hallucinations. Every failure produces a test + a fix.
5. **Performance verification** — establish baselines; gate v1.0 at "no journey takes more than 2× its v0.3.0a3 equivalent."

Only after all five pass do we tag and launch.

---

## 4. The parallel build cycles

Per chairman directive: **agentic development system** — three coding agents in parallel, not three human engineers. Each cycle takes one parallel agent run + Atlas integration pass.

### 4.0 Parallel-build rules (apply to every cycle)

These rules are non-negotiable across all 5 cycles to keep parallel agent work safely mergeable:

**Ownership rule** — every cycle assigns each agent a disjoint set of files / directories. An agent NEVER touches a file owned by another agent in the same cycle. Atlas owns the integration files (`server.py`, `pyproject.toml`, `CHANGELOG.md`, `MANIFEST.in`, GitHub workflows) and is the only writer to them.

**New-files preferred** — agents create new files in their owned directory whenever possible. When an agent must extend an existing file in their territory, they describe the change in their report; Atlas applies it during integration.

**Test-first inside each agent** — each agent writes failing tests first, then implementation, then runs the test suite for their owned scope. An agent does not return until their owned tests pass.

**Atomic commit per cycle** — Atlas commits the integrated cycle as one logical commit (or a small chain on the same branch). No mid-cycle commits from agents directly.

**Conflict-resolution protocol** — if two agents' returned diffs touch the same file (which should be rare given the ownership rule), Atlas resolves manually during integration; if the conflict is non-trivial, flag the cycle as needing a re-run with revised ownership boundaries.

**Worktree posture** — currently NOT using git worktrees per memory `feedback_worktree_auth.md` (worktree agents fail "Not logged in"). Instead, agents run on the shared working tree with the disjoint-ownership rule enforcing safety.

**Cycle gate before next** — Atlas does not start cycle N+1 until cycle N is committed, pushed, tests are green, and a brief progress summary has been surfaced to the chairman.

### 4.1 Cycle 1 — Foundation (next session — start here)

| Agent | Scope (component refs from `05_engineering_expansion.md`) | Files agent OWNS | Files agent NEVER touches |
|---|---|---|---|
| **A — Journey runner stack** | Components 1+2+3+8 — journey YAML schema, persona schema, runner core, `simdrive ci` orchestrator | `simdrive/src/specterqa_ios/journey/` (new dir: loader, schema, runner, result, ci) + `simdrive/tests/test_journey_*.py` | `server.py`, `license/`, `cloud/`, `LapsApp/`, any existing module |
| **B — License + trial + Cloud API scaffold** | Components 4+7 — Ed25519 license, trial state, FastAPI Cloud API skeleton | `simdrive/src/specterqa_ios/license/` (new dir: keypair, signer, validator, trial) + new `cloud/` subdir (FastAPI app, R2 stubs) + `simdrive/tests/test_license_*.py` | `server.py`, `journey/`, `LapsApp/`, any existing module |
| **C — LapsApp scaffold** | Xcode project + SwiftUI shell + 4 of 12 feature areas (Settings, Light/Dark, Crash-Trigger, Search) | New `LapsApp/` directory at repo root — separate Swift project | All Python (zero overlap) |

**Atlas integration** (per § 5):
- Merge tool registrations into `server.py:_TOOLS` (one new MCP tool from Agent A: `run_journey`)
- Bump version in `pyproject.toml`
- Update `CHANGELOG.md` with cycle 1 entry
- Run full test suite + live smoke

### 4.2 Cycle 2 — Real device + LapsApp expansion

| Agent | Scope | Files agent OWNS | Files agent NEVER touches |
|---|---|---|---|
| **A — WDA real-device input (gated beta)** | Components 5+6 — `simdrive bootstrap-device` CLI + WDA HTTP client wired to act tools | `simdrive/src/specterqa_ios/wda/` (new dir: bootstrap, http_client, signing) + `simdrive/tests/test_wda_*.py` | All other Python modules; LapsApp |
| **B — Cloud API completion** | Flesh out the FastAPI scaffold from cycle 1: real R2 storage, license-key bearer auth, per-tier quotas, deployment config | `cloud/` (extends own cycle-1 work) + `cloud/tests/` | Python `simdrive/src/`; LapsApp |
| **C — LapsApp feature areas 5-8** | OAuth login (Sign in with Apple + Google), WebView reader, Lists with infinite scroll, Forms with async validation | `LapsApp/Sources/Features/{OAuth,Reader,Lists,Forms}/` + tests | All Python; LapsApp shell from cycle 1 stays untouched except for navigation registration |

**Note on cycle 2:** WDA bootstrap requires interactive Maurice-side debugging on real hardware (signing identity, dev-team selection, cert-trust prompts). Agent A produces the code; Maurice runs `simdrive bootstrap-device <udid>` against his iPhone 17 Pro Max during the integration pass to surface real-world failures.

**Atlas integration:**
- Wire WDA path into existing `tap`/`swipe`/`type_text`/`press_key` tools when `target=device` (small surgical edit to those tool handlers)
- Update `pyproject.toml`, `CHANGELOG.md`
- Live smoke against Maurice's iPhone 17 Pro Max

### 4.3 Cycle 3 — Hardening + LapsApp finish

| Agent | Scope | Files agent OWNS | Files agent NEVER touches |
|---|---|---|---|
| **A — Production hardening** | Component 9 — error UX audit, structured logging, observability (`SIMDRIVE_DEBUG=1`), perf benchmarks with regression gates, edge-case coverage, docs | `simdrive/src/specterqa_ios/observability/` (new dir) + small surgical edits to existing tools (Atlas reviews each) + `simdrive/tests/test_observability_*.py` + `docs/` updates | `journey/`, `license/`, `wda/`, `cloud/`, LapsApp |
| **B — Journey corpus** | Author 10 of the 20 pre-built journey YAMLs in `LapsApp/.simdrive/journeys/` against LapsApp's feature areas | `LapsApp/.simdrive/journeys/` + `LapsApp/.simdrive/personas/` | All code modules |
| **C — LapsApp feature areas 9-12** | Sheets+Modals, Performance stress (1000-row list), Offline mode, Multi-app journey support | `LapsApp/Sources/Features/{Sheets,Perf,Offline,MultiApp}/` + tests | All Python; earlier LapsApp features |

**Atlas integration:**
- Apply hardening edits to existing tool handlers
- Run perf benchmarks; establish baselines for CI gating
- Run all 10 cycle-3 journeys against LapsApp end-to-end

### 4.4 Cycle 4 — Dogfood-to-perfection

This cycle is **not parallel agent build work** — it's the 5-pass dogfood phase from § 3:

| Pass | Owner | Output |
|---|---|---|
| Self-dogfood week | All 3 agents in tandem (running journeys, filing bugs) | Bug backlog ranked P0/P1/P2 |
| Palace re-validation | Atlas (sends Palace the v1.0 candidate; Palace runs their existing corpus) | Regression report from Maurice |
| Beyond-LapsApp apps | Atlas (drives SimDrive against 2-3 additional iOS apps) | Failure mode catalog |
| Adversarial testing | One coding agent (specialized) runs corrupted-input + crash-mid-tap + simctl-malformed scenarios | Test additions for every failure |
| Performance verification | Atlas runs `simdrive ci` end-to-end across the LapsApp corpus, compares to v0.3.0a3 baseline | Perf gate (no journey > 2× baseline) |

Atlas integrates fixes for any P0/P1 bug between passes. Cycle 4 ends when all 5 passes are green.

### 4.5 Cycle 5 — Launch

| Workstream | Owner |
|---|---|
| Tag v1.0 + LapsApp v1.0 same week | Atlas (commit + tag + push; confirm Trusted Publisher entry; verify PyPI publish) |
| Coordinated MCP registry submissions (Anthropic + Smithery + awesome-mcp) | Atlas + chairman (per `03_gtm_pricing.md` § 7) |
| Show HN + Twitter + blog | Chairman (copy already drafted in `02_brand_marketing.md` § 4) |
| Pricing page live (Stripe live products created, payment links, license server endpoint live) | Atlas + chairman (chairman approves Stripe live creation; Atlas wires it) |
| Day 1-7 trial-signup hand-holding | Chairman |

---

## 5. Cycle 1 Atlas integration responsibilities

After agents A+B+C return, Atlas (the orchestrator session) does:

1. **Merge tool registration into `server.py`** — Agent A and B each emit a patch / list of tools to register; Atlas merges them into `_TOOLS`.
2. **Bump version + update `pyproject.toml`** — to a new alpha (e.g., `17.0.0a2` or whatever the active publish track is).
3. **Update `CHANGELOG.md`** — new entry describing what cycle 1 shipped.
4. **Run full unit test suite** — confirm 91 existing + new cycle-1 tests all pass.
5. **Run live smoke** — confirm the new journey runner can drive at least one trivial journey against TestKitApp end-to-end.
6. **Commit + push** the integrated cycle.

---

## 6. Git + branch hygiene

- Branch: `feat/v17-claude-native` is current. Cycles continue on this branch.
- Each cycle = one Atlas-orchestrated commit (or a small chain) representing the integrated parallel work.
- No PRs to main yet — branch lives until 1.0 launch readiness.

---

## 7. The kickoff command (what the next session runs)

When Atlas returns and reads this doc, the next action is:

```
1. Read 00_SIMDRIVE_1.0_PLAN.md (10 min)
2. Read 00a_VALIDATED_FACTS.md (3 min) — confirm nothing has drifted
3. Read 05_engineering_expansion.md sections §1-§3 (15 min)
4. Read this kickoff doc § 4-5 (3 min)
5. Dispatch the three Cycle 1 agents per § 4
6. Wait for all three to return
7. Run § 5 integration steps
8. Commit + push
9. Confirm with chairman before starting Cycle 2
```

Each cycle = one focused Atlas session. After each, surface progress + any decisions back to chairman.

---

## 8. What's NOT for the next session to do

- **Stripe live setup** — gated on chairman approval of pricing structure ($49/$149/$499); separate from engineering.
- **PyPI publish of `specterqa-ios 17.0.0a1`** — gated on Trusted Publisher entry (chairman's hand). Already tagged + pushed.
- **synctek.io site updates** — ProductPage rewrite for SimDrive premium-positioning is a future task; the marketing copy already drafted in `02_brand_marketing.md` waits.
- **WDA bootstrap implementation** — Cycle 2 work, needs Maurice's hardware.
- **Founder License or any commercial commitment** — paused per current direction.

---

## 9. Honest expectations

- **Each cycle ≈ 1 focused Atlas session** producing roughly 800-1,500 lines of new code + 200-400 lines of tests across 3 parallel agents.
- **5 cycles to get to v1.0 launch-ready.**
- **Calendar:** if chairman runs ~2 cycles per week, v1.0 launches in ~3 weeks of agent-driven work + 1 week of dogfood-to-perfection. That's much faster than the human-engineer 10-week estimate from `05_engineering_expansion.md`, BUT it depends on agent quality holding through 5 cycles. Realistic floor: 6 weeks. Realistic ceiling (if agents stumble): 10 weeks.
- **The cap on agent throughput** is integration testing, not code generation. Each cycle adds integration friction; cycle 4 (dogfood) is the critical-path test.

---

*End of build kickoff. The next Atlas session starts here.*
