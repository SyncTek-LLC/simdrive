# Feature request: state contract on recordings (capture-time + replay-time)

**Filer:** Example Reader iOS dogfood (Maurice Carrier)
**Date:** 2026-05-11
**simdrive version observed:** 1.0.0a7 (disk: 1.0.0a8 — drift warning)

## The problem in one sentence

Recordings carry **no contract** about what the world looks like at step 0, so a replay against a divergent app state silently executes 20+ blind taps at SSIM 0.014 while reporting `executed: true, error: null` for every step.

## Concrete dogfood evidence (today)

Goal: smoke-test a Example Reader iOS PR that refactored the SAML auth surface. Plan: replay the only SAML recording (`pr907-saml-signin-gorgon`, 23 steps) against the post-refactor build.

What happened:

```
mcp__simdrive__replay(name="pr907-saml-signin-gorgon", on_drift="warn")
→ {
    ok: true,
    halted_at: null,
    steps_planned: 23,
    steps: [
      {id: 1,  similarity: 0.0146, drifted: true, executed: true, error: null},
      {id: 2,  similarity: 0.0918, drifted: true, executed: true, error: null},
      {id: 3,  similarity: 0.0938, drifted: true, executed: true, error: null},
      {id: 4,  similarity: 0.0713, drifted: true, executed: true, error: null},
      ...
      {id: 23, similarity: 0.1836, drifted: true, executed: true, error: null},
    ]
  }
```

23/23 steps executed at SSIM 0.014–0.626. The first tap was meant for a library-picker button but actually hit "Don't Allow" on the iOS notifications permission alert. From there the recording's coords landed on arbitrary UI for 22 more taps.

The replay reported success. Nothing about the response indicates that the replay was nonsense.

## Root cause

The recording was captured from a logged-out-at-library-picker state on app version Y. The smoke test ran it against a fresh-install state with the notifications alert visible on app version Y+. There's no metadata on the recording declaring which state it expects, and no check at replay-start verifying the live state matches.

`on_drift=warn` was a contributing factor (with `halt` it would have stopped at step 1), but the deeper problem is that drift detection is *post hoc* — it tells you the replay was garbage AFTER it's executed 23 actions against your app. By then any side effects (account modifications, cookies sent, navigation state) have already happened.

## Proposed solution: capture-time + replay-time state contract

### Schema addition to recording YAML

```yaml
name: pr907-saml-signin-gorgon
simdrive_version: 1.0.0a8
captured_at: 2026-04-04T18:33:12Z
requires:
  app:
    bundle_id: com.example.reader
    version: "3.0.0"
    version_match: minor  # exact | minor | major | any
  sim:
    device: iPhone 16 Pro
    ios_version: ">=18.0"
  initial_state:
    # Captured automatically at record_start: OCR text from the
    # first screenshot, app foreground state, primary buttons.
    foreground: true
    text_subset_required:
      - "Add Library"
      - "Example Reader Bookshelf"
    text_subset_forbidden:
      - "Allow Notifications"  # an alert that wasn't there at capture
      - "Don't Allow"
    primary_button_label: "Add Library"
steps:
  - …
```

**`requires:`** is the new top-level block. Everything inside is captured automatically; the user doesn't author it by hand.

### Capture-time behavior

When `record_start` is called:

1. Capture app bundle ID + version from the running foreground app
2. Capture sim device + iOS version
3. Take a step-0 screenshot, OCR it, and populate:
   - `text_subset_required` from the top ~10 detected text marks (signature of the expected screen)
   - `primary_button_label` from the largest/most-central button-shaped element
   - `text_subset_forbidden` is empty by default (capture surface; user can edit)
4. Persist `requires:` to the YAML alongside steps

**`requires:` is captured, not authored.** This is the key insight — humans don't write contracts well; cameras don't lie.

### Replay-time behavior

When `replay` is called:

1. **Step −1: Contract verification.** Before step 1 runs:
   - Confirm the foreground app's bundle ID matches `requires.app.bundle_id`
   - Confirm version satisfies `requires.app.version` per `version_match` policy
   - Confirm sim device matches (string equality, case-insensitive)
   - Confirm iOS version satisfies `requires.sim.ios_version` (semver predicate)
   - Take a screenshot, OCR it, confirm `text_subset_required` is present and `text_subset_forbidden` is absent
2. **On verification failure**: halt with a structured error:
   ```json
   {
     "ok": false,
     "halted_at": 0,
     "halt_reason": "state_contract_mismatch",
     "expected": { "text_subset_required": ["Add Library", "Example Reader Bookshelf"] },
     "actual":   { "text_subset_present": ["Allow Notifications", "Don't Allow", "Example Reader would like to send"] },
     "remedy": "App appears to be showing a permission alert. Pre-grant via `xcrun simctl privacy <udid> grant notifications <bundle_id>` before launching, then retry.",
     "_simdrive_warning": null
   }
   ```
3. **On verification success**: proceed to step 1.

### Behavioral defaults

- **`halt_on_state_mismatch` defaults to `true`.** Override via parameter: `replay(..., halt_on_state_mismatch=False)` for the rare "I want to drive 23 blind taps anyway" case. Today's behavior (run regardless) becomes opt-in.
- **`on_drift` semantics tighten too**: combine with a new `drift_floor` (default 0.4). If 50%+ of executed steps drop below `drift_floor`, replay aborts mid-flight regardless of `on_drift`. This catches the "recording was contract-clean at step 0 but the app state drifted mid-flow" case.

### Migration path for existing recordings

Three options for pre-1.0.x recordings without `requires:`:

1. **`migrate_recording <name>` command**: opens a session, plays back step 0's screenshot, OCRs it, writes the `requires:` block in-place. User reviews the result, edits if needed.
2. **Implicit "any" until annotated**: recordings without `requires:` are treated as `requires: { app: { version_match: any }, initial_state: {} }` — same permissive behavior as today, but emit a deprecation warning at replay time: `"WARNING: recording has no requires: block. State contract not verified. Run 'simdrive migrate-recording <name>' to capture one."`
3. **CI lint hook**: simdrive surfaces a `lint_recordings` command that exits non-zero for recordings without `requires:` — projects can adopt at their own pace via `verify-pr.sh` integration.

I'd suggest shipping #2 + #3 in the same release as the schema. #1 as a follow-up.

## Why this is better than alternatives

| Alternative | Why it falls short |
|---|---|
| Stricter `on_drift=halt` as default | Catches divergence at step 1, but only via SSIM — and a permission alert is "similar enough" structurally that SSIM can misfire. OCR-based state check is more precise. |
| Per-version baselines (`.simdrive/fixtures/baselines/<app-version>/<flow>/<step>.{json,png}`) | Useful for *visual regression*, but doesn't validate the starting state. A baseline tells you "step 5 looks different than last release" — it doesn't tell you "step 1 is running against the wrong screen entirely." |
| Manual `pre_replay_hook` per recording | Push complexity onto every recording author. The `requires:` block centralizes the contract in the recording itself. |
| Document "always reset to fresh-install before replay" in README | Doesn't scale. People forget. Recordings need to enforce their own preconditions. |

## What's downstream on the consuming-project side

Example Reader landed a complementary primitive layer in https://github.com/ExampleOrg/ios-core/pull/937 — `.example-state/operations/reset-fresh-install.sh` + snapshot/restore. With Layer 2 (this proposal) shipped:

- Example Reader's recording authors run `simctl privacy grant` + reset-fresh-install before `record_start`
- Capture-time auto-populates `requires:` with the post-reset state's OCR signature
- Replay-time verifies and halts cleanly if a future agent forgets the reset
- `verify-pr.sh` lints that every Example Reader recording has a non-empty `requires:` (Example Reader's Layer 3)

This eliminates the entire class of "blind 23 taps at SSIM 0.014" failures.

## Suggested test plan for the simdrive PR that implements this

- **Unit**: `requires:` block round-trips through YAML parse/serialize without lossy transformation
- **Unit**: contract verifier passes when current state matches; halts with structured error when it doesn't
- **Integration**: record on iPhone 16 Pro, replay on iPhone 17 Pro → halts with `sim.device` mismatch (unless `--ignore-sim-device`)
- **Integration**: record at app v3.0.0, replay at v3.1.0 with `version_match: minor` → passes; with `version_match: exact` → halts
- **Integration**: record at "Add Library" screen, replay against fresh sim with notifications alert visible → halts with `text_subset_forbidden` hit ("Don't Allow" present)
- **Migration**: existing pre-`requires:` recordings still replay (with deprecation warning) — nothing in the corpus breaks
- **Lint**: `simdrive lint-recordings` exits non-zero on at least one recording in the test corpus with no `requires:` block, zero on the migrated ones

## Open questions for the simdrive maintainer

1. **OCR provider in capture phase** — does simdrive already have a vendored OCR (vision-based?) it can call during `record_start`, or would the capture-time auto-population require a new dependency? If new dependency, is there a "minimal text extraction" path (e.g. iOS Accessibility tree, which simdrive could query via the existing observe pipeline) that avoids it?
2. **`version_match` policy granularity** — proposed values: `exact`, `minor`, `major`, `any`. Open to a different vocabulary if simdrive has a precedent.
3. **`text_subset_forbidden` capture default** — should it auto-populate with anything (e.g. "alert" / "modal" element types), or always start empty? Empty is safer (no false positives at first); auto-populate could prevent the specific permission-alert case if simdrive can structurally identify alerts.
4. **Backward-compat warning channel** — the existing `_simdrive_warning` field on every MCP response is great for the version-drift case. For the "recording without requires:" deprecation, same channel? Or a new field like `_recording_warnings: [...]`?

## Why I'm filing this now

The smoke-test failure today is the third time this class of bug has bitten Example Reader (per memory: `specterqa_corpus_replay_under_a3.md` 2026-04, `simdrive_v1_0_0a3_dogfood.md` 2026-04). Each prior occurrence got worked around with "use structural_checks instead of SSIM" — that's been the right tactical move but it doesn't address the upstream gap.

This proposal closes the gap at the schema level so individual projects don't have to keep inventing local workarounds.

Happy to review a draft PR or do additional dogfood once an experimental build is available.

---

**Contact**: Maurice Carrier (maurice.carrier@outlook.com)
**Example Reader context**: https://github.com/ExampleOrg/ios-core/pull/937 (Layer 1, just landed for review)
