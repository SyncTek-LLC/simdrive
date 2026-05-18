import XCTest
@testable import LapsApp

@MainActor
final class SheetsViewModelTests: XCTestCase {

    func test_initialState_noSheetPresented() {
        let vm = SheetsViewModel()
        XCTAssertFalse(vm.isAddNoteSheetPresented)
        XCTAssertFalse(vm.isEditProfileSheetPresented)
        XCTAssertFalse(vm.isLiveActivityFullScreen)
        XCTAssertTrue(vm.notes.isEmpty)
    }

    func test_saveNote_addsNoteToList() {
        let vm = SheetsViewModel()
        vm.isAddNoteSheetPresented = true
        vm.draftNoteText = "Great 5K today!"
        vm.saveNote()
        XCTAssertEqual(vm.notes.count, 1)
        XCTAssertEqual(vm.notes[0].text, "Great 5K today!")
        XCTAssertFalse(vm.isAddNoteSheetPresented)
        XCTAssertTrue(vm.draftNoteText.isEmpty)
    }

    func test_saveNote_emptyText_doesNothing() {
        let vm = SheetsViewModel()
        vm.draftNoteText = "   "
        vm.saveNote()
        XCTAssertTrue(vm.notes.isEmpty)
    }

    func test_saveNote_multipleNotes_newestFirst() {
        let vm = SheetsViewModel()
        vm.draftNoteText = "First note"
        vm.saveNote()
        vm.draftNoteText = "Second note"
        vm.saveNote()
        XCTAssertEqual(vm.notes.first?.text, "Second note", "Newest note should be first")
    }

    func test_deleteNote_removesCorrectNote() {
        let vm = SheetsViewModel()
        vm.draftNoteText = "Note A"
        vm.saveNote()
        vm.draftNoteText = "Note B"
        vm.saveNote()
        let noteToDelete = vm.notes[1]
        vm.requestDeleteNote(noteToDelete)
        XCTAssertTrue(vm.isDeleteAlertPresented)
        vm.confirmDeleteNote()
        XCTAssertFalse(vm.notes.contains { $0.id == noteToDelete.id })
        XCTAssertFalse(vm.isDeleteAlertPresented)
    }

    func test_cancelDeleteNote_keepsNote() {
        let vm = SheetsViewModel()
        vm.draftNoteText = "Keep me"
        vm.saveNote()
        let note = vm.notes[0]
        vm.requestDeleteNote(note)
        vm.cancelDeleteNote()
        XCTAssertTrue(vm.notes.contains { $0.id == note.id })
    }

    func test_liveActivity_startsConfirmationDialog() {
        let vm = SheetsViewModel()
        vm.startLiveActivity()
        XCTAssertTrue(vm.isLiveActivityConfirmPresented)
        XCTAssertFalse(vm.liveActivityRunning)
    }

    func test_confirmLiveActivity_setsRunningAndFullScreen() {
        let vm = SheetsViewModel()
        vm.confirmLiveActivity()
        XCTAssertTrue(vm.liveActivityRunning)
        XCTAssertTrue(vm.isLiveActivityFullScreen)
    }

    func test_stopLiveActivity_clearsState() {
        let vm = SheetsViewModel()
        vm.confirmLiveActivity()
        vm.stopLiveActivity()
        XCTAssertFalse(vm.liveActivityRunning)
        XCTAssertFalse(vm.isLiveActivityFullScreen)
    }
}
