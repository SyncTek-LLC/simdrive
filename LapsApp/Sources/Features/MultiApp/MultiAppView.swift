import SwiftUI

// MultiAppView — feature area "multi-app journey support" (cycle 3).
// Uses UIApplication.shared.open(URL) to launch Settings, Mail, Maps.
// Exercises SimDrive's multi-app capability per kickoff §4.3 Agent C.
// Identifiers: multiapp_open_<id> for each external app button.
struct MultiAppView: View {
    @State private var viewModel = MultiAppViewModel()

    var body: some View {
        NavigationStack {
            List {
                Section {
                    Text("Tap any button to open an external app. SimDrive's multi-app journey support observes the transition and app lifecycle change.")
                        .font(.subheadline)
                        .foregroundStyle(.secondary)
                        .listRowBackground(Color.clear)
                }

                Section("External Apps") {
                    ForEach(viewModel.externalApps) { app in
                        appRow(app)
                    }
                }

                Section("Last Launch") {
                    launchResultRow
                }
            }
            .navigationTitle("Multi-App")
            .accessibilityIdentifier("multiapp_screen")
        }
    }

    // MARK: - App row

    private func appRow(_ app: MultiAppViewModel.ExternalApp) -> some View {
        Button {
            Task { await viewModel.openApp(app) }
        } label: {
            HStack(spacing: 12) {
                Image(systemName: app.systemImage)
                    .frame(width: 30)
                    .foregroundStyle(.blue)
                VStack(alignment: .leading, spacing: 2) {
                    Text(app.name).font(.headline)
                    Text(app.description)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                        .lineLimit(2)
                }
                Spacer()
                Image(systemName: "arrow.up.right.square")
                    .foregroundStyle(.secondary)
            }
        }
        .accessibilityIdentifier("multiapp_open_\(app.id)")
    }

    // MARK: - Launch result

    private var launchResultRow: some View {
        HStack {
            switch viewModel.lastLaunchResult {
            case .idle:
                Label("No app launched yet", systemImage: "circle")
                    .foregroundStyle(.secondary)
                    .accessibilityIdentifier("multiapp_status_idle")
            case .launching(let id):
                Label("Launching \(id)…", systemImage: "clock")
                    .foregroundStyle(.orange)
                    .accessibilityIdentifier("multiapp_status_launching")
                ProgressView()
            case .launched(let id):
                Label("Launched: \(id)", systemImage: "checkmark.circle.fill")
                    .foregroundStyle(.green)
                    .accessibilityIdentifier("multiapp_status_launched")
            case .cannotOpen(let id, let reason):
                VStack(alignment: .leading, spacing: 2) {
                    Label("Cannot open: \(id)", systemImage: "xmark.circle")
                        .foregroundStyle(.red)
                    Text(reason).font(.caption).foregroundStyle(.secondary)
                }
                .accessibilityIdentifier("multiapp_status_error")
            }
        }
    }
}
