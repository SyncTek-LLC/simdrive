# SimDrive Coverage Ratchet

**Status:** Active
**Owner:** INIT-2026-549 (W1 floor, W2 climb)
**Last updated:** 2026-05-17

## Policy

Coverage on the hot-path modules is governed by a **ratchet floor** in
`.github/workflows/simdrive-ci.yml` (`--cov-fail-under`). The floor only ever
moves **up** — never down. As tests are added and the aggregate climbs, the
floor is raised in stages so the gain cannot regress.

The long-term target is **80%** aggregate on the hot-path modules. We chose a
staged climb (rather than a single 80% gate) because writing 13 percentage
points of test debt in the foundation PR would balloon scope and violate the
"test debt deserves its own initiative" rule.

## Current floor

| Floor    | Set in                          | Date       |
|----------|---------------------------------|------------|
| **65%**  | INIT-2026-549 W1                | 2026-05-17 |

Aggregate measured at floor-set time: **67.18%** (CI run `25982013410`).

## Per-module baseline (CI run 25982013410)

| Module                | Today | Target | Gap   |
|-----------------------|-------|--------|-------|
| `simdrive.sim`        | 38%   | 80%    | -42pp |
| `simdrive.act`        | 42%   | 80%    | -38pp |
| `simdrive.server`     | 66%   | 80%    | -14pp |
| `simdrive.device`     | 67%   | 80%    | -13pp |
| `simdrive.recorder`   | 76%   | 80%    |  -4pp |
| `simdrive.observe`    | 77%   | 80%    |  -3pp |
| `simdrive.session`    | 79%   | 80%    |  -1pp |
| **Aggregate**         | 67.18% | 80%   | -12.82pp |

## Planned climb

The W2 follow-up initiative will land tests in this order (biggest gap first,
since that's where added tests have the highest marginal impact on the
aggregate):

1. `simdrive.sim` 38 -> 70
2. `simdrive.act` 42 -> 70
3. `simdrive.server` 66 -> 80
4. `simdrive.device` 67 -> 80
5. Raise floor to **75%** once aggregate sustainably >= 77%.
6. Close remaining gaps on `recorder` / `observe` / `session`.
7. Raise floor to **80%** once aggregate sustainably >= 82%.

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
