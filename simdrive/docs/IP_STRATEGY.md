# SpecterQA — IP Protection Strategy

**Status:** Draft v1
**Date:** 2026-04-29
**Author:** Atlas (CEO Interface), SyncTek LLC
**Audience:** Maurice Carrier (Chairman), legal counsel (when retained)

> **This is a strategy document, not legal advice.** It frames SyncTek's options and ranks them by ROI. Any USPTO filing, copyright registration, or license-text rewrite should be reviewed by licensed IP counsel before submission. Cost estimates are public-information rule-of-thumb numbers, not quotes. Lines beginning *"NEEDS COUNSEL"* explicitly defer to an attorney.

---

## §1. Executive summary

SpecterQA is SyncTek's MCP-native iOS simulator driver, shipping as PyPI `specterqa-ios 1.0.0a1` (29 MCP tools, ~4,118 LOC Python + ~600 LOC ObjC native HID helper). The asset to protect is the bundle: a vision-first agent-driven tool surface, a native HID-injection technique that bypasses XCTest on iOS 26, and a fast-moving dogfood relationship with one paying-attention customer (Example Reader iOS). The window before Anthropic, Maestro, or a YC clone closes the gap is roughly 9 months optimistic, 15 pessimistic.

**The three most defensible IP elements:**

1. The *brand* — "SpecterQA" wordmark + pixel-pin logo.
2. The *dogfood velocity loop* — Example Reader receipts, three feedback rounds closed in five days, moving-target release cadence (5 versions in 1 week).
3. The *Cloud / Pro tier* once it ships — closed-source, hosted, with stored journey corpus that creates real switching cost.

**Open-core split:** SpecterQA engine stays open under MIT; SpecterQA Cloud + WDA real-device + compliance ships under a separate proprietary license in a separate package.

**Three highest-leverage IP measures (ranked by ROI):**

1. **Resolve the LICENSE / pyproject mismatch** (~$0). Repo root `LICENSE` says Elastic 2.0; the package says MIT. Self-inflicted wound. Blocks anthropic-cookbook eligibility.
2. **File USPTO trademark for "SpecterQA" wordmark, Class 9** (~$250 self-filed, $750-2,000 with attorney). The brand is the most enforceable asset.
3. **Add `TRADEMARK.md` + `NOTICE` + correct `LICENSE`** (~$0, 1 hour). Establishes first-use evidence and signals professionalism.

Patents are out of scope. Cost ($15-30K per patent through grant) doesn't pencil at <$5K MRR, and most candidate claims fail post-Alice.

---

## §2. Asset inventory

Classification: **Commodity** (anyone rebuilds in <1 week) / **Defensible** (multi-week reverse-engineering) / **Crown jewel** (insider knowledge or sustained-effort moat).

| # | Component | Class | Where it lives | Replication cost |
|---|---|---|---|---|
| 1 | Native HID via `SimDeviceLegacyHIDClient` + `IndigoMessage` | **Crown jewel** | `simdrive/native/src/simdrive_input.m`, `Indigo.h` | 2-3 weeks for a competent ObjC eng. Note: idb's MIT-licensed `FBSimulatorIndigoHID` documents the technique publicly; our value-add is the minimal, FBControlCore-free port + iOS 26 tuning. |
| 2 | Indigo touch wire format (336-byte struct, second-payload duplicate) | **Defensible** | `Indigo.h:160-198`, `simdrive_input.m:113-140` | Documented in idb. Ours adds Xcode 26.2 disassembly notes. ~1-2 weeks to re-derive. |
| 3 | iOS 26 TextField focus via real UITouch (killer feature) | **Crown jewel** | Combination of #1 + dispatch-queue serialization (`simdrive_input.m:142-177`) + 60 ms down/up cadence (`:233-235`) | 2-3 weeks to discover synthetic CGEvents fail on iOS 26, plus a week to find SimDeviceLegacyHIDClient is the fix. The *insight* is what's defensible; partially eroded by our own public docs. |
| 4 | `type_text` 25 ms Shift-settle timing | **Defensible** | `simdrive_input.m:295-321` (`kModSettle`, `kKeyHold`, `kKeyGap`) | Non-obvious. A cloner ships broken upper-case before finding it. ~1 week trial-and-error. |
| 5 | Vision-first MCP tool surface (29 tools, schema choices) | **Defensible** | `src/specterqa_ios/server.py` `_TOOLS` (1,369 LOC) | Schema response shapes (`_simdrive_warning` drift envelope, `step_id`, structured `code` errors) are 2-3 weeks of LLM-loop polish. Copyright covers verbatim copies. |
| 6 | SoM annotation + `stable_id` (20 px tight / 60 px loose buckets) | **Defensible** | `src/specterqa_ios/som.py:241-258` | Specific bucket sizes are non-obvious. ~1 week to re-derive empirically. |
| 7 | SSIM region masking for replay drift | **Commodity** | `src/specterqa_ios/recorder.py:125-205` | SSIM is 2004 prior art. Status-bar masking is obvious within an hour. |
| 8 | Recording/replay format (`recording.yaml` + sidecar JSONs + `actions.jsonl`) | **Commodity** | `recorder.py` (321 LOC) | YAML schema clonable in an afternoon. Defensibility comes from combo with `stable_id` + customer lock-in. |
| 9 | The 29-tool surface as a whole | **Defensible** | `server.py` `_TOOLS` (lines 790-1300) | The lifecycle/observe/act/record/perf/diagnostics/robustness combo is what makes agent loops happy. 3 weeks of taste. |
| 10 | `bootstrap-device` flow (v1.1, WDA path) | **Defensible** (when shipped) | Not built; PRODUCTIZATION_PLAN §4 | Provisioning UX is the work. Detox/Maestro examples exist but each had 6-12 months of polish. |
| 11 | Example Reader dogfood receipts + "canonical iOS sim driver" testimonial | **Crown jewel** | `SIMDRIVE_v0.2.0a1_DOGFOOD.md` (Example Reader repo), CHANGELOG | A clone *cannot* have these. Permanent reputational asset. Most under-rated IP we own. |

**Crown jewels by enforceability:** (1) Brand + dogfood receipts — trademark + reputation, both enforceable; (2) The bundle-as-design (#3 + #5 + #6) — copyright + speed; (3) Native HID technique — derived from MIT-licensed idb, so exclusivity is weak; what we own is the minimal port + iOS 26 tuning.

---

## §3. License strategy — open-core split

### Current state — the contradiction

The repository has a **license-text mismatch** that needs immediate resolution:

| Surface | Declares |
|---|---|
| Repo root `LICENSE` | **Elastic License 2.0** |
| Repo root `pyproject.toml` (legacy 16.x) | `Elastic-2.0` |
| `simdrive/pyproject.toml` (the new 17.x package shipped 2026-05-01) | **`MIT`** |
| `simdrive/native/src/Indigo.h` | MIT (Meta/idb upstream) |

PRODUCTIZATION_PLAN §8 intent: "simdrive (MIT) stays free forever." But the repo root still carries Elastic 2.0 from the legacy 16.x XCTest line. Until fixed, anyone reading the repo (rather than just the wheel) sees a contradictory and more-restrictive license. **Fix before any push to the MCP registry, awesome-mcp, or anthropic-cookbook.** Elastic 2.0 is not OSI-approved and would disqualify us.

### The split

| Layer | License | Rationale |
|---|---|---|
| **SpecterQA engine** (29 tools, native HID, observe/act/record/replay) | **MIT** | OSI-approved, cookbook-eligible, training-corpus-friendly, dev-tool community trust. Already declared in `simdrive/pyproject.toml`. |
| **SpecterQA Cloud / Pro / Team** (hosted runners, journey corpus, dashboards) | **Proprietary, closed-source**, paid commercial license | The revenue moat (PRODUCTIZATION_PLAN §8). SaaS hosted; source not distributed. |
| **WDA real-device + compliance tier** (SOC 2, RBAC, SSO) | **Proprietary**, in `specterqa-cloud` package only | Held back from OSS as paid wedge per PRODUCTIZATION_PLAN §8. |

### Why MIT for the engine

Permissive licenses are the OSS dev-tool default. Anthropic-cookbook accepts MIT/Apache-2 only. Models trained on GitHub absorb MIT code by default — we *want* the next Claude to know SpecterQA's tool surface. Aligning with the MCP ecosystem (mostly MIT/Apache) is positional capital.

### License compatibility — proprietary tier wrapping MIT engine

**Yes, MIT permits this.** The proprietary `specterqa-cloud` package can import, wrap, extend, and re-export `specterqa_ios`'s public API and ship a closed-source binary that includes it, *provided* the MIT copyright notice + license text travel with the binary in a `LICENSES/` or `NOTICE` file. **Action:** when Cloud ships, include `LICENSES/MIT-specterqa-ios.txt` and a `NOTICE` naming SyncTek as engine copyright holder. NEEDS COUNSEL — exact NOTICE wording for first commercial release.

### Risk: someone forks the MIT engine, reskins it, sells competing Cloud

Mitigations:

1. **Trademark.** A fork can copy code but cannot call itself "SpecterQA" without infringement. Single strongest defense.
2. **Cloud value-add.** Hosted runners, dashboards, multi-tenant journey corpus — things MIT doesn't grant.
3. **Speed.** A fork is always behind HEAD (5 versions in 1 week per CHANGELOG).
4. **Brand + dogfood receipts.** Example Reader's testimonial belongs to *us*, not to a fork.

### Apache-2 vs MIT?

Apache-2 adds an explicit patent grant + defensive-termination clause vs MIT's brevity. The patent-grant difference matters only if (a) we patent something or (b) someone patents derivative work and sues us. We're not patenting (§5), and the patent-troll attack surface is small at our scale. **Verdict: stay MIT.** Revisit at 1.0 with counsel. NEEDS COUNSEL — final call between MIT and Apache-2 at 1.0 launch.

### BSL?

Tempting (blocks competitive offerings for N years), but **not OSI-approved** — disqualifies us from anthropic-cookbook + awesome-mcp + "open source" cred. Carries a "founder is anxious" community signal. **No for the engine.** Plausible later for a Cloud client SDK if we ship one.

### Recommendation block — declarations to update

| File | Current | Update to | When |
|---|---|---|---|
| `/LICENSE` (repo root) | Elastic 2.0 | MIT (full text) | 2026-05-05 |
| `/pyproject.toml` (legacy 16.x) | `Elastic-2.0` | MIT, or delete with 16.x retirement | 2026-05-05 |
| `/simdrive/pyproject.toml` | `MIT` | No change | — |
| `/simdrive/native/src/simdrive_input.m` | No header | Add MIT header + "Derived from idb (MIT)" attribution | Before 1.0 |
| `/simdrive/native/src/Indigo.h` | MIT (Meta) | Keep Meta header; add SyncTek modifications notice below | Before 1.0 |
| New: `/NOTICE` | — | Create. SyncTek copyright + MIT recital + Meta/idb attribution | Before 1.0 |
| New: `/TRADEMARK.md` | — | Declare "SpecterQA" + pixel-pin as marks of SyncTek; usage guidelines | Before USPTO filing |

---

## §4. Trademark strategy

### Marks to register

| # | Mark | Type | USPTO Class | Priority |
|---|---|---|---|---|
| 1 | **"SpecterQA"** | Wordmark | **Class 9** (software) | **HIGH — file first** |
| 2 | Pixel-pin logo (`docs/brand/logo-mark-only.svg`) | Figurative | Class 9 | MEDIUM — file second |
| 3 | "Hand your iOS simulator to your agent." | Slogan | Class 9 | LOW — defer |

### Cost & timeline

USPTO TEAS Plus: **~$250 per class per mark** self-filed; **~$500-1,500 attorney fees** on top. Time to registration: **6-12 months**.

| Action | Self | Attorney | Time |
|---|---|---|---|
| USPTO clearance search ("SpecterQA" Class 9 + adjacent) | $0 (TESS, error-prone) | $300-500 basic / $1,500+ deep | 1-2 weeks |
| TEAS Plus wordmark | $250 | $750-2,000 incl. fees | 6-12 months |
| TEAS Plus figurative mark | $250 | $750-2,000 | 6-12 months |
| **Total, attorney-assisted, two marks** | $500 | **$1,500-4,500** | 6-12 months |

### Filing strategy

1. **Wordmark first.** "SpecterQA" is highest-leverage. File Section 1(a) (use in commerce) given the package is on PyPI.
2. **First-use date.** Anchor to the `specterqa-ios 1.0.0a1` release of **2026-05-01** (PRODUCTIZATION_PLAN §7 brand cutover). The legacy 16.x `specterqa-ios` line is also `specterqa-ios` and dates further back. NEEDS COUNSEL: confirm anchor date.
3. **Specimen of use.** PyPI listing, GitHub README, Example Reader dogfood report referencing "SpecterQA" all qualify. Capture dated PDFs/PNGs now.
4. **Logo second**, within ~3 months of wordmark. **Slogan deferred** (hardest to register, lowest impact).

### Defensive actions before filing

- **Document first-use-in-commerce.** Save dated PDFs of: PyPI page, README hero, repo description, Example Reader dogfood report.
- **Use the ™ symbol now.** Pre-registration use of "SpecterQA™" supports common-law trademark rights. Free.
- **Evidence trail.** Each commit + each PyPI release timestamps a use event. Just don't delete the evidence.

### Risks: existing marks

- **`specterqa` 0.4.0 on PyPI** — same SyncTek owner, no conflict.
- **"SimDrive" racing-rig hardware** — Class 28 / hardware, no conflict with our Class 9 software.
- **"Specter" generic word** — common; not blocking. Clearance search will surface close matches.

NEEDS COUNSEL: a real clearance search (TESS + Google + WIPO + common-law) before filing. **The $300-500 spend here is the highest-ROI single line item in this document.**

### What trademark does NOT protect

Code (anyone copies MIT-licensed code), techniques (HID injection is in idb), tool-surface composition (anyone builds a 29-tool MCP server). Trademark just stops them from calling it SpecterQA. That's enough — brand is the most enforceable lever we have.

---

## §5. Patent analysis

**Honest verdict: do not file patents.**

### Post-Alice reality

The 2014 *Alice Corp. v. CLS Bank* decision held that abstract ideas implemented on a generic computer are not patent-eligible. Modern software patents must show "specific improvement to the functioning of the computer itself" — narrow and hard. ~60% of software-patent applications die at examination or in IPR/litigation.

### Candidate analysis

| Candidate | Verdict |
|---|---|
| Indigo wire-format reverse-engineering | **Unpatentable.** Prior art (idb, MIT, public since 2018+). Apple invented the wire format; we ported. |
| Vision-first MCP tool surface | **Unpatentable.** Abstract idea + computer = Alice fail. Specific schemas may have *copyright*, not patent. |
| 25 ms Shift-settle timing | **Probably unpatentable.** Too narrow (designed-around in an afternoon) and arguably obvious. |
| Dual-bucket `stable_id` (20/60 px) | **Probably unpatentable.** Hashing label + position bucket is known in CV/UI testing. |
| Recording/replay with stable_id + SSIM masking | **Probably unpatentable.** Combination of known techniques. |
| Open-core architecture | **Definitely not patentable** — business method. |

### Defensive patent?

NPEs target wealthy companies, not seed-stage startups. Defensive case doesn't pencil at our scale.

### Cost-vs-value

Each patent through grant: $15-30K. Two patents = a year of engineering runway at our current scale. 2-3 years to grant — by then the moat is obsolete or designed-around.

**Re-evaluate at $50K MRR or Series A.** Counsel will spot any genuinely novel claims at that point, and capital will be available to file.

---

## §6. Anti-clone defensive measures

A determined competitor can clone the engine in 3-6 weeks. Question: what slows them down or makes the clone less valuable?

### Effective measures

| Measure | Effort | Effectiveness |
|---|---|---|
| **Trademark enforcement** | Low (file once, $1.5-4.5K total) | **High.** They can copy code; can't call it SpecterQA. |
| **Speed — keep shipping** | Medium (already happening) | **High.** Clone is always behind HEAD. |
| **Brand + dogfood receipts** | Low (already accumulating) | **High.** Example Reader migration is permanent reputational asset; clones can't have it. |
| **Cloud lock-in** | High (8-12 weeks build per PRODUCTIZATION_PLAN §8) | **High** when shipped. Journey corpus + replays in our Cloud = real switching cost. |
| **Network effects on journey corpus** | High (depends on Cloud + multi-tenant + sharing UX) | Medium-High. Value scales with users. |
| **Anthropic relationship / MCP registry blessing** | Medium (ongoing) | **High.** Cookbook PR + registry listing + training-corpus seeding compound over 6-12 months. |
| **Documentation as moat** | Low-Medium | Medium. Comprehensive CHANGELOG + dogfood reports + BEST_PRACTICES.md raise the cost of "copy the code, figure out tribal knowledge later." |

### Hard NOs

- **Code obfuscation.** Engineers won't trust an obfuscated dev tool. Doesn't slow determined cloners. Breaks our own debugging.
- **Telemetry without consent.** Breaks dev-tool norms. CCPA/GDPR risk. Anthropic-cookbook reviewers reject. Turns users against us.
- **DRM / key servers for the OSS engine.** Defeats the open-core point. Cloud features can have license-key checks; the engine cannot.
- **Patent trolling.** Community-toxic, expensive, unlikely to succeed. Burns the Anthropic relationship.
- **C&Ds against MIT-compliant forks.** MIT permits forking. Reputational catastrophe in OSS. Trademark enforcement only — never copyright — and only when the fork uses our marks.

### The honest assessment

The strongest defense is not "stop the clone" — it's "outrun the clone." A clone of `1.0.0a1` released in 6 weeks competes against `18.x` with new features, more dogfood, a Cloud product, and 3 design partners. That's the real moat.

---

## §7. Practical filing checklist (30 days)

Total spend, attorney-assisted: ~$2,500-5,500. Self-filed: ~$500-1,000.

| # | Action | ROI | Cost | Owner | Deadline |
|---|---|---|---|---|---|
| 1 | **Resolve LICENSE / pyproject mismatch.** Replace repo-root `LICENSE` with MIT text. Update or retire legacy 16.x `pyproject.toml`. | **#1** | $0 | CodeAtlas | 2026-05-05 |
| 2 | **Add `TRADEMARK.md`** — declares "SpecterQA" + pixel-pin as marks of SyncTek; usage guidelines. | **#2** | $0 | Atlas drafts, Maurice reviews | 2026-05-05 |
| 3 | **Add `NOTICE`** — SyncTek copyright + MIT recital + Meta/idb attribution for `Indigo.h`. | #3 | $0 | CodeAtlas | 2026-05-05 |
| 4 | **USPTO clearance search for "SpecterQA" Class 9** (flat-fee TM attorney). | **#4** | $300-500 | Maurice + counsel | 2026-05-15 |
| 5 | **File USPTO TEAS Plus wordmark, Class 9.** Section 1(a) use-in-commerce, anchor 2026-05-01. | **#5** | $250 + $500-1,500 attorney | Maurice + counsel | 2026-05-29 |
| 6 | **Capture first-use evidence** — dated PDFs/PNGs of PyPI page, README hero, repo description, Example Reader dogfood report. | #6 | $0 | Atlas | 2026-05-05 |
| 7 | **Use the ™ symbol** in README, PyPI description, marketing copy. | #7 | $0 | CodeAtlas + MarketingAtlas | 2026-05-05 |
| 8 | **File USPTO TEAS Plus pixel-pin logo, Class 9.** | #8 | $250 + $500-1,500 | Maurice + counsel | 2026-06-30 |
| 9 | **Add usage guidelines to `docs/brand/README.md`** — acceptable third-party use, fair use, infringement. | #9 | $0 | MarketingAtlas | 2026-05-15 |
| 10 | **Document first-use date** in `TRADEMARK.md` for evidentiary purposes. | #10 | $0 | Atlas | 2026-05-05 |

---

## §8. Risk register

| # | Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|---|
| 1 | LICENSE / pyproject mismatch confuses contributors or blocks anthropic-cookbook PR / awesome-mcp listing | **High** (current) | Med-High | Action #1, before next public push. |
| 2 | Anthropic / Apple / YC competitor ships an iOS-driving MCP product before our brand is registered | Medium-Low (~50% combined within 12 mo) | High | File USPTO TEAS Plus ASAP (Action #5). Establish first-use date. |
| 3 | Determined cloner forks MIT engine, calls it "SpecterQA Pro", undercuts Cloud pricing | Low-Medium | Medium | Trademark enforcement (brand-name claim, not code) + speed + Cloud value-add. |
| 4 | Apple breaks `SimDeviceLegacyHIDClient` / `IndigoMessage` SPI in Xcode 27+ | Medium | High | Pin tested Xcode versions, monitor betas, document fallback. Adjacent to IP — losing the technique erodes the portfolio's value. |
| 5 | Trademark squatter files "SpecterQA" in adjacent classes / jurisdictions | Low | Medium | File US Class 9 first; consider WIPO Madrid (~$1,200) when international footprint warrants. |
| 6 | We get sued over a "SpecterQA"-adjacent mark we didn't clear | Low | High | The clearance search (Action #4) is the entire mitigation. **Do not skip it.** |
| 7 | Anthropic releases a product whose name conflicts with "SpecterQA" | Very Low | Very High | Monitor announcements; we'd have priority of use. NEEDS COUNSEL if it ever happens. |
| 8 | Contributor PRs code, later claims unassigned IP / patent infringement | Low | Medium | Add `CONTRIBUTING.md` with DCO sign-off requirement. Standard, sufficient at our scale. |

---

## §9. Bottom line

The IP strategy that pencils at SpecterQA's scale is **brand + speed + Cloud lock-in + dogfood receipts** — not patents and not source-code obfuscation.

**Spend $500-2,500 on a USPTO trademark filing within 30 days.** Spend $0 on the LICENSE cleanup, `TRADEMARK.md`, `NOTICE`, and ™ adoption — those are an afternoon of CodeAtlas + Atlas work. Defer logo trademark by 60 days. Defer slogan trademark indefinitely. Do not file patents.

The single highest-leverage action remains the LICENSE / pyproject cleanup — free, unblocks GTM channels, and removes a self-inflicted ambiguity that would be embarrassing to stumble over publicly. Do that first. Everything else cascades from it.

---

*End of strategy. NEEDS COUNSEL: clearance search (§4); MIT-vs-Apache-2 final call at 1.0 (§3); NOTICE wording for first commercial Cloud release (§3); first-use anchor date confirmation (§4).*
