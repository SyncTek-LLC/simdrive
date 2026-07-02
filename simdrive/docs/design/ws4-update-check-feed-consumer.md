# WS-4 — simdrive update-check feed *consumer* (design)

**Status:** DESIGN ONLY — no code lands until the M0 gate clears (cs_81b9ae88 is
approved and held). This doc scopes the simdrive slice of WS-4's
"signed pull-based `releases.json` + `harness update-check` + advisory".

**Owner:** simdrive-lane · **Reviewers:** coordinator (contract), harness-lane
(feed producer/shape), forgeos-lane (product-plane call log / WS-3 overlap).

---

## 1. Goal & non-goals

**Goal.** Let simdrive tell a user "a newer version exists" by *pulling* a
signed release feed, verifying it locally, and printing an **advisory** — never
auto-installing, never sending user data.

**Non-goals.**
- No auto-update / self-mutation. Advisory only; the user runs `pip install -U`.
- No new outbound in the **data plane**. This is a **product-plane** call
  (§2) that carries *no* user code/data.
- Not a telemetry channel. The update check MUST NOT piggyback any counts,
  identifiers, or crash data onto the request.

## 2. Which plane this is (the promise)

Per the two-plane split, the update check is a **product-plane** call:

| Property | Requirement |
|---|---|
| Direction | Outbound GET to a static, signed feed (a CDN object) |
| Payload | **None** beyond the HTTP request itself — no query params carrying user data, no body |
| User data | Never. Not even install-UUID (that is opt-in analytics, a separate WS-4 slice) |
| Severable | Yes, fail-open — simdrive works fully with the check disabled/unreachable |
| Announced | Every call is logged (§6) so the user can see what left the machine |

This is the same discipline the telemetry flip enforced: **the data plane stays
silent; a minimal product plane is opt-out-able and severable.**

## 3. Kill-switch & offline integration (reuse WS-0)

The consumer routes through the **single** `HEKA_TELEMETRY` kill-switch already
landed in `simdrive.license.telemetry.telemetry_killed()`:

- `HEKA_TELEMETRY=off` (or `0/false/no/none/disabled`) → the update check is a
  hard no-op (no fetch attempted). One switch severs telemetry **and** the
  product-plane checks.
- `HEKA_OFFLINE=1` (WS-3 alignment) → all product-plane calls muted; rely on
  cached state / offline grace only.
- Default cadence: at most once per `UPDATE_CHECK_INTERVAL` (default 24h),
  gated by a cached `last_check` timestamp so repeated CLI invocations don't
  nag or hammer the feed. `simdrive update-check --now` forces a check.

Decision: the update check is **opt-out**, not opt-in (unlike telemetry) —
it carries no user data, so default-on advisory is acceptable under the
two-plane rule. This must be confirmed by the coordinator against the BA
positioning (Solo/self-host editions) before implementation.

## 4. Feed shape (`releases.json`) — consumer's contract

The producer (harness-lane / release engineering) owns the canonical schema;
the consumer needs at minimum:

```jsonc
{
  "schema_version": 1,
  "product": "simdrive",
  "latest": "1.0.0",              // newest released version
  "min_supported": "1.0.0b8",    // below this = urge upgrade (security)
  "released_at": "2026-07-01T00:00:00Z",
  "notes_url": "https://simdrive.dev/changelog/",  // advisory only, not fetched
  "channels": { "stable": "1.0.0", "beta": "1.0.1b1" }
}
```

The feed is served as a static object plus a **detached Ed25519 signature**
(`releases.json.sig`) over the exact bytes. **Open contract questions for
harness-lane:** signature envelope (detached file vs inline `sig` field),
key id / rotation story, and the canonical hosting origin.

## 5. Verification (reuse the license Ed25519 pattern)

- Embed a **feed-signing public key** in the client, mirroring
  `simdrive.license.public_key` (do NOT reuse the license key — separate key,
  separate rotation).
- Fetch bytes → verify Ed25519 signature over the raw bytes **before** parsing
  JSON. Signature failure = **refuse** the feed, log a warning, fall back to
  cached-known-good; never act on an unverified feed.
- Then compare `latest` / `min_supported` to `simdrive.__version__` using
  PEP 440 ordering (`packaging.version`).

## 6. Transparency — product-plane call log (WS-3 overlap)

Per the coordinator's contract-freeze addition, the GET must be **announced**:
every update-check call appends one structured record to the shared local
product-plane call log at **`~/.heka/calls.jsonl`**, in the **canonical
cross-lane shape frozen by the WS-3 entitlement contract §8**
(forgeos-lane, `WS3-ENTITLEMENT-CONTRACT-v1.md`) — all 3 Heka products write
this same shape:

```jsonc
{ "ts": ..., "product": "simdrive", "kind": "update_check",
  "method": "GET", "url": "<feed origin, no query values>",
  "payload_shape": [],          // field NAMES only; [] = zero-user-data GET
  "ok": true,                   // + optional "error" on failure
  // retained additional fields (allowed by the contract):
  "result": "ok|skipped_unreachable|skipped_schema|signature_unverified",
  "user_data": false }
```

`simdrive` should expose the tail of this log (e.g. `simdrive update-check
--history`) so a user can audit exactly what left the machine and when.

## 7. Consumer behavior (state machine)

```
update-check
  ├─ telemetry_killed() or HEKA_OFFLINE → no-op (log "skipped: disabled")
  ├─ cached last_check within interval and not --now → use cache
  ├─ GET feed (timeout ~3s, like telemetry)
  │    ├─ network error → silent skip, log "skipped: unreachable" (fail-open)
  │    ├─ signature invalid → refuse + warn, keep cached-known-good
  │    └─ ok → verify → parse → cache → advise
  └─ advise: compare __version__ vs latest/min_supported
       ├─ up to date        → (quiet unless --verbose)
       ├─ newer available   → "simdrive X is available (you have Y): pip install -U simdrive"
       └─ below min_supported → stronger "please upgrade (security/compat)"
```

Network/timeout handling and the "never raise" invariant mirror
`telemetry.send_trial_attribution` exactly (fire-and-forget, non-fatal).

## 8. Test plan (for the implementation slice)

- Ed25519 verify: valid sig accepts; **tampered feed byte → rejected**.
- Wrong-key / missing-sig → rejected.
- Version compare: newer / equal / older / below-min matrix (PEP 440).
- Kill-switch: `HEKA_TELEMETRY=off` and `HEKA_OFFLINE=1` → zero fetch
  (adversarial network-deny, reusing the `test_heka_sim_nosaas.py` harness).
- Cadence: second call within interval makes no fetch; `--now` forces one.
- Fail-open: network error → advisory absent, exit 0, simdrive unaffected.
- Product-plane log: one record per real call, `payload_shape="none"`.

## Appendix — deferred hardening (from cs_81b9ae88 SoD, non-blocking)

Captured here so they aren't lost for the later slice (coordinator flagged
these as do-NOT-block follow-ups on the approved telemetry/crash-sink change):

1. Adversarial no-egress test wrapped around `record_crash` (prove the crash
   sink opens no socket, reusing the network-deny guard).
2. Cover the malformed-config / `OSError` fail-closed branches in
   `telemetry.is_opted_out` with explicit tests.
3. Extend `scripts/mutate_telemetry.py` to mutate `crash_sink.py` predicates
   (scrub-to-basename, message-drop, disabled-env guard).
4. GA/marketing copy must say **"no telemetry / data-plane egress"**, NOT
   "no network calls" — the trial-start license POST to `cloud.simdrive.dev`
   is a *user-initiated product-plane* call and is intentionally retained.
