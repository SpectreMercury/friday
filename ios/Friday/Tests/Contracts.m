#import <Foundation/Foundation.h>
#import "FRAPI.h"
#import "FRStore.h"
#import <Security/Security.h>
static int checks=0;
static void require(BOOL value,NSString *message){checks++;if(!value){fprintf(stderr,"FAIL: %s\n",message.UTF8String);exit(1);}}
@interface FRContractAPI : FRAPI @end
@implementation FRContractAPI
- (NSDictionary *)keychainQuery {return @{(__bridge id)kSecClass:(__bridge id)kSecClassGenericPassword,(__bridge id)kSecAttrService:@"FridayContractTestNeverStoresCredentials",(__bridge id)kSecAttrAccount:@"none"};}
@end
@interface FRMockAPI : FRContractAPI
@property(nonatomic) NSMutableArray *requests;
@property(nonatomic,copy) FRReply pending;
@end
@implementation FRMockAPI
- (instancetype)init {if((self=[super init]))_requests=[NSMutableArray array];return self;}
- (NSDictionary *)credentials {return @{@"pairing":@{@"account":@{@"id":@"test"}}};}
- (void)call:(NSString *)path method:(NSString *)method body:(id)body completion:(FRReply)completion {[self.requests addObject:@{@"path":path,@"body":body?:@{}}];self.pending=completion;}
@end
int main(void){@autoreleasepool{
    FRAPI *api=[FRContractAPI new];NSError *error=nil;
    for(NSString *bad in @[@"http://remote.example",@"https://user:pass@example.com",@"https://example.com/private",@"https://example.com?token=x",@"https://example.com#fragment",@"javascript:alert(1)"]){require(![api setOriginString:bad error:&error],@"unsafe origin accepted");}
    require([api setOriginString:@"https://private.example:8443" error:&error],@"valid HTTPS origin rejected");
    NSString *sid=@"session/with spaces";NSString *path=[api pathForSession:sid suffix:@"/messages"];
    require([path isEqual:@"/v1/sessions/session%2Fwith%20spaces/messages"],@"session path escaped ownership boundary");
    NSURLRequest *request=[api request:path method:@"POST" body:@{@"text":@"保留原会话",@"request_id":@"retry-key"} error:&error];
    require([request.URL.host isEqual:@"private.example"]&&request.URL.port.integerValue==8443,@"origin or port changed");
    NSDictionary *body=[NSJSONSerialization JSONObjectWithData:request.HTTPBody options:0 error:&error];require([body[@"request_id"] isEqual:@"retry-key"],@"request ID lost");
    require(![api request:@"https://elsewhere.example/v1/data" method:@"GET" body:nil error:&error],@"external API accepted");
    require(![api request:@"/private" method:@"GET" body:nil error:&error],@"non-API path accepted");
    require([api request:@"/v1/sessions/a/voice" method:@"POST" body:@{} error:&error].timeoutInterval==135,@"cold voice timeout changed");
    require([FRSegment(@"照片 #1?.png") isEqual:@"%E7%85%A7%E7%89%87%20%231%3F.png"],@"unsafe filename encoding");
    require([FRString(NSNull.null) isEqual:@""],@"null response shown to user");
    require([FRString(@{@"text":@"你好"}) isEqual:@"你好"],@"result display failed");
    FRMockAPI *mock=[FRMockAPI new];FRStore *store=[[FRStore alloc] initWithAPI:mock];
    store.drafts[@"B"]=[@{@"text":@"B 的草稿"} mutableCopy];
    [store sendText:@"A 的要求" session:@"A" completion:^(id v,NSError *e){}];
    NSString *firstID=mock.requests.lastObject[@"body"][@"request_id"];
    [store sendText:@"重复点击" session:@"A" completion:^(id v,NSError *e){}];
    require(mock.requests.count==1,@"double tap sent a second request");
    mock.pending(nil,[NSError errorWithDomain:NSURLErrorDomain code:NSURLErrorTimedOut userInfo:nil]);
    require([store.drafts[@"A"][@"uncertain"] boolValue],@"uncertain send lost retry identity");
    [store sendText:@"重试不能改变原请求" session:@"A" completion:^(id v,NSError *e){}];
    require([mock.requests.lastObject[@"body"][@"request_id"] isEqual:firstID],@"retry changed request ID");
    require([mock.requests.lastObject[@"body"][@"text"] isEqual:@"A 的要求"],@"retry changed submitted text");
    require([mock.requests.lastObject[@"path"] isEqual:@"/v1/sessions/A/messages"],@"retry switched sessions");
    mock.pending(@{@"id":@"job"},nil);
    require(!store.drafts[@"A"]&&[store.drafts[@"B"][@"text"] isEqual:@"B 的草稿"],@"completion cleared another session draft");
    [store sendText:@"需要修改" session:@"A" completion:^(id v,NSError *e){}];
    mock.pending(nil,[NSError errorWithDomain:@"Friday" code:400 userInfo:nil]);
    require(!store.drafts[@"A"][@"request_id"]&&!store.drafts[@"A"][@"uncertain"],@"validation failure locked the draft");
    [store sendText:@"已被列表确认" session:@"A" completion:^(id v,NSError *e){}];
    [store.drafts removeObjectForKey:@"A"];
    store.drafts[@"A"]=[@{@"text":@"下一条草稿"} mutableCopy];
    mock.pending(@{@"id":@"confirmed-job"},nil);
    require([store.drafts[@"A"][@"text"] isEqual:@"下一条草稿"],@"late acknowledgement erased a newer draft");
    // Per-message options travel with the request and survive an uncertain retry.
    FRMockAPI *optionAPI=[FRMockAPI new];FRStore *optionStore=[[FRStore alloc] initWithAPI:optionAPI];
    [optionStore sendText:@"把剧本做成短剧" session:@"C" options:@{@"thinking":@NO,@"agents":@[@"drama"]} completion:^(id v,NSError *e){}];
    require([optionAPI.requests.lastObject[@"body"][@"agents"] isEqual:@[@"drama"]]&&[optionAPI.requests.lastObject[@"body"][@"thinking"] isEqual:@NO],@"send options missing");
    optionAPI.pending(nil,[NSError errorWithDomain:NSURLErrorDomain code:NSURLErrorTimedOut userInfo:nil]);
    [optionStore sendText:@"把剧本做成短剧" session:@"C" options:@{@"thinking":@YES} completion:^(id v,NSError *e){}];
    require([optionAPI.requests.lastObject[@"body"][@"agents"] isEqual:@[@"drama"]]&&[optionAPI.requests.lastObject[@"body"][@"thinking"] isEqual:@NO],@"retry changed send options");
    [store sendText:@"无选项" session:@"D" completion:^(id v,NSError *e){}];
    require(!mock.requests.lastObject[@"body"][@"agents"]&&!mock.requests.lastObject[@"body"][@"thinking"],@"plain send gained options");
    // Server-sent events: complete blocks only, split across network reads.
    NSMutableData *sse=[[@"data: {\"type\":\"text\",\"delta\":\"你\"}\n\n: keep-alive\n\ndata: {\"type\":\"te" dataUsingEncoding:NSUTF8StringEncoding] mutableCopy];
    NSArray *events=[FRStream drainEvents:sse];
    require(events.count==1&&[events[0][@"delta"] isEqual:@"你"],@"first SSE event not parsed");
    [sse appendData:[@"xt\",\"delta\":\"好\"}\n\n" dataUsingEncoding:NSUTF8StringEncoding]];events=[FRStream drainEvents:sse];
    require(events.count==1&&[events[0][@"delta"] isEqual:@"好"]&&sse.length==0,@"split SSE event not reassembled");
    // Loading is independent for each endpoint; an error is not an empty result.
    FRMockAPI *loadingAPI=[FRMockAPI new];FRStore *loadingStore=[[FRStore alloc] initWithAPI:loadingAPI];
    [loadingStore start];require(loadingStore.loading&&!loadingStore.sessionsLoaded&&!loadingStore.tasksLoaded,@"initial load must show placeholders");
    loadingAPI.pending(nil,[NSError errorWithDomain:NSURLErrorDomain code:NSURLErrorNotConnectedToInternet userInfo:nil]);
    require(!loadingStore.loading&&!loadingStore.sessionsLoaded&&loadingStore.sessionsError!=nil,@"failed load must leave skeleton and expose retry");
    [loadingStore refresh];loadingAPI.pending(@{@"items":@[@{@"id":@"A"}]},nil);
    require(loadingStore.sessionsLoaded&&loadingStore.loading&&!loadingStore.tasksLoaded,@"sessions should render before tasks finish");
    loadingAPI.pending(nil,[NSError errorWithDomain:NSURLErrorDomain code:NSURLErrorTimedOut userInfo:nil]);
    require(loadingStore.sessions.count==1&&loadingStore.tasksError!=nil&&!loadingStore.tasksLoaded,@"task error must not discard sessions");
    [loadingStore refresh];require(loadingStore.sessionsLoaded&&loadingStore.sessions.count==1,@"background refresh must retain loaded content");
    loadingAPI.pending(@{@"items":@[]},nil);loadingAPI.pending(@{@"items":@[]},nil);
    require(loadingStore.sessionsLoaded&&loadingStore.tasksLoaded&&!loadingStore.loading&&!loadingStore.tasksError,@"successful empty state must not keep skeleton");
    [loadingStore stop];
    for(NSString *state in @[@"queued",@"running",@"waiting_approval"]){NSDictionary *task=@{@"status":state};require(FRTaskMatchesFilter(task,0)&&FRTaskMatchesFilter(task,1)&&!FRTaskMatchesFilter(task,2),@"active task lost from ongoing filter");}
    for(NSString *state in @[@"succeeded",@"failed",@"cancelled"]){NSDictionary *task=@{@"status":state};require(FRTaskMatchesFilter(task,0)&&!FRTaskMatchesFilter(task,1)&&FRTaskMatchesFilter(task,2),@"ended task misclassified");}
    require([FRTaskStatus(@{@"status":@"succeeded",@"outcome":@"blocked"}) isEqual:@"未完成"],@"blocked result must not display completed");
    require([FRTaskStatus(@{@"status":@"succeeded",@"outcome":@"needs_input"}) isEqual:@"需要操作"],@"permission gap needs an actionable state");
    require([FRTaskTime(@{@"status":@"running",@"created":@100},172) isEqual:@"已用时 1分12秒"],@"active task elapsed time must advance locally");
    require([FRTaskTime(@{@"status":@"succeeded",@"created":@100,@"timing":@{@"finished_at":@138}},999) isEqual:@"总用时 38秒"],@"completed duration must stay frozen");
    require([FRTaskTime(@{@"status":@"failed",@"created":@100},999) isEqual:@"用时未记录"],@"missing historical duration must not be invented");
    NSURL *cacheRoot=[NSURL fileURLWithPath:[NSTemporaryDirectory() stringByAppendingPathComponent:NSUUID.UUID.UUIDString]];
    FRCache *cacheA=[[FRCache alloc] initWithRoot:cacheRoot scope:@"origin/accountA/device"],*cacheB=[[FRCache alloc] initWithRoot:cacheRoot scope:@"origin/accountB/device"];
    [cacheA saveSnapshot:@{@"items":@[@{@"prompt":@"A"}]} key:@"chat:A"];
    require([cacheA snapshot:@"chat:A"]!=nil&&[cacheA snapshot:@"chat:B"]==nil&&[cacheB snapshot:@"chat:A"]==nil,@"conversation cache leaked across sessions/accounts");
    FRCache *reopened=[[FRCache alloc] initWithRoot:cacheRoot scope:@"origin/accountA/device"];
    require([reopened snapshot:@"chat:A"]!=nil,@"conversation cache did not survive relaunch");
    NSURL *temp=[cacheRoot URLByAppendingPathComponent:@"download.tmp"];[@"image bytes" writeToURL:temp atomically:YES encoding:NSUTF8StringEncoding error:nil];
    NSURL *media=[cacheA importFile:temp name:@"../same.png" key:@"media:A:v1" error:&error];
    require(media&&[media.lastPathComponent isEqual:@"same.png"]&&![NSFileManager.defaultManager fileExistsAtPath:temp.path],@"media was not moved into a safe local file");
    require([reopened file:@"media:A:v1"]!=nil&&[reopened file:@"media:A:v2"]==nil&&[cacheB file:@"media:A:v1"]==nil,@"media cache version/account isolation failed");
    [cacheA remove:@"media:A:v1"];require([reopened file:@"media:A:v1"]==nil,@"failed media cannot be invalidated");
    [cacheA clear];require([cacheA snapshot:@"chat:A"]==nil&&[[[FRCache alloc] initWithRoot:cacheRoot scope:@"origin/accountA/device"] snapshot:@"chat:A"]==nil,@"logout did not erase private cache");
    [NSFileManager.defaultManager removeItemAtURL:cacheRoot error:nil];
    printf("Friday Objective-C contracts passed (%d checks).\n",checks);
}return 0;}
