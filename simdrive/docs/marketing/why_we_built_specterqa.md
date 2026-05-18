# Why we built SpecterQA

*Maurice Carrier — founder, SyncTek*

The short version: I gave up on XCTest three times before I figured out that the tool I needed already existed inside the model.

The longer version is what this post is about — what broke, what I tried, what finally worked, and what I think is going to break next. If you've ever spent a Sunday afternoon debugging why a perfectly good `XCUIApplication.textFields["email"].typeText("...")` types nothing on iOS 26, this one is for you.

## The XCTest pivot, three times

The thing I kept trying to build was a "Cypress for iOS." A persistent test runner the agent could talk to, that knew how to find elements, that ran inside the app's process and had access to the accessibility tree. Each rebuild looked plausible at the start and broke under load.

**Build one** was a Swift XCTest runner with a custom HTTP daemon. The agent sent commands; the runner translated them into XCUITest queries and dispatched them. It worked beautifully for a week. Then we hit the iOS 26 beta and `UITextField` first-responder broke. You'd tap the field, the keyboard would appear, and the next `typeText` would lose its first three characters. Filed a Feedback. Watched it sit. Patched around it with a 500ms sleep, which then made our search-as-you-type tests flaky.

**Build two** was a forked WebDriverAgent. Same general shape, more battle-tested upstream. The fork lasted until we tried to run two test files in parallel against two simulators and the WDA process management collapsed in a way that needed me to read 18 pages of CFRunLoop documentation to fix. I shipped that fix. Two weeks later the next Xcode beta dropped and the WDA build broke entirely against the new toolchain.

**Build three** was an accessibility shim — a Swift package the host app would import, exposing a runtime endpoint the agent could query for the live a11y tree. Cleaner architecturally. Required the host app to opt in by linking the package. We got it running against our internal apps. Then I tried to use it against Palace's reading flow, hit Readium 3.x's `WKWebView`, and discovered that the WebKit accessibility tree is a parallel universe XCTest never reaches into. Every flow inside the actual reader — page-forward, table of contents, bookmarks — was invisible.

That was the moment I admitted the abstraction was wrong.

## The "agents look at screens" insight

The thing that finally cracked it was something I'd been doing manually for months without noticing. When XCTest's selectors couldn't find something, my workflow had quietly drifted to: take a screenshot, paste it into Claude, ask Claude where the button was, type the coordinates back into a `tap` call. I was doing this dozens of times a day. The selector layer had already migrated into the model. I just hadn't admitted it.

Once you see it, the implication is unavoidable. For a decade, mobile automation has been a *selector* problem — accessibility identifiers, XPath, label matching, OCR fallback, ML-based element-finding. Every framework spends most of its complexity budget on "find me this thing." A vision-capable model does that for free. It looks at a screenshot, it tells you where the thing is. The runtime's only job is to dispatch a touch where the model points.

So I deleted the runner. Deleted the daemon. Deleted the selector library. Deleted the accessibility shim. What was left was a question: how do you actually drive the simulator from outside?

## What SpecterQA actually became

The honest answer to "how do you drive the simulator" is: through `CoreSimulator`'s HID port, using a private SPI called `SimDeviceLegacyHIDClient` and a message format called `IndigoMessage`. Apple uses it internally for Simulator.app. It's the path that triggers a real `UITouch` instead of a synthetic mouse event — the difference matters because `UITextField` on iOS 26 only accepts first-responder focus from a real `UITouch`. That regression is what killed every XCUITest workflow that typed into a text field. You can verify: try `cliclick c:200,400` against an iOS 26 sim with a `TextField` at that location, then try typing. The keystrokes go nowhere. Drive the same tap through `SimDeviceLegacyHIDClient` and the field focuses on first contact.

The native helper is ~600 lines of Objective-C that ships as a `universal2` Mach-O binary inside the Python wheel. The Python side is ~4,100 lines and does everything else: simulator lifecycle (`session_start`, `session_end`, `session_status`), the vision layer (`observe` returns a raw PNG, an annotated copy with numbered red boxes drawn over every detected text region, and a `marks[]` array), the act layer (`tap`, `swipe`, `type_text`, `press_key`, `clear_field`), recording and replay with SSIM-gated drift detection, performance snapshots, crash retrieval, environment diagnostics. 29 MCP tools total.

The shape of an agent loop: `observe()` → look at the annotated image → `tap text="Sign in"` or `tap stable_id="a229e82e3f00"` → `observe()` to confirm. That's it. Targets accept `{x, y}`, `{mark: <id>}` from the latest annotated observe, `{text: "..."}` matched against detected OCR text, or `{stable_id: <hash>}` derived from `(text + 20px-bucketed bbox)`. The `stable_id` survives between observes — the mark id reshuffles on every screen, so we ship a stable hash to gate replays on.

The mechanic that I underrated when I designed it, and that has since become the most-used feature: SSIM masking on replay. Per-step pixel-similarity check against the recorded pre-screenshot, configurable threshold (default 0.85), `mask_regions` to blank dynamic chrome before the compute. The first version didn't mask the iOS status-bar clock. Same-screen replays drifted into the 0.6s, randomly. One added field on the recording schema and the same-screen drift went away. That's a representative bug for what this product is — most of the engineering is composing pixel-space primitives that have been there all along.

## The Palace cutover

I was nervous shipping this against a real customer. ThePalaceProject's Palace iOS app — `org.thepalaceproject.palace`, the public-library reading client used by hundreds of US library systems — has the worst possible XCTest surface area: Readium 3.x reading flows running in `WKWebView`, OAuth/SAML auth via out-of-process Safari sheets, library search through SwiftUI components without explicit accessibility identifiers. None of it was testable under XCUITest. The Palace team had been carrying a manual-QA bill on every release.

We ran the cutover in 5 days. Day 1, install + first `observe` against the catalog screen. Day 2, the tab-bar tour replay. Day 3, the `type_text` regression repro that proved the iOS 26 `UITextField` issue was actually fixed — a search query landed, results rendered, search auto-submitted. Day 4, three rough edges identified and filed. Day 5, fixes shipped, predecessor archived in the team's `CLAUDE.md`, SpecterQA declared canonical.

Three dogfood rounds since. All feedback closed. The headline from the v0.2.0a1 report:

> "SpecterQA is now the canonical iOS sim driver for Palace iOS development, replacing the predecessor."

And from the v0.3.0a2 round:

> "Replays are now reliable enough to gate PRs on."

That second sentence is the whole product thesis stated as a customer outcome. The replay format is a self-contained YAML+PNG bundle you commit to your repo. SSIM drift gating tells you when a replay's screen has moved relative to the recording. PR-gating on visual regressions is no longer the BrowserStack-priced enterprise feature it used to be.

## What's next

Two things on the immediate roadmap.

**v1.0 stable, sim-only.** The 1.0 cut closes five small items: a `wait_for_keyboard` default to remove the only known silent-failure path on `type_text`, a fix to the `perf` snapshot stale-cache bug, an auto-generated tool table in the README so it can never drift again, a written `STABILITY.md` declaring what's covered by SemVer, and a consolidation of the open `LIMITATIONS.md` and `BEST_PRACTICES.md` items. Two-week clock from 0.3.0a3.

**v1.1 with real-device input via WebDriverAgent.** Today, real-device sessions support `observe`, `logs`, and app lifecycle through `xcrun devicectl` and `libimobiledevice`. `tap`, `swipe`, `type_text`, `press_key` raise `device_input_unavailable` because there's no equivalent of `SimDeviceLegacyHIDClient` for a paired iPhone. WDA is the orthodox bridge. ~3-5 days of implementation plus the provisioning UX, scoped for v1.1.

After that, the Cloud tier — hosted replay archive, SSIM-trend dashboards, multi-sim parallelism, the productized `--specterqa` PR-gate flag for CI. That's a separate package under a separate license. The 29 tools described here stay MIT, forever.

## What I don't know yet

I want to be honest about the existential risks because I'd rather you hear the case from me than from a competitor.

**Anthropic ships native iOS simulator drive in `claude-code`.** Probability medium, time-to-impact 9-15 months. The defensive move is to be the iOS-deep layer Anthropic doesn't build — perf, crashes, replays, real UITouch on iOS 26, the things that take a year of iOS-specific work and aren't a great use of an applied-research team's time. Pursue explicit registration in the MCP registry as the canonical iOS path.

**Apple ships an AI/Agent UI test framework at WWDC 2026.** Probability lower, time-to-impact 12-18 months. The defensive move is to focus SpecterQA on the cross-Apple-version regression surface that any first-party tool is structurally bad at — testing iOS 25 + iOS 26 + iOS 27 from one runtime is something Apple has historically not optimized for.

**Maestro ships an MCP wrapper.** Probability high, time-to-impact 3-6 months. The defensive move is to own iOS-deep — real HID + perf + crashes + replays + the things Maestro's cross-platform position can't structurally match.

**A well-funded YC competitor launches.** Probability medium. They'll have a marketing budget; we'll have receipts. Lock in named customer logos by Q3 and win the OSS-credibility race.

I think the window for category-definition is roughly 9 months. Every week the gap narrows. That's why this is shipping now, in alpha, with the rough edges visible, instead of polished and late.

If you build iOS — especially if you've spent a Sunday afternoon debugging XCUITest — `pip install specterqa-ios`, drop it into your `.mcp.json`, and tell your agent to drive your sim. Then tell me what broke. The repo is `github.com/SyncTek-LLC/specterqa-ios`. The dogfood loop is what makes this thing work. I read every report.
