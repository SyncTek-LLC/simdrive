# Migrating from `specterqa-ios` to `simdrive`

`simdrive` is the rewrite. Different package name, simpler MCP surface, real UITouch input that focuses fields. The migration is a 5-minute job for most projects.

## Install change

```bash
# Old
pip uninstall specterqa-ios

# New
pip install simdrive
```

## `.mcp.json` change

```diff
- { "mcpServers": { "specterqa-ios": { "command": "specterqa-ios-mcp" } } }
+ { "mcpServers": { "simdrive": { "command": "simdrive" } } }
```

## Tool name + shape changes

simdrive's surface is smaller and consolidates several specterqa tools into form-aware ones (`tap` accepts coords / mark id / text instead of separate `ios_tap_by_*` tools).

| specterqa-ios | simdrive | Notes |
|---|---|---|
| `ios_session_start` | `session_start` | Argument names same |
| `ios_session_status` | `session_status` | New `mode` field reports `background`/`foreground` |
| `ios_observe` | `observe` | Now returns `annotated_path` (numbered SoM screenshot) + `marks` list |
| `ios_tap` (x, y) | `tap({x, y})` | Or `tap({mark: N})` / `tap({text: "..."})` |
| `ios_act` (tap subset) | `tap` | Hybrid form replaces multiple act variants |
| `ios_swipe` | `swipe` | Same `{x1,y1,x2,y2}` form, plus `{from, to}` with target dicts |
| `ios_type` | `type_text` | Optional `tap_first` to focus + type in one call |
| `ios_press_button` | `press_key` | `home`, `lock`, `siri`, `return`, `tab`, `escape`, arrow keys |
| `ios_record_start/stop` | `record_start` / `record_stop` | YAML format slightly different — recordings are not interchangeable |
| `ios_replay` | `replay` | New `on_drift: halt|warn|force` parameter |
| `ios_logs` | `logs` | Same NSPredicate filter |
| `ios_screenshot` | (use `observe`) | observe always returns a screenshot path |
| `ios_elements` | (use `observe.marks`) | OCR-based, no AX-tree dependency |
| `ios_wait_for_element` | (use `observe` + `marks`) | Caller polls; no implicit wait primitive |
| `ios_dismiss_first_launch_alerts` | (use `tap({text: "Don't Allow"})` etc.) | Vision-driven; no app-specific knob |
| `ios_action_with_logs` | (use `act tool` + `logs` separately) | Composability over magic |

## Behavioral differences

1. **Coordinates are screenshot pixels, not points.** Last observe's `screenshot_size_pixels` is the canonical reference. `tap({mark: N})` and `tap({text: "..."})` are usually preferred — they don't require pixel math.

2. **Field focus actually works.** specterqa's cliclick path didn't focus UITextFields on iOS 26. simdrive's HID helper does. If you previously worked around this with paste hacks or hardware-keyboard config, you can drop that.

3. **No XCTest runner, no daemons.** specterqa launched a Swift HTTP daemon in the simulator. simdrive talks to CoreSimulator's HID port directly. No port conflicts, no orphan processes, no "runner crashed" recovery dance.

4. **Background by default.** Taps don't steal your editor's focus. `session_status.mode` reports `"background"` when working correctly.

5. **Errors are structured.** Tool failures now return:
   ```json
   {"ok": false, "error": {"code": "target_not_found", "message": "...", "details": {...}}}
   ```
   Switch on `error.code` instead of parsing prose.

## Recordings are NOT compatible

specterqa recordings used a different YAML schema and screenshot storage layout. To migrate a recording, re-record it on simdrive with the same flow.

## Things specterqa had that simdrive does not (yet)

- **Real-device support** (specterqa drove physical iPhones via WebDriverAgent). simdrive 0.1 is simulator-only. v0.2 target.
- **BrowserStack remote-device adapter** — same status, v0.2 target.
- **Tier-gated MCP tools** (specterqa had a license-key gate). simdrive is MIT, full surface always available.

## Quick smoke test

```python
# From a Python REPL after install
from simdrive import server
sid = server.tool_session_start({"app_bundle_id": "com.apple.Preferences"})["session_id"]
obs = server.tool_observe({"session_id": sid})
print([m["text"] for m in obs["marks"][:5]])  # should show iOS Settings rows
server.tool_tap({"session_id": sid, "text": "Wi-Fi"})  # field-focus + tab nav
server.tool_session_end({"session_id": sid})
```

If that runs end-to-end, you're migrated.
