import SwiftUI
import Combine
import UIKit

/// Reproduces the exact Example Reader patterns that crash XCTest:
/// 1. NotificationCenter cascade (5+ rapid posts)
/// 2. UIViewControllerRepresentable in a sheet
/// 3. Combine PassthroughSubject rapid updates (download progress)
/// 4. State machine transitions that fire multiple notifications
struct Example ReaderPatternTab: View {
    @State private var bookState = "unregistered"
    @State private var downloadProgress: Double = 0
    @State private var showLibrarySheet = false
    @State private var statusLog = "Ready"
    @State private var notificationCount = 0

    // Simulates Example Reader's download progress publisher
    @State private var progressCancellable: AnyCancellable?
    private let progressPublisher = PassthroughSubject<Double, Never>()

    var body: some View {
        NavigationView {
            List {
                Section("Book State Machine") {
                    Text("State: \(bookState)")
                        .accessibilityIdentifier("Example Reader_book_state")

                    Button("Borrow") {
                        simulateBorrow()
                    }
                    .accessibilityIdentifier("Example Reader_btn_borrow")

                    Button("Download") {
                        simulateDownload()
                    }
                    .accessibilityIdentifier("Example Reader_btn_download")

                    Button("Return") {
                        simulateReturn()
                    }
                    .accessibilityIdentifier("Example Reader_btn_return")

                    if downloadProgress > 0 && downloadProgress < 1 {
                        ProgressView(value: downloadProgress)
                            .accessibilityIdentifier("Example Reader_download_progress")
                    }
                }

                Section("Library Switch") {
                    Button("Switch Library") {
                        showLibrarySheet = true
                    }
                    .accessibilityIdentifier("Example Reader_btn_switch_library")
                }

                Section("Notification Flood") {
                    Text("Notifications fired: \(notificationCount)")
                        .accessibilityIdentifier("Example Reader_notification_count")

                    Button("Fire 10 Notifications") {
                        fireNotificationCascade(count: 10)
                    }
                    .accessibilityIdentifier("Example Reader_btn_fire_notifications")
                }

                Section("Status") {
                    Text(statusLog)
                        .accessibilityIdentifier("Example Reader_status_log")
                }
            }
            .navigationTitle("Example Reader Patterns")
            .sheet(isPresented: $showLibrarySheet) {
                // Pattern: UIViewControllerRepresentable wrapping UIKit VC
                UIKitLibrarySwitcher(onSelect: { library in
                    statusLog = "Switched to: \(library)"
                    showLibrarySheet = false
                    // Fire notification cascade on library switch
                    fireNotificationCascade(count: 5)
                })
            }
            .onReceive(progressPublisher) { progress in
                downloadProgress = progress
            }
        }
    }

    // MARK: - Example Reader State Machine Simulation

    private func simulateBorrow() {
        bookState = "borrowing"
        statusLog = "Borrowing..."
        // Fire cascade: registry change + state change + UI update
        NotificationCenter.default.post(name: .init("TPPBookRegistryDidChange"), object: nil)
        NotificationCenter.default.post(name: .init("TPPBookRegistryStateDidChange"), object: nil)
        NotificationCenter.default.post(name: .init("TPPBookProcessingDidChange"), object: nil)

        DispatchQueue.main.asyncAfter(deadline: .now() + 0.5) {
            bookState = "borrowed"
            statusLog = "Borrowed — ready to download"
            NotificationCenter.default.post(name: .init("TPPBookRegistryDidChange"), object: nil)
            NotificationCenter.default.post(name: .init("TPPBookRegistryStateDidChange"), object: nil)
            notificationCount += 5
        }
    }

    private func simulateDownload() {
        bookState = "downloading"
        statusLog = "Downloading..."
        downloadProgress = 0

        // Simulate rapid progress updates via Combine (every 100ms)
        var progress = 0.0
        progressCancellable = Timer.publish(every: 0.1, on: .main, in: .common)
            .autoconnect()
            .sink { _ in
                progress += 0.05
                if progress >= 1.0 {
                    progressPublisher.send(1.0)
                    progressCancellable?.cancel()
                    bookState = "ready"
                    statusLog = "Download complete"
                    // Fire multiple notifications on completion
                    NotificationCenter.default.post(name: .init("TPPMyBooksDownloadCenterDidChange"), object: nil)
                    NotificationCenter.default.post(name: .init("TPPBookRegistryDidChange"), object: nil)
                    NotificationCenter.default.post(name: .init("TPPBookRegistryStateDidChange"), object: nil)
                    notificationCount += 3
                } else {
                    progressPublisher.send(progress)
                }
            }
    }

    private func simulateReturn() {
        bookState = "returning"
        statusLog = "Returning..."
        NotificationCenter.default.post(name: .init("TPPBookRegistryDidChange"), object: nil)
        NotificationCenter.default.post(name: .init("TPPBookRegistryStateDidChange"), object: nil)

        DispatchQueue.main.asyncAfter(deadline: .now() + 0.3) {
            bookState = "unregistered"
            statusLog = "Book returned"
            NotificationCenter.default.post(name: .init("TPPBookRegistryDidChange"), object: nil)
            notificationCount += 3
        }
    }

    private func fireNotificationCascade(count: Int) {
        for i in 0..<count {
            NotificationCenter.default.post(
                name: .init("TPPTestNotification_\(i)"),
                object: nil,
                userInfo: ["index": i, "timestamp": Date()]
            )
        }
        notificationCount += count
        statusLog = "Fired \(count) notifications"
    }
}

// MARK: - UIKit Library Switcher (UIViewControllerRepresentable)

struct UIKitLibrarySwitcher: UIViewControllerRepresentable {
    var onSelect: (String) -> Void

    func makeUIViewController(context: Context) -> UINavigationController {
        let vc = LibrarySwitchTableViewController()
        vc.onSelect = onSelect
        return UINavigationController(rootViewController: vc)
    }

    func updateUIViewController(_ uiViewController: UINavigationController, context: Context) {}
}

class LibrarySwitchTableViewController: UITableViewController {
    var onSelect: ((String) -> Void)?
    let libraries = ["Test Library Test Library", "New York Public Library", "Brooklyn Public Library",
                     "Queens Public Library", "Chicago Public Library"]

    override func viewDidLoad() {
        super.viewDidLoad()
        title = "Select Library"
        tableView.register(UITableViewCell.self, forCellReuseIdentifier: "cell")
        navigationItem.rightBarButtonItem = UIBarButtonItem(
            barButtonSystemItem: .cancel,
            target: self,
            action: #selector(cancel)
        )
        view.accessibilityIdentifier = "Example Reader_library_list"
    }

    @objc private func cancel() {
        dismiss(animated: true)
    }

    override func tableView(_ tableView: UITableView, numberOfRowsInSection section: Int) -> Int {
        libraries.count
    }

    override func tableView(_ tableView: UITableView, cellForRowAt indexPath: IndexPath) -> UITableViewCell {
        let cell = tableView.dequeueReusableCell(withIdentifier: "cell", for: indexPath)
        cell.textLabel?.text = libraries[indexPath.row]
        cell.accessibilityIdentifier = "Example Reader_library_\(indexPath.row)"
        return cell
    }

    override func tableView(_ tableView: UITableView, didSelectRowAt indexPath: IndexPath) {
        onSelect?(libraries[indexPath.row])
    }
}
