import SwiftUI

struct ContentView: View {
    var body: some View {
        TabView {
            FormTab()
                .tabItem { Label("Form", systemImage: "doc.text") }
                .accessibilityIdentifier("tab_form")
            ListTab()
                .tabItem { Label("List", systemImage: "list.bullet") }
                .accessibilityIdentifier("tab_list")
            NavigationTab()
                .tabItem { Label("Nav", systemImage: "arrow.right") }
                .accessibilityIdentifier("tab_nav")
            StressTab()
                .tabItem { Label("Stress", systemImage: "flame") }
                .accessibilityIdentifier("tab_stress")
            UIKitBridgeTab()
                .tabItem { Label("Bridge", systemImage: "arrow.left.arrow.right") }
                .accessibilityIdentifier("tab_bridge")
            Example ReaderPatternTab()
                .tabItem { Label("Example Reader", systemImage: "books.vertical") }
                .accessibilityIdentifier("tab_example")
        }
    }
}
