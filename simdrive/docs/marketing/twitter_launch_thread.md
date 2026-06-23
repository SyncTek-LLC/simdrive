# SpecterQA — launch thread

---

**1/**

Shipping SpecterQA today. MCP-native iOS simulator driver. Your agent looks at a screenshot, picks a pixel, and SpecterQA dispatches a real UITouch through CoreSimulator. No XCTest. No selectors. No accessibility tree.

`pip install specterqa-ios`

---

**2/**

The mechanic in one line: screenshot in, click out.

`observe()` returns a raw PNG plus an annotated copy with numbered red boxes over every OCR'd text region. The agent calls `tap text="Sign in"` or `tap stable_id="a229e82e3f00"`. SpecterQA resolves to pixels and dispatches.

---

**3/**

Why this works now: vision-capable models removed the selector bottleneck. For a decade, automation meant teaching a machine to find a button. The machine can already see the screen. The selector layer is the model. The runtime just dispatches.

---

**4/**

Killer technical bit: the bundled native helper drives `SimDeviceLegacyHIDClient` + `IndigoMessage`. That's the path that triggers UITextField first-responder on iOS 26 — the regression that broke XCUITest workflows. Synthetic mouse events stopped working. Real UITouch lands.

---

**5/**

Receipt: a real-world iOS app — public-library reading client, Readium WKWebView + OAuth/SAML — migrated off the predecessor in 5 days. Three dogfood rounds, all feedback closed.

> "Replays are now reliable enough to gate PRs on." — Maurice Carrier, reference customer

---

**6/**

29 MCP tools: lifecycle, observe, tap/swipe/type/press, record-and-replay with SSIM drift gating, perf snapshots, crash retrieval, env diagnostics. 91 unit tests, 26 live E2E against TestKitApp. macOS + iOS Simulator. Real-device input ships in v1.1 via WDA.

---

**7/**

Engine is MIT and stays MIT. Free forever. The proprietary tier — Cloud, real-device WDA, signed builds — ships separately.

Repo: github.com/SyncTek-LLC/specterqa-ios
Install: `pip install specterqa-ios`
