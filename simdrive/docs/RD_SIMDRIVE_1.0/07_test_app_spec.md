# SimDrive 1.0 — Test App Specification (LapsApp)

**Author:** TestAppArchitectureAtlas (Workstream C, BIS expansion round)
**Date:** 2026-04-29
**Status:** R&D memo
**Companions:** `05_engineering_expansion.md` (Workstream A — 1.0 build), `06_world_class_moat_features.md` (Workstream B — post-1.0 moat)
**Scope:** the dogfood iOS app that SimDrive will be driven against, not SimDrive itself

---

# §1. The premise

SimDrive needs a canonical, feature-rich iOS app to drive against — one that exercises every capability listed in `00a_VALIDATED_FACTS.md §A` and every roadmap surface from Workstreams A and B against realistic flows. This app is for three audiences at once: (a) the SimDrive engineering loop, where journey replays gate every PR; (b) the prospective customer, who watches Claude drive it through OAuth and a reading flow in a 90-second demo; (c) the open-source iOS engineer, who clones the repo and reproduces our journey corpus on their own laptop in under fifteen minutes. The app exists because today's TestKitApp cannot serve any of those audiences without contortion.

**Positioning vs the existing TestKitApp** (`/Users/atlas/Documents/specterqa-ios/TestKitApp/`):

- **TestKitApp is a flat unit-test fixture.** Five SwiftUI tabs (Form, List, Nav, Stress, Example Reader) hand-tuned to expose the specific failure modes simdrive's 91 unit tests probe — soft-keyboard focus, debounced input, layout shifts that rebucket `stable_id`. It is excellent at what it is, which is a deterministic diagnostic harness for a single engineer. It will continue to serve that role and is not deprecated by this spec.
- **The new app is a feature-rich consumer-grade iOS app**, designed to look and behave like something a real customer ships, with an onboarding flow, OAuth, a tab bar with five primary surfaces, push permissions, a reader, search, settings, and a deliberate crash trigger. It exercises every SimDrive capability through the lens of *a real journey through a real app*, not through the lens of *a fixture screen designed to expose one feature at a time*.

**Chosen name: `LapsApp`** — a fitness/run-tracking app, MIT-licensed, shipped under `github.com/SyncTek-LLC/LapsApp`. Reasoning: (a) "Laps" maps cleanly to iterative observation cycles that SimDrive itself runs (every replay is a lap around the journey), giving a thematic hook for marketing; (b) fitness apps cover the realistic surface we want — auth, GPS permission, lists with pull-to-refresh, charts, settings, share sheets, push, dark mode — without needing fictional content like books or articles that would invite copyright entanglement; (c) "Laps" is shorter than `SimDriveDemo` and carries no ambiguous corporate weight; (d) a fitness motif lets us seed the journey corpus with personas (`first_time_runner`, `marathon_trainer`, `casual_walker`) that read as believable end users. ReadShelf was a strong runner-up — Example Reader already proves the reader pattern stresses WebView blind spots — but a reader app overlaps Example Reader's domain too directly to ship as our public demo. LapsApp lets Example Reader stay the customer story and LapsApp be the canonical demo.

---

# §2. The architecture

LapsApp is a single iOS-native binary, **iOS 17+ deployment target** (one major version below current at any time so SimDrive's iOS-26 HID path keeps a wider install base in the demo), built canonically. No exotic patterns. The whole point is that an engineer who clones it should recognize every file from their day job.

| Layer | Choice | Reasoning |
|---|---|---|
| Language | Swift 6 (strict concurrency on) | Matches Apple's current direction; surfaces actor-boundary bugs SimDrive should be able to observe through. |
| UI primary | SwiftUI | Default for any new iOS app today; covers ~85 % of our screens. |
| UI secondary | UIKit, surgical | Per Example Reader's the reader lessons in `02_brand_marketing.md` and the SwiftUI gaps Example Reader hit: the WebView reader, the Dynamic Island modal, and one custom UICollectionView for the workout history list. |
| Persistence | SwiftData (Core Data backing) | Modern, deterministic, easy to wipe via test-reset launch arg. |
| Networking | URLSession + actual HTTP backend | Real round-trips so SimDrive's `observe` exercises real loading states, not animations. |
| Backend | Cloudflare Worker stub | Single Worker at `lapsapp-api.synctek.workers.dev` with deterministic state per `X-Reset-Token` header. Free tier handles our demo + CI traffic. Workers replays are byte-identical, which is what journey reproducibility requires. |
| Auth | Sign in with Apple (real, Apple-mediated) + email/password (against Worker stub) + Google OAuth (real, opens Safari/SFSafariViewController) | Sign in with Apple covers the in-process auth path. Google OAuth covers the **out-of-process Safari sheet**, which is the path Workstream B's `webview` and `oauth` features need to exercise. Email/password covers the deterministic-credentials path for CI. |
| Distribution | Open-source, MIT, on `github.com/SyncTek-LLC/LapsApp` plus TestFlight binary for live demos | TestFlight gives prospects a one-tap install on their own iPhone during a sales call; the GitHub repo gives engineers something to clone and grep. |
| Telemetry | None in the open-source build | A demo app must not phone home. Crash logs come from `simctl` and `idevicesyslog` only. |

The repo layout mirrors a healthy production iOS app: `LapsApp/` (the Xcode project), `LapsAppKit/` (a Swift package for shared models and view-models so unit tests run without booting a sim), `Backend/` (the Cloudflare Worker source, deployed via `wrangler`), `journeys/` (the SimDrive YAML corpus, one file per journey), `docs/` (a README, a screencast, and a `JOURNEYS.md` index).

---

# §3. The 12 feature areas

Each area is a real screen group in LapsApp and a test surface for one or more SimDrive capabilities. Each gets at least one journey YAML in §4. Every feature lists `accessibilityIdentifier` values explicitly because SimDrive's vision-first observe falls back to a11y-id lookup when OCR misreads, per the dictionary-gating fix in 0.3.0a3.

**1. Onboarding + first-launch alerts.** Three-screen welcome carousel, then push, location, and contacts permission asks in sequence. Identifiers `welcome_next`, `permission_push_allow`, `permission_location_while_using`. Exercises `dismiss_first_launch_alerts`, `pre_grant_permissions`, the 1-in-4 alert-race re-observe loop. Journey: `onboarding-fresh-install`.

**2. OAuth login (Sign in with Apple + Google).** Email/password row, Apple button, Google button. Apple stays in-process. Google launches `ASWebAuthenticationSession` which spawns a Safari sheet — out of the app's process, which is the case Workstream B's webview support needs to survive. Identifiers `auth_apple_button`, `auth_google_button`, `auth_email_field`, `auth_password_field`. Exercises vision-first observe across processes, focus durability, and the iOS 26 UITextField focus fix. Journeys: `oauth-google-happy`, `oauth-google-cancel`, `oauth-apple-happy`, `email-password-login`.

**3. WebView content (workout-blog reader, Readium-style).** A blog tab where each post opens in a `WKWebView` with selectable text, scrollable, with a share button. WKWebView is XCTest-blind on iOS, which is the killer surface SimDrive's vision-first model exists to solve. Identifiers `blog_post_<slug>`, `blog_share`. Exercises OCR-only navigation, swipe in WebView, post-1.0 webview tool. Journeys: `blog-read-and-share`, `blog-scroll-bottom`.

**4. Search + autocomplete + debounced input.** Search tab with 250 ms debounce and server-side autocomplete. Identifiers `search_field`, `search_result_<index>`. Exercises `type_text` against debounce, the wait-for-keyboard fix, the `clear_field` tool, and the soft-keyboard heuristic correction in 0.3.0a3. Journeys: `search-with-debounce`, `search-clear-and-retry`.

**5. Multi-screen navigation (tab bar + nav stack).** Five tabs: Home, Activities, Search, Blog, Settings. Each pushes detail screens. Identifiers `tab_home`, `tab_activities`, `tab_search`, `tab_blog`, `tab_settings`, plus `activity_row_<id>`. Exercises `stable_id` durability across screens, the `stable_id_loose` fallback, and the mark-cache preservation under `observe(annotate=false)`. Journey: `tab-bar-tour-and-back`.

**6. Sheets + modals + Dynamic Island modal.** Activity-detail screen presents an "Add Note" sheet. Settings presents an "Edit Profile" sheet. A "Live Activity" feature toggles into Dynamic Island display, surfacing the documented limitation in `LIMITATIONS.md`. Identifiers `note_sheet_text`, `note_sheet_save`, `live_activity_start`. Exercises `dismiss_sheet`, surfaces the Dynamic Island case as a known-limitation regression journey. Journeys: `add-note-sheet`, `dynamic-island-shows-limitation`.

**7. Forms with async validation.** Sign-up form: email, password, password-confirm, age, terms checkbox. Server validates email uniqueness asynchronously. Error states ("email taken", "weak password") render inline. Identifiers `signup_email`, `signup_password`, `signup_submit`, `signup_error_<field>`. Exercises record/replay reliability across async server states, SSIM masking around dynamic error text. Journey: `signup-with-validation`.

**8. Lists with pull-to-refresh + infinite scroll.** Activities tab is a 50-row initial list with pull-to-refresh and infinite scroll. Identifiers `activities_list`, `activity_row_<index>`. Exercises `swipe`, scroll perf, and the `swipe` home-indicator zone warning. Journey: `pull-refresh-and-scroll`.

**9. Settings (light/dark, push, accessibility text size).** Toggles for appearance (system/light/dark), push, and accessibility text size (small/medium/large/extra-large). Identifiers `settings_appearance_dark`, `settings_text_size_xl`. Exercises `set_appearance`, accessibility-audit roadmap (Workstream B). Journey: `dark-mode-toggle`.

**10. Crash trigger (developer menu).** Long-press the app icon on Settings → "Crash now" menu item that calls `fatalError`. Identifiers `dev_menu_open`, `dev_menu_crash`. Exercises `crashes` retrieval and post-crash app-state diagnostics. Journey: `crash-and-recover`.

**11. Performance stress (1000-row activity list, animation-heavy detail).** A "Year in Laps" tab loads 1000 activities and renders an animated chart on detail. Identifiers `year_list`, `year_chart`. Exercises `perf_baseline` / `perf_compare`, the cached-RSS fix from 0.3.0a2, and the post-1.0 perf regression dashboard from Workstream B. Journey: `perf-baseline-and-stress`.

**12. Offline / network conditions.** Settings has a "Simulate Offline" toggle that flips the URLSession to a 30-second-timeout config. List shows a graceful empty state. Identifiers `network_offline_toggle`. Exercises the deferred `network` tool whenever it ships. Journey: `offline-mode-graceful`.

---

# §4. The journey corpus

Twenty pre-built journeys ship in `journeys/`, indexed by `JOURNEYS.md`. Each is a YAML written against the journey-runner schema in `01_product_engineering.md §1.1`. Each names a persona (`personas/` directory), a target (`simulator` by default; the real-device journeys carry `target: device`), and a goal sequence.

Three personas seed the corpus and are themselves the user-facing examples for the SimDrive 1.0 product:

- `first_time_runner` — installs LapsApp fresh, clicks through onboarding, denies push, allows location, signs up with email.
- `returning_user` — already authenticated, opens app, browses activities, shares a blog post.
- `power_user` — toggles dark mode, runs a 5K workout, logs a note, exports data.

The 20 journeys, grouped by feature area:

| # | Journey | Persona | Target | Feature area | What it validates |
|---|---|---|---|---|---|
| 1 | `onboarding-fresh-install` | `first_time_runner` | sim | 1 | `dismiss_first_launch_alerts`, alert-race retry |
| 2 | `onboarding-deny-all` | `cautious_user` | sim | 1 | Permission-deny path, app handles gracefully |
| 3 | `oauth-google-happy` | `returning_user` | sim | 2 | Out-of-process Safari sheet observe + tap |
| 4 | `oauth-google-cancel` | `cautious_user` | sim | 2 | Cancel mid-flow, recover, retry |
| 5 | `oauth-apple-happy` | `returning_user` | sim | 2 | In-process Apple sheet, biometric prompt dismissal |
| 6 | `email-password-login` | `power_user` | sim | 2 | UITextField focus + type, iOS-26 HID path |
| 7 | `signup-with-validation` | `first_time_runner` | sim | 7 | Async validation, SSIM masking around error text |
| 8 | `tab-bar-tour-and-back` | `power_user` | sim | 5 | `stable_id` durability across all 5 tabs |
| 9 | `search-with-debounce` | `power_user` | sim | 4 | `type_text` over 250 ms debounce |
| 10 | `search-clear-and-retry` | `power_user` | sim | 4 | `clear_field` then re-type, focus durability |
| 11 | `blog-read-and-share` | `power_user` | sim | 3 | WKWebView OCR-only navigation, share sheet |
| 12 | `blog-scroll-bottom` | `power_user` | sim | 3 | `swipe` in WebView, end-of-content detection |
| 13 | `add-note-sheet` | `power_user` | sim | 6 | Sheet present + dismiss + persistence |
| 14 | `dynamic-island-shows-limitation` | `power_user` | sim | 6 | **Regression journey: must fail with documented Dynamic Island limitation** |
| 15 | `pull-refresh-and-scroll` | `returning_user` | sim | 8 | Pull-to-refresh + infinite scroll, perf snapshot |
| 16 | `dark-mode-toggle` | `accessibility_user` | sim | 9 | `set_appearance` + a11y text size |
| 17 | `crash-and-recover` | `bug_finder` | sim | 10 | Crash trigger, `crashes` retrieval, recovery |
| 18 | `perf-baseline-and-stress` | `power_user` | sim | 11 | `perf_baseline` then 1000-row stress, severity high |
| 19 | `offline-mode-graceful` | `power_user` | sim | 12 | Offline toggle, graceful empty state |
| 20 | `device-observe-only` | `power_user` | **device** | 5,3,9 | Real-device read-only smoke (observe + logs + lifecycle) |

**Two journeys must fail by design.** `#14 dynamic-island-shows-limitation` halts on a known Dynamic Island blind spot; the journey runner must exit non-zero with a `LIMITATIONS.md` cross-reference. `#4 oauth-google-cancel` includes a sub-step that intentionally taps the wrong button to test recovery — its journey-level outcome is "passed-after-retry", not "passed-clean". These are the regression journeys that catch real bugs rather than just recording green runs; without them, a SimDrive bug that causes every journey to silently pass would be invisible.

---

# §5. The state machine

Deterministic state between journeys is non-negotiable. We follow Example Reader's `-Example ReaderTestReset` pattern, named for our app: **`-LapsAppTestReset`**, passed as a launch argument by the journey runner before every journey.

When `-LapsAppTestReset` is present, the app at startup: (a) wipes its SwiftData store; (b) clears the keychain entries under the `io.synctek.lapsapp` access group; (c) clears `URLCache.shared`; (d) sends a `POST /reset` to the Cloudflare Worker stub with the per-journey `X-Reset-Token` header (returning the worker to its seeded fixture state for that journey); (e) flips a `isUITestMode` flag that disables animations longer than 100 ms and skips the welcome confetti view that interferes with OCR.

We rejected reset-via-UI (slow, brittle: a single Settings UI rename breaks every journey) and reset-via-backend-only (incomplete: doesn't clear local SwiftData or keychain). The launch-arg path is what Example Reader runs in production, what Apple's own Xcode UI testing uses, and the pattern engineers will recognize. It also keeps the journey runner's reset call fast (sub-100 ms) because there's no UI traversal — the app re-launches into the same screen state every time.

Per-journey worker state is keyed by the `X-Reset-Token` value in the journey YAML's `setup:` block. The worker holds its fixtures in Durable Objects scoped to the token, so two journeys running in parallel against the same worker do not collide.

---

# §6. The build / dogfood loop

LapsApp drops into the SimDrive engineering loop as the **gate substrate** for every PR. The loop:

1. SimDrive PR opens. CI checks out LapsApp at a pinned tag, builds it once into `~/Library/Caches/SimDrive/LapsApp.app`.
2. The journey corpus runs with the new SimDrive build against the cached LapsApp binary. `simdrive run --corpus journeys/` exits zero only when all 20 journeys behave as expected (the two intentional-fail journeys must fail in their documented way).
3. Failures gate PR merge. This is the `--simdrive` PR-gate Example Reader built and now uses; we adopt it directly.
4. Each new SimDrive feature comes with a new LapsApp journey. A SimDrive PR adding a webview tool requires a journey under `feature_area: 3` exercising it, or the PR is incomplete.
5. Quarterly we add 2-3 new feature areas to LapsApp to exercise emerging SimDrive capabilities. The first such expansion (Q3-2026) lands biometric-gated workout sharing, exercising `pre_grant_permissions(biometric)` once that ships.

This is the same dogfood loop Example Reader runs for SpecterQA-iOS today, reflected back at SimDrive itself. The asymmetry is that LapsApp is ours and we control its evolution — when SimDrive needs a new test surface, we add a screen.

---

# §7. Open-source release plan

LapsApp ships **MIT** under `github.com/SyncTek-LLC/LapsApp`, with `LICENSE`, `NOTICE`, and a 200-line `README.md` whose first three sections are: **what this is** (a demo iOS app), **how to run it** (`open LapsApp.xcodeproj` then ⌘R), and **how to drive it with SimDrive** (a single `simdrive run --corpus journeys/` command).

Public discoverability paths: (a) link from the SimDrive README under "Try it"; (b) link from the `synctek.io/products/simdrive/` page hero; (c) a Show HN post pinned to LapsApp's repo, pointing at the 90-second screencast; (d) a tag-based listing on the `iOS-testing` topic on GitHub; (e) a registry entry on `awesome-mcp` once LapsApp is the canonical demo target referenced in the SimDrive MCP listing.

The README explicitly refuses pull requests adding telemetry or analytics. A `CONTRIBUTING.md` invites new feature areas only when they exercise a SimDrive capability not already covered, with the test journey as part of the PR. We do not accept feature drift unrelated to SimDrive's surface — LapsApp is a demo, not a product, and resisting feature creep is part of keeping the demo crisp.

---

# §8. The marketing payoff

LapsApp doubles as the canonical SimDrive marketing asset:

- **Show HN demo:** "Watch Claude drive LapsApp through Google OAuth, search, read a post, and toggle dark mode — in 90 seconds, on an iOS 26 simulator." This is the pinned tweet, the hero video on `synctek.io/products/simdrive/`, and the YouTube short.
- **Product-page hero:** the same 90-second video.
- **Reference YAML:** the 20 journeys under `journeys/` are the canonical "what real iOS journeys look like in SimDrive YAML" examples linked from the SimDrive docs site. New customers writing their first journey copy from `oauth-google-happy.yaml` or `search-with-debounce.yaml`.
- **Customer onboarding:** "Fork LapsApp, point it at your bundle ID, follow the three-step README, and you have a working SimDrive setup." Time-to-first-replay drops from "a day of YAML wrangling" to "thirty minutes of fork-and-edit."
- **Conference demos:** the TestFlight binary plus the journey corpus turn into a 5-minute live demo at any iOS conference. Drop the URL on the slide, prospects install on their own iPhone, watch it run, and leave with the GitHub link.

The compound effect: every SimDrive sales conversation has a working demo, every prospect has a starting template, and every iOS engineer who's curious has a clone-able repo.

---

# §9. Effort estimate

One full-time iOS engineer, no parallel context-switching. Honest estimate, not optimistic.

| Phase | Effort | Detail |
|---|---|---|
| App scaffolding + 12 feature areas (basic UI, models, tabs) | 4 weeks | Three weeks for the screens, one for polish and `accessibilityIdentifier` audit. SwiftUI gets you 80 % of the way; the WebView reader and Dynamic Island modal eat real time. |
| Cloudflare Worker stub + `-LapsAppTestReset` plumbing | 2 weeks | One week for the Worker, one for the per-journey reset-token state machine and Durable Objects. |
| OAuth integrations (Apple + Google) | 1.5 weeks | Apple is fast; Google's `ASWebAuthenticationSession` integration plus the Worker's redirect handling is where the time goes. |
| 20 pre-built journey YAMLs + personas + corpus runner | 2.5 weeks | Half a week per five journeys plus polish; the two intentional-fail journeys take the most calibration. |
| TestFlight provisioning + first build | 0.5 weeks | App Store Connect, certs, internal testers. |
| Open-source release (README, CONTRIBUTING, Show HN copy, screencast) | 1 week | Screencast eats half. |
| Polish, integration with SimDrive PR gate, first end-to-end run | 1.5 weeks | The "make it actually work as the gate substrate" tax. Always larger than expected. |
| Buffer (test-app overrun tax) | 1 week | Test apps are notorious for overrun. This buffer is non-negotiable; cutting it is how the project slips. |

**Total: 14 calendar weeks (~3.5 months) with one engineer dedicated.** This is honest, not optimistic. Half-time engineer doubles it to seven months. Two engineers in parallel does not halve it because the WebView/OAuth/journey-corpus work is sequentially dependent — call it nine weeks with two engineers, with diminishing returns past two.

---

# §10. Risks

**R1: Test-app feature creep.** LapsApp grows past 12 feature areas because every new SimDrive capability "needs" its own surface. Mitigation: the `CONTRIBUTING.md` rule that no new screen lands without a SimDrive capability it uniquely exercises. Quarterly review prunes stale areas.

**R2: WebView reader is harder than the spec implies.** Readium-style readers sound simple and never are; the Dynamic Island modal is the same trap. Mitigation: timebox each at 1 week of engineering time. If they exceed budget, drop the Readium aspiration and ship a simpler `WKWebView` over a static blog post — the test surface is "WKWebView is XCTest-blind", not "Readium fidelity."

**R3: Cloudflare Worker non-determinism under load.** Durable Objects state can leak between journeys if the per-token reset isn't bulletproof. Mitigation: every journey starts with a `POST /reset` and asserts its expected fixture is loaded before step 1; the runner halts hard on assertion failure rather than running against polluted state. CI runs a journey-isolation regression nightly.

**R4: TestFlight review delay blocks the launch.** Apple's TestFlight review is usually fast but can stall for unclear reasons. Mitigation: submit two weeks before the public SimDrive 1.0 launch date so any rejection has runway. Internal testers (engineers + prospects who explicitly opted in) cover the gap if external testing slips.

**R5: The two intentional-fail journeys atrophy into "always-skip" status.** Engineers under deadline pressure tend to mark deliberately-failing tests as flaky and skip them. Mitigation: the corpus runner exits non-zero unless the intentional-fail journeys fail in the expected documented way — silent skipping is itself a failure mode. A dashboard widget flags any journey skipped more than once in a week.

---

*This memo is the test-app half of the SimDrive 1.0 expansion BIS round. It must not drift from `00a_VALIDATED_FACTS.md` — every LapsApp journey traces to a row in §A or §B of that document, or it is roadmap, not 1.0 substrate.*
