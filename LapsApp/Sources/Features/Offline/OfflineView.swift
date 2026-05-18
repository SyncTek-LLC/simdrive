import SwiftUI

// OfflineView — feature area 12: offline vs online UI states.
// Identifier: network_offline_toggle per spec §3 §12.
struct OfflineView: View {
    @State private var viewModel = OfflineViewModel()

    var body: some View {
        NavigationStack {
            List {
                Section("Network Simulation") {
                    Toggle(isOn: $viewModel.isOffline) {
                        Label("Simulate Offline", systemImage: "wifi.slash")
                    }
                    .accessibilityIdentifier("network_offline_toggle")
                }

                Section("Status") {
                    statusRow
                }

                Section("Content") {
                    if viewModel.networkState == .online {
                        onlineContent
                    } else {
                        offlineContent
                    }
                }
            }
            .navigationTitle("Network State")
            .accessibilityIdentifier("offline_screen")
        }
    }

    // MARK: - Status row

    private var statusRow: some View {
        HStack {
            Circle()
                .fill(statusColor)
                .frame(width: 10, height: 10)
            Text(statusText)
                .accessibilityIdentifier("network_status_label")
            Spacer()
            if let date = viewModel.lastSyncDate {
                Text("Last sync: \(date.formatted(.dateTime.hour().minute()))")
                    .font(.caption)
                    .foregroundStyle(.tertiary)
                    .accessibilityIdentifier("network_last_sync_label")
            }
        }
    }

    private var statusColor: Color {
        switch viewModel.networkState {
        case .online:   return .green
        case .offline:  return .red
        case .retrying: return .orange
        }
    }

    private var statusText: String {
        switch viewModel.networkState {
        case .online:   return "Online"
        case .offline:  return "Offline"
        case .retrying: return "Retrying…"
        }
    }

    // MARK: - Online content

    private var onlineContent: some View {
        Group {
            ForEach(0..<viewModel.cachedItemCount, id: \.self) { i in
                Label("Synced Activity #\(i + 1)", systemImage: "checkmark.circle.fill")
                    .foregroundStyle(.primary)
                    .accessibilityIdentifier("online_item_\(i)")
            }
        }
    }

    // MARK: - Offline content

    private var offlineContent: some View {
        Group {
            VStack(spacing: 16) {
                Image(systemName: "wifi.exclamationmark")
                    .font(.system(size: 48))
                    .foregroundStyle(.secondary)
                Text("No Connection")
                    .font(.headline)
                    .accessibilityIdentifier("offline_empty_title")
                Text("You're offline. Showing \(viewModel.cachedItemCount) cached activities.")
                    .font(.subheadline)
                    .multilineTextAlignment(.center)
                    .foregroundStyle(.secondary)
                    .accessibilityIdentifier("offline_empty_subtitle")

                if viewModel.networkState == .retrying {
                    ProgressView("Retrying…")
                        .accessibilityIdentifier("offline_retry_indicator")
                } else {
                    Button {
                        Task { await viewModel.retry() }
                    } label: {
                        Label("Retry", systemImage: "arrow.clockwise")
                    }
                    .buttonStyle(.bordered)
                    .accessibilityIdentifier("offline_retry_button")
                }
            }
            .padding(.vertical, 24)
            .frame(maxWidth: .infinity)

            // Cached items still visible in offline mode
            ForEach(0..<viewModel.cachedItemCount, id: \.self) { i in
                Label("Cached Activity #\(i + 1)", systemImage: "clock.arrow.circlepath")
                    .foregroundStyle(.secondary)
                    .accessibilityIdentifier("cached_item_\(i)")
            }
        }
    }
}
