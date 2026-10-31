#import "FRStore.h"

BOOL FRTaskIsActive(NSDictionary *task){return [@[@"queued",@"pending",@"running",@"waiting_approval",@"awaiting_approval",@"waiting"] containsObject:task[@"status"]];}
BOOL FRTaskMatchesFilter(NSDictionary *task,NSInteger filter){if(filter==0)return YES;if(filter==1)return FRTaskIsActive(task);return [@[@"succeeded",@"failed",@"cancelled"] containsObject:task[@"status"]];}
NSString *FRTaskStatus(NSDictionary *task){
    if([task[@"status_text"] isKindOfClass:NSString.class]&&[task[@"status_text"] length])return task[@"status_text"];
    if(!FRTaskIsActive(task)){
        if([task[@"outcome"] isEqual:@"needs_input"])return @"需要操作";
        if([task[@"outcome"] isEqual:@"blocked"])return @"未完成";
        if([task[@"outcome"] isEqual:@"partial"])return @"部分完成";
    }
    return @{@"queued":@"排队中",@"pending":@"排队中",@"running":@"进行中",@"waiting_approval":@"等待确认",@"awaiting_approval":@"等待确认",@"waiting":@"等待中",@"succeeded":@"已完成",@"failed":@"失败",@"cancelled":@"已停止"}[task[@"status"]]?:FRString(task[@"status"]);
}
NSString *FRTaskTime(NSDictionary *task,NSTimeInterval now){
    if([task[@"kind"] isEqual:@"imported"])return @"";
    NSDictionary *timing=[task[@"timing"] isKindOfClass:NSDictionary.class]?task[@"timing"]:@{};
    NSNumber *created=[task[@"created"] isKindOfClass:NSNumber.class]?task[@"created"]:nil;
    NSNumber *finished=[timing[@"finished_at"] isKindOfClass:NSNumber.class]?timing[@"finished_at"]:nil;
    BOOL active=FRTaskIsActive(task);
    if(!created||(!active&&!finished))return @"用时未记录";
    NSInteger seconds=(NSInteger)MAX(0,floor((active?now:finished.doubleValue)-created.doubleValue));
    NSString *duration=seconds<60?[NSString stringWithFormat:@"%ld秒",(long)seconds]:(seconds<3600?[NSString stringWithFormat:@"%ld分%02ld秒",(long)(seconds/60),(long)(seconds%60)]:[NSString stringWithFormat:@"%ld小时%02ld分",(long)(seconds/3600),(long)((seconds%3600)/60)]);
    return [NSString stringWithFormat:@"%@ %@",active?([task[@"status"] isEqual:@"queued"]?@"已等待":@"已用时"):@"总用时",duration];
}

@interface FRStore ()
@property(nonatomic) NSTimer *timer;
@property(nonatomic) BOOL loading,active,sessionsLoaded,tasksLoaded;
@property(nonatomic) NSError *sessionsError,*tasksError;
@property(nonatomic) FRAPI *api;
@end
@implementation FRStore
+ (instancetype)shared { static FRStore *v;static dispatch_once_t once;dispatch_once(&once,^{v=[FRStore new];});return v; }
- (instancetype)init {return [self initWithAPI:FRAPI.shared];}
- (instancetype)initWithAPI:(FRAPI *)api { if((self=[super init])) { _api=api; _sessions=@[];_tasks=@[];_removing=[NSMutableSet set];_drafts=[NSMutableDictionary dictionary];_connection=@"正在连接";[[NSNotificationCenter defaultCenter] addObserver:self selector:@selector(accountChanged) name:FRAccountChanged object:nil];}return self; }
- (void)accountChanged { [self stop];[self.removing removeAllObjects];self.sessions=@[];self.tasks=@[];self.home=nil;[self.drafts removeAllObjects];self.loading=NO;self.sessionsLoaded=NO;self.tasksLoaded=NO;self.sessionsError=nil;self.tasksError=nil;if(self.api.credentials)[self start]; }
- (void)start { [self stop];self.active=YES;[self refresh];__weak typeof(self) weak=self;self.timer=[NSTimer scheduledTimerWithTimeInterval:3 repeats:YES block:^(NSTimer *t){[weak refresh];}]; }
- (void)stop { [self.timer invalidate];self.timer=nil;self.active=NO; }
- (NSArray *)kept:(NSArray *)items {
    if(!self.removing.count||![items isKindOfClass:NSArray.class])return items;NSSet *gone=self.removing;
    return [items filteredArrayUsingPredicate:[NSPredicate predicateWithBlock:^BOOL(id item,NSDictionary *b){return ![item isKindOfClass:NSDictionary.class]||(![gone containsObject:FRString(item[@"id"])]&&![gone containsObject:FRString(item[@"session_id"])]&&![gone containsObject:FRString(item[@"parent_job_id"])]);}]];
}
- (void)setSessions:(NSArray<NSDictionary *> *)sessions {_sessions=[self kept:sessions]?:@[];}
- (void)setTasks:(NSArray<NSDictionary *> *)tasks {_tasks=[self kept:tasks]?:@[];}
- (void)setHome:(NSDictionary *)home {
    if(!home||!self.removing.count){_home=home;return;}NSMutableDictionary *m=[home mutableCopy];
    for(NSString *key in @[@"queue",@"active",@"attention"])if(m[key])m[key]=[self kept:m[key]];
    NSDictionary *now=[m[@"now"] isKindOfClass:NSDictionary.class]?m[@"now"]:nil;if(now&&![self kept:@[now]].count)m[@"now"]=NSNull.null;
    _home=m;
}
- (void)hide:(NSArray<NSString *> *)ids {
    if(!ids.count)return;[self.removing addObjectsFromArray:ids];
    self.sessions=self.sessions;self.tasks=self.tasks;self.home=self.home;
    [[NSNotificationCenter defaultCenter] postNotificationName:FRDataChanged object:nil];
}
- (void)unhide:(NSArray<NSString *> *)ids {
    if(!ids.count)return;for(NSString *i in ids)[self.removing removeObject:i];[self refresh];
}
- (NSDictionary *)session:(NSString *)sid { for(NSDictionary *s in self.sessions)if([s[@"id"] isEqual:sid])return s;return nil; }
- (void)refresh {
    if(self.loading||!self.api.credentials||!self.active)return;self.loading=YES;if(!self.sessionsLoaded||!self.tasksLoaded)[[NSNotificationCenter defaultCenter] postNotificationName:FRDataChanged object:nil];
    NSUInteger generation=self.api.generation;
    [self.api get:@"/v1/sessions" completion:^(NSDictionary *v,NSError *e){
        if(generation!=self.api.generation)return;
        if(e){self.loading=NO;self.sessionsError=e;self.tasksError=e;self.connection=@"等待连接";[[NSNotificationCenter defaultCenter] postNotificationName:FRDataChanged object:nil];return;}
        self.sessions=v[@"items"]?:@[];self.sessionsLoaded=YES;self.sessionsError=nil;self.connection=@"已连接";if(!self.tasksLoaded)[[NSNotificationCenter defaultCenter] postNotificationName:FRDataChanged object:nil];
        [self.api get:@"/v1/tasks" completion:^(NSDictionary *v,NSError *e){if(generation!=self.api.generation)return;self.loading=NO;self.tasksError=e;if(!e){self.tasks=v[@"items"]?:@[];self.tasksLoaded=YES;}[[NSNotificationCenter defaultCenter] postNotificationName:FRDataChanged object:nil];
            // The summary decorates lists that are already shown; it never holds up their loading.
            if(!e)[self.api get:@"/v1/home" completion:^(NSDictionary *h,NSError *he){if(generation!=self.api.generation)return;if(!he&&[h isKindOfClass:NSDictionary.class])self.home=h;else if(he.code==404)self.home=nil;else return;[[NSNotificationCenter defaultCenter] postNotificationName:FRDataChanged object:nil];}];}];
    }];
}
- (void)sendText:(NSString *)text session:(NSString *)sid completion:(FRReply)completion {[self sendText:text session:sid options:nil completion:completion];}
- (void)sendText:(NSString *)text session:(NSString *)sid options:(NSDictionary *)options completion:(FRReply)completion {
    NSMutableDictionary *draft=self.drafts[sid]?:[NSMutableDictionary dictionary];
    if([draft[@"sending"] boolValue])return;
    // Uncertain submissions retain their text, options and idempotency identity.
    if(!draft[@"request_id"]){draft[@"request_id"]=NSUUID.UUID.UUIDString;draft[@"submitted"]=text;draft[@"options"]=options?:@{};}
    draft[@"text"]=text;draft[@"sending"]=@YES;self.drafts[sid]=draft;
    NSUInteger generation=self.api.generation;
    NSMutableDictionary *body=[@{@"text":draft[@"submitted"],@"request_id":draft[@"request_id"]} mutableCopy];
    NSDictionary *sent=draft[@"options"];if(sent[@"thinking"])body[@"thinking"]=@([sent[@"thinking"] boolValue]);if([sent[@"agents"] count])body[@"agents"]=sent[@"agents"];
    [self.api call:[self.api pathForSession:sid suffix:@"/messages"] method:@"POST" body:body completion:^(id v,NSError *e){
        if(generation!=self.api.generation){completion(nil,e);return;}
        // A conversation refresh may have confirmed this request already. Its
        // delayed POST callback must not erase a newer draft in the same chat.
        if(self.drafts[sid]!=draft){completion(v,e);return;}
        draft[@"sending"]=@NO;
        if(!e)[self.drafts removeObjectForKey:sid];
        else if(e.code>=400&&e.code<500){[draft removeObjectForKey:@"request_id"];[draft removeObjectForKey:@"submitted"];[draft removeObjectForKey:@"options"];[draft removeObjectForKey:@"uncertain"];}else draft[@"uncertain"]=@YES;
        [self refresh];completion(v,e);
    }];
}
@end
