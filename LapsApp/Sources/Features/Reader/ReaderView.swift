import SwiftUI

// ReaderView — feature area 3: WKWebView content (blog reader).
// WKWebView is XCTest-blind on iOS; this is a primary SimDrive test surface.
// Identifiers: blog_post_<slug>, blog_share per spec §3 §3.
struct ReaderView: View {
    @State private var viewModel = ReaderViewModel()
    @State private var shareItem: String?

    var body: some View {
        NavigationStack {
            if let post = viewModel.selectedPost {
                articleView(post: post)
            } else {
                postList
            }
        }
    }

    // MARK: - Post list

    private var postList: some View {
        List(viewModel.posts) { post in
            Button {
                viewModel.selectPost(post)
            } label: {
                VStack(alignment: .leading, spacing: 4) {
                    Text(post.title)
                        .font(.headline)
                    Text(post.summary)
                        .font(.subheadline)
                        .foregroundStyle(.secondary)
                        .lineLimit(2)
                    Text("\(post.estimatedReadTime) min read")
                        .font(.caption)
                        .foregroundStyle(.tertiary)
                }
                .padding(.vertical, 4)
            }
            .accessibilityIdentifier("blog_post_\(post.id)")
        }
        .navigationTitle("Blog")
        .accessibilityIdentifier("blog_list")
    }

    // MARK: - Article view

    private func articleView(post: ReaderViewModel.BlogPost) -> some View {
        Group {
            if viewModel.isLoading {
                ProgressView("Loading…")
                    .accessibilityIdentifier("blog_loading_indicator")
            } else {
                WebViewWrapper(htmlContent: post.htmlContent)
                    .ignoresSafeArea(edges: .bottom)
            }
        }
        .navigationTitle(post.title)
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            ToolbarItem(placement: .navigationBarLeading) {
                Button("Back") { viewModel.clearSelection() }
                    .accessibilityIdentifier("blog_back_button")
            }
            ToolbarItem(placement: .navigationBarTrailing) {
                Button {
                    shareItem = post.htmlContent
                } label: {
                    Image(systemName: "square.and.arrow.up")
                }
                .accessibilityIdentifier("blog_share")
            }
        }
        .sheet(item: $shareItem) { content in
            ActivityViewRepresentable(activityItems: [content])
        }
    }
}

// MARK: - String identifiable for sheet

extension String: @retroactive Identifiable {
    public var id: String { self }
}

// MARK: - UIActivityViewController wrapper

struct ActivityViewRepresentable: UIViewControllerRepresentable {
    let activityItems: [Any]

    func makeUIViewController(context: Context) -> UIActivityViewController {
        let controller = UIActivityViewController(
            activityItems: activityItems,
            applicationActivities: nil
        )
        return controller
    }

    func updateUIViewController(_ uiViewController: UIActivityViewController, context: Context) {}
}
