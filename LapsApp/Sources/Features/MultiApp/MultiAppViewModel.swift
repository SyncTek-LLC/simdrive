import Foundation
import UIKit
import Observation

// MultiAppViewModel — manages deep links that open external apps.
// Exercises SimDrive's multi-app capability per spec §4 cycle-3.
//
// Opening external apps via UIApplication.shared.open tests SimDrive's ability
// to observe state after the host app is backgrounded and the target app is foregrounded.

@Observable
@MainActor
final class MultiAppViewModel {

    // MARK: - External app targets

    struct ExternalApp: Identifiable, Sendable {
        let id: String
        let name: String
        let urlString: String
        let systemImage: String
        let description: String
    }

    let externalApps: [ExternalApp] = [
        ExternalApp(
            id: "settings",
            name: "Settings",
            urlString: UIApplication.openSettingsURLString,
            systemImage: "gear",
            description: "Opens the iOS Settings app. Tests SimDrive multi-app transition."
        ),
        ExternalApp(
            id: "mail",
            name: "Mail",
            urlString: "mailto:",
            systemImage: "envelope",
            description: "Opens the Mail compose view. Tests URL scheme deep-link."
        ),
        ExternalApp(
            id: "maps",
            name: "Maps",
            urlString: "maps://",
            systemImage: "map",
            description: "Opens Apple Maps. Tests inter-app handoff from fitness context."
        ),
    ]

    // MARK: - Launch state

    enum LaunchResult: Equatable {
        case idle
        case launching(appID: String)
        case launched(appID: String)
        case cannotOpen(appID: String, reason: String)
    }

    private(set) var lastLaunchResult: LaunchResult = .idle

    // MARK: - Actions

    func openApp(_ app: ExternalApp) async {
        guard let url = URL(string: app.urlString) else {
            lastLaunchResult = .cannotOpen(appID: app.id, reason: "Invalid URL: \(app.urlString)")
            return
        }

        lastLaunchResult = .launching(appID: app.id)

        let canOpen = await UIApplication.shared.canOpenURL(url)
        guard canOpen else {
            lastLaunchResult = .cannotOpen(appID: app.id, reason: "URL scheme not supported")
            return
        }

        let opened = await UIApplication.shared.open(url)
        lastLaunchResult = opened
            ? .launched(appID: app.id)
            : .cannotOpen(appID: app.id, reason: "open() returned false")
    }
}
