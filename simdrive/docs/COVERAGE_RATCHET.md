# SimDrive Coverage Ratchet

**Status:** Active
**Owner:** INIT-2026-549 (W1 floor, W2 wire-up, W3 climb to 80)
**Last updated:** 2026-05-20

## Policy

Coverage on the hot-path modules is governed by a **ratchet floor** in
`.github/workflows/simdrive-ci.yml` (`--cov-fail-under`). The floor only ever
moves **up** — never down. As tests are added and the aggregate climbs, the
floor is raised in stages so the gain cannot regress.

The long-term target is **80%** aggregate on the hot-path modules. Hit
**2026-05-20** in INIT-2026-549 Wave 3 (`hardening/coverage-gate`).

## Current floor

| Floor    | Set in                          | Date       |
|----------|---------------------------------|------------|
| 65%      | INIT-2026-549 W1                | 2026-05-17 |
| **80%**  | INIT-2026-549 W3                | 2026-05-20 |

Aggregate measured at floor-set time: **82%** (hot-path modules, local run).
Floor set 2pp below measured so a small flake doesn't break CI.

## Per-module status after Wave 3

| Module                | W1 baseline | W3 measured | Target |
|-----------------------|-------------|-------------|--------|
| `simdrive.sim`        | 38%         | **100%**    | 80%    |
| `simdrive.act`        | 42%         | **100%**    | 80%    |
| `simdrive.session`    | 79%         | **100%**    | 80%    |
| `simdrive.observe`    | 77%         | **97%**     | 80%    |
| `simdrive.device`     | 67%         | **94%**     | 80%    |
| `simdrive.recorder`   | 76%         | **85%**     | 80%    |
| `simdrive.server`     | 66%         | **70%**     | 80%    |
| **Aggregate**         | 67.18%      | **82%**     | 80%    |

`simdrive.server` did not reach 80% — it's a 1000+ statement module with
substantial per-handler subprocess and live-sim code paths. The Wave 3 tests
added cover the dispatcher (`call_tool`, `call_tool_async`, version drift,
quota wire-up), `tool_clear_field`, `_session_scale`, and `_wda_client_for`.
Pushing server.py to 80% requires either (a) running the actual MCP server
loop in tests, or (b) extensive mocking of every tool handler — both are
out of scope for the ratchet effort and best tackled as their own initiative.

## Planned climb (closed)

W3 closed the climb. The remaining work is per-module — see the "Future
work" section if you want to raise the floor above 80%.

## Future work — push toward 85%

Realistic next gains, in order of marginal impact:

1. `simdrive.server` 70 → 80 (would need a subprocess-mock-heavy suite for
   tap/swipe/observe/dismiss_first_launch_alerts handlers).
2. `simdrive.window` (currently 69%) — small file, easy lift.
3. `simdrive.perf` (currently 44%) — subprocess-heavy but tractable.
4. Raise floor to 82% once aggregate sustainably >= 84%.
5. Raise floor to 85% once aggregate sustainably >= 87%.

## Rules

- The floor in `simdrive-ci.yml` is the **only** authoritative number. Update
  this doc whenever the floor changes.
- **The floor only ratchets up.** A PR that lowers `--cov-fail-under` requires
  an explicit ratchet-rationale section in its description and CEO sign-off.
- New hot-path modules added to the `--cov=` list inherit the same ratchet
  policy: ship at current floor or above, then climb.
- Per-module floors (`fail_under` in `.coveragerc` per-file) are out of scope
  for W1; the aggregate gate is sufficient guardrail until the climb completes.

## References

- INIT-2026-549 — SimDrive W1 foundation (this PR)
- INIT-2026-549 W2 — Hot-path test climb (follow-up, not yet filed)
- Failing CI run that motivated the ratchet: `25982013410`
- Memory: `feedback_pr_whack_a_mole_test_debt` — test debt is its own initiative
