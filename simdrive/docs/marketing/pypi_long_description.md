# SpecterQA

**MCP-native iOS simulator driver. Hand your iOS simulator to your agent.**

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

Your agent calls `observe`, gets back a screenshot plus an annotated copy with numbered red boxes drawn over every detected text region, picks a target by `mark`, `stable_id`, `text`, or pixel coords — and SpecterQA dispatches a real `UITouch` through `CoreSimulator`'s HID port. No XCTest. No accessibility-tree query. No selectors. The vision-capable model is the selector engine; SpecterQA is the input dispatcher.

The bundled `universal2` native helper drives the simulator through `SimDeviceLegacyHIDClient` + `IndigoMessage` — the path that triggers `UITextField` first-responder on iOS 26, where synthetic mouse events stopped working.

## 29 MCP tools

| Group | Tools |
|---|---|
| Lifecycle (3) | `session_start`, `session_end`, `session_status` |
| Observe (1) | `observe` |
| Act (5) | `tap`, `swipe`, `type_text`, `press_key`, `clear_field` |
| Record/Replay (5) | `record_start`, `record_stop`, `replay`, `list_replays`, `validate_replay` |
| Logs (1) | `logs` |
| Performance (4) | `perf`, `perf_baseline`, `perf_compare`, `memory` |
| Diagnostics (5) | `doctor`, `app_state`, `apps`, `crashes`, `list_devices` |
| Robustness (4) | `dismiss_first_launch_alerts`, `pre_grant_permissions`, `set_appearance`, `dismiss_sheet` |
| Version (1) | `version` |

## Requirements

- macOS with Xcode + iOS Simulator (for the native HID helper)
- Python ≥ 3.10
- A booted simulator. SpecterQA uses a running one or boots one for you.

## What's stable

- Vision-first OCR observe with `stable_id` per mark
- Real `UITouch` HID injection on iOS 26 (`UITextField` focus works)
- Record + replay with SSIM drift gating and `mask_regions` for dynamic chrome
- Performance snapshots without an XCTest bridge

## Honest tradeoffs

- Real-device input is read-only in v1.0. `tap`/`swipe`/`type_text`/`press_key` against a real device raise `device_input_unavailable`; full input ships in v1.1 via WebDriverAgent.
- macOS-only. `CoreSimulator` doesn't exist elsewhere.
- Not an XCTest replacement for `accessibility_audit` or `webview_elements`. Run XCTest in parallel for those.

## License

MIT. Built by [SyncTek](https://synctek.io). Source: [`SyncTek-LLC/specterqa-ios`](https://github.com/SyncTek-LLC/specterqa-ios).

The console scripts `specterqa-ios` and the legacy alias `simdrive` invoke the same MCP server.
