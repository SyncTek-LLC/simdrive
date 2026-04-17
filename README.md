# SpecterQA iOS

AI-driven iOS testing that records once and replays free in CI — no AI cost on repeat runs.

## Install

```bash
pip install "git+https://github.com/SyncTek-LLC/specterqa-ios.git"
```

## Quick Start with Claude Code (MCP)

```bash
pip install 'specterqa-ios[mcp]'
```

Add to `.claude/mcp.json`:

```json
{
  "mcpServers": {
    "specterqa-ios": {
      "command": "specterqa-ios-mcp",
      "env": { "ANTHROPIC_API_KEY": "sk-ant-..." }
    }
  }
}
```

Then in Claude Code: _"Run a smoke test on the Example Reader app, record it as Example Reader-smoke"_. Claude drives the simulator, records every action. After that, CI runs it free — no AI involved.

### Your First Session (30 seconds)

```python
# Claude calls these 6 MCP tools in order:
ios_start_session(bundle_id="com.example.app")
ios_screenshot()                          # see what's on screen
ios_tap(label="Sign In")                  # tap by label
ios_screenshot()                          # verify result
ios_stop_recording(name="signin-smoke")   # save replay YAML to .specterqa/replays/
ios_stop_session()                        # clean up
```

The replay YAML is now in `.specterqa/replays/signin-smoke.yaml`. Run it in CI with:
```bash
specterqa-ios replay .specterqa/replays/signin-smoke.yaml
```
No AI needed on replay — it runs the deterministic engine directly.

## Dual-Mode Architecture

| Phase | Who drives | Cost |
|-------|-----------|------|
| **Record** (once) | Claude AI via MCP | AI tokens |
| **Replay** (every CI run) | Deterministic replay engine | Free |

Record once with Claude, replay forever without it. This is the key advantage over traditional frameworks.

## Maestro-Compatible YAML

Users migrating from Maestro can use familiar shorthand — SpecterQA understands it natively:

```yaml
replay:
  bundle_id: com.example.app
  steps:
    - tapOn: "Sign In"            # same as: action: tap, element_label: Sign In
    - inputText: "user@example.com"
    - assertVisible: "Dashboard"  # asserts element is present
    - assertNotVisible: "Loading"
    - waitFor: "Feed"             # waits up to 10s for element
```

All Maestro shortcuts work alongside native SpecterQA syntax in the same file.

## CI Commands

```bash
# Run all replays (shared runner on by default — ~10x faster)
specterqa-ios ci .specterqa/replays/

# Parallel execution — run 4 replays simultaneously
specterqa-ios ci --parallel 4

# Per-replay isolation (full reset between each replay)
specterqa-ios ci --no-reuse-runner

# Validate a replay file before running it
specterqa-ios validate-replay .specterqa/replays/smoke.yaml
```

## Full CLI Reference

| Command | Description |
|---------|-------------|
| `setup` | Check Xcode, simulators, API key |
| `devices` | List available iOS simulators |
| `boot` | Boot a simulator |
| `install <app.app>` | Install app on simulator |
| `init` | Scaffold `.specterqa/` project files |
| `validate --product <slug>` | Validate product/journey config |
| `validate-replay <file>` | Validate a replay YAML (schema + references) |
| `run --product <slug> --journey <id>` | Run a test journey (AI-driven) |
| `smoke --product <slug>` | Quick smoke test |
| `replay <file>` | Replay a recorded session |
| `ci [dir]` | Run all replays in CI mode |
| `serve` | Start the MCP server |

## vs. Maestro / Appium / XCUITest

| | SpecterQA | Maestro | Appium | XCUITest |
|---|---|---|---|---|
| No AI in CI | Yes | Yes | Yes | Yes |
| AI-assisted recording | Yes | No | No | No |
| Maestro YAML syntax | Yes | Native | No | No |
| Parallel CI | Yes (`--parallel N`) | No | Yes | Yes |
| Zero config | Yes | Yes | No | No |
| Claude Code native | Yes (MCP) | No | No | No |

## Requirements

- macOS + Xcode 15+
- Python 3.10+
- `ANTHROPIC_API_KEY` (recording only — not needed for replay)

## License

Elastic License 2.0 — see [LICENSE](LICENSE).
