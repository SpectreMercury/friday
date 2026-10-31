#import "FRAPI.h"
@interface FRStore : NSObject
@property(nonatomic) NSArray<NSDictionary *> *sessions;
@property(nonatomic) NSArray<NSDictionary *> *tasks;
@property(nonatomic) NSMutableDictionary<NSString *,NSMutableDictionary *> *drafts;
@property(nonatomic) NSString *connection;
// /v1/home: what the Mac is doing (now, queue, active), what waits on the user (attention) and next_schedule.
// nil until loaded, and stays nil on a Mac service that predates it.
@property(nonatomic) NSDictionary *home;
// Ids of sessions, tasks and drama projects being deleted. They leave every list at once and stay out of
// later refreshes, so a slow delete on the Mac never shows them again; unhide: brings them back (failure, undo).
@property(nonatomic,readonly) NSMutableSet<NSString *> *removing;
- (void)hide:(NSArray<NSString *> *)ids;
- (void)unhide:(NSArray<NSString *> *)ids;
// items without those being deleted (matched by id, session_id or parent_job_id).
- (NSArray *)kept:(NSArray *)items;
@property(nonatomic,readonly) BOOL loading,sessionsLoaded,tasksLoaded;
@property(nonatomic,readonly) NSError *sessionsError,*tasksError;
+ (instancetype)shared;
- (instancetype)initWithAPI:(FRAPI *)api;
- (void)refresh;
- (void)start;
- (void)stop;
- (NSDictionary *)session:(NSString *)sid;
- (void)sendText:(NSString *)text session:(NSString *)sid completion:(FRReply)completion;
// options: thinking (BOOL) and agents (invited agent ids). A retry keeps the first options.
- (void)sendText:(NSString *)text session:(NSString *)sid options:(NSDictionary *)options completion:(FRReply)completion;
@end

FOUNDATION_EXPORT BOOL FRTaskIsActive(NSDictionary *task);
FOUNDATION_EXPORT BOOL FRTaskMatchesFilter(NSDictionary *task,NSInteger filter);
FOUNDATION_EXPORT NSString *FRTaskStatus(NSDictionary *task);
FOUNDATION_EXPORT NSString *FRTaskTime(NSDictionary *task, NSTimeInterval now);
