//
//  SpecterQAObjCBridge.h
//  SpecterQA Runner
//
//  v16.0.0 — defense-in-depth ObjC bridge for catching NSException out of
//  XCTest calls. Swift cannot @try / @catch ObjC exceptions natively;
//  XCUICoordinate / snapshot APIs can throw on unexpected sim states, and
//  without this bridge the throw propagates through HTTPServer.runOnMain →
//  CFRunLoopPerformBlock and kills the test method.
//

#import <Foundation/Foundation.h>

NS_ASSUME_NONNULL_BEGIN

@interface SpecterQAObjCBridge : NSObject

/// Run @c block inside an ObjC @try / @catch.
/// @return The caught NSException on the calling thread, or @c nil on success.
///
/// `NS_SWIFT_NAME` pins the Swift call site as `tryBlock(_:)` because the
/// default Swift importer renames `tryBlock:` to `try(_:)` (which collides
/// with Swift's reserved keyword) and emits an "obsoleted in Swift 3" error.
+ (NSException * _Nullable)tryBlock:(NS_NOESCAPE void (^)(void))block
    NS_SWIFT_NAME(tryBlock(_:));

@end

NS_ASSUME_NONNULL_END
