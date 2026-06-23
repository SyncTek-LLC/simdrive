# Show HN: SpecterQA – MCP-native iOS simulator driver, no XCTest

Hi HN. I'm Maurice. For the last decade I've shipped iOS at companies where the test pyramid had a giant hole in the middle: anything XCUITest couldn't see — `WKWebView` content, SwiftUI no-AX components, out-of-process Safari sheets, the OAuth and SAML flows that actually carry your auth — was untested or covered by manual QA. iOS 26 made it worse: the `UITextField` first-responder path under XCUITest broke, so even the working flows started losing keystrokes.

I tried to fix this the orthodox way three times. A Swift XCTest runner with a custom HTTP daemon. Then a forked WDA. Then an accessibility-shim layer. Each rebuild added a moving piece that broke on the next Xcode beta. The thing I kept tripping over was that I was teaching a machine to find buttons — synthesizing selectors, matching labels, reconstructing an XML tree from a JSON dump — when the machine could already see the screen. Vision-capable models removed the selector layer. Once that clicked, I deleted the runner, the daemon, the selectors, and rewrote the input layer to drive `CoreSimulator` directly through `SimDeviceLegacyHIDClient` + `IndigoMessage`. That's SpecterQA.

The mechanic: the agent calls `observe`, gets back a screenshot plus an annotated copy with numbered red boxes over every detected text region, decides where to act, and SpecterQA dispatches a real `UITouch` through the HID port. `tap text="Sign in"` or `tap stable_id="a229e82e3f00"` resolves to pixel coords against the latest annotated observe. Type, swipe, press-key all flow through the same path. 29 MCP tools cover the agent loop.

How it differs from neighbors: **Maestro** is more mature and ships Android, but it's not MCP-native — the agent has to bounce through a CLI. SpecterQA is designed for the LLM tool loop. **Detox** is great for React Native because it gray-boxes the JS bridge; outside RN, SpecterQA wins by default. **XCUITest** can't see WebViews and broke on `UITextField` focus on iOS 26. **claude-computer-use** is the obvious existential risk and it lacks native HID, sim session lifecycle, `simctl` integration, log tail, crash retrieval, perf, recording/replay, OCR-marks, and `stable_id`. That's roughly 6-9 months of focused work to rebuild.

Real receipt: a real-world iOS app — a public-library reading client — migrated off the predecessor in **5 days**. Three dogfood rounds, all feedback closed. Their reading-engine `WKWebView` and OAuth/SAML flows weren't testable before. Maurice from their team:

> "Replays are now reliable enough to gate PRs on."

What's open: the engine is **MIT** and ships as `pip install specterqa-ios`. All 29 tools, the `universal2` native HID helper, vision-first observe, record-and-replay with SSIM drift gating — permanently free. What's not yet: Cloud (hosted replay archive, SSIM-trend dashboards) and real-device input via WebDriverAgent ship in a separate paid package.

Honest limits: macOS-only because `CoreSimulator` doesn't exist anywhere else. Simulator-only in v1.0; real-device input is read-only (`observe`, `logs`, lifecycle work; `tap`/`swipe`/`type` raise `device_input_unavailable`) until v1.1 lands WDA. Not an XCTest replacement for accessibility audits — run XCTest in parallel for those. 91 unit tests, 26 live E2E against `TestKitApp`, validated on iOS 26.3 / iPhone 17 Pro sim.

Install: `pip install specterqa-ios` and add to `.mcp.json`. Repo: github.com/SyncTek-LLC/specterqa-ios. Happy to answer questions about the HID path, why I gave up on XCTest, or what broke during the reference customer's cutover.
