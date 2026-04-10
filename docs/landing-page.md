# SpecterQA iOS — Landing Page Content

_Canonical URL: https://synctek.io/products/specterqa-ios_
_Last updated: 2026-04-08 | Version: 11.3.0_

---

## Hero

**H1:** The only iOS tester your agent can call.

**H2:** Record tests with AI. Replay free forever. Ship iOS apps with confidence.

**Subtext:** SpecterQA iOS records test sessions once using Claude's vision — then replays them deterministically in CI with zero AI cost. 19 MCP tools. Maestro compatible. BYOK.

**CTA (primary):** Start Free Trial
_Action: GitHub OAuth → activate Trial tier_

**CTA (secondary):** View on GitHub
_Action: https://github.com/SyncTek-LLC/specterqa-ios_

**Install snippet:**
```bash
pip install 'specterqa-ios[mcp]'
```

---

## How It Works (3-step flow)

1. **Record (once)** — Claude drives your iOS simulator, sees the screen, taps the right elements. Every action is saved as deterministic YAML.
2. **Commit** — Your replay files live in `.specterqa/replays/`. Version-controlled, human-readable.
3. **Replay (forever, free)** — CI runs the deterministic engine. No AI. No API cost. Same result every time.

_Diagram suggestion: Record phase (Claude icon → simulator → YAML file) → Replay phase (YAML file → engine → pass/fail)_

---

## Feature Grid

### 1. Record Once, Replay Free
AI records. Deterministic engine replays. You pay for tokens exactly once — every subsequent CI run is free. Unlike tools that call AI on every run, SpecterQA's replay engine never touches the Anthropic API after the initial recording.

### 2. Agent-Native by Design
19 MCP tools expose the full testing surface to any MCP-compatible agent. Add SpecterQA to your Claude Code session and drive iOS tests in plain English. A2A discoverable via `.well-known/agent.json`.

### 3. Maestro Compatible
Your existing Maestro YAML files work natively. `tapOn`, `inputText`, `assertVisible`, `assertNotVisible`, `waitFor` — all understood. Zero migration cost. Mix Maestro shorthand with SpecterQA native syntax in the same file.

### 4. Parallel CI — 10x Faster
Shared runner reuse and clone isolation let you run 10 replays in the time XCUITest runs 1. Configure parallelism in one flag: `--parallel N`. Available on Pro tier and above.

### 5. 90% Tap Accuracy
Set-of-Mark (SoM) prompting annotates the simulator screenshot with numbered markers before asking Claude where to tap. No coordinate guessing. No brittle selectors. Taps land where they should.

### 6. BYOK — Full Data Control
You bring your own Anthropic API key. SyncTek never sees it. Your test recordings, your simulator state, your app binary — none of it leaves your machine. 97% gross margin for us; complete control for you.

---

## Pricing

| | Trial | Indie | Pro | Team | Enterprise |
|---|---|---|---|---|---|
| **Price** | Free | $29/mo | $99/mo | $299/mo | Custom |
| **Simulators** | 1 | 2 | 4 | 10 | Unlimited |
| **Runs/session** | 3 | Unlimited | Unlimited | Unlimited | Unlimited |
| **MCP Tools** | 19 | 19 | 19 | 19 | 19 |
| **Parallel CI** | — | — | Yes | Yes | Yes |
| **Priority Support** | — | — | — | Yes | Yes |
| **SLA** | — | — | — | — | Yes |

_All tiers require your own Anthropic API key (BYOK) for the record phase. Replay is always free._

**CTA:** Start Free Trial — no credit card required

---

## Comparison Table

| | SpecterQA iOS | Maestro | Appium | XCUITest |
|---|---|---|---|---|
| No AI cost in CI | **Yes** | Yes | Yes | Yes |
| AI-assisted recording | **Yes** | No | No | No |
| Maestro YAML syntax | **Yes** | Native | No | No |
| Parallel CI | **Yes** (`--parallel N`) | No | Yes | Yes |
| Zero config | **Yes** | Yes | No | No |
| MCP / agent-native | **Yes** (19 tools) | No | No | No |
| BYOK | **Yes** | N/A | N/A | N/A |

---

## Social Proof

_[Placeholder — design partner testimonials to be added]_

> "We replaced a brittle XCUITest suite with SpecterQA in an afternoon. Record once, it just replays." — _Design Partner, iOS startup_

> "The MCP integration means our Claude Code agent can kick off regression tests without leaving the terminal." — _Design Partner, solo dev_

> "Maestro compatibility was the deciding factor. Zero migration friction." — _Design Partner, mobile team_

---

## FAQ

**What is BYOK?**
BYOK means "Bring Your Own Key." You supply your own Anthropic API key in an environment variable. SpecterQA uses it during the record phase to drive Claude's vision model. We never store, proxy, or see your key. Your data stays on your machine.

**Do I need a Mac?**
Yes. iOS Simulator requires macOS and Xcode 15+. SpecterQA runs on macOS only — this is a fundamental constraint of the iOS simulator platform, not a SpecterQA limitation. If you're running CI in the cloud, you need a macOS runner (GitHub Actions `macos-14` works out of the box).

**Is it Maestro compatible?**
Yes, fully. SpecterQA understands Maestro's shorthand syntax natively: `tapOn`, `inputText`, `assertVisible`, `assertNotVisible`, `waitFor`. Existing Maestro files run without changes. You can also mix Maestro syntax and SpecterQA native syntax in the same YAML file.

**How does pricing work?**
The Trial tier is free — 1 simulator, 3 runs per session, no credit card required. Paid tiers unlock more simulators and parallel CI. All tiers are subscription-based (monthly). You can upgrade, downgrade, or cancel any time. The Anthropic API cost for recording is separate — you pay Anthropic directly using your own key.

**Can my AI agent use it?**
Yes — that's the primary design goal. SpecterQA ships an MCP server (`specterqa-ios-mcp`) with 19 tools. Add it to your Claude Code, Claude Desktop, or any MCP-compatible orchestrator config, and your agent can boot simulators, record sessions, run CI, and read results without leaving the agent session. It is also A2A discoverable via `.well-known/agent.json`.

---

## Technical Specs

- **Language:** Python 3.10+
- **License:** Elastic License 2.0
- **Version:** 11.3.0
- **macOS + Xcode 15+** required
- **MCP transport:** stdio
- **MCP tools:** 19
- **Install:** `pip install 'specterqa-ios[mcp]'`
- **GitHub:** https://github.com/SyncTek-LLC/specterqa-ios

---

## Footer CTAs

- Start Free Trial
- View Docs on GitHub
- Contact Sales (Enterprise) → sales@synctek.io
- Support → support@synctek.io
