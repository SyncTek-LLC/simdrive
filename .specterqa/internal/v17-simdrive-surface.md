# simdrive — v0.1.0 MCP Surface

> **Hand your simulator to your agent.**
>
> Claude-native iOS simulator driver. Self-guided. Recordable. Replayable.

## North Star

A Claude session opens an MCP connection, says "test the login flow on iPhone 17 Pro," and the agent drives a real iOS simulator with vision + clicks + keyboard. No XCTest. No accessibility tree. No selectors. Just **observe → act → repeat**.

Target: 1000 paying users on Anthropic's Claude. Open Core (MIT base + private trial driver).

## Architecture

```
┌──────────────────────────┐
│  Claude (or any MCP host)│
└────────────┬─────────────┘
             │ MCP (stdio)
             ▼
┌──────────────────────────┐
│  simdrive MCP server     │  ← this package
│  (Python 3.11+)          │
└────────────┬─────────────┘
             │
   ┌─────────┼──────────────┐
   ▼         ▼              ▼
 simctl   AppleScript    cliclick
(screenshot, (window     (mouse +
 logs,        bounds,     keyboard)
 boot,        activate)
 install)
             │
             ▼
       iOS Simulator
        (Apple's)
```

**No** XCTest runner. **No** Swift package. **No** accessibility tree querying. **No** HTTP daemon. Vision-first, with fast OS-native primitives where they exist (screenshot, logs, app install) and synthetic input where they don't (cliclick).

## MCP Tool Surface (12 tools)

### Lifecycle

**`session_start(device, os_version=None, app_bundle_id=None)`**
Boot sim if needed, optionally launch an app, return session id + device + window bounds.

**`session_end(session_id)`**
Optional. Sim stays booted by default; only kills app + clears state. Leaves sim alive for next call.

**`session_status(session_id=None)`**
Returns `{state, sim_uuid, window_bounds, current_app, last_action_at}`. State ∈ `idle|active|degraded`.

### Observe

**`observe(session_id, capture_logs=False, log_lines=50)`**
Returns `{screenshot_path, screenshot_size_pixels, device_size_points, captured_at, recent_logs?}`. Screenshot saved to a tempdir; agent reads via MCP image-block on second call. Default 50-line log tail when requested.

### Act

**`tap(session_id, x, y)`**
Click at logical points (0–device_w, 0–device_h). Translates points → macOS coords using AppleScript window bounds, activates Simulator, dispatches via cliclick.

**`swipe(session_id, x1, y1, x2, y2, duration_ms=300)`**
Drag from (x1,y1) → (x2,y2). cliclick `dd` + `du` with intermediate `m` moves to control duration.

**`type_text(session_id, text)`**
Keyboard input. cliclick `t:`. Assumes the focused field accepts plain text; agent is responsible for tapping the field first.

**`press_key(session_id, key)`**
Hardware buttons + special keys. Supported: `home`, `lock`, `volume_up`, `volume_down`, `siri`, `screenshot` (sim hotkeys via `xcrun simctl io ... key` and AppleScript menu items where simctl gaps).

### Recording / Replay

**`record_start(session_id, name)`**
Begin capturing every act-tool call (tap/swipe/type/press_key) plus an `observe` snapshot before each step into `~/.simdrive/recordings/{name}.yaml`.

**`record_stop(session_id)`**
Finalize the YAML; return path + step count.

**`replay(name, on_drift="halt")`**
Re-execute a recording. Before each step, screenshot is compared (SSIM) to the recorded screenshot; if SSIM < 0.85 the action either halts (`halt`), warns (`warn`), or proceeds (`force`). Returns per-step pass/fail.

### Utility

**`logs(session_id, lines=200, predicate=None)`**
`xcrun simctl spawn booted log stream` tail. `predicate` is an NSPredicate string filter.

## Recording schema (YAML)

```yaml
name: login_flow
created_at: 2026-04-27T20:14:00Z
device: iPhone 17 Pro
os_version: "26.3"
device_size_points: [402, 874]
steps:
  - id: 1
    action: tap
    args: { x: 357, y: 286 }
    pre_screenshot: snapshots/01_pre.png
    post_screenshot: snapshots/01_post.png
    captured_at: 2026-04-27T20:14:01Z
  - id: 2
    action: type_text
    args: { text: "maurice@synctek.io" }
    pre_screenshot: snapshots/02_pre.png
    post_screenshot: snapshots/02_post.png
    captured_at: 2026-04-27T20:14:04Z
```

Self-contained — anyone with the YAML + the snapshot dir can replay.

## What we explicitly are NOT building (v0.1.0)

- ❌ XCTest integration. Apple's framework instability is the reason we exist; we don't depend on it.
- ❌ Accessibility tree / element selectors. Vision is the contract.
- ❌ Real-device support. Simulator only for v0.1. Real-device via `idb`/`devicectl` is a v0.2+ topic.
- ❌ Android. Maybe never; staying focused.
- ❌ A web dashboard. Recordings live as files in your repo.
- ❌ Tier gating beyond a free/license check. The OSS surface is the whole tool.

## Pricing posture (TBD with marketing)

- **Free / OSS**: full MCP surface against any sim. MIT-licensed.
- **Paid**: ??? — possibly hosted recordings, fleet runner, CI integration. Decide later. Ship the OSS first.

## Migration from v16.0.0a3 (specterqa-ios)

Hard break. Different package, different name, different repo *eventually*.
- The `specterqa-ios` PyPI package stays at `16.0.0a3`. No more releases.
- `simdrive` ships fresh as `0.1.0a1`. New install command: `pip install simdrive`.
- Anyone who installed `specterqa-ios` should `pip uninstall specterqa-ios && pip install simdrive`.
- Recordings format is incompatible — that's fine, no one had real recordings.

## Repo strategy

For v0.1.0a1:
- Stay in the existing `specterqa-ios` repo, branch `feat/v17-claude-native`.
- Add a new top-level Python package `simdrive/` next to `src/specterqa/`.
- Set up its own `pyproject.toml` either in a subdir or via the existing one with new entry points (decide during impl).
- Once stable, fork/rename repo to `simdrive` on GitHub and archive `specterqa-ios`.

## Risks

| Risk | Mitigation |
|---|---|
| cliclick requires Simulator window focus before each click | Wrap every act in `osascript -e 'tell application "Simulator" to activate'` (verified: works) |
| Window bounds change if user moves the sim window | Re-query AppleScript bounds on every act-tool call (cheap, ~30ms) |
| simctl `io key` has limited button coverage | Fall back to AppleScript menu commands for missing buttons |
| Multiple sims booted | session_start picks the first booted match; if ambiguous, error and ask agent to specify UDID |
| User's macOS rejects Accessibility permission for cliclick | Detect, surface a structured `permission_required` error pointing at System Settings |
| Native logs are noisy | Default to OFF; agent opts in with `capture_logs=True` |

## Done definition for v0.1.0a1

1. All 12 MCP tools implemented and unit-tested
2. End-to-end live test: agent boots sim, taps Settings, types "WiFi" in search, screenshots show change
3. Record + replay round trip on a 5-step Calendar flow
4. README + quickstart explaining "drop this in .mcp.json and go"
5. Published to PyPI as `simdrive==0.1.0a1`
6. Maurice can `pip install simdrive`, add to .mcp.json, and have a working session in <5 minutes
