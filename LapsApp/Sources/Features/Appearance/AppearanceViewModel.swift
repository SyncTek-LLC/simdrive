import Foundation
import Observation

// AppearanceViewModel — holds the selected appearance mode.
// Exercises SimDrive's `ios_set_appearance` MCP tool via the `dark-mode-toggle` journey.
@Observable
final class AppearanceViewModel {
    enum Mode: String, CaseIterable, Identifiable {
        case system = "System"
        case light = "Light"
        case dark = "Dark"

        var id: String { rawValue }

        // Maps to UIUserInterfaceStyle for display bridging (view reads this)
        var displayName: String { rawValue }
    }

    var selectedMode: Mode

    private let defaults: UserDefaults
    private static let defaultsKey = "appearance.selected_mode"

    init(defaults: UserDefaults = .standard) {
        self.defaults = defaults
        let stored = defaults.string(forKey: Self.defaultsKey) ?? ""
        selectedMode = Mode(rawValue: stored) ?? .system
    }

    func select(_ mode: Mode) {
        selectedMode = mode
        defaults.set(mode.rawValue, forKey: Self.defaultsKey)
    }
}
