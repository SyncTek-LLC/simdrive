# simdrive — Productization & Agentic-First GTM Plan

**Status:** Draft v1, BIS-synthesis output
**Date:** 2026-05-01
**Inputs:** 4 parallel R&D memos — Product/Engineering, Brand/Marketing, Agentic-First GTM, Competitive Strategy
**Decision needed from:** Chairman (timeline + revenue target alignment, see §10)

---

## 1. Executive summary

simdrive is **2 weeks from a credible 1.0 stable** on the simulator path, and **3-4 weeks beyond that from real-device parity** via WebDriverAgent. The product surface (29 MCP tools), the killer features (vision-first observe, real-UITouch HID injection, stable_id replay), and the dogfood loop (Palace iOS migrated in 5 days, three feedback rounds all closed) are validated. What stands between 0.3.0a3 and 1.0 is mostly small-effort polish plus one written stability commitment.

The strategic frame is **agentic-first**: distribution through MCP registries, awesome-mcp lists, and the next-Claude training corpus, not through paid ads or sales-led motions. The window for category-definition is **roughly 9 months** before either Anthropic ships native iOS computer-use or Maestro adds an MCP wrapper. The position simdrive can credibly own in 2026: *"The MCP-native iOS simulator driver that AI agents and CI use to gate iOS PRs on real-pixel, real-input behavior — the things XCUITest can't see and Maestro can't deeply touch."*

The chairman's $5K-MRR-by-July target is **not realistic for simdrive standalone** — the paid product (Cloud / Pro tier) doesn't exist yet, and simdrive itself stays MIT. Honest revenue path is October 2026. Recommend re-casting the July target as a **portfolio number** (simdrive + closed-source proprietary iOS layer + other revenue lines), with simdrive's role through July measured in **installs and design-partner LOIs**, not MRR. See §10 for the explicit decision request.

---

## 2. State of simdrive (as of 0.3.0a3, 2026-05-01)

| Dimension | Value |
|---|---|
| MCP tools | **29** (lifecycle 3, observe 1, act 5, record/replay 5, logs 1, perf 4, diagnostics 5, robustness 4, version 1) |
| Test count | **117** total (91 unit, 26 live E2E against TestKitApp) |
| Code | 4,118 LOC Python + ~600 LOC ObjC native HID helper (universal2 binary, in-wheel) |
| Platform | macOS + Xcode + iOS Simulator. Python ≥3.10. |
| Real-device | Read-only (observe + logs + lifecycle) — input gated on WDA, scoped to v1.1 |
| Distribution | PyPI alpha track, Trusted Publisher OIDC, no token. GitHub releases per tag. |
| Customers | **1 paying-attention dogfood**: Palace iOS (org.thepalaceproject.palace), fully migrated off SpecterQA, 5-day cutover, 3 feedback rounds all closed |

**Rock-solid:** sim lifecycle, vision-first OCR observe, HID tap/swipe, type_text on iOS 26, stable_id-resolved record/replay with SSIM masking, MCP wiring, install ergonomics.

**Experimental:** real-device input (pending WDA), `perf` snapshot accuracy under sustained calls (a possible stale-cache bug from latest dogfood), `type_text` race against debounced/async-focus SwiftUI fields, Dynamic Island modal dismissal, OCR on stylized cover text.

---

## 3. Production-readiness gap (8 axes, 1=blocking, 5=ready)

| Axis | Score | Gap |
|---|---|---|
| API stability | 3/5 | No deprecation policy in writing. Two soft-breaks shipped post-0.1 (Session dataclass, type_text response shape). 1.0 needs `STABILITY.md`. |
| Error UX | 4/5 | Structured `SimdriveError` codes, recovery instructions in messages. Minor: a few paths still bubble through the catch-all `internal` envelope. |
| Documentation | 2/5 | **README still says "12 tools"** (`README.md:43`). CHANGELOG is current and detailed; `docs/` has 3 short docs. Missing: cookbook, examples, schema reference auto-generated from `_TOOLS`. |
| Install ergonomics | 4/5 | One-line `pip install specterqa-ios` ships universal2 native binary inline. CLI `--version`/`--help` shipped in 0.2.0a2. |
| Test coverage | 4/5 | 91 unit + 26 live = 117 tests. No CI matrix (Xcode/macOS versions). |
| Observability | 3/5 | Sidecar JSONs per observation, `actions.jsonl` per session, `_simdrive_warning` for version drift. Missing: structured logging, debug-mode env var, telemetry hook. |
| Backwards compat | 2/5 | No SemVer guarantee. Required for 1.0. |
| Real-device support | 2/5 | observe/logs/lifecycle work; tap/swipe/type/key raise `device_input_unavailable`. WDA roadmap scoped, not yet built. |

---

## 4. v1.0.0 roadmap — sim-only path, 2-week clock

**Recommendation: ship 1.0 as the sim-stable cut. Real-device input lands in 1.1 (3-4 weeks after 1.0).** The case for splitting: (a) the sim path is where 100% of revenue traction is today, (b) WDA scope creep on provisioning UX has bitten past projects, (c) clear positioning beats "everything in 1.0" — *"simdrive 1.0 is the canonical sim driver; real-device input ships in 1.1"* is a strong story.

### Must-have for 1.0 (5 items, all S effort)

1. **Fix `type_text` async-focus race** — add `wait_for_keyboard: true` default (poll `keyboard_visible` ~500ms before dispatching keystrokes; return `code: keyboard_not_focused` if it never appears). Removes the only known silent-failure mode in the surface.
2. **Investigate + fix `perf` stale-cache bug** — RSS frozen at 592MB across 20+ snapshots suggests the `simctl spawn ps` call is caching. Trust collapses without this. P0.
3. **Auto-generate the README tool table from `_TOOLS`** — kills the 12-vs-29 drift permanently.
4. **Write `STABILITY.md`** — declare what's covered by SemVer at 1.0 (tool names, error codes, required response fields). Optional fields advisory. Min 1 minor cycle between deprecation warning and removal.
5. **Roll the open dogfood-doc gaps into `LIMITATIONS.md` + `BEST_PRACTICES.md`** — SFSafariViewController fullscreen escape, debounce-window guidance, perf-vs-memory selection guidance.

### Should-have for 1.0 (3 items, S/M/S)

6. **Network monitoring tool** (was deferred from 0.3.0a1) — parse `simctl io booted log show` for CFNetwork events + nettop merge. Closes the perf+regression PR-gate use case Palace ships against. **M effort.**
7. **`app_relaunch` with iOS 26.3 teardown handling** — terminate → wait_for_terminated → launch → wait_for_foreground with `relaunch_failed` error code. **S effort.**
8. **Auto-promote annotate-on-text-tap** — when a tap call uses text/mark resolution after `annotate=false`, lazy-annotate the cached screenshot rather than fail. Removes a real footgun. **S effort.**

### Defer to 1.1+

- **Real-device input via WDA** (the v0.3 roadmap item, L effort, ~3-5 days impl + provisioning UX).
- `accessibility_audit`, `webview_elements` — XCTest-bridge-blocked; cut from 1.0 entirely (don't ship half-implementations).
- Cookbook/recipes directory, CI matrix, native journey-spec format.

### Top 3 risks

1. **Apple breaks the CoreSimulator HID injection in a future Xcode release.** simdrive's killer feature depends on `SimDeviceLegacyHIDClient` + `IndigoMessage` private SPI. Mitigation: pin tested Xcode versions, monitor Xcode betas, document fallback to cliclick.
2. **The `perf` stale-cache bug erodes trust before fix.** P0 — treat with same urgency as a HIGH-severity dogfood item.
3. **WDA scope creep delays 1.0** if we try to bundle it. Mitigation: keep 1.0 sim-only.

---

## 5. Brand identity

### Name
**Public brand: SpecterQA. Internal codename: simdrive.** SpecterQA is the public-facing name (PyPI, README, MCP listings, marketing); simdrive lives on as the internal codename — used in the binary filename, in dev branches, in commit history, and as the legacy console-script alias. Honest weakness on the public name: SEO competes with the broader QA-tools category; mitigate by always pairing the wordmark with iOS/MCP context. Domain: `synctek.io` is canonical; optional `simdrive.io` registration only.

### Tagline
**"Hand your iOS simulator to your agent."** (already in use — keep)

### Logo system — Direction A "Pixel pin"
A 4×4 pixel grid (the screenshot the agent sees), thin black crosshairs through one cell, a vivid red tap-pin (#FF3D2E) at the intersection. The mark literally depicts the mechanic — agent picked that pixel; SpecterQA taps it. Wordmark in geometric monospace, weight-600 `Specter` + weight-400 `QA`. Source files in `simdrive/docs/brand/`:

- `logo-primary.svg` (1200×320) — README hero, PyPI listing, MCP-registry submission
- `logo-mark-only.svg` (200×200) — app icon, social avatar
- `favicon.svg` (32×32) — browser tab, ≤32px contexts
- `wordmark-bracket.svg` — typographic fallback (`[specterqa_]`) for CLI banners

### Voice (5 rules, codified from the existing CHANGELOG)
1. **State the change, then the why, in that order.** Don't lead with motivation.
2. **Name the thing precisely.** Backticks on real symbol names. Backticks earn trust faster than adjectives.
3. **Numbers, not adverbs.** "60px bucket (3× the tight 20px)" beats "much more reliable."
4. **Acknowledge limits in the same paragraph as the capability.** Honesty is the brand.
5. **Sentence > paragraph. Table > sentence when comparing options.**

**Do-not-write list:** revolutionizing / next-generation / seamlessly / effortlessly / magical / leading / world-class / AI-powered / "Learn more →" / exclamation points.

### Three testimonial-grade quotes
All from Palace's `SIMDRIVE_v0.2.0a1_DOGFOOD.md`, attribution **Maurice Carrier, ThePalaceProject**:

1. *"simdrive 0.2.0a1 is a meaningful step forward and is now the canonical iOS sim driver for Palace iOS development, replacing SpecterQA."*
2. *"The single biggest reason SpecterQA was failing — the cliclick path that broke UITextField focus — is fully fixed."*
3. *"Replays are now reliable enough to gate PRs on."*

Use #1 as README banner, #2 in the Show HN post, #3 in v1.0 release notes.

---

## 6. Agentic-first GTM — 30-day plan

The strategic frame: **distribution is registry placement + MCP catalog presence + training-corpus footprint, not content marketing.** Discovery happens when the next iOS-driving agent reaches for the right tool, not when an engineer Googles "iOS test automation."

### Channels (priority-ranked)

| # | Channel | Action | Deadline | Lift |
|---|---|---|---|---|
| 1 | **Anthropic MCP registry** (`claude.ai/mcp`) | Submit listing with copy + demo GIF | 2026-05-08 | **Step-change** |
| 2 | **`modelcontextprotocol/servers` GitHub PR** | Open PR under "Mobile / Testing" | 2026-05-05 | Modest now, step-change later (training corpus) |
| 3 | **Smithery.ai catalog** | Submit with full metadata + 12-tool description | 2026-05-08 | Modest |
| 4 | **Cline + Cursor MCP marketplaces** | PR to Cline; draft Cursor docs entry | 2026-05-15 | Modest |
| 5 | **PyPI search + GitHub Topics** | Add topics: `mcp-server`, `ios-simulator`, `claude`, `anthropic`, `xctest-alternative`. README badge. | 2026-05-03 | Modest, persistent |
| 6 | **`anthropics/anthropic-cookbook` PR** | 30-line "Drive an iOS sim with Claude" recipe | 2026-05-22 | **Step-change** |
| 7 | **Training-corpus seeding** | Publish 3 indexable artifacts: "Why we replaced XCTest with screenshots" essay + Stack Overflow answer + GitHub Discussion with Palace dogfood data | 2026-06-01 | **Step-change**, compounds over 6-12 months |

The **training-corpus channel** is the most under-rated. It's slow but the only channel where the asset compounds without ongoing spend.

### Onboarding — minimum-time-to-first-success

Today: ~15 minutes for an unprepared developer. Target: **under 5 minutes.** Two friction reductions:

- `specterqa-ios doctor` already exists; surface it in README with a one-liner: *"Don't have Xcode? `xcode-select --install` + open Simulator.app once."* Add a 30-second loom-style GIF as README hero. (Install: `pip install specterqa-ios`.)
- Make `session_start({})` (no args) auto-pick the first booted sim and return `device: iPhone 17 Pro, ready`. Document the zero-config path.

---

## 7. SpecterQA cutover

**Decision (Chairman, 2026-05-01): SpecterQA is the public brand.** The iOS-arm PyPI rename is `simdrive` → `specterqa-ios`. The new code that was shipping as `simdrive 0.3.0a3` is now published as `specterqa-ios 17.0.0a1`, continuing the legacy `specterqa-ios` major-version line directly over the abandoned 16.x branch. simdrive lives on as the internal codename — used in the binary filename (`simdrive-input`), in dev branches, in commit history, and as the legacy console-script alias for back-compat.

**No yank, no soft sunset of the historical 54 releases.** The original `specterqa-ios` package (releases through 16.0.0a3) stays on PyPI — historical pins continue to resolve. New publishes from this repo go to the same `specterqa-ios` namespace at version 17.0.0a1+ — pip's resolver picks the new code naturally for unpinned installs.

| Date | Action |
|---|---|
| 2026-05-01 | Ship `specterqa-ios 17.0.0a1` to PyPI (the renamed `simdrive 0.3.0a3` codebase, no behavioral changes). |
| 2026-05-01 | Ship `simdrive 0.3.0a4` deprecation stub: depends on `specterqa-ios>=17.0.0a1`, prints a one-line migration notice on import. So `pip install simdrive` keeps resolving and points users at the new package. |
| 2026-05-05 | Update README banner + repo description pointing to `specterqa-ios`. Pin a migration issue on the repo. |
| 2026-05-15 | Last `simdrive` deprecation-stub release. From here forward, all releases ship under `specterqa-ios` only. |

The legacy 16.x `specterqa-ios` line (the abandoned XCTest-based codebase under `src/specterqa/` at the repo root) is being retired in a separate follow-up commit — the new code being published as `specterqa-ios 17.0.0a1` is a complete rewrite, no migration tooling needed for users (none exist on the old code).

---

## 8. Pricing & monetization

**simdrive (MIT) stays free forever.** The 29 MCP tools, vision-first observe, record/replay, HID injection — all permanently open. Paid layer ships under a separate package (`simdrive-cloud` or `simdrive-pro`) with a different license.

### Three strongest "open → paid" wedges

| Tier | Price | Wedge |
|---|---|---|
| **Pro** (individual) | **$49/mo/seat** | Hosted replay archive, SSIM-trend dashboards, multi-sim parallelism license, priority support, signed builds. Saves ~4 hours of flake-debugging/week — pays for itself at any iOS engineer's loaded rate. |
| **Team** (5 seats) | **$249/mo** | All Pro + shared journey corpus, CI integrations (productized `--simdrive` PR-gate pattern), Slack/Linear hooks, **real-device input via WDA** (the v0.3 roadmap item ships here, not in OSS). |
| **Enterprise** | Sales-led, $5-15K/yr | Compliance: SOC 2, RBAC, SSO, audit logs, on-prem replay storage. The reference-customer tier. |

**Don't price like BrowserStack** ($199+/mo) — simdrive doesn't run real-device cloud, the per-seat math gets ugly. **Don't price like Sauce** ($1K+/mo team minimums) — wrong buyer; simdrive sells to engineers, not QA directors. Maestro Cloud's $99/mo entry is the right reference; undercut at $49.

### Path to $5K MRR — honest math

- $5K ÷ $249 team = 20 paying teams. Or $5K ÷ $49 = 102 individuals. Or any blend.
- Confirmed users today: **1** (Palace, free dogfood).
- Cloud product **does not exist yet** — building Cloud MVP is an 8-12 week effort.

**$5K MRR by July is not realistic for simdrive standalone.** Realistic: **$5K MRR by October 2026** with this funnel:

- May–June: Cloud MVP build (real-device WDA + hosted CI runner)
- July: Beta with 5 design partners (Palace + 4 others recruited via channels #1-4)
- August: Public launch at $49/mo individual. Goal: 50 paid individuals = $2,450 MRR.
- September: Team tier launches. Convert 10 individual users to teams ($2,490) + add 5 new teams ($1,245). **Total ~$6,000 MRR.**

---

## 9. Competitive position & moat

### Map (Open ↔ Cloud, Imperative ↔ AI-native)

simdrive lives in **Open + AI-native local** — a sparsely populated cell. Maestro is the only mature occupant of "AI-native mobile testing"; nobody else is MCP-native.

### Honest differentiator (per opponent)

- **vs Maestro:** Maestro is more mature, ships Android too, has Studio recorder. simdrive's edge: MCP-native protocol designed for an LLM agent loop + native HID bypassing XCTest's iOS 26 TextField issues. Bet: agent-driven becomes default in 18 months.
- **vs Detox:** Detox wins for React Native (gray-boxes the JS bridge). For non-RN iOS, simdrive wins by default. Don't fight Detox on its home turf.
- **vs raw XCUITest:** XCUITest fails on WebViews, SwiftUI no-AX components, iOS 26 UITextField focus, out-of-process Safari sheets. simdrive's vision-first model wins precisely those workloads. Palace's Reader2 + OAuth use cases prove it.
- **vs claude-computer-use:** Existential threat. claude-computer-use lacks native HID, simulator session lifecycle, simctl integration, log tailing, crash retrieval, perf, recording/replay, OCR-marks, stable_id. **Roughly 6-9 months of focused Anthropic-team work to rebuild.** Window is real but not infinite.

### Moat assessment (12-month horizon)

The moat is **NOT** any single capability — it's the **bundle**: real UITouch on iOS 26 + MCP-native composable surface + Claude-tuned ergonomics + dogfood-velocity loop. Hard to clone in <6 months. Strongest single edge: **iOS 26 TextField focus via SimDeviceLegacyHIDClient + IndigoMessage** — the trick is non-obvious; ~3 weeks reverse-engineering for a competent team. **Most leverageable moat to BUILD: brand position** ("the AI-agent driver for iOS sims"), via dogfood receipts + conference talks + integrations.

### Existential risks (combined risk surface = high)

| Scenario | Likelihood | Time-to-impact | Defensive move |
|---|---|---|---|
| Anthropic ships native iOS sim drive in claude-code | **Medium (30-40%)** | 9-15 months | **LEAN IN** — be the iOS layer they don't build. Pursue explicit blessing in Anthropic's MCP registry. |
| Apple ships Xcode 27 AI/Agent UI test framework | Low-Medium (15-25%) | 12-18 months (WWDC 2026 announce) | Focus simdrive on cross-Apple-version regression and the WebView gap Apple won't close. |
| Maestro ships an MCP wrapper | **High (60-70%)** | 3-6 months | **SIDESTEP** — own iOS-deep (real HID + perf + crashes + replays) Maestro's cross-platform position can't match. |
| Well-funded YC competitor launches | Medium (35-45%) | 6-12 months | They have marketing budget; we have receipts. Lock in 3-5 named customer logos by Q3. Win OSS-credibility race. |

### The position simdrive can credibly own in 2026

> **"The MCP-native iOS simulator driver that AI agents and CI use to gate iOS PRs on real-pixel, real-input behavior — the things XCUITest can't see and Maestro can't deeply touch."**

Everything in product, pricing, and GTM should ladder up to that sentence.

---

## 10. Open decisions — chairman input requested

These three calls require chairman direction. They reach beyond Atlas's scope.

| # | Decision | Recommendation | Why it needs chairman |
|---|---|---|---|
| 1 | **Re-cast GOAL-2026-006 ($5K MRR by July) as a portfolio target** rather than a SpecterQA-iOS-standalone target. | Yes. SpecterQA for iOS's role through July is distribution (installs + design-partner LOIs). The proprietary closed-source iOS layer + other revenue lines carry the dollar number. | Changes a chairman directive; Atlas can't unilaterally redefine the goal. |
| 2 | **1.0 timeline: sim-only in 2 weeks (recommended) or include WDA real-device for ~5 weeks.** | Sim-only at 2 weeks. WDA in 1.1. | Affects positioning and the launch-date commitment. |
| ~~3~~ | ~~Procure `simdrive.io` and `simdrive.dev` domains before public launch.~~ | **Resolved 2026-05-01:** `synctek.io` is canonical; optional `simdrive.io` registration only. | — |

---

## 11. 30-day execution priorities

Concrete, owned, measurable.

| # | Priority | Owner | Deadline | Done means |
|---|---|---|---|---|
| 1 | Submit simdrive to Anthropic MCP registry, Smithery, modelcontextprotocol/servers PR | GTMAtlas + CodeAtlas | **2026-05-08** | All 3 listings live; URLs logged in INIT-2026-525 |
| 2 | Ship `specterqa-ios` 15.2.1 deprecation + `MIGRATION_FROM_SPECTERQA.md` | CodeAtlas + DeployAtlas | **2026-05-05** | Legacy package banner + redirect live |
| 3 | Recruit 3 design-partner apps for simdrive Cloud beta | Chairman + GTMAtlas | **2026-05-29** | 3 informal LOIs for free 60-day Cloud beta in exchange for monthly feedback |
| 4 | Ship simdrive 1.0 (sim-only) | CodeAtlas + TestAtlas | **2026-05-15** | All 5 must-haves + 3 should-haves landed; STABILITY.md committed; release announced |
| 5 | Auto-generate README tool table from `_TOOLS` | CodeAtlas | **2026-05-08** | Tool table cannot drift again |
| 6 | Publish 3 training-corpus artifacts ("Why we replaced XCTest" essay + SO answer + GH Discussion) | MarketingAtlas | **2026-06-01** | All 3 indexed by Google + linked from simdrive README |
| 7 | Build simdrive Cloud MVP (real-device WDA + hosted CI runner) | CodeAtlas + DeployAtlas | **2026-06-30** | 1 design-partner running daily journeys against Cloud |

---

## 12. The bottom line

simdrive at 0.3.0a3 is genuinely close. The architecture is right, the killer features are validated by a real paying-attention customer, the test count is healthy, the dogfood loop is exemplary (3 reports → 3 closes in 5 days). Two weeks of focused mostly-S work + one M (network tool) + one written stability commitment gets us to 1.0 stable. The agentic-first distribution play is concrete and dated. The pricing model preserves MIT openness while creating a clean upgrade path to a real revenue stream by October.

The single biggest risk is the **9-month window for category-definition** before either Anthropic or Maestro closes the gap. Every week of delay narrows it.

What's needed from the chairman: re-alignment on the July revenue goal (portfolio vs standalone), a yes/no on sim-only-1.0, and a small budget for domain procurement. With those, the 30-day plan executes.

---

*End of plan. Source memos in conversation history (ProductAtlas, MarketingAtlas, GTMAtlas, CompetitiveAtlas).*
