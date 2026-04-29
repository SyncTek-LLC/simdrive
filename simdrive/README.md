# simdrive

> **Hand your iOS simulator to your agent.**

Claude-native MCP server for driving iOS simulators. Vision-first. No XCTest, no accessibility-tree query, no daemons. Your agent looks at a screenshot, picks a pixel, and `simdrive` taps it.

## Why

You stay in your editor. Your agent drives the sim in the background. Taps don't steal focus, your keyboard doesn't get hijacked.

Automating an iOS simulator from inside an LLM session has historically required:
- A Swift XCTest runner that breaks every Xcode release
- An accessibility tree your agent has to mentally reconstruct from JSON dumps
- Bespoke selectors (`label:"Sign in"`) that drift with every UI change
- Watchdogs killing your runner mid-test

simdrive replaces all of that with: **screenshot in, click out**. Your agent already understands screenshots — the LLM is the selector engine.

## Install

```bash
pip install simdrive
```

Requirements:
- macOS with Xcode + iOS Simulator (for native HID input)
- A booted simulator. simdrive will use a running one or boot one for you.

simdrive runs in the background by default — taps and keystrokes go straight to the simulator without raising its window or stealing your keyboard focus. Verify via `session_status` (`mode: "background"`).

## Wire into Claude

Add to your `.mcp.json`:

```json
{
  "mcpServers": {
    "simdrive": { "command": "simdrive" }
  }
}
```

Restart Claude Code. The 12 simdrive tools are now available.

## Quickstart

```
You: open Settings on iPhone 17 Pro and turn on Airplane Mode.

Claude (using simdrive):
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

## Tool surface (12 tools)

| Tool | Purpose |
|------|---------|
| `session_start` | Boot/find a sim, optionally launch an app |
| `session_end` | End session (sim stays booted) |
| `session_status` | Inspect active session(s) |
| `observe` | Capture screenshot (returns file path), optional log tail |
| `tap` | Click at screenshot pixel coordinate |
| `swipe` | Drag from (x1,y1)→(x2,y2) |
| `type_text` | Send keyboard input |
| `press_key` | Hardware buttons (home, lock, siri, shake, return, etc.) |
| `record_start` | Begin recording every action |
| `record_stop` | Finalize recording.yaml |
| `replay` | Re-execute a recording with SSIM drift detection |
| `logs` | Tail simulator logs (NSPredicate filterable) |

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
pip install simdrive[dev]
pytest                          # 22 unit tests, no sim required
pytest -m live                  # 26 live tests against TestKitApp
```

Live tests boot a fresh TestKitApp session per test and exercise every tool: tap by text/mark/coords, type into focused fields, swipe-to-scroll, alert-while-focused dismissal (the iOS 26 case that defeated v15), record + replay with drift detection.

## What this isn't

- **Not** a real-device tool. v0.1 is simulator-only. Real device support via `idb`/`devicectl` is on the roadmap.
- **Not** a CI replacement (yet). Designed for interactive Claude sessions; CI integration is a follow-up.
- **Not** a fork of XCTest. We deliberately avoid Apple's testing stack to stay durable across Xcode releases.

## License

MIT. Built by [SyncTek](https://synctek.io).
