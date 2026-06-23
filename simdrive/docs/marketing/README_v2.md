<p align="center">
  <img src="../brand/logo-primary.svg" alt="SpecterQA" width="480"/>
</p>

# SpecterQA

> **Hand your iOS simulator to your agent.**

```bash
pip install specterqa-ios
```

```json
{
  "mcpServers": {
    "specterqa-ios": { "command": "specterqa-ios" }
  }
}
```

## What it does

SpecterQA is an MCP-native iOS simulator driver. Your agent calls `observe`, gets back a screenshot plus an annotated copy with numbered red boxes drawn over every detected text region, picks a target, and SpecterQA dispatches a real `UITouch` through `CoreSimulator`'s HID port. No XCTest. No accessibility-tree query. No selectors that drift with every UI change.

The mechanic is `screenshot in, click out`. The LLM is the selector engine — it already understands a screen image — so SpecterQA's job is to turn `tap text="Sign in"` or `tap stable_id="a229e82e3f00"` into pixel coordinates and dispatch the touch where the agent points. Taps run in the background; your foreground app keeps focus, and your keyboard isn't hijacked while a session runs.

A typical agent loop looks like this:

```
session_start({device: "iPhone 17 Pro", app_bundle_id: "com.apple.Preferences"})
observe()                               # raw + annotated PNG, marks[] with stable_id
tap({text: "Airplane Mode"})            # by visible text
observe()                               # confirms the toggle is now green
type_text({tap_first: {stable_id: "850877875550"}, text: "harlem"})
                                        # focus + type in one verb
```

That's the whole API surface for navigation. Under the hood, every action flows through the same vision-first path — observe, resolve, dispatch.

## Why now

Vision-capable models removed the selector bottleneck. For a decade, automating an iOS app meant teaching a machine to find a button: accessibility identifiers, XPath against an XML tree, fragile label matching that breaks on the first localization. Models like Claude already look at screenshots. The selector layer is now the model. SpecterQA is the runtime that picks up where the screenshot ends — it ships the screen to the agent, and it dispatches the touch back to the simulator.

## Tool surface

29 MCP tools, grouped by responsibility.

| Group | Count | Tools |
|-------|------:|-------|
| Lifecycle | 3 | `session_start`, `session_end`, `session_status` |
| Observe | 1 | `observe` |
| Act | 5 | `tap`, `swipe`, `type_text`, `press_key`, `clear_field` |
| Record/Replay | 5 | `record_start`, `record_stop`, `replay`, `list_replays`, `validate_replay` |
| Logs | 1 | `logs` |
| Performance | 4 | `perf`, `perf_baseline`, `perf_compare`, `memory` |
| Diagnostics | 5 | `doctor`, `app_state`, `apps`, `crashes`, `list_devices` |
| Robustness | 4 | `dismiss_first_launch_alerts`, `pre_grant_permissions`, `set_appearance`, `dismiss_sheet` |
| Version | 1 | `version` |

Coordinates are always in screenshot pixel space — the same pixels the agent sees in the most recent `observe`. Targets accept `{x, y}`, `{mark: <id>}` from the latest annotated observe, `{text: "..."}` matched against detected OCR text, or `{stable_id: <hash>}` derived from `(text + bucketed-position)` so a re-shuffled mark index doesn't break the next call.

## What's stable

- **Vision-first OCR observe.** Every `observe()` returns a raw screenshot, an annotated copy with numbered red boxes, and a `marks[]` array carrying `id`, `bbox`, `center`, `text`, `stable_id`, `stable_id_loose`, and a dictionary-gated `confidence_band`. Stylized cover text that used to OCR with false-high confidence ("Sary of the Canadan liothest" at 1.0) is now flagged.
- **Real `UITouch` HID injection on iOS 26.** The bundled native helper drives the simulator through `SimDeviceLegacyHIDClient` + `IndigoMessage` — the private SPI path that triggers `UITextField` first-responder. Synthetic mouse events stopped working on iOS 26; this path works. `type_text` reports `injection_method` and `dispatch_succeeded` so you know the keystrokes landed.
- **`stable_id` replay with SSIM masking.** Recordings serialize `stable_id` and `stable_id_loose` alongside resolved pixel coords. At replay, SpecterQA prefers the stable hash and falls back to pixels only when no mark matches. Per-step SSIM gating with `mask_regions` lets you blank the iOS status-bar clock (or any other dynamic chrome) before the similarity compute, so a same-screen replay doesn't drift into the 0.6s.

## Quickstart

1. **Install.**
   ```bash
   pip install specterqa-ios
   specterqa-ios --version
   ```

2. **Boot a simulator.** Open Simulator.app once, pick `iPhone 17 Pro` (iOS 26.3), let it boot. SpecterQA will use a running sim or boot one for you.

3. **Wire into your agent host.** Drop this into `.mcp.json`:
   ```json
   {
     "mcpServers": {
       "specterqa-ios": { "command": "specterqa-ios" }
     }
   }
   ```
   Restart Claude Code. The 29 tools become available.

4. **First observe.** From the agent (or a Python REPL):
   ```python
   from specterqa_ios import server
   sid = server.call_tool("session_start", {
       "device": "iPhone 17 Pro",
       "app_bundle_id": "com.apple.Preferences",
   })["session_id"]
   obs = server.call_tool("observe", {"session_id": sid})
   # obs["annotated_path"] is a PNG with numbered red boxes over every mark
   # obs["marks"] is the list of {id, bbox, center, text, stable_id, ...}
   ```

5. **First tap.** Pick a mark by visible text or `stable_id`:
   ```python
   server.call_tool("tap", {"session_id": sid, "text": "Airplane Mode"})
   server.call_tool("observe", {"session_id": sid})  # confirm
   ```

That's the whole loop. No selectors, no waits, no XCTest.

## Testimonial

> "SpecterQA is now the canonical iOS sim driver for our development, replacing the predecessor. The single biggest reason it was failing — the path that broke `UITextField` focus — is fully fixed."
>
> — Maurice Carrier, reference customer (a real-world iOS reading app, `com.example.reader`)

## Honest tradeoffs

What SpecterQA does NOT do, today:

- **Real-device input is read-only.** `list_devices`, `observe`, `logs`, and app lifecycle work against a paired iPhone or iPad. `tap`, `swipe`, `type_text`, and `press_key` raise `device_input_unavailable` — full real-device input ships in v1.1 via WebDriverAgent.
- **macOS-only.** The native HID helper is a `universal2` Mach-O binary that talks to `CoreSimulator`. There is no Linux or Windows path because `CoreSimulator` doesn't exist there.
- **Simulator-only for v1.0.** The product is built around the simulator path because that's where 100% of validated dogfood traffic runs today.
- **Not an XCTest replacement for `accessibility_audit` or `webview_elements`.** Those tools require the XCTest bridge, which we deliberately avoid. If you need an a11y conformance audit, run XCTest in parallel.
- **Not a CI runner yet.** Designed for interactive agent sessions. CI integration (a `--specterqa` PR-gate flag, hosted replay archive) lands in the Pro tier.

## Docs and support

- [`LIMITATIONS.md`](../LIMITATIONS.md) — known sharp edges (Dynamic Island modals, `xctrace` ceiling, MFA hard-wall, OCR on stylized covers)
- [`BEST_PRACTICES.md`](../BEST_PRACTICES.md) — HID + debounce-window rule, text-resolution rapid-cycle fallback, SSIM mask conventions
- [`CHANGELOG.md`](../../CHANGELOG.md) — every change, with the why
- [GitHub issues](https://github.com/SyncTek-LLC/specterqa-ios/issues) — bug reports, feature requests
- [`docs/REAL_DEVICE_FEASIBILITY.md`](../REAL_DEVICE_FEASIBILITY.md) — the v1.1 WDA roadmap

## Contributing

PRs welcome. Run `pytest` (91 unit tests, no sim required) and `pytest -m live` (26 live E2E tests against `TestKitApp`) before opening a PR. The test harness exercises every tool: tap by text/mark/coords, type into focused fields, swipe-to-scroll, alert-while-focused dismissal, record + replay with drift detection.

## License

The engine is **MIT**. Use it, fork it, ship it in commercial products. The proprietary tier — Cloud (hosted replay archive, SSIM-trend dashboards, multi-sim parallelism), real-device input via WDA, signed enterprise builds — ships under a separate license in a separate package. The 29 tools described here stay open forever.

---

Built by [SyncTek](https://synctek.io). The console script `specterqa-ios` and the legacy alias `simdrive` invoke the same server.
