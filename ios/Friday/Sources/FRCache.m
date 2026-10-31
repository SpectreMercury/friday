#import "FRCache.h"
#import <CommonCrypto/CommonDigest.h>
#import <TargetConditionals.h>
static NSString *FRCacheHash(NSString *value){NSData *data=[value dataUsingEncoding:NSUTF8StringEncoding];unsigned char hash[CC_SHA256_DIGEST_LENGTH];CC_SHA256(data.bytes,(CC_LONG)data.length,hash);NSMutableString *out=[NSMutableString string];for(int i=0;i<sizeof(hash);i++)[out appendFormat:@"%02x",hash[i]];return out;}
@interface FRCache ()
@property(nonatomic) NSURL *root;
@property(nonatomic) NSCache *memory;
@end
@implementation FRCache
- (instancetype)initWithRoot:(NSURL *)root scope:(NSString *)scope {if((self=[super init])){_root=[root URLByAppendingPathComponent:FRCacheHash(scope) isDirectory:YES];_memory=[NSCache new];_memory.countLimit=24;_memory.totalCostLimit=24*1024*1024;}return self;}
- (NSURL *)directory:(NSString *)key {return [self.root URLByAppendingPathComponent:FRCacheHash(key) isDirectory:YES];}
- (BOOL)prepare:(NSURL *)url error:(NSError **)error {
#if TARGET_OS_IPHONE
    NSDictionary *attributes=@{NSFileProtectionKey:NSFileProtectionComplete};
#else
    NSDictionary *attributes=nil;
#endif
    BOOL ok=[NSFileManager.defaultManager createDirectoryAtURL:url withIntermediateDirectories:YES attributes:attributes error:error];if(ok)[url setResourceValue:@YES forKey:NSURLIsExcludedFromBackupKey error:nil];return ok;
}
- (NSDictionary *)snapshot:(NSString *)key {
    NSDictionary *value=[self.memory objectForKey:key];if(value)return value;
    NSURL *path=[[self directory:key] URLByAppendingPathComponent:@"snapshot.json"];NSData *data=[NSData dataWithContentsOfURL:path];if(data.length>8*1024*1024)return nil;
    id json=data?[NSJSONSerialization JSONObjectWithData:data options:0 error:nil]:nil;if(![json isKindOfClass:NSDictionary.class])return nil;
    [self.memory setObject:json forKey:key cost:data.length];return json;
}
- (void)saveSnapshot:(NSDictionary *)value key:(NSString *)key {
    NSData *data=[NSJSONSerialization dataWithJSONObject:value options:0 error:nil];if(!data||data.length>8*1024*1024)return;
    NSURL *dir=[self directory:key];[self.memory setObject:[NSJSONSerialization JSONObjectWithData:data options:0 error:nil] forKey:key cost:data.length];if(![self prepare:dir error:nil])return;
#if TARGET_OS_IPHONE
    NSDataWritingOptions options=NSDataWritingAtomic|NSDataWritingFileProtectionComplete;
#else
    NSDataWritingOptions options=NSDataWritingAtomic;
#endif
    [data writeToURL:[dir URLByAppendingPathComponent:@"snapshot.json"] options:options error:nil];[self trim];
}
- (NSURL *)file:(NSString *)key {
    NSURL *dir=[self directory:key];for(NSURL *file in [NSFileManager.defaultManager contentsOfDirectoryAtURL:dir includingPropertiesForKeys:@[NSURLIsRegularFileKey,NSURLFileSizeKey] options:0 error:nil]){
        NSNumber *regular,*size;[file getResourceValue:&regular forKey:NSURLIsRegularFileKey error:nil];[file getResourceValue:&size forKey:NSURLFileSizeKey error:nil];
        if(regular.boolValue&&size.unsignedLongLongValue>0){[NSFileManager.defaultManager setAttributes:@{NSFileModificationDate:NSDate.date} ofItemAtPath:dir.path error:nil];return file;}
    }return nil;
}
- (NSURL *)importFile:(NSURL *)temporary name:(NSString *)name key:(NSString *)key error:(NSError **)error {
    NSURL *dir=[self directory:key];if(![self prepare:dir error:error])return nil;
    NSString *safe=name.lastPathComponent;if(!safe.length||[safe isEqual:@"."]||[safe isEqual:@".."])safe=@"attachment";
    NSURL *destination=[dir URLByAppendingPathComponent:safe];[NSFileManager.defaultManager removeItemAtURL:destination error:nil];
    if(![NSFileManager.defaultManager moveItemAtURL:temporary toURL:destination error:error])return nil;
#if TARGET_OS_IPHONE
    if(![NSFileManager.defaultManager setAttributes:@{NSFileProtectionKey:NSFileProtectionComplete} ofItemAtPath:destination.path error:error])return nil;
#endif
    [self trim];return destination;
}
- (void)remove:(NSString *)key {[self.memory removeObjectForKey:key];[NSFileManager.defaultManager removeItemAtURL:[self directory:key] error:nil];}
- (void)clear {[self.memory removeAllObjects];[NSFileManager.defaultManager removeItemAtURL:self.root error:nil];}
- (void)trim {
    // Bound disk usage; eviction of old cache entries never deletes server data.
    NSArray *dirs=[NSFileManager.defaultManager contentsOfDirectoryAtURL:self.root includingPropertiesForKeys:@[NSURLContentModificationDateKey] options:0 error:nil];NSMutableArray *entries=[NSMutableArray array];unsigned long long total=0;
    for(NSURL *dir in dirs){unsigned long long size=0;for(NSURL *file in [NSFileManager.defaultManager contentsOfDirectoryAtURL:dir includingPropertiesForKeys:@[NSURLFileSizeKey] options:0 error:nil]){NSNumber *bytes;[file getResourceValue:&bytes forKey:NSURLFileSizeKey error:nil];size+=bytes.unsignedLongLongValue;}NSDate *date;[dir getResourceValue:&date forKey:NSURLContentModificationDateKey error:nil];[entries addObject:@{@"url":dir,@"size":@(size),@"date":date?:NSDate.distantPast}];total+=size;}
    [entries sortUsingComparator:^NSComparisonResult(NSDictionary *a,NSDictionary *b){return [a[@"date"] compare:b[@"date"]];}];
    NSUInteger remaining=entries.count;for(NSDictionary *entry in entries){if(remaining<=1||(total<=256ULL*1024*1024&&remaining<=160))break;[NSFileManager.defaultManager removeItemAtURL:entry[@"url"] error:nil];total-=[entry[@"size"] unsignedLongLongValue];remaining--;}
}
@end
