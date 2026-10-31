#import <Foundation/Foundation.h>
BOOL FRQueueNotificationReply(NSString *account,NSString *session,NSString *text,NSString *requestID,NSError **error);
void FRRetryNotificationReplies(void);
void FRClearNotificationReplies(void);
