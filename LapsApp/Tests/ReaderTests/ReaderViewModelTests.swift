import XCTest
@testable import LapsApp

@MainActor
final class ReaderViewModelTests: XCTestCase {

    func test_initialState_hasPosts_noneSelected() {
        let vm = ReaderViewModel()
        XCTAssertFalse(vm.posts.isEmpty)
        XCTAssertNil(vm.selectedPost)
        XCTAssertFalse(vm.isLoading)
    }

    func test_catalog_hasThreePosts() {
        let vm = ReaderViewModel()
        XCTAssertEqual(vm.posts.count, 3)
    }

    func test_postIDs_areUnique() {
        let ids = ReaderViewModel.BlogPost.catalog.map(\.id)
        let uniqueIDs = Set(ids)
        XCTAssertEqual(ids.count, uniqueIDs.count, "Post IDs must be unique for a11y identifier deduplication")
    }

    func test_postIDs_containNoSpaces() {
        // accessibilityIdentifier values must not contain spaces
        for post in ReaderViewModel.BlogPost.catalog {
            XCTAssertFalse(post.id.contains(" "), "Post ID '\(post.id)' must not contain spaces")
        }
    }

    func test_selectPost_setsSelectedPost() async {
        let vm = ReaderViewModel()
        let post = vm.posts[0]
        vm.selectPost(post)
        XCTAssertTrue(vm.isLoading, "isLoading should be true immediately after selectPost")
        // Wait for the 200ms loading simulation
        try? await Task.sleep(for: .milliseconds(300))
        XCTAssertFalse(vm.isLoading)
        XCTAssertEqual(vm.selectedPost, post)
    }

    func test_clearSelection_nilsSelectedPost() async {
        let vm = ReaderViewModel()
        vm.selectPost(vm.posts[0])
        try? await Task.sleep(for: .milliseconds(300))
        vm.clearSelection()
        XCTAssertNil(vm.selectedPost)
        XCTAssertFalse(vm.isLoading)
    }

    func test_htmlContent_isNotEmpty() {
        for post in ReaderViewModel.BlogPost.catalog {
            XCTAssertFalse(post.htmlContent.isEmpty, "Post \(post.id) has no HTML content")
            XCTAssertTrue(post.htmlContent.contains("<!DOCTYPE html>"), "Post \(post.id) HTML must be a full document")
        }
    }
}
