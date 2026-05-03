import Foundation
import Observation

// SheetsViewModel — manages sheet, fullScreenCover, and alert presentation state.
//
// WHY .alert instead of .confirmationDialog for the delete-confirmation:
// Per spec §4 cycle-3 finding, `confirmationDialog` is XCTest-blind on iOS 17.
// We include a `confirmationDialog` instance deliberately (the "Live Activity" confirmation)
// as a "deliberately fails" regression surface for SimDrive's dynamic-island journey,
// but use `.alert` for the delete-note flow where SimDrive needs to actually drive the action.

@Observable
@MainActor
final class SheetsViewModel {

    // MARK: - Note model

    struct Note: Identifiable, Equatable, Sendable {
        let id: UUID
        var text: String
        let createdAt: Date
    }

    // MARK: - Sheet state

    var isAddNoteSheetPresented: Bool = false
    var isEditProfileSheetPresented: Bool = false
    var isLiveActivityFullScreen: Bool = false
    var isDeleteAlertPresented: Bool = false
    var isLiveActivityConfirmPresented: Bool = false  // .confirmationDialog — deliberately hard for XCTest

    // MARK: - Content state

    private(set) var notes: [Note] = []
    var draftNoteText: String = ""
    var noteToDelete: Note? = nil

    var profileName: String = "Alex Runner"
    var profileBio: String = "Marathon enthusiast. 5K PB: 21:30."

    // WHY this flag: marks whether the Live Activity is "running" in the simulated Dynamic Island.
    // The `dynamic-island-shows-limitation` journey taps this and then fails — intentional.
    private(set) var liveActivityRunning: Bool = false

    // MARK: - Actions

    func saveNote() {
        let trimmed = draftNoteText.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return }
        notes.insert(Note(id: UUID(), text: trimmed, createdAt: Date()), at: 0)
        draftNoteText = ""
        isAddNoteSheetPresented = false
    }

    func requestDeleteNote(_ note: Note) {
        noteToDelete = note
        isDeleteAlertPresented = true
    }

    func confirmDeleteNote() {
        if let note = noteToDelete {
            notes.removeAll { $0.id == note.id }
        }
        noteToDelete = nil
        isDeleteAlertPresented = false
    }

    func cancelDeleteNote() {
        noteToDelete = nil
        isDeleteAlertPresented = false
    }

    func startLiveActivity() {
        isLiveActivityConfirmPresented = true
    }

    func confirmLiveActivity() {
        liveActivityRunning = true
        isLiveActivityConfirmPresented = false
        isLiveActivityFullScreen = true
    }

    func stopLiveActivity() {
        liveActivityRunning = false
        isLiveActivityFullScreen = false
    }
}
