# SimDrive Coverage Ratchet

**Status:** Active
**Owner:** INIT-2026-549 (W1 floor, W2 wire-up, W3 climb to 80, W4 push to 85)
**Last updated:** 2026-05-21

## Policy

Coverage on the hot-path modules is governed by a **ratchet floor** in
`.github/workflows/simdrive-ci.yml` (`--cov-fail-under`). The floor only ever
moves **up** — never down. As tests are added and the aggregate climbs, the
floor is raised in stages so the gain cannot regress.

The long-term target is **85%** aggregate. Hit **2026-05-21** in
INIT-2026-549 Wave 4 (`test/coverage-server-85`) — hot-path measured
**92%** (well above 85%); overall package coverage **86%**.

## Current floor

| Floor    | Set in                          | Date       |
|----------|---------------------------------|------------|
| 65%      | INIT-2026-549 W1                | 2026-05-17 |
| 80%      | INIT-2026-549 W3                | 2026-05-20 |
| **90%**  | INIT-2026-549 W4                | 2026-05-21 |

Hot-path aggregate measured at floor-set time: **92%** (local run).
Overall package coverage at floor-set time: **86%**.
Floor set 2pp below hot-path measured so a small flake doesn't break CI.

## Per-module status after Wave 4

| Module                | W1 baseline | W3 measured | W4 measured | Target |
|-----------------------|-------------|-------------|-------------|--------|
| `simdrive.sim`        | 38%         | 100%        | **100%**    | 85%    |
| `simdrive.act`        | 42%         | 100%        | **100%**    | 85%    |
| `simdrive.session`    | 79%         | 100%        | **100%**    | 85%    |
| `simdrive.observe`    | 77%         | 97%         | **97%**     | 85%    |
| `simdrive.device`     | 67%         | 94%         | **94%**     | 85%    |
| `simdrive.recorder`   | 76%         | 85%         | **87%**     | 85%    |
| `simdrive.server`     | 66%         | 70%         | **94%**     | 85%    |
| **Hot-path aggregate**| 67.18%      | 82%         | **92%**     | 85%    |
| **Overall package**   | —           | 82%         | **86%**     | 85%    |

`simdrive.server` jumped 70 → **94%** in W4 by adding `test_server_coverage_85.py`
(150 tests covering per-tool handlers via `act`/`sim`/`wda` mocks, CLI subcommand
entries via direct in-process `_cmd_*` calls + subprocess flag dispatch, and
`_resolve_target_xy` / `_mark_center` / `_resolve_bundle_id` error paths).

The remaining ~6% in server.py (lines 175, 412, 567-569, 832-833, 836, 855,
1410-1434, 2143, 2171, 2188-2228, 2283-2294, 2330, 2736-2737, 2746) is:
- `_serve_async` MCP stdio dispatcher (2188-2228) — only reachable when an
  actual MCP client is connected over stdio; deliberately uncovered.
- `tool_run_journey` happy-path body (1410-1434) — requires a live MCP
  ServerSession with `sampling/createMessage` support; reached only via the
  Claude Code / Cline MCP host. Error path is covered.
- ModuleNotFoundError branch for the `anthropic` optional extra (2283-2294)
  — only triggers when the `[claude]` extra is not installed; CI has it.

These are intentional uncovered-in-tests lines, not test debt.

## Climb history

- W1 (2026-05-17): set 65% floor; foundation.
- W2: wired pytest-cov into CI; floor unchanged.
- W3 (2026-05-20): climbed to 82% aggregate; raised floor to 80%.
- **W4 (2026-05-21):** climbed to 92% hot-path / 86% overall; raised floor to 90%.

## Future work — sustain 85%+

Realistic next gains, in order of marginal impact:

1. `simdrive.wda.bootstrap` (71%) — large module, much of the missing
   coverage is xcodebuild / devicectl subprocess paths that need a real
   device. Plan: keep at current level; mark as "real-device only" tests.
2. `simdrive.cloud.middleware.quotas` (53%) — cloud-side enforcement
   paths; could be lifted with a moto-style stub of the cloud routes.
3. `simdrive.diagnostics` (72%) — many shell-out paths. Tractable with
   subprocess mocks similar to perf/server tests in this PR.
4. `simdrive.hid_inject` (47%) — small file, mostly shell-out. Lift with
   subprocess mocks; trivial gain (~5 lines).

## Rules

- The floor in `simdrive-ci.yml` is the **only** authoritative number. Update
  this doc whenever the floor changes.
- **The floor only ratchets up.** A PR that lowers `--cov-fail-under` requires
  an explicit ratchet-rationale section in its description and CEO sign-off.
- New hot-path modules added to the `--cov=` list inherit the same ratchet
  policy: ship at current floor or above, then climb.
- Per-module floors (`fail_under` in `.coveragerc` per-file) are out of scope;
  the aggregate gate is sufficient guardrail.
- **No `pytest.mark.skip` to game coverage.** Every test must do real work.

## References

- INIT-2026-549 — SimDrive coverage initiative (W1 → W4)
- W4 PR: `test/coverage-server-85` (this branch)
- Failing CI run that motivated the original ratchet: `25982013410`
- Memory: `feedback_pr_whack_a_mole_test_debt` — test debt is its own initiative
