# SimDrive 1.0 — Agentic-First GTM and Premium Pricing

**Author:** GTMPricingAtlas
**Date:** 2026-04-29
**Status:** R&D memo for BIS synthesis on SimDrive 1.0
**Frame:** premium-from-day-one with a free trial; no MIT engine; agentic-first distribution

This memo supersedes §6 (channels) and §8 (pricing) of `PRODUCTIZATION_PLAN.md` where the previous open-core plan conflicts with the chairman's premium-from-day-one direction. Brand and CLI naming revert to **SimDrive**. Existing PyPI releases `simdrive 0.1.0a1 → 0.3.0a3` (MIT) remain as historical artifacts — 1.0 ships under a new commercial license at a new track.

---

## §1. Pricing model — premium-from-day-one

Three tier structures evaluated. The product is a CLI + MCP server driving iOS sims and physical devices through journey-driven YAML over 29 primitives — single-developer-shaped, but with obvious team and CI value.

### Option A — Per-seat (JetBrains, GitHub Copilot model)

- **One price, no usage caps:** `$59 / seat / month` annual, `$69 / seat / month` monthly.
- **Trial:** 14 days, full features, no card required.
- **Pros:** predictable revenue, predictable buyer mental model, no "did I run too many journeys" anxiety. Annual billing maps cleanly to engineering tool budgets. License math is trivially defensible against Copilot ($19) and JetBrains ($24) — we are 2-3x because we replace a higher-friction job (iOS QA flake debugging, ~4 hours/week) and address a smaller market.
- **Cons:** undermonetizes high-usage CI accounts (a team running 10K journeys/night pays the same as a team running 100). No natural upgrade path inside the tier — once you have the seat, there is nothing to upsell except more seats.

### Option B — Usage-based (Datadog, BrowserStack App Automate model)

- **`$0.15 / journey-run`** with a $99/month minimum commitment that includes 660 runs.
- **Trial:** 100 free runs across 14 days.
- **Pros:** revenue scales with value delivered. CI-heavy buyers pay more, hobbyists pay less. Aligns the vendor's incentive with reliability — every flaky run we cause costs us reputational margin against a metered bill the customer can audit.
- **Cons:** unfamiliar pricing for a developer-tool buyer who has been trained on per-seat by Copilot and JetBrains. Forces the buyer to forecast usage (notoriously poor at it) before committing, which slows trial-to-paid conversion. Datadog's reputation for surprise bills is now a category headwind, not a tailwind.

### Option C — Tiered with usage caps (Cypress Cloud, Maestro Cloud model)

- **Solo:** `$49 / month` — 1 seat, 1 sim, 1 device, 50 journey-runs/month, 7-day replay retention.
- **Pro:** `$149 / month` — 5 seats, 4 sims, 4 devices, 500 journey-runs/month, 30-day replay retention, CI integration, Slack/Linear hooks.
- **Team:** `$499 / month` — unlimited seats, parallel CI runners, 5,000 journey-runs/month, 90-day retention, shared journey corpus, priority support.
- **Enterprise:** sales-led, $7,500–$25,000 / year — SSO, RBAC, SOC 2, audit logs, on-prem option, custom SLA.
- **Trial:** 14 days unlimited (Pro tier features).
- **Pros:** matches both how the product is used (1-engineer side project → CI suite → org-wide platform) and how comparable dev tools price (Cypress: $75 → $300 → $999; Maestro Cloud: $99 → $499 → enterprise). Three legible upgrade triggers (more seats, more runs, real-device CI). Annual contracts for Team+ are natural.
- **Cons:** four-tier matrix is more to explain on a pricing page than one-line per-seat. Usage caps create support tickets at month-end ("we ran out of runs, why is the CLI failing"). Mitigated by soft-cap behavior (overage at $0.20/run, no hard stop).

### Recommendation: **Option C — Tiered with usage caps, Pro at $149/month as the headline.**

Why Option C over A: the product has three buyers with measurably different willingness-to-pay (solo iOS engineer, mid-size team with CI, large org with compliance), and per-seat collapses that into one signal. Option C captures it.

Why Option C over B: a developer tool with 0 paying customers today cannot afford the conversion drag of unfamiliar pricing. Per-seat tiers with usage caps is the dominant pattern in the comparable set (Cypress Cloud, Maestro Cloud, Vercel, Linear, PostHog). Buyers know how to evaluate it in 90 seconds.

**Why $149/month for Pro is defensible:**
- **Above Maestro Cloud entry ($99):** justified by MCP-native integration, real UITouch on iOS 26, and the journey-driven flow over 29 primitives — capabilities Maestro does not match on the iOS-deep dimension.
- **Below BrowserStack App Automate entry ($199):** BrowserStack runs real-device cloud farms; we run local sim + customer-provided device, so our infra cost is materially lower and we should not pretend otherwise on price.
- **3x Cypress Cloud Solo ($75) but with 5 seats included** — buyer sees seat-equivalency at $30/seat which lands below Copilot Business ($19) plus the QA-tool premium.
- **Solo at $49** matches the prior plan's "undercut Maestro at $49" reasoning and serves the design-partner cohort coming out of Example Reader dogfood.

The pricing page in `simdrive/docs/gtm/pricing_page.md` should be rewritten against this structure (current draft references the open-core Engine, which does not exist in the 1.0 frame).

---

## §2. Free trial mechanics

- **Duration: 14 days.** 7 days is too short — iOS engineers ship on weekly cycles and cannot evaluate a CI gate inside a single sprint. 30 days is too long — our trial-to-paid signal slips by half a month, and the "I'll get to it next week" decay is real. 14 days is the JetBrains, Linear, Vercel, and Cypress default for a reason.
- **Trial gating: full Pro-tier features, soft usage cap of 250 journey-runs.** Feature-limited trials underdemo a tool whose value lives in the integration with CI and Slack. Hard-capping runs at 100 (Option B's idea) starves CI-curious buyers. 250 runs is enough for one engineer to wire a real PR-gate and watch it for a week.
- **Trial activation: email + CLI key, no credit card.** Card-required reduces top-of-funnel by ~60% (Stripe's published benchmark) and we cannot afford that compression at our funnel size. Email-only trades short-term funnel for long-term quality (some triallers will not convert, but the agentic-distribution channels we depend on are mostly developer-quality already). Activation flow: developer runs `simdrive trial start --email me@example.com`, receives signed key by email, paste-into-CLI, 14-day clock starts.
- **Post-trial (day 15): grace period 7 days, then read-only.** Hard CLI stop is hostile and breaks running CI suites — bad word-of-mouth. Read-only means observe/screenshot/report still work but `tap`, `type_text`, `swipe`, and `record/replay` return `license_required`. Customer can finish current debugging session, then upgrade or walk away.
- **Re-trial policy: no.** One trial per email + per machine fingerprint (CLI binds the trial key to the machine on activation). If a customer wants a longer evaluation, they can extend by emailing support — judgment call by Maurice. Standard policy across Cypress, Linear, JetBrains.
- **Realistic conversion benchmark: 3–5% from trial-start to paid.** Comparables: JetBrains 30-day-trial conversion runs around 8% per their public investor decks, but they have a captive enterprise install base. Cypress Cloud reports trial-to-paid in the 4–6% range. Datadog ~2–3%. We should plan around 4% for the first quarter and treat anything above 6% as a signal we are underpricing or undertargeting.

---

## §3. License enforcement

The technical mechanism. Premium pricing requires enforcement that is robust enough to defend revenue but not so heavy-handed that it breaks the agentic ergonomics. The bar: license enforcement must never make `simdrive observe` slower than 50ms.

### License key format

```
SD1-XXXX-XXXX-XXXX-XXXX
```

- Prefix `SD1` = SimDrive 1.x.
- 16 hex chars (4 groups of 4) = 64-bit machine-bound payload + signature.
- Signed with an Ed25519 keypair held by the SyncTek license server. Public key embedded in the CLI binary.
- Payload encodes: license tier, seat count, expiry, machine fingerprint hash, run-cap if applicable.

Activation: `simdrive license activate SD1-XXXX-XXXX-XXXX-XXXX`. CLI verifies signature offline against embedded public key, writes license blob to `~/.simdrive/license.json`, prints tier and expiry.

### License validation: offline-first with online refresh

- **Default mode: offline.** Every CLI command verifies signature locally. No network call on the hot path. This matches developer expectations (Linear CLI, JetBrains all work offline) and keeps `simdrive` usable on airplanes, in air-gapped CI, and behind corporate proxies.
- **Online refresh: weekly.** CLI pings `license.synctek.io/refresh` once every 7 days when network is available. Server returns updated license blob with current tier, run count, and expiry. If the customer downgraded, canceled, or hit a run cap, the new blob reflects it. If the CLI cannot reach the server for 30 consecutive days, it falls back to read-only with a clear warning ("could not refresh license, run `simdrive license refresh`").
- **Honest tradeoff:** offline-first means a determined pirate can monkey-patch the verifier in ~15 minutes. We accept that. The same is true of every commercial CLI we ship against (JetBrains, GitHub Copilot CLI, Charles Proxy). Piracy is not the constraint on a $149/month tool sold to developers with corporate cards. License-server downtime breaking customer CI is the bigger risk, and offline-first is the right choice against that risk.

### Trial enforcement

Trial keys carry a hard expiry timestamp in the signed payload. CLI compares `now()` to expiry on every command. Day 15: `tap` returns `trial_expired` with a one-line upgrade URL. Clock-rollback evasion (system date set to 2026-04-15) is detected by comparing local time to a monotonic timestamp written to the license blob on every successful run — if local time is ever before the last-seen monotonic timestamp, license is invalidated until refresh.

### Privacy posture: anonymous run-count telemetry only

- **Yes:** anonymous run count (one increment per `simdrive run` invocation), CLI version, OS version, license tier — sent on the weekly refresh ping. Used for license enforcement (run-cap tier) and product analytics (which tiers are growing).
- **No:** journey content, screenshots, app names, accessibility-identifier text, log output, command arguments. None of it touches our servers. The pricing page must say so explicitly.
- **Comparable:** matches JetBrains Toolbox (anonymized usage stats, opt-out). Stricter than Cypress Cloud (which records every test artifact by default). This is a real differentiator for the security-review buyer in Enterprise tier — surface it.
- **Opt-out:** `simdrive config set telemetry false` works at any tier except Team and Enterprise (which contractually owe us run-count data for billing).

### License revocation

- **Customer cancels:** license remains valid through end of current billing period, then transitions to read-only on next refresh. No retroactive lockout.
- **Payment failure:** 7-day grace, then read-only. Customer keeps full feature access during the grace window — Stripe handles dunning emails.
- **Hard revocation (chargeback, ToS violation):** server-side flag, takes effect on next refresh ping (max 7-day window). Read-only immediately if the CLI is online.
- **Refunds:** within 14 days, no questions, full refund. After 14 days, prorated. Standard SaaS practice, posted on pricing page.

---

## §4. Distribution channels (agentic-first, premium-adapted)

Premium changes one thing about agentic distribution: we cannot rely on free installs to drive funnel. The MCP registries and awesome-mcp lists still work, but the framing shifts from "install this MIT tool" to "start a 14-day trial of this premium tool." That changes which channels are usable.

| # | Channel | Premium-adapted action | Lift |
|---|---|---|---|
| 1 | **Anthropic MCP registry** (`claude.ai/mcp`) | Submit listing as `[Premium] SimDrive` with a 14-day-trial CTA. Anthropic does accept paid-product listings (per current registry guidelines, last reviewed 2026-04). The tag `[paid]` or `[trial]` is in active use by other listings. The trial mechanic is the unlock — registry policy requires a way for users to evaluate without payment. | **Step-change** |
| 2 | **`modelcontextprotocol/servers` GitHub PR** | Open PR under "Mobile / Testing" with `[paid]` tag in the description. Open-source-list etiquette: paid tools are accepted with a clear marker, rejected if framed as free. The "Try free for 14 days" framing meets the bar. | Modest, persistent |
| 3 | **Smithery.ai catalog** | Smithery accepts paid listings with a trial-required flag. Submit with full metadata + `pricing: paid, trial: 14d`. | Modest |
| 4 | **Cline + Cursor MCP marketplaces** | Cline accepts commercial MCP servers (per their April 2026 marketplace docs); Cursor's marketplace is more conservative but allows trial-gated tools. Both will require a screenshot of the trial flow. | Modest |
| 5 | **PyPI search + GitHub Topics** | Tag the repo with `mcp-server`, `ios-simulator`, `claude`, `anthropic`, `xctest-alternative`. Publish a marketing-only `simdrive` distribution to PyPI that prints a one-line "this is the premium SimDrive — visit simdrive.io to start a trial" message and exits with a non-error code. Avoids confusion with the legacy MIT 0.x track. | Modest, persistent |
| 6 | **`anthropics/anthropic-cookbook` PR** | Cookbook policy (per current contributor guidelines) does not accept examples that require a paid product as the primary integration. Workaround: contribute a recipe that uses Claude + a generic MCP iOS-driver pattern, with SimDrive listed as one implementation alongside the legacy MIT 0.3 release. The PR seeds training-corpus footprint without violating cookbook policy. | **Step-change** (long-tail) |
| 7 | **Training-corpus seeding** | Public docs at `simdrive.io/docs` indexable by Google and the next training cycle. Stack Overflow answers to real iOS-26 UITextField focus problems linking SimDrive as the workaround (with a "free trial" disclosure). GitHub Discussion thread on `modelcontextprotocol/servers` showing Example Reader dogfood data. None of these require the product to be free; they require the product to be useful. | **Step-change** (long-tail, compounds 6-12mo) |
| 8 | **MCP tool reviewers + dev advocates** | Direct outreach to ~15 named reviewers (the "MCP early adopters" segment from `dev_advocate_targets.md`) offering 90-day complimentary Pro licenses in exchange for a written review. Ethical disclosure required (FTC compliance for sponsored reviews). | Step-change for the named accounts; modest aggregate |
| 9 | **iOS QA conferences and podcasts** | Premium-friendly venues: AltConf, /dev/world, iOSDevUK, the Stacktrace podcast, Swift over Coffee, Mobile Dev Memo. Pitch SimDrive as the case study, not the ad. The hook is the engineering story (vision-first thesis, iOS 26 TextField, Example Reader dogfood) not the price. | Modest, durable |

**Channels we are NOT using:** paid Google ads (zero conversion ROI for $149/month dev tools), SEO content farms (poison the brand), outbound sales (wrong unit economics), LinkedIn growth-hacking (the audience here is on Twitter/X and Mastodon).

The big change vs the prior plan: cookbook PR drops from "step-change for SimDrive" to "step-change for our credibility" because Anthropic cookbook does not accept paid-product examples directly. We replace it as a primary funnel with **dev-advocate complimentary licenses** (channel 8) — same ~50-account reach, more directly attributable to MRR.

---

## §5. Conversion funnel — trial to paid

The 5-stage funnel (technically 6 with retention) for an agentic-first premium dev tool. Stage rates calibrated against Cypress Cloud's published benchmarks, JetBrains' investor data, and Datadog's S-1 disclosures — adjusted down by ~30% because we have zero brand recognition entering 2026.

| # | Stage | Rate | Notes |
|---|---|---|---|
| 1 | **Awareness** (impression on registry, PR, podcast, etc.) | 100% baseline | One impression = one event. Volume matters more than rate at this stage. |
| 2 | **Click through to `simdrive.io`** | 4–8% | Registry CTRs run 3–5%; podcast mentions 1–2%; warm dev-advocate posts 8–12%. Blended 5%. |
| 3 | **Click "Start free trial"** | 35–50% | Conditional on a clean pricing page with a clear price. Buyers self-select hard at this gate — most landing-page visitors never intended to buy a $149/month tool. 40% target. |
| 4 | **Activate trial (email + first run)** | 50–65% | Email-only trial removes friction here. Big drop is install failures (Xcode missing, Python version, sim setup). The `simdrive doctor` command is the highest-leverage fix for this stage. 55% target. |
| 5 | **Convert to paid (day 14)** | 3–5% | The most-uncertain rate. JetBrains 8%, Cypress 4–6%, Datadog 2–3% benchmarks. Plan for 4%. |
| 6 | **Retain month 2** | 85–92% | Standard SaaS retention for a sticky CI tool. Pro-tier monthly churn typically 8–15%. Annual prepay improves it materially — push annual at the upgrade flow. |

**Where the biggest leak is:** stages 4 and 5. Stage 4 (activation) is where install friction kills the funnel — every minute of `pip install` confusion costs us conversions. Stage 5 (paid) is where $149/month is asked for the first time and the buyer either has CI integration value to defend it or they don't.

**Highest-leverage fix:** kill the activation gap. `simdrive trial start` should run the equivalent of `simdrive doctor` immediately, fail loud on missing deps with one-line install instructions, and walk the user to their first journey run inside 5 minutes. Every minute saved here lifts stage 4 by ~5%, which compounds through stages 5 and 6.

**Second-highest leverage:** stage 5 conversion is gated by whether the buyer wired a real PR-gate inside the trial. A trial that ends with one local journey is hard to defend at $149/month; a trial that ends with a green check on `simdrive --gate` in CI is easy. Push CI integration in the day-7 trial email aggressively.

---

## §6. Path to $5K MRR — the real math

### Reverse-engineering the customer count

At the recommended pricing:

- $5,000 MRR ÷ $149 Pro = **34 paying Pro accounts**
- $5,000 MRR ÷ $499 Team = **11 paying Team accounts**
- $5,000 MRR ÷ $49 Solo = **103 paying Solo accounts**

Realistic blend for a 60-day push from zero base: 80% Solo, 18% Pro, 2% Team.

```
Solo:  82 accounts × $49  = $4,018
Pro:   18 accounts × $149 = $2,682
Team:   1 account  × $499 = $499
                    Total = $7,199 MRR
```

Or a lighter-weight blend (60% Solo, 35% Pro, 5% Team):

```
Solo:  30 × $49  = $1,470
Pro:   17 × $149 = $2,533
Team:   3 × $499 = $1,497
        Total    = $5,500 MRR
```

So somewhere around **50 paying customers** (mostly Solo, a meaningful Pro cohort, 1–3 Teams) gets us across $5K MRR. Working backward through the funnel at the rates above:

```
50 paying customers
÷ 4% trial-to-paid conversion
= 1,250 trial activations needed
÷ 55% activation rate (email → first run)
= 2,272 trial starts needed
÷ 40% landing-page → trial-start
= 5,681 simdrive.io visitors needed
÷ 5% impression → click-through
= 113,636 impressions needed
```

In ~60 days. **113K impressions in 60 days is ~1,900 impressions per day.**

### Is this realistic by July 2026?

Honest answer: **no, not as a SimDrive standalone target.** Three reasons:

1. **The product does not exist yet at the 1.0 stable + license-server bar.** v1.0 ships sim-only (per `PRODUCTIZATION_PLAN.md` §4) on the 2-week clock, but the license server, trial activation flow, and Stripe integration add 4–6 weeks of build on top of that. Realistic launch window is mid-June.
2. **113K impressions in 60 days requires distribution we don't yet have access to.** The MCP registry submission delivers maybe 15K cumulative impressions in the first 60 days based on comparable launches (Smithery, Cline). Cookbook PR is gated by Anthropic policy. Dev-advocate outreach to ~15 named accounts at ~5K followers each is ~75K reachable but ~5K actually impressed.
3. **Trial-to-paid takes ~21 days to read.** Day-1 trial starts can't pay until day 14, plus a few days of payment-processor lag. So a June 15 launch's first MRR datapoint lands ~July 5 — after the goal date.

**The realistic target by July: 200 trial activations and 8–12 paying customers ($1,500–$2,500 MRR).** Honest expansion of `PRODUCTIZATION_PLAN.md` §10's recommendation: re-cast July as a portfolio number with SimDrive contributing $1.5–$2.5K alongside the rest of the SyncTek revenue lines.

**The realistic SimDrive standalone target for $5K MRR: October 2026.** Same trajectory as the prior plan's October target, but with a cleaner premium-from-day-one motion: June launch → July first paying cohort → August scale to 30+ customers via dev-advocate licenses converting → September Team-tier upsell pushes the number through $5K. This is achievable with the funnel math above, run for 4 months instead of 2.

---

## §7. Launch sequence (D-7 to D+30)

Day-by-day. D0 is the SimDrive 1.0 + license-server-live launch date (target 2026-06-15). All actions concrete, all deliverables testable.

| Day | Action | Owner | Deliverable |
|---|---|---|---|
| D-7 | Cut SimDrive 1.0 RC to a known-good tag; freeze unless P0 | CodeAtlas + DeployAtlas | Tagged commit, RC live on internal PyPI mirror |
| D-7 | License server (`license.synctek.io`) live in production with Ed25519 keypair, key activation endpoint, refresh endpoint, Stripe webhook integration | DeployAtlas + CodeAtlas | All 4 endpoints return 200 on canary suite |
| D-7 | Stripe live mode active for $49 / $149 / $499 / annual prepay SKUs | DeployAtlas | All 8 SKUs visible in Stripe dashboard, test purchase succeeds |
| D-7 | `simdrive.io/pricing` live with Option C tier table | MarketingAtlas | Page renders, all 4 CTAs route correctly |
| D-6 | `simdrive.io/docs` indexable, robots.txt and sitemap shipped | MarketingAtlas | Google Search Console reports 0 errors |
| D-6 | Trial activation flow end-to-end test: email signup → license email → CLI activation → first journey run | TestAtlas | Pass/fail log; first-journey time under 5 minutes |
| D-5 | Anthropic MCP registry listing draft, premium-tagged | MarketingAtlas | Listing copy in `simdrive/docs/gtm/listings/anthropic-mcp-registry.md` |
| D-5 | `modelcontextprotocol/servers` PR draft with `[paid]` tag | MarketingAtlas | PR text in repo, branch pushed |
| D-5 | Show HN draft: "Show HN: SimDrive — premium MCP-native iOS sim driver, 14-day free trial" | MarketingAtlas | Draft in `simdrive/docs/gtm/listings/show-hn.md` |
| D-5 | Twitter/X launch thread draft (5 tweets) | MarketingAtlas | Draft in `simdrive/docs/gtm/listings/twitter-thread.md` |
| D-4 | 90-day complimentary Pro licenses provisioned for 15 dev-advocate targets | DeployAtlas + Maurice | 15 license keys generated, recipients confirmed |
| D-4 | GitHub release draft for `v1.0.0` | DeployAtlas | Draft visible on `gh release list` |
| D-3 | Anthropic dev-rel outreach email queued (template #3) | MarketingAtlas | Email draft, recipients confirmed |
| D-3 | First-3 dev advocates receive their license keys + a 1-page brief | Maurice | 3 confirmed-receipt replies |
| D-2 | Soft-launch ping to Example Reader + 2 friendlies; confirm trial flow on cold install | Maurice | 3 trial activations on D-1, no install blockers |
| D-2 | Triage and close any P0 / P1 from soft-launch | CodeAtlas + TestAtlas | Issue tracker clean |
| D-1 | Final dry-run: registry forms, Smithery, awesome-mcp PR, Show HN, Twitter, blog | Maurice | Checklist signed |
| D-1 | Pre-stage launch-day Twitter thread, LinkedIn post (Maurice's personal page only) | Maurice | Drafts in scheduler |
| D0 | 09:00 PT — Anthropic MCP registry submission | Maurice | Submission ID logged |
| D0 | 09:05 PT — Smithery.ai submission | Maurice | URL logged |
| D0 | 09:15 PT — `modelcontextprotocol/servers` PR opened | Maurice | PR URL logged |
| D0 | 09:30 PT — Show HN posted | Maurice | URL logged; first-comment reply has install line |
| D0 | 09:45 PT — Twitter thread posted | Maurice | Thread URL logged |
| D0 | 10:00 PT — GitHub release `v1.0.0` published | DeployAtlas | Release URL logged |
| D0 | 10:30 PT — Launch blog post live on synctek.io | MarketingAtlas | Blog URL logged |
| D0 | All-day — HN comment monitoring, 15-min reply window for first 10 | Maurice | Comment screenshots saved |
| D+1 | Triage HN feedback into 3 buckets (bug / feature ask / positioning) | CodeAtlas | Issue list with labels |
| D+1 | Anthropic dev-rel outreach email sent | Maurice | Email timestamp logged |
| D+1 | First trial-to-paid conversion call (if any trials hit "ready to upgrade") | Maurice | Call notes filed |
| D+2 | PyPI download + license-server activation count baseline | DeployAtlas | Numbers in launch-receipts.md |
| D+3 | Cline + Cursor MCP marketplace submissions | Maurice + MarketingAtlas | Both submissions logged |
| D+3 | First 3 dev-advocate complimentary licenses redeemed; check usage telemetry | DeployAtlas | Run-count > 5 per advocate confirmed |
| D+5 | Reply to all HN comments older than 24h | Maurice | Comment thread closed out |
| D+5 | Cookbook PR (generic MCP iOS-driver pattern, SimDrive as one implementation) | MarketingAtlas + CodeAtlas | PR opened |
| D+7 | Week-1 metrics review: trial activations, paid conversions, registry approvals | Maurice + MarketingAtlas | `simdrive/docs/gtm/week1-review.md` |
| D+7 | Second-tier dev-advocate outreach (5 more named accounts) | Maurice | 5 emails sent |
| D+10 | First trial-to-paid conversion expected (day-14 post-launch trials closing) | DeployAtlas + Maurice | First MRR datapoint |
| D+10 | Training-corpus essay #1 published: "Why we replaced XCTest with screenshots" | MarketingAtlas | Essay live; 3 inbound links |
| D+14 | Cookbook PR review pass | CodeAtlas | PR moved to "approved" or "merged" |
| D+14 | Training-corpus essay #2: SO answer on iOS-26 UITextField focus, SimDrive linked | MarketingAtlas | SO URL logged |
| D+17 | Mid-month conversion check, churn read on first paid cohort | Maurice | Numbers in week2-review.md |
| D+21 | Training-corpus essay #3: GH Discussion in `modelcontextprotocol/servers` with Example Reader dogfood data | MarketingAtlas | Discussion URL logged |
| D+21 | iOS QA podcast pitch round (3 podcasts, template #6) | Maurice | 3 pitches sent |
| D+25 | v1.1 (real-device WDA) plan freeze | Maurice + CodeAtlas | `simdrive/docs/v1.1-plan.md` |
| D+28 | Design-partner status: trials → paid → expansion | Maurice | LOI count and MRR snapshot |
| D+30 | D+30 retrospective: numbers, lessons, next-30-day plan | Maurice + GTMPricingAtlas | `simdrive/docs/gtm/d+30-retro.md` |

---

## §8. Three execution priorities for the next 30 days

Concrete, owned, dated. Anchors the execution.

| # | Priority | Owner | Deadline | Done means |
|---|---|---|---|---|
| 1 | **Build the license server + Stripe integration end-to-end.** Endpoints (`/activate`, `/refresh`, `/webhook`), Ed25519 keypair, Stripe live SKUs, trial activation email flow. This is the single hardest dependency for premium-from-day-one — without it there is no launch. | DeployAtlas + CodeAtlas | **2026-05-29** | All endpoints green on canary suite; one test purchase round-trips through Stripe live mode and creates a working CLI license. |
| 2 | **Ship `simdrive.io/pricing` and the trial activation funnel.** Pricing page (Option C), trial signup, license-key email, CLI activation flow, first-journey-in-5-minutes path. Funnel stages 2–4 from §5 must render correctly before D-7. | MarketingAtlas + CodeAtlas | **2026-06-05** | End-to-end smoke: cold install → email signup → license activation → first `simdrive run` returns success in under 5 minutes. |
| 3 | **Provision and ship 15 dev-advocate complimentary Pro licenses with 1-page briefs.** Channel 8 from §4 is the highest-leverage paid-product distribution we have. The recipients are the named accounts in `dev_advocate_targets.md` "MCP early adopters" + "iOS QA leads" segments. | Maurice + DeployAtlas | **2026-06-12** | 15 licenses generated, 15 briefs sent, ≥10 confirmed-receipt replies, ≥5 published reviews/posts referencing SimDrive within D+14. |

---

*End of memo.*
