//
//  PerfRoute.swift
//  SpecterQA Runner
//
//  GET /perf — process metrics via mach_task_basic_info (no simctl needed).
//
//  Works from inside the XCTest runner process without any entitlements.
//  Reports RSS, virtual memory, thread count, and CPU time.
//

import Foundation
import Darwin

struct PerfRoute: Route {
    let path = "/perf"
    let methods = ["GET"]

    func handle(request: ParsedRequest, deps: RouteDependencies) -> HTTPResponse {
        var result: [String: Any] = [:]

        // Memory via mach_task_basic_info
        var info = mach_task_basic_info()
        var count = mach_msg_type_number_t(MemoryLayout<mach_task_basic_info>.size) / 4
        let kr = withUnsafeMutablePointer(to: &info) {
            $0.withMemoryRebound(to: integer_t.self, capacity: Int(count)) {
                task_info(mach_task_self_, task_flavor_t(MACH_TASK_BASIC_INFO), $0, &count)
            }
        }
        if kr == KERN_SUCCESS {
            result["memory_rss_bytes"] = info.resident_size
            result["memory_virtual_bytes"] = info.virtual_size
            result["memory_rss_mb"] = Double(info.resident_size) / 1_048_576.0
            result["memory_virtual_mb"] = Double(info.virtual_size) / 1_048_576.0
        }

        // Thread count
        var threadList: thread_act_array_t?
        var threadCount: mach_msg_type_number_t = 0
        let tkr = task_threads(mach_task_self_, &threadList, &threadCount)
        if tkr == KERN_SUCCESS {
            result["thread_count"] = Int(threadCount)
            if let threads = threadList {
                vm_deallocate(
                    mach_task_self_,
                    vm_address_t(bitPattern: threads),
                    vm_size_t(Int(threadCount) * MemoryLayout<thread_act_t>.size)
                )
            }
        }

        // CPU time (user + system)
        var threadTimes = task_thread_times_info()
        var tiCount = mach_msg_type_number_t(MemoryLayout<task_thread_times_info>.size) / 4
        let tir = withUnsafeMutablePointer(to: &threadTimes) {
            $0.withMemoryRebound(to: integer_t.self, capacity: Int(tiCount)) {
                task_info(mach_task_self_, task_flavor_t(TASK_THREAD_TIMES_INFO), $0, &tiCount)
            }
        }
        if tir == KERN_SUCCESS {
            let userSec = Double(threadTimes.user_time.seconds) + Double(threadTimes.user_time.microseconds) / 1_000_000.0
            let sysSec  = Double(threadTimes.system_time.seconds) + Double(threadTimes.system_time.microseconds) / 1_000_000.0
            result["cpu_time_user_sec"]   = userSec
            result["cpu_time_system_sec"] = sysSec
            result["cpu_time_total_sec"]  = userSec + sysSec
        }

        result["process_id"] = ProcessInfo.processInfo.processIdentifier
        result["uptime_sec"]  = ProcessInfo.processInfo.systemUptime
        result["source"]      = "mach_task_basic_info"

        return HTTPResponse.ok(result)
    }
}
