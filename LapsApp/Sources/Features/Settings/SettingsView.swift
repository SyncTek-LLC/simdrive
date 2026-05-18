import SwiftUI

// SettingsView — toggles for notifications, location, analytics, and text size.
// Exercises `tap` + `observe` + `state assertion` in SimDrive journeys.
// Every interactive element carries an accessibilityIdentifier for stable SimDrive targeting.
struct SettingsView: View {
    @State private var viewModel = SettingsViewModel()

    var body: some View {
        List {
            Section("Notifications & Location") {
                Toggle("Push Notifications", isOn: $viewModel.notificationsEnabled)
                    .accessibilityIdentifier("settings_notifications_toggle")

                Toggle("Location Services", isOn: $viewModel.locationEnabled)
                    .accessibilityIdentifier("settings_location_toggle")
            }

            Section("Privacy") {
                Toggle("Analytics", isOn: $viewModel.analyticsEnabled)
                    .accessibilityIdentifier("settings_analytics_toggle")
            }

            Section("Accessibility") {
                HStack {
                    Text("Text Size")
                    Spacer()
                    Button(action: viewModel.decrementTextSize) {
                        Image(systemName: "minus.circle")
                    }
                    .accessibilityIdentifier("settings_text_size_decrement")
                    .disabled(viewModel.textSizeIndex == 0)

                    Text(viewModel.textSizeLabel)
                        .frame(minWidth: 80, alignment: .center)
                        .accessibilityIdentifier("settings_text_size_label")

                    Button(action: viewModel.incrementTextSize) {
                        Image(systemName: "plus.circle")
                    }
                    .accessibilityIdentifier("settings_text_size_increment")
                    .disabled(viewModel.textSizeIndex == 3)
                }

                // Extra-large shortcut — named identifier per spec §3 §9
                Button("Set Extra-Large") {
                    viewModel.textSizeIndex = 3
                }
                .accessibilityIdentifier("settings_text_size_xl")
            }

            Section {
                Button("Reset to Defaults", role: .destructive) {
                    viewModel.resetToDefaults()
                }
                .accessibilityIdentifier("settings_reset_button")
            }
        }
        .navigationTitle("Settings")
        .accessibilityIdentifier("settings_screen")
    }
}
