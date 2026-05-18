# SimDrive

AI-driven iOS testing that records once and replays free in CI — no AI cost on repeat runs.

> **Note:** This repository is in the process of being renamed from `specterqa-ios` to `simdrive`.
> The PyPI package is published as **`simdrive`**. GitHub URLs below currently still resolve under
> the legacy `SyncTek-LLC/specterqa-ios` path; they will redirect after the GitHub rename completes.

## Install

```bash
pip install simdrive
```

Or, install the latest from source:

```bash
pip install "git+https://github.com/SyncTek-LLC/specterqa-ios.git"
```

## Install (developers)

```bash
cd simdrive && pip install -e .
```

## Quick Start with Claude Code (MCP)

```bash
pip install simdrive
```

Add to `.claude/mcp.json`:

```json
{
  "mcpServers": {
    "simdrive": {
      "command": "simdrive"
    }
  }
}
```

Then in Claude Code: _"Run a smoke test on the Example Reader app, record it as Example Reader-smoke"_. Claude drives the simulator, records every action. After that, CI runs it free — no AI involved.

### Your First Session (30 seconds)

```python
# Claude calls these MCP tools in order:
session_start(bundle_id="com.example.app")
observe()                                 # see what's on screen
tap(label="Sign In")                      # tap by label
observe()                                 # verify result
record_stop(session_id="...", name="signin-smoke")  # save replay
session_end(session_id="...")             # clean up
```

The replay file is now saved under your recordings root. Run it in CI with:
```bash
simdrive replay signin-smoke
```
No AI needed on replay — it runs the deterministic engine directly.

## Dual-Mode Architecture

| Phase | Who drives | Cost |
|-------|-----------|------|
| **Record** (once) | Your MCP agent (Claude, Cline, …) | AI tokens |
| **Replay** (every CI run) | Deterministic replay engine | Free |

Record once with your agent, replay forever without it. This is the key advantage over traditional frameworks.

## Maestro-Compatible YAML

Users migrating from Maestro can use familiar shorthand — SimDrive understands it natively:

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

All Maestro shortcuts work alongside native SimDrive syntax in the same file.

## MCP Tool Surface

SimDrive exposes **32 MCP tools** (canonical count — see `docs/MCP_TOOL_SURFACE.md`) across these
categories: session lifecycle, observe, act (tap/swipe/type/press), record/replay, devices/logs,
performance/memory, diagnostics, app state, alerts/permissions, appearance, replay management,
recordings maintenance, and journeys.

## Physical device support (experimental)

SimDrive can drive a connected iOS device (iPhone or iPad) in addition to the simulator. Physical device support uses `devicectl`-based deployment and communicates with the XCTest runner over the device's IP address, exactly as in the simulator path.

To opt in, set the environment variable `SIMDRIVE_ALLOW_PHYSICAL_DEVICE=1` and pass `device_type="physical"` when calling `session_start`:

```bash
export SIMDRIVE_ALLOW_PHYSICAL_DEVICE=1
```

```python
# In your MCP tool call:
session_start(bundle_id="com.example.app", device_id="<device-udid>", device_type="physical")
```

Known limitations: xcodebuild integration has rough edges on iOS 26 that can cause intermittent failures; the install/deploy step is slower than the simulator path; and there is no guarantee of stability on non-GM OS builds. The simulator (`device_type="simulator"`, the default) remains the fully supported path.

## Requirements

- macOS + Xcode 15+
- Python 3.10+
- An MCP-capable client (Claude Code, Claude Desktop, etc.) supplies its own model credentials —
  SimDrive itself does not require an API key.

## Security

See [SECURITY.md](SECURITY.md) for vulnerability reporting and the supported-versions policy.

## License

Elastic License 2.0 — see [LICENSE](LICENSE).
