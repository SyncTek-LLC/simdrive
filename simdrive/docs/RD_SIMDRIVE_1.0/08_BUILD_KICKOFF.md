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
2. **Example Reader re-validation** — re-run Example Reader's existing recording corpus against 1.0. Any regression is P0.
3. **Beyond LapsApp + Example Reader** — drive SimDrive against 2-3 additional real iOS apps. Surface unknown failure modes.
4. **Adversarial testing** — break things on purpose: corrupted YAML, network-down mid-journey, sim killed mid-tap, simctl malformed JSON, OCR hallucinations. Every failure produces a test + a fix.
5. **Performance verification** — establish baselines; gate v1.0 at "no journey takes more than 2× its v0.3.0a3 equivalent."

Only after all five pass do we tag and launch.

---

## 4. The parallel build cycles

Per chairman directive: **agentic development system** — three coding agents in parallel, not three human engineers. Each cycle takes one parallel agent run + Atlas integration pass.

### Cycle 1 (next session — start here)

| Agent | Scope | New files (no edits to existing) |
|---|---|---|
| **A — Journey runner stack** | Components 1+2+3+8 — journey YAML schema, persona schema, runner core, `simdrive ci` orchestrator | `simdrive/src/specterqa_ios/journey/` (loader, schema, runner, result, ci) + tests in `simdrive/tests/test_journey_*.py` |
| **B — License + trial + Cloud API scaffold** | Components 4+7 — Ed25519 license, trial state, FastAPI Cloud API skeleton | `simdrive/src/specterqa_ios/license/` (keypair, signer, validator, trial) + new `cloud/` subdir (FastAPI app, R2 storage stubs) + tests |
| **C — LapsApp scaffold** | Xcode project + SwiftUI shell + 4 of 12 feature areas (Settings, Light/Dark, Crash-Trigger, Search) | New `LapsApp/` directory at repo root — separate Swift project, no Python overlap |

**Strict rule for cycle 1: agents only CREATE new files; never MODIFY existing files** (server.py, pyproject.toml, CHANGELOG.md, etc.). Atlas does the integration merges post-hoc to avoid merge conflicts.

### Cycle 2 (after cycle 1 lands)

- Component 5+6 (WDA real-device input) — high-risk, needs interactive sim+device debugging by Maurice on real hardware
- Cloud API completion (the FastAPI scaffold from cycle 1 gets fleshed out)
- LapsApp feature areas 5-8 (OAuth login, WebView reader, Lists with infinite scroll, Forms with async validation)

### Cycle 3

- Component 9 (production hardening pass)
- LapsApp feature areas 9-12 (Sheets+Modals, Performance stress, Offline mode, Multi-app)
- Journey corpus build-out (10 of the 20 journeys)

### Cycle 4 (dogfood-to-perfection)

The 5-pass dogfood phase from §3.

### Cycle 5 (launch)

- Tag v1.0 + LapsApp v1.0 same week
- Coordinated MCP registry submissions (per `03_gtm_pricing.md` §7 launch sequence)

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
