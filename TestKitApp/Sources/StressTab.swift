import SwiftUI

/// Covers crash-prone SwiftUI patterns:
///   - LazyVStack with 20 items (scroll recycling)
///   - List with 5 TextField rows (Palace pattern amplified)
///   - Nested Form with deep Section nesting + TextField/SecureField
///   - Alert triggered while a TextField is focused
struct StressTab: View {
    // List TextField state
    @State private var listFields: [String] = Array(repeating: "", count: 5)

    // Form state
    @State private var formField1 = ""
    @State private var formField2 = ""
    @State private var formSecure = ""
    @State private var formDeep1 = ""
    @State private var formDeep2 = ""

    // Alert-while-focused state
    @FocusState private var alertFieldFocused: Bool
    @State private var alertFieldText = ""
    @State private var showAlert = false

    var body: some View {
        NavigationView {
            ScrollView {
                VStack(alignment: .leading, spacing: 24) {

                    // ── LazyVStack scroll recycling ──────────────────────────
                    Text("LazyVStack (20 items)")
                        .font(.headline)
                        .accessibilityIdentifier("stress_lazy_header")

                    LazyVStack(spacing: 4) {
                        ForEach(0..<20) { i in
                            HStack {
                                Text("Item \(i + 1)")
                                    .accessibilityIdentifier("stress_lazy_item_\(i)")
                                Spacer()
                                Image(systemName: "checkmark.circle")
                                    .accessibilityIdentifier("stress_lazy_icon_\(i)")
                            }
                            .padding(.horizontal)
                            .padding(.vertical, 6)
                            .background(Color(.secondarySystemBackground))
                            .cornerRadius(8)
                        }
                    }

                    Divider()

                    // ── List with 5 TextField rows ───────────────────────────
                    Text("List TextField rows")
                        .font(.headline)
                        .accessibilityIdentifier("stress_list_header")

                    List {
                        ForEach(0..<5) { i in
                            HStack {
                                Text("Field \(i + 1)")
                                    .frame(width: 70, alignment: .leading)
                                TextField("Enter value \(i + 1)", text: $listFields[i])
                                    .accessibilityIdentifier("stress_list_field_\(i)")
                            }
                        }
                    }
                    .frame(height: 240)
                    .cornerRadius(10)

                    Divider()

                    // ── Nested Form with deep Section nesting ────────────────
                    Text("Nested Form")
                        .font(.headline)
                        .accessibilityIdentifier("stress_form_header")

                    Form {
                        Section("Level 1") {
                            TextField("Form field A", text: $formField1)
                                .accessibilityIdentifier("stress_form_field_a")
                            TextField("Form field B", text: $formField2)
                                .accessibilityIdentifier("stress_form_field_b")

                            Section("Level 2") {
                                SecureField("Secure field", text: $formSecure)
                                    .accessibilityIdentifier("stress_form_secure")

                                Section("Level 3") {
                                    TextField("Deep field 1", text: $formDeep1)
                                        .accessibilityIdentifier("stress_form_deep_1")
                                    TextField("Deep field 2", text: $formDeep2)
                                        .accessibilityIdentifier("stress_form_deep_2")
                                }
                            }
                        }
                    }
                    .frame(height: 280)
                    .cornerRadius(10)

                    Divider()

                    // ── Alert while TextField is focused ─────────────────────
                    Text("Alert while focused")
                        .font(.headline)
                        .accessibilityIdentifier("stress_alert_header")

                    TextField("Focus me, then tap Alert", text: $alertFieldText)
                        .focused($alertFieldFocused)
                        .accessibilityIdentifier("stress_alert_field")
                        .padding()
                        .background(Color(.secondarySystemBackground))
                        .cornerRadius(8)

                    Button("Show Alert While Focused") {
                        alertFieldFocused = true
                        showAlert = true
                    }
                    .accessibilityIdentifier("stress_alert_button")
                    .alert("Stress Alert", isPresented: $showAlert) {
                        Button("OK", role: .cancel) {
                            alertFieldFocused = false
                        }
                        .accessibilityIdentifier("stress_alert_ok")
                    } message: {
                        Text("Alert appeared while a TextField was focused.")
                    }

                    Spacer(minLength: 40)
                }
                .padding()
            }
            .navigationTitle("Stress")
        }
    }
}
