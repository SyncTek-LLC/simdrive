# simdrive

> **MCP-native iOS simulator driver. Agent-first: your MCP client (Claude Code, Cline, etc.) drives via 31 tools — simdrive does not bring its own LLM.**

## 30-second quickstart

```bash
pip install --pre simdrive
simdrive trial start --email you@example.com --offline-dev
simdrive   # runs the MCP server on stdio
```

Then in Claude Code (or any sampling-capable MCP client), add to your MCP config:

```json
{
  "mcpServers": {
    "simdrive": { "command": "simdrive" }
  }
}
```

Restart your MCP client. Ask your agent:

```
Take a screenshot of my booted iPhone simulator
```

That's it. No API key required for the MCP flow. No XCTest, no daemon, no selectors.

> **Trial note:** `--offline-dev` issues a 14-day Ed25519-signed local license without contacting any cloud server — safe for sandboxes, CI, and offline development.

## What you get

- **Agent-first, no API key required.** simdrive doesn't make its own LLM call. Your driving agent's credentials, your driving agent's reasoning. simdrive stays pure tools. `run_journey` delegates back to the connected MCP client via MCP sampling — no `ANTHROPIC_API_KEY` needed.
- **Vision-first observation.** Every `observe` returns a screenshot plus an annotated copy with numbered set-of-marks. Your agent picks a mark number and taps it — no mental reconstruction from accessibility JSON.
- **Recording + replay round-trip.** `record_start` → drive naturally → `record_stop` writes a self-contained YAML+PNG bundle. `replay` re-runs it drift-aware (SSIM advisory; `structural_checks` are the regression gate — see [Known limitations](#known-limitations--workarounds)).
- **Real-device support.** `observe`, `logs`, and app lifecycle work against paired iPhones/iPads via `session_start({target: "device"})`. Touch input routes through WebDriverAgent (WDA) on the roadmap.
- **31 documented tools** covering lifecycle, observation, input, recording/replay, logs, performance, diagnostics, and robustness.

## Why agent-first matters

You stay in your editor. Your agent drives the sim in the background — taps don't steal focus, your keyboard doesn't get hijacked.

Automating an iOS simulator from inside an LLM session has historically required:
- A Swift XCTest runner that breaks every Xcode release
- An accessibility tree your agent has to mentally reconstruct from JSON dumps
- Bespoke selectors (`label:"Sign in"`) that drift with every UI change
- Watchdogs killing your runner mid-test

simdrive replaces all of that with: **screenshot in, click out.** Your agent already understands screenshots — the LLM is the selector engine.

Crucially: **simdrive does not call an LLM itself.** When `run_journey` needs reasoning, it delegates back to your MCP client via MCP sampling. You supply the model and the credentials — simdrive supplies the tools.

## Install

```bash
pip install --pre simdrive
```

Requirements:
- macOS with Xcode + iOS Simulator (for native HID input)
- A booted simulator — simdrive will use a running one or boot one for you

simdrive runs in the background by default — taps and keystrokes go straight to the simulator without raising its window or stealing your keyboard focus. Verify via `session_status` (`mode: "background"`).

## Wire into your MCP client

Add to your `.mcp.json` (Claude Code, Cline, or any MCP-capable client):

```json
{
  "mcpServers": {
    "simdrive": { "command": "simdrive" }
  }
}
```

Restart your client. The 31 simdrive MCP tools are now available.

## Quickstart interaction

```
You: open Settings on iPhone 17 Pro and turn on Airplane Mode.

Agent (using simdrive):
  → session_start({device: "iPhone 17 Pro", app_bundle_id: "com.apple.Preferences"})
  → observe()                              # screenshot + annotated copy with numbered marks
  → tap({text: "Airplane Mode"})           # by visible text
  → observe()                              # sees the toggle
  → tap({mark: 12})                        # by mark number from the annotation
  → observe()                              # confirms it's green
```

You can also `tap({x, y})` for specific pixel coords (great for replay) or `tap({stable_id: "abc123"})` for hash-stable element resolution across observes:

| Form | Use it for |
|------|------------|
| `{text: "..."}` | Buttons, labels, anything with visible text |
| `{mark: N}` | When the agent has just looked at the annotated screenshot |
| `{stable_id: "..."}` | Replay-safe: survives mark reshuffling between observes |
| `{x, y}` | Deterministic replays, icons without text |

That's the whole loop. No selectors. No waits. No XCTest.

## Tool surface (31 MCP tools)

| Group | Tools |
|-------|-------|
| Lifecycle (3) | `session_start`, `session_end`, `session_status` |
| Observe (1) | `observe` |
| Act (5) | `tap`, `swipe`, `type_text`, `press_key`, `clear_field` |
| Record/Replay (5) | `record_start`, `record_stop`, `replay`, `list_replays`, `validate_replay` |
| Logs (1) | `logs` |
| Performance (4) | `perf`, `perf_baseline`, `perf_compare`, `memory` |
| Diagnostics (5) | `doctor`, `app_state`, `apps`, `crashes`, `list_devices` |
| Robustness (4) | `dismiss_first_launch_alerts`, `pre_grant_permissions`, `set_appearance`, `dismiss_sheet` |
| Version (1) | `version` |
| Journey runner (2) | `run_journey` (MCP sampling), `version` |

Coordinates are always in **screenshot pixel space** — same pixels the agent sees in the most recent `observe`.

## Recording + replay

```python
record_start({name: "checkout-flow"})
  ... agent does the flow naturally, calling tap/swipe/type_text ...
record_stop()  # writes ~/.simdrive/recordings/checkout-flow/recording.yaml
```

Later:

```python
replay({name: "checkout-flow", on_drift: "halt"})
```

Each step is gated on visual similarity: if the live screen has drifted from the recorded pre-screenshot, the replay halts (`halt`), warns and continues (`warn`), or proceeds blind (`force`). The recording is a self-contained YAML+PNG bundle you can commit to your repo.

> **Important:** SSIM threshold is advisory — structural assertions are the actual regression gate. See [Known limitations](#known-limitations--workarounds) for details.

## Testing

```bash
pip install simdrive[dev]
pytest                          # unit tests, no sim required
pytest -m live                  # live tests against TestKitApp
```

Live tests boot a fresh TestKitApp session per test and exercise every tool: tap by text/mark/coords, type into focused fields, swipe-to-scroll, alert dismissal, record + replay with drift detection.

## Known limitations + workarounds

### `type_text` first-character drop (HID timing)

The first character occasionally drops when typing into a fresh text field (e.g. `simdrive` typed → `Smdrive`). Cause: HID injection beats the field's keyboard-focus settle.

**Workaround:** pass `tap_first=True` to `type_text`, or call `tap` on the target field immediately before typing. The keyboard focus will settle, then injection lands cleanly.

```python
# Safe pattern for any text field
type_text({text: "simdrive", tap_first: True})
```

### SSIM threshold is advisory; `structural_checks` is the regression gate

Recordings store an SSIM threshold (default 0.85). Replay drift below the SSIM threshold is **reported but does NOT fail a step** — the journey YAML's `structural_checks` (element presence, content assertions) are the actual regression gate.

**Why:** OPDS content, time-of-day clocks, library-list ordering, and appearance changes all shift pixels without changing app behavior. SSIM was designed as a visual decoration signal; structural assertions are what actually catch regressions. Don't chase pixel drift as if it were a behavioral regression — check `struct-check` in replay output instead.

### `dismiss_sheet` covers system sheets only

`dismiss_sheet` swipes down on system-presented modal sheets (`UIPresentationController`-backed). It does **not** dismiss SwiftUI half-sheets (`.sheet` modifier with `.presentationDetents([.medium])`) — those use a different presentation backend.

**Workaround:** for SwiftUI half-sheets, use `swipe` from a point near the top of the sheet's drag handle to a point well below — the sheet's gesture recognizer drives the dismissal. Or tap the sheet's explicit close button if one exists.

```python
# SwiftUI half-sheet: swipe from drag handle to below the sheet
swipe({from_x: 390, from_y: 300, to_x: 390, to_y: 800})
```

### `set_appearance` may need an app respring

`set_appearance` (`light` / `dark`) tells the simulator to switch, but in-flight UI may not redraw until the app respringboards. Most apps observe `traitCollectionDidChange` correctly; some apps with custom theme handling cache colors at launch.

**Workaround:** if the appearance change doesn't propagate, call `session_end` then `session_start` to relaunch the app, or test against a fresh launch.

```python
set_appearance({appearance: "dark"})
# If the app doesn't respond:
session_end()
session_start({...})   # fresh launch sees the correct appearance
```

### Additional limitations

For Dynamic Island modals, xctrace deep profiling, MFA/2FA codes, background-mode keyboard visibility, and real-device input scope — see [`docs/LIMITATIONS.md`](docs/LIMITATIONS.md).

## Migration from `specterqa-ios`

If you're arriving from a `specterqa-ios` link or a 16.x install, see [`docs/MIGRATION.md`](docs/MIGRATION.md).

## What this isn't

- **Not** a CI replacement (yet). Designed for interactive agent sessions; CI integration is a follow-up.
- **Not** a fork of XCTest. simdrive deliberately avoids Apple's testing stack to stay durable across Xcode releases.
- **Not** a managed SaaS for running iOS tests. simdrive is BYOK (bring your own keys) — your agent, your credentials, your simulator.

## License

[Elastic License 2.0](LICENSE). Free for personal/internal use. Prohibits offering simdrive as a competing managed service. Built by [SyncTek](https://synctek.io).
