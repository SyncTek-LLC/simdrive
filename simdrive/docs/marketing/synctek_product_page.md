# SpecterQA

**MCP-native iOS simulator driver. Your agent looks at a screenshot, picks a pixel, and SpecterQA taps it.**

```bash
pip install specterqa-ios
```

---

## Overview

SpecterQA is the runtime layer between an AI agent and an iOS simulator. The agent calls `observe`, gets back a screenshot plus an annotated copy with numbered red boxes over every detected text region, decides where to act, and SpecterQA dispatches a real `UITouch` through `CoreSimulator`'s HID port. There is no XCTest dependency, no accessibility-tree query, no Swift runner that breaks on every Xcode release. The vision-capable model is the selector engine; SpecterQA is the input dispatcher.

Today, 29 MCP tools cover the full agent loop: simulator lifecycle, vision-first observe, tap/swipe/type/press-key, record-and-replay with SSIM drift gating, performance snapshots, crash retrieval, and environment diagnostics. The engine is shipped as a single `pip install` — a `universal2` native helper for HID input is bundled in the wheel, so there is no separate Xcode plugin, Swift target, or daemon to maintain.

## Features

| Capability | What it does |
|---|---|
| **Vision-first observe** | Returns the raw screenshot, an annotated PNG with numbered red boxes, and a `marks[]` array of `{id, bbox, center, text, stable_id, stable_id_loose, confidence_band}`. The agent picks a target by `mark`, `stable_id`, `text`, or pixel coords. |
| **Real `UITouch` HID injection** | A bundled native helper drives the simulator through `SimDeviceLegacyHIDClient` + `IndigoMessage`. Triggers `UITextField` first-responder on iOS 26 — the regression that killed XCUITest workflows. |
| **`stable_id` replay** | A 12-char hex hash of `(text + 20px-bucketed bbox)`. Survives mark-id reshuffling between observes. Recordings serialize it alongside pixel coords and prefer it at replay time. |
| **SSIM drift gating with masks** | Per-step pixel-similarity check against the recorded pre-screenshot. `mask_regions` blank dynamic chrome (status-bar clock, etc.) before the compute. Configurable `halt`/`warn`/`force` policy. |
| **Performance snapshots** | `perf` returns CPU%, memory RSS, thread count via `simctl` + `ps` — no XCTest bridge. `perf_baseline` and `perf_compare` give you per-axis deltas with severity grading. |
| **Diagnostics** | `doctor` checks Xcode CLT, runtimes, booted devices, native HID helper presence. `crashes` retrieves `.ips` reports filtered by session-start time and bundle id. |
| **Background dispatch** | Taps and keystrokes go to the simulator without raising its window. Your foreground app keeps focus while a session runs. |

## Quickstart

1. `pip install specterqa-ios`
2. Open Simulator.app once and boot an `iPhone 17 Pro` on iOS 26.3. SpecterQA will use a running sim or boot one for you.
3. Add to `.mcp.json`:
   ```json
   {
     "mcpServers": {
       "specterqa-ios": { "command": "specterqa-ios" }
     }
   }
   ```
4. Restart Claude Code. Ask your agent: *open Settings on iPhone 17 Pro and turn on Airplane Mode.*

The agent calls `session_start` → `observe` → `tap({text: "Airplane Mode"})` → `observe` to confirm. No code, no selectors, no test runner.

## Cost transparency

The engine is **MIT-licensed and free, forever.** All 29 MCP tools, the vision-first observe, the HID injection helper, record-and-replay, SSIM masking, performance snapshots — permanently open. No usage caps, no key, no telemetry beacon.

The proprietary tier ships under a separate package. **Pro** ($49/mo/seat) adds hosted replay archive, SSIM-trend dashboards, multi-sim parallelism license, priority support, and signed builds. **Team** ($249/mo for 5 seats) adds shared journey corpus, CI integration via the productized `--specterqa` PR-gate pattern, Slack/Linear hooks, and **real-device input via WebDriverAgent**. **Enterprise** is sales-led at $5K-$15K/yr for SOC 2, RBAC, SSO, audit logs, and on-prem replay storage.

The free engine is the product. The paid tier is the convenience layer around it.

## Honest tradeoffs

- **Real-device input is read-only in v1.0.** `list_devices`, `observe`, `logs`, and app lifecycle work against a paired iPhone or iPad. `tap`, `swipe`, `type_text`, and `press_key` raise `device_input_unavailable`. Real-device input ships in v1.1 via WebDriverAgent and is gated to the Team tier.
- **macOS-only.** The HID helper talks to `CoreSimulator`, which only exists on macOS. There is no Linux or Windows path planned.
- **Not an XCTest replacement.** `accessibility_audit` and `webview_elements` require the XCTest bridge, which SpecterQA deliberately avoids. If you need an a11y conformance audit, run XCTest in parallel — SpecterQA is the agent-driven layer, not the compliance layer.
- **`perf` snapshots are `simctl` + `ps` based.** Accurate enough for regression gating; not a substitute for Instruments when you need allocation traces.
- **OCR confidence is dictionary-gated, not perfect.** Stylized cover text on stylized icon glyphs OCRs at low confidence. The `confidence_band` field flags this, but you should still pair text matches with a follow-up `observe` for verification.

## Documentation

- **[README](https://github.com/SyncTek-LLC/specterqa-ios)** — install, wire-up, the full tool table, the agent loop
- **`docs/LIMITATIONS.md`** — Dynamic Island modals, MFA hard-wall, `xctrace` ceiling, debounce-window rule
- **`docs/BEST_PRACTICES.md`** — HID + debounce-window guidance, text-resolution rapid-cycle fallback, SSIM mask conventions
- **`CHANGELOG.md`** — every change, with the why, in voice
- **`docs/REAL_DEVICE_FEASIBILITY.md`** — the v1.1 WDA roadmap

## Support

- **GitHub issues** — bugs, feature requests, dogfood reports — [`SyncTek-LLC/specterqa-ios`](https://github.com/SyncTek-LLC/specterqa-ios)
- **Pro/Team/Enterprise** — [contact@synctek.io](mailto:contact@synctek.io) for paid tier onboarding
- **Security** — security disclosures via [security@synctek.io](mailto:security@synctek.io)

## Related posts

- [Why we replaced XCTest with screenshots](/blog/why-we-built-specterqa) — the founder essay on the iOS 26 `UITextField` regression and the agent-first pivot
- [Case study: Example Reader iOS migrated off the predecessor in 5 days](/blog/case-study-example) — the reader + OAuth coverage that XCTest couldn't reach
- [Show HN launch post](/blog/show-hn-specterqa) — the lede, the mechanic, the receipts
