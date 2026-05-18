# LapsApp

A fitness/run-tracking iOS app built as the canonical SimDrive 1.0 dogfood platform.

MIT License — `github.com/SyncTek-LLC/LapsApp`

---

## What this is

LapsApp is a feature-rich consumer-grade iOS app designed to look and behave like something a real customer ships. It exercises every SimDrive MCP tool capability through realistic user journeys, not through a fixture screen. See `simdrive/docs/RD_SIMDRIVE_1.0/07_test_app_spec.md` for the full spec.

**iOS 17+ minimum deployment target. Swift 6 strict concurrency.**

---

## How to build and run (Xcode)

1. Install [XcodeGen](https://github.com/yonaskolb/XcodeGen): `brew install xcodegen`
2. `cd LapsApp && xcodegen generate`
3. `open LapsApp.xcodeproj`
4. Select an iPhone simulator (iOS 17+) and press ⌘R

To build from the command line:

```bash
cd LapsApp
xcodegen generate
xcodebuild -project LapsApp.xcodeproj \
  -scheme LapsApp \
  -destination 'generic/platform=iOS Simulator' \
  -configuration Debug build \
  CODE_SIGNING_ALLOWED=NO
```

To run tests:

```bash
xcodebuild test \
  -project LapsApp.xcodeproj \
  -scheme LapsAppTests \
  -destination 'platform=iOS Simulator,name=iPhone 16e' \
  CODE_SIGNING_ALLOWED=NO
```

---

## How to drive it with SimDrive

```bash
simdrive run --corpus journeys/
```

Each YAML journey in `journeys/` names a persona from `personas/` and drives through a feature area. See `JOURNEYS.md` for the full index.

---

## Cycle 1 feature areas (Cycle 2+ roadmap below)

| Tab | Feature | SimDrive capability exercised | Key identifiers |
|---|---|---|---|
| Settings | Toggles + UserDefaults persistence + text size stepper | `tap` + `observe` + state assertion | `settings_notifications_toggle`, `settings_location_toggle`, `settings_analytics_toggle`, `settings_text_size_xl`, `settings_text_size_label`, `settings_reset_button` |
| Appearance | Light/Dark/System switcher with `preferredColorScheme` | `ios_set_appearance` MCP tool | `settings_appearance_dark`, `settings_appearance_light`, `settings_appearance_system`, `appearance_current_label` |
| Dev | Crash trigger with confirmation alert | `ios_crashes` crash log retrieval | `dev_menu_open`, `dev_menu_crash`, `dev_menu_cancel` |
| Search | Debounced search field (300 ms) + filtered results list | `type_text` + wait-for-keyboard + `clear_field` | `search_field`, `search_clear_button`, `search_result_<index>`, `search_results_list` |

---

## Cycle 2 roadmap (feature areas 5-8)

- OAuth login (Sign in with Apple + Google via `ASWebAuthenticationSession`)
- WebView content reader (`WKWebView` blog posts)
- Lists with pull-to-refresh + infinite scroll
- Forms with async server-side validation

## Cycle 3 roadmap (feature areas 9-12)

- Sheets + modals + Dynamic Island (known limitation regression journey)
- Performance stress (1000-row activity list + animated chart)
- Offline / network simulation toggle
- Multi-app journey support

---

## Contributing

New feature areas are welcome only when they exercise a SimDrive capability not already covered. The journey YAML for the new area must be part of the PR. No telemetry, analytics, or feature drift unrelated to SimDrive's surface will be merged.

See `CONTRIBUTING.md` for the full policy (Cycle 3 deliverable).
