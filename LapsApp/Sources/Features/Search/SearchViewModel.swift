import Foundation
import Observation

// SearchViewModel — debounced search over a static mock activity corpus.
// Exercises SimDrive `type_text` over 300 ms debounce, the wait-for-keyboard fix,
// and `clear_field` then re-type, per `07_test_app_spec.md §3 §4`.
//
// Debounce approach: a simple Task-based timer — no Combine dependency.
// Swift 6 strict-concurrency safe: timer is managed on @MainActor (via @Observable).
@Observable
@MainActor
final class SearchViewModel {
    // MARK: - Public state

    var query: String = "" {
        didSet { scheduleDebounce() }
    }
    private(set) var results: [Activity] = []
    private(set) var isSearching = false

    // MARK: - Configuration

    let debounceInterval: TimeInterval

    // MARK: - Private

    private var debounceTask: Task<Void, Never>?
    private let corpus: [Activity]

    init(
        corpus: [Activity] = Activity.mockCorpus,
        debounceInterval: TimeInterval = 0.300
    ) {
        self.corpus = corpus
        self.debounceInterval = debounceInterval
    }

    // MARK: - Debounce

    private func scheduleDebounce() {
        debounceTask?.cancel()
        guard !query.isEmpty else {
            results = []
            isSearching = false
            return
        }
        isSearching = true
        debounceTask = Task { [weak self] in
            guard let self else { return }
            try? await Task.sleep(for: .milliseconds(Int(debounceInterval * 1000)))
            guard !Task.isCancelled else { return }
            self.performFilter()
        }
    }

    private func performFilter() {
        let lower = query.lowercased()
        results = corpus.filter {
            $0.name.lowercased().contains(lower) ||
            $0.category.lowercased().contains(lower)
        }
        isSearching = false
    }

    // MARK: - Actions

    func clearQuery() {
        query = ""
        debounceTask?.cancel()
        results = []
        isSearching = false
    }
}

// MARK: - Activity model

struct Activity: Identifiable, Equatable {
    let id: Int
    let name: String
    let category: String
    let distance: String

    // accessibilityIdentifier pattern: search_result_<index> per spec §3 §4
    var accessibilityIdentifier: String { "search_result_\(id)" }

    static let mockCorpus: [Activity] = [
        Activity(id: 0, name: "Morning Run",      category: "Running",   distance: "5.2 km"),
        Activity(id: 1, name: "Lunch Walk",        category: "Walking",   distance: "2.0 km"),
        Activity(id: 2, name: "Evening Jog",       category: "Running",   distance: "4.8 km"),
        Activity(id: 3, name: "Park Sprint",       category: "Running",   distance: "1.5 km"),
        Activity(id: 4, name: "Commute Cycle",     category: "Cycling",   distance: "10.3 km"),
        Activity(id: 5, name: "Weekend Ride",      category: "Cycling",   distance: "32.1 km"),
        Activity(id: 6, name: "Trail Hike",        category: "Hiking",    distance: "8.7 km"),
        Activity(id: 7, name: "Treadmill Interval",category: "Running",   distance: "6.0 km"),
        Activity(id: 8, name: "Beach Walk",        category: "Walking",   distance: "3.3 km"),
        Activity(id: 9, name: "Mountain Climb",    category: "Hiking",    distance: "12.4 km"),
    ]
}
