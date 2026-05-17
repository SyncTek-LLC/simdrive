# SimDrive — Landing Page Content

_Canonical URL: https://synctek.io/products/simdrive_
_Last updated: 2026-05-17 | Version: 1.0.0a13_

> **STATUS: DEPRECATED in favor of the upcoming `simdrive-site` repo.** This file is preserved
> as a content reference for the new marketing site fork being spun up under INIT-2026-549.
> Authoritative product copy lives in `README.md` and `llms.txt`. Brand strings here have been
> updated from the legacy `SpecterQA iOS` name; numbers (tool count, pricing) may lag — treat
> `docs/MCP_TOOL_SURFACE.md` and `pyproject.toml` as canonical.

---

## Hero

**H1:** The only iOS tester your agent can call.

**H2:** Record tests with AI. Replay free forever. Ship iOS apps with confidence.

**Subtext:** SimDrive records test sessions once using your MCP client's vision-capable model — then replays them deterministically in CI with zero AI cost. 32 MCP tools. Maestro compatible. BYOK.

**CTA (primary):** Start Free Trial
_Action: GitHub OAuth → activate Trial tier_

**CTA (secondary):** View on GitHub
_Action: https://github.com/SyncTek-LLC/specterqa-ios (rename to `simdrive` pending)_

**Install snippet:**
```bash
pip install simdrive
```

---

## How It Works (3-step flow)

1. **Record (once)** — Your MCP agent drives the iOS simulator, sees the screen, taps the right elements. Every action is saved as a deterministic recording.
2. **Commit** — Your recordings live alongside your repo. Version-controlled, human-readable.
3. **Replay (forever, free)** — CI runs the deterministic engine. No AI. No API cost. Same result every time.

---

## Feature Grid

### 1. Record Once, Replay Free
AI records. Deterministic engine replays. You pay for tokens exactly once — every subsequent CI run is free. Unlike tools that call AI on every run, SimDrive's replay engine never touches a model API after the initial recording.

### 2. Agent-Native by Design
32 MCP tools expose the full testing surface to any MCP-compatible agent. Add SimDrive to your Claude Code session and drive iOS tests in plain English.

### 3. Maestro Compatible
Your existing Maestro YAML files work natively. `tapOn`, `inputText`, `assertVisible`, `assertNotVisible`, `waitFor` — all understood. Zero migration cost. Mix Maestro shorthand with SimDrive native syntax in the same file.

### 4. Parallel CI
Shared runner reuse and clone isolation let you run multiple recordings concurrently. Configure parallelism via the runner CLI.

### 5. ~90% Tap Accuracy (based on Set-of-Mark research)
Set-of-Mark (SoM) prompting annotates the simulator screenshot with numbered markers before asking the model where to tap. No coordinate guessing. No brittle selectors. Taps land where they should.

### 6. BYOK — Full Data Control
You own your model API key (via your MCP client). Your data never touches our servers. Your recordings, your simulator state, your app binary — none of it leaves your machine.

---

## Pricing

See https://synctek.io/products/simdrive for the canonical, live pricing table.
SimDrive itself does not require an API key; record-time AI cost is borne by your MCP
client (BYOK).

---

## Comparison Table

| | SimDrive | Maestro | Appium | XCUITest |
|---|---|---|---|---|
| No AI cost in CI | **Yes** | Yes | Yes | Yes |
| AI-assisted recording | **Yes** | No | No | No |
| Maestro YAML syntax | **Yes** | Native | No | No |
| Parallel CI | **Yes** | No | Yes | Yes |
| Zero config | **Yes** | Yes | No | No |
| MCP / agent-native | **Yes** (32 tools) | No | No | No |
| BYOK | **Yes** | N/A | N/A | N/A |

---

## FAQ

**What is BYOK?**
BYOK means "Bring Your Own Key." Your MCP client (Claude Code, Cline, etc.) supplies its
own model API key. SimDrive itself does not store, proxy, or see your key. Your data stays
on your machine.

**Do I need a Mac?**
Yes. iOS Simulator requires macOS and Xcode 15+. SimDrive runs on macOS only — this is a
fundamental constraint of the iOS simulator platform.

**Is it Maestro compatible?**
Yes, fully. SimDrive understands Maestro's shorthand syntax natively: `tapOn`, `inputText`,
`assertVisible`, `assertNotVisible`, `waitFor`. Existing Maestro files run without changes.

**Can my AI agent use it?**
Yes — that's the primary design goal. SimDrive ships an MCP server (`simdrive`) with 32
tools. Add it to your Claude Code, Claude Desktop, or any MCP-compatible orchestrator config.

---

## Technical Specs

- **Language:** Python 3.10+
- **License:** Elastic License 2.0
- **Version:** 1.0.0a13
- **macOS + Xcode 15+** required
- **MCP transport:** stdio
- **MCP tools:** 32 (canonical — see `docs/MCP_TOOL_SURFACE.md`)
- **Install:** `pip install simdrive`
- **GitHub:** https://github.com/SyncTek-LLC/specterqa-ios (rename to `simdrive` pending)

---

## Footer CTAs

- Start Free Trial
- View Docs on GitHub
- Contact Sales (Enterprise) → sales@synctek.io
- Support → support@synctek.io
- Security → security@simdrive.dev
