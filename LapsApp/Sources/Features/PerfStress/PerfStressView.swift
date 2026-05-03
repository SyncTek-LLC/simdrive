import SwiftUI

// PerfStressView — feature area 11: 1000-row list for performance stress testing.
// Identifiers: year_list, year_chart per spec §3 §11.
struct PerfStressView: View {
    @State private var viewModel = PerfStressViewModel()

    var body: some View {
        NavigationStack {
            Group {
                if viewModel.isLoading {
                    loadingView
                } else if viewModel.rows.isEmpty {
                    emptyView
                } else if let selectedID = viewModel.selectedRowID,
                          let row = viewModel.rows.first(where: { $0.id == selectedID }) {
                    detailView(row: row)
                } else {
                    stressList
                }
            }
            .navigationTitle("Year in Laps")
            .accessibilityIdentifier("year_screen")
        }
    }

    // MARK: - Loading

    private var loadingView: some View {
        VStack(spacing: 16) {
            ProgressView()
                .scaleEffect(1.5)
                .accessibilityIdentifier("year_loading_indicator")
            Text("Loading 1,000 activities…")
                .foregroundStyle(.secondary)
        }
    }

    // MARK: - Empty state with load button

    private var emptyView: some View {
        VStack(spacing: 24) {
            Spacer()
            Image(systemName: "chart.bar.fill")
                .font(.system(size: 80))
                .foregroundStyle(.blue)
            Text("Year in Laps")
                .font(.title.bold())
            Text("Load your full activity history to see performance stats.")
                .multilineTextAlignment(.center)
                .foregroundStyle(.secondary)
            Button("Load 1,000 Activities") {
                Task { await viewModel.loadRows() }
            }
            .buttonStyle(.borderedProminent)
            .accessibilityIdentifier("year_load_button")
            Spacer()
        }
        .padding()
    }

    // MARK: - 1000-row stress list

    private var stressList: some View {
        VStack(spacing: 0) {
            // Perf render time badge
            if let ms = viewModel.renderTimeMS {
                Text("Rendered in \(String(format: "%.0f", ms)) ms")
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .padding(.vertical, 4)
                    .accessibilityIdentifier("year_render_time_label")
            }

            // The stress list — 1000 rows, each with a color swatch simulating a large image tile
            List(viewModel.rows) { row in
                Button { viewModel.selectRow(row.id) } label: {
                    HStack(spacing: 12) {
                        // Color swatch (~simulated image, large visual complexity)
                        RoundedRectangle(cornerRadius: 8)
                            .fill(Color(hue: row.colorHue, saturation: 0.7, brightness: 0.8))
                            .frame(width: 56, height: 56)
                            .accessibilityHidden(true)

                        VStack(alignment: .leading, spacing: 2) {
                            Text(row.title).font(.headline)
                            Text(row.subtitle).font(.caption).foregroundStyle(.secondary)
                            Text(row.stats).font(.caption2).foregroundStyle(.tertiary)
                        }
                    }
                }
                .accessibilityIdentifier("year_row_\(row.id)")
            }
            .listStyle(.plain)
            .accessibilityIdentifier("year_list")
        }
    }

    // MARK: - Detail view with "animated chart"

    private func detailView(row: PerfStressViewModel.StressRow) -> some View {
        ScrollView {
            VStack(spacing: 24) {
                // Simulated chart — geometric shapes with animation, stress-tests Core Animation
                YearChartView(hue: row.colorHue)
                    .frame(height: 200)
                    .accessibilityIdentifier("year_chart")

                VStack(alignment: .leading, spacing: 8) {
                    Text(row.title).font(.title2.bold())
                    Text(row.subtitle).foregroundStyle(.secondary)
                    Text(row.stats).font(.headline)
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding()

                Button("Back to List") { viewModel.clearSelection() }
                    .buttonStyle(.bordered)
                    .accessibilityIdentifier("year_back_button")
            }
            .padding()
        }
        .navigationTitle(row.title)
        .navigationBarTitleDisplayMode(.inline)
    }
}

// MARK: - Animated chart view (stress-tests Core Animation / SwiftUI rendering)

struct YearChartView: View {
    let hue: Double
    @State private var animationPhase: Double = 0

    var body: some View {
        GeometryReader { geo in
            let barCount = 12  // one bar per month
            let barWidth = geo.size.width / CGFloat(barCount) * 0.6
            let spacing = geo.size.width / CGFloat(barCount) * 0.4

            HStack(alignment: .bottom, spacing: spacing) {
                ForEach(0..<barCount, id: \.self) { i in
                    let height = geo.size.height * CGFloat(0.3 + 0.5 * sin(Double(i) * 0.7 + animationPhase))
                    RoundedRectangle(cornerRadius: 4)
                        .fill(Color(hue: hue, saturation: 0.7, brightness: 0.8).opacity(0.8))
                        .frame(width: barWidth, height: height)
                }
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .bottom)
        }
        .onAppear {
            withAnimation(.easeInOut(duration: 1.5).repeatForever(autoreverses: true)) {
                animationPhase = .pi
            }
        }
        .padding()
        .background(Color(.systemGray6))
        .clipShape(RoundedRectangle(cornerRadius: 12))
    }
}
