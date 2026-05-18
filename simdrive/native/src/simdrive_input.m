// simdrive-input — minimal HID injector for the iOS simulator.
//
// Sends real UITouch / keyboard / button events through the same path
// Simulator.app uses internally (SimDeviceLegacyHIDClient + IndigoMessage).
// Synthetic mouse events through the macOS window do not trigger
// UITextField first-responder requests on iOS 26 SwiftUI; this binary
// bypasses that limitation.
//
// Wire-format + helpers derived from idb's MIT-licensed FBSimulatorIndigoHID
// (https://github.com/facebook/idb), reduced to the minimum needed and
// stripped of FBControlCore dependencies.
//
// Build:   make
// Usage:
//   simdrive-input <udid> tap <x_points> <y_points>
//   simdrive-input <udid> down <x_points> <y_points>
//   simdrive-input <udid> up <x_points> <y_points>
//   simdrive-input <udid> button <home|lock|side|siri>
//   simdrive-input <udid> key <keycode>            (HID usage code; e.g. 4=A, 40=Return)
//   simdrive-input <udid> text "Hello"

#import <Foundation/Foundation.h>
#import <CoreGraphics/CoreGraphics.h>
#import <objc/runtime.h>
#import <dlfcn.h>
#import <mach/mach.h>
#import <mach/mach_time.h>
#import "Indigo.h"

// Forward-declare CoreSimulator + SimulatorKit interfaces we use.
@interface SimServiceContext : NSObject
+ (instancetype)sharedServiceContextForDeveloperDir:(NSString *)devDir error:(NSError **)error;
- (id)defaultDeviceSetWithError:(NSError **)error;
@end

@interface SimDeviceSet : NSObject
- (NSArray *)devices;
@end

@interface SimDeviceType : NSObject
- (CGSize)mainScreenSize;
- (float)mainScreenScale;
@end

@interface SimDevice : NSObject
- (NSUUID *)UDID;
- (SimDeviceType *)deviceType;
@end

@interface SimDeviceLegacyHIDClient : NSObject
- (instancetype)initWithDevice:(SimDevice *)device error:(NSError **)error;
- (void)sendWithMessage:(IndigoMessage *)message
           freeWhenDone:(BOOL)freeWhenDone
        completionQueue:(dispatch_queue_t)queue
             completion:(void (^)(NSError *))completion;
@end

// SimulatorKit private functions (resolved via dlsym).
typedef IndigoMessage *(*MessageForMouseFn)(CGPoint *, CGPoint *, int, int, BOOL);
typedef IndigoMessage *(*MessageForKeyboardFn)(int, int);
typedef IndigoMessage *(*MessageForButtonFn)(int, int, int);

static MessageForMouseFn  gMessageForMouse;
static MessageForKeyboardFn gMessageForKeyboard;
static MessageForButtonFn gMessageForButton;

#define DIRECTION_DOWN 1
#define DIRECTION_UP   2

static int loadFrameworks(NSError **outErr) {
  void *handles[2] = {0};
  handles[0] = dlopen(
    "/Library/Developer/PrivateFrameworks/CoreSimulator.framework/CoreSimulator", RTLD_NOW);
  if (!handles[0]) { fprintf(stderr, "dlopen CoreSimulator: %s\n", dlerror()); return 1; }
  handles[1] = dlopen(
    "/Applications/Xcode.app/Contents/Developer/Library/PrivateFrameworks/SimulatorKit.framework/SimulatorKit",
    RTLD_NOW);
  if (!handles[1]) { fprintf(stderr, "dlopen SimulatorKit: %s\n", dlerror()); return 2; }
  gMessageForMouse = dlsym(handles[1], "IndigoHIDMessageForMouseNSEvent");
  gMessageForKeyboard = dlsym(handles[1], "IndigoHIDMessageForKeyboardArbitrary");
  gMessageForButton = dlsym(handles[1], "IndigoHIDMessageForButton");
  if (!gMessageForMouse || !gMessageForKeyboard || !gMessageForButton) {
    fprintf(stderr, "dlsym Indigo functions failed\n"); return 3;
  }
  return 0;
}

static SimDevice *findDevice(NSString *udid, NSError **err) {
  Class CtxClass = objc_lookUpClass("SimServiceContext");
  NSString *devDir = @"/Applications/Xcode.app/Contents/Developer";
  id ctx = [CtxClass sharedServiceContextForDeveloperDir:devDir error:err];
  if (!ctx) return nil;
  id deviceSet = [ctx defaultDeviceSetWithError:err];
  if (!deviceSet) return nil;
  for (SimDevice *d in [deviceSet devices]) {
    if ([[[d UDID] UUIDString] isEqualToString:udid]) return d;
  }
  return nil;
}

static SimDeviceLegacyHIDClient *makeHIDClient(SimDevice *device, NSError **err) {
  Class ClientClass = objc_lookUpClass("SimulatorKit.SimDeviceLegacyHIDClient");
  if (!ClientClass) {
    *err = [NSError errorWithDomain:@"simdrive" code:10 userInfo:@{NSLocalizedDescriptionKey:@"SimDeviceLegacyHIDClient class missing"}];
    return nil;
  }
  return [[ClientClass alloc] initWithDevice:device error:err];
}

// Build a touch IndigoMessage. Mirrors idb's +touchMessageWithPayload:.
// xRatio / yRatio are 0..1 from top-left, computed by caller using the device's
// pixel screen size and scale.
static IndigoMessage *buildTouchMessage(double xRatio, double yRatio, int direction) {
  CGPoint p = (CGPoint){xRatio, yRatio};
  IndigoMessage *template = gMessageForMouse(&p, NULL, 0x32, direction, NO);
  if (!template) return NULL;

  // Override xRatio/yRatio (template fills other touch fields correctly).
  template->payload.event.touch.xRatio = xRatio;
  template->payload.event.touch.yRatio = yRatio;

  size_t total = sizeof(IndigoMessage) + sizeof(IndigoPayload);  // 0x140
  IndigoMessage *msg = calloc(1, total);
  msg->innerSize = sizeof(IndigoPayload);
  msg->eventType = IndigoEventTypeTouch;
  msg->payload.field1 = 0x0000000b;
  msg->payload.timestamp = mach_absolute_time();

  // Copy IndigoTouch (the union member) into payload.event slot.
  memcpy(&(msg->payload.event.touch), &(template->payload.event.touch), sizeof(IndigoTouch));

  // Duplicate the IndigoPayload right after the first one.
  IndigoPayload *second = (IndigoPayload *)((char *)&(msg->payload) + sizeof(IndigoPayload));
  memcpy(second, &(msg->payload), sizeof(IndigoPayload));
  second->event.touch.field1 = 0x00000001;
  second->event.touch.field2 = 0x00000002;

  free(template);
  return msg;
}

// Send queue — serial so messages stay ordered, but each call is non-blocking.
static dispatch_queue_t sendQueue(void) {
  static dispatch_queue_t q;
  static dispatch_once_t once;
  dispatch_once(&once, ^{ q = dispatch_queue_create("io.synctek.simdrive.send", DISPATCH_QUEUE_SERIAL); });
  return q;
}

static dispatch_semaphore_t pendingSema(void) {
  static dispatch_semaphore_t s;
  static dispatch_once_t once;
  dispatch_once(&once, ^{ s = dispatch_semaphore_create(0); });
  return s;
}

static int gPendingCount = 0;

static void sendBlocking(SimDeviceLegacyHIDClient *client, IndigoMessage *msg) {
  // Despite the name, this is now a fire-and-forget send — we just track a
  // pending counter so the caller can drain at the end before the process exits.
  if (!msg) { fprintf(stderr, "null message\n"); return; }
  __sync_fetch_and_add(&gPendingCount, 1);
  [client sendWithMessage:msg freeWhenDone:YES completionQueue:sendQueue()
              completion:^(NSError *e) {
                if (e) fprintf(stderr, "send error: %s\n", [[e description] UTF8String]);
                if (__sync_sub_and_fetch(&gPendingCount, 1) == 0) {
                  dispatch_semaphore_signal(pendingSema());
                }
              }];
}

static void waitForAllSends(int timeout_ms) {
  // Spin briefly for any pending sends to drain.
  if (gPendingCount == 0) return;
  dispatch_semaphore_wait(pendingSema(), dispatch_time(DISPATCH_TIME_NOW, (int64_t)timeout_ms * NSEC_PER_MSEC));
}

static int doTap(SimDeviceLegacyHIDClient *client, SimDevice *device,
                 double xPoints, double yPoints, int direction) {
  CGSize size = [[device deviceType] mainScreenSize];
  float scale = [[device deviceType] mainScreenScale];
  double rx = (xPoints * scale) / size.width;
  double ry = (yPoints * scale) / size.height;
  if (rx < 0 || rx > 1 || ry < 0 || ry > 1) {
    fprintf(stderr, "out-of-range ratio: (%.3f, %.3f) for screen (%.0fx%.0f)\n", rx, ry, size.width, size.height);
    return 4;
  }
  IndigoMessage *msg = buildTouchMessage(rx, ry, direction);
  sendBlocking(client, msg);
  return 0;
}

static int parseButton(const char *name) {
  if (strcasecmp(name, "home") == 0) return 0x0;
  if (strcasecmp(name, "lock") == 0) return 0x1;
  if (strcasecmp(name, "side") == 0) return 0xbb8;
  if (strcasecmp(name, "siri") == 0) return 0x400002;
  return -1;
}

static void usage(void) {
  fprintf(stderr,
    "simdrive-input <udid> <command> [args]\n"
    "  tap    <xPoints> <yPoints>           — full down+up tap\n"
    "  down   <xPoints> <yPoints>           — touch begin\n"
    "  up     <xPoints> <yPoints>           — touch end\n"
    "  button <home|lock|side|siri>         — hardware button press\n"
    "  key    <usage>                       — keyboard key press (HID usage code)\n"
    "  text   <string>                      — type ASCII text\n"
    "  size                                 — print device screen size in points\n");
}

int main(int argc, const char *argv[]) {
  @autoreleasepool {
    if (argc < 3) { usage(); return 1; }
    NSError *err = nil;
    if (loadFrameworks(&err) != 0) return 5;

    NSString *udid = [NSString stringWithUTF8String:argv[1]];
    SimDevice *device = findDevice(udid, &err);
    if (!device) {
      fprintf(stderr, "no device for udid %s: %s\n", argv[1], err.description.UTF8String); return 6;
    }
    SimDeviceLegacyHIDClient *client = makeHIDClient(device, &err);
    if (!client) {
      fprintf(stderr, "client init failed: %s\n", err.description.UTF8String); return 7;
    }

    const char *cmd = argv[2];
    if (strcmp(cmd, "tap") == 0 && argc == 5) {
      double x = atof(argv[3]), y = atof(argv[4]);
      doTap(client, device, x, y, DIRECTION_DOWN);
      usleep(60 * 1000);
      doTap(client, device, x, y, DIRECTION_UP);
    } else if (strcmp(cmd, "down") == 0 && argc == 5) {
      doTap(client, device, atof(argv[3]), atof(argv[4]), DIRECTION_DOWN);
    } else if (strcmp(cmd, "up") == 0 && argc == 5) {
      doTap(client, device, atof(argv[3]), atof(argv[4]), DIRECTION_UP);
    } else if (strcmp(cmd, "button") == 0 && argc == 4) {
      int src = parseButton(argv[3]);
      if (src < 0) { fprintf(stderr, "unknown button %s\n", argv[3]); return 8; }
      sendBlocking(client, gMessageForButton(src, DIRECTION_DOWN, 0x33));
      usleep(50 * 1000);
      sendBlocking(client, gMessageForButton(src, DIRECTION_UP, 0x33));
    } else if (strcmp(cmd, "key") == 0 && argc == 4) {
      int code = atoi(argv[3]);
      sendBlocking(client, gMessageForKeyboard(code, DIRECTION_DOWN));
      usleep(20 * 1000);
      sendBlocking(client, gMessageForKeyboard(code, DIRECTION_UP));
    } else if (strcmp(cmd, "text") == 0 && argc == 4) {
      // Map ASCII → HID keyboard usage codes (US layout) + whether Shift is required.
      // Reference: HID Usage Tables, page 53.
      // (code, needsShift) per ASCII char.
      static const int code_for[128] = {
        ['a']=4,['b']=5,['c']=6,['d']=7,['e']=8,['f']=9,['g']=10,['h']=11,
        ['i']=12,['j']=13,['k']=14,['l']=15,['m']=16,['n']=17,['o']=18,['p']=19,
        ['q']=20,['r']=21,['s']=22,['t']=23,['u']=24,['v']=25,['w']=26,['x']=27,
        ['y']=28,['z']=29,
        ['A']=4,['B']=5,['C']=6,['D']=7,['E']=8,['F']=9,['G']=10,['H']=11,
        ['I']=12,['J']=13,['K']=14,['L']=15,['M']=16,['N']=17,['O']=18,['P']=19,
        ['Q']=20,['R']=21,['S']=22,['T']=23,['U']=24,['V']=25,['W']=26,['X']=27,
        ['Y']=28,['Z']=29,
        ['1']=30,['2']=31,['3']=32,['4']=33,['5']=34,['6']=35,['7']=36,['8']=37,
        ['9']=38,['0']=39,
        ['!']=30,['@']=31,['#']=32,['$']=33,['%']=34,['^']=35,['&']=36,['*']=37,
        ['(']=38,[')']=39,
        ['\n']=40,['\t']=43,[' ']=44,
        ['-']=45,['_']=45,
        ['=']=46,['+']=46,
        ['[']=47,['{']=47,
        [']']=48,['}']=48,
        ['\\']=49,['|']=49,
        [';']=51,[':']=51,
        ['\'']=52,['"']=52,
        ['`']=53,['~']=53,
        [',']=54,['<']=54,
        ['.']=55,['>']=55,
        ['/']=56,['?']=56,
      };
      // chars that require shift to produce
      static const int needs_shift[128] = {
        ['A']=1,['B']=1,['C']=1,['D']=1,['E']=1,['F']=1,['G']=1,['H']=1,
        ['I']=1,['J']=1,['K']=1,['L']=1,['M']=1,['N']=1,['O']=1,['P']=1,
        ['Q']=1,['R']=1,['S']=1,['T']=1,['U']=1,['V']=1,['W']=1,['X']=1,
        ['Y']=1,['Z']=1,
        ['!']=1,['@']=1,['#']=1,['$']=1,['%']=1,['^']=1,['&']=1,['*']=1,
        ['(']=1,[')']=1,
        ['_']=1,['+']=1,['{']=1,['}']=1,['|']=1,[':']=1,['"']=1,['~']=1,
        ['<']=1,['>']=1,['?']=1,
      };

      const int kShift = 0xE1;  // HID usage for left shift
      int shiftDown = 0;        // track shift state to avoid redundant down/up
      // iOS's keyboard subsystem needs ~20ms to register a modifier-state
      // change before the next keystroke lands; tighter than that drops chars.
      const useconds_t kModSettle = 25 * 1000;
      const useconds_t kKeyHold = 12 * 1000;
      const useconds_t kKeyGap = 12 * 1000;

      const char *s = argv[3];
      for (size_t i = 0; s[i]; i++) {
        unsigned char uc = (unsigned char)s[i];
        if (uc >= 128) continue;  // skip non-ASCII (callers should use the paste path)
        int code = code_for[uc];
        if (!code) continue;
        int wantsShift = needs_shift[uc];
        if (wantsShift && !shiftDown) {
          sendBlocking(client, gMessageForKeyboard(kShift, DIRECTION_DOWN));
          usleep(kModSettle);
          shiftDown = 1;
        } else if (!wantsShift && shiftDown) {
          sendBlocking(client, gMessageForKeyboard(kShift, DIRECTION_UP));
          usleep(kModSettle);
          shiftDown = 0;
        }
        sendBlocking(client, gMessageForKeyboard(code, DIRECTION_DOWN));
        usleep(kKeyHold);
        sendBlocking(client, gMessageForKeyboard(code, DIRECTION_UP));
        usleep(kKeyGap);
      }
      if (shiftDown) {
        sendBlocking(client, gMessageForKeyboard(kShift, DIRECTION_UP));
      }
    } else if (strcmp(cmd, "chord") == 0 && argc >= 4) {
      // chord <modifier> <key>  — e.g.  chord cmd v   →  Cmd+V (paste)
      // Modifier names → HID usage codes (Keyboard/Keypad page 0x07).
      const char *mod = argv[3];
      int modCode = 0;
      if (strcasecmp(mod, "cmd") == 0 || strcasecmp(mod, "command") == 0) modCode = 0xE3;
      else if (strcasecmp(mod, "shift") == 0) modCode = 0xE1;
      else if (strcasecmp(mod, "option") == 0 || strcasecmp(mod, "alt") == 0) modCode = 0xE2;
      else if (strcasecmp(mod, "control") == 0 || strcasecmp(mod, "ctrl") == 0) modCode = 0xE0;
      else { fprintf(stderr, "unknown modifier %s\n", mod); return 11; }

      int keyCode = 0;
      if (argc >= 5) {
        const char *key = argv[4];
        // Single character → US-ASCII map (lowercase only).
        if (strlen(key) == 1) {
          char c = key[0];
          static const int ascii[128] = {
            ['a']=4,['b']=5,['c']=6,['d']=7,['e']=8,['f']=9,['g']=10,['h']=11,
            ['i']=12,['j']=13,['k']=14,['l']=15,['m']=16,['n']=17,['o']=18,['p']=19,
            ['q']=20,['r']=21,['s']=22,['t']=23,['u']=24,['v']=25,['w']=26,['x']=27,
            ['y']=28,['z']=29,
          };
          if (c >= 'A' && c <= 'Z') c = c - 'A' + 'a';
          if (c >= 0 && c < 128) keyCode = ascii[(int)c];
        }
      }
      if (!keyCode) { fprintf(stderr, "chord needs a single ASCII letter as the key\n"); return 12; }

      sendBlocking(client, gMessageForKeyboard(modCode, DIRECTION_DOWN));
      usleep(15 * 1000);
      sendBlocking(client, gMessageForKeyboard(keyCode, DIRECTION_DOWN));
      usleep(15 * 1000);
      sendBlocking(client, gMessageForKeyboard(keyCode, DIRECTION_UP));
      usleep(10 * 1000);
      sendBlocking(client, gMessageForKeyboard(modCode, DIRECTION_UP));
    } else if (strcmp(cmd, "size") == 0) {
      CGSize sz = [[device deviceType] mainScreenSize];
      float sc = [[device deviceType] mainScreenScale];
      printf("%.0f %.0f %.2f\n", sz.width, sz.height, sc);
    } else {
      usage(); return 9;
    }
    // Drain pending sends so we don't exit before they're delivered.
    waitForAllSends(2000);
  }
  return 0;
}
