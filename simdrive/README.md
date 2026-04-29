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

You'll also need:
- macOS with Xcode + iOS Simulator
- Accessibility permission for whichever app launches `simdrive` (Terminal, iTerm, your editor, etc.) — System Settings → Privacy & Security → Accessibility. **Restart the host after granting** — TCC permissions only refresh on process restart.

simdrive runs in the background by default and reports its current operating mode via `session_status`. If your environment doesn't support background dispatch, install `cliclick` (`brew install cliclick`) for the fallback path — note that fallback mode brings Simulator to the foreground on each action.

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
  → observe()                                    # sees Settings home
  → tap({x: 600, y: 580})                        # taps "Airplane Mode" row
  → observe()                                    # sees the toggle
  → tap({x: 1100, y: 220})                       # flips toggle
  → observe()                                    # confirms it's green
```

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

Each step is gated on visual similarity: if the live screen has drifted from the recorded pre-screenshot (SSIM < 0.85), the replay halts (`halt`), warns and continues (`warn`), or proceeds blind (`force`). The recording is a self-contained YAML+PNG bundle you can commit to your repo.

## What this isn't

- **Not** a real-device tool. v0.1 is simulator-only. Real device support via `idb`/`devicectl` is on the roadmap.
- **Not** a CI replacement (yet). Designed for interactive Claude sessions; CI integration is a follow-up.
- **Not** a fork of XCTest. We deliberately avoid Apple's testing stack to stay durable across Xcode releases.

## License

MIT. Built by [SyncTek](https://synctek.io).
