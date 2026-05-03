import SwiftUI

// SettingsRootView — the Settings tab root per spec §3 §9.
// Hosts appearance, notifications, text size, and the developer crash menu
// as a NavigationStack with push-navigation sub-screens.
struct SettingsRootView: View {
    var body: some View {
        List {
            Section("Preferences") {
                NavigationLink {
                    AppearanceView()
                } label: {
                    Label("Appearance", systemImage: "circle.lefthalf.filled")
                }
                .accessibilityIdentifier("settings_navlink_appearance")

                NavigationLink {
                    SettingsView()
                } label: {
                    Label("Notifications & Privacy", systemImage: "bell")
                }
                .accessibilityIdentifier("settings_navlink_notifications")
            }

            Section("Developer") {
                NavigationLink {
                    CrashTriggerView()
                } label: {
                    Label("Developer Menu", systemImage: "ladybug")
                        .foregroundStyle(.red)
                }
                .accessibilityIdentifier("settings_navlink_dev")
            }
        }
        .navigationTitle("Settings")
        .accessibilityIdentifier("settings_root_screen")
    }
}
