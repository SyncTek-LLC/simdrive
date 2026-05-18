import SwiftUI

// ActivitiesView — feature area 8: lists with pull-to-refresh + infinite scroll.
// 50 items per initial page; scroll-to-bottom triggers next page.
// Identifiers: activities_list, activity_row_<index> per spec §3 §8.
struct ActivitiesView: View {
    @State private var viewModel = ActivitiesViewModel()

    var body: some View {
        List {
            ForEach(viewModel.items) { item in
                activityRow(item)
                    .task {
                        await viewModel.loadNextPageIfNeeded(currentItem: item)
                    }
            }

            if viewModel.isLoadingPage {
                HStack {
                    Spacer()
                    ProgressView()
                    Spacer()
                }
                .accessibilityIdentifier("activities_loadmore_trigger")
                .listRowSeparator(.hidden)
            }

            if !viewModel.hasMore && !viewModel.items.isEmpty {
                HStack {
                    Spacer()
                    Text("All \(viewModel.items.count) activities loaded")
                        .font(.caption)
                        .foregroundStyle(.tertiary)
                    Spacer()
                }
                .accessibilityIdentifier("activities_end_indicator")
                .listRowSeparator(.hidden)
            }
        }
        .listStyle(.plain)
        .accessibilityIdentifier("activities_list")
        .navigationTitle("Activities")
        .refreshable {
            await viewModel.refresh()
        }
        .task {
            await viewModel.loadInitialPage()
        }
    }

    private func activityRow(_ item: ActivitiesViewModel.ActivityItem) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack {
                Text(item.name)
                    .font(.headline)
                Spacer()
                Text(item.distance)
                    .font(.subheadline)
                    .foregroundStyle(.blue)
            }
            HStack {
                Label(item.category, systemImage: categoryIcon(item.category))
                    .font(.caption)
                    .foregroundStyle(.secondary)
                Spacer()
                Text(item.duration)
                    .font(.caption)
                    .foregroundStyle(.secondary)
                Text(item.date)
                    .font(.caption)
                    .foregroundStyle(.tertiary)
            }
        }
        .padding(.vertical, 4)
        // accessibilityIdentifier pattern: activity_row_<index> per spec §3 §8
        .accessibilityIdentifier("activity_row_\(item.id)")
    }

    private func categoryIcon(_ category: String) -> String {
        switch category {
        case "Running":  return "figure.run"
        case "Cycling":  return "bicycle"
        case "Walking":  return "figure.walk"
        case "Hiking":   return "mountain.2"
        case "Swimming": return "figure.pool.swim"
        default:         return "sportscourt"
        }
    }
}
