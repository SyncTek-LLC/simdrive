import SwiftUI

struct NavigationTab: View {
    @State private var showSheet = false
    @State private var counterValue = 0

    var body: some View {
        NavigationView {
            VStack(spacing: 24) {
                Text("Navigation Tab")
                    .font(.title)
                    .accessibilityIdentifier("lbl_nav_title")

                Button("Open Sheet") {
                    showSheet = true
                }
                .accessibilityIdentifier("btn_open_sheet")

                Button("Increment Counter") {
                    counterValue += 1
                }
                .accessibilityIdentifier("btn_increment")

                Text("Counter: \(counterValue)")
                    .accessibilityIdentifier("lbl_counter")

                Link("Open Safari", destination: URL(string: "https://apple.com")!)
                    .accessibilityIdentifier("link_safari")
            }
            .navigationTitle("Navigation")
            .sheet(isPresented: $showSheet) {
                VStack(spacing: 20) {
                    Text("Half Sheet")
                        .font(.title2)
                        .accessibilityIdentifier("lbl_sheet_title")
                    Button("Close Sheet") {
                        showSheet = false
                    }
                    .accessibilityIdentifier("btn_close_sheet")
                }
                .presentationDetents([.medium])
            }
        }
    }
}
