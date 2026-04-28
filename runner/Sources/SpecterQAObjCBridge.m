//
//  SpecterQAObjCBridge.m
//  SpecterQA Runner
//
//  v16.0.0 — see header for context.
//

#import "SpecterQAObjCBridge.h"

@implementation SpecterQAObjCBridge

+ (NSException *)tryBlock:(NS_NOESCAPE void (^)(void))block {
    @try {
        block();
        return nil;
    }
    @catch (NSException *exception) {
        return exception;
    }
}

@end
