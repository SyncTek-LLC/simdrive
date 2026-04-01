# SpecterQA iOS Simulator Driver

AI-driven iOS app testing via Claude Computer Use.

## Install

```bash
pip install "git+https://github.com/SyncTek-LLC/specterqa-ios.git"
```

No other packages required — `specterqa-ios` is fully standalone.

## Quick Start

```bash
# Verify environment (Xcode, simulators, API key)
specterqa-ios setup

# List available simulators
specterqa-ios devices

# Scaffold a project for your app
specterqa-ios init --slug my-app --name "My App"

# Edit the generated config
# .specterqa/products/my-app.yaml  — set bundle_id and app_path
# .specterqa/journeys/smoke-test.yaml  — customize test steps

# Validate your config before running
specterqa-ios validate --product my-app --journey smoke-test

# Boot a simulator
specterqa-ios boot

# Run a test journey
specterqa-ios run --product my-app --journey smoke-test

# Quick smoke test (reduced budget, fewer iterations)
specterqa-ios smoke --product my-app
```

## Commands

| Command | Description |
|---------|-------------|
| `specterqa-ios setup` | Check Xcode, simulators, and API key |
| `specterqa-ios devices` | List available iOS simulators |
| `specterqa-ios boot [--device <name>]` | Boot a simulator |
| `specterqa-ios install <app.app> [--device <id>]` | Install app on simulator |
| `specterqa-ios init [--slug <id>]` | Scaffold project config files |
| `specterqa-ios validate --product <slug>` | Validate product/journey config |
| `specterqa-ios run --product <slug> --journey <id>` | Run a test journey |
| `specterqa-ios smoke --product <slug>` | Quick smoke test |
| `specterqa-ios serve` | Start the MCP server (stdio transport) |

## Claude Code Integration

SpecterQA iOS ships as a [Model Context Protocol (MCP)](https://modelcontextprotocol.io/) server,
letting Claude Code drive iOS simulator tests directly from your editor.

### Install with MCP extras

```bash
pip install 'specterqa-ios[mcp]'
```

### Add to Claude Code

Add the server to your Claude Code MCP configuration. The `specterqa-ios-mcp` console script
is registered by `pip install` and starts the stdio server automatically.

**Option A — project-level** (`.claude/mcp.json` in your repo root):

```json
{
  "mcpServers": {
    "specterqa-ios": {
      "command": "specterqa-ios-mcp",
      "env": {
        "SPECTERQA_IOS_LICENSE": "founder"
      }
    }
  }
}
```

**Option B — global** (add to `~/.claude/mcp.json` or via `claude mcp add`):

```bash
claude mcp add specterqa-ios -- specterqa-ios-mcp
```

Set `ANTHROPIC_API_KEY` in the environment (or in the `env` block above).

### Available MCP Tools

Once connected, Claude Code can call these tools:

| Tool | Description |
|------|-------------|
| `ios_setup` | Check environment (Xcode, simulators, API key) |
| `ios_list_devices` | List available iOS simulators |
| `ios_boot_device` | Boot a simulator by name or UDID |
| `ios_install_app` | Install a .app bundle on a simulator |
| `ios_run_test` | Run a full test journey (primary tool) |
| `ios_run_smoke` | Quick smoke test (reduced budget) |
| `ios_run_exploratory` | Persona-driven AI exploration |
| `ios_get_results` | Retrieve results from a previous run |
| `ios_screenshot` | Screenshot the current simulator state |
| `ios_list_products` | List configured products |
| `ios_list_journeys` | List configured journeys |

### Example Claude Code session

```
> Run a smoke test on the Palace iOS app

[Claude calls ios_list_products, discovers palace-ios]
[Claude calls ios_boot_device, boots iPhone 15 Pro]
[Claude calls ios_run_smoke with product_slug="palace-ios"]
[Returns: 4/4 steps passed, 0 findings, $0.32 spent]
```

### Alternative: direct invocation

```bash
# stdio transport (for custom MCP clients)
specterqa-ios-mcp

# or via Python module
python -m specterqa.ios.mcp

# or via CLI
specterqa-ios serve
```

## Requirements

- macOS with Xcode 15+
- Python 3.10+
- `ANTHROPIC_API_KEY` environment variable

```bash
export ANTHROPIC_API_KEY=sk-ant-...
```

## Project Structure

After `specterqa-ios init`, your project contains:

```
.specterqa/
  products/
    my-app.yaml         # bundle_id, device, cost limits
  personas/
    ios-tester.yaml     # AI persona (goals, frustrations, credentials)
  journeys/
    smoke-test.yaml     # test steps with goals and checkpoints
  evidence/
    IOS-RUN-*/          # screenshots, step results, run-result.json
```

## How It Works

1. Each journey step has a `goal` — a natural-language instruction for Claude
2. The AI takes a screenshot of the simulator, observes the UI context, and decides the next action (tap, type, scroll, wait)
3. Actions are executed via `xcrun simctl` and Quartz event injection
4. Crashes, error logs, and performance anomalies are surfaced as Findings
5. All evidence (screenshots, step summaries, findings) is saved to `.specterqa/evidence/`

## License

Elastic License 2.0 — see [LICENSE](LICENSE).
