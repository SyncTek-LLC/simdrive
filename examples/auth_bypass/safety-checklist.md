# AUTH_BYPASS safety checklist

The bypass pattern in this directory has sharp edges. A bypass code path
that ships to App Store users — or a bypass token that escapes a dev
environment — is a P0 incident. Walk this checklist before merging
anything that touches the bypass, and again before every recording
session against a backend that talks to a shared database.

## Never

- **Never enable the bypass in App Store / TestFlight production builds.**
  Gate `fixtureUserIdFromLaunchArgs()` and all its callers behind `#if
  DEBUG` or `#if BYPASS_AUTH` (custom config, not the active config for
  archive builds). Verify with `nm` on the archive's binary that the
  sentinel string is absent.
- **Never bake the `AUTH_BYPASS_TOKEN` value into the iOS app source.**
  The app reads it from `ProcessInfo.processInfo.environment` (set by the
  simulator launch context), never from a hardcoded string. A hardcoded
  token in the app means anyone can grep the App Store binary and find it.
- **Never let the bypass backend code path run in `ENV=production`.**
  The backend must check `ENV in {dev, recording, local}` AND
  `RECORDING_MODE=1` AND the token matches. Three locks. Fail closed if
  any check fails — return 401, not 200.
- **Never point the recording compose at a database that could resolve
  to prod.** Use a dedicated `db-recording` service with tmpfs storage so
  recordings cannot accidentally write to a shared DB.
- **Never reuse fixture-user UUIDs or emails in production.** Treat the
  shape in `seed-dev-users.json` as published — anyone reading the repo
  has them.
- **Never use a real OAuth refresh token or real session JWT as the
  bypass token.** The bypass token should look unmistakably wrong for
  prod (`local-recording-only-not-for-prod` is the canonical shape).
- **Never commit a real recording-environment `AUTH_BYPASS_TOKEN` to the
  compose override if you rotate it per-environment.** Use docker `secrets:`
  or a `.env` file gitignored at the repo root.

## Always

- **Always pair the iOS launch-arg check with a bundle-identifier suffix
  check** (`.dev`, `.recording`, `.local`). Belt and braces — if a
  release build somehow shipped with DEBUG on, the bundle ID check still
  refuses the bypass.
- **Always pass identical launch args at replay time as at record time.**
  Different fixture user = different hydrated session = SSIM blowup on
  the first frame. Pin the args in your replay harness.
- **Always re-seed the recording database from a clean state per session.**
  The `tmpfs` mount in `docker-compose.recording.yml` does this for you;
  if you switch to a persistent volume, add a `docker compose down -v`
  step to your record-start script.
- **Always assert in CI that the App Store archive does not contain the
  bypass sentinel string.** A simple `grep -q SimDriveAuthInject` against
  the binary, fail the build if it matches. Example check:

  ```bash
  # In your release CI, after `xcodebuild archive`:
  if strings "$ARCHIVE_PATH/Products/Applications/MyApp.app/MyApp" | \
       grep -q SimDriveAuthInject; then
      echo "ERROR: bypass sentinel string present in release binary"
      exit 1
  fi
  ```

- **Always treat a leaked bypass token as a P0 incident.** Rotate it
  immediately, revoke any sessions hydrated through it, audit logs for
  use of the token from any non-CI IP.
- **Always rotate the bypass token at least monthly** even if no leak is
  suspected. A leaked-and-unrotated token is the same as no security.
- **Always log every bypass-hydrated session on the backend** with the
  fixture user id and the originating IP. Recording-environment IPs
  should be a known short list (CI runner pool + dev laptops); anything
  else is suspicious.

## Pre-merge review questions

When reviewing a PR that touches anything in `examples/auth_bypass/` or
your app's bypass integration, ask:

1. Is the bypass code path inside `#if DEBUG` (or a verified-absent-from-
   release flag)?
2. Does the backend reject the bypass token outside `ENV in {dev,
   recording}`?
3. Has the bypass token actually been changed if this PR touches the
   shared compose file in a way that suggests rotation is needed?
4. Is the seed data still synthetic (no real PII, no real org names)?
5. Does the CI release pipeline have a sentinel-grep guard?

If any of the above is "no" or "unclear", the PR does not merge.

## Incident playbook (bypass token leaked)

1. Rotate `AUTH_BYPASS_TOKEN` in the recording compose env immediately.
2. Roll any backend signing keys that the bypass token had access to mint.
3. Audit backend access logs for the prior token value over the past
   90 days; flag any requests from IPs outside the known CI / dev pool.
4. Force-expire all sessions hydrated through the bypass code path
   (look up by the dedicated `bypass_session` claim in the JWT).
5. Post-mortem within 48h. Determine how the token leaked (committed to
   source? pasted in chat? in a Slack screenshot?) and add the
   prevention to this checklist.
