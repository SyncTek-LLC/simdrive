import SwiftUI

struct ContentView: View {
    var body: some View {
        TabView {
            FormTab()
                .tabItem { Label("Form", systemImage: "doc.text") }
                .accessibilityIdentifier("tab_form")
            NavigationTab()
                .tabItem { Label("Nav", systemImage: "arrow.right") }
                .accessibilityIdentifier("tab_nav")
        }
    }
}
