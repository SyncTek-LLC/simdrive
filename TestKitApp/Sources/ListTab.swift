import SwiftUI

/// Mirrors the Example Reader Library sign-in pattern: a List with TextField
/// rows (not Form). This is the layout that crashes XCTest's
/// allElementsBoundByIndex on iOS 26.
struct ListTab: View {
    @State private var barcode = ""
    @State private var pin = ""
    @State private var loginResult = ""

    var body: some View {
        NavigationView {
            List {
                Section {
                    HStack {
                        Text("Library Card")
                            .frame(width: 100, alignment: .leading)
                        TextField("Enter barcode", text: $barcode)
                            .accessibilityIdentifier("list_field_barcode")
                    }
                    HStack {
                        Text("PIN")
                            .frame(width: 100, alignment: .leading)
                        SecureField("Enter PIN", text: $pin)
                            .accessibilityIdentifier("list_field_pin")
                    }
                }

                Section {
                    Button("Sign In") {
                        loginResult = "barcode=\(barcode), pin=\(pin.isEmpty ? "empty" : "set(\(pin.count))")"
                    }
                    .accessibilityIdentifier("list_btn_signin")
                }

                if !loginResult.isEmpty {
                    Section("Result") {
                        Text(loginResult)
                            .accessibilityIdentifier("list_lbl_result")
                    }
                }

                // Stress test: multiple cells with text to trigger the
                // deep-tree crash that Example Reader experienced
                Section("Catalog") {
                    ForEach(0..<5) { i in
                        HStack {
                            Image(systemName: "book")
                            VStack(alignment: .leading) {
                                Text("Book Title \(i + 1)")
                                Text("Author \(i + 1)")
                                    .font(.caption)
                                    .foregroundColor(.secondary)
                            }
                        }
                        .accessibilityIdentifier("list_book_\(i)")
                    }
                }
            }
            .navigationTitle("List Sign-In")
        }
    }
}
