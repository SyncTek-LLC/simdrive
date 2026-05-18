import XCTest
@testable import LapsApp

@MainActor
final class PerfStressViewModelTests: XCTestCase {

    func test_initialState_empty() {
        let vm = PerfStressViewModel()
        XCTAssertTrue(vm.rows.isEmpty)
        XCTAssertFalse(vm.isLoading)
        XCTAssertNil(vm.selectedRowID)
        XCTAssertNil(vm.renderTimeMS)
    }

    func test_loadRows_produces1000Rows() async {
        let vm = PerfStressViewModel()
        await vm.loadRows()
        XCTAssertEqual(vm.rows.count, 1000)
        XCTAssertFalse(vm.isLoading)
    }

    func test_loadRows_isIdempotent() async {
        let vm = PerfStressViewModel()
        await vm.loadRows()
        let count = vm.rows.count
        await vm.loadRows()
        XCTAssertEqual(vm.rows.count, count, "loadRows must not duplicate")
    }

    func test_rowIDs_areUnique() async {
        let vm = PerfStressViewModel()
        await vm.loadRows()
        let ids = vm.rows.map(\.id)
        XCTAssertEqual(ids.count, Set(ids).count)
    }

    func test_colorHues_areInRange() async {
        let vm = PerfStressViewModel()
        await vm.loadRows()
        for row in vm.rows {
            XCTAssertTrue((0.0...1.0).contains(row.colorHue), "Hue out of range: \(row.colorHue)")
        }
    }

    func test_selectRow_setsSelectedID() async {
        let vm = PerfStressViewModel()
        await vm.loadRows()
        vm.selectRow(42)
        XCTAssertEqual(vm.selectedRowID, 42)
    }

    func test_clearSelection_nilsID() async {
        let vm = PerfStressViewModel()
        await vm.loadRows()
        vm.selectRow(0)
        vm.clearSelection()
        XCTAssertNil(vm.selectedRowID)
    }

    func test_renderTime_isPositiveAfterLoad() async {
        let vm = PerfStressViewModel()
        await vm.loadRows()
        if let ms = vm.renderTimeMS {
            XCTAssertGreaterThan(ms, 0)
        } else {
            XCTFail("renderTimeMS should not be nil after load")
        }
    }
}
