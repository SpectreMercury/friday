// Screens for the new agent features: short-drama projects, memory, schedules,
// and the task progress list. Kept out of FRViews.m so each screen stays readable.
#import <UIKit/UIKit.h>
#import <AVKit/AVKit.h>
#import "FRAPI.h"
#import "FRList.h"
#import "FRViews.h"
#import "FRStore.h"
#import "FRChatComponents.h"

static NSString *FRDramaPath(NSString *pid,NSString *suffix){return [NSString stringWithFormat:@"/v1/dramas/%@%@",FRSegment(pid),suffix?:@""];}
static NSString *FRShotStatus(NSString *status){
    return @{@"planned":@"已规划",@"drafting":@"草稿渲染中",@"draft_review":@"待审草稿",@"approved":@"草稿通过",@"finalizing":@"正式渲染中",@"done":@"完成"}[status]?:status;
}
static NSString *FRStageText(NSString *stage){return @{@"setup":@"设定中",@"storyboard":@"分镜中",@"producing":@"制作中",@"ready":@"可导出"}[stage]?:stage;}
static NSString *FRJobText(NSString *status){return @{@"waiting":@"等待",@"queued":@"排队中",@"running":@"进行中",@"succeeded":@"已完成",@"failed":@"失败",@"cancelled":@"已取消"}[status]?:status;}
static NSString *FRMinutes(NSNumber *seconds){double s=seconds.doubleValue;return s<90?[NSString stringWithFormat:@"%.0f 秒",MAX(1,s)]:[NSString stringWithFormat:@"%.0f 分钟",round(s/60)];}
static NSMutableDictionary *FRStatusRow(NSString *title,NSString *detail,NSString *status,NSString *code,void(^action)(void)){
    NSMutableDictionary *row=[FRListRow(title,detail,action) mutableCopy];if(status.length){row[@"status"]=status;row[@"statusCode"]=code?:@"";}return row;
}

static NSString *FRRelativeDay(NSTimeInterval stamp){
    if(stamp<=0)return @"";NSDate *date=[NSDate dateWithTimeIntervalSince1970:stamp];NSCalendar *calendar=NSCalendar.currentCalendar;
    static NSDateFormatter *formatter;if(!formatter){formatter=[NSDateFormatter new];formatter.locale=[NSLocale localeWithLocaleIdentifier:@"zh_CN"];}
    if([calendar isDateInToday:date]){formatter.dateFormat=@"今天 HH:mm";}else if([calendar isDateInYesterday:date]){formatter.dateFormat=@"昨天 HH:mm";}else formatter.dateFormat=@"M月d日";
    return [formatter stringFromDate:date];
}
static UILabel *FRWorksLabel(NSString *text,UIFont *font,UIColor *color,NSInteger lines){UILabel *l=[UILabel new];l.text=text;l.font=font;l.textColor=color;l.numberOfLines=lines;l.adjustsFontForContentSizeCategory=YES;return l;}
static NSDictionary *FRIconRow(NSString *icon,NSDictionary *row){NSMutableDictionary *copy=[row mutableCopy];copy[@"icon"]=icon;return copy;}

#pragma mark - Progress list in a running task

NSArray<UIView *> *FRTodoViews(NSArray *items){
    NSMutableArray *views=[NSMutableArray array];
    for(NSDictionary *item in items){
        if(![item isKindOfClass:NSDictionary.class])continue;
        NSString *status=FRString(item[@"status"]);
        UILabel *label=[UILabel new];label.numberOfLines=0;label.font=[UIFont preferredFontForTextStyle:UIFontTextStyleFootnote];label.adjustsFontForContentSizeCategory=YES;
        NSString *mark=[status isEqual:@"completed"]?@"●":([status isEqual:@"in_progress"]?@"◐":@"○");
        label.text=[NSString stringWithFormat:@"%@  %@",mark,FRString(item[@"content"])];
        label.textColor=[status isEqual:@"in_progress"]?UIColor.labelColor:UIColor.secondaryLabelColor;
        label.accessibilityLabel=[NSString stringWithFormat:@"%@，%@",FRString(item[@"content"]),@{@"completed":@"已完成",@"in_progress":@"进行中",@"cancelled":@"已取消"}[status]?:@"待做"];
        [views addObject:label];
    }
    return views;
}

#pragma mark - Multi-line text editor

@interface FRTextEditor : UIViewController <UITextViewDelegate>
@property(nonatomic) UITextView *text;
@property(nonatomic,copy) NSString *initialText,*placeholder;
@property(nonatomic) NSUInteger limit;
@property(nonatomic,copy) void(^save)(NSString *text,FRTextEditor *editor);
@end
@implementation FRTextEditor
- (void)viewDidLoad {
    [super viewDidLoad];self.view.backgroundColor=UIColor.systemGroupedBackgroundColor;
    self.text=[UITextView new];self.text.translatesAutoresizingMaskIntoConstraints=NO;self.text.font=[UIFont preferredFontForTextStyle:UIFontTextStyleBody];self.text.adjustsFontForContentSizeCategory=YES;
    self.text.backgroundColor=UIColor.secondarySystemGroupedBackgroundColor;self.text.textColor=UIColor.labelColor;self.text.layer.cornerRadius=12;self.text.textContainerInset=UIEdgeInsetsMake(12,10,12,10);
    self.text.text=self.initialText?:@"";self.text.delegate=self;[self.view addSubview:self.text];
    UILabel *hint=[UILabel new];hint.translatesAutoresizingMaskIntoConstraints=NO;hint.numberOfLines=0;hint.text=self.placeholder;hint.font=[UIFont preferredFontForTextStyle:UIFontTextStyleFootnote];hint.textColor=UIColor.secondaryLabelColor;[self.view addSubview:hint];
    UILayoutGuide *safe=self.view.safeAreaLayoutGuide;
    [NSLayoutConstraint activateConstraints:@[[hint.topAnchor constraintEqualToAnchor:safe.topAnchor constant:8],[hint.leadingAnchor constraintEqualToAnchor:safe.leadingAnchor constant:16],[hint.trailingAnchor constraintEqualToAnchor:safe.trailingAnchor constant:-16],
        [self.text.topAnchor constraintEqualToAnchor:hint.bottomAnchor constant:8],[self.text.leadingAnchor constraintEqualToAnchor:safe.leadingAnchor constant:12],[self.text.trailingAnchor constraintEqualToAnchor:safe.trailingAnchor constant:-12],
        [self.text.bottomAnchor constraintEqualToAnchor:self.view.keyboardLayoutGuide.topAnchor constant:-12]]];
    self.navigationItem.leftBarButtonItem=[[UIBarButtonItem alloc] initWithBarButtonSystemItem:UIBarButtonSystemItemCancel target:self action:@selector(cancel)];
    self.navigationItem.rightBarButtonItem=[[UIBarButtonItem alloc] initWithTitle:@"保存" style:UIBarButtonItemStyleDone target:self action:@selector(done)];
}
- (void)viewDidAppear:(BOOL)animated {[super viewDidAppear:animated];[self.text becomeFirstResponder];}
- (BOOL)textView:(UITextView *)textView shouldChangeTextInRange:(NSRange)range replacementText:(NSString *)text {return !self.limit||textView.text.length-range.length+text.length<=self.limit;}
- (void)cancel {[self dismissViewControllerAnimated:YES completion:nil];}
- (void)done {self.navigationItem.rightBarButtonItem.enabled=NO;self.save(self.text.text,self);}
- (void)finishWithError:(NSError *)error {
    self.navigationItem.rightBarButtonItem.enabled=YES;
    if(!error){[self dismissViewControllerAnimated:YES completion:nil];return;}
    UIAlertController *a=[UIAlertController alertControllerWithTitle:@"没有保存" message:error.localizedDescription preferredStyle:UIAlertControllerStyleAlert];[a addAction:[UIAlertAction actionWithTitle:@"好" style:UIAlertActionStyleCancel handler:nil]];[self presentViewController:a animated:YES completion:nil];
}
@end

static void FREdit(UIViewController *vc,NSString *title,NSString *value,NSString *hint,NSUInteger limit,void(^save)(NSString *text,FRTextEditor *editor)){
    FRTextEditor *editor=[FRTextEditor new];editor.title=title;editor.initialText=value;editor.placeholder=hint;editor.limit=limit;editor.save=save;
    UINavigationController *nav=[[UINavigationController alloc] initWithRootViewController:editor];
    [vc presentViewController:nav animated:YES completion:nil];
}

static void FRPlay(UIViewController *vc,NSString *path,NSString *filename){
    [FRAPI.shared download:path filename:filename completion:^(NSURL *file,NSError *error){
        if(error){FRShowError(vc,error);return;}
        AVPlayerViewController *player=[AVPlayerViewController new];player.player=[AVPlayer playerWithURL:file];
        [vc presentViewController:player animated:YES completion:^{[player.player play];}];
    }];
}

#pragma mark - Memory

void FRShowMemory(UIViewController *vc){
    FRListController *list=[FRListController new];list.title=@"记忆";list.defaultIcon=@"brain";__weak FRListController *weak=list;
    void(^post)(NSDictionary *)=^(NSDictionary *body){[FRAPI.shared call:@"/v1/memory" method:@"POST" body:body completion:^(id v,NSError *e){if(e)FRShowError(weak,e);[weak reloadData];}];};
    list.reloadAction=^{[FRAPI.shared get:@"/v1/memory" completion:^(NSDictionary *v,NSError *e){
        if(e){if(e.code==404)e=[NSError errorWithDomain:@"Friday" code:404 userInfo:@{NSLocalizedDescriptionKey:@"Mac 上的 Friday 还没有开启新版 agent。"}];FRShowError(weak,e);return;}
        NSMutableArray *sections=[NSMutableArray array];
        for(NSArray *section in @[@[@"user",@"关于我",@"person"],@[@"memory",@"长期记忆",@"brain"]]){
            NSString *target=section[0],*label=section[1];NSArray *entries=v[target];NSMutableArray *rows=[NSMutableArray array];
            for(NSString *entry in entries){
                NSMutableDictionary *row=[FRListRow(entry,@"",^{FRAskText(weak,@"修改这条记忆",entry,^(NSString *text){if(![text isEqual:entry])post(@{@"action":@"replace",@"target":target,@"text":text,@"old_text":entry});});}) mutableCopy];
                NSString *key=[target stringByAppendingFormat:@":%@",entry];row[@"key"]=key;
                row[@"icon"]=section[2];row[@"delete"]=^{FRAskDestructive(weak,@"删除这条记忆？",entry,@"删除",^{[weak hideRows:@[key]];[FRAPI.shared call:@"/v1/memory" method:@"POST" body:@{@"action":@"remove",@"target":target,@"old_text":entry} completion:^(id v,NSError *e){if(e){FRShowError(weak,e);[weak unhideRows:@[key]];}[weak reloadData];}];});};[rows addObject:row];
            }
            NSMutableDictionary *add=[FRListRow([NSString stringWithFormat:@"添加到「%@」",label],@"",^{FRAskText(weak,[NSString stringWithFormat:@"添加到「%@」",label],@"",^(NSString *text){post(@{@"action":@"add",@"target":target,@"text":text});});}) mutableCopy];
            add[@"icon"]=@"plus.circle.fill";add[@"tint"]=FRAccent();[rows addObject:add];
            [sections addObject:@{@"title":label,@"rows":rows,@"footer":[NSString stringWithFormat:@"共 %lu 条，上限 %@ 字。点按修改，左滑删除。",(unsigned long)entries.count,v[@"limits"][target]]}];
        }
        weak.sections=sections;
    }];};
    [vc.navigationController pushViewController:list animated:YES];
}

#pragma mark - Schedules

void FRShowSchedules(UIViewController *vc){
    FRListController *list=[FRListController new];list.title=@"定时任务";list.defaultIcon=@"alarm";list.emptyTitle=@"还没有定时任务";list.emptyDetail=@"在对话里说“每天早上 8 点……”即可创建。";__weak FRListController *weak=list;
    list.reloadAction=^{[FRAPI.shared get:@"/v1/schedules" completion:^(NSDictionary *v,NSError *e){
        if(e){if(e.code==404)e=[NSError errorWithDomain:@"Friday" code:404 userInfo:@{NSLocalizedDescriptionKey:@"Mac 上的 Friday 还没有开启新版 agent。"}];FRShowError(weak,e);return;}
        NSMutableArray *rows=[NSMutableArray array];
        for(NSDictionary *item in v[@"items"]){
            BOOL enabled=[item[@"enabled"] boolValue];NSString *tid=item[@"id"];NSString *path=[@"/v1/schedules/" stringByAppendingString:FRSegment(tid)];
            void(^toggle)(void)=^{[FRAPI.shared call:path method:@"PATCH" body:@{@"enabled":@(!enabled)} completion:^(id r,NSError *x){if(x)FRShowError(weak,x);[weak reloadData];}];};
            void(^remove)(void)=^{FRAskDestructive(weak,@"删除这个定时任务？",FRString(item[@"title"]),@"删除",^{[weak hideRows:@[path]];[FRAPI.shared call:path method:@"DELETE" body:nil completion:^(id r,NSError *x){if(x){FRShowError(weak,x);[weak unhideRows:@[path]];}[weak reloadData];}];});};
            NSString *detail=enabled?[NSString stringWithFormat:@"%@ · 下次 %@%@",item[@"when"],FRString(item[@"next_run"]),[item[@"notify"] isEqual:@"when_relevant"]?@" · 有情况才通知":@""]:[NSString stringWithFormat:@"%@ · 已暂停",item[@"when"]];
            NSMutableDictionary *row=FRStatusRow(FRString(item[@"title"]),detail,enabled?@"开启":@"暂停",enabled?@"running":@"",^{
                UIAlertController *sheet=[UIAlertController alertControllerWithTitle:FRString(item[@"title"]) message:FRString(item[@"prompt"]) preferredStyle:UIAlertControllerStyleActionSheet];
                [sheet addAction:[UIAlertAction actionWithTitle:enabled?@"暂停":@"恢复" style:UIAlertActionStyleDefault handler:^(UIAlertAction *a){toggle();}]];
                [sheet addAction:[UIAlertAction actionWithTitle:@"删除" style:UIAlertActionStyleDestructive handler:^(UIAlertAction *a){remove();}]];
                [sheet addAction:[UIAlertAction actionWithTitle:@"取消" style:UIAlertActionStyleCancel handler:nil]];
                [weak presentViewController:sheet animated:YES completion:nil];});
            row[@"icon"]=enabled?@"alarm":@"pause.circle";row[@"delete"]=remove;row[@"key"]=path;
            row[@"leading"]=@[FRSwipe(enabled?@"暂停":@"恢复",enabled?@"pause.fill":@"play.fill",enabled?UIColor.systemOrangeColor:UIColor.systemGreenColor,toggle)];
            [rows addObject:row];
        }
        weak.sections=rows.count?@[@{@"rows":rows,@"footer":@"右滑暂停或恢复，左滑删除。"}]:@[];
    }];};
    [vc.navigationController pushViewController:list animated:YES];
}

#pragma mark - Drama: one shot

@interface FRShotController : FRListController
@property(nonatomic,copy) NSString *pid,*shotID;
@property(nonatomic,copy) NSDictionary *(^shotSource)(void);
@property(nonatomic,copy) void(^mutate)(NSString *path,NSString *method,NSDictionary *body,void(^done)(NSError *));
@property(nonatomic,copy) void(^submit)(NSString *kind,NSArray *ids);
@end
@implementation FRShotController
- (void)viewDidLoad {[super viewDidLoad];self.defaultIcon=@"film";}
- (void)reloadData {
    NSDictionary *shot=self.shotSource?self.shotSource():nil;if(!shot){self.rows=@[];[super reloadData];return;}
    __weak typeof(self) weak=self;NSString *shid=shot[@"id"],*status=shot[@"status"];
    self.title=[NSString stringWithFormat:@"镜头 %@",shot[@"index"]];
    NSMutableArray *rows=[NSMutableArray array];
    [rows addObject:FRStatusRow(FRString(shot[@"action"]),[NSString stringWithFormat:@"%@ 秒 · %@",shot[@"duration"],FRString(shot[@"camera"])],FRShotStatus(status),[status hasSuffix:@"ing"]?@"running":(FRString(shot[@"last_error"]).length?@"failed":@""),nil)];
    if(FRString(shot[@"last_error"]).length)[rows addObject:FRStatusRow(@"上次生成失败",FRString(shot[@"last_error"]),nil,nil,nil)];
    NSString *current=FRString(shot[@"final_take_id"]).length?shot[@"final_take_id"]:shot[@"draft_take_id"];
    NSInteger drafts=0,finals=0;
    for(NSDictionary *take in shot[@"takes"]){
        BOOL draft=[take[@"kind"] isEqual:@"draft"];NSInteger number=draft?++drafts:++finals;NSString *tid=take[@"id"];BOOL selected=[tid isEqual:shot[@"draft_take_id"]]||[tid isEqual:shot[@"final_take_id"]];
        NSString *title=[NSString stringWithFormat:@"%@ v%ld%@",draft?@"草稿":@"正式",(long)number,selected?@" · 选用中":@""];
        void(^open)(void)=[take[@"has_output"] boolValue]?^{
            UIAlertController *sheet=[UIAlertController alertControllerWithTitle:title message:nil preferredStyle:UIAlertControllerStyleActionSheet];
            [sheet addAction:[UIAlertAction actionWithTitle:@"播放" style:UIAlertActionStyleDefault handler:^(UIAlertAction *a){FRPlay(weak,FRDramaPath(weak.pid,[NSString stringWithFormat:@"/takes/%@/file",FRSegment(tid)]),[tid stringByAppendingString:@".mp4"]);}]];
            if(!selected)[sheet addAction:[UIAlertAction actionWithTitle:@"选用这个版本" style:UIAlertActionStyleDefault handler:^(UIAlertAction *a){weak.mutate([NSString stringWithFormat:@"/shots/%@/selected",FRSegment(shid)],@"PUT",@{@"take_id":tid},nil);}]];
            [sheet addAction:[UIAlertAction actionWithTitle:@"取消" style:UIAlertActionStyleCancel handler:nil]];[weak presentViewController:sheet animated:YES completion:nil];
        }:nil;
        NSMutableDictionary *row=FRStatusRow(title,[take[@"has_output"] boolValue]?@"点按播放或选用":FRString(take[@"error"]),FRJobText(take[@"status"]),[take[@"status"] isEqual:@"failed"]?@"failed":([@[@"queued",@"running",@"waiting"] containsObject:take[@"status"]]?@"running":@""),open);
        row[@"icon"]=[tid isEqual:current]?@"play.rectangle.fill":@"play.rectangle";[rows addObject:row];
    }
    if([status isEqual:@"draft_review"]){
        [rows addObject:FRIconRow(@"checkmark.seal",FRListRow(@"通过这个草稿",@"通过后可以提交正式渲染",^{weak.mutate([NSString stringWithFormat:@"/shots/%@",FRSegment(shid)],@"PATCH",@{@"approved":@YES},nil);}))];
    }
    if([status isEqual:@"approved"]){
        [rows addObject:FRIconRow(@"paperplane.fill",FRListRow(@"提交正式渲染",@"与草稿同一个随机种子",^{weak.submit(@"final",@[shid]);}))];
        [rows addObject:FRIconRow(@"arrow.uturn.backward",FRListRow(@"取消通过",@"回到待审草稿",^{weak.mutate([NSString stringWithFormat:@"/shots/%@",FRSegment(shid)],@"PATCH",@{@"approved":@NO},nil);}))];
    }
    if([@[@"planned",@"draft_review"] containsObject:status]){
        [rows addObject:FRIconRow(@"arrow.clockwise",FRListRow(drafts?@"重出草稿":@"出草稿",@"低分辨率、少步数，先看动作和节奏",^{weak.submit(@"draft",@[shid]);}))];
    }
    if([status isEqual:@"planned"]){
        [rows addObject:FRIconRow(@"forward",FRListRow(@"跳过草稿，直接通过",@"正式渲染单镜约 20 分钟以上",^{FRAskConfirm(weak,@"跳过草稿？",@"没有草稿就直接进入正式渲染。",^{weak.mutate([NSString stringWithFormat:@"/shots/%@",FRSegment(shid)],@"PATCH",@{@"approved":@YES,@"skip_draft":@YES},nil);});}))];
    }
    NSString *dialogue=[[(NSArray *)shot[@"dialogue"] valueForKey:@"line"] componentsJoinedByString:@" / "];
    NSArray *fields=@[@[@"action",@"这一镜发生什么",@300],@[@"visual",@"画面描述（英文）",@1200],@[@"camera",@"景别与运镜",@120]];
    for(NSArray *field in fields){
        NSString *key=field[0];
        [rows addObject:FRIconRow(@"pencil",FRListRow(field[1],FRString(shot[key]).length?FRString(shot[key]):@"未填写",^{
            FREdit(weak,field[1],FRString(shot[key]),@"修改画面相关内容后，这一镜需要重新出草稿；已生成的版本会保留。",[field[2] unsignedIntegerValue],^(NSString *text,FRTextEditor *editor){
                weak.mutate([NSString stringWithFormat:@"/shots/%@",FRSegment(shid)],@"PATCH",@{key:text},^(NSError *e){[editor finishWithError:e];});});}))];
    }
    [rows addObject:FRIconRow(@"timer",FRListRow(@"时长",[NSString stringWithFormat:@"%@ 秒（4–15）",shot[@"duration"]],^{FRAskText(weak,@"时长（4–15 秒）",[shot[@"duration"] description],^(NSString *text){weak.mutate([NSString stringWithFormat:@"/shots/%@",FRSegment(shid)],@"PATCH",@{@"duration":@(text.integerValue)},nil);});}))];
    [rows addObject:FRIconRow(@"text.quote",FRListRow(@"台词",dialogue.length?dialogue:@"无",^{
        NSMutableArray *lines=[NSMutableArray array];for(NSDictionary *d in shot[@"dialogue"])[lines addObject:FRString(d[@"name"]).length?[NSString stringWithFormat:@"%@：%@",d[@"name"],d[@"line"]]:FRString(d[@"line"])];
        FREdit(weak,@"台词",[lines componentsJoinedByString:@"\n"],@"每行一句，格式：角色：台词",3000,^(NSString *text,FRTextEditor *editor){
            NSMutableArray *items=[NSMutableArray array];
            for(NSString *raw in [text componentsSeparatedByCharactersInSet:NSCharacterSet.newlineCharacterSet]){NSString *line=[raw stringByTrimmingCharactersInSet:NSCharacterSet.whitespaceCharacterSet];if(!line.length)continue;NSRange r=[line rangeOfString:@"："];if(r.location==NSNotFound)r=[line rangeOfString:@":"];
                if(r.location!=NSNotFound&&r.location>0&&r.location<=30)[items addObject:@{@"name":[line substringToIndex:r.location],@"line":[line substringFromIndex:r.location+1]}];else [items addObject:@{@"name":@"",@"line":line}];}
            weak.mutate([NSString stringWithFormat:@"/shots/%@",FRSegment(shid)],@"PATCH",@{@"dialogue":items},^(NSError *e){[editor finishWithError:e];});});}))];
    NSMutableDictionary *removeShot=[FRIconRow(@"trash",FRListRow(@"删除镜头",@"排队中的版本会取消",^{FRAskConfirm(weak,@"删除这个镜头？",FRString(shot[@"action"]),^{void(^mutate)(NSString *,NSString *,NSDictionary *,void(^)(NSError *))=weak.mutate;[weak.navigationController popViewControllerAnimated:YES];mutate([NSString stringWithFormat:@"/shots/%@",FRSegment(shid)],@"DELETE",nil,nil);});})) mutableCopy];removeShot[@"destructive"]=@YES;[rows addObject:removeShot];
    self.rows=rows;[super reloadData];
}
@end

#pragma mark - Drama: one project

@interface FRDramaController : FRListController
@property(nonatomic,copy) NSString *pid;
@property(nonatomic) NSDictionary *data;
@property(nonatomic) UISegmentedControl *section;
@property(nonatomic) NSTimer *timer;
@property(nonatomic) NSInteger initialSection;
@property(nonatomic) CGFloat headerWidth;
@end
// UI 2.0 project header: the five stages, the cast, then 设定 / 分镜 / 成片.
static UIView *FRStageBar(NSString *name,CGFloat fraction,BOOL current,BOOL started){
    UIProgressView *bar=[[UIProgressView alloc] initWithProgressViewStyle:UIProgressViewStyleBar];bar.progress=fraction;bar.progressTintColor=current?FRAccent():FRInk();bar.trackTintColor=current?FRAccentSoft():FRHairline();bar.layer.cornerRadius=2;bar.clipsToBounds=YES;[bar.heightAnchor constraintEqualToConstant:4].active=YES;
    UILabel *label=FRWorksLabel(name,FRFont(11,UIFontWeightSemibold),current?FRAccent():(started?FRInkSecondary():FRInkTertiary()),1);label.adjustsFontSizeToFitWidth=YES;label.minimumScaleFactor=.8;
    UIStackView *stack=[[UIStackView alloc] initWithArrangedSubviews:@[bar,label]];stack.axis=UILayoutConstraintAxisVertical;stack.spacing=6;stack.isAccessibilityElement=YES;
    stack.accessibilityLabel=[NSString stringWithFormat:@"%@，%@",name,fraction>=1?@"已完成":(current?[NSString stringWithFormat:@"进行中，%ld%%",(long)lround(fraction*100)]:@"未开始")];return stack;
}
static UIColor *FRCastColor(NSString *name){NSArray *colors=@[@[@.79,@.64,@.48],@[@.36,@.42,@.51],@[@.54,@.5,@.45],@[@.42,@.55,@.48],@[@.6,@.45,@.55]];NSArray *c=colors[(name.hash)%colors.count];return [UIColor colorWithRed:[c[0] doubleValue] green:[c[1] doubleValue] blue:[c[2] doubleValue] alpha:1];}
@implementation FRDramaController
- (instancetype)init {if((self=[super initWithStyle:UITableViewStylePlain])){_initialSection=1;self.hidesBottomBarWhenPushed=YES;}return self;}
- (void)viewDidLayoutSubviews {[super viewDidLayoutSubviews];if(self.data&&fabs(self.tableView.bounds.size.width-self.headerWidth)>.5)[self installHeader];}
- (void)editCharacter:(NSDictionary *)c {
    __weak typeof(self) weak=self;NSString *cid=c[@"id"];
    FREdit(self,[FRString(c[@"name"]) stringByAppendingString:@"的外观"],FRString(c[@"appearance"]),@"英文写稳定的外观：年龄段、发型、体型、服装颜色。会原样写进这个角色出场的每个镜头。",1500,^(NSString *text,FRTextEditor *editor){[weak mutate:[@"/characters/" stringByAppendingString:FRSegment(cid)] method:@"PUT" body:@{@"appearance":text} done:^(NSError *e){[editor finishWithError:e];}];});
}
- (void)installHeader {
    NSDictionary *data=self.data;NSArray *shots=data[@"shots"],*characters=data[@"characters"],*scenes=data[@"scenes"];CGFloat width=self.tableView.bounds.size.width;self.headerWidth=width;if(width<=0)return;
    NSInteger total=shots.count,drafted=0,final=0,seconds=0,described=0;
    for(NSDictionary *shot in shots){NSString *st=shot[@"status"];seconds+=[shot[@"duration"] integerValue];if([@[@"draft_review",@"approved",@"finalizing",@"done"] containsObject:st])drafted++;if([st isEqual:@"done"])final++;}
    for(NSDictionary *c in characters)if(FRString(c[@"appearance"]).length)described++;
    BOOL exported=NO;for(NSDictionary *e in data[@"exports"])if([e[@"kind"] isEqual:@"final"]&&[e[@"ready"] boolValue])exported=YES;
    CGFloat fractions[5]={characters.count?(CGFloat)described/characters.count:0,total?1:0,total?(CGFloat)drafted/total:0,total?(CGFloat)final/total:0,exported?1:0};
    NSInteger current=4;for(NSInteger i=0;i<5;i++)if(fractions[i]<1){current=i;break;}if(exported)current=5;
    NSArray *names=@[@"设定",@"分镜",total?[NSString stringWithFormat:@"草稿 %ld/%ld",(long)drafted,(long)total]:@"草稿",total?[NSString stringWithFormat:@"正式 %ld/%ld",(long)final,(long)total]:@"正式",@"成片"];
    NSMutableArray *bars=[NSMutableArray array];for(NSInteger i=0;i<5;i++)[bars addObject:FRStageBar(names[i],fractions[i],i==current,i<=current)];
    UIStackView *stages=[[UIStackView alloc] initWithArrangedSubviews:bars];stages.spacing=4;stages.distribution=UIStackViewDistributionFillEqually;stages.accessibilityIdentifier=@"drama.stages";
    UILabel *meta=FRWorksLabel([NSString stringWithFormat:@"%lu 场 · %ld 镜 · 约 %@",(unsigned long)scenes.count,(long)total,FRMinutes(@(seconds))],FRFont(13,UIFontWeightRegular),FRInkSecondary(),0);
    UIStackView *stack=[[UIStackView alloc] initWithArrangedSubviews:@[meta,stages]];stack.axis=UILayoutConstraintAxisVertical;stack.spacing=16;
    if(characters.count){
        UILabel *castTitle=FRWorksLabel(@"角色",FRFont(13,UIFontWeightSemibold),FRInkSecondary(),1);castTitle.accessibilityTraits=UIAccessibilityTraitHeader;
        UIStackView *cast=[UIStackView new];cast.spacing=14;cast.alignment=UIStackViewAlignmentTop;__weak typeof(self) weak=self;
        for(NSDictionary *c in characters){
            NSString *name=FRString(c[@"name"]);BOOL ready=FRString(c[@"appearance"]).length>0;
            UIButton *avatar=[UIButton buttonWithType:UIButtonTypeSystem];UIButtonConfiguration *conf=UIButtonConfiguration.plainButtonConfiguration;conf.title=name.length?[name substringToIndex:1]:@"?";conf.baseForegroundColor=ready?UIColor.whiteColor:FRInkSecondary();
            conf.background.backgroundColor=ready?FRCastColor(name):UIColor.clearColor;conf.background.cornerRadius=28;if(!ready){conf.background.strokeColor=FRInkTertiary();conf.background.strokeWidth=1.5;conf.background.strokeOutset=-1;}
            conf.titleTextAttributesTransformer=^NSDictionary *(NSDictionary *a){NSMutableDictionary *m=[a mutableCopy];m[NSFontAttributeName]=[UIFont systemFontOfSize:20 weight:UIFontWeightSemibold];return m;};avatar.configuration=conf;
            [avatar.widthAnchor constraintEqualToConstant:56].active=YES;[avatar.heightAnchor constraintEqualToConstant:56].active=YES;avatar.accessibilityLabel=[NSString stringWithFormat:@"%@，%@",name,ready?@"外观已写好":@"外观未填写"];avatar.accessibilityHint=@"编辑外观";
            [avatar addAction:[UIAction actionWithHandler:^(UIAction *a){[weak editCharacter:c];}] forControlEvents:UIControlEventTouchUpInside];
            UILabel *label=FRWorksLabel(name,FRFont(12,UIFontWeightMedium),ready?FRInk():FRInkSecondary(),1);label.textAlignment=NSTextAlignmentCenter;label.isAccessibilityElement=NO;
            UIStackView *person=[[UIStackView alloc] initWithArrangedSubviews:@[avatar,label]];person.axis=UILayoutConstraintAxisVertical;person.spacing=6;person.alignment=UIStackViewAlignmentCenter;[person.widthAnchor constraintEqualToConstant:60].active=YES;[cast addArrangedSubview:person];
        }
        UIScrollView *scroll=[UIScrollView new];scroll.showsHorizontalScrollIndicator=NO;cast.translatesAutoresizingMaskIntoConstraints=NO;[scroll addSubview:cast];
        [NSLayoutConstraint activateConstraints:@[[cast.leadingAnchor constraintEqualToAnchor:scroll.contentLayoutGuide.leadingAnchor],[cast.trailingAnchor constraintEqualToAnchor:scroll.contentLayoutGuide.trailingAnchor],[cast.topAnchor constraintEqualToAnchor:scroll.contentLayoutGuide.topAnchor],[cast.bottomAnchor constraintEqualToAnchor:scroll.contentLayoutGuide.bottomAnchor],[cast.heightAnchor constraintEqualToAnchor:scroll.frameLayoutGuide.heightAnchor]]];
        [stack addArrangedSubview:castTitle];[stack setCustomSpacing:10 afterView:castTitle];[stack addArrangedSubview:scroll];
    }
    [self.section removeFromSuperview];[stack addArrangedSubview:self.section];
    UIView *header=[UIView new];header.preservesSuperviewLayoutMargins=YES;stack.translatesAutoresizingMaskIntoConstraints=NO;[header addSubview:stack];
    [NSLayoutConstraint activateConstraints:@[[stack.leadingAnchor constraintEqualToAnchor:header.layoutMarginsGuide.leadingAnchor],[stack.trailingAnchor constraintEqualToAnchor:header.layoutMarginsGuide.trailingAnchor],[stack.topAnchor constraintEqualToAnchor:header.topAnchor constant:4],[stack.bottomAnchor constraintEqualToAnchor:header.bottomAnchor constant:-10]]];
    header.frame=CGRectMake(0,0,width,200);[header layoutIfNeeded];CGSize size=[header systemLayoutSizeFittingSize:CGSizeMake(width,0) withHorizontalFittingPriority:UILayoutPriorityRequired verticalFittingPriority:UILayoutPriorityFittingSizeLevel];header.frame=CGRectMake(0,0,width,ceil(size.height));
    self.tableView.tableHeaderView=header;
}
// The one next step for the whole project, pinned at the bottom.
- (void)updateToolbar {
    NSArray *shots=self.data[@"shots"];NSInteger planned=0,approved=0,done=0;BOOL busy=[self busy];
    for(NSDictionary *s in shots){if([s[@"status"] isEqual:@"planned"])planned++;if([s[@"status"] isEqual:@"approved"])approved++;if([s[@"status"] isEqual:@"done"])done++;}
    NSDictionary *estimate=self.data[@"estimate"];__weak typeof(self) weak=self;NSString *title,*detail,*button;void(^action)(void)=nil;
    if(!self.data){self.toolbarItems=@[];return;}
    if(!shots.count){title=@"还没有分镜";detail=@"先核对角色和场景";button=@"生成分镜";action=^{[weak plan:@"storyboard" scenes:nil];};}
    else if(approved){title=[NSString stringWithFormat:@"%ld 镜草稿已通过",(long)approved];detail=[NSString stringWithFormat:@"正式渲染%@",estimate?[@" · " stringByAppendingString:FREstimate([estimate[@"final_seconds_remaining"] doubleValue])]:@""];button=[NSString stringWithFormat:@"正式渲染 %ld 镜",(long)approved];action=^{[weak submit:@"final" shots:@"all_pending"];};}
    else if(planned){title=[NSString stringWithFormat:@"%ld 镜待出草稿",(long)planned];detail=[NSString stringWithFormat:@"低成本预览%@",estimate?[@" · " stringByAppendingString:FREstimate([estimate[@"draft_seconds_remaining"] doubleValue])]:@""];button=[NSString stringWithFormat:@"出草稿 %ld 镜",(long)planned];action=^{[weak submit:@"draft" shots:@"all_pending"];};}
    else if(done==(NSInteger)shots.count){title=@"所有镜头已完成";detail=@"用正式版合成全片";button=@"合成成片";action=^{[FRAPI.shared call:FRDramaPath(weak.pid,@"/exports") method:@"POST" body:@{@"kind":@"final"} completion:^(id v,NSError *e){if(e)FRShowError(weak,e);[weak fetch];}];};}
    else{title=busy?@"正在生成":@"等你审草稿";detail=busy?@"完成后这里会更新":@"打开镜头，通过或重做草稿";}
    UILabel *t=FRWorksLabel(title,FRFont(14,UIFontWeightSemibold),FRInk(),1),*d=FRWorksLabel(detail,FRFont(12,UIFontWeightRegular),FRInkSecondary(),1);
    UIStackView *text=[[UIStackView alloc] initWithArrangedSubviews:@[t,d]];text.axis=UILayoutConstraintAxisVertical;text.spacing=1;text.isAccessibilityElement=YES;text.accessibilityLabel=[NSString stringWithFormat:@"%@，%@",title,detail];
    UIBarButtonItem *summary=[[UIBarButtonItem alloc] initWithCustomView:text];if(@available(iOS 26.0,*))summary.hidesSharedBackground=YES;NSMutableArray *items=[NSMutableArray arrayWithObject:summary];[items addObject:[UIBarButtonItem flexibleSpaceItem]];
    if(button){UIBarButtonItem *go=[[UIBarButtonItem alloc] initWithPrimaryAction:[UIAction actionWithTitle:button image:nil identifier:nil handler:^(UIAction *a){action();}]];if(@available(iOS 26.0,*))go.style=UIBarButtonItemStyleProminent;else go.style=UIBarButtonItemStyleDone;go.tintColor=FRInk();go.accessibilityIdentifier=@"drama.next";[items addObject:go];}
    self.toolbarItems=items;
}
- (void)viewDidLoad {
    [super viewDidLoad];self.defaultIcon=@"film";if(!self.title.length)self.title=@"短剧项目";
    self.section=[[UISegmentedControl alloc] initWithItems:@[@"设定",@"分镜",@"成片"]];self.section.selectedSegmentIndex=self.initialSection;self.section.accessibilityIdentifier=@"drama.section";
    [self.section addTarget:self action:@selector(render) forControlEvents:UIControlEventValueChanged];[self.section.heightAnchor constraintEqualToConstant:32].active=YES;
    self.navigationItem.largeTitleDisplayMode=UINavigationItemLargeTitleDisplayModeAlways;
    __weak typeof(self) weak=self;self.reloadAction=^{[weak fetch];};
}
- (void)viewWillAppear:(BOOL)animated {[super viewWillAppear:animated];[self.navigationController setToolbarHidden:NO animated:animated];__weak typeof(self) weak=self;self.timer=[NSTimer scheduledTimerWithTimeInterval:5 repeats:YES block:^(NSTimer *t){if([weak busy])[weak fetch];}];}
- (void)viewWillDisappear:(BOOL)animated {[super viewWillDisappear:animated];[self.timer invalidate];self.timer=nil;[self.navigationController setToolbarHidden:YES animated:animated];}
- (BOOL)busy {
    for(NSDictionary *job in self.data[@"planning"])if([@[@"queued",@"running"] containsObject:job[@"status"]])return YES;
    for(NSDictionary *shot in self.data[@"shots"])if([@[@"drafting",@"finalizing"] containsObject:shot[@"status"]])return YES;
    for(NSDictionary *export in self.data[@"exports"])if([@[@"queued",@"running",@"waiting"] containsObject:export[@"status"]])return YES;
    return NO;
}
- (void)fetch {
    __weak typeof(self) weak=self;
    [FRAPI.shared get:FRDramaPath(self.pid,nil) completion:^(NSDictionary *v,NSError *e){if(e){FRShowError(weak,e);return;}weak.data=v;[weak render];}];
}
- (void)mutate:(NSString *)suffix method:(NSString *)method body:(NSDictionary *)body done:(void(^)(NSError *))done {
    NSMutableDictionary *payload=[body?:@{} mutableCopy];payload[@"revision"]=self.data[@"project"][@"revision"]?:@0;
    NSString *path=FRDramaPath(self.pid,suffix);if([method isEqual:@"DELETE"]){path=[path stringByAppendingFormat:@"?revision=%@",payload[@"revision"]];payload=nil;}
    __weak typeof(self) weak=self;
    [FRAPI.shared call:path method:method body:payload completion:^(NSDictionary *v,NSError *e){
        if(e.code==409){[weak fetch];e=[NSError errorWithDomain:@"Friday" code:409 userInfo:@{NSLocalizedDescriptionKey:@"项目已在别处修改，已刷新为最新内容，请再改一次。"}];}
        if(e){if(done)done(e);else FRShowError(weak,e);return;}
        if([v isKindOfClass:NSDictionary.class]&&v[@"project"])weak.data=v;[weak render];if(done)done(nil);
    }];
}
- (void)plan:(NSString *)op scenes:(NSArray *)scenes {
    __weak typeof(self) weak=self;NSDictionary *body=scenes?@{@"op":op,@"scene_ids":scenes}:@{@"op":op};
    [FRAPI.shared call:FRDramaPath(self.pid,@"/plan") method:@"POST" body:body completion:^(id v,NSError *e){if(e)FRShowError(weak,e);[weak fetch];}];
}
- (void)submit:(NSString *)kind shots:(id)ids {
    __weak typeof(self) weak=self;NSString *path=FRDramaPath(self.pid,@"/takes");NSString *label=[kind isEqual:@"draft"]?@"草稿":@"正式";
    [FRAPI.shared call:path method:@"POST" body:@{@"kind":kind,@"shot_ids":ids} completion:^(NSDictionary *preview,NSError *e){
        if(e){FRShowError(weak,e);return;}
        NSArray *shots=preview[@"shots"];NSInteger ahead=[preview[@"jobs_ahead"] integerValue];
        NSString *message=[NSString stringWithFormat:@"第 %@ 镜，预计约 %@%@。%@",[shots componentsJoinedByString:@"、"],FRMinutes(preview[@"estimated_seconds"]),[preview[@"basis"] isEqual:@"extrapolated"]?@"（按正式版推算，尚未实测）":@"",ahead?[NSString stringWithFormat:@"前面还有 %ld 个生成任务。",(long)ahead]:@""];
        UIAlertController *a=[UIAlertController alertControllerWithTitle:[NSString stringWithFormat:@"提交 %lu 镜%@？",(unsigned long)shots.count,label] message:message preferredStyle:UIAlertControllerStyleAlert];
        [a addAction:[UIAlertAction actionWithTitle:@"取消" style:UIAlertActionStyleCancel handler:nil]];
        [a addAction:[UIAlertAction actionWithTitle:[@"提交" stringByAppendingString:label] style:UIAlertActionStyleDefault handler:^(UIAlertAction *x){
            [FRAPI.shared call:path method:@"POST" body:@{@"kind":kind,@"shot_ids":ids,@"confirm":@YES} completion:^(id r,NSError *error){if(error)FRShowError(weak,error);[weak fetch];}];}]];
        [weak presentViewController:a animated:YES completion:nil];
    }];
}
- (void)render {
    NSDictionary *data=self.data;if(!data){return;}
    NSDictionary *project=data[@"project"];self.title=FRString(project[@"title"]);
    __weak typeof(self) weak=self;NSMutableArray *rows=[NSMutableArray array];
    for(NSDictionary *job in data[@"planning"]){
        NSString *op=@{@"analyze":@"补全设定",@"storyboard":@"生成分镜",@"export":@"合成"}[job[@"op"]]?:@"处理";
        if([@[@"queued",@"running"] containsObject:job[@"status"]]){[rows addObject:FRStatusRow([@"正在" stringByAppendingString:op],@"完成后这里会自动更新",FRJobText(job[@"status"]),@"running",nil)];break;}
        if([job[@"status"] isEqual:@"failed"]){[rows addObject:FRStatusRow([op stringByAppendingString:@"失败"],FRString(job[@"error"]),@"失败",@"failed",nil)];}
        break;
    }
    NSInteger section=self.section.selectedSegmentIndex;
    if(section==0){
        [rows addObject:FRIconRow(@"sparkles",FRListRow(@"让 Friday 补全空白的设定",@"只填写还空着的外观和场景，不覆盖你的修改",^{[weak plan:@"analyze" scenes:nil];}))];
        [rows addObject:FRIconRow(@"paintpalette",FRListRow(@"整体风格",FRString(project[@"style"]).length?FRString(project[@"style"]):@"未填写",^{FREdit(weak,@"整体风格",FRString(project[@"style"]),@"英文写画面风格、色调、镜头质感，会放在每个镜头描述的最前面。",1000,^(NSString *text,FRTextEditor *editor){[weak mutate:@"" method:@"PATCH" body:@{@"style":text} done:^(NSError *e){[editor finishWithError:e];}];});}))];
        for(NSDictionary *c in data[@"characters"]){
            NSString *cid=c[@"id"];NSMutableDictionary *row=[FRListRow(FRString(c[@"name"]),FRString(c[@"appearance"]).length?FRString(c[@"appearance"]):@"外观未填写",^{
                FREdit(weak,[FRString(c[@"name"]) stringByAppendingString:@"的外观"],FRString(c[@"appearance"]),@"英文写稳定的外观：年龄段、发型、体型、服装颜色。会原样写进这个角色出场的每个镜头。",1500,^(NSString *text,FRTextEditor *editor){[weak mutate:[@"/characters/" stringByAppendingString:FRSegment(cid)] method:@"PUT" body:@{@"appearance":text} done:^(NSError *e){[editor finishWithError:e];}];});}) mutableCopy];
            row[@"icon"]=@"person";[rows addObject:row];
        }
        for(NSDictionary *s in data[@"scenes"]){
            NSString *scid=s[@"id"];NSMutableDictionary *row=[FRListRow(FRString(s[@"heading"]),FRString(s[@"setting"]).length?FRString(s[@"setting"]):@"场景未填写",^{
                FREdit(weak,FRString(s[@"heading"]),FRString(s[@"setting"]),@"英文写地点、时间、光线和陈设。",1500,^(NSString *text,FRTextEditor *editor){[weak mutate:[@"/scenes/" stringByAppendingString:FRSegment(scid)] method:@"PUT" body:@{@"setting":text} done:^(NSError *e){[editor finishWithError:e];}];});}) mutableCopy];
            row[@"icon"]=@"mappin.and.ellipse";[rows addObject:row];
        }
    }else if(section==1){
        NSArray *shots=data[@"shots"];NSInteger planned=0,approved=0;BOOL busy=NO;
        for(NSDictionary *s in shots){if([s[@"status"] isEqual:@"planned"])planned++;if([s[@"status"] isEqual:@"approved"])approved++;busy|=[@[@"drafting",@"finalizing"] containsObject:s[@"status"]];}
        // Submitting drafts and finals lives in the bottom bar; these rows only cover the rest.
        if(approved&&planned)[rows addObject:FRIconRow(@"paperplane",FRListRow([NSString stringWithFormat:@"出草稿 · %ld 镜",(long)planned],@"低成本预览，先看动作和节奏",^{[weak submit:@"draft" shots:@"all_pending"];}))];
        if(busy)[rows addObject:FRIconRow(@"stop.circle",FRListRow(@"取消排队",@"已完成的版本保留",^{FRAskConfirm(weak,@"取消这个项目的生成？",@"排队中和正在生成的镜头都会停止。",^{[FRAPI.shared call:FRDramaPath(weak.pid,@"/cancel") method:@"POST" body:@{} completion:^(id v,NSError *e){if(e)FRShowError(weak,e);[weak fetch];}];});}))];
        NSDictionary *estimate=data[@"estimate"];
        if(shots.count)[rows addObject:FRIconRow(@"clock",FRStatusRow(@"预计剩余",[NSString stringWithFormat:@"正式约 %@ · 草稿约 %@",FRMinutes(estimate[@"final_seconds_remaining"]),FRMinutes(estimate[@"draft_seconds_remaining"])],nil,nil,nil))];
        for(NSDictionary *scene in data[@"scenes"]){
            NSString *scid=scene[@"id"];
            NSMutableDictionary *head=[FRListRow(FRString(scene[@"heading"]),@"点按重新生成本场分镜",^{FRAskConfirm(weak,@"重新生成本场分镜？",@"现有镜头会被替换（手写过 prompt 的保留）。",^{[weak plan:@"storyboard" scenes:@[scid]];});}) mutableCopy];
            head[@"icon"]=@"rectangle.split.3x1";[rows addObject:head];
            for(NSDictionary *shot in shots){
                if(![shot[@"scene_id"] isEqual:scid])continue;
                NSString *shid=shot[@"id"],*status=shot[@"status"];BOOL failed=FRString(shot[@"last_error"]).length>0;
                NSString *detail=[NSString stringWithFormat:@"%@ 秒 · %@",shot[@"duration"],FRString(shot[@"camera"])];if(failed)detail=[detail stringByAppendingFormat:@" · %@",shot[@"last_error"]];
                NSMutableDictionary *row=FRStatusRow([NSString stringWithFormat:@"%@  %@",shot[@"index"],FRString(shot[@"action"])],detail,failed?@"失败":FRShotStatus(status),failed?@"failed":([status hasSuffix:@"ing"]?@"running":([status isEqual:@"done"]?@"done":@"")),^{[weak openShot:shid];});
                row[@"icon"]=failed?@"exclamationmark.triangle":@{@"done":@"checkmark.circle.fill",@"drafting":@"circle.dotted",@"finalizing":@"circle.dotted",@"draft_review":@"eye",@"approved":@"checkmark.circle"}[status]?:@"circle";
                row[@"tint"]=failed?FRAmber():([status isEqual:@"done"]?FROnline():([status hasSuffix:@"ing"]?FRAccent():FRInkTertiary()));[rows addObject:row];
            }
        }
    }else{
        [rows addObject:FRIconRow(@"film.stack",FRListRow(@"合成草稿全片",@"每镜用选定的草稿（已有正式版的用正式版）",^{[FRAPI.shared call:FRDramaPath(weak.pid,@"/exports") method:@"POST" body:@{@"kind":@"draft"} completion:^(id v,NSError *e){if(e)FRShowError(weak,e);[weak fetch];}];}))];
        [rows addObject:FRIconRow(@"film.stack.fill",FRListRow(@"合成正式全片",@"要求每镜都有正式版",^{[FRAPI.shared call:FRDramaPath(weak.pid,@"/exports") method:@"POST" body:@{@"kind":@"final"} completion:^(id v,NSError *e){if(e)FRShowError(weak,e);[weak fetch];}];}))];
        for(NSDictionary *export in data[@"exports"]){
            NSString *eid=export[@"id"];BOOL ready=[export[@"ready"] boolValue];
            NSString *title=[NSString stringWithFormat:@"%@全片",[export[@"kind"] isEqual:@"final"]?@"正式":@"草稿"];
            [rows addObject:FRStatusRow(title,ready?@"点按播放":FRString(export[@"error"]),FRJobText(export[@"status"]),[export[@"status"] isEqual:@"failed"]?@"failed":(ready?@"":@"running"),ready?^{FRPlay(weak,FRDramaPath(weak.pid,[NSString stringWithFormat:@"/exports/%@/file",FRSegment(eid)]),[FRString(project[@"title"]) stringByAppendingString:@".mp4"]);}:nil)];
        }
    }
    self.rows=rows;  // setRows refreshes the table; reloadData here would fetch again
    [self installHeader];[self updateToolbar];
    for(UIViewController *vc in self.navigationController.viewControllers)if([vc isKindOfClass:FRShotController.class])[(FRShotController *)vc reloadData];
}
- (void)openShot:(NSString *)shid {
    FRShotController *detail=[FRShotController new];detail.pid=self.pid;detail.shotID=shid;__weak typeof(self) weak=self;
    detail.shotSource=^NSDictionary *{for(NSDictionary *s in weak.data[@"shots"])if([s[@"id"] isEqual:shid])return s;return nil;};
    detail.mutate=^(NSString *suffix,NSString *method,NSDictionary *body,void(^done)(NSError *)){[weak mutate:suffix method:method body:body done:done];};
    detail.submit=^(NSString *kind,NSArray *ids){[weak submit:kind shots:ids];};
    [self.navigationController pushViewController:detail animated:YES];
}
- (void)dealloc {[self.timer invalidate];}
@end

#pragma mark - Drama: project list

static void FRDeleteDrama(UIViewController *vc,NSDictionary *item,void(^done)(void)){
    // Hidden at once (by its conversation, so undo brings it back); the Mac stops its renders in the background.
    NSString *pid=item[@"id"],*sid=FRString(item[@"session_id"]);NSArray *keys=sid.length?@[sid]:@[FRString(pid)];
    [FRStore.shared hide:keys];if(done)done();
    [FRAPI.shared call:FRDramaPath(pid,nil) method:@"DELETE" body:nil completion:^(id v,NSError *e){
        if(e){FRShowError(vc,e);[FRStore.shared unhide:keys];if(done)done();return;}
        FRToast(vc,@"已移到「最近删除」",sid.length?@"撤销":nil,sid.length?^{FRRestoreSessions(vc,@[sid],done);}:nil);
    }];
}
static NSMutableDictionary *FRDramaRow(UIViewController *vc,NSDictionary *item,void(^changed)(void)){
    NSString *pid=item[@"id"];NSInteger shots=[item[@"shots"] integerValue],done=[item[@"done"] integerValue];__weak UIViewController *weak=vc;
    NSString *detail=shots?[NSString stringWithFormat:@"%ld / %ld 镜完成 · 约 %@",(long)done,(long)shots,FRMinutes(item[@"seconds"])]:@"还没有分镜";
    NSMutableDictionary *row=FRStatusRow(FRString(item[@"title"]),detail,FRStageText(item[@"stage"]),[item[@"stage"] isEqual:@"producing"]?@"running":@"",^{FROpenDrama(weak,pid);});
    row[@"icon"]=@"film";row[@"delete"]=^{FRDeleteDrama(weak,item,changed);};return row;
}
@interface FRDramaListController : FRListController
@end
@implementation FRDramaListController
- (void)viewDidLoad {
    // Projects start in a conversation (@短剧助手); this list only reopens them.
    [super viewDidLoad];self.title=@"短剧项目";self.defaultIcon=@"film";self.emptyTitle=@"还没有短剧项目";self.emptyDetail=@"在对话里输入 @ 邀请短剧助手，再把剧本发给 Friday。";
    __weak typeof(self) weak=self;
    self.reloadAction=^{[FRAPI.shared get:@"/v1/dramas" completion:^(NSDictionary *v,NSError *e){
        if(e){if(e.code==404)e=[NSError errorWithDomain:@"Friday" code:404 userInfo:@{NSLocalizedDescriptionKey:@"Mac 上的 Friday 版本还不支持短剧。"}];FRShowError(weak,e);return;}
        NSMutableArray *rows=[NSMutableArray array];for(NSDictionary *item in [FRStore.shared kept:v[@"items"]])[rows addObject:FRDramaRow(weak,item,^{[weak reloadData];})];
        weak.sections=rows.count?@[@{@"rows":rows,@"footer":@"左滑删除，删除的项目会在「最近删除」保留 30 天。"}]:@[];
    }];};
}
@end

#pragma mark - Works: everything Friday made

static NSString *FRArtifactPath(NSDictionary *job,BOOL thumbnail){
    NSString *path=FRString(job[@"artifact_url"]);if(!path.length)return nil;
    if(thumbnail&&[FRString(job[@"artifact_media_type"]) hasPrefix:@"image/"]&&[job[@"artifact_thumbnail_url"] isEqual:[path stringByAppendingString:@"?thumbnail=1"]])path=job[@"artifact_thumbnail_url"];
    if(FRString(job[@"artifact_version"]).length)path=[path stringByAppendingFormat:@"%@version=%@",[path containsString:@"?"]?@"&":@"?",FRSegment(job[@"artifact_version"])];
    return path;
}
// UI 2.0 作品: drama projects as cover cards, images and videos as a grid, other files as a list.
static UIView *FRWorksPin(UIView *child,UIView *parent,UIEdgeInsets insets){child.translatesAutoresizingMaskIntoConstraints=NO;[parent addSubview:child];[NSLayoutConstraint activateConstraints:@[[child.leadingAnchor constraintEqualToAnchor:parent.leadingAnchor constant:insets.left],[child.trailingAnchor constraintEqualToAnchor:parent.trailingAnchor constant:-insets.right],[child.topAnchor constraintEqualToAnchor:parent.topAnchor constant:insets.top],[child.bottomAnchor constraintEqualToAnchor:parent.bottomAnchor constant:-insets.bottom]]];return child;}
static UIColor *FRCoverColor(void){return [UIColor colorWithRed:.118 green:.125 blue:.188 alpha:1];}

@interface FRWorksCheck : UIImageView @end
@implementation FRWorksCheck
- (instancetype)init {if((self=[super initWithImage:[UIImage systemImageNamed:@"checkmark.circle.fill" withConfiguration:[UIImageSymbolConfiguration configurationWithPointSize:22]]])){self.tintColor=FRAccent();self.backgroundColor=FRPaper();self.layer.cornerRadius=11;self.hidden=YES;self.translatesAutoresizingMaskIntoConstraints=NO;}return self;}
@end

@interface FRDramaCoverCell : UICollectionViewCell
@property(nonatomic) UILabel *title,*detail,*stage,*when;
@property(nonatomic) UIProgressView *progress;
@property(nonatomic) FRWorksCheck *check;
@end
@implementation FRDramaCoverCell
- (instancetype)initWithFrame:(CGRect)frame {
    if((self=[super initWithFrame:frame])){
        UIView *cover=[UIView new];cover.backgroundColor=FRCoverColor();cover.layer.cornerRadius=12;cover.layer.cornerCurve=kCACornerCurveContinuous;cover.clipsToBounds=YES;cover.translatesAutoresizingMaskIntoConstraints=NO;
        [cover.widthAnchor constraintEqualToConstant:96].active=YES;[cover.heightAnchor constraintEqualToConstant:128].active=YES;
        UILabel *badge=FRWorksLabel(@"短剧",[UIFont systemFontOfSize:10 weight:UIFontWeightBold],[UIColor colorWithRed:.78 green:.8 blue:1 alpha:1],1);badge.translatesAutoresizingMaskIntoConstraints=NO;[cover addSubview:badge];
        UIImageView *glyph=[[UIImageView alloc] initWithImage:[UIImage systemImageNamed:@"film" withConfiguration:[UIImageSymbolConfiguration configurationWithPointSize:22]]];glyph.tintColor=[UIColor colorWithWhite:1 alpha:.35];glyph.translatesAutoresizingMaskIntoConstraints=NO;[cover addSubview:glyph];
        _progress=[[UIProgressView alloc] initWithProgressViewStyle:UIProgressViewStyleBar];_progress.progressTintColor=[UIColor colorWithRed:.49 green:.55 blue:1 alpha:1];_progress.trackTintColor=[UIColor colorWithWhite:1 alpha:.12];_progress.translatesAutoresizingMaskIntoConstraints=NO;[cover addSubview:_progress];
        [NSLayoutConstraint activateConstraints:@[[badge.leadingAnchor constraintEqualToAnchor:cover.leadingAnchor constant:8],[badge.topAnchor constraintEqualToAnchor:cover.topAnchor constant:8],[glyph.centerXAnchor constraintEqualToAnchor:cover.centerXAnchor],[glyph.centerYAnchor constraintEqualToAnchor:cover.centerYAnchor],[_progress.leadingAnchor constraintEqualToAnchor:cover.leadingAnchor],[_progress.trailingAnchor constraintEqualToAnchor:cover.trailingAnchor],[_progress.bottomAnchor constraintEqualToAnchor:cover.bottomAnchor],[_progress.heightAnchor constraintEqualToConstant:4]]];
        _title=FRWorksLabel(@"",FRFont(18,UIFontWeightBold),FRInk(),2);_detail=FRWorksLabel(@"",FRFont(13,UIFontWeightRegular),FRInkSecondary(),2);_stage=FRWorksLabel(@"",FRFont(13,UIFontWeightSemibold),FRAccent(),1);_when=FRWorksLabel(@"",FRFont(12,UIFontWeightRegular),FRInkTertiary(),1);
        UIStackView *text=[[UIStackView alloc] initWithArrangedSubviews:@[_title,_detail,_stage,_when]];text.axis=UILayoutConstraintAxisVertical;text.spacing=4;[text setCustomSpacing:10 afterView:_detail];
        UIStackView *row=[[UIStackView alloc] initWithArrangedSubviews:@[cover,text]];row.spacing=14;row.alignment=UIStackViewAlignmentTop;FRWorksPin(row,self.contentView,UIEdgeInsetsMake(4,0,4,0));
        _check=[FRWorksCheck new];[self.contentView addSubview:_check];[NSLayoutConstraint activateConstraints:@[[_check.leadingAnchor constraintEqualToAnchor:cover.leadingAnchor constant:6],[_check.bottomAnchor constraintEqualToAnchor:cover.bottomAnchor constant:-10]]];
        self.isAccessibilityElement=YES;self.accessibilityTraits=UIAccessibilityTraitButton;
    }
    return self;
}
- (void)setSelected:(BOOL)selected {[super setSelected:selected];self.check.hidden=!selected;}
- (void)setHighlighted:(BOOL)highlighted {[super setHighlighted:highlighted];self.contentView.alpha=highlighted?.6:1;}
@end

@interface FRWorksTileCell : UICollectionViewCell
@property(nonatomic) UIImageView *image,*glyph;
@property(nonatomic) UILabel *name,*badge;
@property(nonatomic) FRWorksCheck *check;
@end
@implementation FRWorksTileCell
- (instancetype)initWithFrame:(CGRect)frame {
    if((self=[super initWithFrame:frame])){
        self.contentView.backgroundColor=FRSurface();self.contentView.layer.cornerRadius=6;self.contentView.clipsToBounds=YES;
        _image=[UIImageView new];_image.contentMode=UIViewContentModeScaleAspectFill;_image.clipsToBounds=YES;FRWorksPin(_image,self.contentView,UIEdgeInsetsZero);
        _glyph=[UIImageView new];_glyph.tintColor=FRInkSecondary();_glyph.contentMode=UIViewContentModeCenter;_name=FRWorksLabel(@"",FRFont(11,UIFontWeightRegular),FRInkSecondary(),2);_name.textAlignment=NSTextAlignmentCenter;
        UIStackView *placeholder=[[UIStackView alloc] initWithArrangedSubviews:@[_glyph,_name]];placeholder.axis=UILayoutConstraintAxisVertical;placeholder.spacing=6;placeholder.alignment=UIStackViewAlignmentCenter;placeholder.translatesAutoresizingMaskIntoConstraints=NO;[self.contentView addSubview:placeholder];
        _badge=FRWorksLabel(@"",[UIFont monospacedDigitSystemFontOfSize:10 weight:UIFontWeightSemibold],UIColor.whiteColor,1);_badge.backgroundColor=[UIColor colorWithWhite:.07 alpha:.6];_badge.layer.cornerRadius=4;_badge.clipsToBounds=YES;_badge.textAlignment=NSTextAlignmentCenter;_badge.translatesAutoresizingMaskIntoConstraints=NO;[self.contentView addSubview:_badge];
        _check=[FRWorksCheck new];[self.contentView addSubview:_check];
        [NSLayoutConstraint activateConstraints:@[[placeholder.centerYAnchor constraintEqualToAnchor:self.contentView.centerYAnchor],[placeholder.leadingAnchor constraintEqualToAnchor:self.contentView.leadingAnchor constant:6],[placeholder.trailingAnchor constraintEqualToAnchor:self.contentView.trailingAnchor constant:-6],
            [_badge.trailingAnchor constraintEqualToAnchor:self.contentView.trailingAnchor constant:-6],[_badge.bottomAnchor constraintEqualToAnchor:self.contentView.bottomAnchor constant:-6],[_badge.heightAnchor constraintEqualToConstant:16],[_badge.widthAnchor constraintGreaterThanOrEqualToConstant:30],
            [_check.leadingAnchor constraintEqualToAnchor:self.contentView.leadingAnchor constant:6],[_check.topAnchor constraintEqualToAnchor:self.contentView.topAnchor constant:6]]];
        self.isAccessibilityElement=YES;self.accessibilityTraits=UIAccessibilityTraitButton;
    }
    return self;
}
- (void)setSelected:(BOOL)selected {[super setSelected:selected];self.check.hidden=!selected;self.contentView.alpha=selected?.75:1;}
- (void)setHighlighted:(BOOL)highlighted {[super setHighlighted:highlighted];self.contentView.alpha=highlighted?.6:(self.selected?.75:1);}
@end

@interface FRWorksHeader : UICollectionReusableView
@property(nonatomic) UILabel *label;
@end
@implementation FRWorksHeader
- (instancetype)initWithFrame:(CGRect)frame {if((self=[super initWithFrame:frame])){_label=FRWorksLabel(@"",FRFont(13,UIFontWeightSemibold),FRInkSecondary(),1);_label.accessibilityTraits=UIAccessibilityTraitHeader;_label.translatesAutoresizingMaskIntoConstraints=NO;[self addSubview:_label];
    self.insetsLayoutMarginsFromSafeArea=NO;self.directionalLayoutMargins=NSDirectionalEdgeInsetsMake(14,0,6,0);UILayoutGuide *m=self.layoutMarginsGuide;[NSLayoutConstraint activateConstraints:@[[_label.leadingAnchor constraintEqualToAnchor:m.leadingAnchor],[_label.trailingAnchor constraintEqualToAnchor:m.trailingAnchor],[_label.topAnchor constraintEqualToAnchor:m.topAnchor],[_label.bottomAnchor constraintEqualToAnchor:m.bottomAnchor]]];}return self;}
@end

@interface FRWorksFilters : UICollectionReusableView
@property(nonatomic) UIStackView *pills;
@end
@implementation FRWorksFilters
- (instancetype)initWithFrame:(CGRect)frame {if((self=[super initWithFrame:frame])){UIScrollView *scroll=[UIScrollView new];scroll.showsHorizontalScrollIndicator=NO;FRWorksPin(scroll,self,UIEdgeInsetsMake(4,0,4,0));_pills=[UIStackView new];_pills.spacing=8;_pills.translatesAutoresizingMaskIntoConstraints=NO;[scroll addSubview:_pills];
    [NSLayoutConstraint activateConstraints:@[[_pills.leadingAnchor constraintEqualToAnchor:scroll.contentLayoutGuide.leadingAnchor constant:16],[_pills.trailingAnchor constraintEqualToAnchor:scroll.contentLayoutGuide.trailingAnchor constant:-16],[_pills.topAnchor constraintEqualToAnchor:scroll.contentLayoutGuide.topAnchor],[_pills.bottomAnchor constraintEqualToAnchor:scroll.contentLayoutGuide.bottomAnchor],[_pills.heightAnchor constraintEqualToAnchor:scroll.frameLayoutGuide.heightAnchor]]];}return self;}
@end

@interface FRWorksController : UICollectionViewController <QLPreviewControllerDataSource,UISearchResultsUpdating>
@property(nonatomic) NSArray *dramas;
@property(nonatomic) NSArray<NSDictionary *> *visible;
@property(nonatomic) NSMutableSet *loadingThumbnails;
@property(nonatomic) NSInteger kind;
@property(nonatomic,copy) NSString *query;
@property(nonatomic) NSURL *preview;
@property(nonatomic) BOOL opening,loaded;
@property(nonatomic) UIBarButtonItem *chooseItem;
@end
@implementation FRWorksController
- (instancetype)init {
    UICollectionViewCompositionalLayoutConfiguration *config=[UICollectionViewCompositionalLayoutConfiguration new];
    config.boundarySupplementaryItems=@[[NSCollectionLayoutBoundarySupplementaryItem boundarySupplementaryItemWithLayoutSize:[NSCollectionLayoutSize sizeWithWidthDimension:[NSCollectionLayoutDimension fractionalWidthDimension:1] heightDimension:[NSCollectionLayoutDimension estimatedDimension:46]] elementKind:@"filters" alignment:NSRectAlignmentTop]];
    __block __weak FRWorksController *owner=nil;
    UICollectionViewCompositionalLayout *layout=[[UICollectionViewCompositionalLayout alloc] initWithSectionProvider:^NSCollectionLayoutSection *(NSInteger index,id<NSCollectionLayoutEnvironment> environment){return [owner layoutSection:index environment:environment];} configuration:config];
    if((self=[super initWithCollectionViewLayout:layout]))owner=self;return self;
}
- (NSCollectionLayoutBoundarySupplementaryItem *)sectionHeader {return [NSCollectionLayoutBoundarySupplementaryItem boundarySupplementaryItemWithLayoutSize:[NSCollectionLayoutSize sizeWithWidthDimension:[NSCollectionLayoutDimension fractionalWidthDimension:1] heightDimension:[NSCollectionLayoutDimension estimatedDimension:40]] elementKind:UICollectionElementKindSectionHeader alignment:NSRectAlignmentTop];}
- (NSCollectionLayoutSection *)layoutSection:(NSInteger)index environment:(id<NSCollectionLayoutEnvironment>)environment {
    NSString *kind=index<(NSInteger)self.visible.count?self.visible[index][@"kind"]:@"files";NSCollectionLayoutSection *section;
    if([kind isEqual:@"files"]){
        UICollectionLayoutListConfiguration *list=[[UICollectionLayoutListConfiguration alloc] initWithAppearance:UICollectionLayoutListAppearancePlain];list.backgroundColor=UIColor.clearColor;list.headerMode=UICollectionLayoutListHeaderModeSupplementary;
        __weak typeof(self) weak=self;list.trailingSwipeActionsConfigurationProvider=^UISwipeActionsConfiguration *(NSIndexPath *path){NSDictionary *item=[weak itemAt:path];if(!item||weak.editing)return nil;UIContextualAction *remove=[UIContextualAction contextualActionWithStyle:UIContextualActionStyleDestructive title:@"删除" handler:^(UIContextualAction *a,UIView *v,void(^done)(BOOL)){done(YES);[weak deleteItems:@[item]];}];return [UISwipeActionsConfiguration configurationWithActions:@[remove]];};
        section=[NSCollectionLayoutSection sectionWithListConfiguration:list layoutEnvironment:environment];return section;
    }
    CGFloat inset=MAX(16,environment.container.effectiveContentSize.width>760?(environment.container.effectiveContentSize.width-728)/2:16);
    if([kind isEqual:@"drama"]){
        NSCollectionLayoutSize *size=[NSCollectionLayoutSize sizeWithWidthDimension:[NSCollectionLayoutDimension fractionalWidthDimension:1] heightDimension:[NSCollectionLayoutDimension estimatedDimension:136]];
        NSCollectionLayoutGroup *group=[NSCollectionLayoutGroup verticalGroupWithLayoutSize:size subitems:@[[NSCollectionLayoutItem itemWithLayoutSize:size]]];
        section=[NSCollectionLayoutSection sectionWithGroup:group];section.interGroupSpacing=12;
    }else{
        NSInteger columns=environment.container.effectiveContentSize.width>600?5:3;
        NSCollectionLayoutItem *item=[NSCollectionLayoutItem itemWithLayoutSize:[NSCollectionLayoutSize sizeWithWidthDimension:[NSCollectionLayoutDimension fractionalWidthDimension:1./columns] heightDimension:[NSCollectionLayoutDimension fractionalWidthDimension:1./columns]]];item.contentInsets=NSDirectionalEdgeInsetsMake(2,2,2,2);
        NSCollectionLayoutGroup *group=[NSCollectionLayoutGroup horizontalGroupWithLayoutSize:[NSCollectionLayoutSize sizeWithWidthDimension:[NSCollectionLayoutDimension fractionalWidthDimension:1] heightDimension:[NSCollectionLayoutDimension fractionalWidthDimension:1./columns]] repeatingSubitem:item count:columns];
        section=[NSCollectionLayoutSection sectionWithGroup:group];inset-=2;
    }
    section.contentInsets=NSDirectionalEdgeInsetsMake(0,inset,8,inset);section.boundarySupplementaryItems=@[[self sectionHeader]];return section;
}
- (void)viewDidLoad {
    self.navigationItem.largeTitleDisplayMode=UINavigationItemLargeTitleDisplayModeAlways;[super viewDidLoad];self.title=@"作品";self.loadingThumbnails=[NSMutableSet set];
    self.collectionView.backgroundColor=FRPaper();self.collectionView.alwaysBounceVertical=YES;self.collectionView.allowsMultipleSelectionDuringEditing=YES;self.collectionView.accessibilityIdentifier=@"works.grid";
    [self.collectionView registerClass:FRDramaCoverCell.class forCellWithReuseIdentifier:@"drama"];[self.collectionView registerClass:FRWorksTileCell.class forCellWithReuseIdentifier:@"tile"];[self.collectionView registerClass:UICollectionViewListCell.class forCellWithReuseIdentifier:@"file"];
    [self.collectionView registerClass:FRWorksHeader.class forSupplementaryViewOfKind:UICollectionElementKindSectionHeader withReuseIdentifier:@"header"];[self.collectionView registerClass:FRWorksFilters.class forSupplementaryViewOfKind:@"filters" withReuseIdentifier:@"filters"];
    self.collectionView.refreshControl=[UIRefreshControl new];[self.collectionView.refreshControl addTarget:self action:@selector(fetch) forControlEvents:UIControlEventValueChanged];
    UISearchController *search=[[UISearchController alloc] initWithSearchResultsController:nil];search.searchResultsUpdater=self;search.obscuresBackgroundDuringPresentation=NO;search.searchBar.placeholder=@"搜索作品";self.navigationItem.searchController=search;self.navigationItem.hidesSearchBarWhenScrolling=YES;self.definesPresentationContext=YES;
    self.chooseItem=[[UIBarButtonItem alloc] initWithTitle:@"选择" style:UIBarButtonItemStylePlain target:self action:@selector(toggleChoosing)];self.navigationItem.rightBarButtonItem=self.chooseItem;
    [[NSNotificationCenter defaultCenter] addObserver:self selector:@selector(storeChanged) name:FRDataChanged object:nil];
}
- (void)viewWillAppear:(BOOL)animated {[super viewWillAppear:animated];[self fetch];}
- (void)viewWillDisappear:(BOOL)animated {[super viewWillDisappear:animated];if(self.editing)[self toggleChoosing];}
- (void)dealloc {[[NSNotificationCenter defaultCenter] removeObserver:self];}
- (void)storeChanged {if(self.view.window&&!self.editing)[self render];}
- (void)fetch {__weak typeof(self) weak=self;[FRStore.shared refresh];[FRAPI.shared get:@"/v1/dramas" completion:^(NSDictionary *v,NSError *e){[weak.collectionView.refreshControl endRefreshing];weak.dramas=e?(weak.dramas?:@[]):(v[@"items"]?:@[]);weak.loaded=YES;[weak render];}];}
- (void)updateSearchResultsForSearchController:(UISearchController *)controller {NSString *text=[controller.searchBar.text stringByTrimmingCharactersInSet:NSCharacterSet.whitespaceCharacterSet];if([text isEqual:self.query?:@""])return;self.query=text;[self render];}
- (void)choose:(NSInteger)kind {self.kind=kind;[self render];}
#pragma mark Data
- (NSDictionary *)itemAt:(NSIndexPath *)path {if(path.section>=(NSInteger)self.visible.count)return nil;NSArray *items=self.visible[path.section][@"items"];return path.item<(NSInteger)items.count?items[path.item]:nil;}
- (void)render {
    NSMutableArray *dramas=[NSMutableArray array],*media=[NSMutableArray array],*files=[NSMutableArray array];NSString *query=self.query;
    BOOL(^match)(NSString *)=^BOOL(NSString *text){return !query.length||[text localizedCaseInsensitiveContainsString:query];};
    for(NSDictionary *d in [FRStore.shared kept:self.dramas])if(match(FRString(d[@"title"])))[dramas addObject:@{@"kind":@"drama",@"drama":d,@"title":FRString(d[@"title"])}];
    for(NSDictionary *job in FRStore.shared.tasks){
        if(!FRString(job[@"artifact_url"]).length||FRTaskIsActive(job))continue;
        NSString *mime=FRString(job[@"artifact_media_type"]),*name=FRString(job[@"artifact_filename"]).length?FRString(job[@"artifact_filename"]):FRString(job[@"title"]);if(!match(name))continue;
        NSString *kind=[mime hasPrefix:@"image/"]?@"image":([mime hasPrefix:@"video/"]?@"video":([mime hasPrefix:@"audio/"]?@"audio":@"file"));
        NSDictionary *item=@{@"kind":kind,@"job":job,@"title":name};[[kind isEqual:@"file"]?files:media addObject:item];
    }
    NSMutableArray *sections=[NSMutableArray array];NSInteger k=self.kind;
    if((k==0||k==1)&&dramas.count)[sections addObject:@{@"kind":@"drama",@"title":@"短剧",@"items":dramas}];
    if(k==2||k==3){NSString *want=k==2?@"image":@"video";[media filterUsingPredicate:[NSPredicate predicateWithBlock:^BOOL(NSDictionary *i,NSDictionary *b){return [i[@"kind"] isEqual:want]||(k==3&&[i[@"kind"] isEqual:@"audio"]);}]];}
    if((k==0||k==2||k==3)&&media.count)[sections addObject:@{@"kind":@"media",@"title":k==2?@"图片":(k==3?@"视频与音频":@"图片与视频"),@"items":media}];
    if((k==0||k==4)&&files.count)[sections addObject:@{@"kind":@"files",@"title":@"文件",@"items":files}];
    self.visible=sections;[self.collectionView reloadData];
    UIView *empty=nil;
    if(!sections.count&&(self.loaded||FRStore.shared.tasksLoaded)){UILabel *title=FRWorksLabel(query.length?@"没有找到相关作品":@"还没有作品",FRFont(17,UIFontWeightSemibold),FRInk(),0),*detail=FRWorksLabel(query.length?@"换个关键词试试":@"Friday 生成的图片、视频、文件和短剧项目会集中在这里。",FRFont(14,UIFontWeightRegular),FRInkSecondary(),0);title.textAlignment=detail.textAlignment=NSTextAlignmentCenter;UIStackView *s=[[UIStackView alloc] initWithArrangedSubviews:@[title,detail]];s.axis=UILayoutConstraintAxisVertical;s.spacing=6;empty=[UIView new];s.translatesAutoresizingMaskIntoConstraints=NO;[empty addSubview:s];[NSLayoutConstraint activateConstraints:@[[s.centerYAnchor constraintEqualToAnchor:empty.centerYAnchor],[s.leadingAnchor constraintEqualToAnchor:empty.leadingAnchor constant:40],[s.trailingAnchor constraintEqualToAnchor:empty.trailingAnchor constant:-40]]];}
    self.collectionView.backgroundView=empty;self.chooseItem.enabled=sections.count>0||self.editing;
}
- (UIImage *)thumbnailFor:(NSDictionary *)job {
    NSString *path=FRArtifactPath(job,YES);NSURL *file=[FRAPI.shared cachedMedia:path];
    if(file)return [UIImage imageWithContentsOfFile:file.path];
    if(![self.loadingThumbnails containsObject:path]){[self.loadingThumbnails addObject:path];__weak typeof(self) weak=self;
        [FRAPI.shared download:path filename:FRString(job[@"artifact_filename"]) completion:^(NSURL *url,NSError *e){if(!e&&!weak.editing)[weak render];}];}
    return nil;
}
#pragma mark Collection
- (NSInteger)numberOfSectionsInCollectionView:(UICollectionView *)collectionView {return self.visible.count;}
- (NSInteger)collectionView:(UICollectionView *)collectionView numberOfItemsInSection:(NSInteger)section {return [self.visible[section][@"items"] count];}
- (UICollectionReusableView *)collectionView:(UICollectionView *)collectionView viewForSupplementaryElementOfKind:(NSString *)kind atIndexPath:(NSIndexPath *)indexPath {
    if([kind isEqual:@"filters"]){
        FRWorksFilters *filters=[collectionView dequeueReusableSupplementaryViewOfKind:kind withReuseIdentifier:@"filters" forIndexPath:indexPath];for(UIView *v in filters.pills.arrangedSubviews)[v removeFromSuperview];__weak typeof(self) weak=self;
        [@[@"全部",@"短剧",@"图片",@"视频",@"文件"] enumerateObjectsUsingBlock:^(NSString *name,NSUInteger i,BOOL *stop){
            BOOL on=(NSInteger)i==weak.kind;UIButton *pill=[UIButton buttonWithType:UIButtonTypeSystem];UIButtonConfiguration *c=UIButtonConfiguration.filledButtonConfiguration;c.cornerStyle=UIButtonConfigurationCornerStyleCapsule;c.title=name;c.baseBackgroundColor=on?FRInk():FRSurface();c.baseForegroundColor=on?FRInverse():FRInk();c.contentInsets=NSDirectionalEdgeInsetsMake(8,14,8,14);
            c.titleTextAttributesTransformer=^NSDictionary *(NSDictionary *a){NSMutableDictionary *m=[a mutableCopy];m[NSFontAttributeName]=FRFont(13,on?UIFontWeightSemibold:UIFontWeightMedium);return m;};pill.configuration=c;pill.accessibilityTraits=on?UIAccessibilityTraitButton|UIAccessibilityTraitSelected:UIAccessibilityTraitButton;pill.accessibilityIdentifier=[NSString stringWithFormat:@"works.kind.%lu",(unsigned long)i];
            [pill.heightAnchor constraintGreaterThanOrEqualToConstant:36].active=YES;[pill addAction:[UIAction actionWithHandler:^(UIAction *a){[weak choose:i];}] forControlEvents:UIControlEventTouchUpInside];[filters.pills addArrangedSubview:pill];}];
        return filters;
    }
    FRWorksHeader *header=[collectionView dequeueReusableSupplementaryViewOfKind:kind withReuseIdentifier:@"header" forIndexPath:indexPath];header.label.text=self.visible[indexPath.section][@"title"];
    // List sections span the full width; grid and card sections already carry their insets.
    header.directionalLayoutMargins=NSDirectionalEdgeInsetsMake(14,[self.visible[indexPath.section][@"kind"] isEqual:@"files"]?16:0,6,16);
    return header;
}
- (UICollectionViewCell *)collectionView:(UICollectionView *)collectionView cellForItemAtIndexPath:(NSIndexPath *)indexPath {
    NSDictionary *item=[self itemAt:indexPath];NSString *kind=item[@"kind"];NSDictionary *job=item[@"job"];
    if([kind isEqual:@"drama"]){
        FRDramaCoverCell *cell=[collectionView dequeueReusableCellWithReuseIdentifier:@"drama" forIndexPath:indexPath];NSDictionary *d=item[@"drama"];NSInteger shots=[d[@"shots"] integerValue],done=[d[@"done"] integerValue];
        cell.title.text=FRString(d[@"title"]);cell.detail.text=shots?[NSString stringWithFormat:@"%ld 镜 · 时长约 %@",(long)shots,FRMinutes(d[@"seconds"])]:@"还没有分镜";
        cell.stage.text=shots?[NSString stringWithFormat:@"%@ · %ld/%ld 镜完成",FRStageText(FRString(d[@"stage"])),(long)done,(long)shots]:FRStageText(FRString(d[@"stage"]));cell.stage.textColor=[d[@"stage"] isEqual:@"ready"]?FROnline():FRAccent();
        cell.when.text=[d[@"updated"] doubleValue]>0?[@"更新于 " stringByAppendingString:FRRelativeDay([d[@"updated"] doubleValue])]:@"";cell.progress.progress=shots?(float)done/shots:0;
        cell.accessibilityLabel=[NSString stringWithFormat:@"短剧，%@，%@，%@",cell.title.text,cell.detail.text,cell.stage.text];return cell;
    }
    if([kind isEqual:@"files"]||[kind isEqual:@"file"]){
        UICollectionViewListCell *cell=[collectionView dequeueReusableCellWithReuseIdentifier:@"file" forIndexPath:indexPath];UIListContentConfiguration *c=UIListContentConfiguration.subtitleCellConfiguration;c.text=item[@"title"];
        NSDictionary *timing=[job[@"timing"] isKindOfClass:NSDictionary.class]?job[@"timing"]:@{};NSNumber *when=timing[@"finished_at"]?:job[@"created"];
        c.secondaryText=[NSString stringWithFormat:@"%@ · %@",[FRStore.shared session:job[@"session_id"]][@"name"]?:@"对话",FRRelativeDay(when.doubleValue)];c.secondaryTextProperties.color=FRInkSecondary();c.image=[UIImage systemImageNamed:@"doc"];c.imageProperties.tintColor=FRInk();
        cell.contentConfiguration=c;cell.accessories=@[[UICellAccessoryMultiselect new]];UIBackgroundConfiguration *bg=[UIBackgroundConfiguration listPlainCellConfiguration];bg.backgroundColor=FRPaper();cell.backgroundConfiguration=bg;return cell;
    }
    FRWorksTileCell *cell=[collectionView dequeueReusableCellWithReuseIdentifier:@"tile" forIndexPath:indexPath];
    UIImage *thumb=[kind isEqual:@"image"]?[self thumbnailFor:job]:nil;cell.image.image=thumb;cell.image.hidden=!thumb;
    cell.glyph.image=[UIImage systemImageNamed:[kind isEqual:@"image"]?@"photo":([kind isEqual:@"audio"]?@"waveform":@"play.rectangle") withConfiguration:[UIImageSymbolConfiguration configurationWithPointSize:20]];
    cell.glyph.hidden=cell.name.hidden=thumb!=nil;cell.name.text=item[@"title"];
    cell.badge.text=[kind isEqual:@"video"]?@"视频":([kind isEqual:@"audio"]?@"音频":nil);cell.badge.hidden=!cell.badge.text;
    cell.accessibilityLabel=[NSString stringWithFormat:@"%@，%@",@{@"image":@"图片",@"video":@"视频",@"audio":@"音频"}[kind]?:@"文件",item[@"title"]];return cell;
}
- (void)collectionView:(UICollectionView *)collectionView didSelectItemAtIndexPath:(NSIndexPath *)indexPath {
    if(self.editing){[self updateToolbar];return;}
    [collectionView deselectItemAtIndexPath:indexPath animated:YES];NSDictionary *item=[self itemAt:indexPath];
    if([item[@"kind"] isEqual:@"drama"]){FROpenDrama(self,item[@"drama"][@"id"]);return;}
    [self open:item[@"job"]];
}
- (void)collectionView:(UICollectionView *)collectionView didDeselectItemAtIndexPath:(NSIndexPath *)indexPath {if(self.editing)[self updateToolbar];}
- (UIContextMenuConfiguration *)collectionView:(UICollectionView *)collectionView contextMenuConfigurationForItemsAtIndexPaths:(NSArray<NSIndexPath *> *)indexPaths point:(CGPoint)point {
    if(self.editing||indexPaths.count!=1)return nil;NSDictionary *item=[self itemAt:indexPaths.firstObject];if(!item)return nil;__weak typeof(self) weak=self;
    NSMutableArray *actions=[NSMutableArray array];NSString *sid=item[@"job"]?item[@"job"][@"session_id"]:item[@"drama"][@"session_id"];
    if(FRString(sid).length&&item[@"job"])[actions addObject:[UIAction actionWithTitle:@"打开所在对话" image:[UIImage systemImageNamed:@"bubble.left"] identifier:nil handler:^(UIAction *a){FROpenSession(sid);}]];
    UIAction *remove=[UIAction actionWithTitle:@"删除" image:[UIImage systemImageNamed:@"trash"] identifier:nil handler:^(UIAction *a){[weak deleteItems:@[item]];}];remove.attributes=UIMenuElementAttributesDestructive;[actions addObject:remove];
    return [UIContextMenuConfiguration configurationWithIdentifier:nil previewProvider:nil actionProvider:^UIMenu *(NSArray *s){return [UIMenu menuWithChildren:actions];}];
}
#pragma mark Actions
- (void)open:(NSDictionary *)job {
    if(self.opening||!job)return;self.opening=YES;__weak typeof(self) weak=self;NSString *title=FRString(job[@"artifact_filename"]);
    UIActivityIndicatorView *spinner=[[UIActivityIndicatorView alloc] initWithActivityIndicatorStyle:UIActivityIndicatorViewStyleMedium];[spinner startAnimating];UIBarButtonItem *previous=self.navigationItem.leftBarButtonItem;self.navigationItem.leftBarButtonItem=[[UIBarButtonItem alloc] initWithCustomView:spinner];
    [FRAPI.shared download:FRArtifactPath(job,NO) filename:title completion:^(NSURL *file,NSError *e){weak.opening=NO;weak.navigationItem.leftBarButtonItem=previous;if(e){FRShowError(weak,e);return;}weak.preview=file;QLPreviewController *preview=[QLPreviewController new];preview.dataSource=weak;[weak presentViewController:preview animated:YES completion:nil];}];
}
- (NSInteger)numberOfPreviewItemsInPreviewController:(QLPreviewController *)controller {return self.preview?1:0;}
- (id<QLPreviewItem>)previewController:(QLPreviewController *)controller previewItemAtIndex:(NSInteger)index {return self.preview;}
- (void)deleteItems:(NSArray<NSDictionary *> *)items {
    __weak typeof(self) weak=self;NSMutableArray *tasks=[NSMutableArray array];
    for(NSDictionary *item in items){if(item[@"drama"])FRDeleteDrama(self,item[@"drama"],^{[weak render];});else if(item[@"job"])[tasks addObject:item[@"job"]];}
    if(!tasks.count){if(self.editing)[self toggleChoosing];return;}
    FRAskDestructive(self,tasks.count==1?@"删除这个作品？":[NSString stringWithFormat:@"删除 %lu 个作品？",(unsigned long)tasks.count],@"文件会从 Mac 上删除，生成它的那轮对话也会一起删除。无法恢复。",@"删除",^{
        NSMutableArray *ids=[NSMutableArray array];dispatch_group_t group=dispatch_group_create();NSMutableArray *failed=[NSMutableArray array];__block NSError *failure;
        for(NSDictionary *j in tasks){NSString *jid=FRString(j[@"id"]);[ids addObject:jid];dispatch_group_enter(group);[FRAPI.shared call:[FRAPI.shared pathForSession:j[@"session_id"] suffix:[@"/tasks/" stringByAppendingString:FRSegment(jid)]] method:@"DELETE" body:nil completion:^(id v,NSError *e){if(e){failure=e;[failed addObject:jid];}dispatch_group_leave(group);}];}
        [FRStore.shared hide:ids];if(weak.editing)[weak toggleChoosing];[weak render];
        dispatch_group_notify(group,dispatch_get_main_queue(),^{if(failure){FRShowError(weak,failure);[FRStore.shared unhide:failed];}});
    });
}
- (void)toggleChoosing {
    BOOL on=!self.editing;[self setEditing:on animated:YES];self.collectionView.editing=on;
    self.chooseItem.title=on?@"完成":@"选择";self.chooseItem.style=on?UIBarButtonItemStyleDone:UIBarButtonItemStylePlain;
    if(!on)for(NSIndexPath *p in self.collectionView.indexPathsForSelectedItems)[self.collectionView deselectItemAtIndexPath:p animated:NO];
    [self updateToolbar];[self.navigationController setToolbarHidden:!on animated:YES];if(!on)[self render];
}
- (void)updateToolbar {
    NSUInteger count=self.collectionView.indexPathsForSelectedItems.count;__weak typeof(self) weak=self;
    UIBarButtonItem *remove=[[UIBarButtonItem alloc] initWithTitle:count?[NSString stringWithFormat:@"删除 %lu 项",(unsigned long)count]:@"删除" style:UIBarButtonItemStylePlain target:nil action:nil];remove.tintColor=UIColor.systemRedColor;remove.enabled=count>0;
    remove.primaryAction=[UIAction actionWithTitle:remove.title image:nil identifier:nil handler:^(UIAction *a){NSMutableArray *items=[NSMutableArray array];for(NSIndexPath *p in weak.collectionView.indexPathsForSelectedItems){NSDictionary *i=[weak itemAt:p];if(i)[items addObject:i];}[weak deleteItems:items];}];
    self.toolbarItems=@[[UIBarButtonItem flexibleSpaceItem],remove];
}
@end

UIViewController *FRWorksRootController(void){return [FRWorksController new];}
UIViewController *FRDramaRootController(void){return [FRDramaListController new];}
void FRShowDramas(UIViewController *vc){[vc.navigationController pushViewController:[FRDramaListController new] animated:YES];}
void FROpenDrama(UIViewController *vc,NSString *pid){if(!pid.length)return;FRDramaController *project=[FRDramaController new];project.pid=pid;[vc.navigationController pushViewController:project animated:YES];}

#if DEBUG && TARGET_OS_SIMULATOR
// Simulator-only entry points for screenshots of second-level pages.
void FRDemoOpenAgentPage(UINavigationController *nav,NSString *page){
    if([page isEqual:@"memory"])FRShowMemory(nav.topViewController);
    else if([page isEqual:@"schedules"])FRShowSchedules(nav.topViewController);
    else if([page hasPrefix:@"drama"]){
        FRDramaController *project=[FRDramaController new];project.pid=@"p1";
        if([page isEqual:@"drama-bible"])project.initialSection=0;
        [nav pushViewController:project animated:NO];
        if([page isEqual:@"drama-shot"])dispatch_after(dispatch_time(DISPATCH_TIME_NOW,NSEC_PER_SEC),dispatch_get_main_queue(),^{[project openShot:@"b"];});
        if([page isEqual:@"drama-bible"])project.initialSection=0;
    }
}
#endif
