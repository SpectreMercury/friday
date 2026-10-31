#import "FRNotifications.h"
#import "FRAPI.h"
#import <UIKit/UIKit.h>
#import <UserNotifications/UserNotifications.h>
static NSURL *FROutboxURL(void){NSURL *root=[NSFileManager.defaultManager URLsForDirectory:NSApplicationSupportDirectory inDomains:NSUserDomainMask].firstObject;root=[root URLByAppendingPathComponent:@"Friday" isDirectory:YES];[NSFileManager.defaultManager createDirectoryAtURL:root withIntermediateDirectories:YES attributes:@{NSFileProtectionKey:NSFileProtectionComplete} error:nil];[root setResourceValue:@YES forKey:NSURLIsExcludedFromBackupKey error:nil];return [root URLByAppendingPathComponent:@"NotificationReplies.json"];}
static NSMutableDictionary *FRReadOutbox(void){NSData *data=[NSData dataWithContentsOfURL:FROutboxURL()];id value=data?[NSJSONSerialization JSONObjectWithData:data options:NSJSONReadingMutableContainers error:nil]:nil;return [value isKindOfClass:NSMutableDictionary.class]?value:[NSMutableDictionary dictionary];}
static BOOL FRWriteOutbox(NSDictionary *value,NSError **error){NSData *data=[NSJSONSerialization dataWithJSONObject:value options:0 error:error];return data&&[data writeToURL:FROutboxURL() options:NSDataWritingAtomic|NSDataWritingFileProtectionComplete error:error];}
BOOL FRQueueNotificationReply(NSString *account,NSString *session,NSString *text,NSString *requestID,NSError **error){NSMutableDictionary *outbox=FRReadOutbox();outbox[requestID]=@{@"account_id":account,@"session_id":session,@"text":text,@"request_id":requestID};return FRWriteOutbox(outbox,error);}
void FRClearNotificationReplies(void){[NSFileManager.defaultManager removeItemAtURL:FROutboxURL() error:nil];}
void FRRetryNotificationReplies(void){
    static NSMutableSet *inFlight;if(!inFlight)inFlight=[NSMutableSet set];
    NSString *account=FRAPI.shared.credentials[@"pairing"][@"account"][@"id"];if(!account)return;
    NSDictionary *outbox=FRReadOutbox();
    for(NSString *rid in outbox){NSDictionary *item=outbox[rid];if(![item[@"account_id"] isEqual:account]||[inFlight containsObject:rid])continue;[inFlight addObject:rid];
        __block UIBackgroundTaskIdentifier task=UIBackgroundTaskInvalid;
        task=[UIApplication.sharedApplication beginBackgroundTaskWithName:@"Friday reply" expirationHandler:^{if(task!=UIBackgroundTaskInvalid){[UIApplication.sharedApplication endBackgroundTask:task];task=UIBackgroundTaskInvalid;}}];
        [FRAPI.shared call:[FRAPI.shared pathForSession:item[@"session_id"] suffix:@"/messages"] method:@"POST" body:@{@"text":item[@"text"],@"request_id":rid} completion:^(id value,NSError *error){
            [inFlight removeObject:rid];NSString *notice=[@"reply-pending-" stringByAppendingString:rid];
            if(!error){NSMutableDictionary *current=FRReadOutbox();[current removeObjectForKey:rid];FRWriteOutbox(current,nil);[UNUserNotificationCenter.currentNotificationCenter removeDeliveredNotificationsWithIdentifiers:@[notice]];[[NSNotificationCenter defaultCenter] postNotificationName:FRDataChanged object:nil];}
            else if(error.code!=NSURLErrorCancelled){UNMutableNotificationContent *content=[UNMutableNotificationContent new];content.title=@"补充内容已保留";content.body=@"暂时无法连接，打开 Friday 后会重试发送。";content.categoryIdentifier=@"FRIDAY_INPUT";content.userInfo=@{@"account_id":account,@"session_id":item[@"session_id"],@"kind":@"notification_reply_pending",@"event_id":rid};content.threadIdentifier=[@"friday." stringByAppendingString:item[@"session_id"]];[UNUserNotificationCenter.currentNotificationCenter addNotificationRequest:[UNNotificationRequest requestWithIdentifier:notice content:content trigger:nil] withCompletionHandler:nil];}
            if(task!=UIBackgroundTaskInvalid){[UIApplication.sharedApplication endBackgroundTask:task];task=UIBackgroundTaskInvalid;}
        }];
    }
}
