#import "FRAPI.h"
#import "FRDemo.h"
#import <Security/Security.h>
NSString * const FRAccountChanged = @"FRAccountChanged";
NSString * const FRDataChanged = @"FRDataChanged";
NSString *FRString(id value) {
    if ([value isKindOfClass:NSString.class]) return value;
    if (!value || value == NSNull.null) return @"";
    if ([value isKindOfClass:NSDictionary.class]) { if (value[@"text"]) return FRString(value[@"text"]); if (value[@"message"]) return FRString(value[@"message"]); }
    if ([NSJSONSerialization isValidJSONObject:value]) return [[NSString alloc] initWithData:[NSJSONSerialization dataWithJSONObject:value options:NSJSONWritingPrettyPrinted error:nil] encoding:NSUTF8StringEncoding] ?: @"";
    return [value description];
}
NSString *FRSegment(NSString *value) { return [value stringByAddingPercentEncodingWithAllowedCharacters:[NSCharacterSet characterSetWithCharactersInString:@"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_.~"]]; }
static NSError *FRError(NSInteger code, NSString *message) { return [NSError errorWithDomain:@"Friday" code:code userInfo:@{NSLocalizedDescriptionKey:message}]; }
@interface FRAPI ()
@property(nonatomic,readwrite) NSDictionary *credentials;
@property(nonatomic,readwrite) NSURL *origin;
@property(nonatomic,readwrite) NSUInteger generation;
@property(nonatomic,readwrite) NSURLSession *session;
@property(nonatomic) FRCache *privateCache;
@property(nonatomic) NSString *cacheScope;
@property(nonatomic) NSMutableDictionary *downloads;
@end
@implementation FRAPI
+ (instancetype)shared { static FRAPI *v; static dispatch_once_t once; dispatch_once(&once, ^{ v=[FRAPI new]; }); return v; }
- (NSDictionary *)keychainQuery { return @{(__bridge id)kSecClass:(__bridge id)kSecClassGenericPassword,(__bridge id)kSecAttrService:@"private.friday.credentials",(__bridge id)kSecAttrAccount:@"primary"}; }
- (instancetype)init { if ((self=[super init])) {
    NSMutableDictionary *q=[[self keychainQuery] mutableCopy]; q[(__bridge id)kSecReturnData]=@YES; q[(__bridge id)kSecMatchLimit]=(__bridge id)kSecMatchLimitOne;
    CFTypeRef result=NULL; if (SecItemCopyMatching((__bridge CFDictionaryRef)q,&result)==errSecSuccess) { NSData *data=CFBridgingRelease(result); _credentials=[NSJSONSerialization JSONObjectWithData:data options:0 error:nil]; }
    _origin=[NSURL URLWithString:_credentials[@"origin"] ?: @""];
    NSURLSessionConfiguration *config=NSURLSessionConfiguration.ephemeralSessionConfiguration; config.HTTPCookieStorage=nil; config.URLCache=nil; config.requestCachePolicy=NSURLRequestReloadIgnoringLocalCacheData;
    _session=[NSURLSession sessionWithConfiguration:config delegate:self delegateQueue:nil];
    if(FRDemoEnabled()) _credentials=@{@"origin":@"https://example.invalid",@"pairing":FRDemoPairing()};
    _downloads=[NSMutableDictionary dictionary];
} return self; }
- (BOOL)setOriginString:(NSString *)value error:(NSError **)error {
    NSURLComponents *c=[NSURLComponents componentsWithString:[value stringByTrimmingCharactersInSet:NSCharacterSet.whitespaceAndNewlineCharacterSet]];
    BOOL local=[c.scheme isEqual:@"http"] && [@[@"localhost",@"127.0.0.1",@"::1"] containsObject:c.host];
    if (!c.host.length || (!local && ![c.scheme isEqual:@"https"]) || c.user || c.password || c.query || c.fragment || (c.path.length && ![c.path isEqual:@"/"])) { if(error)*error=FRError(0,@"请输入不含路径的 HTTPS 服务地址。"); return NO; }
    c.path=@""; self.origin=c.URL; return YES;
}
- (BOOL)savePairing:(NSDictionary *)pairing error:(NSError **)error {
    if (![pairing[@"token"] isKindOfClass:NSString.class] || ![pairing[@"account"] isKindOfClass:NSDictionary.class] || ![pairing[@"device_id"] isKindOfClass:NSString.class]) { if(error)*error=FRError(0,@"登录响应不完整。"); return NO; }
    NSDictionary *credentials=@{@"origin":self.origin.absoluteString,@"pairing":pairing};
    NSData *data=[NSJSONSerialization dataWithJSONObject:credentials options:0 error:error]; if(!data)return NO;
    NSDictionary *attributes=@{(__bridge id)kSecValueData:data,(__bridge id)kSecAttrAccessible:(__bridge id)kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly};
    OSStatus status=SecItemUpdate((__bridge CFDictionaryRef)[self keychainQuery],(__bridge CFDictionaryRef)attributes);
    if(status==errSecItemNotFound) { NSMutableDictionary *q=[[self keychainQuery] mutableCopy]; [q addEntriesFromDictionary:attributes]; status=SecItemAdd((__bridge CFDictionaryRef)q,NULL); }
    if(status!=errSecSuccess) { if(error)*error=FRError(status,@"无法将登录凭据保存到钥匙串。");return NO; }
    [self.privateCache clear];self.privateCache=nil;self.cacheScope=nil;self.credentials=credentials; self.generation++; [[NSNotificationCenter defaultCenter] postNotificationName:FRAccountChanged object:nil]; return YES;
}
- (FRCache *)cache {
    NSString *account=self.credentials[@"pairing"][@"account"][@"id"],*device=self.credentials[@"pairing"][@"device_id"];
    if(!account.length||!device.length)return nil;
    NSString *scope=[NSString stringWithFormat:@"%@|%@|%@",self.origin.absoluteString,account,device];
    if(![scope isEqual:self.cacheScope]){self.cacheScope=scope;NSURL *root=[[NSFileManager.defaultManager URLsForDirectory:NSCachesDirectory inDomains:NSUserDomainMask].firstObject URLByAppendingPathComponent:@"FridayPrivate" isDirectory:YES];self.privateCache=[[FRCache alloc] initWithRoot:root scope:scope];}
    return self.privateCache;
}
- (BOOL)isArtifact:(NSString *)path {return [path containsString:@"/tasks/"]&&[[path componentsSeparatedByString:@"?"].firstObject hasSuffix:@"/artifact"];}
- (NSURL *)cachedMedia:(NSString *)path {return [self isArtifact:path]?[self.cache file:[@"media:" stringByAppendingString:path]]:nil;}
- (void)invalidateMedia:(NSString *)path {[self.cache remove:[@"media:" stringByAppendingString:path]];}
- (void)clearPreviews { [self.privateCache clear];self.privateCache=nil;self.cacheScope=nil; NSURL *root=[NSURL fileURLWithPath:[NSTemporaryDirectory() stringByAppendingPathComponent:@"FridayMedia"]]; [[NSFileManager defaultManager] removeItemAtURL:root error:nil]; }
- (void)signOut { self.generation++; self.credentials=nil; SecItemDelete((__bridge CFDictionaryRef)[self keychainQuery]); [self clearPreviews]; [[NSNotificationCenter defaultCenter] postNotificationName:FRAccountChanged object:nil]; }
- (NSString *)pathForSession:(NSString *)sid suffix:(NSString *)suffix { return [NSString stringWithFormat:@"/v1/sessions/%@%@",FRSegment(sid),suffix]; }
- (NSMutableURLRequest *)request:(NSString *)path method:(NSString *)method body:(id)body error:(NSError **)error {
    if (![path hasPrefix:@"/v1/"] || [path containsString:@"\\"] || [path containsString:@"#"]) { if(error)*error=FRError(0,@"资源地址无效。"); return nil; }
    NSURLComponents *url=[NSURLComponents componentsWithURL:self.origin resolvingAgainstBaseURL:NO]; NSArray *parts=[path componentsSeparatedByString:@"?"]; url.percentEncodedPath=parts[0]; url.percentEncodedQuery=parts.count>1?parts[1]:nil;
    NSMutableURLRequest *r=[NSMutableURLRequest requestWithURL:url.URL cachePolicy:NSURLRequestReloadIgnoringLocalCacheData timeoutInterval:[path hasSuffix:@"/voice"]?135:35]; r.HTTPMethod=method;
    NSString *token=self.credentials[@"pairing"][@"token"]; if(token)[r setValue:[@"Bearer " stringByAppendingString:token] forHTTPHeaderField:@"Authorization"];
    if(body) { r.HTTPBody=[NSJSONSerialization dataWithJSONObject:body options:0 error:error]; if(!r.HTTPBody)return nil; [r setValue:@"application/json" forHTTPHeaderField:@"Content-Type"]; } return r;
}
- (void)get:(NSString *)path completion:(FRReply)completion { [self call:path method:@"GET" body:nil completion:completion]; }
- (void)call:(NSString *)path method:(NSString *)method body:(id)body completion:(FRReply)completion { if(FRDemoEnabled()){NSTimeInterval delay=[NSProcessInfo.processInfo.arguments containsObject:@"--friday-slow"]?4:0;dispatch_after(dispatch_time(DISPATCH_TIME_NOW,(int64_t)(delay*NSEC_PER_SEC)),dispatch_get_main_queue(),^{if([NSProcessInfo.processInfo.arguments containsObject:@"--friday-offline"])completion(nil,FRError(NSURLErrorNotConnectedToInternet,@"演示连接中断"));else completion(FRDemoResponse(path,method,body),nil);});return;} NSError *e; NSURLRequest *r=[self request:path method:method body:body error:&e]; if(!r){completion(nil,e);return;} [self sendRequest:r raw:NO completion:completion]; }
- (void)sendRequest:(NSURLRequest *)request raw:(BOOL)raw completion:(FRReply)completion {
    NSUInteger generation=self.generation;
    [[self.session dataTaskWithRequest:request completionHandler:^(NSData *data, NSURLResponse *response, NSError *error) {
        NSHTTPURLResponse *http=(id)response; id json=data?[NSJSONSerialization JSONObjectWithData:data options:NSJSONReadingFragmentsAllowed error:nil]:nil;
        if(!error && (http.statusCode<200 || http.statusCode>=300)){id detail=[json isKindOfClass:NSDictionary.class]?json[@"error"]:nil;NSString *message=[detail isKindOfClass:NSDictionary.class]?FRString(detail[@"message"]):FRString(detail);error=FRError(http.statusCode,message.length?message:@"服务暂时无法连接。");}
        dispatch_async(dispatch_get_main_queue(), ^{ if(generation!=self.generation){completion(nil,FRError(NSURLErrorCancelled,@"登录状态已改变。"));return;} if(error.code==401&&self.credentials)[self signOut];completion(error?nil:(raw?data:(json?:@{})),error); });
    }] resume];
}
- (void)URLSession:(NSURLSession *)session task:(NSURLSessionTask *)task willPerformHTTPRedirection:(NSHTTPURLResponse *)response newRequest:(NSURLRequest *)request completionHandler:(void (^)(NSURLRequest *))completionHandler {
    // API calls never need redirects. In particular, never forward bearer tokens.
    completionHandler(nil);
}
- (void)download:(NSString *)path filename:(NSString *)filename completion:(FRReply)completion {
    NSError *e;NSMutableURLRequest *r=[self request:path method:@"GET" body:nil error:&e];if(!r){completion(nil,e);return;}
    NSUInteger generation=self.generation;FRCache *cache=self.cache;
    NSURL *cached=[self cachedMedia:path];if(cached){dispatch_async(dispatch_get_main_queue(),^{completion(generation==self.generation?cached:nil,generation==self.generation?nil:FRError(NSURLErrorCancelled,@"登录状态已改变。"));});return;}
    NSString *flight=[NSString stringWithFormat:@"%lu:%@",(unsigned long)generation,path];
    if(self.downloads[flight]){[self.downloads[flight] addObject:[completion copy]];return;}
    self.downloads[flight]=[NSMutableArray arrayWithObject:[completion copy]];
    // Media can exceed the text API's 35-second limit over a private relay.
    r.timeoutInterval=120;
    [[self.session downloadTaskWithRequest:r completionHandler:^(NSURL *temporary,NSURLResponse *response,NSError *networkError){
        NSInteger status=[(NSHTTPURLResponse *)response statusCode];NSError *error=networkError;
        if(!error&&(status<200||status>=300))error=FRError(status,[NSString stringWithFormat:@"附件下载失败（HTTP %ld）。",(long)status]);
        // The temporary download must be moved before this callback returns.
        dispatch_sync(dispatch_get_main_queue(),^{
            NSError *resultError=error;NSURL *file=nil;
            if(generation!=self.generation)resultError=FRError(NSURLErrorCancelled,@"登录状态已改变。");
            if(!resultError){
                NSString *key=[@"media:" stringByAppendingString:[self isArtifact:path]?path:NSUUID.UUID.UUIDString];
                file=[cache importFile:temporary name:filename key:key error:&resultError];
                if(!file&&!resultError)resultError=FRError(0,@"无法保存附件，请重新登录后重试。");
            }
            NSArray *listeners=[self.downloads[flight] copy];[self.downloads removeObjectForKey:flight];
            if(generation==self.generation&&status==401&&self.credentials)[self signOut];
            for(FRReply listener in listeners)listener(resultError?nil:file,resultError);
        });
    }] resume];
}
- (void)upload:(NSURL *)file session:(NSString *)sid completion:(FRReply)completion {
    BOOL access=[file startAccessingSecurityScopedResource]; NSNumber *size; [file getResourceValue:&size forKey:NSURLFileSizeKey error:nil];
    if(size.unsignedLongLongValue>20*1024*1024){if(access)[file stopAccessingSecurityScopedResource];completion(nil,FRError(413,@"附件不能超过 20 MB。"));return;}
    NSError *error; NSData *bytes=[NSData dataWithContentsOfURL:file options:NSDataReadingMappedIfSafe error:&error]; if(access)[file stopAccessingSecurityScopedResource]; if(!bytes){completion(nil,error);return;}
    NSString *boundary=NSUUID.UUID.UUIDString; NSString *name=file.lastPathComponent;
    for(NSString *unsafe in @[@"\"",@"\r",@"\n"])name=[name stringByReplacingOccurrencesOfString:unsafe withString:@"_"];
    NSMutableData *body=[[[NSString stringWithFormat:@"--%@\r\nContent-Disposition: form-data; name=\"file\"; filename=\"%@\"\r\nContent-Type: application/octet-stream\r\n\r\n",boundary,name] dataUsingEncoding:NSUTF8StringEncoding] mutableCopy]; [body appendData:bytes]; [body appendData:[[NSString stringWithFormat:@"\r\n--%@--\r\n",boundary] dataUsingEncoding:NSUTF8StringEncoding]];
    NSMutableURLRequest *r=[self request:[self pathForSession:sid suffix:@"/files"] method:@"POST" body:nil error:&error]; r.HTTPBody=body; [r setValue:[@"multipart/form-data; boundary=" stringByAppendingString:boundary] forHTTPHeaderField:@"Content-Type"]; [self sendRequest:r raw:NO completion:completion];
}
@end

@interface FRStream ()
@property(nonatomic,readwrite) BOOL connected;
@property(nonatomic,readwrite) NSString *sessionID;
@property(nonatomic,copy) FREventHandler handler;
@property(nonatomic) NSURLSession *urlSession;
@property(nonatomic) NSURLSessionDataTask *task;
@property(nonatomic) NSMutableData *buffer;
@property(nonatomic) BOOL running;
@property(nonatomic) NSUInteger generation;
@property(nonatomic) NSTimeInterval backoff;
@end
@implementation FRStream
- (instancetype)initWithSession:(NSString *)sid handler:(FREventHandler)handler { if((self=[super init])){_sessionID=[sid copy];_handler=[handler copy];_backoff=1;} return self; }
+ (NSArray<NSDictionary *> *)drainEvents:(NSMutableData *)buffer {
    NSMutableArray *events=[NSMutableArray array];NSData *separator=[@"\n\n" dataUsingEncoding:NSUTF8StringEncoding];
    while(YES){
        NSRange end=[buffer rangeOfData:separator options:0 range:NSMakeRange(0,buffer.length)];if(end.location==NSNotFound)break;
        NSString *block=[[NSString alloc] initWithData:[buffer subdataWithRange:NSMakeRange(0,end.location)] encoding:NSUTF8StringEncoding]?:@"";
        [buffer replaceBytesInRange:NSMakeRange(0,NSMaxRange(end)) withBytes:NULL length:0];
        NSMutableString *data=[NSMutableString string];
        for(NSString *line in [block componentsSeparatedByString:@"\n"])if([line hasPrefix:@"data:"])[data appendString:[[line substringFromIndex:5] stringByTrimmingCharactersInSet:NSCharacterSet.whitespaceCharacterSet]];
        if(!data.length)continue;
        id event=[NSJSONSerialization JSONObjectWithData:[data dataUsingEncoding:NSUTF8StringEncoding] options:0 error:nil];
        if([event isKindOfClass:NSDictionary.class])[events addObject:event];
    }
    return events;
}
- (void)start { if(self.running)return; self.running=YES; self.backoff=1; [self connect]; }
- (void)connect {
    if(!self.running)return;
    if(FRDemoEnabled()){__weak typeof(self) weak=self;FRDemoSubscribe(self.sessionID,^(NSDictionary *event){[weak deliver:event];});self.connected=YES;return;}
    NSError *error;NSMutableURLRequest *request=[FRAPI.shared request:[FRAPI.shared pathForSession:self.sessionID suffix:@"/stream"] method:@"GET" body:nil error:&error];
    if(!request){self.running=NO;return;}
    // The server sends a keep-alive every 15 seconds; this is the idle limit.
    request.timeoutInterval=45;[request setValue:@"text/event-stream" forHTTPHeaderField:@"Accept"];
    self.generation=FRAPI.shared.generation;
    if(!self.urlSession){NSURLSessionConfiguration *config=NSURLSessionConfiguration.ephemeralSessionConfiguration;config.HTTPCookieStorage=nil;config.URLCache=nil;config.timeoutIntervalForResource=24*3600;self.urlSession=[NSURLSession sessionWithConfiguration:config delegate:self delegateQueue:NSOperationQueue.mainQueue];}
    self.buffer=[NSMutableData data];self.task=[self.urlSession dataTaskWithRequest:request];[self.task resume];
}
- (void)deliver:(NSDictionary *)event { if(self.running&&self.handler)self.handler(event); }
- (void)stop {
    self.running=NO;self.connected=NO;[self.task cancel];self.task=nil;
    // The session retains its delegate until invalidated.
    [self.urlSession invalidateAndCancel];self.urlSession=nil;
    if(FRDemoEnabled())FRDemoUnsubscribe(self.sessionID);
}
- (void)URLSession:(NSURLSession *)session dataTask:(NSURLSessionDataTask *)task didReceiveResponse:(NSURLResponse *)response completionHandler:(void (^)(NSURLSessionResponseDisposition))completionHandler {
    NSInteger status=[(NSHTTPURLResponse *)response statusCode];
    if(status==200&&self.generation==FRAPI.shared.generation){self.connected=YES;self.backoff=1;completionHandler(NSURLSessionResponseAllow);return;}
    // An older Mac service has no stream: polling continues to show progress.
    if(status==404||status==401||status==403)self.running=NO;
    completionHandler(NSURLSessionResponseCancel);
}
- (void)URLSession:(NSURLSession *)session dataTask:(NSURLSessionDataTask *)task didReceiveData:(NSData *)data {
    if(task!=self.task)return;[self.buffer appendData:data];
    if(self.buffer.length>4*1024*1024){[task cancel];return;}
    for(NSDictionary *event in [FRStream drainEvents:self.buffer])[self deliver:event];
}
- (void)URLSession:(NSURLSession *)session task:(NSURLSessionTask *)task didCompleteWithError:(NSError *)error {
    if(task!=self.task)return;self.task=nil;BOOL was=self.connected;self.connected=NO;
    if(was)[self deliver:@{@"type":@"disconnected"}];
    if(!self.running||self.generation!=FRAPI.shared.generation)return;
    NSTimeInterval delay=self.backoff;self.backoff=MIN(15,self.backoff*2);__weak typeof(self) weak=self;
    dispatch_after(dispatch_time(DISPATCH_TIME_NOW,(int64_t)(delay*NSEC_PER_SEC)),dispatch_get_main_queue(),^{if(weak.running&&!weak.task)[weak connect];});
}
- (void)URLSession:(NSURLSession *)session task:(NSURLSessionTask *)task willPerformHTTPRedirection:(NSHTTPURLResponse *)response newRequest:(NSURLRequest *)request completionHandler:(void (^)(NSURLRequest *))completionHandler { completionHandler(nil); }
@end
