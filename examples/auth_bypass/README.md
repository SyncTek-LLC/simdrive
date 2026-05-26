# AUTH_BYPASS recording recipe

A canonical pattern for letting SimDrive record deterministic journeys
against an app that normally requires real authentication (Sign-in-with-
Apple, OAuth, magic-link email, etc.).

SimDrive itself is **auth-agnostic** — it drives the simulator and observes
pixels; it doesn't know or care how your app authenticates. That's correct,
but it means every team that wants to record a journey ends up reinventing
the same launch-arg + fixture-user + dev-token pattern. This directory is
the recipe — sharp-edged tools, but ours.

## When you need this

You're recording a journey that starts at the app's first launch screen and
ends somewhere deep inside the post-login UX. Without auth bypass, the
recording either:

1. Includes a real OAuth round-trip → flaky (network, captcha, MFA), leaks
   real credentials into your `recording.yaml`, and the fixture user
   identity drifts on replay; or
2. Stops at the login screen → only covers ~10% of the user journey.

With auth bypass, your dev-build app accepts a launch argument that says
"skip auth, hydrate session with this fixture user", and your backend
accepts a matching dev-token from that launch. The recording captures the
post-login flow deterministically, replays bit-for-bit.

## The pattern, end to end

```
┌────────────────────────────────────┐
│ 1. Build a dev configuration of    │
│    your iOS app that watches for   │
│    a `SimDriveAuthInject` launch   │
│    argument (see ios-launch-arg.   │
│    swift). RELEASE builds must     │
│    NOT include this code path.     │
└────────────────────────────────────┘
                  │
                  ▼
┌────────────────────────────────────┐
│ 2. Stand up a recording-only       │
│    backend that accepts a single   │
│    `AUTH_BYPASS_TOKEN` env-var-    │
│    gated bypass header. See        │
│    docker-compose.recording.yml.   │
│    The prod backend MUST reject    │
│    this header in any environment  │
│    other than `dev`/`recording`.   │
└────────────────────────────────────┘
                  │
                  ▼
┌────────────────────────────────────┐
│ 3. Seed deterministic fixture      │
│    users with fixed UUIDs so       │
│    replay diffs don't drift on     │
│    "user_id". See seed-dev-users.  │
│    json.                           │
└────────────────────────────────────┘
                  │
                  ▼
┌────────────────────────────────────┐
│ 4. Drive SimDrive with launch_args │
│    that include both the bypass    │
│    marker AND the fixture user id  │
│    you want hydrated. See "Driving │
│    SimDrive" below.                │
└────────────────────────────────────┘
                  │
                  ▼
┌────────────────────────────────────┐
│ 5. Before shipping: run            │
│    safety-checklist.md and verify  │
│    every box. The cost of a leaked │
│    bypass in a production build is │
│    a CVE.                          │
└────────────────────────────────────┘
```

## Driving SimDrive

Once your dev-build app + recording backend are wired up, you launch the
SimDrive recording session like any other journey — just pass the auth-
bypass launch args:

```python
session_id = "auth-bypass-demo"
session_start(
    session_id=session_id,
    bundle_id="com.example.myapp.dev",
    launch_args=[
        "SimDriveAuthInject",
        "--fixture-user", "u-fixture-001",
    ],
)
record_start(session_id=session_id, name="post-login-onboarding")
# … drive the journey …
record_stop(session_id=session_id)
```

For replay determinism, **always pass the exact same launch args at replay
time** that you passed at record time. Different launch args = different
hydrated session = different first-frame screenshot = SSIM blowup.

## What's in this directory

| File | Purpose |
| --- | --- |
| `README.md` | This file. The why + the integration shape. |
| `ios-launch-arg.swift` | Minimal Swift snippet — the `CommandLine.arguments` check that hydrates a fixture session instead of prompting for real auth. |
| `seed-dev-users.json` | Example fixture-user payload. Fixed UUIDs, plain-language emails, role variants. Adapt to your backend's user schema. |
| `docker-compose.recording.yml` | Backend-side compose override pattern: env-var-gated `AUTH_BYPASS_TOKEN`, mounted volumes for deterministic seed data, isolated network. |
| `safety-checklist.md` | What NEVER to do. Read before merging anything that touches bypass code paths. |

## Reminders

- **This is a recipe, not a feature of the SimDrive package.** SimDrive
  doesn't know what `SimDriveAuthInject` means — you wire that to your app.
  The marker string is a convention; pick whatever you want as long as it's
  distinctive enough that grepping the repo for it finds every code path.
- **The bypass code lives in YOUR app, not in SimDrive.** SimDrive only
  passes the launch args you tell it to.
- **You are responsible for compile-gating the bypass path out of release
  builds.** SimDrive cannot enforce this — see `safety-checklist.md`.
