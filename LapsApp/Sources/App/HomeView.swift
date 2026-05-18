import SwiftUI

// HomeView — the Home tab root, housing auth (OAuth), sign-up (Forms), sheets,
// offline/network, and multi-app features as navigation destinations.
// Per spec §3 §5, the five primary tabs are Home, Activities, Search, Blog, Settings.
struct HomeView: View {
    var body: some View {
        NavigationStack {
            List {
                Section("Account") {
                    NavigationLink {
                        OAuthView()
                    } label: {
                        Label("Sign In", systemImage: "person.badge.key")
                    }
                    .accessibilityIdentifier("home_navlink_auth")

                    NavigationLink {
                        FormsView()
                    } label: {
                        Label("Sign Up", systemImage: "person.badge.plus")
                    }
                    .accessibilityIdentifier("home_navlink_signup")
                }

                Section("Workout Tools") {
                    NavigationLink {
                        SheetsView()
                    } label: {
                        Label("Sheets & Modals", systemImage: "rectangle.stack")
                    }
                    .accessibilityIdentifier("home_navlink_sheets")
                }

                Section("System") {
                    NavigationLink {
                        OfflineView()
                    } label: {
                        Label("Network State", systemImage: "wifi")
                    }
                    .accessibilityIdentifier("home_navlink_offline")

                    NavigationLink {
                        MultiAppView()
                    } label: {
                        Label("Launch Apps", systemImage: "square.grid.2x2")
                    }
                    .accessibilityIdentifier("home_navlink_multiapp")
                }
            }
            .navigationTitle("Home")
            .accessibilityIdentifier("home_screen")
        }
    }
}
