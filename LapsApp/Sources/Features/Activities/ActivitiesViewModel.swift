import Foundation
import Observation

// ActivitiesViewModel — paginated activities list, 50 items per page.
// Exercises `swipe`, scroll performance, and the pull-to-refresh flow per spec §3 §8.
// In-process simulation only — no real network calls (deferred to cycle 4).

@Observable
@MainActor
final class ActivitiesViewModel {

    // MARK: - Activity model

    struct ActivityItem: Identifiable, Equatable, Sendable {
        let id: Int
        let name: String
        let category: String
        let distance: String
        let duration: String
        let date: String
    }

    // MARK: - Pagination state

    static let pageSize = 50
    // Total items to simulate — large enough to trigger multiple scroll pages
    static let totalItems = 200

    private(set) var items: [ActivityItem] = []
    private(set) var isLoadingPage: Bool = false
    private(set) var isRefreshing: Bool = false
    private(set) var currentPage: Int = 0
    private(set) var hasMore: Bool = true

    // MARK: - Init

    init() {}

    // MARK: - Actions

    func loadInitialPage() async {
        guard items.isEmpty else { return }
        await loadPage(0, refresh: false)
    }

    // Pull-to-refresh — resets to page 0
    func refresh() async {
        guard !isRefreshing else { return }
        isRefreshing = true
        try? await Task.sleep(for: .milliseconds(600)) // simulate network fetch
        items = generatePage(0)
        currentPage = 0
        hasMore = Self.totalItems > Self.pageSize
        isRefreshing = false
    }

    // Infinite-scroll trigger — called when scroll nears bottom
    func loadNextPageIfNeeded(currentItem item: ActivityItem) async {
        guard !isLoadingPage, hasMore else { return }
        // Trigger when within the last 10 items of the current page
        guard let index = items.firstIndex(where: { $0.id == item.id }),
              index >= items.count - 10 else { return }
        await loadPage(currentPage + 1, refresh: false)
    }

    // MARK: - Private

    private func loadPage(_ page: Int, refresh: Bool) async {
        isLoadingPage = true
        try? await Task.sleep(for: .milliseconds(400)) // simulate async fetch
        let newItems = generatePage(page)
        if refresh {
            items = newItems
        } else {
            items.append(contentsOf: newItems)
        }
        currentPage = page
        hasMore = items.count < Self.totalItems
        isLoadingPage = false
    }

    // Generates deterministic mock items for a given page
    private func generatePage(_ page: Int) -> [ActivityItem] {
        let start = page * Self.pageSize
        let end = min(start + Self.pageSize, Self.totalItems)
        let categories = ["Running", "Cycling", "Walking", "Hiking", "Swimming"]
        return (start..<end).map { i in
            let cat = categories[i % categories.count]
            let km = Double(3 + (i % 25)) + Double(i % 10) / 10
            let mins = 20 + (i % 60)
            return ActivityItem(
                id: i,
                name: "\(cat) Session #\(i + 1)",
                category: cat,
                distance: String(format: "%.1f km", km),
                duration: "\(mins) min",
                date: "2026-0\(1 + (i % 4))-\(String(format: "%02d", 1 + (i % 28)))"
            )
        }
    }
}
