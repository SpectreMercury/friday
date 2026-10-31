#import <UIKit/UIKit.h>
#import <UserNotifications/UserNotifications.h>
#import <CommonCrypto/CommonDigest.h>
#import "FRViews.h"
#import "FRAPI.h"
#import "FRStore.h"
#import "FRVoice.h"
#import "FRNotifications.h"
#import "FRDemo.h"
#import "../NotificationContent/FRNotificationController.h"
#import <TargetConditionals.h>

@interface FRSceneDelegate : UIResponder <UIWindowSceneDelegate>
@property(nonatomic) UIWindow *window;
@end
@implementation FRSceneDelegate
- (void)scene:(UIScene *)scene willConnectToSession:(UISceneSession *)session options:(UISceneConnectionOptions *)options {
#if DEBUG && TARGET_OS_SIMULATOR
    if(FRDemoEnabled()&&[NSProcessInfo.processInfo.arguments containsObject:@"--friday-fresh-demo"])[FRAPI.shared.cache clear];
#endif
    self.window=[[UIWindow alloc] initWithWindowScene:(UIWindowScene *)scene];FRApplyAppearance(self.window);self.window.rootViewController=FRRootController();[self.window makeKeyAndVisible];
#if DEBUG && TARGET_OS_SIMULATOR
    if(FRDemoEnabled() && [NSProcessInfo.processInfo.arguments containsObject:@"--friday-chat"]) dispatch_async(dispatch_get_main_queue(),^{FROpenSession(@"demo-work");});
    if(FRDemoEnabled() && [NSProcessInfo.processInfo.arguments containsObject:@"--friday-card"]) {
        UIViewController *preview=[UIViewController new];preview.view.backgroundColor=UIColor.systemGroupedBackgroundColor;
        FRNotificationController *card=[FRNotificationController new];[preview addChildViewController:card];card.view.translatesAutoresizingMaskIntoConstraints=NO;card.view.layer.cornerRadius=20;card.view.clipsToBounds=YES;[preview.view addSubview:card.view];[card didMoveToParentViewController:preview];
        [NSLayoutConstraint activateConstraints:@[[card.view.leadingAnchor constraintEqualToAnchor:preview.view.safeAreaLayoutGuide.leadingAnchor constant:16],[card.view.trailingAnchor constraintEqualToAnchor:preview.view.safeAreaLayoutGuide.trailingAnchor constant:-16],[card.view.centerYAnchor constraintEqualToAnchor:preview.view.centerYAnchor],[card.view.heightAnchor constraintEqualToConstant:164]]];
        UNMutableNotificationContent *content=[UNMutableNotificationContent new];content.categoryIdentifier=@"FRIDAY_APPROVAL";
        [card displayContent:content];self.window.rootViewController=preview;
    }
#endif
#if DEBUG
    if([NSProcessInfo.processInfo.arguments containsObject:@"--friday-media-diagnostic"]){
        fprintf(stderr,"FRIDAY_MEDIA_DIAGNOSTIC start authenticated=%d\n",FRAPI.shared.credentials!=nil);fflush(stderr);
        if(FRAPI.shared.credentials)[FRAPI.shared get:@"/v1/tasks" completion:^(NSDictionary *v,NSError *e){
            fprintf(stderr,"FRIDAY_MEDIA_DIAGNOSTIC tasks count=%lu error=%s code=%ld\n",(unsigned long)[v[@"items"] count],e.domain.UTF8String? :"none",(long)e.code);fflush(stderr);
            NSUInteger count=0;
            for(NSDictionary *job in v[@"items"]){if(![job[@"artifact_media_type"] hasPrefix:@"image/"])continue;
                NSString *path=job[@"artifact_thumbnail_url"]?:job[@"artifact_url"];[FRAPI.shared invalidateMedia:path];NSDate *started=NSDate.date;
                [FRAPI.shared download:path filename:job[@"artifact_filename"] completion:^(NSURL *file,NSError *error){
                    NSError *under=error.userInfo[NSUnderlyingErrorKey];NSNumber *size;[file getResourceValue:&size forKey:NSURLFileSizeKey error:nil];UIImage *decoded=file?[UIImage imageWithContentsOfFile:file.path]:nil;
                    NSString *report=[NSString stringWithFormat:@"FRIDAY_MEDIA_DIAGNOSTIC ok=%d bytes=%@ decoded=%d seconds=%.2f error=%@ code=%ld underlying=%@/%ld detail=%@",file!=nil,size,decoded!=nil,-started.timeIntervalSinceNow,error.domain,(long)error.code,under.domain,(long)under.code,error.localizedDescription];
                    fprintf(stderr,"%s\n",report.UTF8String);fflush(stderr);
                }];if(++count==3)break;
            }
        }];
    }
#endif
    [[NSNotificationCenter defaultCenter] addObserver:self selector:@selector(accountChanged) name:FRAccountChanged object:nil];
    if(options.notificationResponse){NSString *sid=options.notificationResponse.notification.request.content.userInfo[@"session_id"];NSString *account=options.notificationResponse.notification.request.content.userInfo[@"account_id"];if([account isEqual:FRAPI.shared.credentials[@"pairing"][@"account"][@"id"]])FROpenSession(sid);}
}
- (void)accountChanged {self.window.rootViewController=FRRootController();if(!FRAPI.shared.credentials)FRClearNotificationReplies();FRSyncPushToken();FRRetryNotificationReplies();if(FRAPI.shared.credentials&&FRPushAvailable())[[UNUserNotificationCenter currentNotificationCenter] requestAuthorizationWithOptions:UNAuthorizationOptionAlert|UNAuthorizationOptionSound|UNAuthorizationOptionBadge completionHandler:^(BOOL granted,NSError *error){if(granted)dispatch_async(dispatch_get_main_queue(),^{[UIApplication.sharedApplication registerForRemoteNotifications];});}];}
- (void)sceneDidBecomeActive:(UIScene *)scene {if(FRAPI.shared.credentials){[FRStore.shared start];FRSyncPushToken();FRRetryNotificationReplies();}if(!FRPushAvailable())return;[[UNUserNotificationCenter currentNotificationCenter] getNotificationSettingsWithCompletionHandler:^(UNNotificationSettings *settings){if(settings.authorizationStatus==UNAuthorizationStatusAuthorized||settings.authorizationStatus==UNAuthorizationStatusProvisional)dispatch_async(dispatch_get_main_queue(),^{[UIApplication.sharedApplication registerForRemoteNotifications];});}];}
- (void)sceneWillResignActive:(UIScene *)scene {[FRStore.shared stop];}
- (void)dealloc {[[NSNotificationCenter defaultCenter] removeObserver:self];}
@end

@interface FRAppDelegate : UIResponder <UIApplicationDelegate,UNUserNotificationCenterDelegate> @end
@implementation FRAppDelegate
- (BOOL)application:(UIApplication *)application didFinishLaunchingWithOptions:(NSDictionary *)options {
    UNUserNotificationCenter *center=UNUserNotificationCenter.currentNotificationCenter;center.delegate=self;
    UNNotificationAction *open=[UNNotificationAction actionWithIdentifier:@"FRIDAY_OPEN" title:@"查看会话" options:UNNotificationActionOptionForeground|UNNotificationActionOptionAuthenticationRequired];
    UNTextInputNotificationAction *reply=[UNTextInputNotificationAction actionWithIdentifier:@"FRIDAY_REPLY" title:@"补充要求" options:UNNotificationActionOptionAuthenticationRequired textInputButtonTitle:@"发送" textInputPlaceholder:@"告诉 Friday 下一步"];
    UNNotificationAction *review=[UNNotificationAction actionWithIdentifier:@"FRIDAY_OPEN" title:@"解锁并查看" options:UNNotificationActionOptionForeground|UNNotificationActionOptionAuthenticationRequired];
    NSMutableSet *categories=[NSMutableSet set];for(NSString *name in @[@"FRIDAY_RESULT",@"FRIDAY_INPUT",@"FRIDAY_STOPPED",@"FRIDAY_APPROVAL"]){NSArray *actions=[name isEqual:@"FRIDAY_APPROVAL"]?@[review]:@[open,reply];[categories addObject:[UNNotificationCategory categoryWithIdentifier:name actions:actions intentIdentifiers:@[] hiddenPreviewsBodyPlaceholder:@"Friday 有一条任务更新" options:UNNotificationCategoryOptionCustomDismissAction]];}[center setNotificationCategories:categories];
    return YES;
}
- (UISceneConfiguration *)application:(UIApplication *)application configurationForConnectingSceneSession:(UISceneSession *)session options:(UISceneConnectionOptions *)options {UISceneConfiguration *c=[[UISceneConfiguration alloc] initWithName:@"Friday" sessionRole:session.role];c.delegateClass=FRSceneDelegate.class;return c;}
- (void)application:(UIApplication *)application didRegisterForRemoteNotificationsWithDeviceToken:(NSData *)deviceToken {NSMutableString *token=[NSMutableString string];const unsigned char *bytes=deviceToken.bytes;for(NSUInteger i=0;i<deviceToken.length;i++)[token appendFormat:@"%02x",bytes[i]];[NSUserDefaults.standardUserDefaults setObject:token forKey:@"FridayPushToken"];FRSyncPushToken();}
- (void)application:(UIApplication *)application didFailToRegisterForRemoteNotificationsWithError:(NSError *)error {[[NSNotificationCenter defaultCenter] postNotificationName:@"FRPushFailed" object:error];}
- (void)userNotificationCenter:(UNUserNotificationCenter *)center willPresentNotification:(UNNotification *)notification withCompletionHandler:(void (^)(UNNotificationPresentationOptions))completionHandler {
    NSDictionary *info=notification.request.content.userInfo;
    if(![info[@"account_id"] isEqual:FRAPI.shared.credentials[@"pairing"][@"account"][@"id"]]){completionHandler(UNNotificationPresentationOptionNone);return;}
    [FRStore.shared refresh];completionHandler(UNNotificationPresentationOptionBanner|UNNotificationPresentationOptionList|UNNotificationPresentationOptionSound);
}
- (void)userNotificationCenter:(UNUserNotificationCenter *)center didReceiveNotificationResponse:(UNNotificationResponse *)response withCompletionHandler:(void (^)(void))completionHandler {
    NSDictionary *info=response.notification.request.content.userInfo;NSString *sid=info[@"session_id"];
    if(![info[@"account_id"] isEqual:FRAPI.shared.credentials[@"pairing"][@"account"][@"id"]]||!sid.length){completionHandler();return;}
    if([response.actionIdentifier isEqual:@"FRIDAY_REPLY"]&&[response isKindOfClass:UNTextInputNotificationResponse.class]){
        NSString *text=[[(UNTextInputNotificationResponse *)response userText] stringByTrimmingCharactersInSet:NSCharacterSet.whitespaceAndNewlineCharacterSet];if(!text.length){completionHandler();return;}
        NSString *identity=[NSString stringWithFormat:@"%@:%@:%@:%@",sid,info[@"event_id"],FRAPI.shared.credentials[@"pairing"][@"device_id"],text];NSData *bytes=[identity dataUsingEncoding:NSUTF8StringEncoding];unsigned char hash[32];CC_SHA256(bytes.bytes,(CC_LONG)bytes.length,hash);NSMutableString *rid=[NSMutableString stringWithString:@"push-"];for(int i=0;i<32;i++)[rid appendFormat:@"%02x",hash[i]];
        NSError *saveError;
        if(FRQueueNotificationReply(info[@"account_id"],sid,text,rid,&saveError))FRRetryNotificationReplies();
        else {
            UNMutableNotificationContent *notice=[UNMutableNotificationContent new];notice.title=@"这条回复尚未发送";notice.body=@"本机未能保存，请打开原会话重新输入。";notice.sound=UNNotificationSound.defaultSound;notice.userInfo=@{@"account_id":info[@"account_id"],@"session_id":sid};
            [center addNotificationRequest:[UNNotificationRequest requestWithIdentifier:[@"reply-save-failed-" stringByAppendingString:rid] content:notice trigger:nil] withCompletionHandler:nil];
        }
        completionHandler();return;
    }
    if(![response.actionIdentifier isEqual:UNNotificationDismissActionIdentifier]){[FRStore.shared refresh];FROpenSession(sid);}completionHandler();
}
@end
int main(int argc,char *argv[]){@autoreleasepool{return UIApplicationMain(argc,argv,nil,NSStringFromClass(FRAppDelegate.class));}}
