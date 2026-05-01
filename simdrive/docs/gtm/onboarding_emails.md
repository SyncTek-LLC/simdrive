# SpecterQA Cloud — Onboarding Email Sequence

Five-email sequence for users who sign up for the SpecterQA Cloud beta (when it launches; Cloud MVP target is 2026-06-30 per the productization plan §11). Engineer-voice. No "Hi friend!" Each email under 150 words. Subject lines included.

The trigger for email 1 is "user clicked the magic link in the welcome email." All other emails are triggered on calendar offset from email 1 unless explicitly marked otherwise.

Sender: `maurice.carrier@synctek.io`. The maintainer is the sender; this is not a noreply queue.

---

## Email 1 — Day 0 (immediate)

**Subject:** SpecterQA Cloud — your first observe

Welcome to the SpecterQA Cloud beta.

Install the Engine if you have not already:

```
pip install specterqa-ios
```

Then point it at your Cloud account:

```
specterqa-ios cloud login --token [TOKEN]
```

First journey to try: open an iOS sim, run `specterqa-ios session start`, then `specterqa-ios observe`. You will get a screenshot path and a list of OCR'd marks with `stable_id`s. That is the object the agent reasons about.

The fastest way to feel the product is to reply to this email with the bug you are trying to catch. I read every reply and write back with the three or four lines that get you to a deterministic replay of that bug. No support ticket queue — Cloud beta is small on purpose.

— Maurice

---

## Email 2 — Day 1

**Subject:** Did your first observe work?

Quick check — did `specterqa-ios observe` return what you expected?

Two failure modes I see most often:

1. **Sim not booted.** The Engine assumes a booted simulator; it will not boot one for you in beta. `xcrun simctl boot [DEVICE_UDID]` first. (`session_start({})` will pick the first booted sim automatically.)
2. **`reliable_targets` is empty.** Expected if your app has no `accessibilityIdentifier` set. The screenshot is still truthful — tap by `stable_id` from the OCR marks instead.

If you hit something else, reply with the command you ran and the output. The Cloud dashboard at [LINK] also shows the last 10 observations on your account — useful for sanity-checking before you reply.

— Maurice

---

## Email 3 — Day 3

**Subject:** stable_id replay — the 30-second version

The single feature that makes SpecterQA replays survive layout drift is `stable_id` — a 12-char hash of an OCR mark's text plus a coarse bbox bucket. Tap by `stable_id` instead of pixel coords and your replay survives a layout reshuffle.

```
record_start name="login-flow"
tap stable_id="a229e82e3f00"
type_text tap_first={stable_id: "850877875550"} text="harlem"
record_stop
```

That recording, replayed two weeks later after a UI tweak, still finds the right targets.

30-second demo: [GIF_LINK] — same flow against the bundled TestKitApp. Cloud-beta users get the recording archived to `[CLOUD_URL]/recordings/login-flow` automatically; recordings persist across machines.

The recording schema does still serialize pixel coords as a fallback; v1.1 promotes `stable_id` to primary. Until then, layout-stable replays.

— Maurice

---

## Email 4 — Day 7

**Subject:** SSIM region masking + PR-gating

The Cloud-tier replay archive ships SSIM-trend dashboards. The thing they unlock is **PR-gating on visual regression**.

Two pieces:

1. **Per-step SSIM threshold.** Default 0.85. You can override per step in the recording YAML — useful for screens with animated content (Lottie, network-driven counters) where 0.85 is too tight.
2. **Region masking.** A rectangle in the post-snapshot you exclude from SSIM comparison. Mask your Lottie animation, leave the rest of the screen gated. Same recording, no flake.

The CI integration is a one-flag add to your existing pipeline:

```
specterqa-ios replay --gate --threshold 0.85 .specterqa/journeys/login.yaml
```

Exit code 0 on pass, non-zero on drift. Wire it into your PR check, fail the build on drift, ship the screenshot of the diff in the failure comment. Example Reader runs this pattern on every PR; a sample failure comment is at [LINK].

— Maurice

---

## Email 5 — Day 14

**Subject:** Two weeks in — would you let us feature you?

You have been on the Cloud beta for two weeks. Two asks:

1. **Would you let us feature [COMPANY] as a Cloud-beta user?** A logo on the pricing page, a one-line quote we draft together and you approve. No press release, no sales calls. The honesty around 5-day Example Reader cutover and "replays reliable enough to gate PRs on" is the brand — we want yours in the same shape, or not at all.

2. **Would you graduate to the paid Team tier when the beta ends?** $249/month, 5 seats, real-device input via WDA when v1.1 ships. Beta-grad pricing locks for 12 months at the current number even if list moves. Reply "yes" or "tell me more" — no contract until you sign one.

Either answer is fine. The Cloud beta itself stays free for the full 60 days regardless.

— Maurice
