import SwiftUI

// Root tab bar — five primary tabs per spec §3 §5:
// Home, Activities, Search, Blog, Settings.
//
// Identifiers: tab_home, tab_activities, tab_search, tab_blog, tab_settings
// per spec §3 §5. All cycle 2+3 features are reachable via navigation pushes
// within these five primary tabs — no tab overflow / "More" grouping.
//
// Settings also hosts Appearance and the Developer (crash) menu as navigation links.
struct RootTabView: View {
    var body: some View {
        TabView {
            // MARK: Home — Auth, Forms, Sheets, Offline, MultiApp
            HomeView()
                .tabItem { Label("Home", systemImage: "house") }
                .accessibilityIdentifier("tab_home")

            // MARK: Activities — Activity list + Year in Laps (PerfStress)
            ActivitiesRootView()
                .tabItem { Label("Activities", systemImage: "list.bullet") }
                .accessibilityIdentifier("tab_activities")

            // MARK: Search
            NavigationStack { SearchView() }
                .tabItem { Label("Search", systemImage: "magnifyingglass") }
                .accessibilityIdentifier("tab_search")

            // MARK: Blog — WKWebView reader
            ReaderView()
                .tabItem { Label("Blog", systemImage: "doc.richtext") }
                .accessibilityIdentifier("tab_blog")

            // MARK: Settings — appearance, notifications, text size, dev menu, crash trigger
            NavigationStack { SettingsRootView() }
                .tabItem { Label("Settings", systemImage: "gear") }
                .accessibilityIdentifier("tab_settings")
        }
    }
}
