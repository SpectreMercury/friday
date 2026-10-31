#import <Foundation/Foundation.h>
#import "FRCache.h"
NS_ASSUME_NONNULL_BEGIN
FOUNDATION_EXPORT NSString * const FRAccountChanged;
FOUNDATION_EXPORT NSString * const FRDataChanged;
FOUNDATION_EXPORT NSString *FRString(id _Nullable value);
FOUNDATION_EXPORT NSString *FRSegment(NSString *value);
typedef void (^FRReply)(id _Nullable value, NSError * _Nullable error);
@interface FRAPI : NSObject <NSURLSessionTaskDelegate>
@property(nonatomic,readonly,nullable) NSDictionary *credentials;
@property(nonatomic,readonly,nullable) NSURL *origin;
@property(nonatomic,readonly) NSUInteger generation;
@property(nonatomic,readonly) NSURLSession *session;
@property(nonatomic,readonly,nullable) FRCache *cache;
- (nullable NSURL *)cachedMedia:(NSString *)path;
- (void)invalidateMedia:(NSString *)path;
+ (instancetype)shared;
- (BOOL)setOriginString:(NSString *)value error:(NSError **)error;
- (BOOL)savePairing:(NSDictionary *)pairing error:(NSError **)error;
- (void)signOut;
- (nullable NSMutableURLRequest *)request:(NSString *)path method:(NSString *)method body:(nullable id)body error:(NSError **)error;
- (void)get:(NSString *)path completion:(FRReply)completion;
- (void)call:(NSString *)path method:(NSString *)method body:(nullable id)body completion:(FRReply)completion;
- (void)sendRequest:(NSURLRequest *)request raw:(BOOL)raw completion:(FRReply)completion;
- (void)download:(NSString *)path filename:(NSString *)filename completion:(FRReply)completion;
- (void)upload:(NSURL *)file session:(NSString *)sid completion:(FRReply)completion;
- (NSString *)pathForSession:(NSString *)sid suffix:(NSString *)suffix;
@end

// Server-sent events for one session's running replies. Reconnects with backoff
// while started; the handler runs on the main queue. A synthetic
// {"type":"disconnected"} event marks a dropped connection.
typedef void (^FREventHandler)(NSDictionary *event);
@interface FRStream : NSObject <NSURLSessionDataDelegate>
@property(nonatomic,readonly) BOOL connected;
@property(nonatomic,readonly) NSString *sessionID;
- (instancetype)initWithSession:(NSString *)sid handler:(FREventHandler)handler;
- (void)start;
- (void)stop;
// Parses complete "data:" events out of buffer and removes them; exposed for tests.
+ (NSArray<NSDictionary *> *)drainEvents:(NSMutableData *)buffer;
@end
NS_ASSUME_NONNULL_END
