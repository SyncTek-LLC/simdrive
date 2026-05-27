<!-- mcp-name: io.github.SyncTek-LLC/simdrive -->

<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="https://github.com/SyncTek-LLC/simdrive/raw/main/docs/brand/wordmark-dark.svg">
    <img alt="simdrive" src="https://github.com/SyncTek-LLC/simdrive/raw/main/docs/brand/wordmark.svg" width="320">
  </picture>
</p>

<p align="center"><strong>Reproduce and validate iOS bugs in 60 seconds with Claude.</strong></p>


SimDrive is the MCP-native iOS automation toolkit your AI agent already knows
how to drive. Paste a Linear ticket into Cursor or Claude Code; SimDrive opens
the simulator, walks the steps, captures the failure, and saves the recording —
ready to replay deterministically in CI.

> **Repository note:** this repo is in the process of renaming from
> `specterqa-ios` to `simdrive`. The PyPI package is published as **`simdrive`**.

## The 60-second bug-repro loop

```text
You (in Cursor / Claude Code):
  "Use simdrive to reproduce Linear ENG-1247 — sign-in fails on iPhone 17 /
   iOS 26.3. Try test@example.com + 'pw123' and capture the error."

Claude:
  → session_start({device: "iPhone 17", os_version: "26.3", bundle_id: "com.acme.app"})
  → observe()                        # screenshot + annotated marks
  → tap({text: "Email"})
  → type_text({text: "test@example.com"})
  → tap({text: "Password"})
  → type_text({text: "pw123"})
  → tap({text: "Sign In"})
  → observe()                        # error toast captured
  → record_stop({name: "ENG-1247-repro"})

You: ship the fix, then "validate the recording still fails before deploy"
CI: runs the recording → Free. No AI tokens on replay.
```

## Why teams pay for SimDrive

- **Bug reproduction in 60 seconds** — paste the ticket, watch the agent walk
  it, attach the recording to the PR. The hero loop above is the entire pitch.
- **Record once, replay free in CI** — recordings are deterministic YAML+PNG
  bundles. After the AI captures the flow, every CI run is zero-AI-cost.
- **Autonomous test suites via the journey runner** — write a YAML journey
  with goals + success criteria; SimDrive drives the agent loop and reports
  pass/fail with evidence.
- **Real iOS device support** — WebDriverAgent-backed; one-command
  `simdrive bootstrap-device <udid>` bring-up.
- **Visual regression detection** — SSIM-based pre/post comparison with
  configurable drift handling (`halt` / `warn` / `force`).
- **Performance baselines + regression comparison** — capture CPU / RSS /
  thread baselines and compare future runs.

## Install

```bash
pip install simdrive
simdrive trial start --email you@example.com
# 14 days full access, then:
simdrive auth <your-license-key>
```

The trial is locally issued (Ed25519-signed, machine-locked) and works
offline. After 14 days, paid licenses unlock the full tool surface.

Requires macOS, Xcode 15+, Python 3.10+.

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
| **Pro** | $29 / mo | One seat, all tools, unlimited replays in CI |
| **Team** | $99 / seat / mo | Multi-seat, shared recording cloud (W2) |
| **Enterprise** | Contact us | Self-hosted licensing, SLA, custom integrations |

Full pricing + ROI calculator: <https://simdrive.dev/pricing>

## MCP tool surface

SimDrive exposes **32 MCP tools** (canonical count — see
[`docs/MCP_TOOL_SURFACE.md`](simdrive/docs/MCP_TOOL_SURFACE.md)) across these
categories: session lifecycle, observe, act (tap/swipe/type/press),
record/replay, devices/logs, performance/memory, diagnostics, app state,
alerts/permissions, appearance, replay management, recordings maintenance, and
journeys.

## Maestro-compatible YAML

Migrating from Maestro? SimDrive understands the shorthand natively:

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

Native SimDrive syntax and Maestro shortcuts coexist in the same file.

## Physical device support

Drive a paired iPhone/iPad in addition to the simulator. Opt in:

```bash
export SIMDRIVE_ALLOW_PHYSICAL_DEVICE=1
simdrive bootstrap-device <device-udid>
```

```python
session_start(bundle_id="com.example.app", udid="<device-udid>", target="device")
```

Known limitations: xcodebuild has rough edges on iOS 26.x and the install
step is slower than the simulator path. The simulator (`target="simulator"`,
the default) remains the fully supported path.

## Security

See [SECURITY.md](SECURITY.md) for vulnerability reporting and the
supported-versions policy.

## Support

- **Docs:** <https://docs.simdrive.dev>
- **Bugs / feature requests:** [open an issue](https://github.com/SyncTek-LLC/simdrive/issues/new/choose)
- **Email (private — license, billing, account):** <support@simdrive.dev>
- **Security disclosures:** <security@simdrive.dev>

## License

Elastic License 2.0 — see [LICENSE](LICENSE). Free for internal use; prohibits
offering SimDrive as a competing managed service.
