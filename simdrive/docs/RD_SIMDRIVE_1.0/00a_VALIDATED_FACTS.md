# SimDrive — What's Actually Validated

**Status:** Source of truth. Every product/marketing claim must trace to a row here.
**Date:** 2026-05-02
**Rule:** Validated = code path exists + tests pass + Example Reader dogfooded it. Anything else is hypothesis or aspiration.

---

## A. Code paths that exist + test coverage

Each row is a real surface in the SimDrive codebase, with file refs and the test count that exercises it.

| Capability | Code path | Tests |
|---|---|---|
| 29-tool MCP surface | `simdrive/src/specterqa_ios/server.py:_TOOLS` | 91 unit tests pass — every tool's schema + handler |
| Vision-first observe (OCR + Set-of-Mark + stable_id + stable_id_loose + confidence_band) | `simdrive/src/specterqa_ios/observe.py` + `som.py` | unit tests for stable_id, stable_id_loose bucketing, find_by_text alias whitelist, confidence dictionary gating |
| Real UITouch HID injection on iOS 26 (CoreSimulator HID port + Indigo wire format) | `simdrive/native/src/simdrive_input.m` + `hid_inject.py` | live E2E test against iOS 26.3 + iPhone 17 Pro sim verifies UITextField focus |
| Tap / swipe / type_text / press_key / clear_field | `simdrive/src/specterqa_ios/act.py` + `server.py:tool_*` | unit + live E2E tests |
| `type_text` injection_method + dispatch_succeeded fields | `server.py:tool_type_text` (HID-aware return shape) | unit test |
| Record + replay with stable_id resolution + SSIM region masking | `simdrive/src/specterqa_ios/recorder.py` | unit tests for stable_id replay fallback, SSIM masking compute, halt context |
| Performance snapshots (CPU%, RSS, threads, footprint) | `simdrive/src/specterqa_ios/perf.py` | unit tests for snapshot, baseline, compare severity bands; live smoke shows real RSS sampling |
| Crash retrieval + diagnostics (doctor, app_state, apps, crashes) | `simdrive/src/specterqa_ios/diagnostics.py` | unit tests for each tool |
| Robustness helpers (alerts, permissions, appearance, sheets, replays) | `simdrive/src/specterqa_ios/robustness.py` | unit tests; the 1-in-4 alert race re-observe loop has its own test |
| Real-device discovery + logs + app lifecycle (read-only) | `simdrive/src/specterqa_ios/device.py` | unit tests; live smoke against Maurice's iPhone 17 Pro Max |
| Stale-MCP version-drift detection | `server.py:_check_version_drift` | unit + live (caught real drift in smoke) |
| `version` MCP tool | `server.py:tool_version` | unit + live |
| Recording metadata (simdrive_version, app_version, screenshot_size_pixels, tags, created_by_session) | `recorder.py:Recorder.finalize` | unit + live (Preferences app_version returned "1353.3.2" live) |

**Test totals:** 91 unit + 26 live E2E against TestKitApp. All passing on the latest code.

---

## B. What Example Reader actually validated (per dogfood reports)

Three written dogfood reports from Example Reader (Maurice Carrier, exampleorg):

1. `~/Downloads/SIMDRIVE_DOGFOOD_2026_04_29.md` — v0.1.0a1 dogfood
2. `~/Downloads/SIMDRIVE_v0.2.0a1_DOGFOOD.md` — cutover report
3. `~/Downloads/dogfood.rtf` — v0.3.0a2 maintainer report

**Validated capabilities (Example Reader exercised them, reported outcomes, gave testimony):**

| Capability | Example Reader's verbatim or near-verbatim line | Source |
|---|---|---|
| Replaces predecessor as canonical iOS sim driver | *"simdrive 0.2.0a1 is a meaningful step forward and is now the canonical iOS sim driver for Example Reader iOS development, replacing SpecterQA."* | v0.2.0a1 dogfood |
| Vision-first navigation via stable_id | *"`tap stable_id="ccac001882f0"` opened Dobbs v. Jackson detail page. Title, cover, Borrow button all OCR'd cleanly."* | v0.2.0a1 dogfood |
| iOS-26 UITextField focus with type_text | *"The single biggest reason SpecterQA was failing — the cliclick path that broke UITextField focus — is fully fixed."* | v0.2.0a1 dogfood |
| Record + replay reliability for PR gating | *"Replays are now reliable enough to gate PRs on."* | v0.2.0a1 dogfood |
| stable_id durability across observes | *"`tap stable_id="a229e82e3f00"` is robust even when the mark's index reshuffles between observes — but not 100%: small bbox shifts can rebucket."* | v0.2.0a1 dogfood |
| Real-device session attach + observe + logs | *"Real-device sessions support observe + logs + app lifecycle today."* | v0.2.0a1 dogfood |
| 5-day cutover from predecessor | three dogfood rounds across 2026-04-29 → 2026-05-01, all feedback closed | dogfood timeline |
| Perf cached-RSS bug FIXED in 0.3.0a2 | *"#4 perf cached RSS — confirmed real fresh sampling on 0.3.0a2 (426.98 → 543.30 MB after a real catalog load, severity:high)."* | v0.3.0a2 dogfood |

**Specific use cases Example Reader named that work today:**
- Catalog → book detail navigation
- Tab bar tour (multiple tabs via stable_id)
- Search field focus + type → results render
- Record `tab-bar-tour` then replay with SSIM 0.999/step
- Real-device discovery enumerating Maurice's paired iPad + 2 iPhones

---

## C. What's borrowed from SpecterQA-browser, NOT validated for SimDrive

**Critical:** the 1.0 R&D synthesis I just wrote borrowed concepts wholesale from the predecessor `specterqa` 0.4.0 browser product (`/products/specterqa/` on synctek.io). These are **hypotheses** for SimDrive, not validated features.

| Concept borrowed from SpecterQA browser | SimDrive validation | Status |
|---|---|---|
| Personas in YAML (role, technical_comfort, goals, frustrations) | Never tested in SimDrive. No persona code exists. Example Reader never wrote one. | **HYPOTHESIS** |
| Journeys as YAML sequences of goals | Never tested. No journey runner exists. Example Reader's "journeys" are MCP tool sequences they drive directly. | **HYPOTHESIS** |
| `simdrive run --journey <name>` CLI | Doesn't exist. Example Reader never asked for it. | **DOES NOT EXIST** |
| AI driving the app "like a real user would" via persona prompts | Doesn't exist as a feature. Today the agent (Claude/Cursor/whatever) drives directly via MCP tools — there's no SimDrive-side persona injection. | **DOES NOT EXIST** |
| Per-persona observation outputs | Doesn't exist. SimDrive outputs sidecar JSON per-observe, not per-persona. | **DOES NOT EXIST** |
| `simdrive ci` orchestrator | Doesn't exist. Example Reader orchestrates via their own `scripts/simdrive-regress.sh`. | **DOES NOT EXIST** |

**These are not bad ideas — they may be great 1.x or 2.x features. But they are SpecterQA-browser concepts, not SimDrive validations.** Marketing the journey-driven flow as a 1.0 SimDrive feature would be making a claim we cannot back with code or customer testimony.

---

## D. What's hypothesis (untested in market)

These are reasonable bets but not validated:

| Claim | Status |
|---|---|
| SimDrive can sustain $49/$149/$499 premium pricing | Hypothesis. No price-sensitivity testing yet. |
| 14-day free trial converts to paid at 4-5% | Industry benchmark, not SimDrive-specific. |
| Agentic-first GTM (MCP registry, awesome-mcp, training-corpus) drives ~15-25K impressions | Estimate from comparable launches. No SimDrive history. |
| iOS engineers will pay for what Maestro offers free | Hypothesis. Depends on the iOS-deep + agent-loop differentiator surviving real evaluation. |
| Real-device WDA bootstrap takes 3-5 sessions | Estimate from `REAL_DEVICE_FEASIBILITY.md`. Not yet built. |
| Journey-driven flow is the right premium product shape | **Strong hypothesis. The synthesis assumed this; it is not validated.** |

---

## E. The validated-only 1.0 product surface

If we ship **only what's validated**, the SimDrive 1.0 product is:

- **The 29-tool MCP server.** What Example Reader dogfooded.
- **Record + replay with stable_id + SSIM masking.** What Example Reader gates PRs on.
- **Real UITouch HID on iOS 26 simulators.** The killer feature Example Reader named.
- **Real-device read-only (observe + logs + lifecycle).** Already shipping.
- **License/trial system + premium pricing.** New, but it's commerce infrastructure, not unvalidated product features.

What's **out** of validated 1.0:
- Personas / journeys YAML (SpecterQA-browser concept, untested in SimDrive)
- `simdrive run --journey` CLI (doesn't exist)
- Real-device input via WDA (not validated; gated beta or 1.1)
- Cloud / hosted replay archive (doesn't exist)
- Persona-driven AI prompting (doesn't exist)

---

## F. The rule going forward

Every marketing claim, every product-page bullet, every CHANGELOG entry, every Show HN line must trace to a row in §A or §B. If it traces to §C or §D, label it explicitly as "1.x roadmap" or "design hypothesis" — never as a 1.0 feature.

The R&D synthesis (`00_SIMDRIVE_1.0_PLAN.md`) needs revision against this rule. See §12 of that document for the revision note.

---

*This document supersedes any conflicting claim in the four R&D memos. If a memo says SimDrive ships journey-driven 1.0 and this document says it doesn't, this document wins.*
