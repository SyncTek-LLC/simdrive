# SpecterQA iOS Simulator Driver

AI-driven iOS app testing via Claude Computer Use.

## Install

```bash
pip install specterqa
pip install git+https://github.com/SyncTek-LLC/specterqa-ios.git
```

## Quick Start

```bash
# Verify environment (Xcode, simulators, API key)
specterqa ios setup

# List available simulators
specterqa ios devices

# Scaffold a project for your app
specterqa ios init --slug my-app --name "My App"

# Edit the generated config
# .specterqa/products/my-app.yaml  — set bundle_id and app_path
# .specterqa/journeys/smoke-test.yaml  — customize test steps

# Boot a simulator
specterqa ios boot

# Run a test journey
specterqa ios run --product my-app --journey smoke-test

# Quick smoke test (reduced budget, fewer iterations)
specterqa ios smoke --product my-app
```

## Commands

| Command | Description |
|---------|-------------|
| `specterqa ios setup` | Check Xcode, simulators, and API key |
| `specterqa ios devices` | List available iOS simulators |
| `specterqa ios boot [--device <name>]` | Boot a simulator |
| `specterqa ios install <app.app> [--device <id>]` | Install app on simulator |
| `specterqa ios init [--slug <id>]` | Scaffold project config files |
| `specterqa ios run --product <slug> --journey <id>` | Run a test journey |
| `specterqa ios smoke --product <slug>` | Quick smoke test |

## Requirements

- macOS with Xcode 15+
- Python 3.10+
- `ANTHROPIC_API_KEY` environment variable

```bash
export ANTHROPIC_API_KEY=sk-ant-...
```

## Project Structure

After `specterqa ios init`, your project contains:

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
