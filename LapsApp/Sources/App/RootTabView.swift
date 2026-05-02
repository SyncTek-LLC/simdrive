import SwiftUI

// Root tab bar — the primary navigation surface exercised by `tab-bar-tour-and-back` journey.
// Each tab has a stable accessibilityIdentifier so SimDrive's vision-first observe resolves it.
struct RootTabView: View {
    var body: some View {
        TabView {
            NavigationStack {
                SettingsView()
            }
            .tabItem { Label("Settings", systemImage: "gear") }
            .accessibilityIdentifier("tab_settings")

            NavigationStack {
                AppearanceView()
            }
            .tabItem { Label("Appearance", systemImage: "circle.lefthalf.filled") }
            .accessibilityIdentifier("tab_appearance")

            NavigationStack {
                CrashTriggerView()
            }
            .tabItem { Label("Dev", systemImage: "ladybug") }
            .accessibilityIdentifier("tab_dev")

            NavigationStack {
                SearchView()
            }
            .tabItem { Label("Search", systemImage: "magnifyingglass") }
            .accessibilityIdentifier("tab_search")
        }
    }
}
