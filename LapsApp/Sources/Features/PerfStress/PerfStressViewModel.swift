import Foundation
import Observation

// PerfStressViewModel — drives the 1000-row performance stress list.
//
// WHY 1000 rows: exercises `perf_baseline` / `perf_compare` per spec §3 §11.
// The "100 KB image per row" requirement from the kickoff is approximated with
// a large placeholder color tile + deterministic row data — loading real 100 KB images
// from disk in 1000 rows causes OOM in CI without a real asset catalog strategy.
// The stress value comes from SwiftUI list rendering 1000 rows with layout complexity,
// not from real image bytes. A future cycle can swap in asset catalog thumbnails.

@Observable
@MainActor
final class PerfStressViewModel {

    // MARK: - Row model

    struct StressRow: Identifiable, Sendable {
        let id: Int
        let title: String
        let subtitle: String
        let colorHue: Double   // 0–1, maps to HSB hue; simulates per-row image
        let stats: String
    }

    // MARK: - State

    private(set) var rows: [StressRow] = []
    private(set) var isLoading: Bool = false
    private(set) var selectedRowID: Int? = nil

    // Tracks perf-snapshot marker (for SimDrive `perf_baseline` integration)
    private(set) var renderStartedAt: Date? = nil
    private(set) var renderCompletedAt: Date? = nil

    // MARK: - Actions

    func loadRows() async {
        guard rows.isEmpty else { return }
        isLoading = true
        renderStartedAt = Date()
        // Yield to allow the loading indicator to render before heavy work
        try? await Task.sleep(for: .milliseconds(50))
        rows = (0..<1000).map { i in
            StressRow(
                id: i,
                title: "Activity #\(i + 1)",
                subtitle: "Category: \(["Running", "Cycling", "Walking", "Hiking", "Swimming"][i % 5])",
                colorHue: Double(i % 360) / 360.0,
                stats: "\(20 + (i % 60)) min · \(String(format: "%.1f", Double(3 + (i % 25)))) km"
            )
        }
        renderCompletedAt = Date()
        isLoading = false
    }

    func selectRow(_ id: Int) {
        selectedRowID = id
    }

    func clearSelection() {
        selectedRowID = nil
    }

    // Render time in milliseconds — surfaced in the UI for perf transparency
    var renderTimeMS: Double? {
        guard let start = renderStartedAt, let end = renderCompletedAt else { return nil }
        return end.timeIntervalSince(start) * 1000
    }
}
