import SwiftUI

struct FormTab: View {
    @State private var firstName = ""
    @State private var lastName = ""
    @State private var password = ""
    @State private var searchText = ""
    @State private var notes = ""
    @State private var resultText = "Fill the form and tap Submit"

    var body: some View {
        NavigationView {
            Form {
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
        }
    }
}
