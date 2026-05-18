# SpecterQA — Press Kit

**For:** journalists, podcasters, dev-rel reviewers, conference programmers
**Last updated:** 2026-04-29
**Press contact:** Maurice Carrier — `maurice.carrier@synctek.io`

---

## About SpecterQA

SpecterQA is the MCP-native iOS simulator driver that AI agents and CI use to gate iOS PRs on real-pixel, real-input behavior. It does the things XCUITest cannot see (WebViews, SwiftUI components without accessibility identifiers, out-of-process Safari sheets) and the things Maestro cannot deeply touch (real UITouch HID injection on iOS 26, sub-millisecond simulator coordination, agent-loop ergonomics). One install line — `pip install specterqa-ios` — ships 29 MCP tools, a universal2 native HID helper, and the full record/replay pipeline.

---

## Key facts

1. **MCP tool count:** 29 (lifecycle 3, observe 1, act 5, record/replay 5, logs 1, perf 4, diagnostics 5, robustness 4, version 1).
2. **Test count:** 117 (91 unit, 26 live end-to-end against the in-tree TestKitApp).
3. **Install:** `pip install specterqa-ios`. Universal2 native HID helper ships in the wheel — no separate Xcode target.
4. **Supported platforms:** macOS (Apple Silicon + Intel), Xcode + iOS Simulator. Python ≥ 3.10.
5. **Real-device support:** read-only today (observe + logs + lifecycle). Real-device input via WebDriverAgent ships in v1.1.
6. **License:** MIT. Permanent. The 29 MCP tools, vision-first observe, record/replay, and HID injection are all open and stay open.
7. **Distribution:** PyPI (`specterqa-ios` namespace, currently `1.0.0a1`). Trusted Publisher OIDC — no token in the publish path.
8. **Founder:** Maurice Carrier. Army veteran (82nd Airborne, Combat Infantryman Badge), two issued patents (Align Technology AR dental imaging), Computer Science, UNC Chapel Hill.
9. **Company:** SyncTek LLC. SpecterQA is the public product brand; `simdrive` is the internal codename retained in the binary filename and dev branches.
10. **Reference customer:** Example Reader iOS (ExampleOrg), migrated off the legacy XCTest-based driver in 5 days, three feedback rounds all closed.

---

## Logo and brand assets

All marks are hand-coded SVG, in `simdrive/docs/brand/`.

| File | Use |
|---|---|
| `logo-primary.svg` (1200 × 320) | Hero placement, article header, registry listing |
| `logo-mark-only.svg` (200 × 200) | App icon, social avatar, square thumbnails |
| `favicon.svg` (32 × 32) | Browser tabs, ≤32 px contexts |
| `wordmark-bracket.svg` (880 × 220) | CLI banners, monochrome contexts, ASCII-adjacent layouts |

Brand color tokens: Ink `#0A0A0A`, Pixel `#E5E5E5`, Signal `#FF3D2E`. The red is sourced from the Set-of-Mark annotation color the agent already sees in product — non-negotiable. Wordmark sets in geometric monospace (JetBrains Mono / Berkeley Mono / IBM Plex Mono), weight 600 on `Specter`, weight 400 on `QA`. Full guidelines in `simdrive/docs/brand/README.md`.

Do not render the mark with gradients, drop shadows, glows, or 3D effects. Do not animate the tap pin.

---

## Screenshot and demo guidance

If you need visuals, three concrete shots cover the story:

1. **The pixel-pin logo on a dark terminal.** `wordmark-bracket.svg` over a `claude` CLI session. Signals "engineer-to-engineer", not "marketing site."
2. **A real `observe()` response with annotated marks.** Open `examples/observe-with-marks.png` (in the repo) — a Example Reader catalog screen with red Set-of-Mark numbers overlaid on each tap target. The image is the literal object the agent reasons about. This is the most explanatory single visual we have.
3. **A test recording in progress.** Screen capture of a `record_start` → 4-step tap tour → `record_stop` cycle, with the resulting `recording.yaml` open beside it. The before/after of "agent picks pixels" → "deterministic YAML in version control" is the product story in one frame.

A pre-cut 30-second hero GIF lives at `simdrive/docs/brand/hero-30s.gif` — `session_start` → `observe` → `tap_text` against the bundled TestKitApp. Use it where motion is supported.

---

## Approved testimonials

All from Example Reader iOS's published v0.2.0a1 dogfood report, attribution **Maurice Carrier, ExampleOrg**:

> "simdrive 0.2.0a1 is a meaningful step forward and is now the canonical iOS sim driver for Example Reader iOS development, replacing SpecterQA."

> "The single biggest reason SpecterQA was failing — the cliclick path that broke UITextField focus — is fully fixed."

> "Replays are now reliable enough to gate PRs on."

(The Example Reader report predates the rename, so the words "simdrive" and "SpecterQA" appear flipped vs current branding — "simdrive 0.2.0a1" is what now ships as `specterqa-ios 1.0.0a1`. Quote unchanged for fidelity.)

---

## Background story

SpecterQA started as a legacy XCTest-based driver published under the `specterqa-ios` PyPI namespace. Through 16 major versions it stayed inside the same playbook every iOS testing tool sits inside: ask the accessibility tree what is on screen, find the element by label, send a synthetic event through XCUIApplication. That model survived ten years on iOS. It started failing on iOS 26 — SwiftUI components without explicit identifiers, WebViews that the AX tree cannot see, UITextField focus that the cliclick path lost on the first keystroke. By v15.2, in three Example Reader dogfood sessions, the runner died three times on `XCUIElementQuery[label]` ambiguous-match `NSException`. The accessibility-tree selector layer was doing negative work for vision-capable agents.

The pivot was vision-first. An LLM that can see pixels does not need a parallel symbol tree — it needs a faithful representation of what is on the screen and a reliable way to act on it. v16.0.0 deleted the accessibility-tree selector layer entirely and replaced it with two primitives: `ios_observe` (return a screenshot plus the small set of elements the developer marked accessible) and `ios_act` (a single coordinate-primary action verb). Same shape as Anthropic Computer Use, OpenAI Operator, claude-in-chrome — applied to iOS.

The Example Reader dogfood validated the bet. In April 2026, Maurice ran a 5-day cutover from the legacy `specterqa-ios 15.x` driver to `simdrive 0.2.0a1`. The flagship test was UITextField focus on iOS 26 — the failure mode that had been blocking the team for months. With real UITouch via `SimDeviceLegacyHIDClient` + `IndigoMessage` (the private CoreSimulator HID path), keystrokes landed and the field accepted focus. Replays of a four-step tab-bar tour ran with SSIM 0.999 per step, zero drift. Three feedback rounds, all closed within a day each.

The agentic-first frame followed naturally. A driver that an LLM can call directly does not need a marketing site, a sales motion, or a developer-relations pitch deck. It needs to be in the registries that agents and humans-with-agents already reach for: the Anthropic MCP registry, `modelcontextprotocol/servers`, Smithery, Cline, Cursor, the next training corpus. The bet is that AI-driven iOS testing becomes the default in 18 months, and the first MCP-native tool with real receipts wins category definition. SpecterQA (under its `simdrive` codename) shipped 29 tools, 117 tests, and one paying-attention reference customer in eight weeks. The press kit you are reading exists because the next stretch is distribution, and distribution is people.

---

## Press contact

**Maurice Carrier**
Founder, SyncTek LLC
`maurice.carrier@synctek.io`

For interviews, demos, or asset variants, email is the right channel. Response within 24 hours, weekdays Eastern Time.
