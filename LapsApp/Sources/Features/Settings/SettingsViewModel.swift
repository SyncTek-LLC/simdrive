import Foundation
import Observation

// SettingsViewModel — observable state for app settings.
// Persists all toggles to UserDefaults so state survives app restarts.
// Exercises: tap + state assertion in SimDrive `dark-mode-toggle` and `settings-*` journeys.
@Observable
final class SettingsViewModel {
    // MARK: - Keys (internal for testability)
    enum Keys {
        static let notificationsEnabled = "settings.notifications_enabled"
        static let locationEnabled = "settings.location_enabled"
        static let analyticsEnabled = "settings.analytics_enabled"
        static let textSizeIndex = "settings.text_size_index"
    }

    // MARK: - Persisted state
    var notificationsEnabled: Bool {
        didSet { defaults.set(notificationsEnabled, forKey: Keys.notificationsEnabled) }
    }

    var locationEnabled: Bool {
        didSet { defaults.set(locationEnabled, forKey: Keys.locationEnabled) }
    }

    var analyticsEnabled: Bool {
        didSet { defaults.set(analyticsEnabled, forKey: Keys.analyticsEnabled) }
    }

    // 0 = Small, 1 = Medium, 2 = Large, 3 = Extra-Large
    var textSizeIndex: Int {
        didSet { defaults.set(textSizeIndex, forKey: Keys.textSizeIndex) }
    }

    var textSizeLabel: String {
        switch textSizeIndex {
        case 0: return "Small"
        case 1: return "Medium"
        case 2: return "Large"
        case 3: return "Extra-Large"
        default: return "Medium"
        }
    }

    // MARK: - Init
    private let defaults: UserDefaults

    init(defaults: UserDefaults = .standard) {
        self.defaults = defaults
        // Load persisted values; fallback to sensible defaults
        notificationsEnabled = defaults.bool(forKey: Keys.notificationsEnabled)
        locationEnabled = defaults.bool(forKey: Keys.locationEnabled)
        analyticsEnabled = defaults.bool(forKey: Keys.analyticsEnabled)
        // Use object(forKey:) to distinguish "not persisted" (nil) from index 0 (Small).
        // integer(forKey:) returns 0 for missing keys, which incorrectly sets Small as default.
        if let stored = defaults.object(forKey: Keys.textSizeIndex) as? Int, (0...3).contains(stored) {
            textSizeIndex = stored
        } else {
            textSizeIndex = 1
        }
    }

    // MARK: - Actions
    func incrementTextSize() {
        textSizeIndex = min(textSizeIndex + 1, 3)
    }

    func decrementTextSize() {
        textSizeIndex = max(textSizeIndex - 1, 0)
    }

    func resetToDefaults() {
        notificationsEnabled = false
        locationEnabled = false
        analyticsEnabled = false
        textSizeIndex = 1
    }
}
