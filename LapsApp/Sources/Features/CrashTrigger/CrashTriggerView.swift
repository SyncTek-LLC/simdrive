import SwiftUI

// CrashTriggerView — developer menu for intentionally crashing the app.
// Exercises SimDrive's `ios_crashes` / `tool_crashes` retrieval capability
// in the `crash-and-recover` journey (journey #17, §4 of 07_test_app_spec.md).
//
// Per spec §3 §10: "Long-press the app icon on Settings → 'Crash now' menu item
// that calls fatalError". We expose this as a dev tab so no long-press gesture
// is required in Cycle 1 (long-press launcher is a Cycle 3 enhancement).
//
// accessibilityIdentifiers mirror spec exactly: dev_menu_open, dev_menu_crash.
struct CrashTriggerView: View {
    @State private var showConfirm = false

    var body: some View {
        VStack(spacing: 32) {
            Image(systemName: "ladybug.fill")
                .font(.system(size: 64))
                .foregroundStyle(.red)
                .accessibilityIdentifier("dev_menu_icon")

            Text("Developer Menu")
                .font(.title2.bold())
                .accessibilityIdentifier("dev_menu_title")

            Text("This tab is for SimDrive testing only.\nActions here intentionally break the app.")
                .font(.callout)
                .foregroundStyle(.secondary)
                .multilineTextAlignment(.center)
                .padding(.horizontal)

            Divider()

            // The crash button — confirmation gate prevents accidental trigger during casual demos.
            // Uses .alert (not confirmationDialog) so XCUITest can locate the action buttons
            // via stable accessibilityIdentifiers — confirmationDialog renders on a UIKit overlay
            // that falls outside XCTest's accessibility tree.
            Button(role: .destructive) {
                showConfirm = true
            } label: {
                Label("Crash Now", systemImage: "exclamationmark.triangle.fill")
                    .frame(maxWidth: .infinity)
                    .padding()
                    .background(Color.red.opacity(0.15))
                    .clipShape(RoundedRectangle(cornerRadius: 12))
            }
            .accessibilityIdentifier("dev_menu_open")
            .padding(.horizontal)
            .alert("Crash the app?", isPresented: $showConfirm) {
                Button("Crash Now", role: .destructive) {
                    triggerCrash()
                }
                .accessibilityIdentifier("dev_menu_crash")

                Button("Cancel", role: .cancel) {}
                    .accessibilityIdentifier("dev_menu_cancel")
            } message: {
                Text("The app will crash immediately. SimDrive will capture the crash log via ios_crashes.")
            }

            Spacer()
        }
        .padding(.top, 48)
        .navigationTitle("Developer")
    }

    // Intentional crash — fatalError is the cleanest mechanism for crash-log capture testing.
    // SimDrive's `ios_crashes` tool reads crash logs from the simulator's crash reporter,
    // which populates only on unhandled errors (fatalError / force-unwrap nil).
    private func triggerCrash() {
        fatalError("LapsApp intentional crash — SimDrive crash-and-recover journey trigger")
    }
}
