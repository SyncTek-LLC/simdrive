import SwiftUI
import UIKit

// MARK: - UIKit Wrappers

/// UITextField wrapped in UIViewRepresentable for UIKit ↔ SwiftUI bridge testing.
struct UIKitTextField: UIViewRepresentable {
    @Binding var text: String
    var placeholder: String
    var accessibilityId: String

    func makeUIView(context: Context) -> UITextField {
        let tf = UITextField()
        tf.placeholder = placeholder
        tf.borderStyle = .roundedRect
        tf.accessibilityIdentifier = accessibilityId
        tf.delegate = context.coordinator
        return tf
    }

    func updateUIView(_ uiView: UITextField, context: Context) {
        if uiView.text != text {
            uiView.text = text
        }
    }

    func makeCoordinator() -> Coordinator {
        Coordinator(text: $text)
    }

    final class Coordinator: NSObject, UITextFieldDelegate {
        @Binding var text: String
        init(text: Binding<String>) { self._text = text }
        func textField(_ textField: UITextField,
                       shouldChangeCharactersIn range: NSRange,
                       replacementString string: String) -> Bool {
            if let current = textField.text,
               let r = Range(range, in: current) {
                text = current.replacingCharacters(in: r, with: string)
            }
            return true
        }
    }
}

/// UILabel wrapped in UIViewRepresentable.
struct UIKitLabel: UIViewRepresentable {
    var text: String
    var accessibilityId: String

    func makeUIView(context: Context) -> UILabel {
        let lbl = UILabel()
        lbl.accessibilityIdentifier = accessibilityId
        lbl.numberOfLines = 0
        return lbl
    }

    func updateUIView(_ uiView: UILabel, context: Context) {
        uiView.text = text
    }
}

/// UIButton wrapped in UIViewRepresentable.
struct UIKitButton: UIViewRepresentable {
    var title: String
    var accessibilityId: String
    var onTap: () -> Void

    func makeUIView(context: Context) -> UIButton {
        let btn = UIButton(type: .system)
        btn.setTitle(title, for: .normal)
        btn.accessibilityIdentifier = accessibilityId
        btn.addTarget(context.coordinator, action: #selector(Coordinator.tapped), for: .touchUpInside)
        return btn
    }

    func updateUIView(_ uiView: UIButton, context: Context) {
        uiView.setTitle(title, for: .normal)
    }

    func makeCoordinator() -> Coordinator { Coordinator(onTap: onTap) }

    final class Coordinator: NSObject {
        let onTap: () -> Void
        init(onTap: @escaping () -> Void) { self.onTap = onTap }
        @objc func tapped() { onTap() }
    }
}

// MARK: - Bridge Detail View

/// Detail view with both a native SwiftUI TextField and the UIKit-wrapped field,
/// exercising the SwiftUI ↔ UIKit transition in a NavigationLink push.
struct BridgeDetailView: View {
    @State private var swiftUIText = ""
    @State private var uikitText = ""

    var body: some View {
        Form {
            Section("SwiftUI TextField") {
                TextField("SwiftUI native field", text: $swiftUIText)
                    .accessibilityIdentifier("bridge_detail_swiftui_field")
            }

            Section("UIKit TextField (bridged)") {
                UIKitTextField(
                    text: $uikitText,
                    placeholder: "UIKit bridged field",
                    accessibilityId: "bridge_detail_uikit_field"
                )
                .frame(height: 44)
            }

            Section("Values") {
                Text("SwiftUI: \(swiftUIText)")
                    .accessibilityIdentifier("bridge_detail_swiftui_value")
                Text("UIKit: \(uikitText)")
                    .accessibilityIdentifier("bridge_detail_uikit_value")
            }
        }
        .navigationTitle("Bridge Detail")
    }
}

// MARK: - UIKitBridgeTab

/// Covers UIKit ↔ SwiftUI transitions:
///   - UITextField wrapped in UIViewRepresentable
///   - UILabel wrapped in UIViewRepresentable
///   - UIButton wrapped in UIViewRepresentable
///   - NavigationLink to detail with both SwiftUI TextField and UIKit bridged field
struct UIKitBridgeTab: View {
    @State private var uikitFieldText = ""
    @State private var labelText = "Tap the button to update"
    @State private var tapCount = 0

    var body: some View {
        NavigationView {
            Form {
                Section("UIKit TextField") {
                    UIKitTextField(
                        text: $uikitFieldText,
                        placeholder: "UIKit text field",
                        accessibilityId: "bridge_uikit_textfield"
                    )
                    .frame(height: 44)
                    .accessibilityIdentifier("bridge_uikit_textfield")
                }

                Section("UIKit Label") {
                    UIKitLabel(
                        text: labelText,
                        accessibilityId: "bridge_uikit_label"
                    )
                    .frame(height: 44)
                    .accessibilityIdentifier("bridge_uikit_label")
                }

                Section("UIKit Button") {
                    UIKitButton(title: "UIKit Tap Me", accessibilityId: "bridge_uikit_button") {
                        tapCount += 1
                        labelText = "Tapped \(tapCount) time\(tapCount == 1 ? "" : "s")"
                    }
                    .frame(height: 44)
                    .accessibilityIdentifier("bridge_uikit_button")
                }

                Section("Navigation") {
                    NavigationLink("Open Bridge Detail View") {
                        BridgeDetailView()
                    }
                    .accessibilityIdentifier("bridge_nav_link")
                }

                if !uikitFieldText.isEmpty {
                    Section("UIKit Field Value") {
                        Text(uikitFieldText)
                            .accessibilityIdentifier("bridge_field_value")
                    }
                }
            }
            .navigationTitle("UIKit Bridge")
        }
    }
}
