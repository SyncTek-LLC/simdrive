#!/usr/bin/env python3
"""Generate llms.txt from the MCP server's registered tool registry.

Usage:
    python scripts/generate_llms_txt.py          # dry-run (prints to stdout)
    python scripts/generate_llms_txt.py --write   # overwrites llms.txt

Make target:
    make llms                                     # see Makefile

The script parses src/specterqa/ios/mcp/server.py for @mcp.tool(name=...) decorators
and their description strings, then regenerates the tool registry section of llms.txt.
The static header/footer sections (install, pricing, links) are preserved from the
template embedded below.
"""
from __future__ import annotations

import argparse
import ast
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
SERVER_PY = REPO_ROOT / "src" / "specterqa" / "ios" / "mcp" / "server.py"
LLMS_TXT = REPO_ROOT / "llms.txt"

# ── Regex patterns ──────────────────────────────────────────────────────────

# Match:  @mcp.tool(\n    name="ios_foo",\n    description=(...),\n)
TOOL_BLOCK_RE = re.compile(
    r'@mcp\.tool\(\s*\n\s*name="([^"]+)".*?\n\s*description=\((.*?)\),\s*\n\s*\)',
    re.DOTALL,
)

# Match bare @mcp.tool(\n    name="ios_foo",\n) with no description keyword
TOOL_NO_DESC_RE = re.compile(
    r'@mcp\.tool\(\s*\n\s*name="([^"]+)"\s*,?\s*\n\s*\)',
    re.DOTALL,
)


def _extract_string_literal(raw: str) -> str:
    """Evaluate a Python string concatenation expression like ("foo " "bar ") → 'foo bar'."""
    raw = raw.strip()
    try:
        value = ast.literal_eval(raw)
        if isinstance(value, str):
            return value
    except Exception:
        pass
    # Fallback: strip surrounding parens and join quoted chunks
    chunks = re.findall(r'"((?:[^"\\]|\\.)*)"', raw)
    return " ".join(chunks)


def extract_tools(server_src: str) -> list[tuple[str, str]]:
    """Return list of (name, description) for every registered MCP tool."""
    tools: list[tuple[str, str]] = []
    seen: set[str] = set()

    for m in TOOL_BLOCK_RE.finditer(server_src):
        name = m.group(1)
        desc_raw = m.group(2)
        if name in seen:
            continue
        seen.add(name)
        desc = _extract_string_literal(desc_raw)
        # Trim to first sentence for the one-liner
        first_sentence = re.split(r"(?<=\.)\s", desc, maxsplit=1)[0].rstrip(".")
        tools.append((name, first_sentence))

    for m in TOOL_NO_DESC_RE.finditer(server_src):
        name = m.group(1)
        if name not in seen:
            seen.add(name)
            tools.append((name, "(no description)"))

    tools.sort(key=lambda t: t[0])
    return tools


def format_tool_line(name: str, desc: str) -> str:
    return f"- `{name}` — {desc}"


def build_registry_section(tools: list[tuple[str, str]]) -> str:
    count = len(tools)
    lines = [f"## MCP Tool Registry ({count} tools)", ""]
    for name, desc in tools:
        lines.append(format_tool_line(name, desc))
    return "\n".join(lines)


HEADER_TEMPLATE = """\
# SpecterQA iOS

SpecterQA iOS is an AI-native iOS simulator testing tool that lets AI agents record test sessions once using Claude's vision capabilities and replay them deterministically in CI — with zero AI cost on reruns. It ships as both a Python CLI (`specterqa-ios`) and an MCP server (`specterqa-ios-mcp`) with {count} tools, making it natively callable from Claude Code, Claude Desktop, and any MCP-compatible agent platform. The key insight: use AI to record, use a deterministic engine to replay. You pay for AI tokens once; every subsequent CI run is free.

## Install

```bash
# CLI only
pip install specterqa-ios

# With MCP server
pip install 'specterqa-ios[mcp]'

# With AI recording engine (required for record phase)
pip install 'specterqa-ios[mcp,orchestration]'
```

Requires: macOS, Xcode 15+, Python 3.10+

## Use via MCP (Claude Code / Claude Desktop)

Add to `.claude/mcp.json` (Claude Code) or `claude_desktop_config.json` (Claude Desktop):

```json
{{
  "mcpServers": {{
    "specterqa-ios": {{
      "command": "specterqa-ios-mcp",
      "env": {{
        "ANTHROPIC_API_KEY": "sk-ant-..."
      }}
    }}
  }}
}}
```

Then in Claude: _"Run a smoke test on the Example Reader app and record it as example-smoke."_ Claude drives the simulator, records every action as a deterministic YAML file. Future CI runs replay that YAML without calling Claude.

## Minimum Viable Session (5 calls)

```python
ios_start_session(bundle_id="com.example.app")
ios_screenshot()                           # observe initial screen
ios_tap(label="Sign In")                   # interact
ios_screenshot()                           # verify state
ios_stop_recording(name="signin-smoke")    # save replay YAML
ios_stop_session()                         # clean up
```

"""

FOOTER_TEMPLATE = """
## Use via CLI

```bash
# First-time setup
specterqa-ios setup          # verify Xcode + simulators
specterqa-ios init           # scaffold .specterqa/ project files
export ANTHROPIC_API_KEY=sk-ant-...

# Record a test (AI-driven, costs tokens once)
specterqa-ios run --product myapp --journey smoke

# Replay a recorded session (free, deterministic)
specterqa-ios replay .specterqa/replays/smoke.yaml

# CI mode — replay all recorded sessions
specterqa-ios ci .specterqa/replays/
specterqa-ios ci --parallel 4    # run 4 simultaneously (~10x faster)

# Validate a replay file before running
specterqa-ios validate-replay .specterqa/replays/smoke.yaml
```

## Pricing

| Tier       | Price     | Simulators | Runs/Session | Parallel CI |
|------------|-----------|------------|--------------|-------------|
| Trial      | Free      | 1          | 3            | No          |
| Indie      | $29/mo    | 2          | Unlimited    | No          |
| Pro        | $99/mo    | 4          | Unlimited    | Yes         |
| Team       | $299/mo   | 10         | Unlimited    | Yes         |
| Enterprise | Custom    | Unlimited  | Unlimited    | Yes         |

BYOK required: bring your own Anthropic API key for the record phase. Replay is free. SyncTek never sees your API key or test data.

## Links

- Homepage: https://synctek.io/products/specterqa-ios
- GitHub (docs + issues): https://github.com/SyncTek-LLC/specterqa-ios
- Pricing: https://synctek.io/products/specterqa-ios#pricing
- A2A agent card: https://github.com/SyncTek-LLC/specterqa-ios/blob/main/.well-known/agent.json
- Support: support@synctek.io
"""


def generate(write: bool = False) -> str:
    src = SERVER_PY.read_text(encoding="utf-8")
    tools = extract_tools(src)
    count = len(tools)

    registry = build_registry_section(tools)
    content = HEADER_TEMPLATE.format(count=count) + registry + FOOTER_TEMPLATE

    if write:
        LLMS_TXT.write_text(content, encoding="utf-8")
        print(f"Wrote {LLMS_TXT} ({count} tools)")
    else:
        print(content)

    return content


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--write",
        action="store_true",
        help="Write output to llms.txt (default: dry-run to stdout)",
    )
    args = parser.parse_args()
    generate(write=args.write)


if __name__ == "__main__":
    main()
