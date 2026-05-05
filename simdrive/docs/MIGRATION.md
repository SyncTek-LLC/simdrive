# Migration: `specterqa-ios` → `simdrive`

If you're landing here from a `specterqa-ios` link, bookmark, or an older install
in your MCP config — welcome. Here's what changed and how to get up and running.

## The short version

```bash
pip uninstall specterqa-ios
pip install --pre simdrive
simdrive trial start --email you@example.com --offline-dev
```

Update your MCP config:

```json
{
  "mcpServers": {
    "simdrive": { "command": "simdrive" }
  }
}
```

## What changed

| Before (`specterqa-ios` 16.x) | Now (`simdrive` 1.0.x) |
|-------------------------------|------------------------|
| `pip install specterqa-ios` | `pip install --pre simdrive` |
| `command: specterqa-ios` in MCP config | `command: simdrive` |
| `ios_observe`, `ios_tap`, `ios_start_session`, … (all `ios_` prefixed) | `observe`, `tap`, `session_start`, … (unprefixed) |
| 29 MCP tools | 31 MCP tools (adds `run_journey`, `clear_field`) |
| Python import: `from specterqa_ios.X import Y` | `from simdrive.X import Y` |
| ANTHROPIC_API_KEY required for `run_journey` | Not required via MCP — MCP sampling delegates to your client |

## Tool name mapping

All 29 original tools are present in simdrive 1.0 with the `ios_` prefix removed:

| specterqa-ios | simdrive |
|---------------|----------|
| `ios_observe` | `observe` |
| `ios_start_session` | `session_start` |
| `ios_stop_session` | `session_end` |
| `ios_start_recording` | `record_start` |
| `ios_stop_recording` | `record_stop` |
| `ios_list_replays` | `list_replays` |
| `ios_devices` | `list_devices` |
| (all others) | same name, no prefix |

## New in 1.0

- `run_journey` — agent-driven journey execution via MCP sampling (no API key needed)
- `clear_field` — Cmd-A + delete to clear a text field
- `--offline-dev` trial mode — no cloud server required for development licenses

## PyPI history

The package was distributed as `specterqa-ios` during the 16.x development cycle
and renamed to `simdrive` for the 1.0 public launch. If you have `specterqa-ios`
installed alongside `simdrive`, uninstall `specterqa-ios` first — both provide a
`specterqa-ios` console script and they will conflict.

## Questions

Open an issue at [github.com/SyncTek-LLC/specterqa-ios/issues](https://github.com/SyncTek-LLC/specterqa-ios/issues).
