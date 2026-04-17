# SpecterQA iOS — Session Handoff

**Date:** 2026-04-16
**From:** Atlas CEO session (Opus 4.6, ~18 hours across Apr 10-16)
**Branch:** `main` at `ea1ce8f`
**PyPI:** v13.0.1 (released but NOT production-ready)
**INIT:** INIT-2026-527 (dogfood fixes), INIT-2026-532 (test harness)

---

## Current State: 22/40 smoke tests passing — NOT shippable

### What works
- 29 MCP tools fully implemented
- TestKitApp with 5 tabs (Form, List, Nav, Stress, Palace patterns)
- AX backend (host-side, zero crashes, but only sees ~15 elements on SwiftUI)
- XCTest backend sees 58+ elements but crashes on UI transitions
- 40 live smoke tests against real iOS simulator
- WDA-proven crash mitigations applied (partial — see below)

### What's broken: XCTest runner crashes on UI transitions

**Root cause:** `[XCTRunnerIDESession logDebugMessage:]` → `NSKeyedArchiver` tries to serialize a message containing a deallocated AX element pointer during:
- Sheet/modal presentations
- Keyboard open + tab switch
- Notification cascades (borrow/download/library switch)
- `app.snapshot()` during view transitions

**WDA mitigation attempted:** `XCSetDebugLogger` (private symbol to replace the debug logger) — **symbol not found on Xcode 26**. This was WDA's production fix but Apple removed/renamed the symbol.

**Partial mitigations applied (in `SpecterQARunner.swift:applyCrashMitigations()`):**
- `XCTDisableRemoteQueryEvaluation = YES` ✓
- `DisableDiagnosticScreenRecordings = YES` ✓ 
- `DisableScreenshots = YES` ✓
- `XCSetDebugLogger` replacement — **FAILED** (symbol not in Xcode 26)

### 18 failing smoke tests (all from runner crash)

The failures cascade — once the runner dies at test ~27%, all subsequent tests fail because the runner is dead. The first crash trigger is `TestKeyboardDuringTabSwitch` which opens a keyboard then switches tabs.

---

## What the next session needs to do

### Priority 1: Fix the XCTest crash on Xcode 26

The `XCSetDebugLogger` symbol doesn't exist in Xcode 26. Options to investigate:

1. **Find the renamed symbol.** Run:
   ```bash
   nm -gU /Applications/Xcode.app/Contents/Developer/Platforms/iPhoneSimulator.platform/Developer/Library/Frameworks/XCTest.framework/XCTest | grep -i "debug\|logger\|log"
   ```
   The function may have been renamed to `_XCSetDebugLogger` or moved to a different class.

2. **Method swizzle `XCTDefaultDebugLogHandler`** instead of replacing the logger:
   ```swift
   // Swizzle -[XCTDefaultDebugLogHandler logDebugMessage:] to a no-op
   let original = class_getInstanceMethod(XCTDefaultDebugLogHandler.self, #selector(logDebugMessage:))
   let replacement = class_getInstanceMethod(SpecterQASafeDebugLogger.self, #selector(logDebugMessage:))
   method_exchangeImplementations(original, replacement)
   ```

3. **Disable the XCTest observation center entirely:**
   ```swift
   // XCTestObservationCenter.shared.removeTestObserver(...)
   // or intercept the notification that triggers the log
   ```

4. **Add a transition guard to element queries:**
   - Before `app.snapshot()`, wait 500ms
   - Check `app.state == .runningForeground`
   - Retry on failure instead of crashing

5. **Check if WDA's latest code has a different approach for Xcode 16+:**
   ```bash
   # Clone latest WDA and search for their logger fix
   git clone https://github.com/appium/WebDriverAgent.git /tmp/wda
   grep -r "XCSetDebugLogger\|XCTDefaultDebugLog\|logDebugMessage" /tmp/wda/
   ```

### Priority 2: Make all 40 smoke tests pass

The tests are correct — they trigger real crash scenarios. The tool must be fixed, not the tests. Each failing test represents a real user flow that crashes.

### Priority 3: AX backend SwiftUI traversal

The AX backend only sees ~15 elements on SwiftUI views because iOS 26's Simulator AX bridge flattens the tree. R&D confirmed this is a platform limitation — Accessibility Inspector uses DTXConnection (Xcode debugger protocol), not AXUIElement.

Possible approach: hybrid backend that uses XCTest for element queries and AX/CGEvent for actions. But this requires XCTest to not crash during queries.

### Priority 4: Palace dogfood

Palace source is at `/Users/atlas/Downloads/ios-core-modernize-whole-shot/` but can't compile on this machine (missing Carthage artifacts). Either:
- Get a pre-built `.app` from the Palace team
- Run `carthage bootstrap` to fetch AudioEngine.xcframework
- Test on the other machine where Palace is already installed

---

## Key files

| File | Purpose |
|------|---------|
| `runner/Sources/SpecterQARunner.swift` | XCTest runner — crash mitigations at `applyCrashMitigations()` |
| `runner/Sources/HTTPServer.swift` | Runner HTTP server — all endpoint handlers |
| `runner/Sources/TouchInjector.swift` | Tap, type, key press — typeText crash mitigations |
| `src/specterqa/ios/backends/ax_backend.py` | AX backend — host-side, zero crashes, 15 elements |
| `src/specterqa/ios/backends/xctest_client.py` | XCTest HTTP client |
| `src/specterqa/ios/mcp/server.py` | MCP server — all tool handlers, session management |
| `tests/smoke/test_live_session.py` | 13 functional smoke tests |
| `tests/smoke/test_crash_patterns.py` | 27 crash pattern + Palace + mitigation tests |
| `TestKitApp/` | 5-tab test app (Form, List, Nav, Stress, Palace patterns) |

## Key memories

- `feedback_fix_all_test_failures.md` — Fix ALL failures before shipping
- `feedback_live_test_before_pypi.md` — Live sim test before every PyPI release
- `feedback_no_mock_tests_specterqa.md` — No mock tests, all real behavior
- `feedback_testkit_complexity.md` — TestKitApp must mirror real-world complexity
- `feedback_testkit_tdd_gate.md` — Smoke tests are TDD gate for all development

## Session releases (v11.4.0 → v13.0.1)

18 releases across 6 days. Key versions:
- v11.5.0: 12 dogfood fixes, Element Resolver v2
- v12.0.0: Targeted ios_type (multi-field forms)
- v12.1.0: Test harness (10/10 live smoke tests)
- v12.2.0: Test architecture overhaul (deleted 19,932 lines of mock theater)
- v12.4.0: 27 crash pattern scenarios, StressTab, UIKitBridgeTab
- v12.5.0: Agent-first MCP instructions, perf workflow tools
- v12.6.0: PalacePatternTab (notification cascade, Combine progress, UIKit modal)
- v13.0.0: AXUIElement backend (zero crashes, but limited SwiftUI tree)
- v13.0.1: Default back to XCTest (AX too limited for SwiftUI)

## Bottom line

The tool has 29 MCP tools, comprehensive agent instructions, and a solid test harness. The blocker is the XCTest runner crash on iOS 26 when the app triggers UI transitions. The WDA fix (`XCSetDebugLogger`) doesn't work on Xcode 26 — the symbol was removed. The next session needs to find Xcode 26's equivalent or a different mitigation approach.
