# SimDrive

**Reproduce and validate iOS bugs in 60 seconds with Claude.**

SimDrive is the MCP-native iOS automation toolkit your AI agent already knows
how to drive. Hand it a Linear ticket, watch it walk the steps in the
simulator (or a paired real device), and get back a deterministic recording
that replays free in CI forever.

## 60-second bug repro

```text
You (in Cursor / Claude Code):
  "Use simdrive to reproduce Linear ENG-1247 — sign-in fails on iPhone 17 /
   iOS 26.3 with test@example.com."

Claude:
  → session_start({device: "iPhone 17", os_version: "26.3", bundle_id: "com.acme.app"})
  → observe()                              # screenshot + annotated marks
  → tap({text: "Email"})
  → type_text({text: "test@example.com"})
  → tap({text: "Password"})
  → type_text({text: "pw123"})
  → tap({text: "Sign In"})
  → observe()                              # captures error toast
  → record_stop({name: "ENG-1247-repro"})  # YAML+PNG attached to PR
```

After you ship the fix, the same recording replays free in CI — no AI cost
on every run.

## What you get

- **Bug reproduction + validation (hero)** — agent reads the ticket, drives
  the simulator, captures the failure, saves a deterministic recording.
- **Record → replay** — recordings are YAML + PNG bundles that re-run
  identically on every CI build. Zero AI cost on replay.
- **Autonomous test suites** — `run_journey` reads a YAML journey with goals
  and success criteria; SimDrive drives the agent loop and reports
  pass/fail with evidence.
- **Real iOS device support** — WebDriverAgent-backed; one-command
  `simdrive bootstrap-device <udid>` bring-up.
- **Visual regression detection** — SSIM-based pre/post comparison with
  configurable drift handling.
- **Performance baselines + regression comparison** — capture CPU / RSS /
  thread baselines and diff future runs.

## Install + activate

```bash
pip install simdrive
simdrive trial start --email you@example.com
# 14 days full access, then:
simdrive auth <your-license-key>
```

The trial license is Ed25519-signed and machine-locked — it works offline,
in CI sandboxes, and on developer laptops without network. After 14 days,
paid licenses (`simdrive auth …`) unlock the full tool surface.

Requires: macOS, Xcode 15+, Python 3.10+.

## Wire SimDrive into your MCP client

Add to `.claude/mcp.json` (Claude Code), `claude_desktop_config.json` (Claude
Desktop), or your Cursor MCP config:

```json
{
  "mcpServers": {
    "simdrive": { "command": "simdrive" }
  }
}
```

Restart the client. Your agent now has 32 SimDrive tools available.

## Pricing

| Plan | Price | What you get |
|------|-------|--------------|
| **Trial** | Free, 14 days | All Pro features, machine-locked |
| **Pro** | $29 / mo | One seat, all tools, unlimited CI replays |
| **Team** | $99 / seat / mo | Multi-seat, shared recording cloud |
| **Enterprise** | Contact | Self-hosted licensing, SLA, integrations |

Pricing + ROI calculator: <https://simdrive.dev/pricing>

## Minimum-viable session

```python
session_start(bundle_id="com.example.app")
observe()                                  # see initial screen
tap(label="Sign In")                       # tap a labelled control
observe()                                  # verify state
record_stop(session_id="...", name="signin-smoke")   # save replay
session_end(session_id="...")              # clean up
```

## Maestro-compatible YAML

Migrating from Maestro? SimDrive parses the shorthand natively:

```yaml
replay:
  bundle_id: com.example.app
  steps:
    - tapOn: "Sign In"
    - inputText: "user@example.com"
    - assertVisible: "Dashboard"
    - assertNotVisible: "Loading"
    - waitFor: "Feed"
```

## Tool surface (32 MCP tools)

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
| Recordings (2) | `lint_recordings`, `migrate_recording` |
| Journeys (1) | `load_journey` |
| Version (1) | `version` |

Canonical machine-readable list: `simdrive/src/simdrive/server.py::_TOOLS`.

## Real-device support

Drive a paired iPhone or iPad in addition to the simulator:

```bash
export SIMDRIVE_ALLOW_PHYSICAL_DEVICE=1
simdrive bootstrap-device <device-udid>
```

```python
session_start(bundle_id="com.example.app", udid="<device-udid>", target="device")
```

WDA bootstrap on iOS 26.x has some rough edges; the simulator
(`target="simulator"`, default) is the fully supported path.

## Known limitations

See `docs/LIMITATIONS.md` for: `type_text` first-character drop workaround,
SSIM-vs-structural-check semantics, SwiftUI half-sheet dismissal,
appearance-respring caveats, real-device input scope.

## License

Elastic License 2.0 — see `LICENSE`. Free for internal use; prohibits
offering SimDrive as a competing managed service.

Built by [SyncTek](https://synctek.io).
