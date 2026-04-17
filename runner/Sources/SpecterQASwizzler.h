#import <Foundation/Foundation.h>

/// Swizzles XCUIApplication.doesNotHandleUIInterruptions to always return YES.
///
/// This is a WDA-proven pattern (WebDriverAgent) that prevents XCTest's
/// UI-interruption handling machinery from firing during test execution.
/// The machinery can trigger AX element queries on deallocated pointers,
/// which combined with rapid NotificationCenter posts causes SIGABRT.
@interface SpecterQASwizzler : NSObject
+ (void)disableUIInterruptionsHandling;
@end
