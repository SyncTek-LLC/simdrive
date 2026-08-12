# WS-4 — simdrive `releases.json` publishing pipeline (design)

**Status:** APPROVED (coordinator sign-off @ d77dd60) with Q1/Q2 adjudicated
and folded in below; Q3 pending Chairman, Q4 answered (see §8).
Implementation slice SHIPPED (this branch): `scripts/make_releases_feed.py`
(§4 steps 1–4 tooling) + the §6.3 CI self-test + two consumer
reconciliations from the producer-review adjudications (missing-sig
fail-closed classification, product membership pin — see the companion
consumer doc §6/§7).

**Owner:** simdrive-lane (this doc: how *simdrive.dev* publishes the feed).
**Converges with:** harness-lane's `harness update-check` producer + canonical
validator (they own the shared schema and reference implementation — this
pipeline consumes both), forgeos-lane's WS-3 key-custody design.

Companion: `ws4-update-check-feed-consumer.md` (the client half, shipped in
`3a3b3c2`). The contract below is the coordinator-**FROZEN** WS-4 feed contract
(bridge, 2026-07-02 05:23 UTC).

---

## 1. Goal & non-goals

**Goal.** Publish a signed `releases.json` for simdrive at a static origin so
the shipped consumer can verify and advise — with **zero user data** touching
the pipeline or the serving path, and a signing story an operator can actually
run (cut a release → feed updates → clients verify).

**Non-goals.**
- No dynamic backend. The feed is a static object; there is no server-side
  compute, no per-request logic, no accounts.
- No download hosting. `pip install -U simdrive` stays the install path; the
  feed only *advises*.
- No analytics on the feed endpoint. Fetch counts are not a metric we collect.

## 2. Artifacts & origin

| Artifact | Path | Notes |
|---|---|---|
| Feed | `https://releases.simdrive.dev/simdrive/releases.json` | matches the consumer default (`check.py:_DEFAULT_FEED_URL`); overridable client-side via `SIMDRIVE_UPDATE_FEED_URL` |
| Signature | `https://releases.simdrive.dev/simdrive/releases.json.sig` | sibling object; base64url **detached** Ed25519 over the **exact raw bytes** of `releases.json` |

Frozen-contract properties the pipeline must preserve:
- Shared schema across all 3 Heka products (`product` field distinguishes);
  harness-lane ships the canonical schema + validator.
- `schema_version` integer; bumping the **major** requires a coordinated
  client release first (old clients skip unknown majors, fail-open).
- Per-product origin comes from config, never hardcoded in harness core.

Serving requirements (CDN / static host config):
- HTTPS only; HTTP → redirect or refuse.
- No cookies, no query-string processing, no personalization — the objects are
  byte-identical for every requester.
- `Cache-Control: max-age=300` on both objects (short TTL so a rotation or a
  pulled release propagates inside minutes; clients only poll every 24h).
- Access logs: disabled, or minimal + shortest retention the host allows, and
  never joined to any other dataset. Document the actual host setting here
  when infra is chosen (**open item →** §8 Q3).

## 3. Key management

- **Separate trust domain.** The feed key is NOT the license-signing key
  (already enforced client-side in `feed_key.py`). Compromise of one must not
  cascade to the other.
- **Generation:** Ed25519 keypair generated offline by the operator
  (PyNaCl, same primitive as the license ledger — per WS-3 F7, the *primitive*
  is reused; the `key_id`/encoding conventions are new and must not be assumed
  compatible with license tooling).
- **Custody:** private half lives in the operator secrets store alongside the
  license-signing key (same handling, separate entry, separate rotation
  schedule). It is never present in CI by default — see §4 signing step.
- **`key_id` convention:** `feed-YYYY-MM` (e.g. `feed-2026-07`), matching the
  placeholder in `feed_key.py`. **Resolved (coordinator adjudication, Q1):**
  do NOT unify with WS-3's `kid = sha256(pubkey)` mid-build — WS-3 F7 froze
  "new conventions, do not unify". WS-4 feeds keep `feed-YYYY-MM` as an ops
  hint; **membership-pinning is the security boundary** (the consumer tries
  all trusted keys; `key_id` never selects). Revisit post-GA only if ops
  friction appears.
- **Rotation runbook** (mirrors `feed_key.py`'s documented procedure):
  1. Generate the next keypair; prepend `(key_id, hex_pubkey)` to
     `UPDATE_FEED_PUBLIC_KEYS` in a client release (clients now trust
     current + next).
  2. After that client release is `min_supported`, switch signing to the new
     key and update `key_id` in the feed.
  3. Remove the old pubkey in a later client release.
  - **Compromise path:** switch signing immediately; clients that only trust
    the old key fail **closed** to cached-known-good until they upgrade —
    degraded (no advisories) but never spoofable. Ship the client bump fast.

## 4. Publish flow (per release cut)

```
release tagged (vX.Y.Z on main, PR-only per governance)
  1. GENERATE  releases.json from the tag + changelog metadata
               — via harness-lane's `harness update-check` producer once it
                 lands; interim: scripts/make_releases_feed.py (this slice)
  2. VALIDATE  harness-lane's canonical reference validator must PASS
               (schema, PEP 440 version sanity: latest ≥ min_supported,
                channels values parse, notes_url is https://simdrive.dev/…)
  3. SIGN      operator signs the exact bytes offline → releases.json.sig
               (private key never enters CI; signing is the one manual step)
  4. DOGFOOD   run the SHIPPED consumer's verify path against the staged pair:
               `scripts/make_releases_feed.py --dogfood <staging-dir>
                --keys <staging-keyset> --current <installed> --json`
               must verify and report the new version. (CORRECTED from the
               original `SIMDRIVE_UPDATE_FEED_URL=file://…` form: the shipped
               consumer's transport is `requests`, which has NO file://
               adapter — found by the 2026-07-02 parity cross-check. --dogfood
               feeds the staged bytes to the exact verify/advise code real
               clients run, with the staging keyset injected in-process via
               the consumer's `verify_keys=` parameter — the §6.2-adjudicated
               injection, no env trust-anchor override.)
  5. PUBLISH   upload BOTH objects, sig-first, then swap releases.json
  6. VERIFY    fetch from the real origin and re-run step 4 against it
```

**Atomicity / TOCTOU.** A client fetching during the swap can see a new feed
with an old sig (or vice-versa). That window is safe by construction: the
consumer **fails closed** on a bad sig and keeps cached-known-good, then
self-heals on the next check (≤24h). Publishing sig-first shrinks the window;
we do not need (and static hosts don't offer) a true atomic pair-swap.

**Rollback / pulled release.** Republishing an older-content feed (correctly
signed) is legal — the feed is advisory, not an installer, so "rollback" is
just publishing corrected content. If a release is pulled for a security
issue, also raise `min_supported` past it so lagging clients get the stronger
upgrade advisory.

## 5. Privacy invariants (pipeline side of the two-plane rule)

- The pipeline handles **zero user data** at every step: generation reads git
  metadata only; serving is static bytes; no request-time state exists.
- Replay/staleness: an attacker replaying an old (validly signed) feed can at
  worst *suppress* an upgrade advisory — no code execution, no data exposure.
  Accepted for v1; if it ever matters, add a client-side "feed older than N
  days → note staleness in --verbose" (consumer change, logged here so it
  isn't lost).
- Nothing in this pipeline may ever add query params, install IDs, or counts
  to the feed URL. Any future "how many users are on X" question is answered
  by opt-in analytics (separate WS-4 slice), never by feed logs.

## 6. Provisioning & test hooks (GA prerequisites)

1. Embed the production feed pubkey in `feed_key.py` (currently EMPTY →
   consumer fails closed everywhere; this is the tracked GA prereq).
2. The dogfood step needs a **test-key injection hook** so staging can verify
   without the prod key. **Resolved (coordinator adjudication, Q2): NO
   env-based trust-anchor override** — an env-injectable pubkey would break
   fail-closed. Test-only injection (monkeypatch / `verify_keys=` param) plus
   a staging build for the pre-publish dogfood step.
3. Pipeline self-test in CI (no secrets needed): generate a throwaway keypair,
   produce + sign a fixture feed, run the canonical validator AND the shipped
   consumer against it, tamper one byte → must reject. This proves the
   producer/consumer pair end-to-end on every PR.
   **Shipped** as `scripts/make_releases_feed.py --self-test` (CI step +
   `tests/test_make_releases_feed.py`). The validator run in CI is the
   script's mirror of the canonical validator; mirror↔canonical parity
   (byte-identical produce, identical verdicts, cross-verified sigs) is
   locked by `TestCanonicalParity`, which runs against the reference module
   whenever it is reachable (`HEKA_CANONICAL_UPDATE_CHECK_DIR`, pinned to the
   reviewed commit during gate runs; skips on GitHub CI where the harness
   repo is absent).

## 7. Failure modes

| Failure | Effect on users | Detection / recovery |
|---|---|---|
| Bad sig published (operator error) | None — clients fail closed, keep cache | step 6 VERIFY catches it at publish time; republish |
| Feed origin down | None — fail-open silent skip | uptime check on the origin (ops) |
| Schema-major bump before client support | Clients skip (fail-open) | step 2 validator warns on major ≠ shipped-consumer major |
| Signing-key compromise | No spoof of upgraded clients after rotation; stale clients advisory-blind | §3 compromise path |
| Stale feed replay by MITM | Suppressed advisory only | accepted v1 risk (§5) |

## 8. Open questions (routed via coordinator)

- **Q1 — RESOLVED (coordinator):** do NOT unify `key_id` conventions
  mid-build (WS-3 F7). WS-3 tokens keep `kid=sha256(pubkey)`; WS-4 feeds keep
  `feed-YYYY-MM` as an ops hint; membership-pinning is the security boundary.
  Revisit post-GA only on ops friction. (§3)
- **Q2 — RESOLVED (coordinator):** no env-based trust-anchor override;
  test-only injection + staging build. (§6.2)
- **Q3 — PENDING (routed to Chairman):** hosting infra for
  `releases.simdrive.dev` (static bucket + CDN vs existing simdrive.dev host)
  — determines the §2 access-log setting to document.
- **Q4 — ANSWERED YES (harness main @ 7b4ba2d):** `harness update-check
  --produce` emits the feed deterministically from a git tag. **One residual
  raised back to harness-lane:** its `_pick_tag` only considers bare `v*`
  tags, but simdrive releases are tagged `simdrive-v*` (this repo's `v*` tags
  are legacy specterqa releases — auto-pick would emit `latest: 9.0.0`).
  Until the canonical producer grows a tag-prefix option, simdrive cuts
  either pass `--tag` explicitly or use the interim
  `scripts/make_releases_feed.py` (default prefix `simdrive-v`, byte-parity
  locked by tests).

## 9. DoD for the implementation slice

Same DoD as every slice: intent file, ForgeOS changeset, evidence
(unit_test/lint/security_scan/ADR), red-first tests incl. the §6.3 CI
self-test, mutation on the signing/validation path, `verify-pr` green,
independent SoD (architect + qa_test) — plus the §8 answers folded in.
