#import "SpecterQASwizzler.h"
#import <objc/runtime.h>

/// Replacement IMP: always return YES so XCTest skips UI-interruption handling.
static BOOL fb_doesNotHandleUIInterruptions(id self, SEL _cmd) { return YES; }

@implementation SpecterQASwizzler

+ (void)disableUIInterruptionsHandling {
    Class cls = NSClassFromString(@"XCUIApplication");
    if (!cls) {
        NSLog(@"[SpecterQA] ⚠ XCUIApplication class not found — UI-interruption swizzle skipped");
        return;
    }
    SEL sel = NSSelectorFromString(@"doesNotHandleUIInterruptions");
    Method m = class_getInstanceMethod(cls, sel);
    if (m) {
        method_setImplementation(m, (IMP)fb_doesNotHandleUIInterruptions);
        NSLog(@"[SpecterQA] ✓ XCUIApplication UI-interruption handling disabled (WDA pattern)");
    } else {
        NSLog(@"[SpecterQA] ⚠ XCUIApplication doesNotHandleUIInterruptions selector not found");
    }
}

@end
