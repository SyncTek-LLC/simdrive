import SwiftUI

// AppearanceView — explicit light/dark/system switcher.
// Exercises SimDrive's `ios_set_appearance` capability from the `dark-mode-toggle` journey.
// accessibilityIdentifiers match §3 §9 of 07_test_app_spec.md.
struct AppearanceView: View {
    @State private var viewModel = AppearanceViewModel()

    // Propagate the preference down to the hosting window via a custom environment key
    @Environment(\.colorScheme) private var currentScheme

    var body: some View {
        VStack(spacing: 24) {
            Text("Choose Appearance")
                .font(.headline)
                .accessibilityIdentifier("appearance_title")

            ForEach(AppearanceViewModel.Mode.allCases) { mode in
                Button(action: { viewModel.select(mode) }) {
                    HStack {
                        Image(systemName: iconName(for: mode))
                            .frame(width: 28)
                        Text(mode.displayName)
                            .frame(maxWidth: .infinity, alignment: .leading)
                        if viewModel.selectedMode == mode {
                            Image(systemName: "checkmark")
                                .foregroundStyle(.blue)
                        }
                    }
                    .padding()
                    .background(Color(.secondarySystemBackground))
                    .clipShape(RoundedRectangle(cornerRadius: 10))
                }
                .buttonStyle(.plain)
                .accessibilityIdentifier(accessibilityId(for: mode))
            }

            Spacer()

            Text("Current: \(viewModel.selectedMode.displayName)")
                .font(.caption)
                .foregroundStyle(.secondary)
                .accessibilityIdentifier("appearance_current_label")
        }
        .padding()
        .navigationTitle("Appearance")
        .preferredColorScheme(preferredScheme)
    }

    private var preferredScheme: ColorScheme? {
        switch viewModel.selectedMode {
        case .light: return .light
        case .dark: return .dark
        case .system: return nil
        }
    }

    private func iconName(for mode: AppearanceViewModel.Mode) -> String {
        switch mode {
        case .system: return "circle.lefthalf.filled"
        case .light: return "sun.max"
        case .dark: return "moon"
        }
    }

    private func accessibilityId(for mode: AppearanceViewModel.Mode) -> String {
        switch mode {
        case .system: return "settings_appearance_system"
        case .light: return "settings_appearance_light"
        case .dark: return "settings_appearance_dark"
        }
    }
}
