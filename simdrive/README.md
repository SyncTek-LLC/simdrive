# SpecterQA for iOS

> **Hand your iOS simulator to your agent.**

SpecterQA for iOS (simdrive internally) is an MCP server for driving iOS simulators. Vision-first. No XCTest, no accessibility-tree query, no daemons. Your agent looks at a screenshot, picks a pixel, and SpecterQA taps it.

## Why

You stay in your editor. Your agent drives the sim in the background. Taps don't steal focus, your keyboard doesn't get hijacked.

Automating an iOS simulator from inside an LLM session has historically required:
- A Swift XCTest runner that breaks every Xcode release
- An accessibility tree your agent has to mentally reconstruct from JSON dumps
- Bespoke selectors (`label:"Sign in"`) that drift with every UI change
- Watchdogs killing your runner mid-test

SpecterQA for iOS replaces all of that with: **screenshot in, click out**. Your agent already understands screenshots — the LLM is the selector engine.

## Install

```bash
pip install specterqa-ios
```

Requirements:
- macOS with Xcode + iOS Simulator (for native HID input)
- A booted simulator. SpecterQA will use a running one or boot one for you.

SpecterQA runs in the background by default — taps and keystrokes go straight to the simulator without raising its window or stealing your keyboard focus. Verify via `session_status` (`mode: "background"`).

## Wire into Claude

Add to your `.mcp.json`:

```json
{
  "mcpServers": {
    "specterqa-ios": { "command": "specterqa-ios" }
  }
}
```

Restart Claude Code. The 29 SpecterQA MCP tools are now available.

> Existing `.mcp.json` configs with `command: simdrive` keep working — both console scripts (`specterqa-ios` and `simdrive`) point at the same server.

## Quickstart

```
You: open Settings on iPhone 17 Pro and turn on Airplane Mode.

Claude (using SpecterQA):
  → session_start({device: "iPhone 17 Pro", app_bundle_id: "com.apple.Preferences"})
  → observe()                              # screenshot + annotated copy with numbered marks
  → tap({text: "Airplane Mode"})           # by visible text
  → observe()                              # sees the toggle
  → tap({mark: 12})                        # by mark number from the annotation
  → observe()                              # confirms it's green
```

You can also `tap({x, y})` if you have specific pixel coords (great for replay). Pick whichever is lowest-friction per call:

| Form | Use it for |
|------|------------|
| `{text: "..."}` | Buttons, labels, anything with visible text |
| `{mark: N}` | When the agent has just looked at the annotated screenshot |
| `{x, y}` | Replays, deterministic UI tests, icons without text |

That's the whole loop. No selectors. No waits. No XCTest.

## Tool surface (29 MCP tools)

| Group | Tools |
|------|---------|
| Lifecycle (3) | `session_start`, `session_end`, `session_status` |
| Observe (1) | `observe` |
| Act (5) | `tap`, `swipe`, `type_text`, `press_key`, `clear_field` |
| Record/Replay (5) | `record_start`, `record_stop`, `replay`, `list_replays`, `validate_replay` |
| Logs (1) | `logs` |
| Performance (4) | `perf`, `perf_baseline`, `perf_compare`, `memory` |
| Diagnostics (5) | `doctor`, `app_state`, `apps`, `crashes`, `list_devices` |
| Robustness (4) | `dismiss_first_launch_alerts`, `pre_grant_permissions`, `set_appearance`, `dismiss_sheet` |
| Version (1) | `version` |

Coordinates are always in **screenshot pixel space** — same pixels the agent sees in the most recent `observe`.

## Recording + replay

```
record_start({name: "checkout-flow"})
  ... agent does the flow naturally, calling tap/swipe/type_text ...
record_stop()  # writes ~/.simdrive/recordings/checkout-flow/recording.yaml
```

Later:

```
replay({name: "checkout-flow", on_drift: "halt"})
```

Each step is gated on visual similarity: if the live screen has drifted from the recorded pre-screenshot, the replay halts (`halt`), warns and continues (`warn`), or proceeds blind (`force`). The recording is a self-contained YAML+PNG bundle you can commit to your repo.

## Testing

```bash
pip install specterqa-ios[dev]
pytest                          # 91 unit tests, no sim required
pytest -m live                  # 26 live tests against TestKitApp
```

Live tests boot a fresh TestKitApp session per test and exercise every tool: tap by text/mark/coords, type into focused fields, swipe-to-scroll, alert-while-focused dismissal (the iOS 26 case that defeated v15), record + replay with drift detection.

## What this isn't

- **Not** a real-device tool. v0.1 is simulator-only. Real device support via `idb`/`devicectl` is on the roadmap.
- **Not** a CI replacement (yet). Designed for interactive Claude sessions; CI integration is a follow-up.
- **Not** a fork of XCTest. We deliberately avoid Apple's testing stack to stay durable across Xcode releases.

## License

MIT. Built by [SyncTek](https://synctek.io).
