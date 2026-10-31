#import <UIKit/UIKit.h>
FOUNDATION_EXPORT void FRShowError(UIViewController *controller,NSError *error);
FOUNDATION_EXPORT UIViewController *FRRootController(void);
// Applies the 外观 choice (system, light, dark) to the window and every open scene.
FOUNDATION_EXPORT void FRApplyAppearance(UIWindow *window);
FOUNDATION_EXPORT void FROpenSession(NSString *sessionID);
FOUNDATION_EXPORT void FRSyncPushToken(void);
FOUNDATION_EXPORT BOOL FRPushAvailable(void);

FOUNDATION_EXPORT void FRSetButtonBusy(UIButton *button,BOOL busy);
