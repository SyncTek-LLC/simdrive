# simdrive — Best Practices

Patterns that recur across dogfood runs. These aren't rules; they're the
shortcuts other simdrive users have found by hitting the wall first.

## Driving debounced inputs

Many iOS UIs debounce user input — search fields with `.debounce(for:)`, view
models with `DispatchQueue.main.asyncAfter`, view-controller hierarchies that
schedule layout passes via `setNeedsLayout` + `layoutIfNeeded` on the next
runloop tick. Under HID injection, simdrive dispatches keystrokes as fast as
the binary can drain its queue — typically faster than the debounce window.

**Rule of thumb:** after `type_text`, sleep ≥ the app's debounce window before
the next action. If you don't know the window, `time.sleep(0.5)` covers most
cases:

```python
sd.type_text(session_id, text="dance partner")
time.sleep(0.5)  # let .debounce(for: 0.3) fire
sd.observe(session_id)  # now the search results are populated
```

For replays, this pattern shows up as `replay halts at step N: SSIM 0.62 < 0.85`
on the post-step screenshot — your typed text landed, the screen just hadn't
finished rendering when you observed. Bake the sleep into the recording
(record_start → type_text → sleep → observe → record_stop) and replays line up.

## tap_first text-form fallback in rapid cycles

`tap(text: "...")` resolves the query against the most recent observe's marks
using exact > prefix > substring matching. In rapid cycles (loop a search
flow 100x to stress-test) the same text can OCR slightly differently from one
observe to the next, especially under non-deterministic anti-aliasing.

**Strategies that help, in increasing order of robustness:**

1. **Stay on `text=`** if the labels are stable plain English on a static
   layout. Cheapest, most readable.
2. **Switch to `stable_id`** once you have a known-good observe. The 20px
   bucketing means the same element keeps the same hash across observes as long
   as the layout doesn't shift more than ~10px in either axis.
3. **Switch to `stable_id_loose`** when intermittent layout drift (e.g. status
   bar animations, in-flight image loads) shifts elements >3px between observes.
   60px bucketing tolerates the drift at the cost of occasional false matches
   in dense screens.
4. **Combine:** record with `stable_id`, replay with `stable_id` first and
   fall back to text on miss. The recorder serializes both fields exactly
   for this case.

## Pre-grant permissions before launch, not after

The `dismiss_first_launch_alerts` tool handles permission alerts when they
appear, but the tap-the-Allow-button path is racy by nature (~1-in-4 alerts on
iOS 26 hand off to the underlying view between tap dispatch and tap landing —
simdrive retries once internally to close that window).

When you control the test harness, prefer `pre_grant_permissions` *before*
`session_start` launches the app. The grant lands in `simctl privacy grant`
state and the alert never shows. Cheaper, deterministic, no race.

## Use `version` to confirm the running server

After `pip install --upgrade simdrive`, the on-disk wheel is fresh but the
running MCP server keeps its old code in memory until restart. Call the
`version` tool to confirm:

```json
{"version": "0.3.0a3", "loaded_at": 1714512000.0, "disk_version": "0.3.0a3", "drift": false}
```

If `drift: true`, restart your MCP host (or the agent) before continuing — the
new code isn't running yet. The same warning rides along on every other tool
response in the `_simdrive_warning` field, so you'll see it even if you forget
to ask.
