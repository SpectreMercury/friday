#import "FRAPI.h"
FOUNDATION_EXPORT NSString * const FRVoiceChanged;
@interface FRVoice : NSObject
@property(nonatomic,readonly) NSString *sessionID;
@property(nonatomic,readonly) NSString *sessionName;
@property(nonatomic,readonly) NSString *status;
@property(nonatomic,readonly) NSString *transcript;
@property(nonatomic,readonly) NSString *failure;
@property(nonatomic,readonly) BOOL recording;
+ (instancetype)shared;
- (void)start:(NSDictionary *)session;
- (void)stop;
- (void)switchTo:(NSDictionary *)session;
- (void)interrupt;
- (void)record:(NSString *)sid completion:(FRReply)completion;
- (void)finishRecording;
- (void)sendRecording:(FRReply)completion;
- (void)discardRecording;
@end
