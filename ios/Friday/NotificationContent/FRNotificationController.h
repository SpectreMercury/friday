#import <UIKit/UIKit.h>
#import <UserNotifications/UserNotifications.h>
#import <UserNotificationsUI/UserNotificationsUI.h>
@interface FRNotificationController : UIViewController <UNNotificationContentExtension>
- (void)displayContent:(UNNotificationContent *)content;
@end
