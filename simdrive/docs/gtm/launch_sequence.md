# SpecterQA — D-7 to D+30 Launch Sequence

**Owner:** Maurice Carrier, SyncTek LLC
**Target launch date (D0):** 2026-05-08 (the Anthropic MCP registry submission deadline in the productization plan)
**Status:** plan of record. Each row is a discrete action with a testable deliverable.

The sequence assumes `specterqa-ios 17.0.0a1` is already on PyPI. The launch is a coordinated push across MCP registries, awesome-mcp lists, the Anthropic cookbook, and a single Show HN. No paid channels. No press wire. Distribution is registry placement plus a small set of warm hand-offs to people who already cover MCP and iOS testing.

The "Owner" column uses the existing department roles. Where a row says "Maurice", the human Chairman is the operator; the agent stack drafts and stages.

| Day | Action | Owner | Deliverable |
|---|---|---|---|
| D-7 | Cut `specterqa-ios 17.0.0a1` to a known-good tag; freeze the version unless a P0 lands | CodeAtlas + DeployAtlas | Tagged commit, PyPI release confirmed via `pip index versions specterqa-ios` |
| D-7 | Re-run the Palace dogfood smoke (catalog → book detail → tab tour → typed search) against 17.0.0a1 to confirm no regression vs `simdrive 0.2.0a1` | TestAtlas | Pass/fail log written to `simdrive/dogfood/2026-05-01-relaunch-smoke.md` |
| D-7 | Auto-generate the README tool table from `_TOOLS`; confirm it reads "29 MCP tools" everywhere | CodeAtlas | PR merged; CI check fails on drift |
| D-6 | Draft the Anthropic MCP registry listing copy (200-word description, install line, tool count, demo GIF reference) | MarketingAtlas | `simdrive/docs/gtm/listings/anthropic-mcp-registry.md` |
| D-6 | Draft the `modelcontextprotocol/servers` README PR text under "Mobile / Testing" | MarketingAtlas | `simdrive/docs/gtm/listings/mcp-servers-pr.md` |
| D-6 | Draft the Smithery.ai catalog metadata YAML | MarketingAtlas | `simdrive/docs/gtm/listings/smithery.yaml` |
| D-6 | Record the 30-second hero GIF: `session_start({})` → `observe()` → `tap_text("Borrow")` against TestKitApp | Maurice | `simdrive/docs/brand/hero-30s.gif` (≤2 MB) |
| D-5 | Draft the Show HN post (title under 80 chars, first paragraph under 60 words, install line, link to README, link to Palace dogfood) | MarketingAtlas | `simdrive/docs/gtm/listings/show-hn.md` |
| D-5 | Draft the launch-day Twitter/X thread (5 tweets max: position, install, demo GIF, Palace receipt, link) | MarketingAtlas | `simdrive/docs/gtm/listings/twitter-thread.md` |
| D-5 | Update PyPI long description to point at SpecterQA wordmark, the 29-tool count, and the Palace testimonial | DeployAtlas | New release-only metadata bump (no code change) |
| D-5 | Reach out to 3 soft-launch users (target: Palace's Maurice + 2 from the dev-advocate list categories "MCP early adopters" and "iOS QA leads") with a "kicking the tires?" ask for D-1 | Maurice | 3 replies logged in `simdrive/docs/gtm/soft-launch-replies.md` |
| D-4 | Open a draft GitHub release for the `v17.0.0a1` tag with full release notes (lifted from CHANGELOG, no marketing additions) | DeployAtlas | Draft visible on `gh release list --limit 5` |
| D-4 | Schedule the Anthropic MCP registry submission for D0 09:00 PT via the registry's web form (do not submit yet) | Maurice | Submission staged; screenshot saved |
| D-4 | Schedule the Smithery.ai submission for D0 09:00 PT | Maurice | Submission staged; screenshot saved |
| D-3 | Draft the `anthropics/anthropic-cookbook` PR: a 30-line "Drive an iOS sim with Claude" recipe in `examples/iOS_simulator_with_specterqa.ipynb` style | MarketingAtlas + CodeAtlas | PR branch pushed to a SpecterQA-org fork; PR not yet opened |
| D-3 | Draft Anthropic dev-rel outreach email (template #3 from `outreach_templates.md`) and queue for D+1 | MarketingAtlas | Email draft saved; recipient confirmed |
| D-3 | Add GitHub topics to the public repo: `mcp-server`, `ios-simulator`, `claude`, `anthropic`, `xctest-alternative`, `vision-first-testing` | DeployAtlas | Topics visible on `gh repo view --json repositoryTopics` |
| D-3 | Add the SpecterQA badge to the README hero (PyPI version + MCP-registry-soon + license MIT) | CodeAtlas | Visible on the rendered README |
| D-2 | Send the soft-launch users a fresh `pip install --pre specterqa-ios` and ask them to file 1 issue (positive or negative) | Maurice | 3 issues filed on the public repo |
| D-2 | Triage and close any blocking issues from the soft-launch round | CodeAtlas + TestAtlas | All P0/P1 resolved; release notes updated if any code shipped |
| D-1 | Final dry-run: open the Anthropic registry submission form, the Smithery form, the awesome-mcp PR, the Show HN editor — confirm copy and links render correctly | Maurice | Dry-run checklist signed in `simdrive/docs/gtm/launch-day-checklist.md` |
| D-1 | Pre-stage the launch-day Twitter thread in a scheduler (or paste-ready document); pre-stage the LinkedIn announcement on Maurice's personal page only | Maurice | Drafts visible in scheduler / clipboard |
| D-1 | Publish a fresh `simdrive/docs/gtm/launch-receipts.md` skeleton — the file we'll fill with timestamped links as submissions go live | MarketingAtlas | File present, sections empty |
| D0 | 09:00 PT — submit Anthropic MCP registry listing | Maurice | Submission ID logged in launch-receipts.md |
| D0 | 09:05 PT — submit Smithery.ai listing | Maurice | Submission URL logged |
| D0 | 09:15 PT — open the `modelcontextprotocol/servers` PR | Maurice | PR URL logged |
| D0 | 09:30 PT — open Show HN with the title "Show HN: SpecterQA — MCP-native iOS simulator driver" | Maurice | HN URL logged; first comment a reply with the install line |
| D0 | 09:45 PT — post the Twitter/X thread; tag `@AnthropicAI` only on tweet 1 and only because the registry submission is genuinely warm | Maurice | Tweet thread URL logged |
| D0 | 10:00 PT — publish the GitHub release for `v17.0.0a1` (move from draft to published) | DeployAtlas | Release URL logged |
| D0 | 10:00 PT — push the PyPI long-description update | DeployAtlas | New PyPI page rendered with the SpecterQA branding |
| D0 | 10:30 PT — publish the launch blog post on synctek.io (re-uses the press kit "Background story" + a launch-day diff) | MarketingAtlas | Blog URL logged |
| D0 | All-day — Maurice monitors HN, replies to first 10 comments within 15 minutes of arrival; no pile-on, no canned responses | Maurice | Comment-thread screenshot saved at end of day |
| D+1 | Triage HN feedback into 3 buckets: bug (file issue), feature ask (label `gtm-d+1`), positioning gap (queue for D+7 README pass) | CodeAtlas | Issue list with labels; counts in launch-receipts.md |
| D+1 | Open the `anthropics/anthropic-cookbook` PR; reference the registry submission ID | Maurice | PR URL logged; Anthropic dev-rel cc'd via the email queued on D-3 |
| D+1 | Send Anthropic dev-rel the cookbook PR notification (outreach template #3) | Maurice | Email sent timestamp logged |
| D+2 | Watch PyPI download counter (`pypistats recent specterqa-ios`); compare D-7 baseline → D+2 | DeployAtlas | Download delta in launch-receipts.md |
| D+2 | Reach out to 5 design-partner candidates (template #5 — Cloud beta, free 60 days for monthly feedback). Names from `dev_advocate_targets.md` "iOS QA leads at target companies" | Maurice | 5 emails sent; replies tracked in `simdrive/docs/gtm/design-partner-funnel.md` |
| D+3 | Cline + Cursor MCP marketplace submissions | Maurice + MarketingAtlas | Both submissions logged; status open/pending |
| D+3 | First post-launch dogfood: pick a new user from HN comments who says "I'll try this" — offer a 30-min pair install, harvest the friction log | Maurice | Dogfood report `simdrive/dogfood/2026-05-D+3-<user>.md` |
| D+5 | Reply to every HN comment older than 24h that hasn't been answered | Maurice | Comment thread closed out |
| D+5 | Cookbook PR review pass (assume Anthropic asks for revisions); ship them | CodeAtlas | PR moved to "approved" or "merged" |
| D+7 | Week-1 metrics review: PyPI installs, GitHub stars, registry approval status, design-partner reply count | Maurice + MarketingAtlas | Numbers in `simdrive/docs/gtm/week1-review.md`, no commentary |
| D+7 | Second-tier outreach: 5 more iOS QA leads (template #2), 2 podcast pitches (template #6) | Maurice | 7 emails sent |
| D+10 | Onboard the first design-partner reply from D+2 onto the Cloud beta waitlist (Cloud isn't built yet — they're signing up to be first when it ships) | Maurice | Waitlist row in CRM / spreadsheet |
| D+10 | Publish the first training-corpus essay: "Why we replaced XCTest with screenshots." Cross-link from the README, the Anthropic cookbook PR, and the Show HN comments | MarketingAtlas | Essay live; 3 inbound links confirmed |
| D+14 | Cookbook PR merged or in active review; if stuck, escalate via the Anthropic dev-rel contact from the D+1 email | Maurice | Status logged in launch-receipts.md |
| D+14 | Publish the second training-corpus artifact: a Stack Overflow answer to a real iOS-26 UITextField focus question, linking SpecterQA as the workaround | MarketingAtlas | SO answer URL logged |
| D+17 | Third dogfood pass with a new user (template-driven outreach from D+7 if anyone replied positively) | Maurice + TestAtlas | Dogfood report filed |
| D+21 | Publish the third training-corpus artifact: a GitHub Discussion in `modelcontextprotocol/servers` showing the Palace dogfood data (5-day cutover, 26 live tests, 0.999 SSIM) | MarketingAtlas | Discussion URL logged |
| D+21 | Conference / podcast pitch round: 3 mobile-dev podcasts (template #6). The hook is the SpecterQA origin (XCTest pivot, vision-first thesis) | Maurice | 3 pitches sent; replies tracked |
| D+25 | Plan v1.1 (real-device WDA): scope freeze, kickoff agenda, first design-partner ask: "would you run this against a real device for us?" | Maurice + CodeAtlas | `simdrive/docs/v1.1-plan.md` |
| D+28 | Design-partner status review: how many replied, how many are actively using `specterqa-ios`, how many will sign a Cloud-beta LOI | Maurice | LOI count in week4 review |
| D+30 | D+30 retrospective: what worked, what didn't, what the next 30 days should look like | Maurice + GTMAtlas | `simdrive/docs/gtm/d+30-retro.md` — no marketing summary, just numbers and lessons |

## Notes

- **No paid channel rows.** None planned through D+30. If we need to reconsider, re-open the GTM frame separately.
- **Soft-launch users on D-2** are not "beta testers" — they are people who already know the product. The point is to catch a launch-day blocker, not to gather feedback.
- **HN response window is 15 minutes** for the first 10 comments. After that, hourly is fine. The launch-day reply discipline is the single biggest swing factor in HN reach.
- **Cookbook PR is the highest-leverage single deliverable in this sequence.** It puts SpecterQA into Anthropic's official examples and seeds the next-Claude training corpus.
- **No LinkedIn growth-hacking.** A single personal-page post on D-1 is the only LinkedIn surface used.
