import SwiftUI

// SheetsView — feature area 6: .sheet, .fullScreenCover, .alert, .confirmationDialog.
// Exercises `dismiss_sheet` and sheet persistence per spec §3 §6.
// Identifiers: note_sheet_text, note_sheet_save, live_activity_start per spec.
struct SheetsView: View {
    @State private var viewModel = SheetsViewModel()

    var body: some View {
        NavigationStack {
            List {
                Section("Notes") {
                    Button {
                        viewModel.isAddNoteSheetPresented = true
                    } label: {
                        Label("Add Note", systemImage: "plus.circle")
                    }
                    .accessibilityIdentifier("notes_add_button")

                    if viewModel.notes.isEmpty {
                        Text("No notes yet")
                            .foregroundStyle(.secondary)
                            .accessibilityIdentifier("notes_empty_label")
                    } else {
                        ForEach(viewModel.notes) { note in
                            noteRow(note)
                        }
                    }
                }

                Section("Profile") {
                    Button {
                        viewModel.isEditProfileSheetPresented = true
                    } label: {
                        Label("Edit Profile", systemImage: "person.circle")
                    }
                    .accessibilityIdentifier("profile_edit_button")

                    HStack {
                        Text(viewModel.profileName)
                            .font(.headline)
                            .accessibilityIdentifier("profile_name_label")
                        Spacer()
                    }
                    Text(viewModel.profileBio)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                        .accessibilityIdentifier("profile_bio_label")
                }

                Section("Live Activity") {
                    // WHY this button: `dynamic-island-shows-limitation` journey taps here.
                    // `confirmationDialog` appears — XCTest-blind on iOS 17, deliberately hard for SimDrive.
                    Button {
                        viewModel.startLiveActivity()
                    } label: {
                        Label("Start Live Activity", systemImage: "bolt.circle")
                    }
                    .accessibilityIdentifier("live_activity_start")

                    if viewModel.liveActivityRunning {
                        Label("Live Activity running", systemImage: "bolt.fill")
                            .foregroundStyle(.orange)
                            .accessibilityIdentifier("live_activity_running_label")
                    }
                }
            }
            .navigationTitle("Sheets & Modals")
            .accessibilityIdentifier("sheets_screen")
        }
        // MARK: - Add Note sheet
        .sheet(isPresented: $viewModel.isAddNoteSheetPresented) {
            NavigationStack {
                VStack(spacing: 16) {
                    TextEditor(text: $viewModel.draftNoteText)
                        .frame(minHeight: 120)
                        .padding()
                        .background(Color(.systemGray6))
                        .clipShape(RoundedRectangle(cornerRadius: 10))
                        .padding()
                        .accessibilityIdentifier("note_sheet_text")
                    Spacer()
                }
                .navigationTitle("Add Note")
                .toolbar {
                    ToolbarItem(placement: .navigationBarLeading) {
                        Button("Cancel") {
                            viewModel.isAddNoteSheetPresented = false
                            viewModel.draftNoteText = ""
                        }
                        .accessibilityIdentifier("note_sheet_cancel")
                    }
                    ToolbarItem(placement: .navigationBarTrailing) {
                        Button("Save") { viewModel.saveNote() }
                            .disabled(viewModel.draftNoteText.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
                            .accessibilityIdentifier("note_sheet_save")
                    }
                }
            }
            .presentationDetents([.medium, .large])
        }
        // MARK: - Edit Profile sheet
        .sheet(isPresented: $viewModel.isEditProfileSheetPresented) {
            NavigationStack {
                Form {
                    Section("Name") {
                        TextField("Display name", text: $viewModel.profileName)
                            .accessibilityIdentifier("profile_sheet_name_field")
                    }
                    Section("Bio") {
                        TextField("Short bio", text: $viewModel.profileBio)
                            .accessibilityIdentifier("profile_sheet_bio_field")
                    }
                }
                .navigationTitle("Edit Profile")
                .toolbar {
                    ToolbarItem(placement: .navigationBarTrailing) {
                        Button("Done") { viewModel.isEditProfileSheetPresented = false }
                            .accessibilityIdentifier("profile_sheet_done")
                    }
                }
            }
        }
        // MARK: - Live Activity fullScreenCover
        .fullScreenCover(isPresented: $viewModel.isLiveActivityFullScreen) {
            liveActivityScreen
        }
        // MARK: - Delete Note alert (.alert — XCTest + SimDrive drivable)
        .alert("Delete Note?", isPresented: $viewModel.isDeleteAlertPresented) {
            Button("Delete", role: .destructive) { viewModel.confirmDeleteNote() }
                .accessibilityIdentifier("note_delete_confirm")
            Button("Cancel", role: .cancel) { viewModel.cancelDeleteNote() }
                .accessibilityIdentifier("note_delete_cancel")
        } message: {
            Text("This note will be permanently removed.")
        }
        // MARK: - Live Activity confirmationDialog
        // WHY: this is the "deliberately XCTest-blind" surface per spec.
        // The `dynamic-island-shows-limitation` journey encounters this and must fail with
        // a documented Dynamic Island / confirmationDialog limitation.
        .confirmationDialog(
            "Start Live Activity?",
            isPresented: $viewModel.isLiveActivityConfirmPresented,
            titleVisibility: .visible
        ) {
            Button("Start") { viewModel.confirmLiveActivity() }
            Button("Cancel", role: .cancel) {}
        } message: {
            Text("This will show a Live Activity in the Dynamic Island.")
        }
    }

    // MARK: - Note row

    private func noteRow(_ note: SheetsViewModel.Note) -> some View {
        HStack {
            Text(note.text)
                .lineLimit(2)
            Spacer()
            Button {
                viewModel.requestDeleteNote(note)
            } label: {
                Image(systemName: "trash")
                    .foregroundStyle(.red)
            }
            .accessibilityIdentifier("note_delete_\(note.id.uuidString.prefix(8))")
        }
        .accessibilityIdentifier("note_row_\(note.id.uuidString.prefix(8))")
    }

    // MARK: - Live Activity fullScreenCover content

    private var liveActivityScreen: some View {
        VStack(spacing: 24) {
            Spacer()
            Image(systemName: "bolt.fill")
                .font(.system(size: 60))
                .foregroundStyle(.orange)
            Text("Live Activity Active")
                .font(.title.bold())
                .accessibilityIdentifier("live_activity_fullscreen_label")
            Text("Dynamic Island display is simulated.\nActual Dynamic Island API is not supported in the simulator.")
                .multilineTextAlignment(.center)
                .font(.footnote)
                .foregroundStyle(.secondary)
                .accessibilityIdentifier("live_activity_limitation_text")
            Button("Stop") { viewModel.stopLiveActivity() }
                .buttonStyle(.borderedProminent)
                .accessibilityIdentifier("live_activity_stop")
            Spacer()
        }
        .padding()
    }
}
