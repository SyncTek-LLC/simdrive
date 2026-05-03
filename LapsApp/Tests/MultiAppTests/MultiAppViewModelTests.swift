import XCTest
@testable import LapsApp

// MultiAppViewModelTests — validates app catalog and URL construction.
// WHY not testing actual open(): UIApplication.shared.open() does not work in the test host
// (XCTest does not have a running app container that can open external URLs).
// We test the catalog, URL validity, and state machine; the live open() is exercised
// by the `multi-app-launch` SimDrive journey at runtime.
@MainActor
final class MultiAppViewModelTests: XCTestCase {

    func test_externalApps_notEmpty() {
        let vm = MultiAppViewModel()
        XCTAssertFalse(vm.externalApps.isEmpty)
    }

    func test_externalApps_idsAreUnique() {
        let vm = MultiAppViewModel()
        let ids = vm.externalApps.map(\.id)
        XCTAssertEqual(ids.count, Set(ids).count, "App IDs must be unique for a11y identifier deduplication")
    }

    func test_allApps_haveValidURLs() {
        let vm = MultiAppViewModel()
        for app in vm.externalApps {
            XCTAssertNotNil(URL(string: app.urlString), "App \(app.id) has invalid URL: \(app.urlString)")
        }
    }

    func test_allApps_haveNonEmptyNames() {
        let vm = MultiAppViewModel()
        for app in vm.externalApps {
            XCTAssertFalse(app.name.isEmpty)
            XCTAssertFalse(app.description.isEmpty)
        }
    }

    func test_initialLaunchResult_isIdle() {
        let vm = MultiAppViewModel()
        XCTAssertEqual(vm.lastLaunchResult, .idle)
    }

    func test_openApp_invalidURL_producesCannotOpen() async {
        let vm = MultiAppViewModel()
        // Inject an app with a deliberately invalid URL (empty string won't create a valid URL)
        let badApp = MultiAppViewModel.ExternalApp(
            id: "bad",
            name: "Bad",
            urlString: "",
            systemImage: "xmark",
            description: "Invalid URL test"
        )
        await vm.openApp(badApp)
        if case .cannotOpen(let id, _) = vm.lastLaunchResult {
            XCTAssertEqual(id, "bad")
        } else {
            XCTFail("Expected .cannotOpen for invalid URL, got \(vm.lastLaunchResult)")
        }
    }

    func test_settingsApp_isPresent() {
        let vm = MultiAppViewModel()
        let settings = vm.externalApps.first { $0.id == "settings" }
        XCTAssertNotNil(settings)
        XCTAssertEqual(settings?.name, "Settings")
    }
}
