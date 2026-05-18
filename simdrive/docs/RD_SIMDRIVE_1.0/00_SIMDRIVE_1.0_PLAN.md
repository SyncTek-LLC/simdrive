# SimDrive 1.0 Plan-of-Record (v2 — full expansion)

**Status:** Synthesis of 7 BIS memos + validated-facts source-of-truth
**Date:** 2026-05-02
**Public brand:** **SimDrive** (SpecterQA rename reverted; brand-asset SVGs back to "sim/drive" wordmark)
**Strategic frame:** Premium-from-day-one with 14-day free trial; expanded 1.0 scope (journey runner + Cloud private API + WDA real-device beta); LapsApp dogfood built alongside; world-class moat roadmap committed
**Decisions needed from chairman:** §11 (6 explicit calls)

**Source memos** (in `simdrive/docs/RD_SIMDRIVE_1.0/`):
- `00a_VALIDATED_FACTS.md` — source of truth on what exists vs hypothesis
- `01_product_engineering.md` — original product+eng spec (superseded by `05`)
- `02_brand_marketing.md` — brand identity + 10 launch surfaces
- `03_gtm_pricing.md` — agentic-first GTM + premium pricing
- `04_competitive_risk.md` — Maestro/Anthropic/Apple positioning
- `05_engineering_expansion.md` — execution-ready 1.0 build plan (9 components)
- `06_world_class_moat_features.md` — post-1.0 roadmap (5 headline features)
- `07_test_app_spec.md` — LapsApp dogfood platform

---

## 1. Executive summary

SimDrive 1.0 is a **premium iOS testing tool with a 14-day free trial**, priced at **$49 Solo / $149 Pro / $499 Team / Enterprise sales-led**. The 1.0 scope expands the validated 29-tool MCP surface with five new components: journey runner, license/trial system, WDA real-device input (gated beta), Cloud private API (replay archive), and a hardening pass. Built in parallel: **LapsApp**, an MIT iOS app exercising every SimDrive capability, shipping the same week as 1.0.

**Engineering ETA:** **10 weeks with 2 engineers** (mid-July) or **~16 weeks with 1 engineer** (late August). The two-engineer path is the only one that meets the chairman's stated July timing.

**Honest revenue:** $5K MRR by July is **not achievable** as a SimDrive-standalone target under either ETA. Realistic July contribution: **$1.5K-$2.5K** (200 trial activations, 8-12 paying customers); SimDrive standalone $5K MRR lands **October 2026**. Recommend the chairman re-cast July as a portfolio number — same recommendation as the prior PRODUCTIZATION_PLAN.

**Position SimDrive can credibly own at premium price in 2026:**
*The premium iOS testing tool agents reach for first — journey-driven, MCP-native, iOS-deep where XCUITest fails and Maestro doesn't go.*

**Top risk under premium positioning:** trial-to-paid conversion below the 5% floor (30-40% likelihood, 60-120 day clock, instrumentable). Anthropic shipping native iOS computer-use is the larger headline threat (35-45%, 9-15 month clock).

**Post-1.0 moat roadmap:** five features that take SimDrive from "premium SaaS niche" to "category-defining $50K/yr Platform tier" by month 24, anchored by the **Production Session Capture SDK** (the category bet — LogRocket-of-mobile-QA pattern).

---

## 2. The validated foundation — what exists today

Source of truth: `00a_VALIDATED_FACTS.md`. Every marketing/product claim must trace to a row there.

| Layer | Validated capability | Code path | Evidence |
|---|---|---|---|
| MCP surface | 29 tools (lifecycle, observe, tap/swipe/type/press/clear, record/replay, perf, diagnostics, robustness, version) | `simdrive/src/specterqa_ios/server.py:_TOOLS` | 91 unit + 26 live E2E pass |
| Vision-first | OCR + Set-of-Mark + stable_id + stable_id_loose + confidence_band | `observe.py` + `som.py` | unit tests + Example Reader verbatim quote |
| Input | Real UITouch HID via CoreSimulator (iOS 26 TextField focus works) | `simdrive/native/src/simdrive_input.m` | live E2E + Example Reader verbatim quote |
| Record/replay | YAML + stable_id resolution + SSIM region masking | `recorder.py` | Example Reader: *"replays now reliable enough to gate PRs on"* |
| Perf + diagnostics | CPU/RSS/threads, footprint, crashes, doctor, app_state, apps | `perf.py` + `diagnostics.py` | unit tests + live smoke |
| Real-device | Read-only (observe + logs + lifecycle) — no input | `device.py` | live test against Maurice's paired iPhones |

**Customers:** Example Reader iOS (ExampleOrg) — migrated off the predecessor in 5 days, three dogfood rounds, all feedback closed, replays now gate their PRs.

**What does NOT exist today** (and is therefore a 1.0 build-target, not a validated capability):
- Journey runner / `simdrive run --journey` CLI / `simdrive ci` orchestrator
- Persona YAML schema or persona-driven AI prompting
- License key + trial system
- WDA real-device input (only read-only operations work today)
- Cloud private API (no hosted endpoint, no replay archive)

The plan in §3-§5 builds those.

---

## 3. The expanded 1.0 scope (what we build)

Per `05_engineering_expansion.md`, nine components ship in 1.0:

| # | Component | Effort | Why it's in 1.0 |
|---|---|---|---|
| 1 | Journey YAML schema + validator | S | The user-facing surface for premium customers — defines a stable contract |
| 2 | Persona YAML schema + validator | S | Pairs with journeys; injects context into the agent loop |
| 3 | Journey runner core | L | The new headline workflow — orchestrates `observe → AI decide → act → record_step` until success criteria met |
| 4 | License key + trial system | M | Premium pricing requires entitlement; Ed25519 offline-first + weekly refresh |
| 5 | WDA bootstrap CLI | M | `simdrive bootstrap-device <udid>` — clones WDA at pinned SHA, builds with user signing identity, installs |
| 6 | WDA HTTP client | M | Wires `tap/swipe/type/press_key` to WDA when `target=device` |
| 7 | Cloud private API | M | Replay archive endpoint at `forgeos-api.synctek.io/v1/recordings` (or simdrive-api.synctek.io) — Cloudflare R2 storage, license-key bearer auth, per-tier quotas |
| 8 | `simdrive ci` orchestrator | S | Run all journeys, output JUnit XML + corpus |
| 9 | Production hardening pass | M | Error UX audit, structured logging, perf benchmarks, edge-case coverage, docs |

**WDA scope decision:** ships as **gated beta** in 1.0 (`--device-beta` flag, `experimental` in doctor output). Full parity ships in 1.1. Reasoning: WDA provisioning UX (signing identity, dev-team, cert-trust prompts, DDI mount) has historically eaten 3-5 sessions on top of the pure code estimate. Hedging preserves the calendar without abandoning the directive.

**29 MCP tools stay user-facing.** Demoted in *docs* (the journey runner is the headline workflow), not in *visibility*. Example Reader's validation flowed through them directly; hiding them burns evidence before the journey runner has independent proof.

### 3.1 Two-engineer 10-week calendar (the only path to July)

| Weeks | Engineer A | Engineer B | Milestone |
|---|---|---|---|
| 1-2 | Components 1+2+3-skeleton | Components 4+5 | Journey schema locked; license skeleton + WDA bootstrap stub |
| 3-4 | Component 3 completion | Components 6+7-skeleton | First end-to-end journey run on TestKitApp; WDA installs on Maurice's iPhone 17 |
| 5-6 | Component 8 + integration | Component 7 completion | `simdrive ci` works; Cloud API serves first design-partner upload |
| 7-8 | Component 9 (hardening) | Component 9 (hardening) | All 9 components green; Example Reader journey corpus passes |
| 9-10 | Launch readiness | Launch readiness | 1.0 ships |

With **1 engineer**, the calendar slides to ~16 weeks (late August) — per `01_product_engineering.md §2.4`. Either WDA or Cloud must be deferred to 1.1 to hold 10 weeks.

### 3.2 Engineering risks (top 3)

1. **WDA bootstrap UX** — historical 3-5 session overrun on top of the code estimate. Mitigation: gated beta in 1.0; if not installing on Maurice's iPhone 17 Pro Max by week 2 day 4, escalate for scope cut.
2. **Trial Claude-API cost runaway** — $0.90/run × 20/day = $18/day per trialist. Mitigation: server-side $5/day cap during trial.
3. **Journey YAML schema lock-in** — wrong schema in 1.0 breaks every customer's journeys in 1.1. Mitigation: `schema_version: 1` reservation + 2-design-partner draft cycle (Example Reader + 1 other) before public cut.

---

## 4. The dogfood platform — LapsApp

Per `07_test_app_spec.md`. **LapsApp** is an MIT-licensed fitness/run-tracking iOS app that exercises every SimDrive capability through realistic flows. Replaces TestKitApp as the canonical demo and dogfood substrate.

### 4.1 Why LapsApp ships alongside 1.0 (not before, not after)

- **Before:** drives an unstable engine, journey churn destroys the corpus's marketing value
- **After:** 1.0 launches without a working canonical demo, killing the Show HN moment + product-page hero video
- **Alongside:** tag SimDrive 1.0 + LapsApp v1.0 the same week, both linked from launch announcement, journey corpus already validated on the 1.0 engine for ~2 weeks before public cut

### 4.2 The 12 feature areas + 20 pre-built journeys

Each feature area exercises a specific SimDrive capability. Highlights:
- **OAuth login (Sign in with Apple + Google)** — the killer surface; only feature that simultaneously stresses vision-first observe AND iOS-26 UITextField focus (the out-of-process Safari sheet)
- **WebView article reader** — XCTest-blind WKWebView; SimDrive's other killer feature
- **Search + autocomplete + debounced input** — exercises the wait-for-keyboard fix
- **Crash trigger button** — intentional crash → tests `crashes` retrieval
- **Settings (light/dark, push, accessibility)** — exercises `set_appearance` + future a11y audit
- ...8 more

Some journeys **deliberately fail** (regression journeys catching intentionally-introduced bugs) so the corpus catches real failures, not just records green runs.

### 4.3 LapsApp build effort

**14 calendar weeks with 1 engineer.** Built in parallel to SimDrive 1.0 — meets at launch. Dedicated engineer C (separate from engineers A+B on SimDrive). With three engineers running parallel, both ship together at ~week 14.

---

## 5. Brand + marketing

Per `02_brand_marketing.md`. **Brand-asset reverts already executed** (`logo-primary.svg`, `wordmark-bracket.svg`, `brand/README.md` back to SimDrive wordmark, in commit-pending state).

### 5.1 Decisions

- **Public name:** SimDrive (locked)
- **Tagline:** *"Ship iOS releases your agent already tested."* (outcome-first; verb subject is the buyer)
- **Voice:** keep CHANGELOG anti-fluff posture; the *honesty* is the premium hook (every premium surface keeps one footer line of real-tradeoff disclosure)
- **Logo:** pixel-pin mark unchanged (sourced from product — the SoM red the agent already sees)

### 5.2 Voice resolution (from open-source to premium)

The CHANGELOG voice is engineer-to-engineer, anti-fluff, earns trust by *under*-selling. Premium positioning typically does the opposite. **Resolution:** keep CHANGELOG vocabulary; move the verb's center of gravity from the maker to the buyer. Open-source voice says "we built." Premium voice says "you ship." Same words, different subject.

### 5.3 Launch surfaces (drafted in `02_brand_marketing.md`)

10 production-ready surfaces drafted: synctek.io homepage hero, product page (~600 words), README v2, trial-start CTA (3 variants), pricing page hero, cold email, day-1/4/7/13 trial nurture, post-trial conversion landing, Show HN post (premium variant), Twitter thread.

All copy follows the "claims trace to validated facts" rule per Maurice's directive — no journey-runner claims as 1.0 features until 1.0 ships.

---

## 6. Pricing + GTM

Per `03_gtm_pricing.md`. **Tier structure:**

| Tier | Price | Includes |
|---|---|---|
| **Solo** | **$49/mo** | 1 sim, 1 device, 50 journeys/mo, individual use |
| **Pro** | **$149/mo** | 4 sims, 4 devices, 250 journeys/mo, parallel CI, priority support |
| **Team** | **$499/mo** | 5 seats, 1000 journeys/mo, shared journey corpus, real-device WDA included |
| **Enterprise** | $5-15K/yr | SOC 2, RBAC, SSO, audit logs, on-prem replay storage |

**Trial:** 14 days, full Pro features, soft 250-run cap, email-only activation (no card), 7-day grace then read-only on day 22, hard-stop on day 30. **Server-side $5/day Claude API cap during trial** (per Engineering A risk #2).

### 6.1 Path to $5K MRR — honest math

50 paying customers needed (blended). Funnel at industry-benchmarked rates: ~113K impressions → ~5,650 visits → ~2,260 trial-CTA clicks → ~1,240 trial activations → ~50 paying customers.

**Channel-throughput reality:** MCP registry + Smithery + dev-advocate channels deliver ~15-25K cumulative impressions in 60 days. **The funnel is undersized by ~5×.** Realistic July 2026: **200 trial activations, 8-12 paying customers, $1.5K-$2.5K MRR.** Realistic SimDrive standalone $5K MRR: **October 2026** with 4-month funnel.

### 6.2 Distribution channels (agentic-first, premium-adapted)

- Anthropic MCP registry (premium-product listing with "[paid, free trial]" tag)
- `modelcontextprotocol/servers` PR (paid-tier tag)
- Smithery.ai catalog
- Cline + Cursor MCP marketplaces
- PyPI + GitHub topics
- **Anthropic cookbook PR** — reframed as generic MCP-iOS-driver recipe (not a SimDrive ad); Anthropic policy excludes paid-product examples
- **Dev-advocate complimentary-license channel** — 15 named iOS dev-rel + AI-tool-reviewer accounts get 90-day Pro keys (replaces cookbook PR as primary paid-funnel-driver)
- Training-corpus seeding (3-4 indexable artifacts in 90 days)

---

## 7. Competitive position + moat

Per `04_competitive_risk.md`. SimDrive lives in **Premium SaaS + AI-native local** — sparsely populated.

### 7.1 vs Maestro (the hardest competitor)

Maestro is free OSS + paid Cloud, has journey YAML, vision+AX hybrid, mobile-focused. **Why pay for SimDrive instead of free Maestro?**

Three honest answers (each with counter-argument):
1. **iOS-26 TextField focus + native HID** — Maestro doesn't have this. Counter: they can fix in 4-8 weeks if motivated.
2. **MCP-native architecture for agent integration** — Maestro's CLI isn't designed for agent-loop composition. Counter: they could ship an MCP wrapper.
3. **Real-device input via WDA bundled in 1.0 (beta)** — competitive vs Maestro Cloud at $99 + BrowserStack at $199. Counter: they already have this on iOS via XCTest.

**Strategic call: SIDESTEP, not head-on.** Don't try to win generic mobile testing. Claim "the premium iOS testing tool agents reach for first."

### 7.2 vs Anthropic claude-computer-use (existential)

claude-computer-use is part of Anthropic Claude Pro ($200/mo). If they ship native iOS sim drive, our $149/mo Pro pitch competes with their $200/mo all-in subscription.

**Strategic call: LEAN IN with explicit acquisition optionality at months 6-12.** Be the iOS-specific layer Anthropic doesn't bother to build. Make SimDrive's MCP surface what Anthropic's iOS-specific computer-use *would* expose. If they ship, we're the obvious acquihire.

### 7.3 Moat — the only one that compounds is Cloud

Single biggest moat-defining bet (per `06_world_class_moat_features.md` §3): **Production Session Capture SDK** (G3) — the category-defining feature; LogRocket-of-mobile-QA pattern; anchors the new $50K/yr Platform tier. Either we're the LogRocket of mobile QA by month 24 or we course-correct — both honest outcomes.

The 5 headline post-1.0 features (covered in §8 below) layer additional moat on top of the Replay Corpus Cloud foundation that ships in v1.1.

---

## 8. Post-1.0 roadmap — the 5 headline moat features

Per `06_world_class_moat_features.md`. Each compounds the previous; ranked by build order:

| # | Feature | Version | Effort | Why it's a moat |
|---|---|---|---|---|
| 1 | **Replay Corpus Cloud** | v1.1 | 8-12 wks post-1.0 | The only compounding moat — switching cost grows linearly with corpus size |
| 2 | **Perf budgets + trend dashboards** | v1.2 | 12-18 wks | Turns SimDrive into a PR-gate signal source XCTest + Maestro both miss |
| 3 | **SOC 2 signed action ledger** | v1.2 (parallel build) | 12-18 wks build, 6 mo cert | Unlocks regulated TAM beyond the agentic-iOS-developer niche |
| 4 | **Crash-report → journey** | v1.3 | 18-26 wks | Most differentiated single feature; nobody ships this in mobile or web |
| 5 | **Production Session Capture SDK** | v1.4 → 2.0 | 26-40 wks | The category bet; anchors $50K/yr Platform tier |

**Cut entirely:** App-specific test packs (Slack/Notion vetted journey corpora) — derivative-works lawsuit + target-app ToS violation. We'd be the legal target while Maestro shrugs.

---

## 9. The 24-month thesis

Per `06 §5`: by May 2028, SimDrive has X paid customers each storing Y MB of replay corpus, running Z journeys/day, contributing to a journey marketplace with W cataloged flows — and switching costs are now measured in "months of replay-corpus migration work." Production Session Capture SDK has either turned us into the LogRocket of mobile QA OR we've course-corrected to a $499/mo SaaS niche. Both are credible outcomes; the choice point is whether G3 ships.

---

## 10. Risk register (consolidated across memos)

| # | Risk | Likelihood | Time-to-impact | Mitigation |
|---|---|---|---|---|
| 1 | Trial-to-paid conversion below 5% floor | 30-40% | 60-120 days | Instrument every funnel stage; hand-hold first 5 conversions; iterate trial UX weekly during launch |
| 2 | Anthropic ships native iOS computer-use | 35-45% | 9-15 mo | Lean in on iOS-specific layer; make MCP surface acquihire-friendly |
| 3 | Apple ships AI test framework Xcode 27 | 15-25% | 12-18 mo (WWDC 2026) | Cross-Apple-version regression + WebView gap focus |
| 4 | Maestro adds MCP wrapper + journey UX | 60-70% | 3-6 mo | Already inevitable; iOS-deep is the differentiator |
| 5 | Premium pricing pushback from individual engineers | 40-50% | 30-60 days | Solo $49 is the answer; if resisted, drop to $29 in 1.1 |
| 6 | WDA provisioning UX kills launch date | 30-40% | 4-8 wks | Gated beta in 1.0, scope-cut escalation by week 2 day 4 |
| 7 | LapsApp build slips past 14 weeks | 30-40% | 4-8 wks | Buffer in estimate; cut 1-2 feature areas if needed |
| 8 | One-engineer-only forces 1.0 to late August | 40%+ | calendar | Hire / contract second engineer NOW |
| 9 | Existing MIT releases (`simdrive 0.3.0a3`) confuse customers about what's free | 50%+ | day 1 | README + product page lead with "1.0 is paid; alpha lineage stays MIT" |
| 10 | Production Session Capture SDK (G3) doesn't ship by month 24 | 50%+ | 24 mo | Explicit course-correct option — fall back to $499/mo SaaS niche; both credible |

---

## 11. Open decisions for the chairman

| # | Decision | Synthesis recommendation | Why this needs the chairman |
|---|---|---|---|
| 1 | **One engineer or two on SimDrive 1.0** | **Two.** Mid-July ETA depends on it; one engineer slides to late August + forces deferring WDA or Cloud to 1.1. | Hiring/capacity decision. The most consequential call in this plan. |
| 2 | **LapsApp engineer (third headcount) — yes/no** | **Yes.** LapsApp ships alongside 1.0 or there's no demo at launch. 14-week build with 1 dedicated engineer. | Hiring decision. |
| 3 | **Re-cast $5K MRR by July as a portfolio target** | **Yes.** Realistic SimDrive July: $1.5-$2.5K. $5K standalone lands October. | Changes a stated goal; Atlas can't unilaterally redefine. |
| 4 | **WDA gated beta vs full parity in 1.0** | **Gated beta.** Parity blows the calendar by 3-4 weeks for a feature most Solo/Pro buyers won't use month one. | Disagrees with chairman's stated direction. |
| 5 | **MCP primitives stay user-facing or become internal** | **User-facing.** Example Reader's validation flowed through them; hiding them burns evidence before journey runner has its own proof. | Disagrees with chairman's stated direction. |
| 6 | **Cloud private API in 1.0 or punt to 1.1** | **In 1.0.** Engineering A specs it as 1.0 work (M effort); the only compounding moat must ship at launch even if scoped to 5 design-partner replay archives. | Resource allocation. |

---

## 12. 30 / 60 / 90 day execution priorities

### Days 0-30 (now → 2026-06-01)
1. **Brand revert committed** — SVGs back to "sim/drive" wordmark (DONE, awaiting commit)
2. **Hiring / engineer staffing** — confirm 2 engineers on SimDrive + 1 on LapsApp
3. **Pricing infrastructure** — Stripe live products for Solo/Pro/Team. License server scaffold. Trial activation flow.
4. **Engineering kickoff** — start Components 1+2+3-skeleton (engineer A) and 4+5 (engineer B). LapsApp engineer starts feature areas 1-3.
5. **Design-partner schema review** — Example Reader + 1 other for journey YAML schema before lock.

### Days 30-60 (2026-06-01 → 2026-07-01)
6. **Component 3 (journey runner) completion** — first end-to-end journey on TestKitApp
7. **WDA bootstrap working** — installs on Maurice's iPhone 17 Pro Max
8. **Cloud private API live** — first design-partner replay upload
9. **LapsApp midway** — 6 of 12 feature areas live, half the journey corpus drafted
10. **Marketing surfaces final** — synctek.io product page, README v2, all premium copy

### Days 60-100 (2026-07-01 → 2026-08-01)
11. **SimDrive 1.0 launch** (target: mid-July with 2 engineers)
12. **LapsApp v1.0** ships same week
13. **Hand-hold first 5 conversions personally** to instrument every funnel leak
14. **Anthropic cookbook PR** — generic MCP-iOS-driver recipe
15. **First paying customer onboarding**

### Days 100-180 (2026-08-01 → 2026-11-01)
16. **v1.1 — Replay Corpus Cloud** (the only compounding moat)
17. **v1.2 build start — Perf budgets + SOC 2 ledger** (parallel)
18. **First Cloud SOC 2 audit kicked off** (6-month cert clock)
19. **Course-correct on funnel data** — if conversion <3%, reduce Solo to $29 + revisit pricing

---

## 13. Bottom line

SimDrive 1.0 is a real product that can credibly charge premium pricing — IF we accept the 10-week build with two engineers, the gated-beta WDA scope, the LapsApp dogfood platform shipping in parallel, and the honest revenue path (October MRR not July).

The plan is internally consistent. Every claim in marketing copy traces to either (a) what's already validated in `00a_VALIDATED_FACTS.md`, (b) what's specifically planned in `05_engineering_expansion.md`, or (c) what's explicitly labeled post-1.0 in `06_world_class_moat_features.md`.

What kills the plan:
- **One engineer instead of two** — 1.0 slides 6 weeks
- **No LapsApp engineer** — 1.0 launches without a demo
- **Trial-to-paid below 5%** — funnel collapses; revenue path slides another 3-6 months
- **WDA goes 8 weeks instead of 4** — 1.0 ships without real-device input or slips

Most of those are mitigated. The hiring decision is the single biggest one.

The headline ask of the chairman: **align on three engineers (2 on SimDrive + 1 on LapsApp) for the next 14 weeks**, plus the realignment of July as a portfolio number rather than a SimDrive standalone target. With those, the 30/60/90 plan executes.

---

*End of plan-of-record. Updated 2026-05-02. Source memos in `simdrive/docs/RD_SIMDRIVE_1.0/`.*
