#import "FRNotificationController.h"
#import <UserNotifications/UserNotifications.h>
#import <UserNotificationsUI/UserNotificationsUI.h>
@interface FRNotificationController ()
@property(nonatomic) UILabel *status,*titleLabel,*detail;
@property(nonatomic) UIImageView *glyph;
@property(nonatomic) UIStackView *stack;
@end
@implementation FRNotificationController
- (void)viewDidLoad {
    [super viewDidLoad];self.view.backgroundColor=UIColor.secondarySystemBackgroundColor;self.preferredContentSize=CGSizeMake(360,164);
    self.glyph=[[UIImageView alloc] initWithImage:[UIImage systemImageNamed:@"checkmark.circle.fill"]];self.glyph.tintColor=[UIColor colorWithRed:.48 green:.87 blue:.86 alpha:1];self.glyph.contentMode=UIViewContentModeScaleAspectFit;[self.glyph.widthAnchor constraintEqualToConstant:22].active=YES;
    self.status=[UILabel new];self.status.font=[[UIFontMetrics metricsForTextStyle:UIFontTextStyleCaption1] scaledFontForFont:[UIFont systemFontOfSize:12 weight:UIFontWeightMedium]];self.status.adjustsFontForContentSizeCategory=YES;self.status.textColor=UIColor.secondaryLabelColor;
    UIStackView *head=[[UIStackView alloc] initWithArrangedSubviews:@[self.glyph,self.status]];head.spacing=8;head.alignment=UIStackViewAlignmentCenter;[head.heightAnchor constraintGreaterThanOrEqualToConstant:24].active=YES;
    self.titleLabel=[UILabel new];self.titleLabel.font=[[UIFontMetrics metricsForTextStyle:UIFontTextStyleHeadline] scaledFontForFont:[UIFont systemFontOfSize:18 weight:UIFontWeightSemibold]];self.titleLabel.adjustsFontForContentSizeCategory=YES;self.titleLabel.textColor=UIColor.labelColor;self.titleLabel.numberOfLines=0;
    self.detail=[UILabel new];self.detail.font=[[UIFontMetrics metricsForTextStyle:UIFontTextStyleSubheadline] scaledFontForFont:[UIFont systemFontOfSize:14]];self.detail.adjustsFontForContentSizeCategory=YES;self.detail.textColor=UIColor.secondaryLabelColor;self.detail.numberOfLines=0;
    UIStackView *stack=[[UIStackView alloc] initWithArrangedSubviews:@[head,self.titleLabel,self.detail]];self.stack=stack;stack.axis=UILayoutConstraintAxisVertical;stack.spacing=10;stack.translatesAutoresizingMaskIntoConstraints=NO;[self.view addSubview:stack];[NSLayoutConstraint activateConstraints:@[[stack.leadingAnchor constraintEqualToAnchor:self.view.leadingAnchor constant:20],[stack.trailingAnchor constraintEqualToAnchor:self.view.trailingAnchor constant:-20],[stack.topAnchor constraintEqualToAnchor:self.view.topAnchor constant:18],[stack.bottomAnchor constraintLessThanOrEqualToAnchor:self.view.bottomAnchor constant:-18]]];
}
- (void)viewDidLayoutSubviews {
    [super viewDidLayoutSubviews];
    CGSize fit=[self.stack systemLayoutSizeFittingSize:CGSizeMake(MAX(1,self.view.bounds.size.width-40),UILayoutFittingCompressedSize.height) withHorizontalFittingPriority:UILayoutPriorityRequired verticalFittingPriority:UILayoutPriorityFittingSizeLevel];
    CGFloat height=MAX(144,ceil(fit.height)+36);if(fabs(self.preferredContentSize.height-height)>1)self.preferredContentSize=CGSizeMake(self.view.bounds.size.width,height);
}
- (void)didReceiveNotification:(UNNotification *)notification {
    [self displayContent:notification.request.content];
}
- (void)displayContent:(UNNotificationContent *)content {
    NSString *category=content.categoryIdentifier;
    NSDictionary *styles=@{@"FRIDAY_RESULT":@[@"已完成",@"checkmark.circle.fill",@"结果已经准备好",@"打开原会话，查看回复与生成的内容。"],@"FRIDAY_INPUT":@[@"等待你",@"bubble.left.and.text.bubble.right",@"需要你补充一点信息",@"解锁后可以直接回复，或回到会话继续。"],@"FRIDAY_APPROVAL":@[@"待确认",@"hand.raised.fill",@"有一项操作等待确认",@"解锁查看具体请求，由你决定是否继续。"],@"FRIDAY_STOPPED":@[@"任务更新",@"exclamationmark.circle",@"这项任务需要留意",@"打开会话查看原因和已保留的结果。"]};
    NSArray *style=styles[category]?:styles[@"FRIDAY_RESULT"];self.status.text=style[0];self.glyph.image=[UIImage systemImageNamed:style[1]];self.titleLabel.text=style[2];self.detail.text=style[3];
    if([content.userInfo[@"kind"] isEqual:@"task_partial"]){self.status.text=@"部分完成";self.titleLabel.text=@"已有部分结果";self.detail.text=@"回到会话查看已完成的内容与下一步。";}
    if([content.userInfo[@"kind"] isEqual:@"notification_reply_pending"]){self.status.text=@"待发送";self.titleLabel.text=@"补充内容已保留";self.detail.text=@"暂时无法连接，打开 Friday 后会重试发送。";self.glyph.image=[UIImage systemImageNamed:@"arrow.clockwise.circle"]; }
    if([content.userInfo[@"kind"] isEqual:@"upgrade_completed"]){self.status.text=@"已更新";self.titleLabel.text=@"Friday 已完成升级";self.detail.text=@"回到原会话，查看这次更新与验证记录。";}
    if([content.userInfo[@"kind"] isEqual:@"upgrade_failed"]){self.status.text=@"升级状态";self.titleLabel.text=@"这次升级需要留意";self.detail.text=@"打开原会话，查看验证与恢复结果。";}
    self.glyph.tintColor=[category isEqual:@"FRIDAY_APPROVAL"]?[UIColor colorWithRed:.94 green:.74 blue:.39 alpha:1]:[UIColor colorWithRed:.48 green:.87 blue:.86 alpha:1];
}
@end
