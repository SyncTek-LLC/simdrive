# SimDrive Screenshot & Recording Redaction Spec

> **Status:** SPEC ONLY. [internal-tracker] ships this document. W2 ships the
> implementation in `simdrive/src/simdrive/redact.py` and wires it into
> `recorder.py`, `observe.py`, and the on-disk recording writer.

## Goals

1. **No screenshot written to disk contains the rendered glyphs of a
   `secureTextEntry` field.** This applies to in-flight `observe` returns AND
   to persisted recording frames under `~/.simdrive/recordings/<name>/screenshots/`.
2. **No recording step persists the actual text typed into a SecureField.**
   The `type_text` step is rewritten to a placeholder (`<REDACTED:length=N>`)
   for replay, and the secret value is dropped on the floor.
3. **No recording step persists clipboard contents** captured during the
   record window when clipboard read happens to be in the simulator's view.
4. Behavior is **default-on** (fail closed). An explicit opt-out flag
   (`SIMDRIVE_DISABLE_REDACTION=1`) is honored only for golden-fixture tests
   and explicitly-scoped debugging sessions; opt-out is logged loudly to
   stderr and stamped into the recording header so it's visible at replay.

## Non-goals (W2 scope, not this spec)

- OCR-based detection of secrets in non-SecureField labels (would require a
  separate ML model — defer to W3+ if at all).
- Server-side redaction in a hosted runner (out of scope for the local MCP).
- Redacting the *underlying* app's keychain or screenshots taken by the app
  itself — those live in the app's sandbox and are the app author's problem.

## Detection: which fields are SecureField?

iOS surfaces SecureField via XCUIElement attributes accessible to our
runner. The detection ordering, fail-closed:

1. **Primary:** `XCUIElementTypeSecureTextField` element type. Every
   element returned by the observe pipeline carries `type` and `frame`
   (already serialized into the element list). If `type == "SecureTextField"`,
   the frame is added to the redaction mask.
2. **Secondary:** `isSecureTextEntry` attribute on `XCUIElementTypeTextField`
   (sometimes UIKit wraps a UITextField that has `secureTextEntry = YES`
   without changing its element type). Fetched per-element by the runner.
3. **Tertiary heuristic (off by default):** label/identifier matching
   `(?i)(password|passcode|pin|secret|otp|2fa|token)`. Disabled by default
   because it generates false positives on, e.g., a "Forgot password?" link.
   Opt-in flag `SIMDRIVE_REDACT_HEURISTIC=1` enables it for paranoid users.

If the detection step itself raises (e.g. runner disconnect mid-observe),
the screenshot is dropped (not written) rather than written unredacted —
fail closed.

## Masking: bbox redaction

Each detected secure-field frame is converted from simulator points to
screenshot pixels using the same `_pixels_to_screen` math used by `act.py`,
then a solid black rectangle is painted into the PNG buffer **before** the
file is written or before the image bytes are returned in an `observe`
response. The redaction also expands the rect by 4 pixels on each side to
catch sub-pixel anti-aliased glyphs that leak past the strict frame.

After masking, the screenshot is saved with a sidecar `screenshot.meta.json`
recording:

```json
{
  "redacted": true,
  "redaction_method": "secure_field_bbox_v1",
  "redacted_regions": [{"x": 12, "y": 340, "w": 280, "h": 44}],
  "detection_sources": ["element_type", "is_secure_text_entry"],
  "heuristic_enabled": false,
  "schema": "v1"
}
```

The sidecar is the only authoritative record of whether redaction ran; the
PNG alone cannot be trusted (an attacker who can swap the PNG can claim
"this was never redacted" — the sidecar is hashed into the recording's
manifest at W2 time).

## Clipboard scrubbing during recording

The recorder hooks into the recording loop and, at every step boundary,
snapshots the simulator's clipboard via `xcrun simctl pbpaste`. If the
clipboard content is detected as a secret (the same SecureField detection +
optional heuristic + a minimum entropy threshold for blob-ish content), the
clipboard read step is replaced with `<REDACTED:clipboard,len=N>` in the
recording before serialization. The actual clipboard string never enters
the recording in-memory representation past the detection point.

## type_text redaction

When `tap` lands on a SecureField immediately followed by `type_text`, the
recorder rewrites the recorded `type_text` step as:

```yaml
- action: type_text
  text: "<REDACTED:length=12>"
  redaction: {reason: "secure_field", source: "tap_into_secure_field"}
```

Replay logic substitutes the placeholder with the value of the
`SIMDRIVE_REPLAY_SECRET_<key>` env var at replay time (key derived from the
SecureField's identifier or its index in the recording). Replays that lack
the env var fail loudly rather than typing the placeholder string — this
preserves CI determinism for non-secret cases and forces secret material to
live in the CI vault.

## Opt-out flag

`SIMDRIVE_DISABLE_REDACTION=1` disables all four mechanisms (detection,
bbox mask, clipboard scrub, type_text rewrite). When set:

- Every `observe` and recording-frame write logs `WARN redaction=DISABLED`
  to stderr.
- The recording manifest header records `"redaction": {"enabled": false}`
  so the disclaimer is visible to anyone replaying it later.
- The release CI gate (W3) refuses to publish a wheel built from a tree
  whose tests were run with `SIMDRIVE_DISABLE_REDACTION=1` (env-var sniffing
  in the pre-publish gate).

## Golden-fixture test plan (W2 implementation requirement)

W2 ships these tests in `simdrive/tests/test_redaction.py`:

1. `test_secure_field_bbox_masked` — Synthetic PNG + an element list
   declaring one `SecureTextField` at frame `{x:10,y:10,w:100,h:20}`;
   assert the resulting PNG has all-black pixels inside the inflated
   rect and that the sidecar JSON lists exactly one redacted region.
2. `test_secure_field_negative_no_mask` — Same PNG + element list with NO
   secure field; assert the PNG hash is unchanged (no false-positive mask).
3. `test_heuristic_off_by_default` — Element labeled "password reset link"
   with `type=Button`; with heuristic OFF, assert no mask. With
   `SIMDRIVE_REDACT_HEURISTIC=1`, assert a mask appears (this is the
   correct false-positive direction we want documented).
4. `test_type_text_rewrite_in_recording` — Recorder fed a synthetic
   tap-into-secure-field followed by `type_text("hunter2")`; assert the
   serialized YAML contains `<REDACTED:length=7>` and never `hunter2`.
5. `test_clipboard_scrub_on_secret_content` — Recorder fed a clipboard
   snapshot of `sk-ant-abc123…` (high entropy + Anthropic key prefix);
   assert the recorded step says `<REDACTED:clipboard,len=N>`.
6. `test_opt_out_disables_all_and_logs` — With
   `SIMDRIVE_DISABLE_REDACTION=1`, assert PNG is unchanged, recorder
   captures plaintext, and the recording manifest header says
   `redaction.enabled: false`. Capture stderr and assert it contains
   `WARN redaction=DISABLED`.
7. `test_fail_closed_when_detection_raises` — Monkey-patch the detector
   to raise; assert that no PNG is written to disk and an
   `ObserveError` propagates (vs. silently writing unredacted bytes).

Golden PNG fixtures live under `simdrive/tests/fixtures/redaction/`. Each
fixture has a SHA-256 baseline so we catch unintended mask-rect drift.

## Implementation file layout (W2)

```
simdrive/src/simdrive/redact.py            # core: detect + mask + sidecar
simdrive/src/simdrive/recorder.py          # hook: rewrite type_text + clipboard
simdrive/src/simdrive/observe.py           # hook: mask before returning bytes
simdrive/tests/test_redaction.py           # golden-fixture suite
simdrive/tests/fixtures/redaction/         # PNG fixtures + sidecar baselines
```

## Open questions for W2

1. Do we mask in-memory `observe` returns too, or only on-disk? **W1 spec
   answer: BOTH** — the MCP client gets masked bytes by default, with the
   same opt-out flag.
2. Inflation radius of 4 pixels — empirically validated against iPhone 17
   Pro Max @ 3.0x; smaller densities (1x simulators) may need recalibration.
3. Should the sidecar JSON be merged into a single per-recording manifest
   rather than per-screenshot files? Per-screenshot is simpler for atomic
   write/replace; consolidation is a W3 cleanup.
