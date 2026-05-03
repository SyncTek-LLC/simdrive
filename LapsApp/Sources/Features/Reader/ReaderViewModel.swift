import Foundation
import Observation

// ReaderViewModel — manages blog post selection and WKWebView content.
//
// WHY static HTML: WKWebView is XCTest-blind on iOS — SimDrive's vision-first observe exists
// specifically to navigate and interact with WKWebView content where XCTest cannot.
// Static HTML gives deterministic, reproducible content for journey replays.
// Real URLSession round-trips are deferred to cycle 4 (online hardening).

@Observable
@MainActor
final class ReaderViewModel {

    // MARK: - Post model

    struct BlogPost: Identifiable, Equatable, Sendable {
        let id: String         // slug used as accessibilityIdentifier suffix
        let title: String
        let summary: String
        let htmlContent: String
        let estimatedReadTime: Int  // minutes
    }

    // MARK: - State

    private(set) var posts: [BlogPost] = BlogPost.catalog
    var selectedPost: BlogPost?
    var isLoading: Bool = false

    // MARK: - Actions

    func selectPost(_ post: BlogPost) {
        isLoading = true
        selectedPost = post
        // Simulate a 200 ms "loading" state so SimDrive can observe the spinner
        Task {
            try? await Task.sleep(for: .milliseconds(200))
            isLoading = false
        }
    }

    func clearSelection() {
        selectedPost = nil
        isLoading = false
    }
}

// MARK: - Static blog post catalog

extension ReaderViewModel.BlogPost {
    static let catalog: [ReaderViewModel.BlogPost] = [
        ReaderViewModel.BlogPost(
            id: "morning-5k",
            title: "Your First 5K: A Beginner's Guide",
            summary: "Everything you need to know to run your first 5 kilometres.",
            htmlContent: Self.articleHTML(
                title: "Your First 5K: A Beginner's Guide",
                body: """
                <p>Running your first 5K is one of the most rewarding things you can do for your health.
                Start with the run-walk method: run for 60 seconds, walk for 90 seconds, and repeat eight times.</p>
                <h2>Week 1 Plan</h2>
                <ul>
                  <li>Monday: 20-minute run-walk</li>
                  <li>Wednesday: 20-minute run-walk</li>
                  <li>Friday: Rest or light stretch</li>
                  <li>Saturday: 25-minute run-walk</li>
                </ul>
                <h2>Gear Checklist</h2>
                <p>You don't need much: a pair of running shoes that fit well, moisture-wicking socks,
                and a phone for tracking. That's it.</p>
                <p>By week eight you'll run 5K continuously. Commit to the plan and trust the process.</p>
                """
            ),
            estimatedReadTime: 4
        ),
        ReaderViewModel.BlogPost(
            id: "marathon-training",
            title: "16 Weeks to Marathon Day",
            summary: "A science-backed plan for first-time marathon finishers.",
            htmlContent: Self.articleHTML(
                title: "16 Weeks to Marathon Day",
                body: """
                <p>Finishing a marathon is a bucket-list achievement. This 16-week plan builds you
                to 42.2 km safely, without overtraining.</p>
                <h2>The Three Rules</h2>
                <ol>
                  <li><strong>80 % easy runs</strong> — if you can't hold a conversation, slow down.</li>
                  <li><strong>One long run per week</strong> — the cornerstone of marathon training.</li>
                  <li><strong>Rest is training</strong> — two full rest days per week, non-negotiable.</li>
                </ol>
                <h2>Peak Week</h2>
                <p>Your longest run is 32 km, three weeks before race day. After that, taper:
                reduce mileage by 40 % each week so you arrive at the start line fresh.</p>
                <p>Nutrition matters most in the final 48 hours: carbohydrate-load with pasta, rice,
                and bread. Avoid fibre-heavy foods. Hydrate well but don't overdo it.</p>
                """
            ),
            estimatedReadTime: 6
        ),
        ReaderViewModel.BlogPost(
            id: "recovery-science",
            title: "The Science of Recovery",
            summary: "Why rest days make you faster, not slower.",
            htmlContent: Self.articleHTML(
                title: "The Science of Recovery",
                body: """
                <p>Athletes often make the mistake of treating rest days as wasted days.
                The truth is the opposite: fitness is built during recovery, not during the run itself.</p>
                <h2>What Happens When You Rest</h2>
                <p>During a hard run you create micro-tears in muscle fibres. In the 24–48 hours after,
                your body repairs and strengthens those fibres — but only if you rest, eat, and sleep.</p>
                <h2>Signs You Need More Recovery</h2>
                <ul>
                  <li>Resting heart rate elevated by more than 5 bpm</li>
                  <li>Mood changes or irritability</li>
                  <li>Persistent muscle soreness after 48 hours</li>
                  <li>Performance plateau despite consistent training</li>
                </ul>
                <p>Schedule two easy days for every hard workout. Your marathon time will thank you.</p>
                """
            ),
            estimatedReadTime: 5
        ),
    ]

    // WHY a helper: keeps the catalog readable; all posts share the same HTML shell.
    private static func articleHTML(title: String, body: String) -> String {
        """
        <!DOCTYPE html>
        <html lang="en">
        <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>\(title)</title>
        <style>
          body { font-family: -apple-system, sans-serif; padding: 16px; max-width: 700px; margin: 0 auto; line-height: 1.6; color: #222; }
          h1 { font-size: 1.5em; margin-bottom: 0.5em; }
          h2 { font-size: 1.1em; margin-top: 1.5em; color: #0066cc; }
          p  { margin: 0.8em 0; }
          ul, ol { padding-left: 1.5em; }
          li { margin: 0.4em 0; }
        </style>
        </head>
        <body>
        <h1>\(title)</h1>
        \(body)
        </body>
        </html>
        """
    }
}
