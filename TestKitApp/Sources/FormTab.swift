import SwiftUI

struct FormTab: View {
    @State private var firstName = ""
    @State private var lastName = ""
    @State private var password = ""
    @State private var searchText = ""
    @State private var notes = ""
    @State private var resultText = "Fill the form and tap Submit"
    // "Go to Page"-style UIAlertController prompt — exercises text entry into an
    // alert field (which HID keystrokes can't reach; host-AX set-value can).
    @State private var showGoToPage = false
    @State private var pageInput = ""
    @State private var goResult = "no page yet"

    var body: some View {
        NavigationView {
            Form {
                Section("Go to Page") {
                    Button("Go to Page…") { showGoToPage = true }
                        .accessibilityIdentifier("btn_go_to_page")
                    Text(goResult)
                        .accessibilityIdentifier("lbl_go_result")
                }
                Section("Identity") {
                    TextField("First Name", text: $firstName)
                        .accessibilityIdentifier("field_first_name")
                        .textContentType(.givenName)
                    TextField("Last Name", text: $lastName)
                        .accessibilityIdentifier("field_last_name")
                        .textContentType(.familyName)
                    SecureField("Password", text: $password)
                        .accessibilityIdentifier("field_password")
                        .textContentType(.password)
                }

                Section("Search") {
                    TextField("Search...", text: $searchText)
                        .accessibilityIdentifier("field_search")
                }

                Section("Notes") {
                    TextEditor(text: $notes)
                        .frame(height: 80)
                        .accessibilityIdentifier("field_notes")
                }

                Section {
                    Button("Submit") {
                        resultText = "First: \(firstName), Last: \(lastName), Pass: \(password.isEmpty ? "empty" : "set(\(password.count))")"
                    }
                    .accessibilityIdentifier("btn_submit")
                }

                Section("Result") {
                    Text(resultText)
                        .accessibilityIdentifier("lbl_result")
                }
            }
            .navigationTitle("TestKit")
            .alert("Go to Page", isPresented: $showGoToPage) {
                TextField("Enter a page number", text: $pageInput)
                    .accessibilityIdentifier("field_go_to_page")
                Button("Go") { goResult = "Went to page \(pageInput)" }
                Button("Cancel", role: .cancel) { }
            }
        }
    }
}
