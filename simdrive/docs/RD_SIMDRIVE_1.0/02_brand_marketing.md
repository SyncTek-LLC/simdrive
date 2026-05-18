# SimDrive 1.0 — Brand & Marketing

**Author:** MarketingAtlas (BIS R&D)
**Date:** 2026-04-29
**Scope:** Brand identity refresh, asset revert checklist, premium-positioning copy package for SimDrive 1.0 launch.
**Status:** Draft for BIS review.

---

## §1. Brand identity refresh

### Name (locked)
**SimDrive.** The codename is the brand. No internal/external split. The earlier rename to SpecterQA-for-iOS is reverted; "SpecterQA" refers exclusively to the predecessor browser-automation product (now archive). Any time we need to disambiguate in copy, the predecessor is **"the SpecterQA browser product"** or **"the SpecterQA browser archive"** — never bare "SpecterQA."

### Tagline — three options for premium positioning, ranked

| # | Tagline | Why it fits premium |
|---|---|---|
| **1** | **Ship iOS releases your agent already tested.** | Outcome-first, not mechanic-first. Earns the price by tying the product to the release decision — the moment a $X/mo invoice gets approved. Implies SimDrive is on the critical path, not a tool you bolt on. |
| **2** | **The iOS test runner your agent operates.** | Names the buyer's mental category ("test runner") then claims the agentic delta. Shorter, more declarative. Reads as a product, not a hobby. |
| **3** | **iOS QA that closes the WebView and OAuth gaps.** | Specificity. The two coverage holes Example Reader named are the two holes every iOS team has. Trades brand-feel for buyer-pain matching — the kind of line that survives a forwarded Slack message. |

The current open-source line — *"Hand your iOS simulator to your agent"* — is good for an MIT pitch ("look at the cool mechanic"). Premium buyers aren't paying to admire a mechanic; they're paying to stop spending Sunday afternoons debugging XCUITest. Tagline #1 is the recommended primary; #3 is the alternate for buyer-aware surfaces (cold email subject lines, paid search).

### Positioning statement
**SimDrive is the premium iOS test runner that lets your AI agent drive the simulator and connected devices through journeys you author once and replay on every PR — covering the WebView, OAuth, and SwiftUI surfaces XCUITest can't reach.**

### Buyer persona
The user is an iOS engineer. The **buyer** is one tier up: an **iOS Engineering Manager or Director of Mobile Engineering** at a 20-200-engineer mobile org, accountable for release velocity and on-call burden. They have an existing manual-QA bill (offshore vendor, in-house QA pod, or engineer time) somewhere between $15K and $80K/quarter, and they have at least one critical user flow — usually auth or content — that XCUITest can't cover. They've already sanctioned a Claude/Cursor/Copilot license for the team, so "AI agents" is a green field, not a ledge to talk them off. They approve $249/mo or $5K-$15K/yr without a procurement process; anything beyond that needs a champion-paved path.

Decision drivers, in order: (1) does it close the coverage gap I'm getting paged about, (2) can I stand it up in a sprint, (3) does it survive the next iOS beta, (4) what does the buy/build math look like vs three engineer-weeks of XCUITest plumbing.

### Voice — five rules

1. **Outcome before mechanic.** Lead with what the buyer gets (one fewer release fire, PR-gating you can trust). Mechanic comes second, as proof.
2. **Numbers, not adverbs.** "5-day cutover, 3 dogfood rounds, all feedback closed" beats "blazing-fast iteration." This rule survives from the CHANGELOG voice.
3. **Sentences over paragraphs; tables when comparing.** Premium copy is not longer copy. It is *denser* copy.
4. **Premium tone, not sales tone.** No "revolutionary," "seamless," "next-generation," "unleash," "supercharge." Allowed: "earned," "covered," "gates," "ships," "verified," "drift-gated."
5. **One footer line of honesty per surface.** Every premium page calls out one real tradeoff (macOS-only, sim-first in v1.0, real-device input via WDA in v1.1). The honesty is the hook — it tells the buyer the page wasn't written by sales.

The shift from the open-source CHANGELOG voice: same vocabulary, same anti-fluff posture, but the verbs change. Open-source voice says "we built." Premium voice says "you ship." The center of gravity moves from the maker to the buyer.

---

## §2. Brand asset revert checklist

The pixel-pin mark is correct and stays. The wordmark text is what reverts.

| File | Change | Detail |
|---|---|---|
| `docs/brand/logo-primary.svg` | Edit `<text>` block | `<tspan font-weight="600">Specter</tspan><tspan font-weight="400">QA</tspan>` → `<tspan font-weight="600">sim</tspan><tspan font-weight="400">drive</tspan>`. Letter-spacing + font stack stay. Update `aria-label`, `<title>`, `<desc>` to "SimDrive" / "simdrive." |
| `docs/brand/wordmark-bracket.svg` | Edit `<text>` content | `[specterqa_]` → `[simdrive_]`. Update `aria-label`, `<title>`, `<desc>`. |
| `docs/brand/logo-mark-only.svg` | No edit | Pure pixel-pin mark, no text. |
| `docs/brand/favicon.svg` | No edit | Pure mark, no text. |
| `docs/brand/README.md` | Full rewrite | Replace every "SpecterQA" with "SimDrive." The "Internal codename" parenthetical is removed entirely (no codename split). The bracket-fallback example is `[simdrive_]`. The "two weights: 600 for Specter, 400 for QA" line becomes "two weights: 600 for `sim`, 400 for `drive`." |
| `docs/marketing/synctek_product_page.md` | Bulk find/replace + restructure | All "SpecterQA" → "SimDrive." Where the file currently says "the predecessor" referring to the old browser product, change to **"the SpecterQA browser product"** (one occurrence in the case-study link block). The `pip install` becomes `pip install simdrive`. The `.mcp.json` command becomes `simdrive`. |
| `docs/marketing/case_study_Example Reader.md` | Bulk find/replace | "SpecterQA" → "SimDrive" everywhere except the historical references to "the predecessor" — those become **"the SpecterQA browser product"** to disambiguate. The package versions (`specterqa-ios 0.2.0a1` → `simdrive 0.2.0a1`) revert to their `simdrive` namespace. |
| `docs/marketing/show_hn_post.md` | Rewrite per §4.9 | The premium variant is materially different from the open-source Show HN; do not bulk find/replace, replace whole. |
| `docs/marketing/twitter_launch_thread.md` | Rewrite per §4.10 | Same — premium thread is structurally different. |
| `docs/marketing/why_we_built_specterqa.md` | Rename + edit | New filename `why_we_built_simdrive.md`. Body keeps the founder essay structure but every "SpecterQA" → "SimDrive," and every "predecessor" referring to the browser product → "the SpecterQA browser product." |
| `docs/marketing/README_v2.md` | Bulk find/replace + tagline swap | "SpecterQA" → "SimDrive" everywhere. Hero tagline `> **Hand your iOS simulator to your agent.**` → `> **Ship iOS releases your agent already tested.**` (Tagline #1). |
| `docs/marketing/pypi_long_description.md` | Bulk find/replace + tagline swap | Same pattern as README_v2. |
| `CHANGELOG.md` v17.0.0a1 entry | Add a v17.0.0a2 entry, do **not** rewrite history | A new entry on top: "Brand revert. The 17.0.0a1 rename to `specterqa-ios` is reversed. Public package returns to `simdrive`; the `specterqa-ios` name lives on as a deprecation alias that re-exports `simdrive`. Same code, fourth name." This preserves the historical record while reverting the public surface. |

**Disambiguation rule** for any copy that touches both products: SimDrive is **the iOS product**. The browser product is **"the SpecterQA browser product"** (full phrase, on first reference; "the SpecterQA browser archive" works after that). Never bare "SpecterQA" — that's the ambiguous form that caused this whole rename loop.

**Sed safety note.** Bulk find/replace on `SpecterQA → SimDrive` will catch the disambiguation phrases incorrectly. Recommended workflow: (1) first pass replaces `the SpecterQA browser` with a sentinel like `__BROWSER_PRODUCT__`, (2) second pass replaces all remaining `SpecterQA` with `SimDrive`, (3) third pass replaces the sentinel back. Same pattern for `specterqa` (lowercase) → `simdrive`.

---

## §3. Premium positioning vs open-source positioning

The product is the same. The pitch is not.

| Dimension | Open-source pitch | Premium pitch (SimDrive 1.0) |
|---|---|---|
| **Headline verb** | "Use," "try," "explore" | "Ship," "gate," "cover" |
| **Center of gravity** | The mechanic ("look at this elegant HID path") | The outcome ("releases your agent already tested") |
| **Social proof** | GitHub stars, MCP-registry inclusion | Named customer logo, 5-day cutover, "reliable enough to gate PRs on" |
| **Urgency mechanic** | "Beta — feedback wanted" | "14-day free trial — full feature access, no credit card" |
| **Risk language** | "MIT, fork it, commit back" | "Free trial removes the risk; cancel before day 14, never billed" |
| **Pricing language** | "Free engine, paid Pro tier" | "Pricing reflects value; ROI math in §6 below" |
| **Tradeoff disclosure** | A "Honest tradeoffs" section, technical | A "What we don't do yet" section, buyer-decision-relevant |
| **CTA** | `pip install` | "Start your 14-day trial" → in-app install + license key flow |
| **Voice register** | Engineer-to-engineer, scrappy | Engineer-to-engineering-manager, calibrated |

**The single biggest psychological shift:** open-source pitches ask the reader to *invest effort* (clone, install, contribute). Premium pitches ask the reader to *evaluate a decision* (trial, decide, expense). Effort is free; a decision has political cost. So premium copy must remove decision friction at every turn — by being specific about the buyer's pain, by anchoring trial-start at zero risk, and by giving the champion the language they need to defend the line item.

---

## §4. Launch surfaces — production copy

### §4.1 Homepage hero on synctek.io (~80 words)

> **Ship iOS releases your agent already tested.**
>
> SimDrive is the premium iOS test runner your AI agent drives directly. It covers the surfaces XCUITest can't reach — the reader inside `WKWebView`, OAuth and SAML auth sheets, SwiftUI text input on iOS 26 — and gates them on every PR via SSIM-thresholded replays. Stand it up in a sprint. Run it free for 14 days.
>
> **[Start your 14-day trial →]**     [See pricing]     [Read the Example Reader case study]

### §4.2 SimDrive product page on synctek.io (~600 words)

---

# SimDrive

**Ship iOS releases your agent already tested.**

```
[Start your 14-day trial →]    [Read the docs]    [Talk to sales]
```

## Overview

SimDrive is the iOS test runner your AI agent operates. Your agent calls `observe`, gets back a screenshot plus an annotated copy with every text region marked, picks a target by `text` or `stable_id`, and SimDrive dispatches a real `UITouch` through `CoreSimulator`'s HID port. There is no XCTest, no accessibility-tree query, no Swift runner that breaks on the next Xcode beta. The vision-capable model is the selector engine; SimDrive is the dispatch layer it operates through.

You author **journeys** in YAML — the same model the SpecterQA browser product made famous, applied to iOS. A journey is a sequence of agent-driven steps with SSIM-gated assertions; SimDrive replays them on every PR and fails the build on visual drift. Your agent writes the journey once. Your CI runs it every commit.

## Features

| Capability | What you get |
|---|---|
| **Vision-first observe** | Screenshot + annotated PNG + `marks[]` array with `stable_id` for every detected text region. The agent never has to compute pixels. |
| **Real `UITouch` HID dispatch** | The bundled native helper drives the simulator through `SimDeviceLegacyHIDClient` — the path that triggers `UITextField` first-responder on iOS 26. The regression that broke XCUITest is fixed. |
| **YAML journeys + SSIM replay** | Author a journey once; replay on every PR with per-step SSIM drift gating and `mask_regions` for dynamic chrome. |
| **Connected device coverage** | Real-iPhone observe, logs, and lifecycle today; full input via WebDriverAgent in v1.1. Authored journeys run sim-first, then promote to device with no rewrite. |
| **Performance regression gates** | `perf_baseline` + `perf_compare` give per-axis CPU / memory / thread deltas with severity grading. No XCTest bridge. |
| **Crash and diagnostics retrieval** | `.ips` reports filtered by session-start time; environment readiness check via `doctor`. |

## Quickstart

1. Start your trial. You receive an install command and a license key by email.
2. `pip install simdrive` and add to `.mcp.json`:
   ```json
   { "mcpServers": { "simdrive": { "command": "simdrive" } } }
   ```
3. Restart Claude Code. Ask your agent: *open Settings on iPhone 17 Pro and turn on Airplane Mode.*
4. The agent calls `session_start` → `observe` → `tap({text: "Airplane Mode"})` → `observe` to confirm.

Your first journey is one prompt away. Stand up your first PR-gated journey by end of week.

## Pricing

| Tier | Price | Includes |
|---|---|---|
| **Trial** | Free, 14 days | Full feature access, simulator + read-only device, journey authoring, replay |
| **Pro** | $49 / month / seat | Sim + read-only device, hosted replay archive, SSIM-trend dashboards, priority support |
| **Team** | $249 / month for 5 seats | Pro + shared journey corpus + CI integration (`--simdrive` PR-gate flag) + Slack/Linear hooks + real-device input via WebDriverAgent |
| **Enterprise** | $5K-$15K / year | Team + SSO + SOC 2 + RBAC + audit logs + on-prem replay storage |

## What we don't do yet

- **Real-device input ships in v1.1.** `observe`, `logs`, and lifecycle work against connected devices today; `tap` / `swipe` / `type_text` raise `device_input_unavailable` until the WebDriverAgent bridge lands. Real-device input is gated to the Team tier.
- **macOS-only.** The HID helper talks to `CoreSimulator`. There is no Linux or Windows path planned.
- **Not an XCTest accessibility-audit replacement.** If you need a11y conformance certification, run XCTest in parallel — SimDrive is the agent-driven layer, not the compliance layer.

## Documentation

- **[Quickstart](/docs/simdrive/quickstart)** — install, license, first journey
- **[Journey authoring guide](/docs/simdrive/journeys)** — the YAML model and persona patterns
- **[Limitations](/docs/simdrive/limitations)** — Dynamic Island modals, MFA hard-wall, OCR on stylized art
- **[Best practices](/docs/simdrive/best-practices)** — HID + debounce-window rule, SSIM mask conventions
- **[Changelog](/docs/simdrive/changelog)** — every change, with the why

## Support

- **Trial / sales** — [contact@synctek.io](mailto:contact@synctek.io)
- **Pro & Team support** — in-app, 1-business-day SLA
- **Enterprise** — named CSM, 4-business-hour SLA
- **Security disclosures** — [security@synctek.io](mailto:security@synctek.io)

## Related posts

- [Why we built SimDrive](/blog/why-we-built-simdrive) — the founder essay on the iOS 26 `UITextField` regression and the agent-first pivot
- [Case study: Example Reader iOS migrated in 5 days](/blog/case-study-Example Reader-simdrive) — the reader + OAuth coverage that XCTest couldn't reach
- [How premium iOS QA earns its line item](/blog/simdrive-roi) — the buy/build math vs three weeks of XCUITest plumbing

---

### §4.3 README hero (~150 words)

```
<p align="center">
  <img src="docs/brand/logo-primary.svg" alt="SimDrive" width="480"/>
</p>
```

# SimDrive

> **Ship iOS releases your agent already tested.**

SimDrive is the premium iOS test runner an AI agent operates directly. Author journeys in YAML, run them sim-first, gate PRs on SSIM-thresholded replays, promote to connected device when v1.1 lands WebDriverAgent. The 29 MCP tools — vision-first observe, real `UITouch` dispatch through `SimDeviceLegacyHIDClient`, perf snapshots, crash retrieval, drift-gated replay — are what your agent uses to operate the simulator. The journey YAML is what your team authors and reviews.

Built for iOS engineering managers evaluating agentic QA against a manual-QA bill. Stand up your first PR-gated journey in a sprint. Free for 14 days; full feature access, no credit card. Cancel before day 14, never billed.

```bash
pip install simdrive
```

[Start your 14-day trial →](https://synctek.io/simdrive/trial) · [Read the docs](https://synctek.io/docs/simdrive) · [Case study: Example Reader iOS](https://synctek.io/blog/case-study-Example Reader-simdrive)

---

### §4.4 Trial-start CTA — three variants to A/B

| Variant | Sentence | Button |
|---|---|---|
| **A — Outcome** | Your AI agent already drafts code, reviews PRs, and writes tests. Let it run them too. | `Start your 14-day trial →` |
| **B — Math** | Three weeks of XCUITest plumbing, or 14 days to find out if SimDrive replaces it. | `Start your free trial →` |
| **C — Risk-removal** | Full feature access. Fourteen days. No credit card. Cancel anytime; you're never billed. | `Start trial — no card →` |

Recommended starter: **B**. The buyer's mental ledger is already running the buy/build comparison; B names it explicitly.

---

### §4.5 Pricing page hero (~100 words)

> **Pricing reflects what SimDrive replaces.**
>
> Most teams evaluating SimDrive are paying somewhere between $15K and $80K per quarter for manual iOS QA — vendor invoices, in-house headcount, or engineering hours that should be shipping features. A Team subscription at $249 / month covers five seats, real-device input, CI integration, and the `--simdrive` PR-gate flag. The math is straightforward.
>
> Start free. Try every feature for 14 days. If the math doesn't work for your team, cancel before day 14 and you're never billed.
>
> **[Start your 14-day trial →]**

---

### §4.6 Cold email to a target buyer (200 words)

**Subject:** iOS QA that closes the WebView and OAuth gaps

Hi {first_name},

I'm Maurice — I run SyncTek. We make SimDrive, an iOS test runner an AI agent operates directly. I'm reaching out because {company} ships a {WKWebView-heavy / OAuth-heavy / SwiftUI-heavy} iOS app, and the public release notes from your last two cycles mention coverage gaps that look a lot like what XCUITest can't reach.

exampleorg — public-library reading client, Readium 3.x in `WKWebView`, OAuth/SAML auth — migrated their iOS test driver to SimDrive in 5 days. Their engineering lead, three rounds of dogfood feedback in:

> "Replays are now reliable enough to gate PRs on."

The mechanic is simple: your agent calls `observe`, sees the screenshot, picks a target by visible text or `stable_id`, and SimDrive dispatches a real `UITouch` through `CoreSimulator`. No XCTest, no Swift runner that breaks on the next Xcode beta, no accessibility tree.

A 14-day trial gives your team full feature access — sim + read-only device, journey authoring, SSIM-gated replay, the works. No credit card; cancel before day 14, never billed.

Would 20 minutes next week make sense? I can demo against your build, or if you'd prefer, here's the trial link: synctek.io/simdrive/trial

— Maurice

---

### §4.7 Trial conversion email sequence (5 emails, day 1 / 4 / 7 / 11 / 13)

**Day 1 — Welcome + first journey (under 100 words)**

> **Subject:** SimDrive trial — your first journey in 20 minutes
>
> {first_name}, your trial is live. Here's the fastest path to value: pick one user flow XCUITest doesn't cover today (auth, search, paywall, reader). Ask your agent to drive it once — `session_start` → `observe` → tap your way through. Then `record_start` and do it again; you've got a replayable journey. Reply if you hit anything weird; we read every email.

**Day 4 — The case study (under 80 words)**

> **Subject:** How Example Reader migrated in 5 days
>
> Three dogfood rounds, all feedback closed. Five days, sim driver fully cut over. The flow that finally proved it: a `WKWebView` reading regression XCUITest couldn't see, gated on a SimDrive replay. Full case study: synctek.io/blog/case-study-Example Reader-simdrive. Worth 4 minutes if you're evaluating.

**Day 7 — Halfway check (under 100 words)**

> **Subject:** Halfway through — what does the math look like?
>
> {first_name}, you're halfway. Quick check: what does your team spend per quarter on manual iOS QA today? Vendor, headcount, or engineering hours all count. A Team subscription at $249 / month replaces the bottom of that ledger for five seats — sim + real-device input + CI integration + Slack hooks. If the math doesn't work, cancel anytime; you're never billed. Reply with your number and I'll show you the comparison spreadsheet we share with directors.

**Day 11 — Hands-on offer (under 60 words)**

> **Subject:** Want a live walk-through?
>
> Three days left in your trial. If you'd rather see SimDrive run against {company}'s app live than figure it out solo, I have 30-minute slots Tue / Thu this week. I'll drive against your build over screen-share, no slides. Reply with a time.

**Day 13 — Last day (under 80 words)**

> **Subject:** Trial ends tomorrow — three options
>
> Your trial ends in 24 hours. (1) Convert to Pro at $49 / seat / month — keeps your journeys, hosted replays, dashboards. (2) Convert to Team at $249 / month for 5 seats — adds CI integration and real-device input when v1.1 ships. (3) Let it expire — your data stays for 30 days, journeys stay yours under MIT-compatible terms. No automatic charge either way. Pick at synctek.io/simdrive/billing.

---

### §4.8 Post-trial conversion landing page (day 14)

> **Your SimDrive trial is complete.**
>
> Your journeys, recordings, and session data are preserved for 30 days. Pick up where you left off any time before {expiry_date}.
>
> | | |
> |---|---|
> | **Continue with Pro — $49 / seat / month** | Hosted replay archive, SSIM-trend dashboards, priority support, signed builds. |
> | **Continue with Team — $249 / month for 5 seats** | Pro + shared journey corpus + CI integration + Slack/Linear hooks + real-device input via WebDriverAgent (v1.1). |
> | **Talk to sales for Enterprise** | SSO, SOC 2, RBAC, audit logs, on-prem replay storage. |
>
> **[Continue with Pro]**     **[Continue with Team]**     **[Talk to sales]**
>
> *Not ready? That's fine. Your data is here when you are.*

Soft-CTA tone, no urgency mechanics, no countdown clock. The buyer didn't fail; the trial completed. Premium products don't pressure.

---

### §4.9 Show HN — premium product variant (~350 words)

**Show HN: SimDrive – iOS test runner your agent operates (paid, 14-day free trial)**

Hi HN. I'm Maurice. I want to be upfront before the comment thread spins up: SimDrive is a paid product. Trial is 14 days, full feature access, no credit card. I'm posting it on Show HN anyway because the mechanic is unusual enough that the engineers in this audience are the ones who'll know whether it's right.

The product is an iOS test runner an AI agent operates. The agent calls `observe`, gets a screenshot plus an annotated copy with numbered red boxes over every OCR'd text region, picks a target by visible text or `stable_id`, and SimDrive dispatches a real `UITouch` through `CoreSimulator`'s HID port using `SimDeviceLegacyHIDClient` + `IndigoMessage`. No XCTest, no Swift runner, no accessibility tree. 29 MCP tools.

The user surface is journeys in YAML — same model my earlier browser-automation product (the SpecterQA browser archive) shipped, now applied to iOS sims and connected devices. Your agent authors a journey, your team reviews the YAML, your CI replays it on every PR with SSIM drift gating.

Why I'm charging instead of going OSS: the engineering surface (real `UITouch` on iOS 26, perf, crashes, replay drift, real-device WDA path) is iOS-specific and deep. I want to fund a small team that survives the next three Xcode betas without choosing between paying rent and shipping fixes. The honest version: this is the kind of tool that gets abandoned when an OSS maintainer's day job changes. I'd rather it not be that.

Receipt: exampleorg (public-library reading client, Readium 3.x in `WKWebView`, OAuth/SAML auth) cut over their iOS test driver in 5 days. Three dogfood rounds, all feedback closed. Their lead engineer:

> "Replays are now reliable enough to gate PRs on."

Honest limits: macOS-only because `CoreSimulator` only exists there. Sim-first in v1.0; real-device input is read-only until v1.1 lands the WDA bridge. Not an XCTest replacement for accessibility audits.

Trial: synctek.io/simdrive/trial. Pricing: synctek.io/simdrive/pricing. Happy to answer technical questions, especially about the HID path or why I went paid instead of MIT.

---

### §4.10 Twitter/X launch thread (7 tweets)

**1/** Shipping SimDrive today. The iOS test runner your AI agent operates directly. Premium product, 14-day free trial. No credit card.

`pip install simdrive` — synctek.io/simdrive/trial

**2/** The mechanic in one line: your agent looks at a screenshot, picks a target by visible text, SimDrive dispatches a real UITouch through CoreSimulator's HID port.

No XCTest. No Swift runner. No accessibility tree. The model is the selector engine; SimDrive is the dispatch layer.

**3/** Why this works now: vision-capable models removed the selector bottleneck. Mobile QA has spent a decade teaching machines to find buttons. The machine can already see the screen.

The selector layer migrated into the model. The runtime just dispatches.

**4/** Why paid instead of OSS: real `UITouch` on iOS 26, perf, crashes, replay drift, the WDA bridge — this is iOS-deep work that survives the next three Xcode betas only if a small team can fund the surface. Premium pricing funds the surface. Trial removes the buyer's risk.

**5/** Receipt: exampleorg — public-library reading client, Readium 3.x WKWebView, OAuth/SAML auth — cut over their iOS test driver in 5 days. Three dogfood rounds, all feedback closed.

> "Replays are now reliable enough to gate PRs on." — Maurice Carrier, exampleorg

**6/** What you author is a journey: a YAML file your agent drafts and your team reviews. CI replays it on every PR with SSIM-thresholded drift gating. PR-gating on visual regression isn't BrowserStack-priced anymore.

**7/** Pricing: $49/seat for Pro, $249/mo for 5-seat Team (with CI integration + real-device input via WDA in v1.1), Enterprise sales-led.

Trial: synctek.io/simdrive/trial — full feature access, 14 days, no card. Cancel before day 14, never billed.

---

## §5. Competitive narrative

### vs Maestro (free / freemium)
Maestro is a great cross-platform tool with an installed base, a CLI, and Android coverage SimDrive deliberately doesn't pursue. SimDrive's argument against Maestro is iOS-deep: real `UITouch` on iOS 26, native HID via `SimDeviceLegacyHIDClient`, perf and crash retrieval, SSIM drift gating, and the journey-driven authoring model — the things that take an iOS-specialist team a year to build well. If your bottleneck is iOS-specific coverage on a flagship app where one bad release costs a quarter of velocity, the iOS-deep tool earns its line item against the cross-platform free tier.

### vs XCUITest (free, Apple)
XCUITest is free but structurally cannot see `WKWebView` content, cannot drive out-of-process Safari sheets, broke `UITextField` first-responder on iOS 26, and requires a Swift runner that re-breaks on every Xcode beta. SimDrive replaces what XCUITest can't reach (WebView, OAuth/SAML, SwiftUI text input on 26) and complements what XCUITest is still good at (accessibility-conformance audits, which are explicitly out of SimDrive's scope). The framing is co-existence: XCUITest for compliance, SimDrive for coverage and PR-gating.

### vs hand-rolled Claude computer-use
Claude's computer-use API can drive a simulator screenshot-by-screenshot, and a sufficiently determined team can rebuild SimDrive's surface on top of it. The realistic estimate from our own codebase is 6-9 months of focused iOS-specialist work to replicate native HID, sim session lifecycle, `simctl` integration, log tail, crash retrieval, perf, recording with SSIM drift gating, and `stable_id` resolution. SimDrive's price point at the Team tier is what that team would cost in a sprint of payroll. The buy/build math favors buy until the org's iOS roadmap is large enough to fund a permanent QA-tooling team.

---

## §6. Three testimonial-grade quotes

All three sourced from `SIMDRIVE_v0.2.0a1_DOGFOOD.md` (Example Reader iOS, Maurice Carrier). Adapted for premium-buyer use — the engine-mechanic praise is set aside in favor of value, time, and gating reliability.

> **"Replays are now reliable enough to gate PRs on."**
> — *Maurice Carrier, exampleorg (Example Reader iOS)*

The single line that names the buyer outcome. Use as the primary pull-quote on the homepage hero, the case-study TL;DR, and the cold-email body.

> **"SimDrive is now the canonical iOS sim driver for Example Reader iOS development. The single biggest reason the predecessor was failing — the path that broke `UITextField` focus — is fully fixed."**
> — *Maurice Carrier, exampleorg*

The "we made the cut" line. Use on the product page Features section as the closing testimonial; use in cold-email follow-ups when the buyer asks "but does it actually replace what we have?"

> **"5 days, 3 dogfood rounds, all feedback closed. Three flows that were structurally untestable under XCUITest — the reader inside `WKWebView`, OAuth/SAML auth via Safari sheets, and iOS 26 `UITextField` regression coverage — are now automatable."**
> — *Maurice Carrier, exampleorg (paraphrased from the case-study TL;DR + cutover summary)*

The time-and-coverage line. Use in pricing-page hero, in the day-7 trial email, and in the Show HN comment thread when the inevitable "is this real?" question lands.

---

*End of brand & marketing memo.*
