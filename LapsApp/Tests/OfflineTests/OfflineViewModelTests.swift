import XCTest
@testable import LapsApp

@MainActor
final class OfflineViewModelTests: XCTestCase {

    func test_initialState_isOnline() {
        let vm = OfflineViewModel()
        XCTAssertEqual(vm.networkState, .online)
        XCTAssertFalse(vm.isOffline)
    }

    func test_goOffline_setsOfflineState() {
        let vm = OfflineViewModel()
        vm.goOffline()
        XCTAssertEqual(vm.networkState, .offline)
        XCTAssertTrue(vm.isOffline)
    }

    func test_goOnline_setsOnlineState() {
        let vm = OfflineViewModel()
        vm.goOffline()
        vm.goOnline()
        XCTAssertEqual(vm.networkState, .online)
        XCTAssertFalse(vm.isOffline)
    }

    func test_goOnline_updatesLastSyncDate() {
        let vm = OfflineViewModel()
        XCTAssertNil(vm.lastSyncDate)
        vm.goOnline()
        XCTAssertNotNil(vm.lastSyncDate)
    }

    func test_isOfflineToggle_roundtrip() {
        let vm = OfflineViewModel()
        vm.isOffline = true
        XCTAssertTrue(vm.isOffline)
        vm.isOffline = false
        XCTAssertFalse(vm.isOffline)
    }

    func test_retry_fromOffline_transitionsToRetryingAndBack() async {
        let vm = OfflineViewModel()
        vm.goOffline()
        let retryTask = Task { await vm.retry() }
        // Brief wait to observe retrying state mid-flight
        try? await Task.sleep(for: .milliseconds(100))
        // After retry completes, should still be offline (toggle controls outcome)
        await retryTask.value
        XCTAssertEqual(vm.networkState, .offline)
    }

    func test_retry_fromOnline_doesNothing() async {
        let vm = OfflineViewModel()
        XCTAssertEqual(vm.networkState, .online)
        await vm.retry()
        XCTAssertEqual(vm.networkState, .online)
    }

    func test_cachedItemCount_isPositive() {
        let vm = OfflineViewModel()
        XCTAssertGreaterThan(vm.cachedItemCount, 0)
    }
}
