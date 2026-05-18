import Foundation
import Observation

// OfflineViewModel — manual offline mode toggle per spec §3 §12.
//
// WHY a manual toggle instead of real Reachability: cycle 4 is the hardening cycle for
// real NWPathMonitor integration. Cycle 3 ships the UI states (offline, online, retry)
// so SimDrive's `offline-mode-graceful` journey can exercise graceful empty state
// without a real network dependency. The `network_offline_toggle` identifier is what
// the journey taps.

@Observable
@MainActor
final class OfflineViewModel {

    enum NetworkState: Equatable {
        case online
        case offline
        case retrying
    }

    // MARK: - State

    var networkState: NetworkState = .online
    private(set) var lastSyncDate: Date? = nil
    private(set) var cachedItemCount: Int = 12   // simulates a cached dataset

    // MARK: - Computed

    var isOffline: Bool {
        get { networkState == .offline || networkState == .retrying }
        set { networkState = newValue ? .offline : .online }
    }

    // MARK: - Actions

    // Retry simulates a brief "checking..." state before resolving back to the toggle value
    func retry() async {
        guard networkState == .offline else { return }
        networkState = .retrying
        try? await Task.sleep(for: .milliseconds(800))
        // Still offline — the toggle controls the outcome; retry just shows the UX state
        networkState = .offline
    }

    func goOnline() {
        networkState = .online
        lastSyncDate = Date()
    }

    func goOffline() {
        networkState = .offline
    }
}
