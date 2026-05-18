import XCTest
@testable import LapsApp

// Tests for SearchViewModel — TDD: tests capture the contract before implementation locked.
// Uses near-zero debounce (1 ms) so async tests stay fast without sleep loops.
@MainActor
final class SearchViewModelTests: XCTestCase {

    private var sut: SearchViewModel!

    override func setUp() {
        super.setUp()
        // 1 ms debounce so async expectations resolve instantly in CI
        sut = SearchViewModel(debounceInterval: 0.001)
    }

    override func tearDown() {
        sut = nil
        super.tearDown()
    }

    // MARK: - Initial state

    func test_initialQuery_isEmpty() {
        XCTAssertEqual(sut.query, "")
    }

    func test_initialResults_isEmpty() {
        XCTAssertTrue(sut.results.isEmpty)
    }

    func test_initialIsSearching_isFalse() {
        XCTAssertFalse(sut.isSearching)
    }

    // MARK: - Filtering

    func test_queryMatchingRunning_returnsRunningActivities() async throws {
        sut.query = "running"
        try await Task.sleep(for: .milliseconds(20))
        XCTAssertFalse(sut.results.isEmpty)
        XCTAssertTrue(sut.results.allSatisfy { $0.category == "Running" })
    }

    func test_queryMatchingName_returnsMatchingActivity() async throws {
        sut.query = "Morning Run"
        try await Task.sleep(for: .milliseconds(20))
        XCTAssertEqual(sut.results.count, 1)
        XCTAssertEqual(sut.results.first?.name, "Morning Run")
    }

    func test_queryCaseInsensitive_matchesLowercase() async throws {
        sut.query = "cycling"
        try await Task.sleep(for: .milliseconds(20))
        let categories = sut.results.map(\.category)
        XCTAssertTrue(categories.allSatisfy { $0 == "Cycling" })
        XCTAssertFalse(sut.results.isEmpty)
    }

    func test_queryNoMatch_returnsEmptyResults() async throws {
        sut.query = "xyznonexistent"
        try await Task.sleep(for: .milliseconds(20))
        XCTAssertTrue(sut.results.isEmpty)
    }

    // MARK: - Clear

    func test_clearQuery_resetsQueryToEmpty() async throws {
        sut.query = "run"
        try await Task.sleep(for: .milliseconds(20))
        sut.clearQuery()
        XCTAssertEqual(sut.query, "")
    }

    func test_clearQuery_resetsResultsToEmpty() async throws {
        sut.query = "run"
        try await Task.sleep(for: .milliseconds(20))
        XCTAssertFalse(sut.results.isEmpty, "Pre-condition: should have results")
        sut.clearQuery()
        XCTAssertTrue(sut.results.isEmpty)
    }

    func test_clearQuery_setsIsSearchingFalse() {
        sut.query = "run" // starts searching
        sut.clearQuery()  // should cancel immediately
        XCTAssertFalse(sut.isSearching)
    }

    // MARK: - Debounce cancellation

    func test_rapidQueryChanges_onlyLastQueryProducesResults() async throws {
        // Rapid-fire — only the last one should survive debounce
        sut.query = "run"
        sut.query = "walk"
        sut.query = "cycling"
        try await Task.sleep(for: .milliseconds(20))
        // All cycling results — earlier queries were cancelled
        XCTAssertTrue(sut.results.allSatisfy { $0.category == "Cycling" })
    }

    // MARK: - Activity model

    func test_activityAccessibilityIdentifier_matchesIndexPattern() {
        let activity = Activity(id: 3, name: "Park Sprint", category: "Running", distance: "1.5 km")
        XCTAssertEqual(activity.accessibilityIdentifier, "search_result_3")
    }

    func test_mockCorpus_hasTenEntries() {
        XCTAssertEqual(Activity.mockCorpus.count, 10)
    }
}
