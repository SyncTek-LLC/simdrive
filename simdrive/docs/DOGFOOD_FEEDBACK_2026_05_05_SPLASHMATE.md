# SimDrive Dogfood Feedback — SplashMate v1.1.0 run, 2026-05-05

**Source:** Atlas (Claude Code Opus 4.7) running manual SplashMate dogfood after `mcp__simdrive__run_journey` was unusable on this MCP host.
**simdrive version loaded:** 1.0.0a6
**Sim target:** iPhone 17 Pro / iOS 26.3

This is a flat list of friction, missing capabilities, and confusing behaviors hit while driving 17 SplashMate journeys end-to-end via `mcp__simdrive__*` primitives. Ordered roughly by impact.

---

## Critical (blockers)

### S-1. `run_journey` MCP path silently fails on hosts without sampling support

**What happened:** Called `mcp__simdrive__run_journey` from Claude Code. Returned:
```json
{
  "outcome": "error",
  "failure_reason": "claude_call_failed: Method not found",
  "steps_executed": 0
}
```

**Root cause:** `simdrive.journey.mcp_sampling_client.MCPSamplingLLMClient` calls `session.create_message()` (MCP `sampling/createMessage`). Claude Code's MCP client doesn't implement this method, so the JSON-RPC call returns "Method not found." simdrive surfaces this generically as `claude_call_failed`.

**Why this hurts:** the entire MCP-native value prop ("no API key needed") evaporates on Claude Code, which is arguably the largest MCP client today. The user needs to discover the standalone CLI fallback (`simdrive run …` after `pip install simdrive[claude]`) by reading source code.

**Suggested fixes (any of these):**
1. **Pre-flight sampling probe at `session_start` or `run_journey` entry.** Call a no-op `sampling/createMessage` (or read MCP capabilities) and fail-fast with a structured error: `{code: "mcp_sampling_unavailable", message: "Connected MCP client (Claude Code) doesn't implement sampling/createMessage. Run via `simdrive run …` standalone, or use a host that supports sampling. See docs/MCP_SAMPLING.md."}`. Message should include the EXACT fallback command.
2. **Auto-fallback to standalone path** if `ANTHROPIC_API_KEY` is in the env — surface a one-line warning explaining the fallback and proceed.
3. **Document explicitly in README** which MCP hosts support sampling. Currently the README's MCP-first framing implies any MCP client works.

### S-2. simdrive 1.0.0a6 disk-vs-loaded drift warning fires on every response

```
"_simdrive_warning": "Loaded simdrive 1.0.0a6 but disk version is 1.0.0a4. Restart the MCP server (or your agent host) to pick up the upgrade."
```

This duplicates on every single tool response — observe, tap, swipe, type_text, app_state, crashes — quickly bloating the agent's context. With 50+ tool calls per journey and 17 journeys, that's 800+ duplicates of the same warning text in the agent's conversation, which pushes useful screen state out of view.

**Suggested fix:** drift warning should fire **once** on session_start (or at first tool call after drift is detected), then stop. Optionally include a `drift: true` boolean in subsequent responses without the prose. Alternatively, return it only on `version` and `doctor`, not every tool.

---

## High-value (frequent friction)

### S-3. `dismiss_sheet` swipe geometry doesn't always land on the grabber

The doc says "swipe down (20% → 70% of screen height)". On iPhone 17 Pro 1206×2622, that's y=524 → y=1835. SplashMate's SignInView sheet has its handle at the very top of the sheet (y≈760) and a custom presentation. The default swipe didn't dismiss it. Required manually swiping y=750 → y=2400 with `duration_ms=500`.

**Suggested fix:** auto-detect the sheet grabber via a tighter SoM mark for the grabber pill (visible in most modal sheets), or expose the swipe extents as parameters: `dismiss_sheet(start_y_pct, end_y_pct, duration_ms)`.

### S-4. `pre_grant_permissions` doesn't suppress alerts after fresh install

I ran `pre_grant_permissions(permissions: ["photos","camera","location"])` on a freshly-installed app. On launch, SplashMate still showed all three system dialogs (camera, photos, notifications). Either:

- The grant happens after the app has already inspected `AVCaptureDevice.authorizationStatus(.video)` etc., which freezes the permissions in-process; or
- TCC's grant for a sim app requires the app NOT to be running when granted; or
- iOS 26.3 has a stricter TCC interaction that breaks `simctl privacy grant` for these scopes.

**Suggested fix:** document the required ordering ("call `pre_grant_permissions` BEFORE first install — if the app is already installed, uninstall first"), or add a doctor check: after grant, verify via `simctl privacy <perm>` and warn if grant didn't stick.

### S-5. `crashes(since_session_start: true)` returns crashes from before session_start

Default filter says "Filter by session-start time (default true)." But I got back four .ips files with timestamps clearly before my `session_start` call. The mtime cutoff seems off by minutes (or uses a UTC/local conversion mismatch).

**Suggested fix:** verify the mtime comparison against the actual `simdrive` session start timestamp. Add a `since_unix` explicit cutoff for tests/agents that need exact filtering.

### S-6. SoM detection misses small system-dialog affordances

iOS 26.3 Strong Password sheet probably has a "Choose Different Password" link below "Fill Strong Password," but SoM didn't pick it up. All visible marks ended at y=2213 even though the screen extends to y=2622. Either the link is hidden by iOS until the user interacts with the sheet (in which case our agent doesn't know to interact), or it's there but below SoM's confidence threshold.

**Suggested fix:** offer an `observe(low_confidence: true)` option that returns marks below the default confidence threshold (e.g., raw_confidence ≥ 0.1) so agents can attempt taps on edge-case affordances. Or expose the raw OCR pass output so agents can grep for known strings even when SoM filters them out.

---

## Medium

### S-7. `app_state: "not-running"` when crashes were just emitted

When SplashMate crashed (CloudKit assertion), `app_state` returned `not-running` — but this looks identical to "user closed the app cleanly." For an autonomous runner trying to recover, those two cases call for different actions. Could `app_state` correlate with recent crash reports automatically: `{state: "crashed", last_crash_seconds_ago: 12, last_crash_path: "..."}`?

### S-8. `tap` text-resolution sometimes matches partial strings

Marks with low OCR confidence get truncated text fields like `"Spla"` or `"WebDriverAgen..."`. When I called `tap({text: "SplashMate"})`, it could plausibly match `"Spla"`. In practice I haven't seen a wrong-target tap from this, but the risk exists. Consider preferring full-text marks over substring matches when confidence < threshold.

### S-9. `swipe` lacks "scroll within sheet" semantics

Multiple times I needed to scroll within a presented sheet (not the underlying scroll view). The current `swipe(x1,y1,x2,y2)` works but I had to guess coordinates. A `scroll_within_sheet(direction, magnitude)` helper, or even `swipe(target: {sheet: "active"})`, would save observation rounds.

### S-10. Session-start pre-grant should accept an array of bundle ids

When a journey changes installed-app context (uninstall+reinstall) mid-session, I wanted to `pre_grant_permissions` for the new install. Currently this is per-session — but if the bundle id is the same and the underlying app changed, the grant may not re-apply. Document this; consider a `re_grant_after_install` flag.

---

## Low / nice-to-have

### S-11. `observe` annotated screenshot path

Every observe returns both `screenshot_path` (raw) and `annotated_path` (with red numbered boxes). For agents that don't need vision, the annotated PNG render adds latency. Consider `observe(annotate: false)` — already exists but defaults to `true`. Make `false` the default for `mcp__simdrive__*` and document `annotate: true` as opt-in for vision agents.

### S-12. `journey_path` and `persona_path` resolution

Worth making both relative-to-cwd-friendly. `mcp__simdrive__run_journey` requires absolute paths today. When journeys live in a `.specterqa/journeys/` dir relative to the repo, the agent has to resolve cwd manually.

### S-13. Better `_simdrive_warning` for sampling-not-supported

Right now sampling failure surfaces as `claude_call_failed: Method not found` inside the result — same as a network-flap or a real Anthropic API error. A distinct error code (`sampling_unsupported`) lets the agent decide between retry, fallback, and bail.

### S-15. No "true background→resume" primitive

`press_key(home)` sends app to background (good), but `xcrun simctl launch` to bring it back appears to often cold-restart the app (PID changes between calls). On iOS, true background→resume is what production users experience all the time; cold restart is only after iOS kills the app for memory pressure.

For an autonomous runner trying to differentiate "app suspended in background" from "app cold-restarted," the sequence is murky. Suggested:
- `mcp__simdrive__background_app(session_id)` — sends home key, returns the app to background, no relaunch attempted
- `mcp__simdrive__resume_app(session_id)` — taps the SplashBoard app icon for the session's bundle id, foregrounding without restart
- The combination would exercise the actual UIScene resume path that production users experience

This bit me on SplashMate's J16 (background-foreground) journey today.

### S-14. Recordings missing iOS-version sentinel

Any recording captured today is iOS 26.3-specific. When iOS 27 lands and dialog layouts shift, replays will silently drift past the SSIM threshold without knowing why. Capture `os_version` and `device_name` into recording metadata and warn on cross-OS replay.

---

## Things that worked great

- `observe` with SoM annotation made tap-by-text trivial.
- `tap(text: "...")`, `type_text(tap_first: {...})`, and `clear_field` covered 95% of UI manipulation needs.
- `crashes` returning structured .ips data including the triggered-thread backtrace was critical for diagnosing F-1 (CloudKit).
- `app_state` polling for "foreground" after `xcrun simctl launch` confirmed the app actually came up vs. crashed silently.
- `doctor` zero-arg run gave a clean health check before each journey.
- `pre_grant_permissions` succeeded mechanically — the issue is only that the grant didn't suppress runtime dialogs in this case.

## Out of scope but worth noting

- The simdrive license shows trial 13 days remaining. Once we have BAU (paid) license keys, the trial banner shouldn't appear in agent responses (or should be suppressible via env var) for clean transcripts.
