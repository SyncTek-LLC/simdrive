# SpecterQA v16.0.0 — Vision-First Redirection Handoff

**Branch:** `feat/v16.0.0-vision-first` (off `main` at v15.2.0)
**Status:** Phase A landed. Phases B–F pending. WIP — no PyPI release yet.
**Strategic basis:** Maurice's `.specterqa/dogfood/v15.2.0-direction-proposal-maurice.md`.

This is a **wholeshot pivot**, not a 5-phase migration. There's no real
consumer cohort outside Palace + BusinessAtlas dogfood that depends on the
v15.x AX-tree selector path. Every iOS major has shipped SwiftUI/AX changes
that broke us; the AX layer is doing negative work for vision-capable
agents who already see the screen better than the tree describes it. v16
deletes the layer.

---

## What landed in Phase A

### Vision-first primitives (new tools, additive)

- `ios_observe` — `handle_observe` in `src/specterqa/ios/mcp/server.py`.
  Returns `{screenshot, device_w, device_h, reliable_targets, app_state, captured_at}`.
  `reliable_targets` filters to elements with explicit `accessibilityIdentifier` only.
- `ios_act` — `handle_act` in same file. Single dispatcher for
  `tap/type/swipe/key/scroll/long_press/drag`. Coordinate-primary; identifier
  permitted on tap/long_press; `normalized=true` for resolution-independent coords.
- `UIElement.identifier` field added to `src/specterqa/ios/som_annotator.py` so
  `parse_elements_from_json` populates it from the runner JSON.

### Defense-in-depth (carry-forward from v15.2.1 patches that didn't ship)

- `runner/Sources/SpecterQAObjCBridge.{h,m}` — Swift-callable `@try`/`@catch` shim.
- `runner/Sources/HTTPServer.swift` — `runOnMain` wraps the dispatched block in
  the bridge; uncaught NSException becomes a logged error instead of killing the
  test method.
- `runner/SpecterQARunner.xcodeproj/project.pbxproj` — bridge files wired into
  the SpecterQARunner target.
- `runner/Sources/SpecterQARunner-Bridging-Header.h` — imports the bridge.

### Folded-in v15.x work (cherry-picked from feat/mcp-tier-enforcement and feat/sec-high-005-jwt-offline-grace)

- Tier enforcement across MCP tool surface (PR #79's content)
- SEC-HIGH-005 JWT decoder hardening (PR #78's content)
- v16's `ios_observe` and `ios_act` are tier-mapped (`trial`)

### Tests / pins updated

- `tests/test_mcp_tool_registration.py` _EXPECTED_TOOL_COUNT 47 → 49
- `tests/regression/test_mcp_instructions_sync.py` server.py header 47 → 49
- `tests/test_physical_device_optin.py` — `tool_count` field 47 → 49
- 592 unit tests pass, 20 skipped, 0 fail.

### Files NOT yet touched (Phase B work)

- `runner/Sources/SpecterQAElementQuery.swift` (the throw-site file)
- Legacy MCP tools in `server.py`: `ios_screenshot`, `ios_elements`, `ios_tap`,
  `ios_long_press`, `ios_swipe`, `ios_swipe_back`, `ios_type`, `ios_press_key`,
  `ios_dismiss_keyboard`, `ios_wait_idle`, `ios_wait_for_element`,
  `ios_capture_state`, `ios_action_with_logs`
- `runner/Sources/Routes/ElementsRoute.swift`, parts of `TapRoute.swift`,
  `TypeRoute.swift`, `SwipeRoute.swift` that hit `findByLabel` / `findByIdentifier`

---

## Phase B — Demolition (1 working day)

Goal: delete the AX-tree selector layer entirely.

**Swift side:**
1. Delete `runner/Sources/SpecterQAElementQuery.swift`.
2. Audit `runner/Sources/Routes/TapRoute.swift`, `TypeRoute.swift`,
   `SwipeRoute.swift` — remove all paths that call `findByLabel` /
   `findByIdentifier` / `waitForElement` / any `XCUIElementQuery` selector.
   Keep ONLY the coordinate paths.
3. Delete `runner/Sources/Routes/ElementsRoute.swift` (the `/elements` HTTP
   route the legacy `ios_elements` MCP tool fronted) and remove its
   registration in `SpecterQARunner.swift`'s `registerRoutes(...)` call.
4. Update `runner/SpecterQARunner.xcodeproj/project.pbxproj` to drop the
   PBXFileReference / PBXBuildFile entries for the deleted Swift files.
5. Audit `runner/Sources/AccessibilityTree.swift` — delete if it's only
   used by the selector layer; keep if `ios_observe` still pulls element
   metadata through it (it currently does via `som_annotator`, which uses
   the runner `/source` endpoint — that endpoint stays).

**Python side:**
1. Delete the legacy MCP tool definitions in `server.py`:
   - `ios_screenshot` (replaced by `ios_observe`)
   - `ios_elements` (folded into `ios_observe.reliable_targets`)
   - `ios_tap`, `ios_long_press`, `ios_swipe`, `ios_swipe_back`, `ios_type`,
     `ios_press_key`, `ios_dismiss_keyboard` (replaced by `ios_act`)
   - `ios_wait_idle`, `ios_wait_for_element` (agent loops on `ios_observe` instead)
   - `ios_capture_state` (folded into `ios_observe`; agent calls `ios_logs_tail`/`ios_perf`/etc separately)
   - `ios_action_with_logs` (composed: `ios_act` + `ios_logs_tail`)
2. Delete the corresponding `handle_*` functions and helpers.
3. Update `tier_gate.py`'s `TOOL_TIER_MAP` to drop the deleted entries.
4. Bump `_EXPECTED_TOOL_COUNT` and the header count to whatever the new total is
   (likely ~22).
5. Run unit sweep — many tests will fail. Delete tests that exercise the
   deleted tools; preserve tests that test out-of-band telemetry (logs/perf/etc.)
   and the new primitives.

**Live verification after Phase B:**
- Build runner (it should build clean — no references to SpecterQAElementQuery).
- Live deploy + ios_observe + ios_act on iPhone 17 Pro / iOS 26.2.
- Run a coord-only auth journey (Maurice's exact flow): observe → tap → observe
  → tap → ... for 11 sequential taps. Pre-v16 this required manual coord
  fallback after AX crashed. Under v16 it's the only path and should be solid.

---

## Phase C — Replay rewrite (3–5 working days)

Goal: replay engine that's coord + visual-diff, not selector + element-existence.

**Schema:**
```yaml
replay:
  name: a1qa_signin
  device:
    width: 402
    height: 874
  steps:
    - kind: observe
      capture: signin_form_state    # named visual reference
    - kind: act
      action: {kind: tap, x: 0.503, y: 0.385}      # normalized coords
    - kind: assert_visual
      reference: signin_form_state
      threshold: 0.92                              # SSIM threshold
      region: [0, 0.4, 1.0, 0.6]                   # only diff form region
      mode: ssim                                   # or "perceptual_hash"
```

**Implementation:**
1. New module `src/specterqa/ios/replay_v2.py` (don't break `replay.py` until
   Phase E migration tool runs).
2. `ReplayExecutor` walks steps, calls `handle_observe` / `handle_act` for each.
3. `assert_visual` compares the captured screenshot to the named reference PNG
   using SSIM. Reference PNGs live alongside the YAML (`<replay>.refs/`).
4. `ios_replay` MCP tool dispatches to v2 when YAML schema declares
   `version: 2`; legacy schema returns a clear "migrate via specterqa-ios
   replay migrate <file>" error.
5. Use `pillow` + `scikit-image.metrics.structural_similarity` for SSIM.

**Threshold tuning:** ship with default 0.90, expose per-step override. Iterate
from real Palace usage.

---

## Phase D — Recording rewrite (1–2 working days)

Goal: `ios_start_recording` / `ios_stop_recording` capture screenshots + coord
taps, output v2 schema YAML.

**Implementation:**
1. `ReplayRecorder` (existing in `src/specterqa/ios/replay.py`) gains a v2 mode.
2. On every `handle_act` call, append a step + capture a reference screenshot.
3. Optional `include_ocr=True` runs macOS Vision framework OCR on the area
   around the tap coordinate to produce a human-readable comment.
4. On stop, write YAML + reference PNG directory.

---

## Phase E — Tests, README, migration guide

1. New live integration tests: `tests/integration/test_observe_act_live.py`,
   `tests/integration/test_replay_v2_live.py`. Run on iPhone 17 Pro / iOS 26.2.
2. README rewrite — vision-first model section, migration table, deletion list.
3. Migration guide: `docs/MIGRATING-TO-V16.md` — step-by-step for any consumer
   on v15.x label-based tools to translate to coord-based.
4. CHANGELOG fully populated for the v16.0.0 release entry (currently in-progress).
5. CLI `specterqa-ios replay migrate <yaml>` — converts a v1 replay to v2 by
   running it once, capturing screenshots, replacing `expect_elements` with
   `assert_visual` references.

---

## Phase F — Ship

1. Live verification matrix:
   - iPhone 12 / iOS 26.0
   - iPhone 17 Pro / iOS 26.2
   - iPhone 16 Pro / iOS 18.4
   Run the full Palace auth journey against each. Zero crashes required.
2. Push branch, open PR (squash-merge will collapse the v16 work into one
   commit on main).
3. QualityAtlas certification — focus on: deletion completeness (no dangling
   selector references), test theater check on the new live tests, breaking-change
   doc clarity.
4. Chairman merge auth.
5. DeployAtlas: bump pyproject 15.2.0 → 16.0.0, tag v16.0.0, push, monitor
   `publish.yml`, fresh-venv dogfood install, deployment_record at
   `CompanyState/deployments/records/specterqa-ios.jsonl`.
6. Maurice / Palace re-runs A1QA on v16.0.0 — confirms the AX-crash class is
   gone and `ios_act` handles his flow cleanly.

---

## Open questions to revisit at Phase E/F

1. **License tier for `ios_observe` / `ios_act`** — currently both `trial`.
   Reconsider: should `ios_observe` stay trial (vision-capable agents need it)
   while `ios_act` requires `indie+` (input is the revenue gate)? Maurice's
   spec says no tier on the primitives; revenue gating is on persistence
   (replay/recording). Probably correct.
2. **`include_legacy_elements=True` flag on `ios_observe`** — keep in v16.0.0
   for transition? Or drop immediately because we're wholeshot? Currently
   kept; can drop in v16.1 once Maurice confirms he doesn't use it.
3. **Visual-diff library choice.** scikit-image is heavy; `pillow` alone gives
   us pixel-diff + perceptual hash but not SSIM. Decide before Phase C — start
   with scikit-image, profile, swap if it bloats the wheel unacceptably.

---

## What changed in this session that informs Phase B

- The ObjC bridge wraps `runOnMain` defense-in-depth. After Phase B deletes
  the dominant XCTest throw site (XCUIElementQuery selector layer), the bridge
  stays — XCUICoordinate / snapshot / screenshot APIs can still throw on rare
  iOS bugs. Don't delete the bridge during Phase B.
- The cherry-picked tier enforcement (PR #79 content) and SEC-HIGH-005 (PR #78
  content) are orthogonal to vision-first. They land cleanly in v16 with no
  changes needed.
- Dogfood docs persisted at `.specterqa/dogfood/v15.1.0-maurice.md`,
  `.specterqa/dogfood/v15.2.0-direction-proposal-maurice.md`, and
  `.specterqa/dogfood/v15.2.0-runner-stability-patch-maurice.md`. Phase B
  agent should read both v15.2.0 docs before touching the runner.

---

## Tasks tracker

| Phase | Status |
|---|---|
| A — vision-first primitives + ObjC bridge + folded PRs + tests pinned | **DONE** |
| B — demolition | pending |
| C — replay v2 | pending |
| D — recording v2 | pending |
| E — tests + README + migration guide | pending |
| F — PR + QA + DeployAtlas | pending |

Phase A is committed on `feat/v16.0.0-vision-first`. Continue from there.
