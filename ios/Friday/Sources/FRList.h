#import <UIKit/UIKit.h>
#import <QuickLook/QuickLook.h>
// Grouped list screen shared by FRViews.m and FRAgentViews.m. A row is a dictionary:
//   title, detail, action (tap), icon (SF Symbol), iconColor (draws a filled tile),
//   thumbnail (UIImage), status + statusCode (right label), value (right text),
//   checked, busy, destructive (red title),
//   delete (block: red trailing swipe and long-press item), deleteTitle,
//   swipe / leading (extra swipe actions, see FRSwipe), menu (UIMenuElements on long press),
//   id (makes the row selectable while choosing several for batchActions), key (for hideRows:, defaults to id).
// sections: @[@{@"title":…,@"footer":…,@"rows":@[…]}]; setting rows makes one untitled section.
@interface FRListController : UITableViewController <QLPreviewControllerDataSource,UISearchResultsUpdating>
@property(nonatomic) NSArray<NSDictionary *> *rows;
@property(nonatomic) NSArray<NSDictionary *> *sections;
@property(nonatomic,copy) void(^reloadAction)(void);
@property(nonatomic) BOOL fetching,loaded;
@property(nonatomic) NSError *loadError;
@property(nonatomic,copy) NSString *defaultIcon;
@property(nonatomic,copy) NSString *busyTitle;
@property(nonatomic,copy) NSString *emptyTitle,*emptyDetail;
@property(nonatomic) BOOL compactRows;
// Adds a search field that filters rows by title and detail.
@property(nonatomic) BOOL searchable;
// Shown in a toolbar while choosing rows: @{title, destructive, action:^(NSArray *rows)}.
@property(nonatomic,copy) NSArray<NSDictionary *> *batchActions;
@property(nonatomic) NSURL *preview;
- (void)reloadData;
// Deleting: rows whose key (or id) is listed leave at once and stay out of reloads until unhidden.
- (void)hideRows:(NSArray<NSString *> *)keys;
- (void)unhideRows:(NSArray<NSString *> *)keys;
- (void)beginLoading;
- (void)finishLoadingWithError:(NSError *)error;
- (void)updateState;
- (void)presentPreview:(NSURL *)file;
// 选择 / 完成: toggles choosing several rows for batchActions.
- (UIBarButtonItem *)selectButton;
- (void)endSelecting;
@end
FOUNDATION_EXPORT NSDictionary *FRListRow(NSString *title,NSString *detail,void(^action)(void));
// A swipe action: title, SF Symbol, background color (nil for gray).
FOUNDATION_EXPORT NSDictionary *FRSwipe(NSString *title,NSString *symbol,UIColor *color,void(^action)(void));
FOUNDATION_EXPORT void FRAskText(UIViewController *controller,NSString *title,NSString *value,void(^save)(NSString *text));
FOUNDATION_EXPORT void FRAskConfirm(UIViewController *controller,NSString *title,NSString *message,void(^action)(void));
// Confirmation for something that cannot be undone; the button names the action.
FOUNDATION_EXPORT void FRAskDestructive(UIViewController *controller,NSString *title,NSString *message,NSString *button,void(^action)(void));
// New screens (FRAgentViews.m)
FOUNDATION_EXPORT UIViewController *FRDramaRootController(void);
FOUNDATION_EXPORT UIViewController *FRWorksRootController(void);
FOUNDATION_EXPORT void FRShowDramas(UIViewController *controller);
FOUNDATION_EXPORT void FROpenDrama(UIViewController *controller,NSString *projectID);
FOUNDATION_EXPORT void FRShowMemory(UIViewController *controller);
FOUNDATION_EXPORT void FRShowSchedules(UIViewController *controller);
FOUNDATION_EXPORT NSArray<UIView *> *FRTodoViews(NSArray *items);
// Session actions shared by lists and the conversation menu (FRViews.m).
FOUNDATION_EXPORT void FRDeleteSessions(UIViewController *controller,NSArray<NSString *> *sessionIDs,void(^done)(void));
FOUNDATION_EXPORT void FRRestoreSessions(UIViewController *controller,NSArray<NSString *> *sessionIDs,void(^done)(void));
