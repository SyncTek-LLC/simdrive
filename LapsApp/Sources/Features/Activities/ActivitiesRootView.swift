import SwiftUI

// ActivitiesRootView — the Activities tab root, hosting the activities list and
// Year in Laps (PerfStress) sub-navigation. Per spec §3 §5, the Activities tab
// pushes detail screens — this root presents a sectioned menu of activity features.
struct ActivitiesRootView: View {
    var body: some View {
        NavigationStack {
            List {
                Section("Activity Feed") {
                    NavigationLink {
                        ActivitiesView()
                    } label: {
                        Label("Activity List", systemImage: "list.bullet")
                    }
                    .accessibilityIdentifier("activities_navlink")
                }

                Section("Performance") {
                    NavigationLink {
                        PerfStressView()
                    } label: {
                        Label("Year in Laps", systemImage: "chart.bar.fill")
                    }
                    .accessibilityIdentifier("year_navlink")
                }
            }
            .navigationTitle("Activities")
            .accessibilityIdentifier("activities_root_screen")
        }
    }
}
