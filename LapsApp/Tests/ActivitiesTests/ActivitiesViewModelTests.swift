import XCTest
@testable import LapsApp

@MainActor
final class ActivitiesViewModelTests: XCTestCase {

    func test_initialState_isEmpty() {
        let vm = ActivitiesViewModel()
        XCTAssertTrue(vm.items.isEmpty)
        XCTAssertFalse(vm.isLoadingPage)
        XCTAssertFalse(vm.isRefreshing)
        XCTAssertTrue(vm.hasMore)
        XCTAssertEqual(vm.currentPage, 0)
    }

    func test_loadInitialPage_loads50Items() async {
        let vm = ActivitiesViewModel()
        await vm.loadInitialPage()
        XCTAssertEqual(vm.items.count, ActivitiesViewModel.pageSize)
        XCTAssertFalse(vm.isLoadingPage)
        XCTAssertTrue(vm.hasMore, "hasMore should remain true when not all items loaded")
    }

    func test_loadInitialPage_isIdempotent() async {
        let vm = ActivitiesViewModel()
        await vm.loadInitialPage()
        let countAfterFirst = vm.items.count
        await vm.loadInitialPage()
        XCTAssertEqual(vm.items.count, countAfterFirst, "loadInitialPage must not duplicate items")
    }

    func test_refresh_resetsToPage0() async {
        let vm = ActivitiesViewModel()
        await vm.loadInitialPage()
        let originalFirst = vm.items.first?.id
        await vm.refresh()
        XCTAssertEqual(vm.items.count, ActivitiesViewModel.pageSize)
        XCTAssertEqual(vm.items.first?.id, originalFirst, "Refresh should reload from page 0")
        XCTAssertEqual(vm.currentPage, 0)
    }

    func test_itemIDs_areUnique() async {
        let vm = ActivitiesViewModel()
        await vm.loadInitialPage()
        let ids = vm.items.map(\.id)
        XCTAssertEqual(ids.count, Set(ids).count, "Item IDs must be unique")
    }

    func test_allItems_haveNonEmptyFields() async {
        let vm = ActivitiesViewModel()
        await vm.loadInitialPage()
        for item in vm.items {
            XCTAssertFalse(item.name.isEmpty)
            XCTAssertFalse(item.category.isEmpty)
            XCTAssertFalse(item.distance.isEmpty)
            XCTAssertFalse(item.duration.isEmpty)
            XCTAssertFalse(item.date.isEmpty)
        }
    }
}
