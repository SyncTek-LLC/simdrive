# simdrive Release Checklist

**This document is the authoritative pre-publish checklist.** It exists because
1.0.0b2 and 1.0.0b3 both shipped to PyPI in a broken state — fresh
`pip install simdrive` failed at first import — and the CI smoke test masked
the failure with a permissive `||` fallback. The checklist below is now
enforced by workflow gates, not just goodwill.

Every entry here either has a CI gate that fires on PR, or a manual step
called out by name. If you change the release shape, update this doc in the
same PR.

---

## Pre-merge gates (every PR to `main`)

These run automatically on every PR. **Do not merge if any are red.**

| Gate | Workflow | What it catches |
|---|---|---|
| Unit + non-live tests | `simdrive-ci.yml` | logic regressions |
| Coverage ≥ 90% on hot-path modules | `simdrive-ci.yml` (`--cov-fail-under=90`) | coverage drift |
| Cleanroom install smoke | `cleanroom-install.yml` | dep-graph leaks (e.g. fastapi b3 regression) |
| pip-audit HIGH-severity CVE block | `security.yml` | dependency CVEs |
| CodeQL python + actions | `codeql.yml` | static analysis findings |
| `[INIT-…]` reference in commits | `ba-pr-validate.yml` | governance |
| Native HID helper builds (universal2) | `simdrive-ci.yml` | binary regressions |

**Why these specifically:** they reflect the failure modes we have actually shipped. Every gate maps to a real incident; we do not add gates speculatively.

---

## Pre-publish gates (tag push `simdrive-v*`)

These run automatically when a `simdrive-v*.*.*[a|b|rc]*` tag is pushed and gate the PyPI upload itself. Workflow: `specterqa-ios-publish.yml`.

1. **Version match** — `git tag == simdrive-v<pyproject.version>`. Prevents the "tag says b4 but pyproject says b3" mismatch.
2. **CHANGELOG head** — the first `## [X.Y.Z]` heading in `CHANGELOG.md` matches `pyproject.version`. Prevents shipping without release notes.
3. **Full non-live pytest** — every test in `simdrive/tests` with `-m "not live"` passes.
4. **Build wheel + sdist** — both build cleanly with `python -m build`. universal2 native binary is verified.
5. **Fresh-venv install smoke** — three explicit checks (no `||` fallback):
   - `simdrive --version` exits 0
   - `import simdrive.server` succeeds
   - tool registry has ≥ 33 tools and includes the critical set

If any pre-publish gate fails, the publish job **does not run**.

---

## Manual checklist before tagging

Before pushing the `simdrive-v*` tag — even if all PRs are merged green — do:

- [ ] **Read open issues** for the previous release tag. The Example Reader team logs dogfood-found bugs against the current beta. Surface anything filed in the last 7 days; address blockers or document deferrals in the release notes.
- [ ] **Pull main + verify the merge commit is the one you expect.** Don't tag a commit you haven't read.
- [ ] **`pyproject.toml` version bumped + CHANGELOG entry has matching heading.** The pre-publish gate catches this, but verifying locally first saves CI minutes.
- [ ] **CHANGELOG entry is honest.** Specifically:
  - List every bug closed in this version (link to the issue if filed)
  - Call out any deferred work (`What's NOT in this PR` section)
  - For hotfixes, include an upgrade note for users on the broken version
- [ ] **Run the cleanroom smoke locally** — same command CI runs:
  ```bash
  python -m venv /tmp/release-smoke
  /tmp/release-smoke/bin/pip install ./simdrive
  /tmp/release-smoke/bin/simdrive --version
  /tmp/release-smoke/bin/python -c "from simdrive import server; print(len(server.list_tools()))"
  ```
  If this fails locally, the publish workflow will fail too — but with a 5-minute round-trip.

---

## Post-publish verification (within 10 minutes of tag push)

After the `Publish to PyPI` workflow reports success:

- [ ] **Check the PyPI page** — `https://pypi.org/project/simdrive/<version>/` exists and shows the new version
- [ ] **Verify install from PyPI** — in a clean venv on your laptop:
  ```bash
  python -m venv /tmp/pypi-verify && source /tmp/pypi-verify/bin/activate
  pip install simdrive==<version>
  simdrive --version
  ```
  This is the exact command a customer runs. If it fails, **yank the release immediately** (see below).

---

## How to yank a broken release

If a published version is broken (b2/b3 fastapi regression class):

1. Log into pypi.org with the SyncTek-LLC maintainer account
2. Navigate to the simdrive project → Releases → click the broken version
3. Click **Yank release** and enter a brief reason
4. Yanking does NOT delete the wheel (existing installations with pinned versions keep working), but it prevents new `pip install simdrive` from picking it up by default

PyPI's OIDC Trusted Publisher token can ONLY upload, not yank. Yanking requires a maintainer-scoped API token or the web UI. There is no automation for this — by design, since accidental yanks are reputationally costly.

After yanking, publish a fixed version with a higher version number and write a CHANGELOG entry that includes the yank note + upgrade path.

---

## What we learned (Example Reader b3 dogfood post-mortem, 2026-05-22)

Four bugs surfaced in b3 dogfood that should have been caught earlier:

- **F-B3-007** `fastapi` ModuleNotFoundError on fresh install (b2 + b3 both shipped broken). Caused by the pre-publish smoke test using `simdrive --version || python -c "import simdrive; ..."` — the bare `import simdrive` runs `__init__.py` only and works fine, masking the failure of the real import path. **Fix:** rebuilt the smoke as three explicit checks with no fallback; added `cleanroom-install.yml` as a per-PR gate.
- **F-B3-009** `clear_field` did not emit a recording step → silent divergence on replay. **Fix:** added `_record_act_step` calls on both branches; new test pins this in `test_b4_example_dogfood_fixes.py`.
- **F-B3-010** `tap_and_wait_keyboard` serialized as bare `tap` → wait semantic stripped on replay. **Fix:** added `Recorder.upgrade_step_action()` and call from the composite tool.
- **F-B3-011** `type_text` returned `keyboard_visible: false` when dispatch succeeded → agents would retry into a successfully-typed field. **Fix:** added `keyboard_visible_reason` field with explicit guidance not to retry on `dispatch_succeeded=true`.

Root cause across all four: insufficient pre-publish exercise of the actual user surface. The lesson lives here so the next person who reviews the release process sees these incidents, not just the abstract gates.

---

## Where the version numbers live

If you change one of these, change all of them in the same PR:

- `simdrive/pyproject.toml` line 7: `version = "..."`
- `simdrive/CHANGELOG.md` first `## [X.Y.Z] — YYYY-MM-DD` heading
- Git tag pattern: `simdrive-v<version>` (no `v` prefix in pyproject, with `v` in tag)
- `simdrive/src/simdrive/__init__.py` reads version from package metadata at runtime — no manual change needed

A linter (`tests/packaging/test_publish_gates.py`) verifies these match before the publish workflow runs.

---

## Coverage policy

- **Hot-path floor: 90%** on the modules listed in `simdrive-ci.yml`'s `--cov=` flags (`server`, `session`, `recorder`, `observe`, `act`, `sim`, `device`).
- **Overall floor: track but do not gate** — overall is currently ~86% and dragged by `som.py`, `diagnostics.py`, `robustness.py`. These are tracked in `docs/COVERAGE_RATCHET.md`.
- **Raise the floor** when adding tests pushes coverage materially above the current threshold (rule: floor = floor(measured − 2)). This is in CI workflow comments where it's enforced.

---

## Adding a new MCP tool — paywall checklist

Every new MCP tool must:

1. **Call `_entitlement_gate()` at its first line.** Skip and the test below fires.
2. **Register in `_TOOLS` and `_SUBCOMMANDS` (if it has a CLI surface)** with a name and inputSchema.
3. **Update `EXPECTED_TOOL_COUNT` and `GATED_TOOLS` in `tests/test_paywall_gates.py`.** The pinned list fires if you skip this.
4. **Update `tests/test_unit.py::test_tool_names_match_spec`** to include the new name.
5. **Update `docs/MCP_TOOL_SURFACE.md`** with a one-line description.
6. **Add unit tests in a dedicated file or extend an existing one.**

The three pin tests (`test_tool_count_pinned_at_N`, `test_pinned_gated_list_matches_registry`, `test_tool_names_match_spec`) form a tripwire: if you add a tool without updating all three, the suite fails immediately.
