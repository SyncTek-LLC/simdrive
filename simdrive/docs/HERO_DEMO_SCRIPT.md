# SimDrive Hero Demo — 60-second Bug Repro & Validate

This is the recording script for the SimDrive hero MP4 / GIF that anchors the
positioning "**Reproduce and validate iOS bugs in 60 seconds with Claude.**"

Audience: prospective Pro / Team buyers landing on simdrive.dev and the PyPI
project page. The demo is muted (no voice-over) — captions carry the
narrative. Two output sizes: 1920×1080 MP4 for embeds and a 800×450 looping
GIF for the README hero.

> **For the Chairman to record.** Atlas drafted the storyboard, exact prompts,
> and terminal commands; the human recorder runs them in real time and
> captures the screen.

---

## Pre-flight checklist (10 minutes)

1. **Demo target app**: FamilyBag iOS (Chairman owns the codebase + a
   debug build on this Mac).
   - Repo: `~/Documents/familybag-ios`
   - Build & install on a fresh simulator: `bundle exec fastlane build_sim`
     (alternatively use SplashMate `bundle exec fastlane beta` if you prefer
     a customer-facing-looking app — both ship a sign-in screen).
2. **iOS Simulator**: `iPhone 17 / iOS 26.3` booted, freshly launched,
   FamilyBag installed and reset (no cached credentials).
3. **SimDrive**: `pip install --editable simdrive/` from this repo, then
   `simdrive trial start --email demo@synctek.io --offline-dev` to clear the
   paywall.
4. **Cursor or Claude Code**: open with the SimDrive MCP server wired in:
   ```json
   { "mcpServers": { "simdrive": { "command": "simdrive" } } }
   ```
   Restart Cursor / Claude Code so the 32 tools are visible.
5. **Recording tool**: QuickTime "New Screen Recording" alone is sufficient;
   ScreenFlow is preferred if you want post-roll captions + zoom emphasis.
6. **Window layout**: Cursor on the left half of the screen (1080 px wide),
   iOS Simulator on the right half (centred). Hide every other window.

---

## Storyboard — second-by-second

### 0:00 – 0:05  · Linear / Jira ticket on screen

Show a Linear ticket titled exactly:

> **ENG-1247 · Sign-in fails on iPhone 17 with iOS 26.3**
>
> Steps: open app → enter `test@example.com` → enter `pw123` → tap Sign In →
> see error toast "Network unavailable" even on full Wi-Fi.

If you don't want to use a real Linear board, mock this up in a Notes window
sized to 1080×600 with the title bold and the steps in a smaller font.

**Caption overlay:** `Bug: ENG-1247  ·  Sign-in fails on iPhone 17 / iOS 26.3`

### 0:05 – 0:08  · Cursor + SimDrive ready

Cut to Cursor on the left, iOS Simulator on the right (FamilyBag's launch
screen). The cursor focus is in the Cursor chat box, empty.

**Caption overlay:** `Cursor + SimDrive (32 MCP tools)`

### 0:08 – 0:13  · Operator types the prompt

In the Cursor chat box, type **exactly** this prompt (it must look like a
real ticket-handoff, not a polished demo line):

```
Use simdrive to reproduce ENG-1247 - sign-in fails on iPhone 17 / iOS 26.3.
Open the app, try test@example.com / pw123, capture whatever error shows.
```

Hit Enter at 0:13.

**Caption overlay:** `Prompt → Claude (no setup, no selectors)`

### 0:13 – 0:35  · Split-screen: Claude drives the simulator

This is the load-bearing 22 seconds. Claude calls the MCP tools in this
order (the captions list the exact MCP call names so viewers see the
toolchain at work):

| Seconds | MCP call shown in caption | What happens in the simulator |
|---------|---------------------------|-------------------------------|
| 0:13–0:15 | `session_start({device: "iPhone 17", os_version: "26.3", bundle_id: "com.synctek.familybag"})` | FamilyBag's sign-in screen comes to the foreground |
| 0:15–0:17 | `observe()` | Annotated screenshot flashes; numbered marks visible on form fields |
| 0:17–0:20 | `tap({text: "Email"})` → `type_text({text: "test@example.com"})` | Email field focuses, characters appear |
| 0:20–0:23 | `tap({text: "Password"})` → `type_text({text: "pw123"})` | Password field focuses, dots appear |
| 0:23–0:25 | `tap({text: "Sign In"})` | Button flashes, spinner appears |
| 0:25–0:30 | `observe()` | Error toast "Network unavailable" pops; SimDrive captures + annotates it |
| 0:30–0:35 | `record_stop({name: "ENG-1247-repro"})` | Cursor side shows "Saved recording to ~/.simdrive/recordings/ENG-1247-repro/" |

**Captions during this block:** keep the running MCP call name in the bottom
strip; switch each call exactly when Claude emits it.

### 0:35 – 0:45  · Operator types the validation prompt

After Claude prints its "captured, recording saved" summary, type:

```
Now I've fixed the bug. Validate the sign-in flow works -
test@example.com / pw123 should land on the Home screen.
```

Hit Enter at 0:45.

**Caption overlay:** `Same agent. Same tools. Now: validate.`

### 0:45 – 0:55  · Same flow re-runs, this time succeeds

Pre-arrange this: between takes (off camera), patch FamilyBag so sign-in
succeeds for `test@example.com / pw123`. Now the same sequence
(`session_start` → `observe` → tap → type → tap → type → tap → `observe`)
re-runs and the final `observe` shows the Home screen with the test user's
profile pill.

Caption sequence mirrors 0:13–0:35 but the final caption flips to:

> `observe() → Home screen reached. PASS.`

### 0:55 – 0:60  · End card

Full-screen card:

```
 Repro + validate in 47 seconds.
 Manual: 12 minutes.

 simdrive.dev
 Start your 14-day trial — pip install simdrive
```

Hold for 5 seconds. Fade out.

---

## Exact prompts to copy-paste

```text
Prompt #1 (0:08):
Use simdrive to reproduce ENG-1247 - sign-in fails on iPhone 17 / iOS 26.3.
Open the app, try test@example.com / pw123, capture whatever error shows.

Prompt #2 (0:35):
Now I've fixed the bug. Validate the sign-in flow works -
test@example.com / pw123 should land on the Home screen.
```

## Exact terminal commands to run before recording

```bash
# 1. Reset the simulator so the demo starts on a known screen.
xcrun simctl shutdown all
xcrun simctl erase "iPhone 17"
xcrun simctl boot "iPhone 17"
open -a Simulator

# 2. Install (or reinstall) FamilyBag for the demo.
cd ~/Documents/familybag-ios
bundle exec fastlane build_sim

# 3. Install SimDrive + trial license.
cd ~/Documents/specterqa-ios/simdrive
pip install --editable .
simdrive trial start --email demo@synctek.io --offline-dev

# 4. Verify the MCP wiring once.
simdrive --version
# Cursor / Claude Code: restart so .mcp.json picks up `simdrive`.
```

## Recording tool recommendation

- **Baseline (no editing required):** macOS QuickTime → File → New Screen
  Recording → Selected Portion → frame the Cursor + Simulator pair at
  1920×1080. Captions added in post via QuickTime Player → "Show Movie
  Properties" (limited but fine for a draft).
- **Preferred:** ScreenFlow ($169). Lets you add per-second caption strips,
  zoom emphases on the MCP call names, and export the same source at both
  1920×1080 MP4 and 800×450 GIF.
- **Free alternative to ScreenFlow:** record raw in QuickTime, then run
  `ffmpeg -i hero.mp4 -vf "scale=800:450,fps=12" hero.gif` for the GIF
  export.

## Output assets

| File | Size | Where it ships |
|------|------|----------------|
| `hero-60s.mp4` | 1920×1080, ≤ 25 MB | simdrive.dev landing page hero |
| `hero-60s.gif` | 800×450, ≤ 6 MB | README.md "60-second bug repro" section |
| `hero-poster.jpg` | 1920×1080 | Social cards + email signatures |

Drop final assets into `simdrive/docs/marketing/hero/` and surface their
paths in the simdrive-site repo via a PR. (Out of scope for this script — the
simdrive-site update is a separate workstream.)

---

## Why this demo works

The whole 60 seconds answers two questions a buyer asks in the first
minute:

1. **Will it actually drive my app?** The split-screen with live taps + the
   captioned MCP calls shows the agent *doing the thing*, not a polished
   render.
2. **What's the payoff?** The end-card pits 47 seconds against the 12-minute
   manual repro — the time-saved number is the wedge for the $29/mo
   conversion.

Do not embellish with stock music or 3D logo intros. The product is the
agent driving the simulator; everything else dilutes the proof.
