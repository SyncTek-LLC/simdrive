# SpecterQA — Outreach Templates

Seven engineer-to-engineer email templates for the first 30 days post-launch. Voice matches the rest of the project: state the change then the why, numbers not adverbs, no marketing speak. Each template under 200 words. Personalization slots in `[BRACKETS]`.

Send from `maurice.carrier@synctek.io`. No tracking pixels. No "just following up" three-day cadence. One send, one polite follow-up after 7 days if there is no reply, then drop it.

---

## 1. Cold outreach to a dev-tool reviewer

**Subject:** MCP-native iOS sim driver — 5-min demo if it interests you

Hi [FIRST_NAME],

I saw your [POST/VIDEO/THREAD] on [SPECIFIC_TOPIC] — [ONE_LINE_REACTION_THAT_PROVES_I_READ_IT]. I am the maintainer of SpecterQA, an MCP-native driver for the iOS Simulator. It ships 29 tools, real UITouch on iOS 26 (the kind that actually focuses UITextField, which XCUITest stopped doing a year ago), and stable_id replay. Example Reader iOS migrated off their previous driver in 5 days and is now PR-gating on SSIM-thresholded replays.

Install is one line: `pip install specterqa-ios`. No Xcode target to build, no provisioning profile dance.

If a 5-minute demo would be useful for [PUBLICATION/CHANNEL], I am happy to record one against any iOS app you pick — or against the bundled TestKitApp if you would rather see a clean baseline. The 30-second hero GIF is at [LINK]. The Example Reader dogfood report (every recommendation we did not ship is listed) is at [LINK].

Either way, thanks for the work you put into [SPECIFIC_TOPIC].

— Maurice
SyncTek LLC

---

## 2. Cold outreach to an iOS QA lead at a target company

**Subject:** [COMPANY]'s iOS regression suite — vision-first sim driver?

Hi [FIRST_NAME],

I run an open-source iOS testing tool called SpecterQA. The thesis is that XCUITest stopped working on iOS 26 SwiftUI + WebView-heavy apps, and the right replacement is a vision-first MCP driver that an LLM can call directly. 29 MCP tools, MIT license, ships through PyPI as `specterqa-ios`. Real reference customer: Example Reader iOS (ExampleOrg) — they cut over from their previous driver in 5 days, three feedback rounds all closed, and now PR-gate on SSIM 0.85 against canonical journeys.

I am reaching out because [COMPANY]'s [SPECIFIC_PRODUCT_OR_FLOW] looks like it sits in the same gap Example Reader was in — [SPECIFIC_REASON: WebView-heavy, OAuth/SAML flows, SwiftUI without AX identifiers, etc.]. Would a 30-minute call to walk through the architecture and the Example Reader dogfood data be useful? No pitch deck, no sales motion — I am the engineer.

Install line if you want to look first: `pip install specterqa-ios`. Repo: [LINK]. Example Reader dogfood report: [LINK].

— Maurice
SyncTek LLC

---

## 3. Outreach to Anthropic's MCP team for cookbook PR coordination

**Subject:** Cookbook PR — drive an iOS sim with Claude (SpecterQA)

Hi [FIRST_NAME],

I am opening a PR against `anthropics/anthropic-cookbook` adding a 30-line "Drive an iOS Simulator with Claude" recipe. The MCP server is SpecterQA (`pip install specterqa-ios`, 29 tools, MIT). The recipe walks through `session_start` → `observe` → a `tap_text` action against a bundled TestKitApp — no developer setup beyond Xcode and the simulator that ships with it.

Three things I would value your input on, none of them blocking:

1. Whether you would prefer the recipe in the `examples/` notebook style or as a standalone `.py` script under `mcp/` — I have drafted both and will land whichever fits the existing pattern best.
2. Whether the SpecterQA listing in claude.ai/mcp registry [SUBMISSION_ID] is moving through review. Happy to provide whatever the review needs.
3. Any iOS-specific constraints on the cookbook — e.g., does the recipe need to run in a CI matrix, or is local-only acceptable?

PR link: [LINK_WHEN_OPEN]. Cookbook repo discussion thread, if you prefer that channel: [LINK].

Thanks — I know the queue is long.

— Maurice
SyncTek LLC

---

## 4. Outreach to Cline / Cursor for marketplace listing

**Subject:** SpecterQA — MCP server for [CLINE/CURSOR] marketplace

Hi [FIRST_NAME],

Submitting SpecterQA for the [CLINE/CURSOR] MCP marketplace. It is an iOS Simulator driver, MCP-native, 29 tools, MIT-licensed, on PyPI as `specterqa-ios`. Use case: a [CLINE/CURSOR] user with an iOS project asks the agent to "test the login flow," and the agent drives the actual simulator — observes via a real screenshot, taps via real UITouch, replays deterministically.

Reference customer: Example Reader iOS (ExampleOrg) — full migration off their previous driver in 5 days. They are PR-gating on SSIM-thresholded replays.

I have followed the [CLINE/CURSOR] marketplace submission template at [LINK_TO_OUR_SUBMISSION_BRANCH]. Three things I want to confirm before opening the PR:

1. Is there a preferred format for the install line in the listing — `pip install` versus a bundled-binary approach?
2. Does the marketplace surface MCP servers that require local toolchain deps (Xcode, Simulator)? If yes, where in the listing should that be flagged?
3. Anything else I should preflight before opening the PR.

Happy to do a 15-minute screen share if it speeds review.

— Maurice
SyncTek LLC

---

## 5. Design-partner pitch (Cloud beta)

**Subject:** SpecterQA Cloud beta — 60 days free, monthly feedback

Hi [FIRST_NAME],

We are recruiting design partners for the SpecterQA Cloud beta — the hosted layer that adds replay archive, SSIM-trend dashboards, multi-sim parallelism, and (in v1.1) real-device input via WebDriverAgent on top of the open-source Engine you already have.

The deal is simple: 60 days free on what will become the Team tier ($249/month). In exchange, one 30-minute call per month to tell us what is working and what is not. No paid pilot, no contract, no obligation to convert. If at the end of 60 days the Cloud is not earning its keep for [COMPANY], you go back to the free Engine and we keep the feedback.

We are looking for [COMPANY] specifically because [SPECIFIC_REASON: medium iOS app size, WebView-heavy flows, agent-driven CI ambitions, prior dogfood relationship, etc.]. The Engine you would be running underneath is the same code Example Reader iOS migrated to in 5 days — repo and dogfood report at [LINKS].

Cloud beta opens [DATE]. Five slots, allocated in reply order. Worth a call?

— Maurice
SyncTek LLC

---

## 6. Conference / podcast pitch

**Subject:** Talk pitch — "Why we deleted the accessibility tree"

Hi [FIRST_NAME],

Pitching a talk for [PODCAST/CONFERENCE]. The story is the SpecterQA pivot: we shipped a legacy XCTest-based iOS driver through 16 major versions, watched it die on iOS 26 SwiftUI + WebView surfaces, and then deleted the accessibility-tree selector layer entirely. The replacement is vision-first — same shape as Anthropic Computer Use and OpenAI Operator, applied to iOS. It worked: Example Reader iOS (ExampleOrg) cut over from their previous driver in 5 days, three feedback rounds all closed.

Talk format: 25 minutes, technical, code-on-screen. Concrete artifacts:

- The runner stack trace that ended the AX-tree era (a real `XCUIElementQuery[label]` `NSException`).
- The five-line `observe` call that replaced it, with the actual screenshot the agent reasons about.
- The Example Reader migration delta — what worked, what we got wrong, what we shipped to fix it.

The hook for [PODCAST/CONFERENCE] specifically: [SPECIFIC_HOOK — agentic testing, iOS 26 changes, MCP ecosystem, etc.].

Slot lengths I can hit: 15-min lightning, 25-min standard, 45-min deep-dive with Q&A. No keynote ambitions.

— Maurice
SyncTek LLC

---

## 7. Customer reference ask (template — Example Reader already aligned)

**Subject:** Reference call request — [PROSPECT_COMPANY]

Hi [REFERENCE_FIRST_NAME],

[PROSPECT_COMPANY] is evaluating SpecterQA for [SPECIFIC_USE_CASE: iOS-26 UITextField regression, WebView coverage, etc.]. They have asked for a reference call with a current user. You are the strongest reference we have — the 5-day cutover and the SSIM-gated PR pattern are exactly the proof points they want.

The ask is one 30-minute call, on a date that works for you, with [PROSPECT_FIRST_NAME] (their [PROSPECT_ROLE]). I will send the calendar invite and a one-page brief on what they are evaluating against. You decide what you say — we are not asking you to read a script, and we are happy for you to flag rough edges. The honesty is the value.

If a call does not work, a 5-line email reply to a 5-line ask from [PROSPECT_FIRST_NAME] would also be useful — same content, less synchronous.

If you would rather not, please say so — references are a favor, not an obligation.

— Maurice
SyncTek LLC
