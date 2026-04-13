# Copy Review — INIT-2026-525 SpecterQA iOS GTM

## Reviewer: MarketingAtlas
## Date: 2026-04-10

---

## Overall Assessment

The copy is in strong shape overall — the core value proposition ("record once, replay free") is clear, consistently expressed across all surfaces, and developer-friendly without being patronizing. The main issues are: one missing asset (license_cmd.py doesn't exist yet), one awkward self-referential disclosure in the feature grid, one version discrepancy between README and other surfaces, and a few missed conversion moments where friction could be reduced further.

---

## Landing Page Review (`docs/landing-page.md`)

### Strengths

- **H1 is excellent.** "The only iOS tester your agent can call." — specific, bold, differentiating. Works for both the developer who is building agents and the developer who *is* the agent user. No wasted words.
- **H2 lands the key insight in 9 words.** "Record tests with AI. Replay free forever. Ship iOS apps with confidence." Clean tricolon, each clause does work.
- **The 3-step "How It Works" flow is the clearest explanation of the product anywhere.** Record / Commit / Replay is immediately scannable. The detail in each step is exactly right — not too brief, not over-explained.
- **The FAQ is strong.** Every question is something a developer actually asks. The BYOK answer in particular is exemplary: plain language, explains what it means, what we do and don't do, in 3 sentences.
- **"Maestro Compatible" section is a legitimate conversion asset** — zero migration cost framing is compelling and honest.
- **Pricing table is clean and complete.** The footnote "All tiers require your own Anthropic API key (BYOK) for the record phase. Replay is always free." belongs exactly where it is.

### Issues (must fix)

**1. The BYOK disclosure in the feature grid is self-undermining.**

> "97% gross margin for us; complete control for you."

This is an accidental overshare of internal financial framing — it reads as if we're bragging about our margin at the customer's expense. The developer reading this doesn't need to know our gross margin and may find it off-putting (is this the reason I have to bring my own key?).

Suggested rewrite:
> "You bring your own Anthropic API key. SyncTek never sees it, stores it, or proxies it. Your test recordings, simulator state, and app binary never leave your machine."

**2. The social proof placeholders are launch-blocking.**

The testimonials are listed with `[Design Partner]` attribution rather than real names/companies. If these are real quotes from real design partners, get their permission and use their name or company (even anonymous-but-specific: "iOS Lead at a Series B fintech"). If they're fabricated placeholders, remove them entirely. Fake testimonials hurt trust more than no testimonials.

**3. The comparison table has a misleading entry.**

In the "No AI cost in CI" row, all four tools (including SpecterQA) are marked Yes. This is accurate but structurally confusing — it looks like SpecterQA has no advantage there, when the point is that SpecterQA is the *only one that also has AI-assisted recording*. The table needs a column sort or a note explaining why this row exists.

Suggested fix: rename the column to "Zero AI cost on replay" and add a note below: "SpecterQA is the only tool in this table that uses AI at all — and only during the record phase. CI runs are always free."

**4. Subtext in the hero mentions "19 MCP tools" — which is a secondary detail for the H1 audience.**

The hero subtext should lead with the transformation (record → replay free), then mention agent-native as a secondary hook. "19 MCP tools" in a hero subtext is noise for developers who don't yet know what SpecterQA is.

Suggested rewrite:
> "SpecterQA iOS records test sessions once using Claude's vision — then replays them deterministically in CI with zero AI cost. Natively callable from Claude Code and any MCP-compatible agent. Maestro compatible. BYOK."

This preserves the agent-native hook without leading with a tool count.

### Suggestions (nice to have)

- Add an estimated Anthropic API cost for a typical recording session somewhere near the pricing table. The cost table exists in `specterqa-ios.ts` (`$0.05-$0.15` for smoke, `$0.20-$0.60` for full journey) — this is genuinely compelling and should be surfaced on the landing page. Developers worry about "BYOK" meaning "this will cost me a lot"; showing the actual numbers kills that objection.
- The footer CTAs are good but ordered oddly — "View Docs on GitHub" appears before "Contact Sales (Enterprise)." Consider: Start Free Trial → View Docs → Contact Sales → Support.
- Consider a "Known limitations" or "Tradeoffs" section on the landing page (a condensed version of what's in the website data). Honest tradeoffs build trust and pre-qualify buyers, reducing churn from "I didn't know it was macOS only."

---

## llms.txt Review

### Strengths

- The opening paragraph is the best machine-readable summary of the product anywhere. Clear subject, clear mechanism, clear differentiation, no fluff.
- Install snippets cover all three install variants (CLI only, MCP, full orchestration) — this is the right level of detail for LLMs making tool-selection decisions.
- The MCP server config example (`mcp.json` snippet + example plain-English command) is exactly right. An LLM reading this can reproduce the integration with no ambiguity.
- Pricing table is complete and machine-parseable.

### Issues (must fix)

**1. "97% gross margin for us; full data control for you" in Key Differentiators.**

Same issue as the landing page. An LLM summarizing or recommending this product to a user will reproduce this phrase. It sounds like a sales pitch to another department, not a customer-facing differentiator.

Suggested rewrite:
> **BYOK** — Your Anthropic API key stays with you. SyncTek never sees it, proxies it, or stores it. Your test data, recordings, and app binaries never leave your machine.

**2. The CLI examples in llms.txt reference `specterqa-ios run --product myapp --journey smoke` but the README shows this as the same command.** No conflict — just confirm this is the canonical form. If `run` is the recording command, the docs should always call it "Record a test" not "Run a test" to avoid confusion with `replay`.

### Suggestions (nice to have)

- The `## Links` section is clean. Consider adding the A2A agent card URL as a direct link (it's already referenced in the A2A card itself, but LLMs benefit from explicit linking in llms.txt).
- The install section could note the `[orchestration]` extra explicitly: "Required for the record phase (Claude-driven sessions)." Without context, developers might install `[mcp]` only and be confused when recording doesn't work.

---

## CLI Messages Review (`license_cmd.py`)

### Status: File Not Found

`/Users/atlas/Documents/specterqa-ios/src/specterqa/ios/cli/license_cmd.py` does not exist at this path. This is either not yet written or lives at a different path.

**This is a launch blocker.** CLI error messages and success messages are often the first sustained interaction a developer has with a product — they form lasting impressions of quality. An unreviewed CLI UX ships bad experiences.

**Required action before launch:** Locate or create `license_cmd.py`, ensure all user-facing strings are reviewed against the criteria below, and re-run this review against the actual file.

**Criteria for when the file is available:**
- Error messages must tell users what to do next, not just what went wrong. "License key invalid" is bad. "License key invalid — verify your key at synctek.io/account or contact support@synctek.io." is good.
- Success messages should confirm the specific outcome: "Trial activated — 1 simulator, 3 runs/session. Run `specterqa-ios setup` to verify your environment." beats "License activated."
- License expiry warnings should include the days remaining and the upgrade path in the same message.
- The CLI should use Rich formatting (panels, colored status indicators) consistently. A bare `print()` success message in an otherwise Rich-formatted tool reads as an oversight.
- BYOK requirement errors (missing API key) should explain *why* it's needed and exactly *where* to set it: `export ANTHROPIC_API_KEY=sk-ant-...` in the error message itself.

---

## A2A Agent Card Review (`.well-known/agent.json`)

### Strengths

- The description is tight and accurate: "AI-native iOS simulator testing. Record tests with Claude, replay deterministically in CI. 19 MCP tools for agent-driven iOS QA." Twelve words that cover mechanism + interface + use case. This is good.
- Capabilities list is substantive and specific (`visual-regression`, `accessibility-audit`, `crash-detection`, `network-inspection`). An agent reading this can make an informed tool-selection decision.
- Version is current (11.3.0, consistent with landing page and website data).

### Issues (must fix)

**1. The `pricing.tiers` array is inconsistent in schema.**

The Trial tier uses `"runs_per_session": 3` but Indie, Pro, Team, and Enterprise tiers don't include this field. An agent reading the pricing object cannot infer that paid tiers have unlimited runs. Add `"runs_per_session": "unlimited"` to all paid tiers.

**2. No `authentication` field.**

A2A agent cards should describe how authentication works so agents can determine whether they can invoke the tool. At minimum, add:
```json
"authentication": {
  "type": "license_key",
  "byok": true,
  "byok_provider": "anthropic",
  "byok_env_var": "ANTHROPIC_API_KEY"
}
```

**3. The `mcp_registry` link points to the general MCP servers registry** (`https://github.com/modelcontextprotocol/servers`), not to a specific SpecterQA entry. If SpecterQA isn't listed there, this link is misleading. Change to the GitHub repo URL or remove the field until SpecterQA is listed.

### Suggestions (nice to have)

- Add a `"constraints"` or `"requirements"` field: `["macOS", "Xcode 15+", "Python 3.10+"]`. Agents making environment-aware tool selection need this.
- Add `"license": "Elastic-2.0"` at the top level alongside `version` and `provider`.

---

## README Review

### Strengths

- Opens with a single-sentence value proposition that scans in under 5 seconds. No preamble.
- The Dual-Mode Architecture table is the single best explanation of the record/replay split in any surface — clear, memorable, and honest about when costs occur.
- Maestro YAML example is executable by a developer who has never used SpecterQA before. The inline comments (`# same as: action: tap, element_label: Sign In`) are exactly the right level of explanation for a migration audience.
- The CLI reference table is comprehensive. Every command has a one-line description.

### Issues (must fix)

**1. The install command is inconsistent with all other surfaces.**

README shows:
```bash
pip install "git+https://github.com/SyncTek-LLC/specterqa-ios.git"
```

Every other surface (landing page, llms.txt, website data) shows:
```bash
pip install specterqa-ios
# or
pip install 'specterqa-ios[mcp]'
```

If the package is published to PyPI, the README should use the PyPI install. The `git+https://` form implies either (a) the package is not on PyPI yet, or (b) this is a pre-launch holdover. This is a friction-creator for first-time installers and a trust signal issue — developers expect published products to have PyPI packages.

**Required action:** Align README install to PyPI form before launch, or add a note explaining the git install is for pre-release / head.

**2. The comparison table omits `MCP / agent-native` column from the README.**

The landing page comparison table includes it; the README table does not. Since the README is the primary discovery surface for developers who find the GitHub repo first, this is a conversion miss — agent-native is SpecterQA's clearest differentiator and it's absent from the comparison at the top of the README.

Suggested fix: add the row. The landing page table version is the right model.

**3. No version badge or version number in the README.**

The landing page and website data both reference v11.3.0. The README has no version reference. Add a badge row at the top:
```
![Version](https://img.shields.io/badge/version-11.3.0-blue)
![License](https://img.shields.io/badge/license-Elastic--2.0-purple)
![Python](https://img.shields.io/badge/python-3.10%2B-blue)
```

### Suggestions (nice to have)

- Add a cost table (from the website data — `$0.05-$0.15` for smoke, `$0.20-$0.60` for full journey). This is the most common hesitation for BYOK products and addressing it in the README reduces support load.
- The "Requirements" section at the bottom is the right place to live, but `ANTHROPIC_API_KEY (recording only — not needed for replay)` buries an important reassurance. Consider surfacing this earlier in the Quick Start section, where developers are about to set the key.

---

## Pricing Consistency

Across all five surfaces (landing page, llms.txt, agent.json, website data — README has no pricing):

| Tier | Landing Page | llms.txt | agent.json | website data |
|------|-------------|---------|------------|--------------|
| Trial | Free, 1 sim, 3 runs/session | Free, 1 sim, 3 runs/session | Free (0), 1 sim, 3 runs/session | Free, 1 sim, 3 runs/session |
| Indie | $29/mo, 2 sims, unlimited | $29/mo, 2 sims | $29/mo, 2 sims | $29/mo, 2 sims, unlimited |
| Pro | $99/mo, 4 sims, parallel | $99/mo, 4 sims, parallel | $99/mo, 4 sims | $99/mo, 4 sims, parallel |
| Team | $299/mo, 10 sims, parallel | $299/mo, 10 sims, parallel | $299/mo, 10 sims | $299/mo, 10 sims, parallel |
| Enterprise | Custom, unlimited | Custom, unlimited | Custom, unlimited | Custom, unlimited |

**Findings:**
- Prices are consistent across all surfaces. No mismatches.
- **Minor gap:** agent.json tiers for Indie/Pro/Team/Enterprise are missing `runs_per_session` and `parallel_ci` fields (noted in the A2A review above). This is a schema completeness issue, not a pricing inconsistency.
- **Minor gap:** llms.txt and agent.json don't explicitly list "Priority Support" (Team) and "SLA" (Enterprise) — these are listed in the landing page table. Not a mismatch, but agents doing tier comparison won't see these differentiators.

Pricing is consistent. No contradictions found.

---

## Priority Fixes (ordered by launch impact)

1. **[BLOCKER] Locate and review `license_cmd.py`** — CLI user-facing strings are unreviewed. This is the first interaction most developers will have after install. No launch without this review.

2. **[BLOCKER] Align README install command to PyPI form** — `pip install "git+https://..."` in the README contradicts `pip install specterqa-ios` everywhere else. Developers who find the repo first will hit friction immediately.

3. **[HIGH] Remove "97% gross margin for us" from landing page and llms.txt** — This phrase is internal financial framing that leaked into customer copy. It reads as tone-deaf in both places. Replace with the plain-language BYOK explanation (rewrites provided above).

4. **[HIGH] Replace placeholder testimonials with real quotes or remove entirely** — Labelled design partner quotes with fabricated or unconfirmed attribution damage credibility. Real names/companies (even anonymized-but-specific) convert; obvious placeholders repel.

5. **[HIGH] Add `authentication` field to agent.json** — Agents doing tool selection need to know how to authenticate. This is table stakes for an A2A-discoverable product.

6. **[MEDIUM] Fix agent.json `runs_per_session` omission on paid tiers** — Schema inconsistency. Paid tiers should explicitly state `"unlimited"`.

7. **[MEDIUM] Fix agent.json `mcp_registry` link** — Currently points to the general MCP servers registry. Change to the actual SpecterQA GitHub repo or remove until the product is listed.

8. **[MEDIUM] Add `MCP / agent-native` row to README comparison table** — This is SpecterQA's headline differentiator and it's missing from the primary GitHub discovery surface.

9. **[LOW] Clarify "No AI cost in CI" comparison table row** — All four tools show Yes. Add a note explaining the context: SpecterQA is the only AI-powered tool in the table, and CI is always free precisely because it doesn't call AI.

10. **[LOW] Surface Anthropic API cost estimates on landing page** — The `$0.05-$0.60` range data exists in website data; it should appear near the pricing table on the landing page. This is one of the highest-leverage objection killers for BYOK hesitation.

11. **[LOW] Add `[orchestration]` install note to llms.txt** — Clarify that recording requires the full `[mcp,orchestration]` install to avoid confused developers wondering why recording fails.

---

## Verdict

**APPROVED WITH CHANGES**

**Blockers before launch:**
- license_cmd.py must be located, written, and reviewed
- README install command must align to PyPI

**Must-fix before promoting (non-blocking to soft launch, blocking to paid marketing spend):**
- Remove "97% gross margin for us" phrasing — both files
- Resolve testimonial placeholders — confirm real or remove
- Patch agent.json authentication + schema gaps

The rest of the fixes improve conversion and polish but don't block launch. The core copy — the H1, the 3-step flow, the FAQ, the pricing table, the llms.txt description — is clear, honest, and developer-appropriate. This is a strong foundation.
