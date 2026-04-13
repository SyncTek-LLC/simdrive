# Technical Accuracy Review — INIT-2026-525

## Reviewer: CodeAtlas
## Date: 2026-04-08

---

## Scope

Files reviewed:
- `/Users/atlas/Documents/specterqa-ios/README.md` — primary user-facing doc (EXISTS)
- `/Users/atlas/Documents/specterqa-ios/docs/landing-page.md` — DOES NOT EXIST (not yet created)
- `/Users/atlas/Documents/specterqa-ios/llms.txt` — DOES NOT EXIST (not yet created)
- `/Users/atlas/Documents/specterqa-ios/.well-known/agent.json` — DOES NOT EXIST (not yet created)

Code reviewed:
- `src/specterqa/ios/mcp/server.py` — 19 `@mcp.tool` decorators confirmed
- `src/specterqa/ios/cli/commands.py` — all CLI commands enumerated
- `pyproject.toml` — version, package name, entry points
- `src/specterqa/ios/backends/` — xctest_client.py, indigo_hid.py, browserstack.py
- `src/specterqa/ios/som_runner.py` — SoM accuracy claim source
- `src/specterqa/ios/replay.py` — replay engine (no API key dependency confirmed)
- `src/specterqa/ios/license/validator.py` + `license_cmd.py` + `stripe_webhook.py` — tier names

**Note:** The three GTM files (landing-page.md, llms.txt, agent.json) do not yet exist. This review audits claims in the README (the only existing user-facing document) and pre-validates any claims intended for those forthcoming files.

---

## Verified Claims

| Claim | Status | Evidence |
|-------|--------|----------|
| **19 MCP tools** | CONFIRMED | Exactly 19 `@mcp.tool(...)` decorators in `server.py` (lines 1367–1694): `ios_start_session`, `ios_stop_session`, `ios_screenshot`, `ios_tap`, `ios_wait`, `ios_wait_for_element`, `ios_start_recording`, `ios_stop_recording`, `ios_accessibility_audit`, `ios_swipe`, `ios_swipe_back`, `ios_type`, `ios_elements`, `ios_set_appearance`, `ios_press_key`, `ios_long_press`, `ios_save_replay`, `ios_simctl`, `ios_webview_elements` |
| **Version v11.3.0** | CONFIRMED — in pyproject.toml | `pyproject.toml` line 7: `version = "11.3.0"`. Latest git tag: `v11.3.0`. These match. |
| **Record once, replay free forever** | CONFIRMED | `replay.py` contains zero references to `ANTHROPIC_API_KEY` or `anthropic` SDK. `ReplayPlayer` runs deterministically from YAML. README states "ANTHROPIC_API_KEY (recording only — not needed for replay)" which is accurate. |
| **Maestro YAML compatible** | CONFIRMED (partial) | `replay.py` lines 381–431: `_normalize_maestro_step()` handles `tapOn`, `assertVisible`, `assertNotVisible`, `inputText`, `waitFor`. These are the shortcuts documented in README. |
| **Parallel CI execution** | CONFIRMED | `commands.py` lines 1886–2010: `--parallel N` flag on `ci` command, uses `ThreadPoolExecutor`. README shows `specterqa-ios ci --parallel 4`. |
| **Crash detection** | CONFIRMED | `src/specterqa/ios/drivers/simulator/crash.py`: `CrashDetector` class, integrated into `SimulatorDriver`. |
| **Visual regression** | CONFIRMED | `replay.py` lines 39–80: `screenshot_diff()` function using Pillow `ImageChops.difference()`. Returns percent pixel diff between two screenshots. |
| **Network inspection** | CONFIRMED | `src/specterqa/ios/drivers/simulator/network.py`: `NetworkInspector` class. Integrated via `ai_context.py`. |
| **XCTest backend** | CONFIRMED | `backends/xctest_client.py`: `XCTestBackend` — HTTP client talking to Swift runner on port 8222. |
| **IndigoHID backend** | CONFIRMED | `backends/indigo_hid.py`: pure-Python ctypes-based headless touch injection via Apple private API. |
| **Entry points match docs** | CONFIRMED | `pyproject.toml` defines `specterqa-ios` → `specterqa.ios.cli.commands:main` and `specterqa-ios-mcp` → `specterqa.ios.mcp.server:serve`. Both match README install instructions. |
| **CLI commands exist** | CONFIRMED | All commands in README table (`setup`, `devices`, `boot`, `install`, `init`, `validate`, `validate-replay`, `run`, `smoke`, `replay`, `ci`, `serve`) are registered in `commands.py`. |
| **`doctor` command exists** | CONFIRMED (not in README table) | `commands.py` line 338: `@ios_command_group.command("doctor")`. README omits this command from its CLI reference table — this is an omission, not an error. |
| **License tiers** | CONFIRMED | `validator.py` and `license_cmd.py` define tiers: `trial`, `indie`, `pro`, `enterprise`, `founder` (internal grant). `stripe_webhook.py` maps Stripe prices to the same tier strings. |
| **Python 3.10+ requirement** | CONFIRMED | `pyproject.toml`: `requires-python = ">=3.10"`. README states "Python 3.10+". |
| **macOS + Xcode 15+** | UNVERIFIABLE FROM CODE ALONE | Xcode minimum version is not enforced programmatically. `setup` command checks `xcrun`, but no version gate. Stated as a requirement in README — flagged below. |

---

## Accuracy Issues (must fix before deploy)

### ISSUE 1 — PyPI version lag: `pip install specterqa-ios` installs v11.2.0, not v11.3.0

**Severity: HIGH**

`pyproject.toml` and git tag say `v11.3.0`. PyPI shows latest published version as `11.2.0`. Version `11.3.0` has NOT been published to PyPI.

The README install instruction uses:
```bash
pip install "git+https://github.com/SyncTek-LLC/specterqa-ios.git"
```
This correctly installs from git HEAD and will get v11.3.0. That is the PRIMARY install method in the README and it is accurate.

However, the MCP quick-start section shows:
```bash
pip install 'specterqa-ios[mcp]'
```
This installs from PyPI and will land users on v11.2.0 — one major version behind. This is a **broken install path** for the MCP use case if v11.3.0 has not been published to PyPI before launch.

**Fix:** Publish v11.3.0 to PyPI before shipping GTM copy, OR change the MCP install example to also use `git+` form.

---

### ISSUE 2 — `doctor` command missing from README CLI reference table

**Severity: LOW**

`doctor` command exists at `commands.py:338` and is described there as checking "Xcode, simulator, Python env, license key, BrowserStack credentials, and installed package version." It is more comprehensive than `setup`. It is not listed in the README CLI reference table.

**Fix:** Add `doctor` row to the CLI reference table. Users migrating or troubleshooting will miss it.

---

### ISSUE 3 — `runner` and `wda` subcommand groups not documented

**Severity: LOW / INFORMATIONAL**

`commands.py` registers two subcommand groups:
- `specterqa-ios runner build/status/clean` (runner build utilities)
- `specterqa-ios wda start/stop/status` (WebDriverAgent integration)
- `specterqa-ios license` (license management — mounted conditionally)

These are absent from the README. For a GTM doc this is arguably appropriate (they are power-user / internal commands), but `runner build` is a prerequisite for XCTest mode and should at minimum be mentioned in setup instructions.

---

## Unverifiable Claims (flag for disclaimer)

### CLAIM — "90% SoM tap accuracy"

**Status: UNVERIFIABLE FROM CODE — sourced from research citation, not internal benchmarks**

`som_runner.py` line 16:
```python
# Research: SoM prompting improves UI agent accuracy from ~50% to ~90%+
# by eliminating coordinate prediction entirely.
```

This is a comment citing external SoM research, not an internally measured benchmark. SpecterQA has not published benchmark results for its own tap accuracy on iOS Simulator.

**If landing-page.md or llms.txt makes a "90% tap accuracy" claim, it must be attributed as a research-derived estimate, not a measured product metric, OR the internal benchmark must be run and documented first.**

**Recommended copy:** "SoM-powered tapping eliminates coordinate prediction — the technique improves UI agent accuracy from ~50% to 90%+ in published research" (with citation). Do NOT state "SpecterQA achieves 90% tap accuracy" without running controlled benchmarks.

---

### CLAIM — "Xcode 15+ required"

**Status: UNVERIFIABLE — no code-level enforcement**

The codebase does not gate on Xcode version. The claim in README is a stated requirement but is not validated at runtime. If tested against Xcode 14, behavior is undefined. Flag this as a "tested against Xcode 15/16" disclaimer rather than a hard requirement, unless a version check is added to `setup`/`doctor`.

---

## Version / Naming Mismatches

| Item | Claimed | Actual | Match? |
|------|---------|--------|--------|
| Package version (pyproject.toml) | v11.3.0 | v11.3.0 | YES |
| Latest git tag | v11.3.0 | v11.3.0 | YES |
| PyPI published version | (implied current) | v11.2.0 | **NO — v11.3.0 not on PyPI** |
| Package name | `specterqa-ios` | `specterqa-ios` | YES |
| MCP entry point | `specterqa-ios-mcp` | `specterqa-ios-mcp` | YES |
| MCP tool count | 19 | 19 | YES |
| License tiers in copy | (not yet in GTM docs) | trial / indie / pro / enterprise / founder | N/A — GTM docs not yet written |

---

## GTM Documents Not Yet Created

The following files referenced in the review brief do not exist:

| File | Status |
|------|--------|
| `docs/landing-page.md` | NOT CREATED |
| `llms.txt` | NOT CREATED |
| `.well-known/agent.json` | NOT CREATED |

These must be authored before launch. The claims verified above apply to content that WILL go into these files. The README is accurate except for the PyPI version lag issue.

---

## Verdict

**NEEDS CORRECTIONS** — one blocking issue, two minor issues.

**Blocking before launch:**
1. **PyPI v11.3.0 not published** — the `pip install specterqa-ios[mcp]` path in the MCP quick-start installs v11.2.0. Either publish v11.3.0 to PyPI or update the install command to use `git+`.

**Required for GTM copy accuracy:**
2. **"90% SoM tap accuracy" claim must be attributed to research, not stated as a measured product metric**, unless internal benchmarks are run.

**Low priority:**
3. Add `doctor` command to CLI reference table.

The core architecture claims (replay is free, 19 MCP tools, Maestro YAML compatibility, parallel CI, crash detection, visual regression, network inspection, XCTest + IndigoHID backends) are all **code-confirmed and accurate**.
