# SpecterQA iOS v14.0.0 — Design Document

**Status:** Draft — awaiting Chairman review  
**Author:** CodeAtlas / SyncTek  
**Date:** 2026-04-19  
**Initiative:** INIT-2026-525  
**Supersedes:** v13.3.0 (PyPI), v13.2.2 (last stable)

---

## 1. Problem Statement

### Root Cause: Three Parallel Deploy Paths

v13.3.0 introduced `ios_start_runner` / `ios_stop_runner` as explicit runner lifecycle tools, adding a third implementation of "deploy XCTest runner" on top of the two that already existed:

| Path | Location | Owns xctestrun mutation? | Owns xcodebuild process? |
|------|----------|--------------------------|--------------------------|
| `TestSession._deploy_runner()` | `session_manager.py:883` | Yes | Yes (self._runner_process) |
| `handle_start_session` inline | `mcp/server.py:371–405` | Yes | Yes (module-level \_runner_proc) |
| `handle_start_runner` | `mcp/server.py:2731` | Yes | Yes (\_active_runners dict) |

All three paths call `TestSession._inject_xctestrun_env()` to mutate the same `.xctestrun` plist on disk before launching `xcodebuild test-without-building`. When any two paths are in flight simultaneously — which happens whenever an AI agent calls both `ios_start_session` and `ios_start_runner` in parallel, or when a session reconnects — they corrupt each other's plist writes and race on the same xcodebuild process launch.

### Why the Same Bug Keeps Coming Back

Tracing three recent regressions to the same structural cause:

- **B9 / v13.2.0**: `ios_start_session(backend="xctest")` stopped deploying the runner because the inline deploy path in `handle_start_session` was removed during the BackendSelector refactor, but `TestSession._deploy_runner()` was not wired back as the sole path. Two paths → one disappeared → silent failure.
- **B1.x / v13.2.0+1**: `_runner_source_dir()` pointed at the dev-tree `runner/` layout. The `setup.py build_py` override copies Swift sources into `src/specterqa/ios/runner_source/` at wheel-build time, but the installed wheel has a different path. The dual-source-directory arrangement (canonical `runner/`, wheel copy `runner_source/`) created a third surface where path resolution diverged.
- **v13.3.0 sim-kill**: `ios_start_runner` launches a second `xcodebuild test-without-building` process against a sim that may already be held by a `TestSession`. When xcodebuild fails (port conflict, plist corruption), its cleanup path calls `xcrun simctl shutdown <udid>`. The session that was already running sees its sim disappear. The tool that was meant to be a convenience wrapper became a silent sim-killer.

**Pattern:** Every regression traces to the same root — there is no single owner of runner process state. Any refactor that doesn't address the owner problem produces a new variant of the same class of bug.

---

## 2. Goals

v14.0.0 serves two equal-priority use cases. Both must be first-class after this release.

### G1 — CI Replay (Record → Save → Deterministic Replay)

User records a flow once via MCP or CLI. Saves a YAML replay artifact. CI runs `ios_replay` or `specterqa-ios replay` on every PR. The replay is a single-shot operation that may take minutes — startup cost is acceptable. Priority: determinism and zero flakiness over speed.

**Current state:** Works end-to-end as of v13.2.2 (B3+B4 fixed, validate-replay pipeline complete). v14 must not regress any validated replay flow.

### G2 — AI Debugging Loop (Agent → Walk → Log → Rebuild → Retest)

Claude or Cursor drives the app iteratively: tap something, read the logs that fired, modify code, rebuild app, relaunch, repeat. Each cycle must be under 5 seconds. Current per-cycle cost is 35–50s because relaunching the app requires tearing down and reconstructing the entire session.

**Current state:** Not viable. v14 introduces primitives that make this loop fast and observable. This is the primary motivation for the new MCP tools in Section 7.

---

## 3. Non-Goals

The following are explicitly out of scope for v14.0.0. They are first-class features and must continue to work without regression:

| Tag | Feature | Status |
|-----|---------|--------|
| W1 | Maestro YAML syntax for replay files | Keep as-is |
| W2 | Shared-runner CI mode (`ios_start_session` no-clone path) | Keep as-is |
| W3 | `validate-replay` strict mode / over-eager validation | Keep as-is, no behavior change |
| W4 | Runner status table in `ios_doctor` | Keep as-is |
| W5 | All 10 discovery + observation tools | Keep as-is |
| W6 | `frontmost_udid` auto-detection | Keep as-is |

v14 does not introduce any new Maestro YAML fields, does not change replay file schema, and does not change CLI command surface (except removing the two broken tools from MCP registration).

---

## 4. Architecture — `RunnerProcess` Lifecycle Class

### 4.1 Motivation

The fix is ownership, not patching. One class owns the runner process lifecycle. Every path that needs a runner asks `RunnerProcess` for one. No path bypasses it.

### 4.2 Class Location

```
src/specterqa/ios/runner_process.py
```

### 4.3 API Surface

```python
from __future__ import annotations
from enum import Enum, auto
from pathlib import Path
from typing import Optional


class RunnerState(Enum):
    IDLE = auto()       # No process. Port unallocated.
    BUILDING = auto()   # xcodebuild -scheme running (runner build).
    DEPLOYED = auto()   # xcodebuild test-without-building launched; awaiting /health.
    RUNNING = auto()    # /health returned 200. Ready for requests.
    STOPPED = auto()    # Gracefully stopped. Port released.
    FAILED = auto()     # Unrecoverable. Error stored in self.last_error.


class RunnerProcess:
    """Single owner of the XCTest runner process lifecycle.

    One instance per (udid, port) pair. All callers share the same instance
    via RunnerProcess.acquire(udid, port).
    """

    # ── Factory ──────────────────────────────────────────────────────────────

    @classmethod
    def acquire(cls, udid: str, port: int = 8222) -> "RunnerProcess":
        """Return existing instance for (udid, port) or create a new IDLE one."""
        ...

    # ── Lifecycle ────────────────────────────────────────────────────────────

    def build(self, build_dir: Path, force: bool = False) -> None:
        """Build the Swift runner if sources changed (hash-gated).

        Raises RunnerBuildError with xcodebuild stderr on failure.
        State: IDLE → BUILDING → IDLE (build only, no deploy).
        """
        ...

    def deploy(self, bundle_id: str, port: Optional[int] = None) -> None:
        """Inject env into xctestrun, launch xcodebuild test-without-building.

        Idempotent if already RUNNING on the same port.
        Raises RunnerDeployError on xcodebuild failure — LOUD, no fallback.
        State: IDLE → DEPLOYED → RUNNING.
        """
        ...

    def stop(self, shutdown_sim: bool = False) -> None:
        """Terminate xcodebuild process. Release port.

        shutdown_sim=True only when the caller is explicitly tearing down the
        simulator (e.g. session cleanup). Never called by ios_stop_runner.
        State: RUNNING → STOPPED.
        """
        ...

    def healthcheck(self, timeout_s: float = 60.0) -> bool:
        """Poll /health until 200 or timeout. Returns True on success."""
        ...

    def relaunch_app(self, bundle_id: str) -> None:
        """Kill + relaunch the user's app without stopping the runner.

        Uses simctl terminate + simctl launch. Runner HTTP server stays up.
        Target: < 2s. Does NOT restart xcodebuild.
        State: RUNNING → RUNNING (no state change).
        """
        ...

    def allocate_port(self) -> int:
        """Find a free port in _PORT_RANGE. Raises RuntimeError if all busy."""
        ...

    # ── Introspection ────────────────────────────────────────────────────────

    @property
    def state(self) -> RunnerState:
        ...

    @property
    def port(self) -> Optional[int]:
        ...

    @property
    def last_error(self) -> Optional[str]:
        ...
```

### 4.4 State Machine

```
                  ┌─────────────────────────────────────────────────────┐
                  │                  RunnerProcess                       │
                  └─────────────────────────────────────────────────────┘

   acquire()
      │
      ▼
   IDLE ──── build() ──► BUILDING ──► IDLE  (build only, no process)
      │
      │ deploy()
      ▼
   DEPLOYED  (xcodebuild launched, /health not yet 200)
      │
      │ healthcheck() → 200
      ▼
   RUNNING ◄──── relaunch_app() (loop back, no state change)
      │
      │ stop()
      ▼
   STOPPED

   Any state ──── xcodebuild exits non-zero ──► FAILED
                  (last_error populated, no retry)
```

### 4.5 Concurrency

`RunnerProcess.acquire()` is protected by a per-(udid, port) `threading.Lock`. The deploy + healthcheck sequence holds the lock. Concurrent callers block on `acquire()` until the first caller reaches RUNNING, then receive the already-running instance. No double-launch possible.

The module-level registry (`_instances: dict[tuple[str, int], RunnerProcess]`) is guarded by a separate global lock for registry mutation only.

### 4.6 Migration of Existing Callers

| Old caller | New call |
|-----------|----------|
| `TestSession._deploy_runner()` | `self._runner = RunnerProcess.acquire(udid, port); self._runner.deploy(bundle_id)` |
| `handle_start_session` inline deploy | same |
| `handle_start_runner` | same |
| `TestSession.stop()` xcodebuild kill | `self._runner.stop(shutdown_sim=True)` |

All three existing parallel implementations are deleted. No `subprocess.Popen("xcodebuild", ...)` call exists outside `RunnerProcess`.

---

## 5. Backend Policy

### XCTest is Default and Only First-Class Backend

XCTest is the backend for all production use. When `ios_start_session` is called without `backend=` argument, XCTest is selected. When XCTest deploy fails, the error is loud and actionable:

```
RunnerDeployError: xcodebuild test-without-building failed.
  UDID: 1A2B3C4D-...
  Port: 8222
  Build dir: ~/.specterqa/runner-build
  xcodebuild stderr:
    <full stderr here>

  Next steps:
    1. Run: specterqa-ios runner build
    2. Verify the simulator is booted: xcrun simctl list | grep Booted
    3. See docs/troubleshooting.md for known Xcode 16 / iOS 18.4 issues.
```

**There is NO silent fallback to AX on XCTest failure.** Silent wrong-data (AX returning stale or partial element trees while the user thinks XCTest is running) is worse than a loud failure. This policy was introduced in v13.2.0 and must be reinforced throughout the v14 refactor. Any code path that catches a `RunnerDeployError` and silently retries with AX is a bug.

### AX Backend

Opt-in only: `ios_start_session(backend="ax")`. Intended for environments without Xcode installed (CI machines with only Command Line Tools, remote runners). AX returns lower-fidelity element trees and does not support replay recording. It is documented as a fallback for observation-only tasks, not for recording or AI debugging loops.

---

## 6. Wheel Restructure

### Current Layout (Fragile)

```
runner/                          ← canonical Swift sources (dev layout)
  Sources/
  SpecterQARunner.xcodeproj/
  ...

src/specterqa/ios/runner_source/ ← wheel copy (populated by setup.py build_py override)
  Sources/              ← (MISSING in current runner_source — only stubs)
  build.sh
  launch.sh
  Package.swift
  __init__.py
```

The `setup.py build_py` override copies `runner/Sources/` into `runner_source/Sources/` at wheel-build time. This is the source of the B1 class of bugs: any divergence between `runner/` and `runner_source/` — a new Swift file, a new xcodeproj reference — produces a broken wheel. The developer doesn't see it because their dev layout finds `runner/` directly; the user sees it because the installed wheel only has `runner_source/`.

### v14 Layout (Clean)

```
runner/                          ← promoted to a proper Python package
  __init__.py                    ← NEW: empty, makes runner/ a package
  Sources/
  SpecterQARunner.xcodeproj/
  HostApp/
  build.sh
  launch.sh
  Package.swift
```

**Deletions:**

- `src/specterqa/ios/runner_source/` — entire directory deleted
- `setup.py` — deleted (the `build_py` override is the only reason it exists)
- `[tool.setuptools.package-data]` glob list in `pyproject.toml` — deleted
- `recursive-include specterqa/ios/runner_source` in `MANIFEST.in` — deleted

**pyproject.toml after restructure:**

```toml
[tool.setuptools.packages.find]
where = ["src", "."]
include = ["specterqa*", "runner*"]

[tool.setuptools.package-data]
"runner" = [
    "Sources/*.swift",
    "Sources/Routes/*.swift",
    "Sources/SpecterQARunner-Bridging-Header.h",
    "Sources/SpecterQASwizzler.h",
    "Sources/SpecterQASwizzler.m",
    "HostApp/**",
    "SpecterQARunner.xcodeproj/project.pbxproj",
    "build.sh",
    "launch.sh",
    "Package.swift",
]
```

`_runner_source_dir()` in `session_manager.py` is updated to resolve via `importlib.resources` against the `runner` package. Both dev and installed-wheel layouts resolve to the same path. The B1 class of bugs is structurally eliminated.

**`verify-wheel` CI job** runs `python -m build --wheel`, installs into a fresh venv, and executes `specterqa-ios runner build` against a booted simulator. This job must pass before any PyPI publish step runs.

---

## 7. Five New MCP Tools — AI Debugging Loop

These five tools reduce the AI debugging loop cycle time from 35–50s to under 5s per iteration. They are additive — they do not replace any CI replay tooling.

### 7.1 `ios_app_relaunch`

**Purpose:** Kill and relaunch the user's app without touching the XCTest runner. The runner HTTP server stays alive on its port. This is the critical path for the AI debugging loop — currently an agent must call `ios_stop_session`, then `ios_start_session` (35–50s) just to pick up a new build of the app.

**Signature:**
```python
ios_app_relaunch(bundle_id: str) -> dict
# Returns: {"bundle_id": str, "launch_pid": int, "elapsed_ms": int}
```

**What it replaces:** The 4-call sequence `ios_stop_session → wait → ios_start_session → ios_wait_idle` (35–50s total). Target elapsed: < 2s.

**Implementation:** `xcrun simctl terminate <udid> <bundle_id>` + `xcrun simctl launch <udid> <bundle_id>`. No xcodebuild involved.

**Example:**
```
# Agent has just rebuilt MyApp.app and copied it to the sim
ios_app_relaunch(bundle_id="com.example.MyApp")
# → {"bundle_id": "com.example.MyApp", "launch_pid": 12345, "elapsed_ms": 980}
```

---

### 7.2 `ios_logs_tail`

**Purpose:** Return only logs that have appeared since the last call to this tool (per session). Agents currently call `ios_logs` repeatedly and manually diff the output. This produces O(n) log volume per agent turn with no correlation to the action just taken.

**Signature:**
```python
ios_logs_tail(
    since_last_call: bool = True,
    level: str = "all",          # "debug" | "info" | "error" | "all"
    category: str | None = None,
    limit: int = 200,
) -> dict
# Returns: {"entries": list[LogEntry], "count": int, "cursor_advanced": bool}
```

**What it replaces:** Repeated full `ios_logs` calls + manual windowing. The tool maintains a per-session monotonic cursor (log sequence number or timestamp). `since_last_call=True` is the default; `since_last_call=False` returns the full recent buffer.

**Example:**
```
ios_tap(label="Submit")
ios_logs_tail()
# → {"entries": [{"level": "error", "message": "NetworkError: timeout", ...}], "count": 3}
```

---

### 7.3 `ios_capture_state`

**Purpose:** Bundle screenshot + element tree + recent logs + basic perf snapshot into one MCP return. Reduces 4 sequential tool calls (screenshot, elements, logs, perf) to 1. Cuts agent turn count and reduces total round-trip time.

**Signature:**
```python
ios_capture_state(
    include_screenshot: bool = True,
    include_elements: bool = True,
    include_logs: bool = True,
    include_perf: bool = False,
    log_tail_lines: int = 50,
) -> dict
# Returns: {"screenshot": base64 | None, "elements": list, "logs": list, "perf": dict | None, "captured_at": str}
```

**What it replaces:** 4 separate calls: `ios_screenshot`, `ios_elements`, `ios_logs`, `ios_perf`. Particularly useful at the start of each debugging iteration to get full situational awareness in one round-trip.

**Example:**
```
state = ios_capture_state(include_perf=True)
# → {screenshot: "...", elements: [...], logs: [...], perf: {memory_mb: 142, cpu_pct: 3.2}}
```

---

### 7.4 `ios_action_with_logs`

**Purpose:** Execute a single interaction action and atomically return the logs that fired during that action. The agent no longer has to manually time log windows around actions. Eliminates the "did this log come from before or after the tap?" problem.

**Signature:**
```python
ios_action_with_logs(
    action: dict,                # Same schema as individual action tools
    log_window_ms: int = 2000,   # How long to collect logs after the action
    level: str = "all",
) -> dict
# Returns: {"action_result": dict, "logs": list[LogEntry], "log_count": int}
```

**Supported action types in `action` dict:** `tap`, `long_press`, `type`, `swipe`, `press_key`, `swipe_back`. Schema mirrors existing MCP tool arguments.

**What it replaces:** Manual sequence of `ios_tap` + `ios_wait(1)` + `ios_logs`. Log collection is guaranteed to cover the action's response window without agent-side timing logic.

**Example:**
```python
ios_action_with_logs(
    action={"type": "tap", "label": "Login"},
    log_window_ms=3000
)
# → {"action_result": {"success": true}, "logs": [{"message": "auth: token issued", ...}], "log_count": 5}
```

---

### 7.5 `ios_promote_session_to_test` — KILLER FEATURE

**Purpose:** Save the current live debugging session as a named replay artifact — instant regression test creation. An agent that has just debugged a bug and confirmed the fix can call this tool to capture the exact interaction sequence as a replay YAML. The next CI run will exercise this exact flow as a regression test.

This closes the loop between AI debugging and CI replay: every debugging session is one call away from becoming a permanent test. No manual YAML authoring. No `ios_start_recording` → walk flow → `ios_stop_recording` ceremony required.

**Signature:**
```python
ios_promote_session_to_test(
    name: str,                   # Replay file name (without .yaml)
    description: str = "",       # Human-readable test description
    validate: bool = True,       # Run ios_validate_replay immediately
) -> dict
# Returns: {"replay_path": str, "step_count": int, "validation": dict | None}
```

**What it replaces:** Manual `ios_stop_recording(name=...)` + reviewing the YAML + submitting it to the repo. The tool snapshots the current step buffer (same buffer `ios_stop_recording` reads), writes the YAML, optionally validates it in-place, and returns the path.

**Implementation note:** The step buffer is not cleared by this call. The session continues. The agent can keep debugging and promote again with a different name.

**Example:**
```python
# Agent has just walked through and fixed the login timeout bug
ios_promote_session_to_test(
    name="login_timeout_regression",
    description="Reproduces and validates fix for login timeout on slow network",
    validate=True,
)
# → {"replay_path": "~/.specterqa/replays/login_timeout_regression.yaml",
#    "step_count": 8,
#    "validation": {"passed": true, "steps_validated": 8}}
```

---

## 8. Tool Surface Delta

### Removals

| Tool | Reason | Migration |
|------|--------|-----------|
| `ios_start_runner` | Sim-killer. Launches a competing xcodebuild against an already-running session. Root cause of v13.3.0 regression. | `ios_start_session` handles runner lifecycle automatically. No replacement needed. |
| `ios_stop_runner` | Paired with `ios_start_runner`. Without the start tool, the stop tool has no valid use. Also risks shutting down the runner mid-session. | `ios_stop_session` handles cleanup. No replacement needed. |
| `ios_save_replay` | Deprecated since v13.2.0. `ios_stop_recording(name=...)` is the canonical save path. `ios_promote_session_to_test` supersedes for AI debugging users. | Use `ios_stop_recording(name="my_flow")` or `ios_promote_session_to_test(name="my_flow")`. |

### Additions

| Tool | Purpose |
|------|---------|
| `ios_app_relaunch` | Sub-2s app restart without runner teardown |
| `ios_logs_tail` | Incremental log cursor per session |
| `ios_capture_state` | Bundle screenshot + elements + logs + perf in one call |
| `ios_action_with_logs` | Atomic action + log window |
| `ios_promote_session_to_test` | Live session → replay YAML in one call |

### Net Count

| Version | Count |
|---------|-------|
| v13.2.0 | 38 tools |
| v13.3.0 | 40 tools (+2 broken: start_runner, stop_runner) |
| v14.0.0 | **43 tools** (−3 removed, +5 added, net +3) |

The MCP server's tool-count regression test must be updated to assert 43.

---

## 9. End-to-End CI Dogfood Tests

Two tests mirror real user workflows. Both run on every PR against main.

### 9.1 CI Replay Dogfood

```
tests/e2e/test_ci_replay_dogfood.py
```

Steps:
1. `pip install specterqa-ios==<version> --no-cache-dir` from PyPI into a fresh venv (or from the local wheel during development)
2. Boot a named simulator (iPhone 15, iOS 17 target)
3. Call `ios_start_session` → `ios_start_recording`
4. Walk TestKitApp: tap 3 buttons, assert element states
5. Call `ios_stop_recording(name="ci_dogfood_flow")`
6. Call `ios_validate_replay(name="ci_dogfood_flow")` — assert all steps PASS
7. Call `ios_replay(name="ci_dogfood_flow")` — assert all steps PASS, elapsed < 60s
8. `ios_stop_session`

Pass criteria: all replay steps pass, no sim shutdown during the run, replay YAML written to disk.

### 9.2 AI Debugging Dogfood

```
tests/e2e/test_ai_debugging_dogfood.py
```

Steps:
1. `pip install` (same as above)
2. Boot sim, `ios_start_session` against TestKitApp
3. Walk through 3 screens using `ios_action_with_logs` for each action — assert logs returned are non-empty
4. `ios_capture_state()` — assert all four payload keys present
5. `ios_app_relaunch(bundle_id="com.synctek.TestKitApp")` — assert elapsed_ms < 3000, sim still booted
6. Walk 3 screens again via `ios_action_with_logs`
7. `ios_logs_tail()` — assert returns incremental entries (fewer than a full `ios_logs` call would return)
8. `ios_promote_session_to_test(name="ai_debug_dogfood", validate=True)` — assert validation passes
9. Confirm resulting YAML is a valid replay (run `ios_validate_replay`)
10. `ios_stop_session`

Pass criteria: `ios_app_relaunch` under 3s, all new tools return non-error responses, promoted replay validates cleanly, sim never shuts down unexpectedly.

---

## 10. Version Bump Justification

v14.0.0 (not v13.4.0 or v13.3.1) for three independent reasons, any one of which would justify a major version:

1. **Breaking change — MCP tool removals.** `ios_start_runner`, `ios_stop_runner`, and `ios_save_replay` are removed. Any caller depending on these tools receives a "tool not found" error after upgrading. Per SemVer, removal of public API is a major version increment.

2. **Internal architecture overhaul.** `RunnerProcess` replaces all three parallel deploy paths. While this is not a public API change, it invalidates any integrations that monkey-patched or subclassed `TestSession._deploy_runner` or relied on the module-level `_active_runners` dict.

3. **Wheel restructure changes internal import paths.** `specterqa.ios.runner_source` package is deleted. Any user code that imported from it (unlikely but possible for advanced integrators) will break.

The "consolidation release" narrative is accurate: v14 is the release where the tool stabilizes its architecture. Future minor versions (14.1, 14.2) add capabilities without structural churn.

---

## 11. Phased Rollout

### Phase 1 — v14.0.0-alpha.1

**Scope:**
- Implement `RunnerProcess` class (`runner_process.py`)
- Refactor `TestSession._deploy_runner()` to use it
- Refactor `handle_start_session` inline deploy to use it
- Delete `handle_start_runner`, `handle_stop_runner`, `_active_runners` dict
- Remove `ios_start_runner`, `ios_stop_runner`, `ios_save_replay` MCP registrations
- All three existing parallel paths gone

**Gate:** Maurice dogfoods v14.0.0-alpha.1 against TestKitApp. Requirements:
- `ios_start_session` → record flow → `ios_stop_recording` → `ios_replay` works end-to-end
- Simulator is NOT shut down at any point during the session
- `ios_doctor` reports runner healthy

**Do NOT proceed to Phase 2 until dogfood passes.**

### Phase 2 — v14.0.0-beta.1

**Scope:**
- Implement 5 new MCP tools (Section 7)
- Wheel restructure: add `runner/__init__.py`, delete `runner_source/`, delete `setup.py`, update `pyproject.toml`
- Update `_runner_source_dir()` to use `importlib.resources` against `runner` package
- `verify-wheel` CI job wired to publish.yml

**Gate:** Maurice dogfoods AI debugging loop against TestKitApp:
- `ios_app_relaunch` cycles < 3s
- `ios_logs_tail` returns incremental entries
- `ios_promote_session_to_test` produces a valid replay YAML
- `specterqa-ios runner build` succeeds from a fresh `pip install` of the beta wheel

**Do NOT proceed to Phase 3 until dogfood passes.**

### Phase 3 — v14.0.0 (Final)

**Scope:**
- Write and pass `test_ci_replay_dogfood.py` + `test_ai_debugging_dogfood.py`
- Update all docs (README tool count, llms.txt, troubleshooting.md, CHANGELOG.md)
- Run `make llms` to sync tool-surface docs; regression test must pass
- PR → QualityAtlas review → merge → tag v14.0.0 → auto-publish → post-publish PyPI verification

---

## 12. Release Gates (Non-Negotiable)

All three phases share the same release gate structure:

| Gate | Requirement |
|------|-------------|
| PR + review | All code on a feature branch; PR reviewed by QualityAtlas before merge |
| Tag + auto-publish | `git tag v14.0.0-alpha.1` triggers `.github/workflows/publish.yml` |
| `verify-wheel` job | Build wheel → fresh-venv install → `specterqa-ios runner build` → must pass before PyPI upload |
| Post-publish verification | `pip install specterqa-ios==X.Y.Z --no-cache-dir` from a fresh venv on a separate machine/env; confirm `runner build` works; confirm new MCP tools exercise against a live booted simulator |
| Live simulator smoke test | Required for every phase release. Local dogfood (dev layout) does NOT satisfy this gate. |

**Local dogfood does NOT count as a release gate.** The B1/B1.5 regression class was caused specifically by a divergence that was invisible in the dev layout and only appeared in an installed wheel. The `verify-wheel` CI job is the structural gate that catches this class of bug. It must run in CI, not locally.

---

## 13. Risks and Mitigations

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| `RunnerProcess.acquire()` lock deadlock on session teardown + concurrent start | Medium | Runner stuck in DEPLOYED forever | Implement lock with timeout (30s); FAILED state on timeout; log clearly |
| `relaunch_app` leaves app in background state instead of foreground | Medium | AI debugging loop gets element tree of previous screen | After `simctl launch`, wait for `ios_app_state` to return "foreground" (max 3s); error if not |
| Wheel restructure breaks `_runner_source_dir()` in installed wheel | High | Every fresh-install user's `runner build` fails (B1 repeat) | `verify-wheel` gate; explicit `importlib.resources.files("runner")` path resolution test in `test_packaging.py` |
| Removing `ios_save_replay` breaks a Example Reader flow that uses it | Low | Prod regression for an actual user | Audit `Example Reader` repo for `ios_save_replay` calls before Phase 1 lands; add deprecation notice in v13.3.1 if needed (but note: v13.3.1 is skipped — include notice in alpha.1 error message: "ios_save_replay removed; use ios_stop_recording(name=...)") |
| `ios_promote_session_to_test` step buffer drift if session was interrupted | Medium | Promoted replay is incomplete | Check step buffer length before promote; error if < 2 steps; add `force=True` override |
| Concurrent AX + XCTest session requests after RunnerProcess lands | Low | RunnerProcess allocated for wrong backend | `RunnerProcess.acquire()` keyed on (udid, port, backend); AX sessions never touch RunnerProcess |

---

## 14. Open Questions for Chairman

The following decisions require explicit Chairman input before Phase 1 implementation begins. No code is written on these points until resolved.

**OQ-1: RunnerProcess API shape — concurrent session semantics**

The current design has one `RunnerProcess` instance per (udid, port). If two MCP clients call `ios_start_session` concurrently against the same sim, both receive the same `RunnerProcess` instance and share the runner. Is this the intended behavior? Alternative: reject the second call with "sim already in use." This affects multi-agent parallelism scenarios.

**OQ-2: `ios_app_relaunch` — does it need to reinstall the app binary?**

The current design uses `simctl terminate` + `simctl launch` (fast, sub-2s). The AI debugging loop case where the developer has just rebuilt the app and wants to test the new binary requires `simctl install <path>` first. Should `ios_app_relaunch` accept an optional `app_path` parameter to reinstall before relaunching? If yes, the sub-2s target applies only when `app_path` is None.

**OQ-3: `ios_promote_session_to_test` — replay save location**

Promoted replays default to `~/.specterqa/replays/<name>.yaml`. For the regression-test use case to work, the file needs to land in the repo (e.g., `tests/replays/<name>.yaml`). Should the tool accept a `save_dir` parameter? Or should it always save to `~/.specterqa/replays/` and require the user to commit the file manually?

**OQ-4: `ios_save_replay` removal — Example Reader integration check**

Before removing `ios_save_replay`, confirm no live Example Reader flows call it. If Example Reader is calling it, we need a migration window. v13.3.1 hotfix was skipped — if Example Reader uses `ios_save_replay`, we either (a) add a v13.3.1 deprecation-only release that prints a warning but still works, or (b) accept the break in alpha.1. Chairman decides.

**OQ-5: Tool count target (43) — confirm net count**

The current MCP server has tools registered in a flat list. Before Phase 3 final, confirm the actual count via `make llms` regeneration. The 43 figure is derived as: 40 (v13.3.0) − 3 removed + 5 added = 42, not 43. Recount required against the actual server registration. Locking in 43 now may require adjusting the regression test target.

---

*End of v14.0.0 Design Document.*
