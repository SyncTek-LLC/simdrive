# Case study: Example Reader iOS migrates to SpecterQA in 5 days

**Customer:** exampleorg — Example Reader iOS (`com.example.reader`)
**Stack:** Swift, SwiftUI, Readium 3.x reading engine (`WKWebView`), OAuth/SAML auth via Safari sheets
**Driver before:** the predecessor product (XCTest-based, archived)
**Driver after:** SpecterQA (`specterqa-ios 0.2.0a1` → `0.3.0a3`)
**Cutover:** 5 days, 3 dogfood rounds, all feedback closed

---

## TL;DR

Example Reader migrated off the predecessor and onto SpecterQA as their canonical iOS simulator driver in 5 days. The two flows that were structurally untestable under XCTest — Readium reading inside `WKWebView` and out-of-process OAuth/SAML auth — are now automatable. Replays are SSIM-gated and reliable enough to run as PR gates. Three dogfood feedback rounds, all closed.

## The problem

Example Reader iOS sits on three surfaces that XCTest's accessibility tree can't reach:

- **Readium 3.x WKWebView.** The reading engine is a WebKit web view. XCUITest sees a single opaque container; the page-forward, table-of-contents, and bookmark gestures inside it are invisible to the framework. Manual QA was the only coverage option.
- **OAuth and SAML library auth.** Library systems authenticate via out-of-process Safari sheets. `SFSafariViewController` runs in a different process; XCTest can't drive it. The login flows that gate every other feature were untested.
- **iOS 26 SwiftUI search and login regressions.** With iOS 26, the `UITextField` first-responder path under XCUITest broke. Tap a field, the keyboard appears, the next `typeText` loses its first three characters. Auth forms, search, library-add — everything with text input started flaking simultaneously.

Three regressions, one root cause: the test runner couldn't see or touch what the user actually used.

## The pivot

The cutover process was unusually mechanical because the model is unusually simple. SpecterQA's loop — `observe` → annotated screenshot with numbered marks → `tap text=` or `tap stable_id=` → `observe` to confirm — doesn't need selectors or test fixtures, so most of the work was deciding which existing flows to recreate as journeys, not writing new test infrastructure.

Day-by-day:

- **Day 1.** `pip install` + first `observe` against the Example Reader catalog. OCR'd cleanly: titles, covers, tab-bar labels. The annotated PNG was usable from the agent loop with no tuning.
- **Day 2.** The first replay: a 4-step tab-bar tour (Catalog → My Books → Holds → Settings → Catalog) recorded once, replayed cleanly. SSIM 0.999 on every step, zero drift.
- **Day 3.** The killer test. Search field, type "harlem", expect 5 results to render. Under the predecessor, the keyboard would lose focus on iOS 26 and characters never landed. Under SpecterQA: `type_text(tap_first={stable_id: "850877875550"}, text: "harlem")` → field focused, "Harlem" appears (auto-capitalized), search auto-submits, 5 results render. Single API call. The cliclick path that broke `UITextField` focus is fully fixed.
- **Day 4.** Three rough edges identified and filed: recordings serializing pixel coords without `stable_id`, `observe(annotate=false)` wiping the mark cache, `type_text` returning no focus signal. All three closed in subsequent releases.
- **Day 5.** `CLAUDE.md` updated — predecessor section retitled "ARCHIVE", SpecterQA declared the canonical sim driver. `docs/Testing/REGRESSION_TEST_MATRIX.md` updated. Harness gained a `harness simdrive {status,upgrade,sessions}` subcommand. The predecessor's 26-journey corpus kept on disk as archive but not extended.

## The result

Three dogfood feedback rounds in two weeks. Every reported issue closed in the next release.

| Round | Reports | Closed | Notable |
|---|---:|---:|---|
| v0.2.0a1 | 3 | 3 | `stable_id` on recordings, `keyboard_visible`/`focused_field` on `type_text` response, mark-cache retention on `annotate=false` |
| v0.2.0a2 | 2 | 2 | `last_seen` + `unavailable_reason` on `list_devices`, `app_version` on recordings |
| v0.3.0a2 | 4 | 4 | `injection_method` + `dispatch_succeeded` on `type_text`, dictionary-gated OCR confidence, `version` MCP tool, `clear_field` |

Feedback turnaround: same-day to next-day on all rounds. The dogfood loop is the product development loop.

> "Replays are now reliable enough to gate PRs on."
>
> — Maurice Carrier, exampleorg

## What this unblocked

Coverage Example Reader did not have before:

- **the reader regression coverage.** The `~/.simdrive/journeys/the reader-page-forward.yaml` is the first canonical journey. Open EPUB → page forward → open TOC → bookmark — a flow that was 100% manual under the predecessor because XCTest can't see into `WKWebView`. SpecterQA sees pixels.
- **OAuth and SAML flow validation.** Out-of-process Safari sheets are now drivable. Library auth flows that gate every downstream test can be exercised end-to-end.
- **iOS 26 `UITextField` regression coverage.** Auth forms, search, library-add — anything with text input. The cliclick path that broke focus is replaced with real `UITouch` via HID. Keystrokes land on first contact.
- **Visual regression gating in CI.** SSIM-thresholded replays (0.85 default, configurable per-journey, with `mask_regions` for the iOS status-bar clock) running on a small set of critical journeys via a new `--specterqa` flag in `scripts/verify-pr.sh`. Opt-in initially, then default-on.

The predecessor's 26-journey corpus stays on disk as archive — useful for archaeology, not extended. The new corpus is built fresh against the flows that actually matter, with the tooling that can actually reach them.
