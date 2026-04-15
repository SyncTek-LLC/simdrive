import SwiftUI

/// Minimal host app for the SpecterQA XCTest runner.
/// This app does nothing — it exists solely as a signing container
/// so the test bundle can deploy to physical iOS devices.
/// On simulator, the test bundle deploys without a host app.
@main
struct HostApp: App {
    var body: some Scene {
        WindowGroup {
            Text("SpecterQA Runner Host")
                .font(.caption)
                .foregroundColor(.secondary)
        }
    }
}
