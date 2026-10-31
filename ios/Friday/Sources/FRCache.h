#import <Foundation/Foundation.h>
// Private, account-scoped cache. It never stores tokens, approval grants or voice state.
@interface FRCache : NSObject
- (instancetype)initWithRoot:(NSURL *)root scope:(NSString *)scope;
- (NSDictionary *)snapshot:(NSString *)key;
- (void)saveSnapshot:(NSDictionary *)value key:(NSString *)key;
- (NSURL *)file:(NSString *)key;
- (NSURL *)importFile:(NSURL *)temporary name:(NSString *)name key:(NSString *)key error:(NSError **)error;
- (void)remove:(NSString *)key;
- (void)clear;
@end
