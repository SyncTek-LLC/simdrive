import SwiftUI

// SearchView — search field with 300 ms debounce and autocomplete-style results list.
// Exercises SimDrive journeys: `search-with-debounce` (#9) and `search-clear-and-retry` (#10).
// accessibilityIdentifiers: search_field, search_result_<index> per §3 §4 spec.
struct SearchView: View {
    @State private var viewModel = SearchViewModel()

    var body: some View {
        VStack(spacing: 0) {
            // Search field — iOS 26 UITextField focus is exercised here
            HStack {
                Image(systemName: "magnifyingglass")
                    .foregroundStyle(.secondary)

                TextField("Search activities…", text: $viewModel.query)
                    .textFieldStyle(.plain)
                    .autocorrectionDisabled()
                    .textInputAutocapitalization(.never)
                    .accessibilityIdentifier("search_field")
                    .submitLabel(.search)

                if !viewModel.query.isEmpty {
                    Button(action: viewModel.clearQuery) {
                        Image(systemName: "xmark.circle.fill")
                            .foregroundStyle(.secondary)
                    }
                    .accessibilityIdentifier("search_clear_button")
                }
            }
            .padding(12)
            .background(Color(.secondarySystemBackground))
            .clipShape(RoundedRectangle(cornerRadius: 10))
            .padding()

            // Results or state indicators
            if viewModel.isSearching {
                ProgressView()
                    .padding()
                    .accessibilityIdentifier("search_progress")
                Spacer()
            } else if viewModel.query.isEmpty {
                ContentUnavailableView(
                    "Search Activities",
                    systemImage: "figure.run",
                    description: Text("Type to search your runs, walks, and rides.")
                )
                .accessibilityIdentifier("search_empty_prompt")
            } else if viewModel.results.isEmpty {
                ContentUnavailableView.search(text: viewModel.query)
                    .accessibilityIdentifier("search_no_results")
            } else {
                List(viewModel.results) { activity in
                    ActivityRow(activity: activity)
                }
                .listStyle(.plain)
                .accessibilityIdentifier("search_results_list")
            }
        }
        .navigationTitle("Search")
    }
}

// MARK: - Row

private struct ActivityRow: View {
    let activity: Activity

    var body: some View {
        HStack {
            VStack(alignment: .leading, spacing: 4) {
                Text(activity.name)
                    .font(.body.weight(.medium))
                Text(activity.category)
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
            Spacer()
            Text(activity.distance)
                .font(.callout.monospacedDigit())
                .foregroundStyle(.secondary)
        }
        .padding(.vertical, 4)
        .accessibilityIdentifier(activity.accessibilityIdentifier)
    }
}
