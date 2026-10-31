#import "FRViews.h"
#import "FRChatComponents.h"
#import "FRList.h"
#if DEBUG && TARGET_OS_SIMULATOR
void FRDemoOpenAgentPage(UINavigationController *nav,NSString *page);
#endif
#import "FRMarkdownView.h"
#import "FRAPI.h"
#import "FRStore.h"
#import "FRVoice.h"
#import "FRDemo.h"
#import <TargetConditionals.h>
#import <AVKit/AVKit.h>
#import <QuickLook/QuickLook.h>
#import <UniformTypeIdentifiers/UniformTypeIdentifiers.h>
#import <SafariServices/SafariServices.h>
#import <UserNotifications/UserNotifications.h>

@protocol FRLoadingSurface <NSObject>
- (void)finishLoadingWithError:(NSError *)error;
- (NSArray *)rows;
@end

static UIImage *FRIcon(NSString *name,CGFloat size){return [UIImage systemImageNamed:name withConfiguration:[UIImageSymbolConfiguration configurationWithPointSize:size weight:UIImageSymbolWeightRegular]];}
void FRSetButtonBusy(UIButton *button,BOOL busy){FRComponentSetBusy(button,busy);}

static NSString *FRRowIcon(NSString *title){
    return @{@"连接的账号":@"link",@"连接 Google Drive":@"plus.circle",@"通知":@"bell.badge",@"技能库":@"square.stack.3d.up",@"登录设备":@"iphone",@"名称":@"pencil",@"技能":@"square.stack.3d.up",@"可访问资源":@"lock.shield",@"会话文件":@"folder",@"从这里新建会话":@"arrow.triangle.branch",@"Mac 管理":@"desktopcomputer",@"Mac 状态":@"waveform.path.ecg",@"能力与升级":@"arrow.up.circle",@"归档对话":@"archivebox",@"取消归档":@"tray.and.arrow.up",@"清空上下文":@"eraser",@"保存选择":@"checkmark",@"创建新会话":@"square.and.pencil",@"创建或改进能力":@"plus.circle",@"核心升级器":@"cpu",@"改动说明":@"text.alignleft",@"验证记录":@"checkmark.shield",@"确认并验证":@"checkmark.shield",@"确认发布":@"arrow.up.circle",@"撤销此次升级":@"arrow.uturn.backward",@"恢复此版本":@"clock.arrow.circlepath",@"停止播报":@"speaker.slash",@"结束通话":@"phone.down",@"切换到当前会话":@"arrow.left.arrow.right",@"停止录音":@"stop.circle",@"发送语音留言":@"arrow.up.circle",@"丢弃录音":@"trash",@"开始实时通话":@"waveform",@"录制语音留言":@"mic",@"连接状态":@"antenna.radiowaves.left.and.right",@"刚刚听到":@"text.bubble",@"记忆":@"brain",@"定时任务":@"alarm",@"外观":@"circle.lefthalf.filled",@"已归档对话":@"archivebox",@"最近删除":@"trash",@"删除对话":@"trash"}[title];
}
static UIView *FRStateView(NSString *symbol,NSString *title,NSString *detail,void(^retry)(void)){
    UIView *view=[UIView new];UIStackView *stack=[UIStackView new];stack.axis=UILayoutConstraintAxisVertical;stack.alignment=UIStackViewAlignmentCenter;stack.spacing=10;stack.translatesAutoresizingMaskIntoConstraints=NO;
    UIImageView *icon=[[UIImageView alloc] initWithImage:FRIcon(symbol,34)];icon.tintColor=UIColor.tertiaryLabelColor;[stack addArrangedSubview:icon];[stack setCustomSpacing:14 afterView:icon];
    UILabel *heading=[UILabel new];heading.text=title;heading.textAlignment=NSTextAlignmentCenter;heading.numberOfLines=0;heading.font=[UIFont preferredFontForTextStyle:UIFontTextStyleHeadline];heading.adjustsFontForContentSizeCategory=YES;[stack addArrangedSubview:heading];
    if(detail.length){UILabel *label=[UILabel new];label.text=detail;label.textAlignment=NSTextAlignmentCenter;label.numberOfLines=0;label.font=[UIFont preferredFontForTextStyle:UIFontTextStyleSubheadline];label.adjustsFontForContentSizeCategory=YES;label.textColor=UIColor.secondaryLabelColor;[stack addArrangedSubview:label];}
    if(retry){UIButton *button=[UIButton buttonWithType:UIButtonTypeSystem];UIButtonConfiguration *c=UIButtonConfiguration.borderedButtonConfiguration;c.cornerStyle=UIButtonConfigurationCornerStyleCapsule;c.title=@"重新加载";c.image=FRIcon(@"arrow.clockwise",14);c.imagePadding=6;c.baseForegroundColor=UIColor.labelColor;button.configuration=c;button.accessibilityIdentifier=@"loading.retry";[button.heightAnchor constraintGreaterThanOrEqualToConstant:44].active=YES;[button addAction:[UIAction actionWithHandler:^(UIAction *a){retry();}] forControlEvents:UIControlEventTouchUpInside];[stack addArrangedSubview:button];}
    [view addSubview:stack];[NSLayoutConstraint activateConstraints:@[[stack.centerXAnchor constraintEqualToAnchor:view.centerXAnchor],[stack.centerYAnchor constraintEqualToAnchor:view.safeAreaLayoutGuide.centerYAnchor constant:-20],[stack.leadingAnchor constraintGreaterThanOrEqualToAnchor:view.leadingAnchor constant:32],[stack.trailingAnchor constraintLessThanOrEqualToAnchor:view.trailingAnchor constant:-32]]];return view;
}
static UITableViewCell *FRSkeletonCell(NSString *kind){
    UITableViewCell *cell=[[UITableViewCell alloc] initWithStyle:UITableViewCellStyleDefault reuseIdentifier:nil];cell.backgroundColor=[kind isEqual:@"chat"]?UIColor.clearColor:UIColor.secondarySystemGroupedBackgroundColor;cell.selectionStyle=UITableViewCellSelectionStyleNone;FRSkeletonView *v=[[FRSkeletonView alloc] initWithKind:kind];v.translatesAutoresizingMaskIntoConstraints=NO;[cell.contentView addSubview:v];[NSLayoutConstraint activateConstraints:@[[v.leadingAnchor constraintEqualToAnchor:cell.contentView.leadingAnchor constant:16],[v.trailingAnchor constraintEqualToAnchor:cell.contentView.trailingAnchor constant:-16],[v.topAnchor constraintEqualToAnchor:cell.contentView.topAnchor],[v.bottomAnchor constraintEqualToAnchor:cell.contentView.bottomAnchor]]];return cell;
}
// Settings-style glyph: a white symbol on a small colored tile.
static UIImage *FRTileIcon(NSString *symbol,UIColor *color){
    UIImage *glyph=[[UIImage systemImageNamed:symbol withConfiguration:[UIImageSymbolConfiguration configurationWithPointSize:15 weight:UIImageSymbolWeightMedium]] imageWithTintColor:UIColor.whiteColor renderingMode:UIImageRenderingModeAlwaysOriginal];
    UIGraphicsImageRenderer *renderer=[[UIGraphicsImageRenderer alloc] initWithSize:CGSizeMake(30,30)];
    return [[renderer imageWithActions:^(UIGraphicsImageRendererContext *context){
        [color setFill];[[UIBezierPath bezierPathWithRoundedRect:CGRectMake(0,0,30,30) cornerRadius:7.5] fill];
        CGFloat scale=MIN(18/glyph.size.width,18/glyph.size.height);CGSize size=CGSizeMake(glyph.size.width*scale,glyph.size.height*scale);
        [glyph drawInRect:CGRectMake((30-size.width)/2,(30-size.height)/2,size.width,size.height)];
    }] imageWithRenderingMode:UIImageRenderingModeAlwaysOriginal];
}
static UIImage *FRRoundedThumbnail(UIImage *image){
    UIGraphicsImageRenderer *renderer=[[UIGraphicsImageRenderer alloc] initWithSize:CGSizeMake(44,44)];
    return [[renderer imageWithActions:^(UIGraphicsImageRendererContext *context){
        [[UIBezierPath bezierPathWithRoundedRect:CGRectMake(0,0,44,44) cornerRadius:8] addClip];
        CGFloat scale=MAX(44/image.size.width,44/image.size.height);CGSize size=CGSizeMake(image.size.width*scale,image.size.height*scale);
        [image drawInRect:CGRectMake((44-size.width)/2,(44-size.height)/2,size.width,size.height)];
    }] imageWithRenderingMode:UIImageRenderingModeAlwaysOriginal];
}

void FRShowError(UIViewController *vc,NSError *e){if(!e||e.code==NSURLErrorCancelled)return;if([vc respondsToSelector:@selector(finishLoadingWithError:)]){[(id<FRLoadingSurface>)vc finishLoadingWithError:e];if([vc respondsToSelector:@selector(rows)]&&![(id<FRLoadingSurface>)vc rows].count)return;}UIAlertController *a=[UIAlertController alertControllerWithTitle:@"暂时未完成" message:e.localizedDescription preferredStyle:UIAlertControllerStyleAlert];[a addAction:[UIAlertAction actionWithTitle:@"好" style:UIAlertActionStyleCancel handler:nil]];[vc presentViewController:a animated:YES completion:nil];}
static void FRPrompt(UIViewController *vc,NSString *title,NSString *value,void(^save)(NSString *)){UIAlertController *a=[UIAlertController alertControllerWithTitle:title message:nil preferredStyle:UIAlertControllerStyleAlert];[a addTextFieldWithConfigurationHandler:^(UITextField *t){t.text=value;t.clearButtonMode=UITextFieldViewModeWhileEditing;}];[a addAction:[UIAlertAction actionWithTitle:@"取消" style:UIAlertActionStyleCancel handler:nil]];[a addAction:[UIAlertAction actionWithTitle:@"保存" style:UIAlertActionStyleDefault handler:^(UIAlertAction *x){NSString *text=[a.textFields.firstObject.text stringByTrimmingCharactersInSet:NSCharacterSet.whitespaceAndNewlineCharacterSet];if(text.length)save(text);}]];[vc presentViewController:a animated:YES completion:nil];}
static void FRConfirm(UIViewController *vc,NSString *title,NSString *message,void(^action)(void)){UIAlertController *a=[UIAlertController alertControllerWithTitle:title message:message preferredStyle:UIAlertControllerStyleAlert];[a addAction:[UIAlertAction actionWithTitle:@"取消" style:UIAlertActionStyleCancel handler:nil]];[a addAction:[UIAlertAction actionWithTitle:@"确认" style:UIAlertActionStyleDestructive handler:^(UIAlertAction *x){action();}]];[vc presentViewController:a animated:YES completion:nil];}
void FRAskDestructive(UIViewController *vc,NSString *title,NSString *message,NSString *button,void(^action)(void)){UIAlertController *a=[UIAlertController alertControllerWithTitle:title message:message preferredStyle:UIAlertControllerStyleAlert];[a addAction:[UIAlertAction actionWithTitle:@"取消" style:UIAlertActionStyleCancel handler:nil]];[a addAction:[UIAlertAction actionWithTitle:button style:UIAlertActionStyleDestructive handler:^(UIAlertAction *x){action();}]];[vc presentViewController:a animated:YES completion:nil];}
static UIButton *FRButton(NSString *symbol,NSString *label,void(^action)(void)){UIButton *b=[UIButton buttonWithType:UIButtonTypeSystem];UIButtonConfiguration *config=UIButtonConfiguration.plainButtonConfiguration;config.image=FRIcon(symbol,18);config.contentInsets=NSDirectionalEdgeInsetsZero;b.configuration=config;b.tintColor=UIColor.labelColor;b.accessibilityLabel=label;[b addAction:[UIAction actionWithHandler:^(UIAction *a){action();}] forControlEvents:UIControlEventTouchUpInside];NSLayoutConstraint *width=[b.widthAnchor constraintEqualToConstant:44];width.priority=999;width.active=YES;[b.heightAnchor constraintEqualToConstant:44].active=YES;return b;}
static NSString *FRStatus(NSDictionary *j){return FRTaskStatus(j);}

static BOOL FRActive(NSDictionary *j){return FRTaskIsActive(j);}
static NSDictionary *FRRow(NSString *title,NSString *detail,void(^action)(void)){NSMutableDictionary *row=[@{@"title":title?:@"",@"detail":detail?:@""} mutableCopy];if(action)row[@"action"]=[action copy];if(FRRowIcon(title))row[@"icon"]=FRRowIcon(title);return row;}
NSDictionary *FRListRow(NSString *title,NSString *detail,void(^action)(void)){return FRRow(title,detail,action);}
NSDictionary *FRSwipe(NSString *title,NSString *symbol,UIColor *color,void(^action)(void)){NSMutableDictionary *swipe=[@{@"title":title?:@"",@"action":[action copy]} mutableCopy];if(symbol)swipe[@"icon"]=symbol;if(color)swipe[@"color"]=color;return swipe;}
static NSMutableDictionary *FRMutableRow(NSString *title,NSString *detail,void(^action)(void)){return [FRRow(title,detail,action) mutableCopy];}
void FRAskText(UIViewController *vc,NSString *title,NSString *value,void(^save)(NSString *)){FRPrompt(vc,title,value,save);}
void FRAskConfirm(UIViewController *vc,NSString *title,NSString *message,void(^action)(void)){FRConfirm(vc,title,message,action);}

// Render user turns and their media without exposing child tool prompts as turns.
static NSArray *FRConversationRows(NSArray *items,NSString *sid) {
    NSMutableDictionary *byID=[NSMutableDictionary dictionary],*children=[NSMutableDictionary dictionary];NSMutableArray *roots=[NSMutableArray array],*orphans=[NSMutableArray array],*result=[NSMutableArray array];
    for(NSDictionary *j in items)if([j[@"session_id"] isEqual:sid]&&[j[@"id"] isKindOfClass:NSString.class]){byID[j[@"id"]]=j;if(!j[@"parent_job_id"]||j[@"parent_job_id"]==NSNull.null)[roots addObject:j];}
    for(NSDictionary *j in items){if(![j[@"session_id"] isEqual:sid]||(!FRString(j[@"artifact_url"]).length&&![j[@"project"] isKindOfClass:NSDictionary.class]&&![j[@"shot"] isKindOfClass:NSDictionary.class])||!j[@"parent_job_id"]||j[@"parent_job_id"]==NSNull.null)continue;NSDictionary *root=j;NSMutableSet *seen=[NSMutableSet set];while([root[@"parent_job_id"] isKindOfClass:NSString.class]&&byID[root[@"parent_job_id"]]&&![seen containsObject:root[@"id"]]){[seen addObject:root[@"id"]];root=byID[root[@"parent_job_id"]];}if(root[@"parent_job_id"]&&root[@"parent_job_id"]!=NSNull.null){[orphans addObject:j];continue;}NSString *key=root[@"id"];if(!children[key])children[key]=[NSMutableArray array];[children[key] addObject:j];}
    for(NSDictionary *root in roots){[result addObject:root];NSArray *own=children[root[@"id"]]?:@[];[result addObjectsFromArray:own];
        // Media the reply sent from earlier turns shows under that reply too, where the user expects it.
        NSArray *sent=[root[@"sent_media"] isKindOfClass:NSArray.class]?root[@"sent_media"]:@[];
        for(id mid in sent){NSDictionary *media=[mid isKindOfClass:NSString.class]?byID[mid]:nil;if(media&&FRString(media[@"artifact_url"]).length&&![own containsObject:media])[result addObject:media];}}
    [result addObjectsFromArray:orphans];return result;
}

// Agents the user can @ into a conversation. The Mac's list replaces this default.
static NSArray<NSDictionary *> *FRDefaultAgents(void){return @[@{@"id":@"drama",@"name":@"短剧助手",@"symbol":@"film",@"description":@"把剧本做成多镜头短剧：设定、分镜、渲染、合成"}];}
static NSString *FRAgentsKey(NSString *sid){return [@"FridayAgents." stringByAppendingString:sid?:@""];}
static NSArray<NSString *> *FRSavedAgents(NSString *sid){NSArray *saved=[NSUserDefaults.standardUserDefaults arrayForKey:FRAgentsKey(sid)];return [saved isKindOfClass:NSArray.class]?saved:@[];}
// List timestamps the way chat apps do: 刚刚, 14:05, 昨天, 星期二, 10月20日, 2025/10/20.
static NSString *FRRelativeTime(NSTimeInterval stamp){
    NSDate *date=[NSDate dateWithTimeIntervalSince1970:stamp];NSCalendar *calendar=NSCalendar.currentCalendar;NSTimeInterval age=-date.timeIntervalSinceNow;
    if(age<60)return @"刚刚";
    static NSDateFormatter *formatter;if(!formatter){formatter=[NSDateFormatter new];formatter.locale=[NSLocale localeWithLocaleIdentifier:@"zh_CN"];}
    if([calendar isDateInToday:date])formatter.dateFormat=@"HH:mm";
    else if([calendar isDateInYesterday:date])return @"昨天";
    else if(age<6*86400)formatter.dateFormat=@"EEEE";
    else if([calendar component:NSCalendarUnitYear fromDate:date]==[calendar component:NSCalendarUnitYear fromDate:NSDate.date])formatter.dateFormat=@"M月d日";
    else formatter.dateFormat=@"yyyy/M/d";
    return [formatter stringFromDate:date];
}
// Section heading for a list grouped by recency.
static NSString *FRRecencyGroup(NSTimeInterval stamp){
    if(stamp<=0)return @"更早";NSDate *date=[NSDate dateWithTimeIntervalSince1970:stamp];NSCalendar *calendar=NSCalendar.currentCalendar;
    if([calendar isDateInToday:date])return @"今天";if([calendar isDateInYesterday:date])return @"昨天";
    if(-date.timeIntervalSinceNow<7*86400)return @"过去 7 天";if(-date.timeIntervalSinceNow<30*86400)return @"过去 30 天";return @"更早";
}
static NSString *FRLiveWaiting(NSDictionary *live){NSString *status=FRString(live[@"status"]);return status.length?status:@"Friday 正在回复…";}
static NSString *FRLiveSignature(NSDictionary *live){
    BOOL thinking=FRString(live[@"thinking"]).length>0,tools=[live[@"tools"] isKindOfClass:NSArray.class]&&[live[@"tools"] count],text=FRString(live[@"text"]).length>0;
    return [NSString stringWithFormat:@"live:%d%d%d",thinking,tools,text];
}
static NSString *FRDramaStage(NSString *stage){return @{@"setup":@"设定中",@"storyboard":@"分镜中",@"producing":@"制作中",@"ready":@"可导出"}[stage]?:FRString(stage);}
static NSTimeInterval FRSessionActivity(NSDictionary *s){NSDictionary *last=[s[@"last"] isKindOfClass:NSDictionary.class]?s[@"last"]:nil;return MAX([s[@"created"] doubleValue],[last[@"created"] doubleValue]);}

#pragma mark - Appearance

static NSString * const FRAppearanceKey=@"FridayAppearance";
static NSArray *FRAppearanceNames(void){return @[@"跟随系统",@"浅色",@"深色"];}
static NSInteger FRAppearanceChoice(void){NSInteger value=[NSUserDefaults.standardUserDefaults integerForKey:FRAppearanceKey];return value>=0&&value<=2?value:0;}
void FRApplyAppearance(UIWindow *window){
    UIUserInterfaceStyle styles[]={UIUserInterfaceStyleUnspecified,UIUserInterfaceStyleLight,UIUserInterfaceStyleDark};UIUserInterfaceStyle style=styles[FRAppearanceChoice()];
    NSMutableArray *windows=[NSMutableArray array];if(window)[windows addObject:window];
    for(UIScene *scene in UIApplication.sharedApplication.connectedScenes)if([scene isKindOfClass:UIWindowScene.class])[windows addObjectsFromArray:((UIWindowScene *)scene).windows];
    for(UIWindow *w in windows){w.overrideUserInterfaceStyle=style;w.tintColor=UIColor.labelColor;}
}

#pragma mark - Shared list

@interface FRListController ()
@property(nonatomic,copy) NSString *query;
@property(nonatomic) NSArray<NSDictionary *> *visible;
@property(nonatomic) UIBarButtonItem *chooseItem;
@property(nonatomic) NSMutableSet<NSString *> *hiddenRows;
// For lists whose loading state comes from FRStore rather than a fetch.
- (void)replaceSections:(NSArray<NSDictionary *> *)sections;
- (NSDictionary *)rowAt:(NSIndexPath *)index;
- (void)toggleSelecting;
@end
@implementation FRListController
- (instancetype)init {return [super initWithStyle:UITableViewStyleInsetGrouped];}
- (void)viewDidLoad {
    [super viewDidLoad];UIColor *ground=self.tableView.style==UITableViewStylePlain?FRPaper():UIColor.systemGroupedBackgroundColor;self.view.backgroundColor=ground;self.tableView.backgroundColor=ground;
    if(self.tableView.style==UITableViewStylePlain){self.tableView.sectionHeaderTopPadding=6;self.tableView.separatorColor=FRHairline();}
    self.tableView.estimatedRowHeight=56;self.tableView.rowHeight=UITableViewAutomaticDimension;self.tableView.cellLayoutMarginsFollowReadableWidth=YES;
    self.tableView.allowsMultipleSelectionDuringEditing=YES;
    if(self.navigationItem.largeTitleDisplayMode==UINavigationItemLargeTitleDisplayModeAutomatic)self.navigationItem.largeTitleDisplayMode=UINavigationItemLargeTitleDisplayModeNever;
    self.refreshControl=[UIRefreshControl new];[self.refreshControl addTarget:self action:@selector(pullRefresh) forControlEvents:UIControlEventValueChanged];
    if(self.searchable)[self installSearch];
}
- (void)setSearchable:(BOOL)searchable {_searchable=searchable;if(searchable&&self.isViewLoaded)[self installSearch];}
- (void)installSearch {
    if(self.navigationItem.searchController)return;
    UISearchController *search=[[UISearchController alloc] initWithSearchResultsController:nil];search.searchResultsUpdater=self;search.obscuresBackgroundDuringPresentation=NO;search.searchBar.placeholder=@"搜索";
    self.navigationItem.searchController=search;self.navigationItem.hidesSearchBarWhenScrolling=YES;self.definesPresentationContext=YES;
}
- (void)updateSearchResultsForSearchController:(UISearchController *)controller {
    NSString *text=[controller.searchBar.text stringByTrimmingCharactersInSet:NSCharacterSet.whitespaceCharacterSet];
    if([text isEqual:self.query?:@""])return;self.query=text;[self updateState];
}
- (void)viewWillAppear:(BOOL)animated {[super viewWillAppear:animated];[self reloadData];}
- (void)viewWillDisappear:(BOOL)animated {[super viewWillDisappear:animated];if(self.editing)[self endSelecting];}
- (void)pullRefresh {[self reloadData];}
- (void)beginLoading {self.fetching=YES;self.loadError=nil;[self updateState];}
- (NSArray<NSDictionary *> *)rows {NSMutableArray *all=[NSMutableArray array];for(NSDictionary *section in self.sections)[all addObjectsFromArray:section[@"rows"]?:@[]];return all;}
- (void)setRows:(NSArray<NSDictionary *> *)rows {self.sections=@[@{@"rows":rows?:@[]}];}
- (void)replaceSections:(NSArray<NSDictionary *> *)sections {_sections=[sections copy];}
- (void)setSections:(NSArray<NSDictionary *> *)sections {_sections=[sections copy];self.loaded=YES;self.fetching=NO;self.loadError=nil;if(self.isViewLoaded)[self updateState];}
- (void)finishLoadingWithError:(NSError *)error {self.fetching=NO;self.loadError=error;[self updateState];}
- (BOOL)showsSkeleton {return self.fetching&&!self.loaded&&!self.loadError;}
- (NSArray<NSDictionary *> *)filteredSections {
    NSMutableArray *result=[NSMutableArray array];NSString *query=self.query;
    for(NSDictionary *section in self.sections){
        NSArray *rows=section[@"rows"]?:@[];NSSet *hidden=self.hiddenRows;
        if(hidden.count)rows=[rows filteredArrayUsingPredicate:[NSPredicate predicateWithBlock:^BOOL(NSDictionary *row,NSDictionary *b){return ![hidden containsObject:FRString(row[@"key"]?:row[@"id"])];}]];
        if(query.length)rows=[rows filteredArrayUsingPredicate:[NSPredicate predicateWithBlock:^BOOL(NSDictionary *row,NSDictionary *b){return [FRString(row[@"title"]) localizedCaseInsensitiveContainsString:query]||[FRString(row[@"detail"]) localizedCaseInsensitiveContainsString:query];}]];
        if(rows.count||(!query.length&&section[@"title"]&&[section[@"keepEmpty"] boolValue])){NSMutableDictionary *copy=[section mutableCopy];copy[@"rows"]=rows;[result addObject:copy];}
    }
    return result;
}
- (void)updateState {
    if(!self.fetching)[self.refreshControl endRefreshing];
    self.visible=[self filteredSections];NSUInteger count=0;for(NSDictionary *s in self.visible)count+=[s[@"rows"] count];
    __weak typeof(self) weak=self;
    if(!count&&self.loadError)self.tableView.backgroundView=FRStateView(@"wifi.exclamationmark",@"暂时无法加载",@"请检查与 Mac 的连接",^{[weak pullRefresh];});
    else if(!count&&self.query.length)self.tableView.backgroundView=FRStateView(@"magnifyingglass",@"没有找到相关内容",[NSString stringWithFormat:@"换个关键词试试，不只是「%@」",self.query],nil);
    else if(!count&&self.loaded&&!self.fetching)self.tableView.backgroundView=FRStateView(self.defaultIcon?:@"tray",self.emptyTitle?:@"这里还没有内容",self.emptyDetail,nil);
    else self.tableView.backgroundView=nil;
    [self.tableView reloadData];[self updateToolbar];self.chooseItem.enabled=count>0||self.editing;
}
- (void)hideRows:(NSArray<NSString *> *)keys {if(!keys.count)return;if(!self.hiddenRows)self.hiddenRows=[NSMutableSet set];[self.hiddenRows addObjectsFromArray:keys];if(self.editing)[self endSelecting];if(self.isViewLoaded)[self updateState];}
- (void)unhideRows:(NSArray<NSString *> *)keys {for(NSString *k in keys)[self.hiddenRows removeObject:k];if(self.isViewLoaded)[self updateState];}
- (void)reloadData {if(self.reloadAction){if(self.fetching)return;[self beginLoading];self.reloadAction();}else [self updateState];}
- (NSDictionary *)rowAt:(NSIndexPath *)index {if(index.section>=self.visible.count)return nil;NSArray *rows=self.visible[index.section][@"rows"];return index.row<rows.count?rows[index.row]:nil;}
- (NSInteger)numberOfSectionsInTableView:(UITableView *)tableView {return [self showsSkeleton]?1:self.visible.count;}
- (NSInteger)tableView:(UITableView *)tableView numberOfRowsInSection:(NSInteger)section{return [self showsSkeleton]?MAX(5,MIN(14,(NSInteger)ceil((self.tableView.bounds.size.height-self.tableView.adjustedContentInset.top-self.tableView.adjustedContentInset.bottom)/(self.compactRows?48.:62.)))):[self.visible[section][@"rows"] count];}
- (NSString *)tableView:(UITableView *)tableView titleForHeaderInSection:(NSInteger)section {return [self showsSkeleton]?nil:self.visible[section][@"title"];}
- (NSString *)tableView:(UITableView *)tableView titleForFooterInSection:(NSInteger)section {return [self showsSkeleton]?nil:self.visible[section][@"footer"];}
- (UITableViewCell *)tableView:(UITableView *)tableView cellForRowAtIndexPath:(NSIndexPath *)indexPath {
    if([self showsSkeleton])return FRSkeletonCell(self.compactRows?@"compactRow":@"row");
    UITableViewCell *c=[tableView dequeueReusableCellWithIdentifier:@"row"];if(!c){c=[[UITableViewCell alloc] initWithStyle:UITableViewCellStyleSubtitle reuseIdentifier:@"row"];[c.contentView.heightAnchor constraintGreaterThanOrEqualToConstant:44].active=YES;}NSDictionary *row=[self rowAt:indexPath];
    BOOL value=self.compactRows||row[@"value"];
    UIListContentConfiguration *content=value?UIListContentConfiguration.valueCellConfiguration:UIListContentConfiguration.subtitleCellConfiguration;content.text=row[@"title"];
    content.textProperties.font=[UIFont preferredFontForTextStyle:UIFontTextStyleBody];content.textProperties.numberOfLines=value?1:2;
    content.secondaryText=row[@"value"]?:(self.compactRows&&row[@"action"]?nil:([row[@"detail"] length]?row[@"detail"]:nil));
    content.secondaryTextProperties.numberOfLines=value?1:2;content.secondaryTextProperties.font=[UIFont preferredFontForTextStyle:value?UIFontTextStyleBody:UIFontTextStyleSubheadline];content.secondaryTextProperties.color=UIColor.secondaryLabelColor;content.textToSecondaryTextVerticalPadding=3;
    if([row[@"destructive"] boolValue])content.textProperties.color=UIColor.systemRedColor;
    NSString *symbol=row[@"icon"]?:self.defaultIcon;
    if([row[@"thumbnail"] isKindOfClass:UIImage.class]){content.image=FRRoundedThumbnail(row[@"thumbnail"]);content.imageProperties.reservedLayoutSize=CGSizeMake(44,44);content.imageProperties.maximumSize=CGSizeMake(44,44);content.imageToTextPadding=12;}
    else if(row[@"iconColor"]&&symbol){content.image=FRTileIcon(symbol,row[@"iconColor"]);content.imageProperties.reservedLayoutSize=CGSizeMake(30,30);content.imageProperties.maximumSize=CGSizeMake(30,30);content.imageToTextPadding=14;}
    else if(symbol&&![row[@"destructive"] boolValue]){content.image=FRIcon(symbol,17);content.imageProperties.tintColor=row[@"tint"]?:UIColor.secondaryLabelColor;content.imageProperties.reservedLayoutSize=CGSizeMake(24,24);content.imageProperties.maximumSize=CGSizeMake(22,22);content.imageToTextPadding=12;}
    else content.image=nil;
    content.directionalLayoutMargins=NSDirectionalEdgeInsetsMake(11,16,11,16);c.contentConfiguration=content;c.backgroundColor=tableView.style==UITableViewStylePlain?FRPaper():UIColor.secondarySystemGroupedBackgroundColor;c.tintColor=UIColor.labelColor;
    c.accessibilityHint=self.compactRows?row[@"detail"]:nil;c.accessoryView=nil;c.accessoryType=row[@"action"]&&![row[@"destructive"] boolValue]?UITableViewCellAccessoryDisclosureIndicator:UITableViewCellAccessoryNone;
    if(row[@"checked"]){UIImageView *check=[[UIImageView alloc] initWithImage:FRIcon([row[@"checked"] boolValue]?@"checkmark.circle.fill":@"circle",20)];check.tintColor=[row[@"checked"] boolValue]?FRAccent():UIColor.tertiaryLabelColor;c.accessoryView=check;c.accessoryType=UITableViewCellAccessoryNone;}
    if([row[@"busy"] boolValue]||[self.busyTitle isEqual:row[@"title"]]){UIActivityIndicatorView *spinner=[[UIActivityIndicatorView alloc] initWithActivityIndicatorStyle:UIActivityIndicatorViewStyleMedium];[spinner startAnimating];c.accessoryView=spinner;c.accessoryType=UITableViewCellAccessoryNone;}
    if(row[@"status"]){UILabel *status=[UILabel new];status.text=row[@"status"];status.font=[UIFont preferredFontForTextStyle:UIFontTextStyleFootnote];NSString *code=row[@"statusCode"];status.textColor=[code isEqual:@"failed"]?FRAmber():([code isEqual:@"running"]?FRAccent():([code isEqual:@"done"]?FROnline():FRInkTertiary()));if([code isEqual:@"time"])status.font=[[UIFontMetrics metricsForTextStyle:UIFontTextStyleFootnote] scaledFontForFont:[UIFont monospacedDigitSystemFontOfSize:12 weight:UIFontWeightRegular]];status.adjustsFontForContentSizeCategory=YES;[status sizeToFit];c.accessoryView=status;c.accessoryType=UITableViewCellAccessoryNone;c.accessibilityValue=row[@"status"];}
    c.selectionStyle=row[@"action"]||row[@"id"]?UITableViewCellSelectionStyleDefault:UITableViewCellSelectionStyleNone;if(!row[@"status"])c.accessibilityValue=row[@"checked"]?([row[@"checked"] boolValue]?@"已选":@"未选"):nil;
    if(row[@"delete"])c.accessibilityCustomActions=@[[[UIAccessibilityCustomAction alloc] initWithName:row[@"deleteTitle"]?:@"删除" actionHandler:^BOOL(UIAccessibilityCustomAction *a){((void(^)(void))row[@"delete"])();return YES;}]];else c.accessibilityCustomActions=nil;
    return c;
}
- (void)tableView:(UITableView *)tableView didSelectRowAtIndexPath:(NSIndexPath *)indexPath {
    if(tableView.editing){[self updateToolbar];return;}
    [tableView deselectRowAtIndexPath:indexPath animated:YES];if([self showsSkeleton])return;NSDictionary *row=[self rowAt:indexPath];
    if(!row||[self.busyTitle isEqual:row[@"title"]])return;void(^action)(void)=row[@"action"];if(action)action();
}
- (void)tableView:(UITableView *)tableView didDeselectRowAtIndexPath:(NSIndexPath *)indexPath {if(tableView.editing)[self updateToolbar];}
- (BOOL)tableView:(UITableView *)tableView canEditRowAtIndexPath:(NSIndexPath *)indexPath {
    if([self showsSkeleton])return NO;NSDictionary *row=[self rowAt:indexPath];
    return tableView.editing?row[@"id"]!=nil:(row[@"delete"]||[row[@"swipe"] count]||[row[@"leading"] count]);
}
- (UIContextualAction *)contextual:(NSDictionary *)swipe destructive:(BOOL)destructive {
    void(^action)(void)=swipe[@"action"];
    UIContextualAction *item=[UIContextualAction contextualActionWithStyle:destructive?UIContextualActionStyleDestructive:UIContextualActionStyleNormal title:swipe[@"title"] handler:^(UIContextualAction *a,UIView *source,void(^completion)(BOOL)){completion(YES);dispatch_async(dispatch_get_main_queue(),^{action();});}];
    if(swipe[@"icon"])item.image=[UIImage systemImageNamed:swipe[@"icon"]];item.backgroundColor=swipe[@"color"]?:(destructive?UIColor.systemRedColor:UIColor.systemGrayColor);return item;
}
- (UISwipeActionsConfiguration *)tableView:(UITableView *)tableView trailingSwipeActionsConfigurationForRowAtIndexPath:(NSIndexPath *)indexPath {
    NSDictionary *row=[self rowAt:indexPath];if(!row||tableView.editing)return nil;NSMutableArray *actions=[NSMutableArray array];
    if(row[@"delete"])[actions addObject:[self contextual:FRSwipe(row[@"deleteTitle"]?:@"删除",@"trash",nil,row[@"delete"]) destructive:YES]];
    for(NSDictionary *swipe in row[@"swipe"])[actions addObject:[self contextual:swipe destructive:NO]];
    if(!actions.count)return nil;UISwipeActionsConfiguration *config=[UISwipeActionsConfiguration configurationWithActions:actions];config.performsFirstActionWithFullSwipe=[row[@"fullSwipe"] boolValue];return config;
}
- (UISwipeActionsConfiguration *)tableView:(UITableView *)tableView leadingSwipeActionsConfigurationForRowAtIndexPath:(NSIndexPath *)indexPath {
    NSDictionary *row=[self rowAt:indexPath];if(!row||tableView.editing||![row[@"leading"] count])return nil;NSMutableArray *actions=[NSMutableArray array];
    for(NSDictionary *swipe in row[@"leading"])[actions addObject:[self contextual:swipe destructive:NO]];
    UISwipeActionsConfiguration *config=[UISwipeActionsConfiguration configurationWithActions:actions];config.performsFirstActionWithFullSwipe=YES;return config;
}
- (UIContextMenuConfiguration *)tableView:(UITableView *)tableView contextMenuConfigurationForRowAtIndexPath:(NSIndexPath *)indexPath point:(CGPoint)point {
    NSDictionary *row=[self rowAt:indexPath];if(!row||tableView.editing||[self showsSkeleton])return nil;
    NSMutableArray *items=[NSMutableArray arrayWithArray:row[@"menu"]?:@[]];
    if(row[@"delete"]){void(^remove)(void)=row[@"delete"];UIAction *action=[UIAction actionWithTitle:row[@"deleteTitle"]?:@"删除" image:[UIImage systemImageNamed:@"trash"] identifier:nil handler:^(UIAction *a){remove();}];action.attributes=UIMenuElementAttributesDestructive;[items addObject:[UIMenu menuWithTitle:@"" image:nil identifier:nil options:UIMenuOptionsDisplayInline children:@[action]]];}
    if(!items.count)return nil;
    return [UIContextMenuConfiguration configurationWithIdentifier:nil previewProvider:nil actionProvider:^UIMenu *(NSArray *suggested){return [UIMenu menuWithChildren:items];}];
}
#pragma mark Choosing several rows
- (UIBarButtonItem *)selectButton {
    if(!self.chooseItem){self.chooseItem=[[UIBarButtonItem alloc] initWithTitle:@"选择" style:UIBarButtonItemStylePlain target:self action:@selector(toggleSelecting)];self.chooseItem.accessibilityIdentifier=@"list.select";}
    return self.chooseItem;
}
- (void)toggleSelecting {if(self.editing)[self endSelecting];else{[self setEditing:YES animated:YES];self.chooseItem.title=@"完成";self.chooseItem.style=UIBarButtonItemStyleDone;[self updateToolbar];[self.navigationController setToolbarHidden:NO animated:YES];}}
- (void)endSelecting {[self setEditing:NO animated:YES];self.chooseItem.title=@"选择";self.chooseItem.style=UIBarButtonItemStylePlain;[self.navigationController setToolbarHidden:YES animated:YES];}
- (NSArray<NSDictionary *> *)selectedRows {NSMutableArray *rows=[NSMutableArray array];for(NSIndexPath *index in self.tableView.indexPathsForSelectedRows){NSDictionary *row=[self rowAt:index];if(row[@"id"])[rows addObject:row];}return rows;}
- (void)updateToolbar {
    if(!self.editing){self.toolbarItems=nil;return;}
    NSArray *selected=[self selectedRows];NSMutableArray *items=[NSMutableArray array];__weak typeof(self) weak=self;
    for(NSDictionary *batch in self.batchActions){
        NSString *title=selected.count?[NSString stringWithFormat:@"%@（%lu）",batch[@"title"],(unsigned long)selected.count]:batch[@"title"];
        void(^run)(NSArray *)=batch[@"action"];
        UIBarButtonItem *item=[[UIBarButtonItem alloc] initWithPrimaryAction:[UIAction actionWithTitle:title image:nil identifier:nil handler:^(UIAction *a){NSArray *rows=[weak selectedRows];if(!rows.count)return;[weak endSelecting];run(rows);}]];
        item.enabled=selected.count>0;if([batch[@"destructive"] boolValue])item.tintColor=UIColor.systemRedColor;
        if(items.count)[items addObject:[UIBarButtonItem flexibleSpaceItem]];[items addObject:item];
    }
    if(items.count==1)[items insertObject:[UIBarButtonItem flexibleSpaceItem] atIndex:0];
    self.toolbarItems=items;
}
#pragma mark Preview
- (void)presentPreview:(NSURL *)file {if(!file||self.presentedViewController)return;self.preview=file;QLPreviewController *preview=[QLPreviewController new];preview.dataSource=self;[self presentViewController:preview animated:YES completion:nil];}
- (NSInteger)numberOfPreviewItemsInPreviewController:(QLPreviewController *)controller{return self.preview?1:0;}
- (id<QLPreviewItem>)previewController:(QLPreviewController *)controller previewItemAtIndex:(NSInteger)index{return self.preview;}
@end

@interface FRChatController : UIViewController <UITableViewDataSource,UITableViewDelegate,UITextViewDelegate,UIDocumentPickerDelegate,QLPreviewControllerDataSource>
@property(nonatomic) NSString *sid,*messageRevision;
@property(nonatomic) NSTimer *draftTimer;
@property(nonatomic) NSArray *rawMessages;
@property(nonatomic) NSDictionary *acceptedMessage;
@property(nonatomic) BOOL restoredPosition;
@property(nonatomic) CGFloat savedPosition;
@property(nonatomic) UITableView *table;
@property(nonatomic) UITextView *input;
@property(nonatomic) UILabel *placeholder;
@property(nonatomic) UIButton *send,*latestButton;
@property(nonatomic) CGFloat measuredInputWidth;
@property(nonatomic) UIButton *callBanner;
@property(nonatomic) NSLayoutConstraint *inputHeight,*bannerHeight;
@property(nonatomic) NSArray *jobs,*approvals;
@property(nonatomic) NSData *stamp;
@property(nonatomic) NSMutableDictionary *mediaFiles;
@property(nonatomic) NSMutableSet *loadingMedia;
@property(nonatomic) NSMutableDictionary *mediaErrors,*players;
@property(nonatomic) NSURL *preview;
@property(nonatomic) BOOL loading;
@property(nonatomic) NSError *loadError;
@property(nonatomic) UIButton *attachmentButton;
@property(nonatomic) UIButton *managementButton;
@property(nonatomic) NSMutableSet *reviewedApprovals;
@property(nonatomic) NSMutableSet *expandedRows;
@property(nonatomic) NSString *presentedApprovalToken;
@property(nonatomic,weak) FRPermissionController *permissionSheet;
@property(nonatomic) NSLayoutConstraint *titleWidth;
// Composer: invited agents, thinking, suggestions.
@property(nonatomic) NSMutableArray<NSString *> *agents;
@property(nonatomic) NSArray<NSDictionary *> *agentCatalog;
@property(nonatomic) BOOL thinking,mentionTyped,focusOnAppear;
@property(nonatomic) UIButton *thinkingButton,*agentButton;
@property(nonatomic) UIStackView *chips;
@property(nonatomic) FRSuggestionBar *suggestionBar;
@property(nonatomic) NSDictionary *suggestion;
@property(nonatomic) NSMutableSet *dismissedSuggestions;
@property(nonatomic) NSTimer *suggestTimer;
// Streaming: live state per running job, merged with what polling returns.
@property(nonatomic) FRStream *stream;
@property(nonatomic) NSMutableDictionary<NSString *,NSMutableDictionary *> *live;
@property(nonatomic) NSMutableSet *liveDirty;
@property(nonatomic) BOOL liveScheduled;
- (NSArray<UIMenuElement *> *)conversationMenu;
- (NSArray<UIMenuElement *> *)turnMenu:(NSDictionary *)job;
@end
@interface FRSessionsController : FRListController
@property(nonatomic) BOOL showArchived;
@property(nonatomic) CGFloat headerWidth;
@property(nonatomic) UIButton *macButton;
@end
@interface FRActivityController : FRListController
@property(nonatomic) NSString *scheduleSummary;
@property(nonatomic) CGFloat headerWidth;
@end
@interface FRMacController : FRListController
@property(nonatomic) NSString *pushStatus;
@property(nonatomic) CGFloat headerWidth;
@end
@interface FRTrashController : FRListController
@end
static void FRSessionSettings(UIViewController *vc,NSDictionary *session);
static void FRFiles(UIViewController *vc,NSString *sid);
static void FRVoiceSheet(UIViewController *vc,NSDictionary *session);
static UITabBarController *FRCurrentTabs;
static void FRFocusOpenSession(void);
static void FRCreateSession(UIViewController *vc,void(^done)(void));

static NSString *FRPendingSession;

#pragma mark - Session actions

static void FRReplaceSession(NSString *sid,NSDictionary *(^change)(NSDictionary *session)){
    NSMutableArray *sessions=[NSMutableArray array];
    for(NSDictionary *s in FRStore.shared.sessions){if(![s[@"id"] isEqual:sid]){[sessions addObject:s];continue;}NSDictionary *updated=change(s);if(updated)[sessions addObject:updated];}
    FRStore.shared.sessions=sessions;[[NSNotificationCenter defaultCenter] postNotificationName:FRDataChanged object:nil];
}
// PATCH needs the current revision; read it first so a stale list never conflicts.
static void FRPatchSession(UIViewController *vc,NSString *sid,NSDictionary *change,void(^done)(NSDictionary *session)){
    NSString *path=[FRAPI.shared pathForSession:sid suffix:@""];
    [FRAPI.shared get:path completion:^(NSDictionary *current,NSError *error){
        if(error){FRShowError(vc,error);return;}
        NSMutableDictionary *body=[change mutableCopy];body[@"revision"]=current[@"revision"]?:@0;
        [FRAPI.shared call:path method:@"PATCH" body:body completion:^(NSDictionary *updated,NSError *e){
            if(e){FRShowError(vc,e);return;}
            if([updated isKindOfClass:NSDictionary.class])FRReplaceSession(sid,^NSDictionary *(NSDictionary *s){NSMutableDictionary *merged=[s mutableCopy];[merged addEntriesFromDictionary:updated];return merged;});
            [FRStore.shared refresh];if(done)done(updated);
        }];
    }];
}
static void FRRenameSession(UIViewController *vc,NSDictionary *session,void(^done)(void)){
    NSString *sid=session[@"id"];FRPrompt(vc,@"重命名对话",session[@"name"],^(NSString *name){FRPatchSession(vc,sid,@{@"name":name},^(NSDictionary *s){if(done)done();});});
}
static void FRArchiveSession(UIViewController *vc,NSDictionary *session,BOOL archived){
    NSString *sid=session[@"id"];FRReplaceSession(sid,^NSDictionary *(NSDictionary *s){NSMutableDictionary *m=[s mutableCopy];m[@"archived"]=@(archived);return m;});
    FRPatchSession(vc,sid,@{@"archived":@(archived)},^(NSDictionary *s){
        if(archived)FRToast(vc,@"已归档，可在「我的 › 已归档对话」找到",@"撤销",^{FRPatchSession(vc,sid,@{@"archived":@NO},nil);});
    });
}
static void FRCallEach(NSArray<NSString *> *paths,NSString *method,void(^done)(NSArray<NSString *> *succeeded,NSError *error)){
    dispatch_group_t group=dispatch_group_create();NSMutableArray *ok=[NSMutableArray array];__block NSError *failure=nil;
    for(NSString *path in paths){dispatch_group_enter(group);[FRAPI.shared call:path method:method body:[method isEqual:@"POST"]?@{}:nil completion:^(id v,NSError *e){if(e)failure=e;else[ok addObject:path];dispatch_group_leave(group);}];}
    dispatch_group_notify(group,dispatch_get_main_queue(),^{done(ok,failure);});
}
void FRRestoreSessions(UIViewController *vc,NSArray<NSString *> *ids,void(^done)(void)){
    NSMutableArray *paths=[NSMutableArray array];for(NSString *sid in ids)[paths addObject:[NSString stringWithFormat:@"/v1/trash/%@/restore",FRSegment(sid)]];
    FRCallEach(paths,@"POST",^(NSArray *ok,NSError *error){if(error)FRShowError(vc,error);[FRStore.shared unhide:ids];if(done)done();});
}
// Deleted conversations wait 30 days in 「最近删除」, so the toast offers undo instead of asking first.
// They leave every list at once; the Mac stops their work in the background.
void FRDeleteSessions(UIViewController *vc,NSArray<NSString *> *ids,void(^done)(void)){
    if(!ids.count)return;NSMutableArray *paths=[NSMutableArray array];
    for(NSString *sid in ids){[paths addObject:[FRAPI.shared pathForSession:sid suffix:@""]];[FRAPI.shared.cache remove:[@"chat:" stringByAppendingString:sid]];}
    [FRStore.shared hide:ids];
    UIViewController *anchor=vc.navigationController.viewControllers.firstObject?:vc;
    FRCallEach(paths,@"DELETE",^(NSArray *ok,NSError *error){
        NSMutableArray *restorable=[NSMutableArray array],*failed=[NSMutableArray array];
        for(NSString *sid in ids)[[ok containsObject:[FRAPI.shared pathForSession:sid suffix:@""]]?restorable:failed addObject:sid];
        if(error){FRShowError(anchor,error);[FRStore.shared unhide:failed];}
        if(ok.count)FRToast(anchor,ok.count==1?@"已移到「最近删除」":[NSString stringWithFormat:@"已将 %lu 个对话移到「最近删除」",(unsigned long)ok.count],@"撤销",^{FRRestoreSessions(anchor,restorable,nil);});
        if(done)done();
    });
}
static NSArray<UIMenuElement *> *FRSessionMenu(UIViewController *vc,NSDictionary *session){
    BOOL archived=[session[@"archived"] boolValue];
    UIAction *rename=[UIAction actionWithTitle:@"重命名" image:[UIImage systemImageNamed:@"pencil"] identifier:nil handler:^(UIAction *a){FRRenameSession(vc,session,nil);}];
    UIAction *archive=[UIAction actionWithTitle:archived?@"取消归档":@"归档" image:[UIImage systemImageNamed:archived?@"tray.and.arrow.up":@"archivebox"] identifier:nil handler:^(UIAction *a){FRArchiveSession(vc,session,!archived);}];
    return @[rename,archive];
}
static NSMutableDictionary *FRSessionRow(UIViewController *vc,NSDictionary *s){
    NSString *sid=s[@"id"];NSDictionary *last=[s[@"last"] isKindOfClass:NSDictionary.class]?s[@"last"]:nil;NSString *detail=last?FRString(last[@"title"]):[s[@"skill_names"] componentsJoinedByString:@" · "];
    NSMutableDictionary *row=FRMutableRow(s[@"name"],detail.length?detail:@"新的对话",^{FROpenSession(sid);});row[@"id"]=sid;
    if([s[@"waiting"] integerValue]){row[@"status"]=@"待确认";row[@"statusCode"]=@"failed";row[@"icon"]=@"exclamationmark.shield";row[@"tint"]=FRAmber();}
    else if([s[@"active"] integerValue]){row[@"status"]=@"进行中";row[@"statusCode"]=@"running";row[@"icon"]=@"ellipsis.bubble";row[@"tint"]=FRAccent();}
    else if(FRSessionActivity(s)>0){row[@"status"]=FRRelativeTime(FRSessionActivity(s));row[@"statusCode"]=@"time";}
    __weak UIViewController *weak=vc;BOOL archived=[s[@"archived"] boolValue];
    row[@"delete"]=^{FRDeleteSessions(weak,@[sid],nil);};
    row[@"swipe"]=@[FRSwipe(archived?@"取消归档":@"归档",archived?@"tray.and.arrow.up":@"archivebox",UIColor.systemIndigoColor,^{FRArchiveSession(weak,s,!archived);})];
    row[@"menu"]=FRSessionMenu(vc,s);return row;
}

#pragma mark - UI 2.0 surfaces

@interface FRTapView : UIControl
@property(nonatomic,copy) void(^tap)(void);
@end
@implementation FRTapView
- (instancetype)initWithFrame:(CGRect)frame {if((self=[super initWithFrame:frame])){[self addTarget:self action:@selector(fire) forControlEvents:UIControlEventTouchUpInside];self.isAccessibilityElement=YES;self.accessibilityTraits=UIAccessibilityTraitButton;}return self;}
- (void)fire {if(self.tap)self.tap();}
- (void)setHighlighted:(BOOL)highlighted {[super setHighlighted:highlighted];self.alpha=highlighted?.55:1;}
@end
static UILabel *FRText(NSString *text,UIFont *font,UIColor *color,NSInteger lines){UILabel *l=[UILabel new];l.text=text;l.font=font;l.textColor=color;l.numberOfLines=lines;l.adjustsFontForContentSizeCategory=YES;return l;}
static UIStackView *FRStack(NSArray *views,UILayoutConstraintAxis axis,CGFloat spacing){UIStackView *s=[[UIStackView alloc] initWithArrangedSubviews:views];s.axis=axis;s.spacing=spacing;return s;}
static UIView *FRHug(UIView *view){[view setContentHuggingPriority:UILayoutPriorityRequired forAxis:UILayoutConstraintAxisHorizontal];[view setContentCompressionResistancePriority:UILayoutPriorityRequired forAxis:UILayoutConstraintAxisHorizontal];return view;}
// A card that runs action on tap; its contents never take the touch.
static FRTapView *FRCard(UIView *content,UIColor *fill,UIColor *border,NSString *label,void(^action)(void)){
    FRTapView *card=[FRTapView new];card.backgroundColor=fill;card.layer.cornerRadius=16;card.layer.cornerCurve=kCACornerCurveContinuous;if(border)FRSetBorder(card,border,1);card.tap=action;card.accessibilityLabel=label;
    content.translatesAutoresizingMaskIntoConstraints=NO;content.userInteractionEnabled=NO;[card addSubview:content];
    [NSLayoutConstraint activateConstraints:@[[content.leadingAnchor constraintEqualToAnchor:card.leadingAnchor constant:14],[content.trailingAnchor constraintEqualToAnchor:card.trailingAnchor constant:-14],[content.topAnchor constraintEqualToAnchor:card.topAnchor constant:14],[content.bottomAnchor constraintEqualToAnchor:card.bottomAnchor constant:-14]]];
    return card;
}
static UIView *FRDot(UIColor *color,CGFloat size,BOOL hollow){UIView *d=[UIView new];d.translatesAutoresizingMaskIntoConstraints=NO;d.layer.cornerRadius=size/2;if(hollow)FRSetBorder(d,color,1.5);else d.backgroundColor=color;[d.widthAnchor constraintEqualToConstant:size].active=YES;[d.heightAnchor constraintEqualToConstant:size].active=YES;return FRHug(d);}
static UIProgressView *FRProgress(CGFloat value,UIColor *tint,UIColor *track,CGFloat height){UIProgressView *p=[[UIProgressView alloc] initWithProgressViewStyle:UIProgressViewStyleBar];p.progress=value;p.progressTintColor=tint;p.trackTintColor=track;p.layer.cornerRadius=height/2;p.clipsToBounds=YES;[p.heightAnchor constraintEqualToConstant:height].active=YES;p.isAccessibilityElement=NO;return p;}
static UIImageView *FRGlyphTile(NSString *symbol,UIColor *tint,UIColor *fill,CGFloat size){UIImageView *v=[[UIImageView alloc] initWithImage:FRIcon(symbol,size*.45)];v.tintColor=tint;v.contentMode=UIViewContentModeCenter;v.backgroundColor=fill;v.layer.cornerRadius=size*.28;v.layer.cornerCurve=kCACornerCurveContinuous;v.translatesAutoresizingMaskIntoConstraints=NO;[v.widthAnchor constraintEqualToConstant:size].active=YES;[v.heightAnchor constraintEqualToConstant:size].active=YES;return (UIImageView *)FRHug(v);}
// Wraps a vertical stack with the screen's 16pt gutters, for table headers.
static UIView *FRGutter(UIView *content,CGFloat top,CGFloat bottom){UIView *box=[UIView new];content.translatesAutoresizingMaskIntoConstraints=NO;[box addSubview:content];[NSLayoutConstraint activateConstraints:@[[content.leadingAnchor constraintEqualToAnchor:box.layoutMarginsGuide.leadingAnchor],[content.trailingAnchor constraintEqualToAnchor:box.layoutMarginsGuide.trailingAnchor],[content.topAnchor constraintEqualToAnchor:box.topAnchor constant:top],[content.bottomAnchor constraintEqualToAnchor:box.bottomAnchor constant:-bottom]]];box.preservesSuperviewLayoutMargins=YES;return box;}
// Sizes a header for the table's width; nil removes it.
static void FRSetTableHeader(UITableView *table,UIView *header){
    CGFloat width=table.bounds.size.width;if(!header||width<=0){table.tableHeaderView=nil;return;}
    header.frame=CGRectMake(0,0,width,100);[header layoutIfNeeded];
    CGSize size=[header systemLayoutSizeFittingSize:CGSizeMake(width,0) withHorizontalFittingPriority:UILayoutPriorityRequired verticalFittingPriority:UILayoutPriorityFittingSizeLevel];
    header.frame=CGRectMake(0,0,width,ceil(size.height));table.tableHeaderView=header;
}

static NSArray *FRHomeList(NSString *key){NSArray *v=FRStore.shared.home[key];return [v isKindOfClass:NSArray.class]?v:@[];}
static NSDictionary *FRHomeNow(void){NSDictionary *v=FRStore.shared.home[@"now"];return [v isKindOfClass:NSDictionary.class]?v:nil;}
static NSString *FRPercent(NSDictionary *item){return [NSString stringWithFormat:@"%ld%%",(long)lround([item[@"progress"] doubleValue]*100)];}
// What waits on the user: the Mac's summary, or approvals found in the task list on an older Mac.
static NSArray<NSDictionary *> *FRAttention(void){
    if(FRStore.shared.home)return FRHomeList(@"attention");
    NSMutableArray *items=[NSMutableArray array];
    for(NSDictionary *j in FRStore.shared.tasks)if([j[@"status"] isEqual:@"waiting_approval"])[items addObject:@{@"id":j[@"id"]?:@"",@"session_id":j[@"session_id"]?:@"",@"session_name":[FRStore.shared session:j[@"session_id"]][@"name"]?:@"",@"title":FRString(j[@"title"]),@"reason":@"approval"}];
    return items;
}
static NSString *FRAttentionTitle(NSDictionary *item){NSString *reason=item[@"reason"];return [reason isEqual:@"approval"]?@"等你确认":([reason isEqual:@"paused"]?@"已暂停 · 说继续就接着做":@"需要你补充");}
static NSUInteger FRWorkingCount(void){return ([FRHomeNow()[@"mine"] boolValue]?1:0)+FRHomeList(@"queue").count+FRHomeList(@"active").count;}
static void FRSelectTab(NSUInteger index){if(index<FRCurrentTabs.viewControllers.count){FRCurrentTabs.selectedIndex=index;[(UINavigationController *)FRCurrentTabs.selectedViewController popToRootViewControllerAnimated:NO];}}
static void FRResumeSession(UIViewController *vc,NSDictionary *item){
    NSString *sid=FRString(item[@"session_id"]);if(!sid.length)return;
    FRAskConfirm(vc,@"让 Friday 继续？",[NSString stringWithFormat:@"会在「%@」里发送“继续”，接着上次停下的地方做。",FRString(item[@"session_name"]).length?item[@"session_name"]:@"对话"],^{
        [FRStore.shared sendText:@"继续" session:sid completion:^(id v,NSError *e){if(e)FRShowError(vc,e);else FRToast(vc,@"已让 Friday 继续",@"查看",^{FROpenSession(sid);});}];
    });
}

static NSString *FRLoadedName(NSString *kind){return @{@"chat":@"对话模型",@"image":@"图片模型",@"video":@"视频模型"}[kind?:@""]?:@"未加载";}
static void FRShowMac(UIViewController *vc);
static void FRUpdateMacButton(UIButton *button){
    BOOL offline=FRStore.shared.sessionsError!=nil;NSDictionary *now=FRHomeNow();
    NSString *title=offline?@"等待连接 Mac":(now?(@{@"video":@"Mac · 渲染中",@"image":@"Mac · 生图中"}[now[@"kind"]]?:@"Mac · 忙碌"):@"Mac 在线");
    UIButtonConfiguration *c=button.configuration;c.title=title;
    c.image=[[UIImage systemImageNamed:@"circle.fill" withConfiguration:[UIImageSymbolConfiguration configurationWithPointSize:7]] imageWithTintColor:offline?FRAmber():FROnline() renderingMode:UIImageRenderingModeAlwaysOriginal];
    button.configuration=c;button.accessibilityLabel=[NSString stringWithFormat:@"这台 Mac，%@",title];
}
static UIButton *FRMacButton(UIViewController *vc){
    UIButton *b=[UIButton buttonWithType:UIButtonTypeSystem];UIButtonConfiguration *c=UIButtonConfiguration.filledButtonConfiguration;
    // iOS 26 bar items already sit in a glass capsule.
    if(@available(iOS 26.0,*))c=UIButtonConfiguration.plainButtonConfiguration;
    c.cornerStyle=UIButtonConfigurationCornerStyleCapsule;c.baseBackgroundColor=FRSurface();c.baseForegroundColor=FRInk();c.imagePadding=6;c.contentInsets=NSDirectionalEdgeInsetsMake(7,10,7,12);
    c.titleTextAttributesTransformer=^NSDictionary *(NSDictionary *a){NSMutableDictionary *m=[a mutableCopy];m[NSFontAttributeName]=[UIFont systemFontOfSize:13 weight:UIFontWeightMedium];return m;};
    b.configuration=c;b.accessibilityIdentifier=@"home.mac";__weak UIViewController *weak=vc;
    [b addAction:[UIAction actionWithHandler:^(UIAction *a){FRShowMac(weak);}] forControlEvents:UIControlEventTouchUpInside];FRUpdateMacButton(b);return b;
}

// Home: the one thing waiting on the user, then what the Mac is working on.
static UIView *FRHomeHeader(void){
    UIStackView *stack=FRStack(@[],UILayoutConstraintAxisVertical,10);
    NSArray *attention=FRAttention();
    if(attention.count){
        NSDictionary *first=attention.firstObject;NSString *sid=FRString(first[@"session_id"]);
        NSString *title=attention.count>1?[NSString stringWithFormat:@"%@ · 共 %lu 件",FRAttentionTitle(first),(unsigned long)attention.count]:FRAttentionTitle(first);
        NSString *detail=[NSString stringWithFormat:@"「%@」%@",FRString(first[@"session_name"]).length?first[@"session_name"]:@"对话",FRString(first[@"title"])];
        UIView *icon=FRGlyphTile([first[@"reason"] isEqual:@"approval"]?@"exclamationmark.shield":@"pause.circle",FRAmber(),FRPaper(),36);
        UIImageView *chevron=[[UIImageView alloc] initWithImage:FRIcon(@"chevron.right",13)];chevron.tintColor=FRAmber();FRHug(chevron);
        UIStackView *row=FRStack(@[icon,FRStack(@[FRText(title,FRFont(14,UIFontWeightSemibold),FRInk(),0),FRText(detail,FRFont(13,UIFontWeightRegular),FRAmber(),1)],UILayoutConstraintAxisVertical,2),chevron],UILayoutConstraintAxisHorizontal,12);row.alignment=UIStackViewAlignmentCenter;
        FRTapView *card=FRCard(row,FRAmberSoft(),nil,[NSString stringWithFormat:@"%@，%@",title,detail],^{if(attention.count>1)FRSelectTab(1);else FROpenSession(sid);});card.accessibilityIdentifier=@"home.attention";
        [stack addArrangedSubview:card];
    }
    NSDictionary *now=FRHomeNow();NSArray *queue=FRHomeList(@"queue"),*active=FRHomeList(@"active");NSUInteger count=FRWorkingCount();
    if(count){
        UIStackView *content=FRStack(@[FRStack(@[FRText([NSString stringWithFormat:@"正在 Mac 上进行 · %lu",(unsigned long)count],FRFont(13,UIFontWeightSemibold),FRInkSecondary(),1),FRHug(FRText(@"全部",FRFont(13,UIFontWeightMedium),FRAccent(),1))],UILayoutConstraintAxisHorizontal,8)],UILayoutConstraintAxisVertical,10);
        NSMutableArray *spoken=[NSMutableArray array];NSUInteger shown=0;
        if([now[@"mine"] boolValue]){
            UIStackView *line=FRStack(@[FRDot(FRAccent(),6,NO),FRText(FRString(now[@"title"]),FRFont(14,UIFontWeightMedium),FRInk(),1),FRHug(FRText(FRPercent(now),FRMono(12),FRInkSecondary(),1))],UILayoutConstraintAxisHorizontal,10);line.alignment=UIStackViewAlignmentCenter;
            [content addArrangedSubview:line];[content addArrangedSubview:FRProgress([now[@"progress"] floatValue],FRAccent(),FRAccentSoft(),4)];shown++;
            [spoken addObject:[NSString stringWithFormat:@"%@，大约完成 %@",now[@"title"],FRPercent(now)]];
        }
        for(NSDictionary *item in active){if(shown>=3)break;UIStackView *line=FRStack(@[FRDot(FRAccent(),6,![item[@"status"] isEqual:@"running"]),FRText(FRString(item[@"title"]),FRFont(13,UIFontWeightRegular),FRInkSecondary(),1),FRHug(FRText([item[@"status"] isEqual:@"running"]?@"进行中":@"排队中",FRFont(12,UIFontWeightRegular),FRInkSecondary(),1))],UILayoutConstraintAxisHorizontal,10);line.alignment=UIStackViewAlignmentCenter;[content addArrangedSubview:line];[spoken addObject:FRString(item[@"title"])];shown++;}
        for(NSDictionary *item in queue){if(shown>=3)break;NSString *wait=[NSString stringWithFormat:@"第 %@ 位 · %@",item[@"position"],FREstimate([item[@"eta_seconds"] doubleValue])];UIStackView *line=FRStack(@[FRDot(FRInkTertiary(),6,YES),FRText(FRString(item[@"title"]),FRFont(13,UIFontWeightRegular),FRInkSecondary(),1),FRHug(FRText(wait,FRMono(12),FRInkSecondary(),1))],UILayoutConstraintAxisHorizontal,10);line.alignment=UIStackViewAlignmentCenter;[content addArrangedSubview:line];[spoken addObject:[NSString stringWithFormat:@"%@，排队%@",item[@"title"],wait]];shown++;}
        FRTapView *card=FRCard(content,FRPaper(),FRHairline(),[NSString stringWithFormat:@"正在 Mac 上进行 %lu 项。%@",(unsigned long)count,[spoken componentsJoinedByString:@"。"]],^{FRSelectTab(1);});card.accessibilityIdentifier=@"home.working";
        [stack addArrangedSubview:card];
    }
    return stack.arrangedSubviews.count?FRGutter(stack,4,14):nil;
}

#pragma mark - Conversations tab

@implementation FRSessionsController
- (instancetype)init {return [self initWithStyle:UITableViewStylePlain];}
- (void)pullRefresh {[FRStore.shared refresh];}
- (void)viewDidLayoutSubviews {[super viewDidLayoutSubviews];if(!self.showArchived&&fabs(self.tableView.bounds.size.width-self.headerWidth)>.5)[self updateHeader];}
// Home sits above the conversations: what waits on you, what the Mac is doing, and the 进行中 badge.
- (void)updateHeader {
    self.headerWidth=self.tableView.bounds.size.width;FRSetTableHeader(self.tableView,self.editing?nil:FRHomeHeader());if(self.macButton)FRUpdateMacButton(self.macButton);
    NSUInteger badge=FRWorkingCount()+FRAttention().count;if(FRCurrentTabs.viewControllers.count>1)FRCurrentTabs.viewControllers[1].tabBarItem.badgeColor=FRAccent();FRCurrentTabs.viewControllers[1].tabBarItem.badgeValue=badge?[NSString stringWithFormat:@"%lu",(unsigned long)badge]:nil;
}
- (void)viewDidLoad {
    if(!self.showArchived)self.navigationItem.largeTitleDisplayMode=UINavigationItemLargeTitleDisplayModeAlways;
    [super viewDidLoad];self.navigationItem.title=self.showArchived?@"已归档对话":@"Friday";self.searchable=YES;
    self.emptyTitle=self.showArchived?@"没有归档的对话":@"开始第一段对话";self.emptyDetail=self.showArchived?@"在对话列表左滑即可归档，归档不会停止正在执行的任务。":@"点右上角新建对话，直接说出你想完成的事。";
    __weak typeof(self) weak=self;
    self.batchActions=@[@{@"title":self.showArchived?@"取消归档":@"归档",@"action":^(NSArray *rows){for(NSDictionary *row in rows){NSDictionary *s=[FRStore.shared session:row[@"id"]];if(s)FRArchiveSession(weak,s,!weak.showArchived);}}},
                        @{@"title":@"删除",@"destructive":@YES,@"action":^(NSArray *rows){FRDeleteSessions(weak,[rows valueForKey:@"id"],nil);}}];
    UIBarButtonItem *compose=[[UIBarButtonItem alloc] initWithImage:[UIImage systemImageNamed:@"square.and.pencil"] style:UIBarButtonItemStylePlain target:self action:@selector(create)];compose.accessibilityLabel=@"新建对话";compose.accessibilityIdentifier=@"sessions.new";
    if(self.showArchived)self.navigationItem.rightBarButtonItem=[self selectButton];
    else{self.macButton=FRMacButton(self);self.navigationItem.leftBarButtonItem=[[UIBarButtonItem alloc] initWithCustomView:self.macButton];self.navigationItem.rightBarButtonItems=@[compose,[self selectButton]];}
    [[NSNotificationCenter defaultCenter] addObserver:self selector:@selector(reloadData) name:FRDataChanged object:nil];
}
// No title first: the conversation opens ready to type, and the Mac names it later.
- (void)create {UIBarButtonItem *item=self.navigationItem.rightBarButtonItems.firstObject;if(!item.enabled||self.showArchived)return;item.enabled=NO;FRCreateSession(self,^{item.enabled=YES;});}
- (void)reloadData {
    if(self.editing){[super updateState];return;}
    NSArray *sessions=[FRStore.shared.sessions sortedArrayWithOptions:NSSortStable usingComparator:^NSComparisonResult(NSDictionary *a,NSDictionary *b){return [@(FRSessionActivity(b)) compare:@(FRSessionActivity(a))];}];
    NSMutableArray *sections=[NSMutableArray array];NSMutableDictionary *byGroup=[NSMutableDictionary dictionary];
    for(NSDictionary *s in sessions){
        if([s[@"archived"] boolValue]!=self.showArchived)continue;
        // Anything that needs the user comes first, whatever its age.
        NSString *group=self.showArchived?@"":([s[@"waiting"] integerValue]?@"需要你确认":FRRecencyGroup(FRSessionActivity(s)));
        NSMutableArray *rows=byGroup[group];if(!rows){rows=[NSMutableArray array];byGroup[group]=rows;NSMutableDictionary *section=[@{@"rows":rows} mutableCopy];if(group.length)section[@"title"]=group;if([group isEqual:@"需要你确认"])[sections insertObject:section atIndex:0];else[sections addObject:section];}
        [rows addObject:FRSessionRow(self,s)];
    }
    [self replaceSections:sections];self.loaded=FRStore.shared.sessionsLoaded;self.fetching=FRStore.shared.loading;self.loadError=FRStore.shared.sessionsError;[super updateState];
    if(!self.showArchived)[self updateHeader];
    if(FRPendingSession&&[FRStore.shared session:FRPendingSession]){NSString *sid=FRPendingSession;FRPendingSession=nil;FROpenSession(sid);}
}
- (void)dealloc {[[NSNotificationCenter defaultCenter] removeObserver:self];}
@end
static void FRCreateSession(UIViewController *vc,void(^done)(void)){
    void(^opened)(NSDictionary *,NSError *)=^(NSDictionary *v,NSError *e){if(done)done();if(e){FRShowError(vc,e);return;}NSMutableArray *sessions=[FRStore.shared.sessions mutableCopy];if([v[@"id"] isKindOfClass:NSString.class]&&![FRStore.shared session:v[@"id"]])[sessions insertObject:v atIndex:0];FRStore.shared.sessions=sessions;[FRStore.shared refresh];FROpenSession(v[@"id"]);FRFocusOpenSession();};
    // A Mac service from before automatic titles requires a name: fall back to the placeholder.
    [FRAPI.shared call:@"/v1/sessions" method:@"POST" body:@{} completion:^(NSDictionary *v,NSError *e){if(e.code==400)[FRAPI.shared call:@"/v1/sessions" method:@"POST" body:@{@"name":@"新对话"} completion:opened];else opened(v,e);}];
}

#pragma mark - Recently deleted

@implementation FRTrashController
- (void)viewDidLoad {
    [super viewDidLoad];self.title=@"最近删除";self.defaultIcon=@"trash";self.emptyTitle=@"没有最近删除的内容";self.emptyDetail=@"删除的对话和短剧项目会在这里保留 30 天。";
    __weak typeof(self) weak=self;
    UIBarButtonItem *clear=[[UIBarButtonItem alloc] initWithTitle:@"全部清空" style:UIBarButtonItemStylePlain target:self action:@selector(clearAll)];clear.tintColor=UIColor.systemRedColor;
    self.navigationItem.rightBarButtonItems=@[clear,[self selectButton]];
    self.batchActions=@[@{@"title":@"恢复",@"action":^(NSArray *rows){FRRestoreSessions(weak,[rows valueForKey:@"id"],^{[weak reloadData];});}},
                        @{@"title":@"永久删除",@"destructive":@YES,@"action":^(NSArray *rows){[weak purge:[rows valueForKey:@"id"]];}}];
    self.reloadAction=^{[FRAPI.shared get:@"/v1/trash" completion:^(NSDictionary *v,NSError *e){
        if(e){if(e.code==404)e=[NSError errorWithDomain:@"Friday" code:404 userInfo:@{NSLocalizedDescriptionKey:@"Mac 上的 Friday 需要更新后才能使用「最近删除」。"}];FRShowError(weak,e);return;}
        NSMutableArray *rows=[NSMutableArray array];NSTimeInterval now=NSDate.date.timeIntervalSince1970;
        for(NSDictionary *item in v[@"items"]){
            NSString *sid=item[@"id"];NSInteger days=MAX(1,(NSInteger)ceil(([item[@"expires"] doubleValue]-now)/86400));BOOL drama=[item[@"kind"] isEqual:@"drama"];
            NSMutableDictionary *row=FRMutableRow(drama?[@"短剧 · " stringByAppendingString:FRString(item[@"name"])]:FRString(item[@"name"]),[NSString stringWithFormat:@"%@删除 · %ld 天后永久删除",FRRelativeTime([item[@"deleted"] doubleValue]),(long)days],^{[weak choose:item];});
            row[@"id"]=sid;row[@"icon"]=drama?@"film":@"bubble.left";row[@"deleteTitle"]=@"永久删除";row[@"delete"]=^{[weak purge:@[sid]];};
            row[@"leading"]=@[FRSwipe(@"恢复",@"arrow.uturn.backward",UIColor.systemGreenColor,^{FRRestoreSessions(weak,@[sid],^{[weak reloadData];FRToast(weak,@"已恢复",nil,nil);});})];
            [rows addObject:row];
        }
        weak.sections=rows.count?@[@{@"rows":rows,@"footer":[NSString stringWithFormat:@"删除的内容保留 %@ 天，到期后连同消息和文件一起永久删除。",v[@"days"]?:@30]}]:@[];
        weak.navigationItem.rightBarButtonItems.firstObject.enabled=rows.count>0;
    }];};
}
- (void)choose:(NSDictionary *)item {
    __weak typeof(self) weak=self;NSString *sid=item[@"id"];
    UIAlertController *sheet=[UIAlertController alertControllerWithTitle:FRString(item[@"name"]) message:nil preferredStyle:UIAlertControllerStyleActionSheet];
    [sheet addAction:[UIAlertAction actionWithTitle:@"恢复" style:UIAlertActionStyleDefault handler:^(UIAlertAction *a){FRRestoreSessions(weak,@[sid],^{[weak reloadData];FRToast(weak,@"已恢复",nil,nil);});}]];
    [sheet addAction:[UIAlertAction actionWithTitle:@"永久删除" style:UIAlertActionStyleDestructive handler:^(UIAlertAction *a){[weak purge:@[sid]];}]];
    [sheet addAction:[UIAlertAction actionWithTitle:@"取消" style:UIAlertActionStyleCancel handler:nil]];[self presentViewController:sheet animated:YES completion:nil];
}
- (void)purge:(NSArray *)ids {
    __weak typeof(self) weak=self;
    FRAskDestructive(self,ids.count==1?@"永久删除？":[NSString stringWithFormat:@"永久删除 %lu 项？",(unsigned long)ids.count],@"消息、文件和生成的作品会一起删除，无法恢复。",@"永久删除",^{
        NSMutableArray *paths=[NSMutableArray array];for(NSString *sid in ids)[paths addObject:[@"/v1/trash/" stringByAppendingString:FRSegment(sid)]];[weak hideRows:ids];
        FRCallEach(paths,@"DELETE",^(NSArray *ok,NSError *error){if(error){FRShowError(weak,error);[weak unhideRows:ids];}[weak reloadData];});
    });
}
- (void)clearAll {
    if(!self.rows.count)return;__weak typeof(self) weak=self;
    FRAskDestructive(self,@"清空「最近删除」？",@"这里的所有内容都会永久删除，无法恢复。",@"全部永久删除",^{NSArray *ids=[weak.rows valueForKey:@"id"];[weak hideRows:ids];[FRAPI.shared call:@"/v1/trash" method:@"DELETE" body:nil completion:^(id v,NSError *e){if(e){FRShowError(weak,e);[weak unhideRows:ids];}[weak reloadData];}];});
}
@end

#pragma mark - Tasks tab

static BOOL FRTaskNeedsUser(NSDictionary *j){return [j[@"status"] isEqual:@"waiting_approval"]||(!FRActive(j)&&[@[@"needs_input",@"blocked"] containsObject:j[@"outcome"]?:@""]);}
static void FRDeleteTasks(UIViewController *vc,NSArray<NSDictionary *> *tasks,void(^done)(void)){
    NSString *title=tasks.count==1?@"删除这条任务记录？":[NSString stringWithFormat:@"删除 %lu 条任务记录？",(unsigned long)tasks.count];
    FRAskDestructive(vc,title,@"对话里的这轮内容和生成的文件会一起删除，Friday 之后也不会再参考它。无法恢复。",@"删除",^{
        NSMutableArray *paths=[NSMutableArray array],*ids=[NSMutableArray array];
        for(NSDictionary *j in tasks){[paths addObject:[FRAPI.shared pathForSession:j[@"session_id"] suffix:[@"/tasks/" stringByAppendingString:FRSegment(j[@"id"])]]];[ids addObject:FRString(j[@"id"])];}
        [FRStore.shared hide:ids];if(done)done();
        FRCallEach(paths,@"DELETE",^(NSArray *ok,NSError *error){
            if(!error)return;FRShowError(vc,error);NSMutableArray *failed=[NSMutableArray array];
            for(NSUInteger i=0;i<ids.count;i++)if(![ok containsObject:paths[i]])[failed addObject:ids[i]];[FRStore.shared unhide:failed];
        });
    });
}
static void FRStopTask(UIViewController *vc,NSDictionary *j){
    FRAskDestructive(vc,@"停止这项任务？",@"已经完成的内容会保留。",@"停止",^{[FRAPI.shared call:[FRAPI.shared pathForSession:j[@"session_id"] suffix:[NSString stringWithFormat:@"/tasks/%@/cancel",FRSegment(j[@"id"])]] method:@"POST" body:@{} completion:^(id v,NSError *e){if(e)FRShowError(vc,e);[FRStore.shared refresh];}];});
}
// 进行中: the GPU's current render, everyone's queue as positions, what waits on the user, what comes next.
static UIButton *FRInverseButton(NSString *title,void(^action)(void)){
    UIButton *b=[UIButton buttonWithType:UIButtonTypeSystem];UIButtonConfiguration *c=UIButtonConfiguration.filledButtonConfiguration;c.title=title;c.cornerStyle=UIButtonConfigurationCornerStyleMedium;c.baseBackgroundColor=[FRInverse() colorWithAlphaComponent:.12];c.baseForegroundColor=FRInverse();
    c.titleTextAttributesTransformer=^NSDictionary *(NSDictionary *a){NSMutableDictionary *m=[a mutableCopy];m[NSFontAttributeName]=FRFont(14,UIFontWeightSemibold);return m;};
    b.configuration=c;[b.heightAnchor constraintGreaterThanOrEqualToConstant:44].active=YES;[b addAction:[UIAction actionWithHandler:^(UIAction *a){action();}] forControlEvents:UIControlEventTouchUpInside];return b;
}
static UIView *FRNowCard(UIViewController *vc){
    NSDictionary *now=FRHomeNow();if(!now)return nil;BOOL mine=[now[@"mine"] boolValue];UIColor *ink=FRInverse(),*quiet=[FRInverse() colorWithAlphaComponent:.68];
    NSInteger elapsed=[now[@"elapsed"] integerValue];
    UIStackView *head=FRStack(@[FRDot(FRAccentSoft(),7,NO),FRText(@"GPU 正在做",FRFont(12,UIFontWeightSemibold),quiet,1),[UIView new],FRHug(FRText([NSString stringWithFormat:@"已用 %ld:%02ld",(long)elapsed/60,(long)elapsed%60],FRMono(12),quiet,1))],UILayoutConstraintAxisHorizontal,6);head.alignment=UIStackViewAlignmentCenter;
    NSString *detail=mine?FRString(now[@"session_name"]):@"做完后会依次开始你排队的任务";
    UIStackView *title=FRStack(@[FRGlyphTile([now[@"kind"] isEqual:@"video"]?@"film":@"photo",quiet,[FRInverse() colorWithAlphaComponent:.1],48),FRStack(@[FRText(FRString(now[@"title"]),FRFont(16,UIFontWeightSemibold),ink,2),FRText(detail,FRFont(13,UIFontWeightRegular),quiet,1)],UILayoutConstraintAxisVertical,3)],UILayoutConstraintAxisHorizontal,12);title.alignment=UIStackViewAlignmentCenter;
    UIStackView *progress=FRStack(@[FRProgress([now[@"progress"] floatValue],FRAccentSoft(),[FRInverse() colorWithAlphaComponent:.14],6),FRHug(FRText([NSString stringWithFormat:@"%@ · %@",FRPercent(now),FREstimate([now[@"eta_seconds"] doubleValue])],FRMono(12),ink,1))],UILayoutConstraintAxisHorizontal,10);progress.alignment=UIStackViewAlignmentCenter;
    UIStackView *stack=FRStack(@[head,title,progress],UILayoutConstraintAxisVertical,12);
    if(mine){NSString *sid=FRString(now[@"session_id"]);__weak UIViewController *weak=vc;
        UIStackView *buttons=FRStack(@[FRInverseButton(@"打开对话",^{FROpenSession(sid);}),FRInverseButton(@"停止",^{FRStopTask(weak,now);})],UILayoutConstraintAxisHorizontal,8);buttons.distribution=UIStackViewDistributionFillEqually;[stack addArrangedSubview:buttons];}
    UIView *card=[UIView new];card.backgroundColor=FRInk();card.layer.cornerRadius=18;card.layer.cornerCurve=kCACornerCurveContinuous;card.accessibilityIdentifier=@"activity.now";
    stack.translatesAutoresizingMaskIntoConstraints=NO;[card addSubview:stack];[NSLayoutConstraint activateConstraints:@[[stack.leadingAnchor constraintEqualToAnchor:card.leadingAnchor constant:16],[stack.trailingAnchor constraintEqualToAnchor:card.trailingAnchor constant:-16],[stack.topAnchor constraintEqualToAnchor:card.topAnchor constant:16],[stack.bottomAnchor constraintEqualToAnchor:card.bottomAnchor constant:-16]]];
    return FRGutter(card,8,8);
}
@implementation FRActivityController
- (instancetype)init {return [self initWithStyle:UITableViewStylePlain];}
- (void)pullRefresh {[FRStore.shared refresh];[self loadSchedules];}
- (void)viewDidLoad {
    self.navigationItem.largeTitleDisplayMode=UINavigationItemLargeTitleDisplayModeAlways;
    [super viewDidLoad];self.title=@"进行中";self.emptyTitle=@"Mac 现在空闲";self.emptyDetail=@"在对话里交代的事会出现在这里，离开 App 也会在 Mac 上继续。";
    __weak typeof(self) weak=self;self.navigationItem.rightBarButtonItem=[self selectButton];
    self.batchActions=@[@{@"title":@"删除",@"destructive":@YES,@"action":^(NSArray *rows){FRDeleteTasks(weak,[rows valueForKey:@"task"],nil);}}];
    [[NSNotificationCenter defaultCenter] addObserver:self selector:@selector(reloadData) name:FRDataChanged object:nil];
}
- (void)viewWillAppear:(BOOL)animated {[super viewWillAppear:animated];[self loadSchedules];}
- (void)viewDidLayoutSubviews {[super viewDidLayoutSubviews];if(fabs(self.tableView.bounds.size.width-self.headerWidth)>.5){self.headerWidth=self.tableView.bounds.size.width;FRSetTableHeader(self.tableView,FRNowCard(self));}}
- (void)loadSchedules {
    [FRAPI.shared get:@"/v1/schedules" completion:^(NSDictionary *v,NSError *e){
        if(e)return;NSUInteger on=0,total=[v[@"items"] count];for(NSDictionary *item in v[@"items"])on+=[item[@"enabled"] boolValue];
        self.scheduleSummary=total?[NSString stringWithFormat:@"%lu 个已开启 · 共 %lu 个",(unsigned long)on,(unsigned long)total]:@"在对话里说“每天早上 8 点……”即可创建";[self reloadData];
    }];
}
- (NSMutableDictionary *)rowForHomeItem:(NSDictionary *)item status:(NSString *)status code:(NSString *)code icon:(NSString *)icon tint:(UIColor *)tint {
    NSString *sid=FRString(item[@"session_id"]);NSMutableDictionary *row=FRMutableRow(FRString(item[@"title"]),FRString(item[@"session_name"]),^{FROpenSession(sid);});
    row[@"status"]=status;row[@"statusCode"]=code;row[@"icon"]=icon;row[@"tint"]=tint;return row;
}
- (void)reloadData {
    if(self.editing){[super updateState];return;}
    __weak typeof(self) weak=self;NSDictionary *home=FRStore.shared.home;
    NSMutableArray *queue=[NSMutableArray array],*attention=[NSMutableArray array],*running=[NSMutableArray array],*ended=[NSMutableArray array];
    for(NSDictionary *item in FRHomeList(@"queue")){
        NSMutableDictionary *row=[self rowForHomeItem:item status:[NSString stringWithFormat:@"第 %@ 位 · %@",item[@"position"],FREstimate([item[@"eta_seconds"] doubleValue])] code:@"time" icon:@"clock" tint:FRInkTertiary()];
        row[@"swipe"]=@[FRSwipe(@"停止",@"stop.circle",FRAmber(),^{FRStopTask(weak,item);})];[queue addObject:row];
    }
    for(NSDictionary *item in FRAttention()){
        BOOL paused=[item[@"reason"] isEqual:@"paused"];
        NSMutableDictionary *row=[self rowForHomeItem:item status:paused?@"继续":([item[@"reason"] isEqual:@"approval"]?@"去确认":@"去补充") code:@"failed" icon:paused?@"pause.circle":@"exclamationmark.shield" tint:FRAmber()];
        if(paused){row[@"action"]=^{FRResumeSession(weak,item);};row[@"menu"]=@[[UIAction actionWithTitle:@"打开对话" image:[UIImage systemImageNamed:@"bubble.left"] identifier:nil handler:^(UIAction *a){FROpenSession(item[@"session_id"]);}]];}
        [attention addObject:row];
    }
    for(NSDictionary *item in FRHomeList(@"active")){
        NSMutableDictionary *row=[self rowForHomeItem:item status:[item[@"status"] isEqual:@"running"]?@"进行中":@"排队中" code:@"running" icon:@"ellipsis.bubble" tint:FRAccent()];
        row[@"swipe"]=@[FRSwipe(@"停止",@"stop.circle",FRAmber(),^{FRStopTask(weak,item);})];[running addObject:row];
    }
    for(NSDictionary *j in FRStore.shared.tasks){
        if(j[@"parent_job_id"]&&j[@"parent_job_id"]!=NSNull.null)continue;
        if([j[@"outcome"] isEqual:@"continued"])continue;
        NSString *sid=j[@"session_id"],*name=[FRStore.shared session:sid][@"name"]?:@"对话";
        BOOL active=FRActive(j),needs=FRTaskNeedsUser(j);
        // The Mac's summary already lists active and waiting work; an older Mac relies on the task list.
        if(home&&(active||needs))continue;
        NSMutableDictionary *row=FRMutableRow(FRString(j[@"title"]).length?FRString(j[@"title"]):FRString(j[@"prompt"]),[NSString stringWithFormat:@"%@ · %@",name,FRTaskTime(j,NSDate.date.timeIntervalSince1970)],^{FROpenSession(sid);});
        row[@"status"]=FRStatus(j);row[@"statusCode"]=needs||[j[@"status"] isEqual:@"failed"]?@"failed":(active?@"running":@"time");row[@"task"]=j;
        UIAction *open=[UIAction actionWithTitle:@"打开对话" image:[UIImage systemImageNamed:@"bubble.left"] identifier:nil handler:^(UIAction *a){FROpenSession(sid);}];
        if(active){row[@"icon"]=@"ellipsis.bubble";row[@"tint"]=FRAccent();row[@"swipe"]=@[FRSwipe(@"停止",@"stop.circle",FRAmber(),^{FRStopTask(weak,j);})];
            UIAction *stop=[UIAction actionWithTitle:@"停止任务" image:[UIImage systemImageNamed:@"stop.circle"] identifier:nil handler:^(UIAction *a){FRStopTask(weak,j);}];stop.attributes=UIMenuElementAttributesDestructive;row[@"menu"]=@[open,stop];}
        else {row[@"id"]=j[@"id"];row[@"delete"]=^{FRDeleteTasks(weak,@[j],nil);};row[@"menu"]=@[open];}
        if(home&&ended.count>=40)continue;
        [needs?attention:(active?running:ended) addObject:row];
    }
    NSMutableArray *sections=[NSMutableArray array];
    if(attention.count)[sections addObject:@{@"title":@"需要你",@"rows":attention}];
    if(queue.count)[sections addObject:@{@"title":[NSString stringWithFormat:@"排队 · %lu",(unsigned long)queue.count],@"rows":queue,@"footer":@"一次生成一个，时间按最近几次估算"}];
    if(running.count)[sections addObject:@{@"title":@"正在回复",@"rows":running}];
    NSDictionary *next=[home[@"next_schedule"] isKindOfClass:NSDictionary.class]?home[@"next_schedule"]:nil;
    NSMutableDictionary *schedules=FRMutableRow(@"定时任务",next?[NSString stringWithFormat:@"下一个：%@ · %@",FRString(next[@"title"]),FRString(next[@"when"])]:(self.scheduleSummary?:@"到点自动执行，可暂停或删除"),^{FRShowSchedules(weak);});schedules[@"icon"]=@"alarm";schedules[@"tint"]=FRInkSecondary();
    [sections addObject:@{@"title":@"接下来",@"rows":@[schedules]}];
    if(ended.count)[sections addObject:@{@"title":@"最近结束",@"rows":ended,@"footer":@"左滑可删除已结束的任务记录。"}];
    [self replaceSections:sections];self.loaded=FRStore.shared.tasksLoaded;self.fetching=FRStore.shared.loading;self.loadError=FRStore.shared.tasksError;[super updateState];
    if(!self.editing){self.headerWidth=self.tableView.bounds.size.width;FRSetTableHeader(self.tableView,FRNowCard(self));}
}
- (void)dealloc {[[NSNotificationCenter defaultCenter] removeObserver:self];}
@end

#pragma mark - Me tab

static void FRPresentBrowser(UIViewController *vc,NSString *string,BOOL google){NSURL *url=[NSURL URLWithString:string];NSURL *origin=FRAPI.shared.origin;BOOL same=[url.scheme isEqual:origin.scheme]&&[url.host isEqual:origin.host]&&[url.port?:@0 isEqual:origin.port?:@0];if(!url.user&&!url.password&&[url.scheme isEqual:@"https"]&&(same||(google&&[url.host isEqual:@"accounts.google.com"]))) [vc presentViewController:[[SFSafariViewController alloc] initWithURL:url] animated:YES completion:nil];}
static void FRConnections(UIViewController *vc){FRListController *list=[FRListController new];list.title=@"连接的账号";list.defaultIcon=@"link";list.emptyTitle=@"还没有连接账号";__weak FRListController *weak=list;list.reloadAction=^{[FRAPI.shared get:@"/v1/connectors" completion:^(NSDictionary *v,NSError *e){if(e){FRShowError(weak,e);return;}NSMutableArray *accounts=[NSMutableArray array];
    for(NSDictionary *c in v[@"items"]){NSMutableDictionary *row=FRMutableRow(c[@"display_name"],c[@"status"],nil);row[@"icon"]=@"person.crop.circle";row[@"deleteTitle"]=@"断开";row[@"delete"]=^{FRAskDestructive(weak,@"断开此账号？",@"所有会话都将失去此连接的访问权限。",@"断开",^{[FRAPI.shared call:[@"/v1/connectors/" stringByAppendingString:FRSegment(c[@"id"])] method:@"DELETE" body:nil completion:^(id v,NSError *e){if(e)FRShowError(weak,e);else[weak reloadData];}];});};[accounts addObject:row];}
    NSMutableArray *sections=[NSMutableArray array];if(accounts.count)[sections addObject:@{@"title":@"已连接",@"rows":accounts,@"footer":@"左滑可断开连接。"}];
    [sections addObject:@{@"rows":@[FRRow(@"连接 Google Drive",@"仅访问你选择或由助手创建的文件",^{[FRAPI.shared call:@"/v1/connectors/google/authorize" method:@"POST" body:@{} completion:^(NSDictionary *v,NSError *e){if(e)FRShowError(weak,e);else FRPresentBrowser(weak,v[@"auth_url"],YES);}];})]}];
    weak.sections=sections;}];};[vc.navigationController pushViewController:list animated:YES];}
static void FRDevices(UIViewController *vc){FRListController *list=[FRListController new];list.title=@"登录设备";list.defaultIcon=@"iphone";__weak FRListController *w=list;list.reloadAction=^{[FRAPI.shared get:@"/v1/devices" completion:^(NSDictionary *v,NSError *e){if(e){FRShowError(w,e);return;}NSMutableArray *rows=[NSMutableArray array];
    for(NSDictionary *d in v[@"items"]){if([d[@"revoked"] boolValue])continue;BOOL current=[d[@"id"] isEqual:FRAPI.shared.credentials[@"pairing"][@"device_id"]];NSMutableDictionary *row=FRMutableRow(d[@"device_name"],current?@"本机":@"",nil);row[@"deleteTitle"]=@"撤销";
        row[@"delete"]=^{FRAskDestructive(w,@"撤销这台设备？",current?@"本机会立即退出登录。":@"该设备需要重新登录才能继续访问。",@"撤销",^{[FRAPI.shared call:[@"/v1/devices/" stringByAppendingString:FRSegment(d[@"id"])] method:@"DELETE" body:nil completion:^(id v,NSError *e){if(e)FRShowError(w,e);else if(current)[FRAPI.shared signOut];else[w reloadData];}];});};[rows addObject:row];}
    w.sections=@[@{@"rows":rows,@"footer":@"左滑可撤销设备的访问权限。"}];}];};[vc.navigationController pushViewController:list animated:YES];}
static void FRSkillLibrary(UIViewController *vc){FRListController *list=[FRListController new];list.title=@"技能库";list.defaultIcon=@"square.stack.3d.up";__weak FRListController *w=list;list.reloadAction=^{[FRAPI.shared get:@"/v1/skills" completion:^(NSDictionary *v,NSError *e){if(e){FRShowError(w,e);return;}NSMutableArray *rows=[NSMutableArray array];for(NSDictionary *skill in v[@"items"])[rows addObject:FRRow(skill[@"name"],skill[@"description"],nil)];w.sections=@[@{@"rows":rows,@"footer":@"技能在每个对话的设置里单独开启。"}];}];};[vc.navigationController pushViewController:list animated:YES];}
static void FRChooseAppearance(UIViewController *vc,void(^done)(void)){
    UIAlertController *sheet=[UIAlertController alertControllerWithTitle:@"外观" message:nil preferredStyle:UIAlertControllerStyleActionSheet];
    [FRAppearanceNames() enumerateObjectsUsingBlock:^(NSString *name,NSUInteger index,BOOL *stop){
        [sheet addAction:[UIAlertAction actionWithTitle:index==(NSUInteger)FRAppearanceChoice()?[name stringByAppendingString:@" ✓"]:name style:UIAlertActionStyleDefault handler:^(UIAlertAction *a){[NSUserDefaults.standardUserDefaults setInteger:index forKey:FRAppearanceKey];FRApplyAppearance(vc.view.window);if(done)done();}]];
    }];
    [sheet addAction:[UIAlertAction actionWithTitle:@"取消" style:UIAlertActionStyleCancel handler:nil]];[vc presentViewController:sheet animated:YES completion:nil];
}
// Mac 与我: the Mac's state on top, then what Friday knows, connections, and the app itself.
static UIView *FRMacCard(void){
    NSDictionary *mac=[FRStore.shared.home[@"mac"] isKindOfClass:NSDictionary.class]?FRStore.shared.home[@"mac"]:nil;BOOL offline=FRStore.shared.sessionsError!=nil;
    UIView *icon=FRGlyphTile(@"laptopcomputer",FRInverse(),FRInk(),48);
    NSString *state=offline?@"等待连接":(FRHomeNow()?@"在线 · 正在生成":@"在线 · 空闲");
    UIStackView *status=FRStack(@[FRDot(offline?FRAmber():FROnline(),7,NO),FRText(state,FRFont(13,UIFontWeightMedium),offline?FRAmber():FROnline(),1)],UILayoutConstraintAxisHorizontal,6);status.alignment=UIStackViewAlignmentCenter;
    UIStackView *name=FRStack(@[FRText(FRAPI.shared.origin.host?:@"这台 Mac",FRFont(17,UIFontWeightBold),FRInk(),1),status],UILayoutConstraintAxisVertical,3);name.alignment=UIStackViewAlignmentLeading;
    UIStackView *top=FRStack(@[icon,name],UILayoutConstraintAxisHorizontal,12);top.alignment=UIStackViewAlignmentCenter;
    UIStackView *stack=FRStack(@[top],UILayoutConstraintAxisVertical,14);
    if(mac){
        UIView *(^stat)(NSString *,NSString *,BOOL)=^UIView *(NSString *label,NSString *value,BOOL mono){UIStackView *s=FRStack(@[FRText(label,FRFont(11,UIFontWeightSemibold),FRInkSecondary(),1),FRText(value,mono?FRMono(14):FRFont(13,UIFontWeightSemibold),FRInk(),1)],UILayoutConstraintAxisVertical,4);UIView *box=[UIView new];box.backgroundColor=FRSurface();box.layer.cornerRadius=12;s.translatesAutoresizingMaskIntoConstraints=NO;[box addSubview:s];[NSLayoutConstraint activateConstraints:@[[s.leadingAnchor constraintEqualToAnchor:box.leadingAnchor constant:10],[s.trailingAnchor constraintEqualToAnchor:box.trailingAnchor constant:-10],[s.topAnchor constraintEqualToAnchor:box.topAnchor constant:10],[s.bottomAnchor constraintEqualToAnchor:box.bottomAnchor constant:-10]]];box.isAccessibilityElement=YES;box.accessibilityLabel=[NSString stringWithFormat:@"%@：%@",label,value];return box;};
        UIStackView *stats=FRStack(@[stat(@"已加载",FRLoadedName(mac[@"loaded"]),NO),stat(@"排队",[NSString stringWithFormat:@"%@",mac[@"queued"]?:@0],YES),stat(@"今天完成",[NSString stringWithFormat:@"%@",mac[@"done_today"]?:@0],YES)],UILayoutConstraintAxisHorizontal,8);stats.distribution=UIStackViewDistributionFillEqually;
        [stack addArrangedSubview:stats];
    }
    UIView *card=[UIView new];card.backgroundColor=UIColor.secondarySystemGroupedBackgroundColor;card.layer.cornerRadius=20;card.layer.cornerCurve=kCACornerCurveContinuous;card.accessibilityIdentifier=@"mac.card";
    stack.translatesAutoresizingMaskIntoConstraints=NO;[card addSubview:stack];[NSLayoutConstraint activateConstraints:@[[stack.leadingAnchor constraintEqualToAnchor:card.leadingAnchor constant:16],[stack.trailingAnchor constraintEqualToAnchor:card.trailingAnchor constant:-16],[stack.topAnchor constraintEqualToAnchor:card.topAnchor constant:16],[stack.bottomAnchor constraintEqualToAnchor:card.bottomAnchor constant:-16]]];
    return FRGutter(card,8,4);
}
@implementation FRMacController
- (void)viewDidLoad {[super viewDidLoad];self.title=@"Mac 与我";[[NSNotificationCenter defaultCenter] addObserver:self selector:@selector(reloadData) name:FRDataChanged object:nil];}
- (void)dealloc {[[NSNotificationCenter defaultCenter] removeObserver:self];}
- (void)close {[self dismissViewControllerAnimated:YES completion:nil];}
- (void)viewDidLayoutSubviews {[super viewDidLayoutSubviews];if(fabs(self.tableView.bounds.size.width-self.headerWidth)>.5){self.headerWidth=self.tableView.bounds.size.width;FRSetTableHeader(self.tableView,FRMacCard());}}
- (void)viewWillAppear:(BOOL)animated {[super viewWillAppear:animated];if(!FRPushAvailable()){self.pushStatus=@"本地测试版未启用";[self reloadData];return;}[FRAPI.shared get:@"/v1/notifications/status" completion:^(NSDictionary *v,NSError *e){if(e)self.pushStatus=@"等待连接 Mac";else if(![v[@"server_configured"] boolValue])self.pushStatus=@"Mac 未配置";else if(![v[@"device_registered"] boolValue])self.pushStatus=@"未开启";else self.pushStatus=@"已开启";[self reloadData];}];}
- (void)reloadData {
    __weak typeof(self) weak=self;
    NSDictionary *account=FRAPI.shared.credentials[@"pairing"][@"account"];NSString *name=FRString(account[@"display_name"]).length?FRString(account[@"display_name"]):@"Friday";
    NSMutableDictionary *(^item)(NSString *,NSString *,NSString *,void(^)(void))=^NSMutableDictionary *(NSString *title,NSString *symbol,NSString *value,void(^action)(void)){NSMutableDictionary *row=FRMutableRow(title,@"",action);row[@"icon"]=symbol;row[@"tint"]=FRInk();if(value)row[@"value"]=value;return row;};
    NSMutableDictionary *notifications=item(@"通知",@"bell",self.pushStatus?:@"",^{if(!FRPushAvailable()){FRShowError(weak,[NSError errorWithDomain:@"Friday" code:0 userInfo:@{NSLocalizedDescriptionKey:@"当前为本地测试版。开发者会员生效后，安装支持推送的 Friday 版本即可接收锁屏通知。"}]);return;}[[UNUserNotificationCenter currentNotificationCenter] requestAuthorizationWithOptions:UNAuthorizationOptionAlert|UNAuthorizationOptionSound|UNAuthorizationOptionBadge completionHandler:^(BOOL granted,NSError *e){dispatch_async(dispatch_get_main_queue(),^{if(granted)[UIApplication.sharedApplication registerForRemoteNotifications];else if(e)FRShowError(weak,e);else[UIApplication.sharedApplication openURL:[NSURL URLWithString:UIApplicationOpenSettingsURLString] options:@{} completionHandler:nil];});}];});
    NSMutableDictionary *profile=FRMutableRow(name,[NSString stringWithFormat:@"%@%@",FRAPI.shared.origin.host?:@"Mac",[account[@"is_admin"] boolValue]?@" · 所有者":@""],nil);profile[@"icon"]=@"person.crop.circle";profile[@"tint"]=FRInk();
    NSMutableDictionary *signOut=FRMutableRow(@"退出登录",@"",^{FRAskDestructive(weak,@"退出 Friday？",@"本机凭据和临时预览将被清除，Mac 上的任务会继续。",@"退出登录",^{NSString *did=FRAPI.shared.credentials[@"pairing"][@"device_id"];[FRVoice.shared stop];[FRAPI.shared call:[@"/v1/devices/" stringByAppendingString:FRSegment(did)] method:@"DELETE" body:nil completion:^(id v,NSError *e){[FRAPI.shared signOut];}];});});signOut[@"destructive"]=@YES;
    NSString *version=[NSString stringWithFormat:@"Friday %@（%@）",[NSBundle.mainBundle objectForInfoDictionaryKey:@"CFBundleShortVersionString"]?:@"",[NSBundle.mainBundle objectForInfoDictionaryKey:@"CFBundleVersion"]?:@""];
    self.sections=@[
      @{@"title":@"Friday 知道的",@"rows":@[item(@"记忆",@"brain",nil,^{FRShowMemory(weak);}),item(@"技能库",@"square.stack.3d.up",nil,^{FRSkillLibrary(weak);}),item(@"定时任务",@"alarm",nil,^{FRShowSchedules(weak);})]},
      @{@"title":@"连接与设备",@"rows":@[item(@"连接的账号",@"link",nil,^{FRConnections(weak);}),item(@"登录设备",@"iphone",nil,^{FRDevices(weak);}),notifications]},
      @{@"title":@"App",@"rows":@[item(@"外观",@"circle.lefthalf.filled",FRAppearanceNames()[FRAppearanceChoice()],^{FRChooseAppearance(weak,^{[weak reloadData];});}),item(@"已归档对话",@"archivebox",nil,^{FRSessionsController *archived=[FRSessionsController new];archived.showArchived=YES;[weak.navigationController pushViewController:archived animated:YES];}),item(@"最近删除",@"trash",nil,^{[weak.navigationController pushViewController:[FRTrashController new] animated:YES];})]},
      @{@"title":@"账号",@"rows":@[profile,signOut],@"footer":version}];
    self.headerWidth=self.tableView.bounds.size.width;FRSetTableHeader(self.tableView,FRMacCard());
}
@end
static void FRShowMac(UIViewController *vc){
    if(!vc)return;FRMacController *mac=[FRMacController new];
    mac.navigationItem.leftBarButtonItem=[[UIBarButtonItem alloc] initWithBarButtonSystemItem:UIBarButtonSystemItemClose target:mac action:@selector(close)];
    UINavigationController *nav=[[UINavigationController alloc] initWithRootViewController:mac];nav.modalPresentationStyle=UIModalPresentationPageSheet;nav.sheetPresentationController.detents=@[UISheetPresentationControllerDetent.largeDetent];nav.sheetPresentationController.prefersGrabberVisible=YES;
    [vc presentViewController:nav animated:YES completion:nil];
}

@implementation FRChatController
- (void)viewDidLoad {
    [super viewDidLoad];self.view.backgroundColor=FRPaper();self.hidesBottomBarWhenPushed=YES;self.navigationItem.largeTitleDisplayMode=UINavigationItemLargeTitleDisplayModeNever;self.jobs=@[];self.approvals=@[];self.reviewedApprovals=[NSMutableSet set];self.expandedRows=[NSMutableSet set];self.mediaFiles=[NSMutableDictionary dictionary];self.loadingMedia=[NSMutableSet set];self.mediaErrors=[NSMutableDictionary dictionary];self.players=[NSMutableDictionary dictionary];
    self.live=[NSMutableDictionary dictionary];self.liveDirty=[NSMutableSet set];self.dismissedSuggestions=[NSMutableSet set];
    self.agentCatalog=FRDefaultAgents();self.agents=[FRSavedAgents(self.sid) mutableCopy];
    NSNumber *thinking=[NSUserDefaults.standardUserDefaults objectForKey:@"FridayThinking"];self.thinking=thinking?thinking.boolValue:YES;
    NSDictionary *saved=[FRAPI.shared.cache snapshot:[@"chat:" stringByAppendingString:self.sid]];
    if([saved[@"items"] isKindOfClass:NSArray.class]){self.rawMessages=saved[@"items"];self.jobs=FRConversationRows(self.rawMessages,self.sid);self.messageRevision=FRString(saved[@"revision"]);self.stamp=[NSJSONSerialization dataWithJSONObject:@[self.jobs,@[]] options:NSJSONWritingSortedKeys error:nil];self.savedPosition=[saved[@"offset"] doubleValue];}
    self.title=[FRStore.shared session:self.sid][@"name"]?:@"会话";
    UIBarButtonItem *more=[[UIBarButtonItem alloc] initWithImage:[UIImage systemImageNamed:@"ellipsis"] menu:nil];more.accessibilityLabel=@"对话选项";more.accessibilityIdentifier=@"chat.more";
    __weak typeof(self) me=self;more.menu=[UIMenu menuWithChildren:@[[UIDeferredMenuElement elementWithUncachedProvider:^(void(^completion)(NSArray<UIMenuElement *> *)){completion([me conversationMenu]);}]]];
    UIBarButtonItem *compose=[[UIBarButtonItem alloc] initWithImage:[UIImage systemImageNamed:@"square.and.pencil"] style:UIBarButtonItemStylePlain target:self action:@selector(newConversation)];compose.accessibilityLabel=@"新建对话";
    self.navigationItem.rightBarButtonItems=@[more,compose];
    if([FRAPI.shared.credentials[@"pairing"][@"account"][@"is_admin"] boolValue]){self.managementButton=[UIButton buttonWithType:UIButtonTypeSystem];self.managementButton.titleLabel.font=[UIFont systemFontOfSize:11 weight:UIFontWeightMedium];[self.managementButton addTarget:self action:@selector(management) forControlEvents:UIControlEventTouchUpInside];self.managementButton.accessibilityIdentifier=@"chat.management";UILabel *heading=[UILabel new];heading.text=self.title;heading.font=[UIFont systemFontOfSize:15 weight:UIFontWeightSemibold];heading.lineBreakMode=NSLineBreakByTruncatingTail;UIStackView *title=[[UIStackView alloc] initWithArrangedSubviews:@[heading,self.managementButton]];title.axis=UILayoutConstraintAxisVertical;title.alignment=UIStackViewAlignmentFill;heading.textAlignment=NSTextAlignmentCenter;self.managementButton.titleLabel.lineBreakMode=NSLineBreakByTruncatingTail;self.titleWidth=[title.widthAnchor constraintEqualToConstant:180];self.titleWidth.active=YES;[title.heightAnchor constraintEqualToConstant:40].active=YES;self.navigationItem.titleView=title;[self updateManagement];}
    self.callBanner=[UIButton buttonWithType:UIButtonTypeSystem];self.callBanner.titleLabel.font=[UIFont systemFontOfSize:12];[self.callBanner addTarget:self action:@selector(voice) forControlEvents:UIControlEventTouchUpInside];self.callBanner.translatesAutoresizingMaskIntoConstraints=NO;[self.view addSubview:self.callBanner];
    self.table=[[UITableView alloc] initWithFrame:CGRectZero style:UITableViewStylePlain];self.table.backgroundColor=FRPaper();self.table.separatorStyle=UITableViewCellSeparatorStyleNone;self.table.dataSource=self;self.table.delegate=self;self.table.estimatedRowHeight=220;self.table.rowHeight=UITableViewAutomaticDimension;self.table.contentInset=UIEdgeInsetsMake(8,0,16,0);self.table.keyboardDismissMode=UIScrollViewKeyboardDismissModeInteractive;self.table.translatesAutoresizingMaskIntoConstraints=NO;[self.view addSubview:self.table];
    UIView *composer=[UIView new];composer.backgroundColor=FRPaper();composer.layer.cornerRadius=22;composer.layer.cornerCurve=kCACornerCurveContinuous;FRSetBorder(composer,FRHairline(),1);composer.accessibilityIdentifier=@"composer";
    self.suggestionBar=[FRSuggestionBar new];self.suggestionBar.hidden=YES;
    UIStackView *dock=[[UIStackView alloc] initWithArrangedSubviews:@[self.suggestionBar,composer]];dock.axis=UILayoutConstraintAxisVertical;dock.spacing=6;dock.translatesAutoresizingMaskIntoConstraints=NO;[self.view addSubview:dock];
    __weak typeof(self) weak=self;UIButton *plus=FRButton(@"plus",@"添加附件与工具",^{[weak attachments];});self.attachmentButton=plus;[self configureAttachmentMenu];UIButton *mic=FRButton(@"waveform",@"语音",^{[weak voice];});self.send=FRButton(@"arrow.up.circle.fill",@"发送",^{[weak sendText];});
    self.input=[UITextView new];self.input.font=[[UIFontMetrics metricsForTextStyle:UIFontTextStyleBody] scaledFontForFont:[UIFont systemFontOfSize:16]];self.input.backgroundColor=UIColor.clearColor;self.input.textColor=UIColor.labelColor;self.input.delegate=self;self.input.textContainerInset=UIEdgeInsetsMake(10,6,8,6);self.input.accessibilityLabel=@"发送消息";NSDictionary *savedDraft=FRStore.shared.drafts[self.sid];self.input.text=([savedDraft[@"sending"] boolValue]||[savedDraft[@"uncertain"] boolValue])?@"":savedDraft[@"text"]?:@"";
    self.placeholder=[UILabel new];self.placeholder.text=@"发消息，输入 @ 邀请助手";self.placeholder.font=self.input.font;self.placeholder.textColor=UIColor.placeholderTextColor;self.placeholder.translatesAutoresizingMaskIntoConstraints=NO;self.placeholder.userInteractionEnabled=NO;[self.input addSubview:self.placeholder];[NSLayoutConstraint activateConstraints:@[[self.placeholder.leadingAnchor constraintEqualToAnchor:self.input.leadingAnchor constant:11],[self.placeholder.topAnchor constraintEqualToAnchor:self.input.topAnchor constant:10]]];
    // Explicit controls under the text: who joins this task, and whether Friday thinks first.
    self.agentButton=FRButton(@"at",@"邀请助手参与",^{});self.agentButton.accessibilityIdentifier=@"composer.agents";self.agentButton.showsMenuAsPrimaryAction=YES;
    self.thinkingButton=FRPillButton(@"思考",@"lightbulb",^(UIButton *b){[weak toggleThinking];});self.thinkingButton.accessibilityLabel=@"深度思考";self.thinkingButton.accessibilityIdentifier=@"composer.thinking";
    UIView *spacer=[UIView new];[spacer setContentHuggingPriority:UILayoutPriorityDefaultLow forAxis:UILayoutConstraintAxisHorizontal];
    UIStackView *controls=[[UIStackView alloc] initWithArrangedSubviews:@[plus,self.agentButton,self.thinkingButton,spacer,mic,self.send]];controls.alignment=UIStackViewAlignmentCenter;controls.spacing=2;[controls setCustomSpacing:6 afterView:self.agentButton];
    self.chips=[UIStackView new];self.chips.spacing=6;self.chips.alignment=UIStackViewAlignmentCenter;
    UIStackView *chipRow=[[UIStackView alloc] initWithArrangedSubviews:@[self.chips,[UIView new]]];chipRow.directionalLayoutMargins=NSDirectionalEdgeInsetsMake(8,6,0,0);chipRow.layoutMarginsRelativeArrangement=YES;
    UIStackView *line=[[UIStackView alloc] initWithArrangedSubviews:@[chipRow,self.input,controls]];line.axis=UILayoutConstraintAxisVertical;line.spacing=0;line.translatesAutoresizingMaskIntoConstraints=NO;[composer addSubview:line];self.inputHeight=[self.input.heightAnchor constraintEqualToConstant:44];self.bannerHeight=[self.callBanner.heightAnchor constraintEqualToConstant:0];
    UIKeyboardLayoutGuide *keyboard=self.view.keyboardLayoutGuide;
    // Reading column: full width on iPhone, centered and capped when the window is wide (iPhone Duo unfolded, iPhone Mirroring).
    UILayoutGuide *safe=self.view.safeAreaLayoutGuide,*column=[UILayoutGuide new];[self.view addLayoutGuide:column];NSLayoutConstraint *fill=[column.widthAnchor constraintEqualToAnchor:safe.widthAnchor];fill.priority=UILayoutPriorityDefaultHigh;
    [NSLayoutConstraint activateConstraints:@[[column.centerXAnchor constraintEqualToAnchor:safe.centerXAnchor],[column.leadingAnchor constraintGreaterThanOrEqualToAnchor:safe.leadingAnchor],[column.widthAnchor constraintLessThanOrEqualToConstant:760],fill,[column.topAnchor constraintEqualToAnchor:safe.topAnchor],[column.bottomAnchor constraintEqualToAnchor:safe.bottomAnchor]]];
    [NSLayoutConstraint activateConstraints:@[[self.callBanner.topAnchor constraintEqualToAnchor:self.view.safeAreaLayoutGuide.topAnchor],[self.callBanner.leadingAnchor constraintEqualToAnchor:column.leadingAnchor constant:16],[self.callBanner.trailingAnchor constraintEqualToAnchor:column.trailingAnchor constant:-16],self.bannerHeight,[self.table.topAnchor constraintEqualToAnchor:self.callBanner.bottomAnchor],[self.table.leadingAnchor constraintEqualToAnchor:column.leadingAnchor],[self.table.trailingAnchor constraintEqualToAnchor:column.trailingAnchor],[self.table.bottomAnchor constraintEqualToAnchor:dock.topAnchor constant:-4],[dock.leadingAnchor constraintEqualToAnchor:column.leadingAnchor constant:12],[dock.trailingAnchor constraintEqualToAnchor:column.trailingAnchor constant:-12],[dock.bottomAnchor constraintEqualToAnchor:keyboard.topAnchor constant:-2],[line.topAnchor constraintEqualToAnchor:composer.topAnchor constant:1],[line.bottomAnchor constraintEqualToAnchor:composer.bottomAnchor constant:-2],[line.leadingAnchor constraintEqualToAnchor:composer.leadingAnchor constant:4],[line.trailingAnchor constraintEqualToAnchor:composer.trailingAnchor constant:-4],self.inputHeight]];
    self.latestButton=FRLatestButton(^(UIButton *button){[weak scrollToLatest];});self.latestButton.hidden=YES;self.latestButton.translatesAutoresizingMaskIntoConstraints=NO;[self.view addSubview:self.latestButton];
    [NSLayoutConstraint activateConstraints:@[[self.latestButton.trailingAnchor constraintEqualToAnchor:self.table.trailingAnchor constant:-16],[self.latestButton.bottomAnchor constraintEqualToAnchor:self.table.bottomAnchor constant:-8],[self.latestButton.heightAnchor constraintEqualToConstant:44]]];
    [self registerForTraitChanges:@[UITraitPreferredContentSizeCategory.class] withHandler:^(id<UITraitEnvironment> environment,UITraitCollection *previous){
        FRChatController *chat=(id)environment;chat.input.font=[[UIFontMetrics metricsForTextStyle:UIFontTextStyleBody] scaledFontForFont:[UIFont systemFontOfSize:16] compatibleWithTraitCollection:chat.traitCollection];chat.placeholder.font=chat.input.font;[chat updateInput];
        // Child Markdown views rebuild after inheriting the new trait. Re-measure rows on the next turn.
        dispatch_async(dispatch_get_main_queue(),^{[chat.table beginUpdates];[chat.table endUpdates];[chat.table layoutIfNeeded];[chat updateLatestButton];});
    }];
    NSNotificationCenter *n=NSNotificationCenter.defaultCenter;[n addObserver:self selector:@selector(dataChanged) name:FRDataChanged object:nil];[n addObserver:self selector:@selector(updateVoice) name:FRVoiceChanged object:nil];[n addObserver:self selector:@selector(accountGone) name:FRAccountChanged object:nil];[self updateInput];[self updateVoice];[self updateComposerControls];[self loadAgentCatalog];
}
- (void)viewWillAppear:(BOOL)animated {[super viewWillAppear:animated];[self updateManagement];NSDictionary *draft=FRStore.shared.drafts[self.sid];if([draft[@"sending"] boolValue]||[draft[@"uncertain"] boolValue])[self showCurrentMessages];[self reload];__weak typeof(self) weak=self;[self.draftTimer invalidate];__block NSUInteger ticks=0;self.draftTimer=[NSTimer scheduledTimerWithTimeInterval:1 repeats:YES block:^(NSTimer *timer){ticks++;[weak updateClocks];
        // A connected stream carries running replies; polling then only catches status changes.
        BOOL streaming=weak.stream.connected;if(weak.approvals.count)[weak reload];else for(NSDictionary *job in weak.jobs)if(FRActive(job)){if(!streaming||ticks%4==0)[weak reload];break;}[weak presentNextApproval];}];
    if(!self.stream){self.stream=[[FRStream alloc] initWithSession:self.sid handler:^(NSDictionary *event){[weak handleStreamEvent:event];}];}[self.stream start];}
- (void)viewDidAppear:(BOOL)animated {[super viewDidAppear:animated];if(self.focusOnAppear){self.focusOnAppear=NO;[self.input becomeFirstResponder];}if(self.stamp&&!self.restoredPosition){self.restoredPosition=YES;[self.table layoutIfNeeded];CGFloat maximum=MAX(-self.table.adjustedContentInset.top,self.table.contentSize.height-self.table.bounds.size.height+self.table.adjustedContentInset.bottom);self.table.contentOffset=CGPointMake(0,MIN(maximum,MAX(-self.table.adjustedContentInset.top,self.savedPosition)));}}
- (void)viewDidLayoutSubviews {
    [super viewDidLayoutSubviews];self.titleWidth.constant=MIN(220,MAX(80,self.view.bounds.size.width-180));
    if(fabs(self.measuredInputWidth-self.input.bounds.size.width)>.5){self.measuredInputWidth=self.input.bounds.size.width;[self updateInput];}
    [self updateLatestButton];
}
- (CGFloat)distanceFromLatest {return MAX(0,self.table.contentSize.height+self.table.adjustedContentInset.bottom-self.table.contentOffset.y-self.table.bounds.size.height);}
- (void)updateLatestButton {self.latestButton.hidden=!self.stamp||!(self.jobs.count+self.approvals.count)||[self distanceFromLatest]<100;}
- (void)scrollViewDidScroll:(UIScrollView *)scrollView {if(scrollView==self.table)[self updateLatestButton];}
- (void)scrollToLatest {
    if(!self.jobs.count&&!self.approvals.count)return;
    [self.table layoutIfNeeded];CGFloat bottom=MAX(-self.table.adjustedContentInset.top,self.table.contentSize.height+self.table.adjustedContentInset.bottom-self.table.bounds.size.height);
    [self.table setContentOffset:CGPointMake(0,bottom) animated:UIView.areAnimationsEnabled&&!UIAccessibilityIsReduceMotionEnabled()];
}
- (void)scrollViewDidEndScrollingAnimation:(UIScrollView *)scrollView {
    if(scrollView==self.table){[self updateLatestButton];if(self.latestButton.hidden)UIAccessibilityPostNotification(UIAccessibilityLayoutChangedNotification,self.table.visibleCells.lastObject);}
}
- (void)saveConversation {if(!self.rawMessages||!FRAPI.shared.credentials)return;NSMutableArray *saved=[NSMutableArray array];for(NSDictionary *item in self.rawMessages){NSMutableDictionary *row=[item mutableCopy];[row removeObjectForKey:@"draft"];[saved addObject:row];}[FRAPI.shared.cache saveSnapshot:@{@"items":saved,@"revision":self.messageRevision?:@"",@"offset":@(self.table.contentOffset.y)} key:[@"chat:" stringByAppendingString:self.sid]];}
- (void)viewWillDisappear:(BOOL)animated {[super viewWillDisappear:animated];[self.draftTimer invalidate];self.draftTimer=nil;[self.stream stop];[self.suggestTimer invalidate];[self saveConversation];}
- (void)viewDidDisappear:(BOOL)animated {[super viewDidDisappear:animated];for(AVPlayerViewController *player in self.players.allValues)[player.player pause];}
- (void)accountGone {[self.stream stop];[self.live removeAllObjects];[self.permissionSheet dismissViewControllerAnimated:NO completion:nil];self.permissionSheet=nil;self.presentedApprovalToken=nil;[self.reviewedApprovals removeAllObjects];self.jobs=@[];self.approvals=@[];self.rawMessages=nil;self.acceptedMessage=nil;self.messageRevision=nil;self.stamp=nil;self.input.text=@"";[self.table reloadData];for(UIViewController *child in self.childViewControllers){if([child isKindOfClass:AVPlayerViewController.class])[(AVPlayerViewController *)child player].rate=0;}[self.mediaFiles removeAllObjects];self.preview=nil;}
- (void)dealloc {[[NSNotificationCenter defaultCenter] removeObserver:self];[_stream stop];}
- (void)settings {FRSessionSettings(self,[FRStore.shared session:self.sid]);}
- (void)newConversation {FRCreateSession(self,nil);}
- (NSArray<UIMenuElement *> *)conversationMenu {
    __weak typeof(self) weak=self;NSDictionary *session=[FRStore.shared session:self.sid]?:@{@"id":self.sid,@"name":self.title?:@""};BOOL archived=[session[@"archived"] boolValue];
    UIAction *rename=[UIAction actionWithTitle:@"重命名" image:[UIImage systemImageNamed:@"pencil"] identifier:nil handler:^(UIAction *a){FRRenameSession(weak,session,^{weak.title=[FRStore.shared session:weak.sid][@"name"]?:weak.title;[weak updateManagement];});}];
    UIAction *files=[UIAction actionWithTitle:@"对话文件" image:[UIImage systemImageNamed:@"folder"] identifier:nil handler:^(UIAction *a){FRFiles(weak,weak.sid);}];
    UIAction *settings=[UIAction actionWithTitle:@"技能与设置" image:[UIImage systemImageNamed:@"slider.horizontal.3"] identifier:nil handler:^(UIAction *a){[weak settings];}];
    UIAction *voice=[UIAction actionWithTitle:@"语音" image:[UIImage systemImageNamed:@"waveform"] identifier:nil handler:^(UIAction *a){[weak voice];}];
    UIAction *archive=[UIAction actionWithTitle:archived?@"取消归档":@"归档" image:[UIImage systemImageNamed:archived?@"tray.and.arrow.up":@"archivebox"] identifier:nil handler:^(UIAction *a){FRArchiveSession(weak,session,!archived);if(!archived)[weak.navigationController popToRootViewControllerAnimated:YES];}];
    UIAction *remove=[UIAction actionWithTitle:@"删除对话" image:[UIImage systemImageNamed:@"trash"] identifier:nil handler:^(UIAction *a){[weak deleteConversation];}];remove.attributes=UIMenuElementAttributesDestructive;
    return @[[UIMenu menuWithTitle:@"" image:nil identifier:nil options:UIMenuOptionsDisplayInline children:@[rename,files,settings,voice]],[UIMenu menuWithTitle:@"" image:nil identifier:nil options:UIMenuOptionsDisplayInline children:@[archive,remove]]];
}
- (void)deleteConversation {
    NSString *sid=self.sid;UINavigationController *nav=self.navigationController;UIViewController *root=nav.viewControllers.firstObject;
    [self.stream stop];[nav popToRootViewControllerAnimated:YES];FRDeleteSessions(root,@[sid],nil);
}
// One turn: the request, its reply and any media it made.
- (NSArray<UIMenuElement *> *)turnMenu:(NSDictionary *)job {
    __weak typeof(self) weak=self;NSString *prompt=FRString(job[@"prompt"]),*reply=FRString(job[@"result"]);NSMutableArray *items=[NSMutableArray array];
    if(prompt.length)[items addObject:[UIAction actionWithTitle:@"复制我的消息" image:[UIImage systemImageNamed:@"doc.on.doc"] identifier:nil handler:^(UIAction *a){UIPasteboard.generalPasteboard.string=prompt;FRToast(weak,@"已复制",nil,nil);}]];
    if(reply.length&&![job[@"kind"] isEqual:@"image"]&&![job[@"kind"] isEqual:@"video"])[items addObject:[UIAction actionWithTitle:@"复制回复" image:[UIImage systemImageNamed:@"doc.on.clipboard"] identifier:nil handler:^(UIAction *a){UIPasteboard.generalPasteboard.string=reply;FRToast(weak,@"已复制",nil,nil);}]];
    if(prompt.length)[items addObject:[UIAction actionWithTitle:@"编辑后重新发送" image:[UIImage systemImageNamed:@"pencil"] identifier:nil handler:^(UIAction *a){weak.input.text=prompt;[weak textViewDidChange:weak.input];[weak.input becomeFirstResponder];}]];
    UIAction *remove=[UIAction actionWithTitle:@"删除这轮对话" image:[UIImage systemImageNamed:@"trash"] identifier:nil handler:^(UIAction *a){[weak deleteTurn:job];}];remove.attributes=UIMenuElementAttributesDestructive;
    return @[[UIMenu menuWithTitle:@"" image:nil identifier:nil options:UIMenuOptionsDisplayInline children:items],remove];
}
- (void)deleteTurn:(NSDictionary *)job {
    __weak typeof(self) weak=self;NSString *jid=FRString(job[@"id"]);if(!jid.length||[jid hasPrefix:@"local:"]||[jid hasPrefix:@"import:"])return;
    FRAskDestructive(self,@"删除这轮对话？",FRActive(job)?@"任务会先停止。这轮消息、回复和生成的文件都会删除，Friday 之后也不会再参考它。":@"这轮消息、回复和生成的文件都会删除，Friday 之后也不会再参考它。无法恢复。",@"删除",^{
        // Gone from the conversation now; the Mac stops and removes it in the background.
        [FRStore.shared hide:@[jid]];weak.rawMessages=[FRStore.shared kept:weak.rawMessages];[weak.live removeObjectForKey:jid];[weak showCurrentMessages];
        [FRAPI.shared call:[FRAPI.shared pathForSession:weak.sid suffix:[@"/tasks/" stringByAppendingString:FRSegment(jid)]] method:@"DELETE" body:nil completion:^(NSDictionary *v,NSError *e){
            if(e){FRShowError(weak,e);[FRStore.shared unhide:@[jid]];weak.messageRevision=nil;[weak reload];return;}
            NSMutableSet *gone=[NSMutableSet setWithObject:jid];for(id item in [v isKindOfClass:NSDictionary.class]&&[v[@"deleted"] isKindOfClass:NSArray.class]?v[@"deleted"]:@[])[gone addObject:item];
            weak.rawMessages=[weak.rawMessages filteredArrayUsingPredicate:[NSPredicate predicateWithBlock:^BOOL(NSDictionary *m,NSDictionary *b){return ![gone containsObject:m[@"id"]]&&![gone containsObject:FRString(m[@"parent_job_id"])];}]];
            weak.messageRevision=nil;[weak showCurrentMessages];[weak saveConversation];[FRStore.shared refresh];
        }];
    });
}
- (UIContextMenuConfiguration *)tableView:(UITableView *)tableView contextMenuConfigurationForRowAtIndexPath:(NSIndexPath *)indexPath point:(CGPoint)point {
    if(!self.stamp||indexPath.row>=self.jobs.count)return nil;NSDictionary *job=self.jobs[indexPath.row];
    if(FRString(job[@"local_status"]).length||[job[@"kind"] isEqual:@"imported"])return nil;
    __weak typeof(self) weak=self;
    return [UIContextMenuConfiguration configurationWithIdentifier:nil previewProvider:nil actionProvider:^UIMenu *(NSArray *suggested){return [UIMenu menuWithChildren:[weak turnMenu:job]];}];
}
- (void)updateManagement {BOOL enabled=[[FRStore.shared session:self.sid][@"management"] boolValue];[self.managementButton setTitle:@"Mac 管理已开启" forState:UIControlStateNormal];self.managementButton.hidden=!enabled;[self.managementButton setTitleColor:UIColor.secondaryLabelColor forState:UIControlStateNormal];UIStackView *title=(id)self.navigationItem.titleView;if([title isKindOfClass:UIStackView.class])[(UILabel *)title.arrangedSubviews.firstObject setText:self.title];}
- (void)management {
    if(self.presentedViewController)return;
    if([[FRStore.shared session:self.sid][@"management"] boolValue]){[self settings];return;}
    FRPermissionController *sheet=[[FRPermissionController alloc] initWithTitle:@"开启 Mac 管理" detail:@"允许这个会话查看 Mac 屏幕、打开应用、诊断网络，并使用管理工具。\n\n写入与修改操作会单独请求确认。屏幕录制和辅助功能仍需在 Mac 的系统设置中授权。\n\n开启后可重新编辑并发送原请求。" context:[@"仅对此会话生效 · " stringByAppendingString:self.title?:@"会话"] allowTitle:@"允许此会话管理 Mac" canDeny:NO];
    self.permissionSheet=sheet;__weak typeof(self) weak=self;__weak FRPermissionController *dialog=sheet;NSUInteger generation=FRAPI.shared.generation;
    sheet.decisionHandler=^(NSString *decision){NSString *path=[FRAPI.shared pathForSession:weak.sid suffix:@""];FRSetButtonBusy(weak.managementButton,YES);
        [FRAPI.shared get:path completion:^(NSDictionary *session,NSError *error){
            if(generation!=FRAPI.shared.generation)return;
            if(error){FRSetButtonBusy(weak.managementButton,NO);[dialog finishWithError:error];return;}
            [FRAPI.shared call:path method:@"PATCH" body:@{@"management":@YES,@"revision":session[@"revision"]} completion:^(NSDictionary *value,NSError *error){
                if(generation!=FRAPI.shared.generation)return;
                FRSetButtonBusy(weak.managementButton,NO);[dialog finishWithError:error];
                if(!error){NSMutableArray *sessions=[FRStore.shared.sessions mutableCopy];for(NSUInteger i=0;i<sessions.count;i++)if([sessions[i][@"id"] isEqual:weak.sid]){NSMutableDictionary *updated=[sessions[i] mutableCopy];updated[@"management"]=@YES;if(value[@"revision"])updated[@"revision"]=value[@"revision"];sessions[i]=updated;}FRStore.shared.sessions=sessions;[weak updateManagement];[weak.table reloadData];[FRStore.shared refresh];}
            }];
        }];
    };[self presentViewController:sheet animated:YES completion:nil];
}
- (void)presentNextApproval {
    if(!self.view.window||self.presentedViewController||self.navigationController.topViewController!=self)return;
    for(NSDictionary *approval in self.approvals)if(![self.reviewedApprovals containsObject:approval[@"token"]]){[self reviewApproval:approval];break;}
}
- (void)reviewApproval:(NSDictionary *)approval {
    if(self.presentedViewController)return;
    NSString *token=FRString(approval[@"token"]);if(!token.length)return;[self.reviewedApprovals addObject:token];self.presentedApprovalToken=token;
    NSString *request=@"";for(NSDictionary *job in self.jobs)if([job[@"id"] isEqual:approval[@"job_id"]]){request=FRString(job[@"prompt"]);break;}
    NSString *context=[NSString stringWithFormat:@"会话：%@\n仅批准此次操作，不授予长期权限。%@",self.title?:@"会话",request.length?[@"\n原始请求：" stringByAppendingString:request]:@""];
    FRPermissionController *sheet=[[FRPermissionController alloc] initWithTitle:@"操作需要你的授权" detail:FRString(approval[@"description"]) context:context allowTitle:@"允许此次操作" canDeny:YES];self.permissionSheet=sheet;__weak typeof(self) weak=self;__weak FRPermissionController *dialog=sheet;NSUInteger generation=FRAPI.shared.generation;
    sheet.closeHandler=^{weak.presentedApprovalToken=nil;[weak reload];};
    sheet.decisionHandler=^(NSString *decision){
        BOOL current=NO;for(NSDictionary *item in weak.approvals)if([item[@"token"] isEqual:token]&&[item[@"expires"] doubleValue]>NSDate.date.timeIntervalSince1970)current=YES;
        if(!current){[dialog finishWithError:[NSError errorWithDomain:@"FridayApproval" code:410 userInfo:@{NSLocalizedDescriptionKey:@"此授权已过期或已处理，请关闭后刷新会话。"}]];return;}
        [FRAPI.shared call:[FRAPI.shared pathForSession:weak.sid suffix:[@"/approvals/" stringByAppendingString:FRSegment(token)]] method:@"POST" body:@{@"decision":decision} completion:^(id value,NSError *error){
            if(generation!=FRAPI.shared.generation)return;
            if(!error){NSMutableArray *remaining=[weak.approvals mutableCopy];[remaining filterUsingPredicate:[NSPredicate predicateWithBlock:^BOOL(NSDictionary *item,NSDictionary *bindings){return ![item[@"token"] isEqual:token];}]];weak.approvals=remaining;[weak.table reloadData];}
            [dialog finishWithError:error];
        }];
    };[self presentViewController:sheet animated:YES completion:nil];
}
- (FRDisclosureRow *)informationRow:(NSString *)title detail:(NSString *)detail symbol:(NSString *)symbol key:(NSString *)key action:(UIButton *)action {
    FRDisclosureRow *row=[[FRDisclosureRow alloc] initWithTitle:title detail:detail symbol:symbol action:action];row.expanded=[self.expandedRows containsObject:key];__weak typeof(self) weak=self;
    row.expansionChanged=^(BOOL expanded){if(expanded)[weak.expandedRows addObject:key];else[weak.expandedRows removeObject:key];[UIView performWithoutAnimation:^{[weak.table beginUpdates];[weak.table endUpdates];}];};return row;
}
- (void)updateClocks {for(NSIndexPath *index in self.table.indexPathsForVisibleRows){if(index.row>=self.jobs.count)continue;UILabel *label=(id)[[self.table cellForRowAtIndexPath:index].contentView viewWithTag:6103];if([label isKindOfClass:UILabel.class])label.text=[NSString stringWithFormat:@"%@ · %@",FRStatus(self.jobs[index.row]),FRTaskTime(self.jobs[index.row],NSDate.date.timeIntervalSince1970)];}}
- (void)updateVoice {FRVoice *v=FRVoice.shared;self.bannerHeight.constant=v.sessionID?30:0;[self.callBanner setTitle:v.sessionID?[NSString stringWithFormat:@"%@ · %@",v.sessionName,v.status]:@"" forState:UIControlStateNormal];}
- (void)voice {FRVoiceSheet(self,[FRStore.shared session:self.sid]);}
- (void)textViewDidChange:(UITextView *)textView {NSMutableDictionary *d=FRStore.shared.drafts[self.sid]?:[NSMutableDictionary dictionary];d[@"text"]=textView.text;FRStore.shared.drafts[self.sid]=d;[self updateInput];
    if(self.mentionTyped){self.mentionTyped=NO;[self showMentionPicker];}
    else if([self.suggestion[@"kind"] isEqual:@"mention"]&&![textView.text hasSuffix:@"@"])[self hideSuggestion];
    [self scheduleSuggestionCheck];
}
- (BOOL)textView:(UITextView *)textView shouldChangeTextInRange:(NSRange)range replacementText:(NSString *)text {
    // "@" opens the agent picker above the composer, like a mention.
    self.mentionTyped=[text isEqual:@"@"]||[text isEqual:@"＠"];return YES;
}
- (void)updateInput {NSDictionary *draft=FRStore.shared.drafts[self.sid];BOOL pending=[draft[@"sending"] boolValue]||[draft[@"uncertain"] boolValue];self.input.editable=!pending;self.placeholder.hidden=self.input.text.length>0;self.send.hidden=pending||!self.input.text.length;FRSetButtonBusy(self.send,NO);self.send.enabled=!pending&&self.input.text.length>0;self.send.accessibilityLabel=@"发送";UIButtonConfiguration *sendConfig=self.send.configuration;sendConfig.image=FRIcon(@"arrow.up.circle.fill",22);self.send.configuration=sendConfig;CGFloat h=self.input.text.length?[self.input sizeThatFits:CGSizeMake(MAX(120,self.input.bounds.size.width),CGFLOAT_MAX)].height:ceil(self.input.font.lineHeight)+self.input.textContainerInset.top+self.input.textContainerInset.bottom;self.inputHeight.constant=MIN(132,MAX(44,h));self.input.scrollEnabled=h>132;}
- (NSArray *)presentedJobs {
    NSMutableArray *jobs=[FRConversationRows(self.rawMessages?:@[],self.sid) mutableCopy];
    if(self.acceptedMessage){BOOL found=NO;for(NSDictionary *job in jobs)if([job[@"id"] isEqual:self.acceptedMessage[@"id"]]){found=YES;break;}if(found)self.acceptedMessage=nil;else[jobs addObject:self.acceptedMessage];}
    NSDictionary *draft=FRStore.shared.drafts[self.sid];NSString *requestID=FRString(draft[@"request_id"]);
    if(requestID.length&&([draft[@"sending"] boolValue]||[draft[@"uncertain"] boolValue])){
        NSUInteger match=NSNotFound;for(NSUInteger i=0;i<jobs.count;i++)if([FRString(jobs[i][@"request_id"]) isEqual:requestID]){match=i;break;}
        NSMutableDictionary *pending=[@{@"id":[@"local:" stringByAppendingString:requestID],@"session_id":self.sid,@"prompt":FRString(draft[@"submitted"]),@"local_status":[draft[@"sending"] boolValue]?@"sending":@"uncertain"} mutableCopy];
        if([draft[@"options"] isKindOfClass:NSDictionary.class])pending[@"options"]=draft[@"options"];
        if(match!=NSNotFound){pending[@"id"]=jobs[match][@"id"];jobs[match]=pending;}else[jobs addObject:pending];
    }
    return [self mergeLive:jobs];
}
// Streamed state wins while it is newer than what the last poll carried.
- (NSArray *)mergeLive:(NSArray *)jobs {
    NSMutableArray *merged=[NSMutableArray arrayWithCapacity:jobs.count];
    for(NSDictionary *job in jobs){
        NSString *jid=FRString(job[@"id"]);NSDictionary *server=[job[@"live"] isKindOfClass:NSDictionary.class]?job[@"live"]:nil;
        if(!FRActive(job)||!jid.length){if(jid.length)[self.live removeObjectForKey:jid];[merged addObject:job];continue;}
        NSMutableDictionary *local=self.live[jid];
        if(server&&(!local||[server[@"seq"] integerValue]>[local[@"seq"] integerValue])){local=[server mutableCopy];self.live[jid]=local;}
        if(local){NSMutableDictionary *copy=[job mutableCopy];copy[@"live"]=[local copy];[merged addObject:copy];}else[merged addObject:job];
    }
    return merged;
}
- (void)reconcileAcceptedMessage {
    NSDictionary *draft=FRStore.shared.drafts[self.sid];NSString *requestID=FRString(draft[@"request_id"]);
    if(!requestID.length)return;
    for(NSDictionary *message in self.rawMessages)if([FRString(message[@"request_id"]) isEqual:requestID]){
        self.acceptedMessage=message;
        [FRStore.shared.drafts removeObjectForKey:self.sid];
        self.input.text=@"";[self updateInput];
        break;
    }
}
- (void)showCurrentMessages {
    NSArray *jobs=[self presentedJobs];NSData *stamp=[NSJSONSerialization dataWithJSONObject:@[jobs,self.approvals?:@[]] options:NSJSONWritingSortedKeys error:nil];
    [self displayJobs:jobs approvals:self.approvals?:@[] stamp:stamp];
}
- (void)submitText:(NSString *)text {
    if(!text.length)return;
    __block BOOL completedSynchronously=NO;
    __block NSString *submittedRequestID=nil;
    NSDictionary *options=@{@"thinking":@(self.thinking),@"agents":[self.agents copy]?:@[]};
    if([self.suggestion[@"kind"] isEqual:@"typing"]||[self.suggestion[@"kind"] isEqual:@"mention"])[self hideSuggestion];
    [self.suggestTimer invalidate];[self.dismissedSuggestions filterUsingPredicate:[NSPredicate predicateWithBlock:^BOOL(NSString *key,NSDictionary *b){return ![key hasPrefix:@"typing:"];}]];
    [FRStore.shared sendText:text session:self.sid options:options completion:^(id value,NSError *error){
        completedSynchronously=YES;
        NSDictionary *draft=FRStore.shared.drafts[self.sid];
        if(!error&&[value isKindOfClass:NSDictionary.class])self.acceptedMessage=value;
        self.input.text=([draft[@"sending"] boolValue]||[draft[@"uncertain"] boolValue])?@"":draft[@"text"]?:@"";
        [self updateInput];[self showCurrentMessages];[self reload];
        BOOL confirmed=[FRString(self.acceptedMessage[@"request_id"]) isEqual:submittedRequestID]&&submittedRequestID.length;
        if(error&&!confirmed&&![draft[@"uncertain"] boolValue])FRShowError(self,error);
    }];
    submittedRequestID=[FRStore.shared.drafts[self.sid][@"request_id"] copy];
    if(!completedSynchronously){self.input.text=@"";[self updateInput];[self showCurrentMessages];[self scrollToLatest];}
}
- (void)sendText {NSString *text=[self.input.text stringByTrimmingCharactersInSet:NSCharacterSet.whitespaceAndNewlineCharacterSet];[self submitText:text];}
- (void)retryPendingMessage {NSDictionary *draft=FRStore.shared.drafts[self.sid];if([draft[@"uncertain"] boolValue])[self submitText:FRString(draft[@"submitted"])];}
- (UIAlertController *)supplementDialogForJob:(NSDictionary *)job {
    NSString *question=FRString(job[@"result"]);
    NSString *detail=[job[@"outcome"] isEqual:@"needs_input"]&&question.length&&question.length<500&&![question containsString:@"还没能给出可靠答复"]?question:@"请补充说明或修改要求，发送后会作为新消息处理。";
    UIAlertController *dialog=[UIAlertController alertControllerWithTitle:@"补充信息" message:detail preferredStyle:UIAlertControllerStyleAlert];
    NSString *existing=[self.input.text stringByTrimmingCharactersInSet:NSCharacterSet.whitespaceAndNewlineCharacterSet];
    [dialog addTextFieldWithConfigurationHandler:^(UITextField *field){field.placeholder=@"输入补充内容";field.text=existing;field.clearButtonMode=UITextFieldViewModeWhileEditing;}];
    [dialog addAction:[UIAlertAction actionWithTitle:@"取消" style:UIAlertActionStyleCancel handler:nil]];
    __weak typeof(self) weak=self;__weak UIAlertController *weakDialog=dialog;
    [dialog addAction:[UIAlertAction actionWithTitle:@"发送补充" style:UIAlertActionStyleDefault handler:^(UIAlertAction *action){
        NSString *text=[weakDialog.textFields.firstObject.text stringByTrimmingCharactersInSet:NSCharacterSet.whitespaceAndNewlineCharacterSet];
        if(text.length)[weak submitText:text];
    }]];
    return dialog;
}
- (void)presentSupplementForJob:(NSDictionary *)job {
    NSDictionary *draft=FRStore.shared.drafts[self.sid];
    if([draft[@"sending"] boolValue]||[draft[@"uncertain"] boolValue])return;
    [self presentViewController:[self supplementDialogForJob:job] animated:YES completion:nil];
}
- (void)dataChanged {if(self.view.window){[self updateManagement];[self reload];}}
- (void)retryLoad {self.loadError=nil;[self reload];}
- (void)updateLoadingState {
    __weak typeof(self) weak=self;
    self.table.backgroundView=(!self.stamp&&self.loadError)?FRStateView(@"wifi.exclamationmark",@"对话暂时无法加载",@"请检查与 Mac 的连接",^{[weak retryLoad];}):(self.stamp&&!self.jobs.count&&!self.approvals.count?[self emptyConversationView]:nil);
    [self.table reloadData];
}
// A blank conversation offers a few starting points; tapping one fills the input
// for review instead of sending, so nothing runs on the Mac by accident.
- (UIView *)emptyConversationView {
    // UI 2.0 blank conversation: a plain question, four starting points, and how to bring in a helper.
    UILabel *heading=FRText(@"有什么要在 Mac 上做的？",FRFont(26,UIFontWeightBold),FRInk(),0);heading.accessibilityTraits=UIAccessibilityTraitHeader;
    UILabel *detail=FRText(@"推理全部在这台 Mac 上完成。可以发文字、图片、文档或语音，标题会自动生成。",FRFont(14,UIFontWeightRegular),FRInkSecondary(),0);
    UIStackView *grid=FRStack(@[],UILayoutConstraintAxisVertical,10);UIStackView *row=nil;__weak typeof(self) weak=self;
    NSArray *items=@[@[@"photo",@"画一张图",@"画一张雨夜城市的插画"],@[@"film",@"做一段 15 秒视频",@"做一段 15 秒的视频："],@[@"doc.text",@"读文档并回答",@"帮我读一下附件，告诉我重点"],@[@"alarm",@"定时提醒我",@"每天早上 8 点提醒我看天气"]];
    for(NSUInteger i=0;i<items.count;i++){
        NSArray *item=items[i];NSString *prompt=item[2];
        UIButton *card=[UIButton buttonWithType:UIButtonTypeSystem];UIButtonConfiguration *c=UIButtonConfiguration.plainButtonConfiguration;
        c.image=FRIcon(item[0],18);c.imagePlacement=NSDirectionalRectEdgeTop;c.imagePadding=10;c.title=item[1];c.titleAlignment=UIButtonConfigurationTitleAlignmentLeading;c.baseForegroundColor=FRInk();c.contentInsets=NSDirectionalEdgeInsetsMake(14,14,14,14);
        c.titleTextAttributesTransformer=^NSDictionary *(NSDictionary *a){NSMutableDictionary *m=[a mutableCopy];m[NSFontAttributeName]=FRFont(14,UIFontWeightMedium);return m;};
        c.background.backgroundColor=FRPaper();c.background.strokeColor=FRHairline();c.background.strokeWidth=1;c.background.cornerRadius=14;
        card.configuration=c;card.contentHorizontalAlignment=UIControlContentHorizontalAlignmentLeading;card.contentVerticalAlignment=UIControlContentVerticalAlignmentTop;card.titleLabel.numberOfLines=0;
        card.accessibilityHint=@"填入输入框，可修改后发送";card.accessibilityIdentifier=@"empty.suggestion";[card.heightAnchor constraintGreaterThanOrEqualToConstant:88].active=YES;
        [card addAction:[UIAction actionWithHandler:^(UIAction *a){FRChatController *me=weak;if(!me)return;me.input.text=prompt;[me textViewDidChange:me.input];[me.input becomeFirstResponder];}] forControlEvents:UIControlEventTouchUpInside];
        if(i%2==0){row=FRStack(@[],UILayoutConstraintAxisHorizontal,10);row.distribution=UIStackViewDistributionFillEqually;[grid addArrangedSubview:row];}
        [row addArrangedSubview:card];
    }
    if(UIContentSizeCategoryIsAccessibilityCategory(self.traitCollection.preferredContentSizeCategory))for(UIStackView *r in grid.arrangedSubviews)r.axis=UILayoutConstraintAxisVertical;
    UIStackView *hint=FRStack(@[FRHug([[UIImageView alloc] initWithImage:FRIcon(@"at",13)]),FRText(@"输入 @ 可以请短剧助手加入，它只在这次任务里打开自己的工具",FRFont(13,UIFontWeightRegular),FRInkSecondary(),0)],UILayoutConstraintAxisHorizontal,8);hint.alignment=UIStackViewAlignmentFirstBaseline;hint.arrangedSubviews.firstObject.tintColor=FRInkSecondary();
    UIStackView *stack=FRStack(@[heading,detail,grid,hint],UILayoutConstraintAxisVertical,6);[stack setCustomSpacing:24 afterView:detail];[stack setCustomSpacing:18 afterView:grid];
    UIView *view=[UIView new];stack.translatesAutoresizingMaskIntoConstraints=NO;[view addSubview:stack];
    NSLayoutConstraint *top=[stack.topAnchor constraintEqualToAnchor:view.safeAreaLayoutGuide.topAnchor constant:40];top.priority=UILayoutPriorityDefaultHigh;
    [NSLayoutConstraint activateConstraints:@[[stack.leadingAnchor constraintEqualToAnchor:view.leadingAnchor constant:24],[stack.trailingAnchor constraintEqualToAnchor:view.trailingAnchor constant:-24],top,[stack.topAnchor constraintGreaterThanOrEqualToAnchor:view.topAnchor constant:8]]];
    return view;
}
- (void)reload {
    if(self.loading||!FRAPI.shared.credentials)return;
    self.loading=YES;if(!self.stamp)[self updateLoadingState];
    NSString *sid=[self.sid copy];NSUInteger generation=FRAPI.shared.generation;
    [FRAPI.shared get:[FRAPI.shared pathForSession:sid suffix:self.messageRevision.length?[@"/messages?revision=" stringByAppendingString:FRSegment(self.messageRevision)]:@"/messages"] completion:^(NSDictionary *v,NSError *e){
        if(![sid isEqual:self.sid]||generation!=FRAPI.shared.generation)return;
        if(e){self.loading=NO;self.loadError=e;if(!self.stamp)[self updateLoadingState];return;}
        if(![v[@"unchanged"] boolValue]){self.rawMessages=[FRStore.shared kept:v[@"items"]?:@[]];self.messageRevision=FRString(v[@"revision"]);[self reconcileAcceptedMessage];}
        NSArray *jobs=[self presentedJobs];
        [FRAPI.shared get:[FRAPI.shared pathForSession:sid suffix:@"/approvals"] completion:^(NSDictionary *a,NSError *e){
            self.loading=NO;if(![sid isEqual:self.sid]||generation!=FRAPI.shared.generation||!FRAPI.shared.credentials)return;
            self.loadError=nil;NSArray *approvals=e?self.approvals:a[@"items"]?:@[];
            NSData *stamp=[NSJSONSerialization dataWithJSONObject:@[jobs,approvals] options:NSJSONWritingSortedKeys error:nil];self.title=[FRStore.shared session:sid][@"name"]?:@"会话";
            if([stamp isEqual:self.stamp]){[self presentNextApproval];return;}
            if(self.table.dragging||self.table.decelerating)return;
            [self displayJobs:jobs approvals:approvals stamp:stamp];
        }];
    }];
}
- (void)displayJobs:(NSArray *)jobs approvals:(NSArray *)approvals stamp:(NSData *)stamp {
    BOOL first=!self.stamp,follow=first||[self distanceFromLatest]<100;
    NSArray *old=self.jobs;BOOL same=[old valueForKey:@"id"]&&[[old valueForKey:@"id"] isEqual:[jobs valueForKey:@"id"]]&&[self.approvals isEqual:approvals];
    NSIndexPath *anchor=[[self.table.indexPathsForVisibleRows sortedArrayUsingSelector:@selector(compare:)] firstObject];NSString *anchorID=anchor&&anchor.row<old.count?old[anchor.row][@"id"]:nil;CGFloat distance=anchor?[self.table rectForRowAtIndexPath:anchor].origin.y-self.table.contentOffset.y:0;
    __block BOOL snapshotChanged=first||!same;
    self.stamp=stamp;self.jobs=jobs;self.approvals=approvals;self.table.backgroundView=nil;
    if(self.presentedApprovalToken&&![[approvals valueForKey:@"token"] containsObject:self.presentedApprovalToken]&&!self.permissionSheet.submitting){[self.permissionSheet dismissViewControllerAnimated:YES completion:nil];self.presentedApprovalToken=nil;}

    [UIView performWithoutAnimation:^{
        if(first||!same){[self updateLoadingState];}
        else {NSMutableArray *changed=[NSMutableArray array];BOOL resized=NO;
            for(NSUInteger i=0;i<jobs.count;i++){if([old[i] isEqual:jobs[i]])continue;NSIndexPath *index=[NSIndexPath indexPathForRow:i inSection:0];UITableViewCell *cell=[self.table cellForRowAtIndexPath:index];FRMarkdownView *text=(id)[cell.contentView viewWithTag:6101];NSMutableDictionary *before=[old[i] mutableCopy],*after=[jobs[i] mutableCopy];[before removeObjectForKey:@"draft"];[after removeObjectForKey:@"draft"];snapshotChanged=snapshotChanged||![before isEqual:after];
                [before removeObjectForKey:@"live"];[after removeObjectForKey:@"live"];
                if(text&&[before isEqual:after]&&[jobs[i][@"draft"] isKindOfClass:NSDictionary.class]&&!jobs[i][@"live"]){text.text=FRString(jobs[i][@"draft"][@"text"]);UILabel *phase=(id)[cell.contentView viewWithTag:6102];phase.text=[jobs[i][@"draft"][@"phase"] isEqual:@"reviewing"]?@"草稿 · 正在核对":@"草稿 · 正在生成";resized=YES;}
                else if(cell&&[before isEqual:after]&&[jobs[i][@"live"] isKindOfClass:NSDictionary.class]&&[self updateLiveCell:cell job:jobs[i]])resized=YES;
                else [changed addObject:index];}
            if(changed.count)[self.table reloadRowsAtIndexPaths:changed withRowAnimation:UITableViewRowAnimationNone];else if(resized){[self.table beginUpdates];[self.table endUpdates];}
        }
        [self.table layoutIfNeeded];
        if(follow&&jobs.count+approvals.count)[self.table scrollToRowAtIndexPath:[NSIndexPath indexPathForRow:jobs.count+approvals.count-1 inSection:0] atScrollPosition:UITableViewScrollPositionBottom animated:NO];
        else if(anchorID){NSUInteger index=[[jobs valueForKey:@"id"] indexOfObject:anchorID];if(index!=NSNotFound)self.table.contentOffset=CGPointMake(0,[self.table rectForRowAtIndexPath:[NSIndexPath indexPathForRow:index inSection:0]].origin.y-distance);}
    }];[self updateLatestButton];[self updateClocks];if(snapshotChanged)[self saveConversation];[self presentNextApproval];[self updateJobSuggestion];
}
- (NSInteger)tableView:(UITableView *)tableView numberOfRowsInSection:(NSInteger)section {return !self.stamp&&self.loading&&!self.loadError?MAX(3,MIN(12,(NSInteger)ceil((self.table.bounds.size.height-self.table.adjustedContentInset.top-self.table.adjustedContentInset.bottom)/112.))):self.jobs.count+self.approvals.count;}
- (UITableViewCell *)tableView:(UITableView *)tableView cellForRowAtIndexPath:(NSIndexPath *)indexPath {
    if(!self.stamp&&self.loading&&!self.loadError)return FRSkeletonCell(@"chat");
    UITableViewCell *cell=[[UITableViewCell alloc] initWithStyle:UITableViewCellStyleDefault reuseIdentifier:nil];cell.backgroundColor=UIColor.clearColor;cell.selectionStyle=UITableViewCellSelectionStyleNone;
    UIStackView *stack=[UIStackView new];stack.axis=UILayoutConstraintAxisVertical;stack.spacing=12;stack.translatesAutoresizingMaskIntoConstraints=NO;[cell.contentView addSubview:stack];[NSLayoutConstraint activateConstraints:@[[stack.leadingAnchor constraintEqualToAnchor:cell.contentView.leadingAnchor constant:16],[stack.trailingAnchor constraintEqualToAnchor:cell.contentView.trailingAnchor constant:-16],[stack.topAnchor constraintEqualToAnchor:cell.contentView.topAnchor constant:14],[stack.bottomAnchor constraintEqualToAnchor:cell.contentView.bottomAnchor constant:-14]]];
    if(indexPath.row>=self.jobs.count){NSDictionary *approval=self.approvals[indexPath.row-self.jobs.count];__weak typeof(self) weak=self;
        FRPermissionCard *card=[[FRPermissionCard alloc] initWithTitle:@"等待授权" detail:FRString(approval[@"description"]) buttonTitle:@"查看并决定" review:^(UIButton *button){[weak reviewApproval:approval];}];[stack addArrangedSubview:card];return cell;}
    NSDictionary *job=self.jobs[indexPath.row];BOOL child=job[@"parent_job_id"]&&job[@"parent_job_id"]!=NSNull.null;
    NSString *localStatus=FRString(job[@"local_status"]);
    NSString *prompt=child?@"":FRString(job[@"prompt"]),*reply=[job[@"outcome"] isEqual:@"continued"]?@"":FRString(job[@"result"]);
    NSDictionary *draft=[job[@"draft"] isKindOfClass:NSDictionary.class]?job[@"draft"]:nil;
    BOOL showingDraft=!reply.length&&[FRString(job[@"status"]) isEqual:@"running"]&&FRString(draft[@"text"]).length;
    if(showingDraft)reply=FRString(draft[@"text"]);
    NSDictionary *live=FRActive(job)&&[job[@"live"] isKindOfClass:NSDictionary.class]?job[@"live"]:nil;
    BOOL showingLive=live&&!reply.length&&FRString(live[@"text"]).length;
    if(showingLive){reply=FRString(live[@"text"]);showingDraft=NO;}
    if(prompt.length){
        UIView *row=[UIView new],*bubble=[UIView new];bubble.backgroundColor=FRSurface();bubble.layer.cornerRadius=18;bubble.layer.cornerCurve=kCACornerCurveContinuous;bubble.translatesAutoresizingMaskIntoConstraints=NO;[row addSubview:bubble];
        UILabel *label=[UILabel new];label.text=prompt;label.numberOfLines=0;label.font=[[UIFontMetrics metricsForTextStyle:UIFontTextStyleBody] scaledFontForFont:[UIFont systemFontOfSize:16]];label.textColor=UIColor.labelColor;label.translatesAutoresizingMaskIntoConstraints=NO;[bubble addSubview:label];
        [NSLayoutConstraint activateConstraints:@[[bubble.trailingAnchor constraintEqualToAnchor:row.trailingAnchor],[bubble.leadingAnchor constraintGreaterThanOrEqualToAnchor:row.leadingAnchor constant:32],[bubble.widthAnchor constraintLessThanOrEqualToAnchor:row.widthAnchor multiplier:.86],[bubble.topAnchor constraintEqualToAnchor:row.topAnchor],[bubble.bottomAnchor constraintEqualToAnchor:row.bottomAnchor],[label.topAnchor constraintEqualToAnchor:bubble.topAnchor constant:10],[label.bottomAnchor constraintEqualToAnchor:bubble.bottomAnchor constant:-10],[label.leadingAnchor constraintEqualToAnchor:bubble.leadingAnchor constant:14],[label.trailingAnchor constraintEqualToAnchor:bubble.trailingAnchor constant:-14]]];[stack addArrangedSubview:row];[stack setCustomSpacing:20 afterView:row];
        NSString *caption=[self optionsCaption:job[@"options"]];
        if(caption.length){UILabel *note=[UILabel new];note.text=caption;note.font=[UIFont preferredFontForTextStyle:UIFontTextStyleCaption2];note.textColor=UIColor.tertiaryLabelColor;note.textAlignment=NSTextAlignmentRight;note.accessibilityIdentifier=@"message.options";[stack setCustomSpacing:4 afterView:row];[stack addArrangedSubview:note];[stack setCustomSpacing:16 afterView:note];}
    }
    if(localStatus.length){
        UIStackView *state=[UIStackView new];state.axis=UILayoutConstraintAxisHorizontal;state.alignment=UIStackViewAlignmentCenter;state.spacing=8;
        if([localStatus isEqual:@"sending"]){UIActivityIndicatorView *spinner=[[UIActivityIndicatorView alloc] initWithActivityIndicatorStyle:UIActivityIndicatorViewStyleMedium];spinner.accessibilityIdentifier=@"message.sending";[spinner startAnimating];[state addArrangedSubview:spinner];}
        UILabel *label=[UILabel new];label.text=[localStatus isEqual:@"sending"]?@"发送中…":@"发送状态未确认";label.font=[UIFont preferredFontForTextStyle:UIFontTextStyleCaption1];label.textColor=UIColor.secondaryLabelColor;label.accessibilityIdentifier=@"message.sendStatus";[state addArrangedSubview:label];
        if([localStatus isEqual:@"uncertain"]){__weak typeof(self) weak=self;UIButton *retry=FRQuietButton(nil,@"arrow.clockwise",@"重试",^(UIButton *button){[weak retryPendingMessage];});retry.accessibilityIdentifier=@"message.retry";[state addArrangedSubview:retry];}
        [stack addArrangedSubview:state];return cell;
    }
    if(live)[self addLive:live job:job stack:stack cell:cell];
    NSDictionary *project=[job[@"project"] isKindOfClass:NSDictionary.class]?job[@"project"]:nil;
    NSDictionary *shot=[job[@"shot"] isKindOfClass:NSDictionary.class]?job[@"shot"]:nil;
    if(project||shot){NSString *detail=project?[NSString stringWithFormat:@"%@ · 已完成 %@ / %@ 个镜头",FRString(project[@"title"]),project[@"completed"],project[@"total"]]:[NSString stringWithFormat:@"镜头 %@ / %@ · %@",shot[@"index"],shot[@"total"],FRStatus(job)];[stack addArrangedSubview:[self informationRow:detail detail:@"生成结果会依次显示在这条消息下方。" symbol:@"film" key:[job[@"id"] stringByAppendingString:@".project"] action:nil]];}
    if(showingDraft){UILabel *status=[UILabel new];status.tag=6102;status.text=[draft[@"phase"] isEqual:@"reviewing"]?@"草稿 · 正在核对":@"草稿 · 正在生成";status.font=[UIFont preferredFontForTextStyle:UIFontTextStyleCaption1];status.textColor=UIColor.secondaryLabelColor;[stack addArrangedSubview:status];}
    if(reply.length&&!([job[@"required_action"] isEqual:@"enable_mac_management"]&&[FRAPI.shared.credentials[@"pairing"][@"account"][@"is_admin"] boolValue])){FRMarkdownView *message=[[FRMarkdownView alloc] initWithText:reply];message.tag=6101;message.showsActions=!FRActive(job);if(!FRActive(job)&&!localStatus.length&&![job[@"kind"] isEqual:@"imported"])message.moreMenu=[UIMenu menuWithChildren:[self turnMenu:job]];message.metadata=[job[@"kind"] isEqual:@"imported"]||showingLive?@"":[NSString stringWithFormat:@"%@ · %@",FRStatus(job),FRTaskTime(job,NSDate.date.timeIntervalSince1970)];[stack addArrangedSubview:message];}
    NSDictionary *drama=[job[@"drama"] isKindOfClass:NSDictionary.class]?job[@"drama"]:nil;
    if(drama)[stack addArrangedSubview:[self dramaCard:drama]];
    BOOL needsManagement=[job[@"required_action"] isEqual:@"enable_mac_management"]&&[FRAPI.shared.credentials[@"pairing"][@"account"][@"is_admin"] boolValue];
    BOOL managementEnabled=[[FRStore.shared session:self.sid][@"management"] boolValue];
    BOOL canRetry=!FRActive(job)&&[@[@"blocked",@"needs_input",@"partial",@"paused"] containsObject:job[@"outcome"]?:@""];
    __weak typeof(self) weak=self;
    if(needsManagement&&!managementEnabled){[stack addArrangedSubview:[[FRPermissionCard alloc] initWithTitle:@"需要 Mac 管理权限" detail:@"此会话尚未获得权限，电脑操作未执行。" buttonTitle:@"查看权限并开启" review:^(UIButton *b){[weak management];}]];}
    if(canRetry&&(!needsManagement||managementEnabled)){
        UIButton *supplement=FRQuietButton(nil,@"square.and.pencil",@"填写补充信息",^(UIButton *button){[weak presentSupplementForJob:job];});
        supplement.accessibilityIdentifier=@"message.supplement";
        [stack addArrangedSubview:[self informationRow:needsManagement&&managementEnabled?@"权限已开启，可补充后继续":@"补充信息后继续" detail:@"点按右侧按钮，在弹窗中填写并发送补充。" symbol:@"text.bubble" key:[job[@"id"] stringByAppendingString:@".retry"] action:supplement]];
    }
    if(FRString(job[@"error"]).length)[stack addArrangedSubview:[self informationRow:@"操作未完成 · 查看原因" detail:FRString(job[@"error"]) symbol:@"exclamationmark.circle" key:[job[@"id"] stringByAppendingString:@".error"] action:nil]];
    NSString *artifact=FRString(job[@"artifact_url"]);NSString *expected=[FRAPI.shared pathForSession:self.sid suffix:[NSString stringWithFormat:@"/tasks/%@/artifact",FRSegment(job[@"id"])]];
    if(artifact.length&&[artifact isEqual:expected]&&[job[@"session_id"] isEqual:self.sid]) {
        NSString *mediaPath=artifact;if([job[@"artifact_thumbnail_url"] isEqual:[artifact stringByAppendingString:@"?thumbnail=1"]])mediaPath=job[@"artifact_thumbnail_url"];if(FRString(job[@"artifact_version"]).length)mediaPath=[mediaPath stringByAppendingFormat:@"%@version=%@",[mediaPath containsString:@"?"]?@"&":@"?",FRSegment(job[@"artifact_version"])];
        NSURL *file=self.mediaFiles[mediaPath];if(file&&![NSFileManager.defaultManager fileExistsAtPath:file.path]){[self.mediaFiles removeObjectForKey:mediaPath];file=nil;}if(!file)file=[FRAPI.shared cachedMedia:mediaPath];
        NSString *mime=FRString(job[@"artifact_media_type"]);BOOL image=[mime hasPrefix:@"image/"],video=[mime hasPrefix:@"video/"],audio=[mime hasPrefix:@"audio/"];
        Class cardClass=image?FRImageCard.class:(video?FRVideoCard.class:(audio?FRAudioCard.class:FRFileCard.class));
        FRMediaCard *card=[[cardClass alloc] initWithFilename:FRString(job[@"artifact_filename"]) kind:image?@"图片":(video?@"视频":(audio?@"音频":@"文件")) symbol:image?@"photo":(video?@"play.rectangle":(audio?@"waveform":@"doc")) ready:file!=nil error:self.mediaErrors[job[@"id"]] action:^(UIButton *button){if(file)[weak openArtifact:job button:button];else{FRSetButtonBusy(button,YES);[weak loadMedia:job path:mediaPath];}}];
        card.accessibilityIdentifier=image?@"component.image":(video?@"component.video":(audio?@"component.audio":@"component.file"));
        if(!file&&!self.mediaErrors[job[@"id"]]&&(image||video)){FRSkeletonView *placeholder=[[FRSkeletonView alloc] initWithKind:@"media"];placeholder.accessibilityLabel=@"正在加载媒体预览";[card installPreview:placeholder height:210];}
        if(file&&image)[(FRImageCard *)card setPreviewImage:[UIImage imageWithContentsOfFile:file.path] action:^(UIButton *button){[weak openArtifact:job button:button];}];
        if(file&&(video||audio)){AVPlayerViewController *player=self.players[job[@"id"]];BOOL newPlayer=!player;if(newPlayer){player=[AVPlayerViewController new];player.player=[AVPlayer playerWithURL:file];self.players[job[@"id"]]=player;[self addChildViewController:player];}
            // The cached player moves between cards; its old size constraints must not accumulate.
            for(NSLayoutConstraint *constraint in [player.view.constraints copy])if(constraint.firstAttribute==NSLayoutAttributeHeight&&constraint.secondItem==nil)[player.view removeConstraint:constraint];
            [card installPreview:player.view height:audio?80:210];if(newPlayer)[player didMoveToParentViewController:self];}
        [stack addArrangedSubview:card];
        if(!file&&!self.mediaErrors[job[@"id"]])[self loadMedia:job path:mediaPath];
    }
    if(FRActive(job)){
        if([job[@"todo"] isKindOfClass:NSArray.class])for(UIView *line in FRTodoViews(job[@"todo"]))[stack addArrangedSubview:line];
        UIButton *stop=FRQuietButton(nil,@"stop.circle",@"停止任务",^(UIButton *button){FRConfirm(weak,@"停止这项任务？",@"已经完成的内容会保留。",^{FRSetButtonBusy(button,YES);[FRAPI.shared call:[FRAPI.shared pathForSession:weak.sid suffix:[NSString stringWithFormat:@"/tasks/%@/cancel",FRSegment(job[@"id"])]] method:@"POST" body:@{} completion:^(id value,NSError *error){FRSetButtonBusy(button,NO);if(error)FRShowError(weak,error);else[weak reload];}];});});
        FRDisclosureRow *activity=[self informationRow:[NSString stringWithFormat:@"%@ · %@",FRStatus(job),FRTaskTime(job,NSDate.date.timeIntervalSince1970)] detail:@"任务在 Mac 上执行。离开会话不会中断任务；停止后，已经完成的内容会保留。" symbol:@"circle.dotted" key:[job[@"id"] stringByAppendingString:@".activity"] action:stop];activity.summaryLabel.tag=6103;activity.summaryLabel.accessibilityIdentifier=@"task.elapsed";[stack addArrangedSubview:activity];
    }else if(![job[@"kind"] isEqual:@"imported"]&&![stack viewWithTag:6101]){
        UILabel *time=[UILabel new];time.tag=6103;time.text=[NSString stringWithFormat:@"%@ · %@",FRStatus(job),FRTaskTime(job,NSDate.date.timeIntervalSince1970)];time.font=[UIFont monospacedDigitSystemFontOfSize:12 weight:UIFontWeightRegular];time.textColor=UIColor.secondaryLabelColor;time.numberOfLines=0;time.accessibilityIdentifier=@"task.elapsed";[stack addArrangedSubview:time];
    }return cell;
}

#pragma mark - Live reply

- (NSString *)optionsCaption:(NSDictionary *)options {
    if(![options isKindOfClass:NSDictionary.class])return @"";NSMutableArray *bits=[NSMutableArray array];
    for(NSString *aid in [options[@"agents"] isKindOfClass:NSArray.class]?options[@"agents"]:@[])[bits addObject:[@"@" stringByAppendingString:[self agentNamed:aid][@"name"]?:aid]];
    if([options[@"thinking"] isKindOfClass:NSNumber.class]&&[options[@"thinking"] boolValue])[bits addObject:@"深度思考"];
    return [bits componentsJoinedByString:@" · "];
}
- (void)addLive:(NSDictionary *)live job:(NSDictionary *)job stack:(UIStackView *)stack cell:(UITableViewCell *)cell {
    NSString *thinking=FRString(live[@"thinking"]),*text=FRString(live[@"text"]);NSArray *tools=[live[@"tools"] isKindOfClass:NSArray.class]?live[@"tools"]:@[];
    cell.restorationIdentifier=FRLiveSignature(live);
    if(thinking.length){
        NSString *key=[FRString(job[@"id"]) stringByAppendingString:@".thinking"];FRThinkingView *view=[[FRThinkingView alloc] initWithText:thinking active:!text.length&&!tools.count];view.tag=6104;view.expanded=[self.expandedRows containsObject:key];__weak typeof(self) weak=self;
        view.expansionChanged=^(BOOL expanded){if(expanded)[weak.expandedRows addObject:key];else[weak.expandedRows removeObject:key];[UIView performWithoutAnimation:^{[weak.table beginUpdates];[weak.table endUpdates];}];};
        [stack addArrangedSubview:view];
    }
    if(tools.count){UIStackView *steps=FRToolStepsView(tools);steps.tag=6105;[stack addArrangedSubview:steps];}
    if(!thinking.length&&!tools.count&&!text.length){
        UIActivityIndicatorView *spinner=[[UIActivityIndicatorView alloc] initWithActivityIndicatorStyle:UIActivityIndicatorViewStyleMedium];[spinner startAnimating];
        // The Mac says why nothing streams yet (model loading, queued behind another job).
        UILabel *label=[UILabel new];label.tag=6106;label.text=FRLiveWaiting(live);label.numberOfLines=0;label.font=[UIFont preferredFontForTextStyle:UIFontTextStyleFootnote];label.adjustsFontForContentSizeCategory=YES;label.textColor=UIColor.secondaryLabelColor;
        UIStackView *row=[[UIStackView alloc] initWithArrangedSubviews:@[spinner,label,[UIView new]]];row.spacing=8;row.alignment=UIStackViewAlignmentCenter;row.accessibilityIdentifier=@"live.waiting";[stack addArrangedSubview:row];
    }
}
// Updates a visible streaming row without rebuilding it; NO when its layout must change.
- (BOOL)updateLiveCell:(UITableViewCell *)cell job:(NSDictionary *)job {
    NSDictionary *live=job[@"live"];if(![cell.restorationIdentifier isEqual:FRLiveSignature(live)])return NO;
    NSString *text=FRString(live[@"text"]);NSArray *tools=[live[@"tools"] isKindOfClass:NSArray.class]?live[@"tools"]:@[];
    FRThinkingView *thinking=(id)[cell.contentView viewWithTag:6104];if([thinking isKindOfClass:FRThinkingView.class])[thinking setText:FRString(live[@"thinking"]) active:!text.length&&!tools.count];
    UIStackView *steps=(id)[cell.contentView viewWithTag:6105];if([steps isKindOfClass:UIStackView.class])FRUpdateToolSteps(steps,tools);
    UILabel *waiting=(id)[cell.contentView viewWithTag:6106];if([waiting isKindOfClass:UILabel.class]&&![waiting.text isEqual:FRLiveWaiting(live)]){waiting.text=FRLiveWaiting(live);return NO;}
    FRMarkdownView *message=(id)[cell.contentView viewWithTag:6101];if(text.length){if(![message isKindOfClass:FRMarkdownView.class])return NO;if(![message.text isEqual:text])message.text=text;}
    return YES;
}
- (void)handleStreamEvent:(NSDictionary *)event {
    NSString *type=FRString(event[@"type"]),*jid=FRString(event[@"job_id"]);
    if(!jid.length)return;
    if([type isEqual:@"done"]){[self.live removeObjectForKey:jid];dispatch_after(dispatch_time(DISPATCH_TIME_NOW,200*NSEC_PER_MSEC),dispatch_get_main_queue(),^{[self reload];});return;}
    NSInteger seq=[event[@"seq"] integerValue];NSMutableDictionary *state=self.live[jid];
    if([type isEqual:@"snapshot"]){NSDictionary *snapshot=event[@"state"];if(![snapshot isKindOfClass:NSDictionary.class])return;if(state&&[state[@"seq"] integerValue]>=seq)return;state=[snapshot mutableCopy];self.live[jid]=state;}
    else{
        if(!state){state=[@{@"thinking":@"",@"text":@"",@"tools":@[],@"seq":@0} mutableCopy];self.live[jid]=state;}
        if(seq&&seq<=[state[@"seq"] integerValue])return;state[@"seq"]=@(seq);
        if([type isEqual:@"thinking"])state[@"thinking"]=[FRString(state[@"thinking"]) stringByAppendingString:FRString(event[@"delta"])];
        else if([type isEqual:@"text"])state[@"text"]=[FRString(state[@"text"]) stringByAppendingString:FRString(event[@"delta"])];
        else if([type isEqual:@"step"])state[@"text"]=@"";
        else if([type isEqual:@"suggest"])state[@"suggested"]=FRString(event[@"agent"]);
        else if([type isEqual:@"status"])state[@"status"]=FRString(event[@"text"]);
        else if([type isEqual:@"tool"]&&[event[@"tool"] isKindOfClass:NSDictionary.class]){NSMutableArray *tools=[state[@"tools"] mutableCopy]?:[NSMutableArray array];NSUInteger index=[[tools valueForKey:@"id"] indexOfObject:event[@"tool"][@"id"]];if(index==NSNotFound)[tools addObject:event[@"tool"]];else tools[index]=event[@"tool"];state[@"tools"]=tools;}
    }
    if(![[self.jobs valueForKey:@"id"] containsObject:jid]){[self reload];return;}
    [self.liveDirty addObject:jid];
    if(self.liveScheduled)return;self.liveScheduled=YES;
    // Coalesce token-sized events into a few redraws per second.
    dispatch_after(dispatch_time(DISPATCH_TIME_NOW,80*NSEC_PER_MSEC),dispatch_get_main_queue(),^{self.liveScheduled=NO;[self flushLive];});
}
- (void)flushLive {
    if(!self.liveDirty.count||!self.stamp||self.table.dragging||self.table.decelerating){if(self.liveDirty.count&&self.stamp){self.liveScheduled=YES;dispatch_after(dispatch_time(DISPATCH_TIME_NOW,250*NSEC_PER_MSEC),dispatch_get_main_queue(),^{self.liveScheduled=NO;[self flushLive];});}return;}
    [self.liveDirty removeAllObjects];
    NSArray *jobs=[self mergeLive:self.jobs];
    [self displayJobs:jobs approvals:self.approvals?:@[] stamp:[NSJSONSerialization dataWithJSONObject:@[jobs,self.approvals?:@[]] options:NSJSONWritingSortedKeys error:nil]];
}
- (UIView *)dramaCard:(NSDictionary *)drama {
    FRChatCard *card=[[FRChatCard alloc] initWithTitle:[@"短剧 · " stringByAppendingString:FRString(drama[@"title"])] symbol:@"film"];card.accessibilityIdentifier=@"component.drama";
    NSInteger shots=[drama[@"shots"] integerValue];
    [card addDetail:shots?[NSString stringWithFormat:@"%@ · %ld / %ld 镜完成",FRDramaStage(drama[@"stage"]),(long)[drama[@"done"] integerValue],(long)shots]:FRDramaStage(drama[@"stage"])];
    NSString *pid=FRString(drama[@"id"]);__weak typeof(self) weak=self;
    UIButton *open=FRActionButton(@"打开项目",@"arrow.up.forward.square",FRActionStyleSecondary,^(UIButton *b){FROpenDrama(weak,pid);});open.accessibilityIdentifier=@"drama.open";
    UIStackView *row=[[UIStackView alloc] initWithArrangedSubviews:@[open,[UIView new]]];[card.content addArrangedSubview:row];return card;
}

#pragma mark - Composer: agents, thinking, suggestions

- (NSDictionary *)agentNamed:(NSString *)aid {for(NSDictionary *agent in self.agentCatalog)if([agent[@"id"] isEqual:aid])return agent;return nil;}
- (void)loadAgentCatalog {
    [FRAPI.shared get:@"/v1/agents" completion:^(NSDictionary *value,NSError *error){
        if(error||![value[@"items"] isKindOfClass:NSArray.class])return;
        self.agentCatalog=value[@"items"];[self.agents filterUsingPredicate:[NSPredicate predicateWithBlock:^BOOL(NSString *aid,NSDictionary *b){return [self agentNamed:aid]!=nil;}]];[self updateComposerControls];
    }];
}
- (void)saveAgents {[NSUserDefaults.standardUserDefaults setObject:[self.agents copy] forKey:FRAgentsKey(self.sid)];}
- (void)addAgent:(NSString *)aid {
    if(![self agentNamed:aid]||[self.agents containsObject:aid])return;[self.agents addObject:aid];[self saveAgents];
    if([self.suggestion[@"id"] isEqual:aid])[self hideSuggestion];[self updateComposerControls];
    UIAccessibilityPostNotification(UIAccessibilityAnnouncementNotification,[NSString stringWithFormat:@"%@已加入",[self agentNamed:aid][@"name"]]);
}
- (void)removeAgent:(NSString *)aid {[self.agents removeObject:aid];[self saveAgents];[self updateComposerControls];[self scheduleSuggestionCheck];}
- (void)toggleThinking {self.thinking=!self.thinking;[NSUserDefaults.standardUserDefaults setBool:self.thinking forKey:@"FridayThinking"];[self updateComposerControls];}
- (void)updateComposerControls {
    FRSetPillOn(self.thinkingButton,self.thinking?@"lightbulb.fill":@"lightbulb",self.thinking);
    for(UIView *chip in self.chips.arrangedSubviews){[self.chips removeArrangedSubview:chip];[chip removeFromSuperview];}
    __weak typeof(self) weak=self;
    for(NSString *aid in self.agents){NSDictionary *agent=[self agentNamed:aid];if(agent)[self.chips addArrangedSubview:FRAgentChip(agent[@"name"],agent[@"symbol"],^(UIButton *b){[weak removeAgent:aid];})];}
    self.chips.superview.hidden=self.chips.arrangedSubviews.count==0;
    NSMutableArray *items=[NSMutableArray array];
    for(NSDictionary *agent in self.agentCatalog){NSString *aid=agent[@"id"];BOOL on=[self.agents containsObject:aid];
        UIAction *action=[UIAction actionWithTitle:agent[@"name"] image:[UIImage systemImageNamed:agent[@"symbol"]?:@"person.crop.circle"] identifier:nil handler:^(UIAction *a){if(on)[weak removeAgent:aid];else[weak addAgent:aid];}];
        action.subtitle=agent[@"description"];action.state=on?UIMenuElementStateOn:UIMenuElementStateOff;[items addObject:action];}
    UIMenu *invite=[UIMenu menuWithTitle:@"邀请助手参与这项任务" image:nil identifier:nil options:UIMenuOptionsDisplayInline children:items];
    UIAction *projects=[UIAction actionWithTitle:@"查看短剧项目" image:[UIImage systemImageNamed:@"film.stack"] identifier:nil handler:^(UIAction *a){FRShowDramas(weak);}];
    self.agentButton.menu=[UIMenu menuWithChildren:@[invite,[UIMenu menuWithTitle:@"" image:nil identifier:nil options:UIMenuOptionsDisplayInline children:@[projects]]]];
    self.agentButton.tintColor=self.agents.count?FRAccent():UIColor.labelColor;
    self.agentButton.accessibilityValue=self.agents.count?[NSString stringWithFormat:@"已邀请 %lu 位",(unsigned long)self.agents.count]:@"未邀请";
}
- (void)showSuggestion:(NSDictionary *)suggestion {
    self.suggestion=suggestion;NSDictionary *agent=[self agentNamed:suggestion[@"id"]];NSString *kind=suggestion[@"kind"];__weak typeof(self) weak=self;
    if([kind isEqual:@"mention"]){
        [self.suggestionBar showSymbol:@"at" title:@"邀请助手参与这项任务" detail:[NSString stringWithFormat:@"%@ · %@",agent[@"name"],agent[@"description"]?:@""] accept:[@"@" stringByAppendingString:agent[@"name"]]];
        self.suggestionBar.accept=^{[weak removeTypedMention];[weak addAgent:suggestion[@"id"]];[weak hideSuggestion];};
        self.suggestionBar.dismiss=^{[weak hideSuggestion];};
    }else{
        BOOL job=[kind isEqual:@"job"];
        [self.suggestionBar showSymbol:agent[@"symbol"] title:[NSString stringWithFormat:@"让%@加入？",agent[@"name"]] detail:job?@"Friday 认为这项任务需要它，加入后继续":FRString(suggestion[@"reason"]) accept:job?@"加入并继续":@"加入"];
        self.suggestionBar.accept=^{[weak acceptSuggestion];};
        self.suggestionBar.dismiss=^{[weak.dismissedSuggestions addObject:FRString(weak.suggestion[@"key"])];[weak hideSuggestion];};
    }
    if(self.suggestionBar.hidden){[UIView performWithoutAnimation:^{self.suggestionBar.hidden=NO;[self.view layoutIfNeeded];}];UIAccessibilityPostNotification(UIAccessibilityLayoutChangedNotification,self.suggestionBar);}
}
- (void)hideSuggestion {self.suggestion=nil;if(!self.suggestionBar.hidden)[UIView performWithoutAnimation:^{self.suggestionBar.hidden=YES;[self.view layoutIfNeeded];}];[self updateJobSuggestionIfIdle];}
- (void)acceptSuggestion {
    NSDictionary *suggestion=self.suggestion;[self addAgent:suggestion[@"id"]];[self hideSuggestion];
    // The task is waiting on this answer: continue it with the agent invited.
    if([suggestion[@"kind"] isEqual:@"job"]){[self.dismissedSuggestions addObject:FRString(suggestion[@"key"])];[self submitText:[NSString stringWithFormat:@"好的，请%@加入，继续刚才的任务。",[self agentNamed:suggestion[@"id"]][@"name"]]];}
}
- (void)removeTypedMention {NSString *text=self.input.text;if([text hasSuffix:@"@"]||[text hasSuffix:@"＠"]){self.input.text=[text substringToIndex:text.length-1];[self textViewDidChange:self.input];}}
- (void)showMentionPicker {
    for(NSDictionary *agent in self.agentCatalog)if(![self.agents containsObject:agent[@"id"]]){[self showSuggestion:@{@"id":agent[@"id"],@"kind":@"mention",@"key":@"mention"}];return;}
}
- (void)scheduleSuggestionCheck {
    [self.suggestTimer invalidate];NSString *text=[self.input.text stringByTrimmingCharactersInSet:NSCharacterSet.whitespaceAndNewlineCharacterSet];
    if([self.suggestion[@"kind"] isEqual:@"typing"]&&text.length<4)[self hideSuggestion];
    if(text.length<4)return;__weak typeof(self) weak=self;
    self.suggestTimer=[NSTimer scheduledTimerWithTimeInterval:.7 repeats:NO block:^(NSTimer *t){[weak checkSuggestion:text];}];
}
- (void)checkSuggestion:(NSString *)text {
    if([self.suggestion[@"kind"] isEqual:@"mention"])return;
    [FRAPI.shared call:@"/v1/agents/suggest" method:@"POST" body:@{@"text":text,@"agents":[self.agents copy]} completion:^(NSDictionary *value,NSError *error){
        if(error||![[self.input.text stringByTrimmingCharactersInSet:NSCharacterSet.whitespaceAndNewlineCharacterSet] isEqual:text])return;
        NSDictionary *found=[value[@"suggestion"] isKindOfClass:NSDictionary.class]?value[@"suggestion"]:nil;NSString *key=[@"typing:" stringByAppendingString:FRString(found[@"id"])];
        if(found&&[self agentNamed:found[@"id"]]&&![self.agents containsObject:found[@"id"]]&&![self.dismissedSuggestions containsObject:key]){NSMutableDictionary *s=[found mutableCopy];s[@"kind"]=@"typing";s[@"key"]=key;[self showSuggestion:s];}
        else if([self.suggestion[@"kind"] isEqual:@"typing"])[self hideSuggestion];
    }];
}
- (void)updateJobSuggestionIfIdle {if(!self.suggestion)[self updateJobSuggestion];}
// Friday asked for an agent it may not open by itself: offer it, never switch silently.
- (void)updateJobSuggestion {
    if(self.suggestion&&![self.suggestion[@"kind"] isEqual:@"job"])return;
    NSDictionary *latest=nil;for(NSDictionary *job in self.jobs.reverseObjectEnumerator)if([job[@"kind"] isEqual:@"chat"]&&!(job[@"parent_job_id"]&&job[@"parent_job_id"]!=NSNull.null)){latest=job;break;}
    NSString *aid=FRString(latest[@"live"][@"suggested"]);if(!aid.length&&[latest[@"options"] isKindOfClass:NSDictionary.class])aid=FRString(latest[@"options"][@"suggested"]);
    NSString *key=[@"job:" stringByAppendingString:FRString(latest[@"id"])];
    if(aid.length&&[self agentNamed:aid]&&![self.agents containsObject:aid]&&![self.dismissedSuggestions containsObject:key]){if(![self.suggestion[@"key"] isEqual:key])[self showSuggestion:@{@"id":aid,@"kind":@"job",@"key":key}];}
    else if([self.suggestion[@"kind"] isEqual:@"job"]){self.suggestion=nil;[UIView performWithoutAnimation:^{self.suggestionBar.hidden=YES;}];}
}
- (void)openArtifact:(NSDictionary *)job button:(UIButton *)button {
    FRSetButtonBusy(button,YES);NSString *path=job[@"artifact_url"];
    if(FRString(job[@"artifact_version"]).length)path=[path stringByAppendingFormat:@"?version=%@",FRSegment(job[@"artifact_version"])];
    [FRAPI.shared download:path filename:FRString(job[@"artifact_filename"]) completion:^(NSURL *file,NSError *error){
        FRSetButtonBusy(button,NO);if(error){if(error.code!=NSURLErrorCancelled)FRShowError(self,error);return;}
        if(!self.view.window||self.presentedViewController)return;
        self.preview=file;QLPreviewController *preview=[QLPreviewController new];preview.dataSource=self;[self presentViewController:preview animated:YES completion:nil];
    }];
}
- (void)refreshMediaRow:(NSString *)jid {
    NSUInteger row=[[self.jobs valueForKey:@"id"] indexOfObject:jid];if(row==NSNotFound)return;
    CGPoint position=self.table.contentOffset;[UIView performWithoutAnimation:^{[self.table reloadRowsAtIndexPaths:@[[NSIndexPath indexPathForRow:row inSection:0]] withRowAnimation:UITableViewRowAnimationNone];[self.table layoutIfNeeded];self.table.contentOffset=position;}];
}
- (void)loadMedia:(NSDictionary *)job path:(NSString *)path {
    NSString *jid=job[@"id"];if([self.loadingMedia containsObject:jid])return;
    BOOL retry=self.mediaErrors[jid]!=nil;[self.mediaErrors removeObjectForKey:jid];[self.loadingMedia addObject:jid];
    // Defer row refresh: the first load may originate while a cell is being built.
    if(retry)dispatch_async(dispatch_get_main_queue(),^{[self refreshMediaRow:jid];});
    NSUInteger generation=FRAPI.shared.generation;
    [FRAPI.shared download:path filename:FRString(job[@"artifact_filename"]) completion:^(NSURL *file,NSError *e){
        [self.loadingMedia removeObject:jid];if(generation!=FRAPI.shared.generation)return;
        if(!e&&[FRString(job[@"artifact_media_type"]) hasPrefix:@"image/"]&&![UIImage imageWithContentsOfFile:file.path]){[FRAPI.shared invalidateMedia:path];e=[NSError errorWithDomain:@"FridayMedia" code:1 userInfo:@{NSLocalizedDescriptionKey:@"图片内容无法读取，请点按重试。"}];file=nil;}
        if(e)self.mediaErrors[jid]=[NSString stringWithFormat:@"%@（%ld）",e.localizedDescription,(long)e.code];else{[self.mediaErrors removeObjectForKey:jid];self.mediaFiles[path]=file;}
        [self refreshMediaRow:jid];
    }];
}
- (NSInteger)numberOfPreviewItemsInPreviewController:(QLPreviewController *)controller{return self.preview?1:0;}
- (id<QLPreviewItem>)previewController:(QLPreviewController *)controller previewItemAtIndex:(NSInteger)index{return self.preview;}
- (void)attachments {} // The attachment button presents its native menu on primary action.
- (void)configureAttachmentMenu {
    __weak typeof(self) weak=self;
    UIAction *file=[UIAction actionWithTitle:@"添加文件" image:FRIcon(@"paperclip",18) identifier:nil handler:^(UIAction *a){UIDocumentPickerViewController *picker=[[UIDocumentPickerViewController alloc] initForOpeningContentTypes:@[UTTypeItem] asCopy:YES];picker.delegate=weak;[weak presentViewController:picker animated:YES completion:nil];}];
    UIAction *files=[UIAction actionWithTitle:@"会话文件" image:FRIcon(@"folder",18) identifier:nil handler:^(UIAction *a){FRFiles(weak,weak.sid);}];
    UIAction *skills=[UIAction actionWithTitle:@"技能与资源" image:FRIcon(@"slider.horizontal.3",18) identifier:nil handler:^(UIAction *a){[weak settings];}];
    UIAction *voice=[UIAction actionWithTitle:@"语音留言" image:FRIcon(@"mic",18) identifier:nil handler:^(UIAction *a){[weak voice];}];
    self.attachmentButton.menu=[UIMenu menuWithChildren:@[file,files,skills,voice]];self.attachmentButton.showsMenuAsPrimaryAction=YES;
}
- (void)documentPicker:(UIDocumentPickerViewController *)controller didPickDocumentsAtURLs:(NSArray<NSURL *> *)urls {NSString *sid=[self.sid copy];if(!urls.count)return;FRSetButtonBusy(self.attachmentButton,YES);[FRAPI.shared upload:urls[0] session:sid completion:^(id v,NSError *e){FRSetButtonBusy(self.attachmentButton,NO);if(e)FRShowError(self,e);else FRFiles(self,sid);}];}
@end

static void FRFiles(UIViewController *vc,NSString *sid){FRListController *list=[FRListController new];list.title=@"对话文件";list.defaultIcon=@"doc";list.emptyTitle=@"还没有文件";list.emptyDetail=@"在对话里点 + 添加文件，或让 Friday 生成。";__weak FRListController *weak=list;
    list.reloadAction=^{[FRAPI.shared get:[FRAPI.shared pathForSession:sid suffix:@"/files"] completion:^(NSDictionary *v,NSError *e){if(e){FRShowError(weak,e);return;}NSMutableArray *rows=[NSMutableArray array];
        for(NSDictionary *f in v[@"items"]){NSString *name=f[@"name"];NSString *path=[FRAPI.shared pathForSession:sid suffix:[@"/files/" stringByAppendingString:FRSegment(name)]];
            NSString *mime=FRString(f[@"media_type"]);NSMutableDictionary *row=FRMutableRow(name,[NSByteCountFormatter stringFromByteCount:[f[@"size"] longLongValue] countStyle:NSByteCountFormatterCountStyleFile],^{if(weak.busyTitle)return;weak.busyTitle=name;[weak updateState];[FRAPI.shared download:path filename:name completion:^(NSURL *url,NSError *e){weak.busyTitle=nil;[weak updateState];if(e){FRShowError(weak,e);return;}[weak presentPreview:url];}];});
            row[@"icon"]=[mime hasPrefix:@"image/"]?@"photo":([mime hasPrefix:@"video/"]?@"play.rectangle":([mime hasPrefix:@"audio/"]?@"waveform":@"doc"));
            row[@"delete"]=^{FRAskDestructive(weak,@"删除这个文件？",[name stringByAppendingString:@"\n删除后 Friday 也无法再读取它，无法恢复。"],@"删除",^{[weak hideRows:@[path]];[FRAPI.shared call:path method:@"DELETE" body:nil completion:^(id r,NSError *x){if(x){FRShowError(weak,x);[weak unhideRows:@[path]];}[FRAPI.shared invalidateMedia:path];[weak reloadData];}];});};row[@"key"]=path;
            [rows addObject:row];}
        weak.sections=rows.count?@[@{@"rows":rows,@"footer":@"左滑可删除文件。"}]:@[];}];};[vc.navigationController pushViewController:list animated:YES];}

static void FRBindings(UIViewController *vc,NSString *sid){FRListController *list=[FRListController new];list.title=@"可访问资源";list.defaultIcon=@"lock.shield";__weak FRListController *weak=list;
list.reloadAction=^{[FRAPI.shared get:@"/v1/connectors" completion:^(NSDictionary *connections,NSError *e){if(e){FRShowError(weak,e);return;}[FRAPI.shared get:[FRAPI.shared pathForSession:sid suffix:@"/connectors"] completion:^(NSDictionary *v,NSError *e){if(e){FRShowError(weak,e);return;}NSMutableArray *bindings=[NSMutableArray arrayWithArray:v[@"items"]?:@[]];NSMutableArray *rows=[NSMutableArray array];for(NSDictionary *c in connections[@"items"]){NSString *cid=c[@"id"];NSDictionary *binding;for(NSDictionary *b in bindings)if([b[@"connection_id"] isEqual:cid])binding=b;NSDictionary *current=binding?:@{@"connection_id":cid,@"resource_ids":@[],@"can_create":@NO};
[rows addObject:FRRow(c[@"display_name"],[NSString stringWithFormat:@"%lu 个文件 · %@",(unsigned long)[current[@"resource_ids"] count],[current[@"can_create"] boolValue]?@"可创建":@"只访问所选文件"],^{UIAlertController *a=[UIAlertController alertControllerWithTitle:c[@"display_name"] message:@"此设置仅作用于当前会话。" preferredStyle:UIAlertControllerStyleActionSheet];
[a addAction:[UIAlertAction actionWithTitle:@"从 Google Drive 选择" style:UIAlertActionStyleDefault handler:^(UIAlertAction *x){[FRAPI.shared call:[NSString stringWithFormat:@"/v1/connectors/%@/picker",FRSegment(cid)] method:@"POST" body:@{@"session_id":sid,@"revision":v[@"revision"]} completion:^(NSDictionary *r,NSError *e){if(e)FRShowError(weak,e);else FRPresentBrowser(weak,r[@"url"],NO);}];}]];
[a addAction:[UIAlertAction actionWithTitle:[current[@"can_create"] boolValue]?@"关闭创建文件权限":@"允许创建文件" style:UIAlertActionStyleDefault handler:^(UIAlertAction *x){NSMutableDictionary *changed=[current mutableCopy];changed[@"can_create"]=@(![current[@"can_create"] boolValue]);NSMutableArray *items=[NSMutableArray array];for(NSDictionary *b in bindings)if(![b[@"connection_id"] isEqual:cid])[items addObject:b];[items addObject:changed];[FRAPI.shared call:[FRAPI.shared pathForSession:sid suffix:@"/connectors"] method:@"PUT" body:@{@"bindings":items,@"revision":v[@"revision"]} completion:^(id r,NSError *e){if(e)FRShowError(weak,e);[weak reloadData];}];}]];
[a addAction:[UIAlertAction actionWithTitle:@"清除本会话授权" style:UIAlertActionStyleDestructive handler:^(UIAlertAction *x){NSMutableArray *items=[NSMutableArray array];for(NSDictionary *b in bindings)if(![b[@"connection_id"] isEqual:cid])[items addObject:b];[FRAPI.shared call:[FRAPI.shared pathForSession:sid suffix:@"/connectors"] method:@"PUT" body:@{@"bindings":items,@"revision":v[@"revision"]} completion:^(id r,NSError *e){if(e)FRShowError(weak,e);[weak reloadData];}];}]];[a addAction:[UIAlertAction actionWithTitle:@"取消" style:UIAlertActionStyleCancel handler:nil]];[weak presentViewController:a animated:YES completion:nil];})];}weak.rows=rows;[weak.tableView reloadData];}];}];};[vc.navigationController pushViewController:list animated:YES];}

static void FRFork(UIViewController *vc,NSString *sid){FRListController *list=[FRListController new];list.title=@"带入新会话";NSMutableSet *messages=[NSMutableSet set],*files=[NSMutableSet set];__weak FRListController *weak=list;
list.reloadAction=^{[FRAPI.shared get:[FRAPI.shared pathForSession:sid suffix:@"/messages"] completion:^(NSDictionary *v,NSError *e){if(e){FRShowError(weak,e);return;}[FRAPI.shared get:[FRAPI.shared pathForSession:sid suffix:@"/files"] completion:^(NSDictionary *f,NSError *e){if(e){FRShowError(weak,e);return;}NSMutableArray *rows=[NSMutableArray arrayWithObject:FRRow(@"创建新会话",@"仅复制所选内容与技能选择；不继承权限、审批或通话",^{FRPrompt(weak,@"新会话名称",@"",^(NSString *name){[FRAPI.shared call:[FRAPI.shared pathForSession:sid suffix:@"/fork"] method:@"POST" body:@{@"name":name,@"message_ids":messages.allObjects,@"file_names":files.allObjects} completion:^(NSDictionary *r,NSError *e){if(e)FRShowError(weak,e);else{[FRStore.shared refresh];FROpenSession(r[@"id"]);}}];});})];
for(NSDictionary *j in v[@"items"]){if([j[@"kind"] isEqual:@"imported"]||(j[@"parent_job_id"]&&j[@"parent_job_id"]!=NSNull.null))continue;NSString *key=j[@"id"];NSMutableDictionary *row=[FRRow(FRString(j[@"prompt"]),@"对话",^{if([messages containsObject:key])[messages removeObject:key];else[messages addObject:key];[weak reloadData];}) mutableCopy];row[@"checked"]=@([messages containsObject:key]);[rows addObject:row];}
for(NSDictionary *item in f[@"items"]){NSString *name=item[@"name"];NSMutableDictionary *row=[FRRow(name,@"文件",^{if([files containsObject:name])[files removeObject:name];else[files addObject:name];[weak reloadData];}) mutableCopy];row[@"checked"]=@([files containsObject:name]);[rows addObject:row];}weak.rows=rows;[weak.tableView reloadData];}];}];};[vc.navigationController pushViewController:list animated:YES];}

static NSString *FREvolutionState(NSString *state){return @{@"staged":@"待确认",@"queued":@"等待验证",@"testing":@"正在验证",@"waiting_idle":@"等待任务结束",@"applying":@"正在切换",@"succeeded":@"升级完成",@"published":@"已发布",@"failed":@"验证未通过",@"rolled_back":@"已恢复旧版",@"rollback_failed":@"需要本机恢复"}[state]?:state;}
static void FRCodePreview(UIViewController *vc,NSString *title,NSString *content){UIViewController *detail=[UIViewController new];detail.title=title;UITextView *text=[[UITextView alloc] initWithFrame:detail.view.bounds];text.autoresizingMask=UIViewAutoresizingFlexibleWidth|UIViewAutoresizingFlexibleHeight;text.editable=NO;text.font=[UIFont monospacedSystemFontOfSize:13 weight:UIFontWeightRegular];text.text=content;text.backgroundColor=UIColor.systemBackgroundColor;text.textColor=UIColor.labelColor;text.textContainerInset=UIEdgeInsetsMake(16,12,16,12);[detail.view addSubview:text];[vc.navigationController pushViewController:detail animated:YES];}
static void FREvolutionDetail(UIViewController *vc,NSString *sid,NSString *rid){
    FRListController *list=[FRListController new];list.title=@"改动详情";list.defaultIcon=@"doc.text";__weak FRListController *weak=list;
    NSString *path=[FRAPI.shared pathForSession:sid suffix:[@"/evolution/" stringByAppendingString:rid]];
    list.reloadAction=^{[FRAPI.shared get:path completion:^(NSDictionary *item,NSError *error){if(error){FRShowError(weak,error);return;}
        NSMutableArray *rows=[NSMutableArray arrayWithObjects:FRRow(item[@"name"],FREvolutionState(item[@"state"]),nil),FRRow(@"改动说明",item[@"summary"],^{FRCodePreview(weak,@"改动说明",item[@"summary"]);}),nil];
        NSDictionary *files=item[@"files"];for(NSString *name in [[files allKeys] sortedArrayUsingSelector:@selector(compare:)])[rows addObject:FRRow(name,@"查看完整内容",^{FRCodePreview(weak,name,files[name]);})];
        [rows addObject:FRRow(@"验证记录",@"静态检查不代表代码安全或功能已通过",^{FRCodePreview(weak,@"验证记录",FRString(item[@"report"]));})];
        void(^submit)(NSString *)=^(NSString *action){[FRAPI.shared call:path method:@"POST" body:@{@"action":action,@"revision":rid} completion:^(NSDictionary *result,NSError *e){if(e)FRShowError(weak,e);else if([action isEqual:@"rollback_draft"])FREvolutionDetail(weak,sid,result[@"id"]);else[weak reloadData];}];};
        if([item[@"state"] isEqual:@"staged"]){BOOL code=![item[@"kind"] isEqual:@"skill"];NSString *message=code?@"确认后将在 Mac 上执行这份代码。核心升级通过测试后才切换，启动失败会恢复旧版。请先查看文件内容。":@"更新共享技能库。已开始的任务保留旧版；各会话的启用清单保持独立。";[rows addObject:FRRow(code?@"确认并验证":@"确认发布",[rid substringToIndex:MIN(12,rid.length)],^{FRConfirm(weak,@"发布这个版本？",message,^{submit(@"publish");});})];}
        if([item[@"state"] isEqual:@"published"]||[item[@"state"] isEqual:@"succeeded"])[rows addObject:FRRow([item[@"kind"] isEqual:@"core"]?@"撤销此次升级":@"恢复此版本",@"生成新的回退草案，确认后生效",^{submit(@"rollback_draft");})];
        weak.rows=rows;[weak.tableView reloadData];}];};[vc.navigationController pushViewController:list animated:YES];
}
static void FREvolution(UIViewController *vc,NSString *sid){
    FRListController *list=[FRListController new];list.title=@"能力与升级";list.defaultIcon=@"square.stack.3d.up";__weak FRListController *weak=list;
    list.reloadAction=^{[FRAPI.shared get:[FRAPI.shared pathForSession:sid suffix:@"/evolution"] completion:^(NSDictionary *value,NSError *error){if(error){FRShowError(weak,error);return;}
        NSMutableArray *rows=[NSMutableArray arrayWithObjects:FRRow(@"创建或改进能力",@"描述需要的技能、MCP 或后端改动",^{FRPrompt(weak,@"希望 Friday 学会什么？",@"",^(NSString *text){NSString *request=[@"请创建或改进以下能力，先生成可审阅的升级草案，说明测试方法，再请求我确认发布：\n" stringByAppendingString:text];[FRStore.shared sendText:request session:sid completion:^(id result,NSError *e){if(e)FRShowError(weak,e);else FROpenSession(sid);}];});}),FRRow(@"核心升级器",[value[@"updater_online"] boolValue]?@"在线 · 验证、切换与恢复独立运行":@"未连接 · 核心升级暂停",nil),nil];
        NSDictionary *binding=value[@"mcp"];NSArray *names=binding[@"names"]?:@[];
        for(NSDictionary *entry in value[@"catalog"]){if(![entry[@"kind"] isEqual:@"mcp"])continue;NSString *name=entry[@"name"];BOOL enabled=[names containsObject:name];NSMutableDictionary *row=[FRRow(name,enabled?@"MCP · 已在此会话启用":@"MCP · 未启用",^{FRConfirm(weak,enabled?@"停用此 MCP？":@"允许此会话使用 MCP？",enabled?@"后续调用会立即停止。":@"本机 MCP 是受信任代码，会在 Mac 上执行。新任务使用当前已发布版本。",^{NSMutableArray *updated=[names mutableCopy];if(enabled)[updated removeObject:name];else[updated addObject:name];[FRAPI.shared call:[FRAPI.shared pathForSession:sid suffix:@"/mcp"] method:@"PUT" body:@{@"names":updated,@"revision":binding[@"revision"]} completion:^(id result,NSError *e){if(e)FRShowError(weak,e);[weak reloadData];}];});}) mutableCopy];row[@"checked"]=@(enabled);[rows addObject:row];}
        for(NSDictionary *item in value[@"items"]){NSString *detail=[NSString stringWithFormat:@"%@ · %@",FREvolutionState(item[@"state"]),item[@"summary"]];[rows addObject:FRRow(item[@"name"],detail,^{FREvolutionDetail(weak,sid,item[@"id"]);})];}
        weak.rows=rows;[weak.tableView reloadData];}];};[vc.navigationController pushViewController:list animated:YES];
}

static void FRSessionSettings(UIViewController *vc,NSDictionary *session){if(!session)return;NSString *sid=session[@"id"];FRListController *list=[FRListController new];list.title=@"对话设置";list.compactRows=YES;list.defaultIcon=@"slider.horizontal.3";__weak FRListController *weak=list;
list.reloadAction=^{[FRAPI.shared get:[FRAPI.shared pathForSession:sid suffix:@""] completion:^(NSDictionary *s,NSError *e){if(e){FRShowError(weak,e);return;}void(^patch)(NSDictionary *)=^(NSDictionary *change){NSMutableDictionary *body=[change mutableCopy];body[@"revision"]=s[@"revision"];[FRAPI.shared call:[FRAPI.shared pathForSession:sid suffix:@""] method:@"PATCH" body:body completion:^(id v,NSError *e){if(e)FRShowError(weak,e);[FRStore.shared refresh];[weak reloadData];}];};
NSMutableDictionary *name=FRMutableRow(@"名称",@"",^{FRPrompt(weak,@"重命名对话",s[@"name"],^(NSString *name){patch(@{@"name":name});});});name[@"value"]=s[@"name"]?:@"";
NSArray *skillNames=s[@"skill_names"]?:@[];NSMutableDictionary *skillsRow=FRMutableRow(@"技能",@"",^{
    FRListController *skills=[FRListController new];skills.title=@"启用的技能";skills.defaultIcon=@"square.stack.3d.up";
    NSMutableSet *enabled=[NSMutableSet setWithArray:s[@"skill_names"]?:@[]];__weak FRListController *w=skills;
    __block NSArray *catalog=nil;
    skills.navigationItem.rightBarButtonItem=[[UIBarButtonItem alloc] initWithPrimaryAction:[UIAction actionWithTitle:@"保存" image:nil identifier:nil handler:^(UIAction *a){[FRAPI.shared call:[FRAPI.shared pathForSession:sid suffix:@"/skills"] method:@"PUT" body:@{@"skill_names":enabled.allObjects,@"revision":s[@"revision"]} completion:^(id r,NSError *e){if(e)FRShowError(w,e);else{[FRStore.shared refresh];[w.navigationController popViewControllerAnimated:YES];}}];}]];
    skills.navigationItem.rightBarButtonItem.style=UIBarButtonItemStyleDone;
    void(^render)(void)=^{NSMutableArray *rows=[NSMutableArray array];
        for(NSDictionary *skill in catalog){NSString *name=skill[@"name"];NSMutableDictionary *row=FRMutableRow(name,skill[@"description"],^{if([enabled containsObject:name])[enabled removeObject:name];else[enabled addObject:name];[w reloadData];});row[@"checked"]=@([enabled containsObject:name]);[rows addObject:row];}w.sections=@[@{@"rows":rows,@"footer":@"只影响这个对话之后的新任务。"}];};
    skills.reloadAction=^{if(catalog){render();return;}[FRAPI.shared get:@"/v1/skills" completion:^(NSDictionary *v,NSError *e){if(e){FRShowError(w,e);return;}catalog=v[@"items"]?:@[];render();}];};[weak.navigationController pushViewController:skills animated:YES];});
skillsRow[@"value"]=skillNames.count?[NSString stringWithFormat:@"%lu 个",(unsigned long)skillNames.count]:@"未开启";
NSMutableArray *sections=[NSMutableArray arrayWithObjects:
 @{@"rows":@[name,skillsRow,FRRow(@"可访问资源",@"独立设置连接器范围",^{FRBindings(weak,sid);})]},
 @{@"rows":@[FRRow(@"会话文件",@"此工作空间的文件",^{FRFiles(weak,sid);}),FRRow(@"从这里新建会话",@"选择要带入的对话和文件",^{FRFork(weak,sid);})]},nil];
if([FRAPI.shared.credentials[@"pairing"][@"account"][@"is_admin"] boolValue]){
    NSMutableArray *mac=[NSMutableArray array];NSMutableDictionary *management=FRMutableRow(@"Mac 管理",@"",^{FRConfirm(weak,[s[@"management"] boolValue]?@"关闭管理权限？":@"允许此会话管理 Mac？",@"允许此会话使用 Mac 管理工具，以及经你确认的技能、MCP 与核心升级。",^{patch(@{@"management":@(![s[@"management"] boolValue])});});});management[@"value"]=[s[@"management"] boolValue]?@"已开启":@"未开启";[mac addObject:management];
    if([s[@"management"] boolValue])[mac addObject:FRRow(@"能力与升级",@"创建技能与 MCP，审阅和恢复版本",^{FREvolution(weak,sid);})];
    if([s[@"management"] boolValue])[mac addObject:FRRow(@"Mac 状态",@"诊断服务与设备",^{FRListController *detail=[FRListController new];detail.title=@"Mac 状态";detail.defaultIcon=@"desktopcomputer";__weak FRListController *d=detail;detail.reloadAction=^{[FRAPI.shared get:[FRAPI.shared pathForSession:sid suffix:@"/host/status"] completion:^(NSDictionary *v,NSError *e){if(e){FRShowError(d,e);return;}NSMutableArray *items=[NSMutableArray array];for(NSString *key in [[v allKeys] sortedArrayUsingSelector:@selector(compare:)])[items addObject:FRRow(key,FRString(v[key]),nil)];d.rows=items;}];};[weak.navigationController pushViewController:detail animated:YES];})];
    [sections addObject:@{@"title":@"Mac",@"rows":mac}];}
NSMutableDictionary *reset=FRMutableRow(@"清空上下文",@"",^{FRAskDestructive(weak,@"清空这个对话的上下文？",@"之后的新消息不再参考此前的内容，已有消息会从列表中隐藏。",@"清空",^{[FRAPI.shared call:[FRAPI.shared pathForSession:sid suffix:@"/reset"] method:@"POST" body:@{} completion:^(id v,NSError *e){if(e)FRShowError(weak,e);else{[FRAPI.shared.cache remove:[@"chat:" stringByAppendingString:sid]];[FRStore.shared refresh];FRToast(weak,@"已清空上下文",nil,nil);}}];});});
NSMutableDictionary *archive=FRMutableRow([s[@"archived"] boolValue]?@"取消归档":@"归档对话",@"",^{patch(@{@"archived":@(![s[@"archived"] boolValue])});});
NSMutableDictionary *remove=FRMutableRow(@"删除对话",@"",^{UINavigationController *nav=weak.navigationController;UIViewController *root=nav.viewControllers.firstObject;[nav popToRootViewControllerAnimated:YES];FRDeleteSessions(root,@[sid],nil);});remove[@"destructive"]=@YES;
[sections addObject:@{@"rows":@[archive,reset],@"footer":@"归档不会停止正在执行的任务。"}];
[sections addObject:@{@"rows":@[remove],@"footer":@"删除的对话会在「我的 › 最近删除」保留 30 天。"}];
weak.sections=sections;}];};[vc.navigationController pushViewController:list animated:YES];}

// UI 2.0 voice: one dark full screen for the call and for a voice message. Leaving it never ends the call.
@interface FRVoiceController : UIViewController
@property(nonatomic) NSDictionary *workspace;
@property(nonatomic) BOOL draft;
@property(nonatomic) UILabel *heading,*status,*heard,*footer,*failure;
@property(nonatomic) UIStackView *wave,*controls;
@property(nonatomic) NSTimer *animation;
@property(nonatomic) NSDate *callStarted;
@end
@implementation FRVoiceController
- (UIColor *)ink {return [UIColor colorWithWhite:.96 alpha:1];}
- (UIColor *)go {return [UIColor colorWithRed:.184 green:.271 blue:1 alpha:1];}
- (UIColor *)quiet {return [UIColor colorWithRed:.64 green:.64 blue:.68 alpha:1];}
- (void)viewDidLoad {
    [super viewDidLoad];self.view.backgroundColor=[UIColor colorWithRed:.067 green:.067 blue:.075 alpha:1];self.overrideUserInterfaceStyle=UIUserInterfaceStyleDark;self.view.accessibilityIdentifier=@"voice.screen";
    UIButton *close=FRButton(@"chevron.down",@"收起，通话继续",^{[self close];});close.tintColor=self.ink;
    self.heading=FRText(@"",FRFont(16,UIFontWeightSemibold),self.ink,1);self.heading.textAlignment=NSTextAlignmentCenter;
    self.status=FRText(@"",FRMono(12),self.quiet,1);self.status.textAlignment=NSTextAlignmentCenter;
    UIStackView *titles=FRStack(@[self.heading,self.status],UILayoutConstraintAxisVertical,2);
    UIView *balance=[UIView new];[balance.widthAnchor constraintEqualToConstant:44].active=YES;
    UIStackView *top=FRStack(@[close,titles,balance],UILayoutConstraintAxisHorizontal,0);top.alignment=UIStackViewAlignmentCenter;
    self.wave=FRStack(@[],UILayoutConstraintAxisHorizontal,6);self.wave.alignment=UIStackViewAlignmentCenter;self.wave.isAccessibilityElement=NO;
    for(NSInteger i=0;i<9;i++){UIView *bar=[UIView new];bar.backgroundColor=self.ink;bar.layer.cornerRadius=3;bar.translatesAutoresizingMaskIntoConstraints=NO;[bar.widthAnchor constraintEqualToConstant:6].active=YES;NSLayoutConstraint *h=[bar.heightAnchor constraintEqualToConstant:12];h.identifier=@"height";h.active=YES;[self.wave addArrangedSubview:bar];}
    UIView *waveBox=[UIView new];self.wave.translatesAutoresizingMaskIntoConstraints=NO;[waveBox addSubview:self.wave];[NSLayoutConstraint activateConstraints:@[[self.wave.centerXAnchor constraintEqualToAnchor:waveBox.centerXAnchor],[self.wave.centerYAnchor constraintEqualToAnchor:waveBox.centerYAnchor],[waveBox.heightAnchor constraintEqualToConstant:100]]];
    self.heard=FRText(@"",FRFont(18,UIFontWeightRegular),self.ink,0);self.heard.accessibilityIdentifier=@"voice.heard";
    self.failure=FRText(@"",FRFont(14,UIFontWeightRegular),[UIColor colorWithRed:1 green:.71 blue:.36 alpha:1],0);self.failure.textAlignment=NSTextAlignmentCenter;
    self.controls=FRStack(@[],UILayoutConstraintAxisHorizontal,28);self.controls.alignment=UIStackViewAlignmentCenter;
    self.footer=FRText(@"",FRFont(12,UIFontWeightRegular),self.quiet,0);self.footer.textAlignment=NSTextAlignmentCenter;
    UIStackView *bottom=FRStack(@[self.failure,self.controls,self.footer],UILayoutConstraintAxisVertical,18);bottom.alignment=UIStackViewAlignmentCenter;
    for(UIView *v in @[top,waveBox,self.heard,bottom]){v.translatesAutoresizingMaskIntoConstraints=NO;[self.view addSubview:v];}
    UILayoutGuide *safe=self.view.safeAreaLayoutGuide;
    [NSLayoutConstraint activateConstraints:@[[top.topAnchor constraintEqualToAnchor:safe.topAnchor constant:4],[top.leadingAnchor constraintEqualToAnchor:safe.leadingAnchor constant:8],[top.trailingAnchor constraintEqualToAnchor:safe.trailingAnchor constant:-8],
        [waveBox.topAnchor constraintEqualToAnchor:top.bottomAnchor constant:72],[waveBox.leadingAnchor constraintEqualToAnchor:safe.leadingAnchor],[waveBox.trailingAnchor constraintEqualToAnchor:safe.trailingAnchor],
        [self.heard.topAnchor constraintEqualToAnchor:waveBox.bottomAnchor constant:40],[self.heard.leadingAnchor constraintEqualToAnchor:safe.leadingAnchor constant:24],[self.heard.trailingAnchor constraintEqualToAnchor:safe.trailingAnchor constant:-24],[self.heard.bottomAnchor constraintLessThanOrEqualToAnchor:bottom.topAnchor constant:-16],
        [bottom.leadingAnchor constraintEqualToAnchor:safe.leadingAnchor constant:24],[bottom.trailingAnchor constraintEqualToAnchor:safe.trailingAnchor constant:-24],[bottom.bottomAnchor constraintEqualToAnchor:safe.bottomAnchor constant:-20]]];
    [[NSNotificationCenter defaultCenter] addObserver:self selector:@selector(reloadData) name:FRVoiceChanged object:nil];[self reloadData];
}
- (void)viewWillAppear:(BOOL)animated {[super viewWillAppear:animated];__weak typeof(self) weak=self;self.animation=[NSTimer scheduledTimerWithTimeInterval:.18 repeats:YES block:^(NSTimer *t){[weak tick];}];}
- (void)viewWillDisappear:(BOOL)animated {[super viewWillDisappear:animated];[self.animation invalidate];self.animation=nil;}
- (void)viewDidDisappear:(BOOL)animated {[super viewDidDisappear:animated];if(self.draft)[FRVoice.shared discardRecording];}
- (void)dealloc {[[NSNotificationCenter defaultCenter] removeObserver:self];[_animation invalidate];}
- (void)close {if(self.draft)[FRVoice.shared discardRecording];[self dismissViewControllerAnimated:YES completion:nil];}
// Bars move only while someone is speaking; reduce motion holds them still.
- (void)tick {
    FRVoice *v=FRVoice.shared;NSString *state=v.status;BOOL speaking=[state isEqual:@"正在回答"],listening=[state isEqual:@"正在听"]||v.recording;
    if(v.sessionID&&self.callStarted){NSInteger s=(NSInteger)-self.callStarted.timeIntervalSinceNow;self.status.text=[NSString stringWithFormat:@"%@ %02ld:%02ld",state?:@"",(long)s/60,(long)s%60];}
    BOOL still=UIAccessibilityIsReduceMotionEnabled();
    [UIView animateWithDuration:.18 animations:^{
        NSInteger i=0;for(UIView *bar in self.wave.arrangedSubviews){CGFloat base=(CGFloat)(4-labs(i-4))/4;CGFloat h=12;
            if(!still&&speaking)h=24+arc4random_uniform(72)*(.4+.6*base);else if(!still&&listening)h=10+arc4random_uniform(18)*base;else if(v.sessionID||v.recording)h=12+16*base;
            for(NSLayoutConstraint *c in bar.constraints)if([c.identifier isEqual:@"height"])c.constant=h;i++;}
        [self.wave layoutIfNeeded];}];
}
- (UIButton *)round:(NSString *)symbol label:(NSString *)label fill:(UIColor *)fill size:(CGFloat)size action:(void(^)(void))action {
    UIButton *b=[UIButton buttonWithType:UIButtonTypeSystem];UIButtonConfiguration *c=UIButtonConfiguration.filledButtonConfiguration;c.image=FRIcon(symbol,size*.34);c.baseBackgroundColor=fill;c.baseForegroundColor=UIColor.whiteColor;c.cornerStyle=UIButtonConfigurationCornerStyleCapsule;b.configuration=c;
    b.accessibilityLabel=label;[b.widthAnchor constraintEqualToConstant:size].active=YES;[b.heightAnchor constraintEqualToConstant:size].active=YES;[b addAction:[UIAction actionWithHandler:^(UIAction *a){action();}] forControlEvents:UIControlEventTouchUpInside];return b;
}
- (UIView *)labelled:(UIButton *)button title:(NSString *)title {UILabel *l=FRText(title,FRFont(12,UIFontWeightRegular),self.quiet,1);l.isAccessibilityElement=NO;UIStackView *s=FRStack(@[button,l],UILayoutConstraintAxisVertical,8);s.alignment=UIStackViewAlignmentCenter;return s;}
- (void)reloadData {
    if(!self.isViewLoaded)return;FRVoice *v=FRVoice.shared;__weak typeof(self) weak=self;
    for(UIView *c in self.controls.arrangedSubviews)[c removeFromSuperview];
    UIColor *gray=[UIColor colorWithRed:.15 green:.15 blue:.17 alpha:1],*red=[UIColor colorWithRed:.9 green:.28 blue:.3 alpha:1];
    self.failure.text=v.failure;self.failure.hidden=!v.failure.length;
    NSString *here=FRString(self.workspace[@"name"]).length?self.workspace[@"name"]:@"这个对话";
    if(v.sessionID){
        if(!self.callStarted)self.callStarted=[NSDate date];
        self.heading.text=v.sessionName?:here;self.status.text=v.status;
        self.heard.text=v.transcript.length?[@"刚刚听到：" stringByAppendingString:v.transcript]:@"直接说话就行，Friday 说话时也可以打断它。";self.heard.textColor=v.transcript.length?self.ink:self.quiet;
        [self.controls addArrangedSubview:[self labelled:[self round:@"speaker.slash.fill" label:@"停止播报" fill:gray size:64 action:^{[v interrupt];}] title:@"停止播报"]];
        [self.controls addArrangedSubview:[self labelled:[self round:@"phone.down.fill" label:@"结束通话" fill:red size:76 action:^{[v stop];}] title:@"结束"]];
        if(self.workspace&&![v.sessionID isEqual:self.workspace[@"id"]])[self.controls addArrangedSubview:[self labelled:[self round:@"arrow.left.arrow.right" label:[NSString stringWithFormat:@"切换到%@",here] fill:gray size:64 action:^{FRConfirm(weak,@"切换语音会话？",@"当前通话将结束，再连接当前会话。",^{[v switchTo:weak.workspace];});}] title:@"切到这里"]];
        self.footer.text=[NSString stringWithFormat:@"通话绑定在「%@」· 锁屏后继续 · 收起不会挂断",v.sessionName?:here];
    }else if(self.draft){
        self.callStarted=nil;self.heading.text=here;self.status.text=v.recording?@"正在录音 · 最长 3 分钟":@"录好了";
        self.heard.text=v.recording?@"说完点停止，确认后再发送。":@"发送到开始录音时的对话，也可以丢弃重录。";self.heard.textColor=self.quiet;
        if(v.recording)[self.controls addArrangedSubview:[self labelled:[self round:@"stop.fill" label:@"停止录音" fill:red size:76 action:^{[v finishRecording];}] title:@"停止"]];
        else{[self.controls addArrangedSubview:[self labelled:[self round:@"trash" label:@"丢弃录音" fill:gray size:64 action:^{weak.draft=NO;[v discardRecording];[weak reloadData];}] title:@"丢弃"]];
            [self.controls addArrangedSubview:[self labelled:[self round:@"arrow.up" label:@"发送语音留言" fill:self.go size:76 action:^{[v sendRecording:^(id value,NSError *e){if(e)FRShowError(weak,e);else{weak.draft=NO;[FRStore.shared refresh];[weak close];}}];}] title:@"发送"]];}
        self.footer.text=@"语音留言会转成文字交给 Friday";
    }else{
        self.callStarted=nil;self.heading.text=here;self.status.text=@"语音";self.heard.text=@"实时通话在这台 Mac 上识别和合成语音，通话绑定在开始时的对话。";self.heard.textColor=self.quiet;
        [self.controls addArrangedSubview:[self labelled:[self round:@"mic.fill" label:@"录制语音留言" fill:gray size:64 action:^{weak.draft=YES;[v record:weak.workspace[@"id"] completion:^(id result,NSError *e){if(e){weak.draft=NO;FRShowError(weak,e);}[weak reloadData];}];[weak reloadData];}] title:@"语音留言"]];
        [self.controls addArrangedSubview:[self labelled:[self round:@"waveform" label:@"开始实时通话" fill:self.go size:76 action:^{[v start:weak.workspace];}] title:@"开始通话"]];
        self.footer.text=@"结束通话不会取消 Mac 上的任务";
    }
}
@end
static void FRVoiceSheet(UIViewController *vc,NSDictionary *session){if(!session)return;FRVoiceController *voice=[FRVoiceController new];voice.workspace=session;voice.modalPresentationStyle=UIModalPresentationFullScreen;voice.modalTransitionStyle=UIModalTransitionStyleCoverVertical;[vc presentViewController:voice animated:YES completion:nil];}

#if DEBUG && TARGET_OS_SIMULATOR
// The checks predate the system tab bar; they address the selected navigation stack.
@interface UITabBarController (FRVisible)
@property(nonatomic,readonly) UINavigationController *visibleController;
@end
@implementation UITabBarController (FRVisible)
- (UINavigationController *)visibleController {return (id)self.selectedViewController;}
@end
typedef UITabBarController FRCompactTabsController;
#include "../Tests/Navigation.inc"
#include "../Tests/Components.inc"
#include "../Tests/MarkdownUI.inc"
#include "../Tests/Interaction.inc"
#include "../Tests/Deletion.inc"
#endif

UIViewController *FRRootController(void){
    if(!FRAPI.shared.credentials){FRCurrentTabs=nil;FRPendingSession=nil;return [[UINavigationController alloc] initWithRootViewController:[NSClassFromString(@"FRLoginController") new]];}
    UITabBarController *tabs=[UITabBarController new];FRCurrentTabs=tabs;NSMutableArray *controllers=[NSMutableArray array];
    // Short drama starts inside conversations (@短剧助手); its projects live under 作品 with the rest of the output.
    // UI 2.0: three places. The Mac and account settings open from the status pill on 对话.
    NSArray *roots=@[[FRSessionsController new],[FRActivityController new],FRWorksRootController()],*titles=@[@"对话",@"进行中",@"作品"],*icons=@[@"bubble.left",@"waveform.path.ecg",@"square.grid.2x2"],*names=@[@"sessions",@"tasks",@"works"];
    for(NSUInteger i=0;i<roots.count;i++){UIViewController *vc=roots[i];UINavigationController *nav=[[UINavigationController alloc] initWithRootViewController:vc];nav.navigationBar.prefersLargeTitles=YES;nav.tabBarItem=[[UITabBarItem alloc] initWithTitle:titles[i] image:[UIImage systemImageNamed:icons[i]] tag:i];nav.tabBarItem.accessibilityIdentifier=[@"tabs." stringByAppendingString:names[i]];[controllers addObject:nav];}
    tabs.viewControllers=controllers;tabs.tabBar.tintColor=FRInk();
    if(@available(iOS 26.0,*))tabs.tabBarMinimizeBehavior=UITabBarMinimizeBehaviorOnScrollDown;
    [FRStore.shared start];
#if DEBUG && TARGET_OS_SIMULATOR
    if(FRDemoEnabled()&&[NSProcessInfo.processInfo.arguments containsObject:@"--friday-permission-preview"])dispatch_after(dispatch_time(DISPATCH_TIME_NOW,NSEC_PER_SEC),dispatch_get_main_queue(),^{BOOL real=[NSProcessInfo.processInfo.arguments containsObject:@"--friday-real"];FRPermissionController *sheet=[[FRPermissionController alloc] initWithTitle:@"操作需要你的授权" detail:real?@"将生成一段 10 秒视频（352×608），在本机排队渲染。\n\n画面描述：\n竖屏视频。宁静的海边日落，太阳缓缓沉向海平线，天空呈金橙到淡紫的渐变，海浪轻拍沙滩，镜头缓慢向前推进。\n\n首帧图片：无\n\n确认后开始渲染；想修改就拒绝，在聊天里说明要改什么。\n版本 3fa2c41b9e0d":@"将本周的想法整理为行动清单，并写入你选择的工作空间。\n\n目标文件\n工作空间 / 行动清单.md\n\n操作内容\n创建文档，保留每项决定的依据，并列出下一步。" context:@"会话：想法与日常\n仅批准此次操作，不授予长期权限。" allowTitle:@"允许此次操作" canDeny:YES];[tabs.selectedViewController presentViewController:sheet animated:NO completion:nil];});
    if(FRDemoEnabled()&&[NSProcessInfo.processInfo.arguments containsObject:@"--friday-components"])dispatch_async(dispatch_get_main_queue(),^{[(UINavigationController *)tabs.selectedViewController pushViewController:FRComponentGallery() animated:NO];});
    if(FRDemoEnabled()&&[NSProcessInfo.processInfo.arguments containsObject:@"--friday-interaction-check"])dispatch_after(dispatch_time(DISPATCH_TIME_NOW,NSEC_PER_SEC),dispatch_get_main_queue(),^{FRRunInteractionChecks(tabs);});
    if(FRDemoEnabled()&&[NSProcessInfo.processInfo.arguments containsObject:@"--friday-markdown-check"])dispatch_after(dispatch_time(DISPATCH_TIME_NOW,NSEC_PER_SEC),dispatch_get_main_queue(),^{FRRunMarkdownChecks(tabs);});
    if(FRDemoEnabled()&&[NSProcessInfo.processInfo.arguments containsObject:@"--friday-component-check"])dispatch_after(dispatch_time(DISPATCH_TIME_NOW,NSEC_PER_SEC),dispatch_get_main_queue(),^{FRRunComponentChecks(tabs);});
    if(FRDemoEnabled()&&[NSProcessInfo.processInfo.arguments containsObject:@"--friday-deletion-check"])dispatch_after(dispatch_time(DISPATCH_TIME_NOW,NSEC_PER_SEC),dispatch_get_main_queue(),^{FRRunDeletionChecks(tabs);});
    if(FRDemoEnabled()&&[NSProcessInfo.processInfo.arguments containsObject:@"--friday-navigation-check"])dispatch_after(dispatch_time(DISPATCH_TIME_NOW,NSEC_PER_SEC),dispatch_get_main_queue(),^{FRRunNavigationChecks(tabs);});
    if(FRDemoEnabled()&&[NSProcessInfo.processInfo.arguments containsObject:@"--friday-settings"])dispatch_async(dispatch_get_main_queue(),^{FRShowMac(tabs.selectedViewController);});
    if(FRDemoEnabled()&&[NSProcessInfo.processInfo.arguments containsObject:@"--friday-works"])tabs.selectedIndex=2;
    if(FRDemoEnabled()&&[NSProcessInfo.processInfo.arguments containsObject:@"--friday-trash"])dispatch_async(dispatch_get_main_queue(),^{FRShowMac(tabs.selectedViewController);[(UINavigationController *)tabs.selectedViewController.presentedViewController pushViewController:[FRTrashController new] animated:NO];});
    if(FRDemoEnabled()&&[NSProcessInfo.processInfo.arguments containsObject:@"--friday-dramas"])dispatch_async(dispatch_get_main_queue(),^{FRShowDramas(((UINavigationController *)tabs.viewControllers.firstObject).topViewController);});
    for(NSString *page in @[@"memory",@"schedules",@"drama",@"drama-shot",@"drama-bible"])if(FRDemoEnabled()&&[NSProcessInfo.processInfo.arguments containsObject:[@"--friday-page-" stringByAppendingString:page]]){
        BOOL drama=[page hasPrefix:@"drama"];if(drama)tabs.selectedIndex=2;dispatch_async(dispatch_get_main_queue(),^{UINavigationController *nav=(id)tabs.viewControllers[2];if(!drama){FRShowMac(tabs.selectedViewController);nav=(id)tabs.selectedViewController.presentedViewController;}FRDemoOpenAgentPage(nav,page);});}
    if(FRDemoEnabled()&&[NSProcessInfo.processInfo.arguments containsObject:@"--friday-tasks"])tabs.selectedIndex=1;
    if(FRDemoEnabled()&&[NSProcessInfo.processInfo.arguments containsObject:@"--friday-voice"])dispatch_after(dispatch_time(DISPATCH_TIME_NOW,NSEC_PER_SEC),dispatch_get_main_queue(),^{FRVoiceSheet(tabs.selectedViewController,[FRStore.shared session:@"demo-work"]?:@{@"id":@"demo-work",@"name":@"东京三日行程"});});
    if(FRDemoEnabled()&&[NSProcessInfo.processInfo.arguments containsObject:@"--friday-new-chat"])dispatch_after(dispatch_time(DISPATCH_TIME_NOW,500*NSEC_PER_MSEC),dispatch_get_main_queue(),^{[(FRSessionsController *)((UINavigationController *)tabs.viewControllers.firstObject).viewControllers.firstObject create];
        // Demo input for screenshots: a script pasted into the new conversation.
        if([NSProcessInfo.processInfo.arguments containsObject:@"--friday-sample-script"])dispatch_after(dispatch_time(DISPATCH_TIME_NOW,900*NSEC_PER_MSEC),dispatch_get_main_queue(),^{FRChatController *chat=(id)((UINavigationController *)tabs.viewControllers.firstObject).viewControllers.lastObject;if(![chat isKindOfClass:FRChatController.class])return;chat.input.text=@"第1场 内景·咖啡馆·夜\n林夏：还营业吗？\n店员：快打烊了。\n帮我做成一集短剧";[chat textViewDidChange:chat.input];});});
    if(FRDemoEnabled()&&[NSProcessInfo.processInfo.arguments containsObject:@"--friday-upgrades"])dispatch_async(dispatch_get_main_queue(),^{UINavigationController *nav=(id)tabs.viewControllers.firstObject;FREvolution(nav.topViewController,@"demo-mac");});
#endif
    return tabs;
}
void FROpenSession(NSString *sid){if(!sid.length)return;if(!FRAPI.shared.credentials){FRPendingSession=sid;return;}FRCurrentTabs.selectedIndex=0;UINavigationController *nav=(id)FRCurrentTabs.viewControllers.firstObject;[nav.presentedViewController dismissViewControllerAnimated:NO completion:nil];for(UIViewController *existing in nav.viewControllers){if([existing isKindOfClass:FRChatController.class]&&[((FRChatController *)existing).sid isEqual:sid]){[nav popToViewController:existing animated:YES];return;}}FRChatController *chat=[FRChatController new];chat.sid=sid;chat.hidesBottomBarWhenPushed=YES;[nav setViewControllers:@[nav.viewControllers.firstObject,chat] animated:YES];}
static void FRFocusOpenSession(void){UINavigationController *nav=(id)FRCurrentTabs.viewControllers.firstObject;FRChatController *chat=(id)nav.viewControllers.lastObject;if([chat isKindOfClass:FRChatController.class]){if(chat.view.window)[chat.input becomeFirstResponder];else chat.focusOnAppear=YES;}}
BOOL FRPushAvailable(void){return [@[@"sandbox",@"production"] containsObject:[NSBundle.mainBundle objectForInfoDictionaryKey:@"FridayPushEnvironment"]?:@""]; }
void FRSyncPushToken(void){if(!FRPushAvailable())return;NSString *token=[NSUserDefaults.standardUserDefaults stringForKey:@"FridayPushToken"];NSString *did=FRAPI.shared.credentials[@"pairing"][@"device_id"];NSString *environment=[NSBundle.mainBundle objectForInfoDictionaryKey:@"FridayPushEnvironment"];if(!token||!did||![@[@"sandbox",@"production"] containsObject:environment])return;[FRAPI.shared call:[NSString stringWithFormat:@"/v1/devices/%@/push",FRSegment(did)] method:@"POST" body:@{@"token":token,@"environment":environment} completion:^(id v,NSError *e){}];}
