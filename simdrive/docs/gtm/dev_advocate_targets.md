# SpecterQA — Dev-Advocate Target List (30-day outreach)

Twenty names, six categories, one outreach hook each. Reach out via `outreach_templates.md` template #1 (reviewers), #2 (QA leads), #3 (Anthropic), #4 (marketplace), #5 (design partners), or #6 (podcasts) as appropriate.

**Confidence convention:** every entry is tagged `confirmed` (the person and role are verifiable from public training data), `likely` (the person is publicly active in the space but the specific role / handle / project may have shifted), or `[research needed]` (we know the slot exists but the human in it has to be filled in by Maurice or a research pass before the email goes out). Do not fabricate. Where a slot says `[research needed]`, the row is still useful — it specifies what to look for.

The list trends conservative on names because the agentic-iOS-testing intersection is small enough that getting one name wrong is more damaging than a short list.

---

## Category A — Anthropic dev-rel and MCP team

These outreach paths matter most. Anthropic's MCP team is the registry gatekeeper and the cookbook reviewer. Approach via the warm/official channels (registry submission, cookbook PR), not cold-LinkedIn.

| # | Name | Role | Contact | Why they matter | Hook |
|---|---|---|---|---|---|
| 1 | `[research needed]` — Anthropic MCP team lead | Engineering lead, Model Context Protocol | Reach via `mcp@anthropic.com` or via cookbook PR cc | Owns the MCP protocol direction; iOS-deep MCP server is a credible registry case study | Lead with the cookbook PR (template #3). The PR is the artifact; the email is the courtesy ping. |
| 2 | `[research needed]` — Anthropic dev advocate | Developer Relations, Claude API and Claude Code | Reach via Anthropic developer Twitter / X account or `developers@anthropic.com` | Public face of Anthropic dev-rel; covers MCP integrations on the official channels | "We are the iOS-Simulator MCP server you do not have to build." 5-min demo offer. Template #1. |
| 3 | Mahesh Murag | Anthropic — has presented MCP technical content at conferences (`likely` — public talks on MCP available) | Twitter / GitHub `[research needed]` | Active voice on MCP technical depth; iOS-deep is a natural extension | Cookbook PR notification + offer to coordinate on a follow-on blog post about the iOS use case. |

---

## Category B — MCP early adopters and contributors

People who have shipped MCP servers, contributed to `modelcontextprotocol/servers`, or built MCP-aware clients. Distribution leverage: a single retweet from this category puts SpecterQA into 5,000+ MCP-curious feeds.

| # | Name | Role | Contact | Why they matter | Hook |
|---|---|---|---|---|---|
| 4 | `[research needed]` — top contributor to `modelcontextprotocol/servers` | OSS contributor, MCP servers monorepo | GitHub profile via `git log --format='%an %ae' modelcontextprotocol/servers \| sort -u \| head` | Has commit credibility; their thumbs-up on the SpecterQA PR speeds review | "We are submitting an iOS-Simulator MCP server. Would value your eyes on the PR before we open it formally." Template #1 with a code-review framing. |
| 5 | `[research needed]` — Cline maintainer / dev-rel | Maintainer or DevRel, Cline (the VSCode MCP client) | GitHub: `cline/cline` org | Owns the Cline MCP marketplace listing path | Template #4. |
| 6 | `[research needed]` — Cursor team (MCP integrations) | Engineer or product lead, Cursor | `careers@cursor.com` is the public address; better path is via Cursor team members on X | Cursor MCP ecosystem is the second-largest after Claude desktop | Template #4. Lead with the cookbook PR if it has shipped. |
| 7 | Simon Willison | Engineer, blogger (`simonwillison.net`) — `confirmed` covers MCP | `@simonw` on X, blog comment form | Writes the most-read independent coverage of MCP and Claude tooling; a Simon Willison weeknotes mention is the single highest-leverage independent endorsement available | "Vision-first MCP server for iOS sims; here is the Palace dogfood. If it is interesting enough for weeknotes, the install is one line." Template #1. |
| 8 | `[research needed]` — Smithery.ai team | Founder or dev-rel, Smithery.ai | Listed on smithery.ai/about | Catalog gatekeeper; faster-moving than the Anthropic registry | Template #4 framed at the catalog submission. |

---

## Category C — iOS testing thought leaders

The iOS engineering community is small and trust-driven. A nod from this category lands harder than 100 cold emails to QA leads. Be honest about what SpecterQA is and is not — these people read carefully.

| # | Name | Role | Contact | Why they matter | Hook |
|---|---|---|---|---|---|
| 9 | Pol Piella | iOS engineer, blogger (`polpielladev.com`), `confirmed` writes on Swift testing | `@polpielladev` on X | Mid-deep iOS testing audience; writes the post that 5,000 iOS engineers will read on a Saturday morning | "iOS 26 broke XCUITest's UITextField focus. Here is the working alternative — and the Palace dogfood receipts." Template #1. |
| 10 | Paul Hudson | Engineer, author of Hacking with Swift (`hackingwithswift.com`), `confirmed` | `@twostraws` on X | Largest iOS-Swift-tutorial audience; not a direct buyer but a credibility multiplier | "Vision-first iOS testing — short post for HWS readers if it fits the editorial calendar?" Template #6, podcast-pitch shape. |
| 11 | John Sundell | Engineer, Swift by Sundell podcast (`swiftbysundell.com`), `confirmed` | `@johnsundell` on X | Podcast platform — see Category D | Template #6, the SpecterQA origin-story hook. |
| 12 | Antoine van der Lee | Engineer, blogger (`avanderlee.com`), `confirmed` writes on iOS testing and CI | `@twannl` on X | iOS-testing-curious readership; Antoine's posts surface in CI-tooling discussions | "iOS-26 SwiftUI + WebView test gap; vision-first MCP driver as the patch." Template #1. |
| 13 | Donny Wals | Engineer, blogger (`donnywals.com`), `confirmed` Swift Concurrency + testing content | `@donnywals` on X | Concurrency-aware test audience; the `wait_for_keyboard` debounce work in SpecterQA is the kind of detail he reads | Template #1, lead with the type_text-on-iOS-26 fix. |
| 14 | Shashank Mohabia | iOS testing community contributor (`likely` — speaks at iOS test conferences) | `[research needed]` — confirm current handle | Tactical iOS QA lead who writes from the trenches | Template #2 framed as "tools from the trenches," not as a sale. |

---

## Category D — Mobile dev podcasters

Single best ROI per minute of effort: a 45-minute episode on a podcast in this category puts SpecterQA into thousands of iOS-engineer commutes. The pitch is template #6.

| # | Name / Show | Hosts | Contact | Why they matter | Hook |
|---|---|---|---|---|---|
| 15 | Stacktrace | John Sundell + Gui Rambo (`confirmed` historic; current cadence `[research needed]`) | Show contact form on `stacktracepodcast.fm` | iOS engineer audience, deep-tech format | "We deleted the accessibility tree in our iOS test driver. Here is what happened." Template #6. |
| 16 | Swift over Coffee | Paul Hudson + Sundell (`likely`; cross-checks with item 10/11) | Via `swiftovercoffee` listing | Lighter format; better for the brand-introduction episode than the technical deep-dive | "Vision-first iOS testing — 25-min explainer episode?" Template #6. |
| 17 | App Force One | Hosts `[research needed]` — confirm current panel | Show site `[research needed]` | Indie iOS focus; SpecterQA's pip-install simplicity plays well | "Indie-friendly iOS testing tool, MIT, no Xcode target." Template #6. |
| 18 | Empower Apps | Leo Dion (`confirmed`) | `@LeoG_Dion` on X | Deep-tech indie-iOS audience; appropriate format for the SpecterQA origin story | Template #6, origin-story hook. |

---

## Category E — YouTubers covering Claude / Cursor / MCP

Video reach is the fastest distribution channel for the "watch the agent drive an iOS sim" demo. Goal: one good 5-minute SpecterQA clip in any of these channels.

| # | Name / Channel | Focus | Contact | Why they matter | Hook |
|---|---|---|---|---|---|
| 19 | `[research needed]` — top "Claude Code tutorial" channel | YouTube (Claude Code, Cursor, MCP) | YouTube channel email | Aggregates the LLM-coding-tooling audience that is most likely to want an iOS testing layer | "30-second hero GIF + a 5-min screen-record for your channel?" Template #1. |
| 20 | `[research needed]` — top "MCP server demo" creator | YouTube / TikTok | YouTube channel email | Demand for novel MCP servers exceeds supply right now — being the iOS-Simulator entry is the hook | Template #1, lead with the install-line simplicity. |

---

## Category F — iOS QA leads at target companies (5 prospects beyond Palace)

Same shape as Palace iOS: medium iOS app, WebView-heavy flows, OAuth/SAML in-app, accessibility-tree gaps Palace's team felt acutely. Names below are slot-shaped and `[research needed]` because the right contact at each company moves quickly. Do not fabricate.

| # | Company shape | Why it fits | Outreach |
|---|---|---|---|
| 21 | A library / e-reader iOS app at a public-sector or non-profit publisher | Same WebView + DRM-reader profile as Palace | LinkedIn search "iOS lead" + company; template #2 |
| 22 | A mid-size fintech iOS app with OAuth + biometric flows | OAuth sheet + biometric prompt = XCTest blind spot SpecterQA is built for | Template #2; reference Palace's SSIM-gated CI |
| 23 | A music or audio iOS app with WebView lyrics / sheet | WebView-heavy; iOS 26 SwiftUI components | Template #2 |
| 24 | A health / fitness iOS app with HealthKit + WebView dashboards | HealthKit prompts + WebView dashboards = AX-tree gap | Template #2 |
| 25 | A crypto / wallet iOS app with WalletConnect + WebView | WebView + dApp interactions invisible to XCUITest | Template #2 |

---

## Operational notes

- **Never send template #1 to all 20 in one batch.** Send 5 per week. A bad reply in week 1 changes the framing for week 2.
- **Track replies** in a single document, not a CRM. The 30-day surface is small enough that a markdown table beats Salesforce noise.
- **Confidence tags update.** If we confirm a `[research needed]` person and email them successfully, change the tag to `confirmed` in the next revision.
- **Categories C and D have the highest credibility-per-effort ratio.** If the 30-day window has to compress, drop Categories E and F first; never drop C and D.
- **Category A is gated on the cookbook PR opening.** Do not send those emails before the PR is in draft.
