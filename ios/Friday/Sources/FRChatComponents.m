#import "FRChatComponents.h"
#import <objc/runtime.h>

UIColor *FRShade(CGFloat dark) {
    return [UIColor colorWithDynamicProvider:^UIColor *(UITraitCollection *traits){
        return [UIColor colorWithWhite:traits.userInterfaceStyle==UIUserInterfaceStyleDark?dark:1-dark*.62 alpha:1];
    }];
}
static char FRBorderColorKey;
static UIColor *FRPair(uint32_t light, uint32_t dark) {
    UIColor *(^rgb)(uint32_t)=^UIColor *(uint32_t v){return [UIColor colorWithRed:((v>>16)&255)/255. green:((v>>8)&255)/255. blue:(v&255)/255. alpha:1];};
    UIColor *l=rgb(light),*d=rgb(dark);
    return [UIColor colorWithDynamicProvider:^UIColor *(UITraitCollection *t){return t.userInterfaceStyle==UIUserInterfaceStyleDark?d:l;}];
}
UIColor *FRInk(void){return FRPair(0x111113,0xF4F4F2);}
UIColor *FRInkSecondary(void){return FRPair(0x55565C,0xB4B5BC);}
UIColor *FRInkTertiary(void){return FRPair(0x6B6C72,0x8C8D96);}
UIColor *FRPaper(void){return FRPair(0xFFFFFF,0x0E0E10);}
UIColor *FRSurface(void){return FRPair(0xF1F1EE,0x1F2024);}
UIColor *FRHairline(void){return FRPair(0xE6E6E3,0x2C2D33);}
UIColor *FRAccent(void){return FRPair(0x2F45FF,0x8B98FF);}
UIColor *FRAccentSoft(void){return FRPair(0xEEF0FF,0x23264A);}
UIColor *FRAmber(void){return FRPair(0x9A4A00,0xFFB45C);}
UIColor *FRAmberSoft(void){return FRPair(0xFFF4E0,0x3A2A14);}
UIColor *FROnline(void){return FRPair(0x0F8A55,0x3CCB8A);}
UIColor *FRInverse(void){return FRPair(0xFFFFFF,0x111113);}
UIFont *FRFont(CGFloat size, UIFontWeight weight){return [[UIFontMetrics defaultMetrics] scaledFontForFont:[UIFont systemFontOfSize:size weight:weight]];}
UIFont *FRMono(CGFloat size){return [[UIFontMetrics defaultMetrics] scaledFontForFont:[UIFont monospacedDigitSystemFontOfSize:size weight:UIFontWeightMedium]];}
NSString *FREstimate(NSTimeInterval seconds){
    NSInteger minutes=MAX(1,(NSInteger)ceil(seconds/60));
    if(minutes<60)return [NSString stringWithFormat:@"约 %ld 分",(long)minutes];
    return minutes%60?[NSString stringWithFormat:@"约 %ld 小时 %ld 分",(long)minutes/60,(long)minutes%60]:[NSString stringWithFormat:@"约 %ld 小时",(long)minutes/60];
}
void FRSetBorder(UIView *view, UIColor *color, CGFloat width) {
    BOOL registered=objc_getAssociatedObject(view,&FRBorderColorKey)!=nil;
    objc_setAssociatedObject(view,&FRBorderColorKey,color,OBJC_ASSOCIATION_RETAIN_NONATOMIC);
    view.layer.borderWidth=width;view.layer.borderColor=[color resolvedColorWithTraitCollection:view.traitCollection].CGColor;
    if(!registered)[view registerForTraitChanges:@[UITraitUserInterfaceStyle.class] withHandler:^(UIView *v,UITraitCollection *previous){
        UIColor *current=objc_getAssociatedObject(v,&FRBorderColorKey);v.layer.borderColor=[current resolvedColorWithTraitCollection:v.traitCollection].CGColor;
    }];
}
void FRToast(UIViewController *controller, NSString *text, NSString *actionTitle, void(^action)(void)) {
    UIView *host=controller.view.window?:controller.view;if(!host)return;
    [[host viewWithTag:6190] removeFromSuperview];
    UIVisualEffectView *toast=[[UIVisualEffectView alloc] initWithEffect:[UIBlurEffect effectWithStyle:UIBlurEffectStyleSystemThickMaterial]];toast.tag=6190;
    toast.translatesAutoresizingMaskIntoConstraints=NO;toast.layer.cornerRadius=22;toast.clipsToBounds=YES;FRSetBorder(toast,UIColor.separatorColor,.5);toast.accessibilityIdentifier=@"toast";
    UILabel *label=[UILabel new];label.text=text;label.font=[UIFont preferredFontForTextStyle:UIFontTextStyleSubheadline];label.adjustsFontForContentSizeCategory=YES;label.numberOfLines=2;
    UIStackView *row=[[UIStackView alloc] initWithArrangedSubviews:@[label]];row.spacing=12;row.alignment=UIStackViewAlignmentCenter;row.translatesAutoresizingMaskIntoConstraints=NO;[toast.contentView addSubview:row];
    __weak UIView *weakToast=toast;
    if(actionTitle.length&&action){
        UIButton *button=[UIButton buttonWithType:UIButtonTypeSystem];UIButtonConfiguration *c=UIButtonConfiguration.plainButtonConfiguration;
        c.attributedTitle=[[NSAttributedString alloc] initWithString:actionTitle attributes:@{NSFontAttributeName:[UIFont preferredFontForTextStyle:UIFontTextStyleHeadline]}];c.baseForegroundColor=UIColor.systemBlueColor;c.contentInsets=NSDirectionalEdgeInsetsMake(6,6,6,6);button.configuration=c;button.accessibilityIdentifier=@"toast.action";
        [button setContentHuggingPriority:UILayoutPriorityRequired forAxis:UILayoutConstraintAxisHorizontal];
        [button addAction:[UIAction actionWithHandler:^(UIAction *a){[weakToast removeFromSuperview];action();}] forControlEvents:UIControlEventTouchUpInside];[row addArrangedSubview:button];
    }
    [host addSubview:toast];
    [NSLayoutConstraint activateConstraints:@[[toast.centerXAnchor constraintEqualToAnchor:host.centerXAnchor],[toast.leadingAnchor constraintGreaterThanOrEqualToAnchor:host.safeAreaLayoutGuide.leadingAnchor constant:16],[toast.bottomAnchor constraintEqualToAnchor:host.safeAreaLayoutGuide.bottomAnchor constant:-64],[toast.heightAnchor constraintGreaterThanOrEqualToConstant:44],
        [row.leadingAnchor constraintEqualToAnchor:toast.contentView.leadingAnchor constant:18],[row.trailingAnchor constraintEqualToAnchor:toast.contentView.trailingAnchor constant:-10],[row.topAnchor constraintEqualToAnchor:toast.contentView.topAnchor constant:6],[row.bottomAnchor constraintEqualToAnchor:toast.contentView.bottomAnchor constant:-6]]];
    UIAccessibilityPostNotification(UIAccessibilityAnnouncementNotification,text);
    toast.alpha=0;[UIView animateWithDuration:UIAccessibilityIsReduceMotionEnabled()?0:.2 animations:^{toast.alpha=1;}];
    dispatch_after(dispatch_time(DISPATCH_TIME_NOW,(int64_t)(4.5*NSEC_PER_SEC)),dispatch_get_main_queue(),^{UIView *t=weakToast;if(!t)return;[UIView animateWithDuration:.25 animations:^{t.alpha=0;} completion:^(BOOL done){[t removeFromSuperview];}];});
}

static UIFont *FRComponentFont(CGFloat size, UIFontWeight weight) {
    return [[UIFontMetrics metricsForTextStyle:UIFontTextStyleSubheadline] scaledFontForFont:[UIFont systemFontOfSize:size weight:weight]];
}
static UILabel *FRComponentLabel(NSString *text, CGFloat size, UIFontWeight weight) {
    UILabel *label=[UILabel new];label.text=text;label.font=FRComponentFont(size,weight);label.adjustsFontForContentSizeCategory=YES;label.numberOfLines=0;
    [label setContentCompressionResistancePriority:UILayoutPriorityRequired forAxis:UILayoutConstraintAxisVertical];return label;
}
UIButton *FRActionButton(NSString *title, NSString *symbol, FRActionStyle style, void(^action)(UIButton *)) {
    UIButton *button=[UIButton buttonWithType:UIButtonTypeSystem];
    UIButtonConfiguration *config=style==FRActionStylePrimary?UIButtonConfiguration.filledButtonConfiguration:UIButtonConfiguration.plainButtonConfiguration;
    config.title=title;config.image=symbol.length?[UIImage systemImageNamed:symbol withConfiguration:[UIImageSymbolConfiguration configurationWithPointSize:16]]:nil;
    config.preferredSymbolConfigurationForImage=[UIImageSymbolConfiguration configurationWithPointSize:15 weight:UIImageSymbolWeightRegular];config.imagePadding=6;config.cornerStyle=UIButtonConfigurationCornerStyleMedium;config.contentInsets=NSDirectionalEdgeInsetsMake(10,12,10,12);
    config.baseBackgroundColor=style==FRActionStylePrimary?FRInk():UIColor.clearColor;
    config.baseForegroundColor=style==FRActionStylePrimary?FRInverse():(style==FRActionStyleDestructive?UIColor.systemRedColor:FRInk());
    config.titleTextAttributesTransformer=^NSDictionary *(NSDictionary *attributes){NSMutableDictionary *result=[attributes mutableCopy];result[NSFontAttributeName]=FRComponentFont(14,UIFontWeightSemibold);return result;};
    button.configuration=config;button.titleLabel.adjustsFontForContentSizeCategory=YES;button.titleLabel.numberOfLines=0;button.titleLabel.lineBreakMode=NSLineBreakByWordWrapping;
    [button.heightAnchor constraintGreaterThanOrEqualToConstant:44].active=YES;
    if(action)[button addAction:[UIAction actionWithHandler:^(UIAction *a){action((UIButton *)a.sender);}] forControlEvents:UIControlEventTouchUpInside];return button;
}
static UIImage *FRQuietSymbol(NSString *symbol) {
    UIImage *image=[UIImage systemImageNamed:symbol withConfiguration:[UIImageSymbolConfiguration configurationWithPointSize:16 weight:UIImageSymbolWeightRegular]];
    UIGraphicsImageRenderer *renderer=[[UIGraphicsImageRenderer alloc] initWithSize:CGSizeMake(18,18)];
    return [[renderer imageWithActions:^(UIGraphicsImageRendererContext *context){CGFloat scale=MIN(16/image.size.width,16/image.size.height);CGSize size=CGSizeMake(image.size.width*scale,image.size.height*scale);[image drawInRect:CGRectMake((18-size.width)/2,(18-size.height)/2,size.width,size.height)];}] imageWithRenderingMode:UIImageRenderingModeAlwaysTemplate];
}
UIButton *FRQuietButton(NSString *title, NSString *symbol, NSString *accessibilityLabel, void(^action)(UIButton *)) {
    UIButton *button=[UIButton buttonWithType:UIButtonTypeSystem];UIButtonConfiguration *config=UIButtonConfiguration.plainButtonConfiguration;
    config.title=title;config.image=FRQuietSymbol(symbol);config.preferredSymbolConfigurationForImage=[UIImageSymbolConfiguration configurationWithPointSize:16 weight:UIImageSymbolWeightRegular];config.imagePadding=6;config.contentInsets=NSDirectionalEdgeInsetsMake(8,8,8,8);config.baseForegroundColor=UIColor.secondaryLabelColor;
    config.titleTextAttributesTransformer=^NSDictionary *(NSDictionary *attrs){NSMutableDictionary *result=[attrs mutableCopy];result[NSFontAttributeName]=FRComponentFont(12,UIFontWeightMedium);return result;};button.configuration=config;button.accessibilityLabel=accessibilityLabel;button.titleLabel.numberOfLines=0;
    [button.heightAnchor constraintGreaterThanOrEqualToConstant:44].active=YES;[button.widthAnchor constraintGreaterThanOrEqualToConstant:44].active=YES;
    if(!title.length)[button.widthAnchor constraintEqualToConstant:44].active=YES;
    if(action)[button addAction:[UIAction actionWithHandler:^(UIAction *event){action((UIButton *)event.sender);}] forControlEvents:UIControlEventTouchUpInside];return button;
}
UIButton *FRCopyButton(NSString *label, NSString *(^textProvider)(void)) {
    __block NSUInteger feedbackVersion=0;
    UIButton *button=FRQuietButton(nil,@"doc.on.doc",label,^(UIButton *sender){
        NSString *text=textProvider?textProvider():nil;if(!text)return;
        UIPasteboard.generalPasteboard.string=text;
        UIButtonConfiguration *config=sender.configuration;config.image=FRQuietSymbol(@"checkmark");sender.configuration=config;
        sender.accessibilityValue=@"已复制";UIAccessibilityPostNotification(UIAccessibilityAnnouncementNotification,@"已复制");
        NSUInteger version=++feedbackVersion;__weak UIButton *weak=sender;
        dispatch_after(dispatch_time(DISPATCH_TIME_NOW,1800*NSEC_PER_MSEC),dispatch_get_main_queue(),^{
            UIButton *current=weak;if(!current||version!=feedbackVersion)return;
            UIButtonConfiguration *restored=current.configuration;restored.image=FRQuietSymbol(@"doc.on.doc");current.configuration=restored;current.accessibilityValue=nil;
        });
    });return button;
}
UIButton *FRLatestButton(void(^action)(UIButton *)) {
    UIButton *button=FRQuietButton(nil,@"arrow.down",@"回到最新消息",action);
    button.backgroundColor=UIColor.secondarySystemBackgroundColor;button.layer.cornerRadius=22;FRSetBorder(button,UIColor.separatorColor,.5);
    button.accessibilityIdentifier=@"chat.latest";return button;
}
void FRComponentSetBusy(UIButton *button, BOOL busy) {
    if(button.configuration){UIButtonConfiguration *config=button.configuration;config.showsActivityIndicator=busy;button.configuration=config;}
    else {
        // Never convert legacy navigation/image buttons to UIButtonConfiguration:
        // conversion replaces their font, insets and image sizing on first use.
        UIActivityIndicatorView *spinner=(id)[button viewWithTag:6099];
        if(busy&&!spinner){spinner=[[UIActivityIndicatorView alloc] initWithActivityIndicatorStyle:UIActivityIndicatorViewStyleMedium];spinner.tag=6099;spinner.userInteractionEnabled=NO;spinner.translatesAutoresizingMaskIntoConstraints=NO;[button addSubview:spinner];[NSLayoutConstraint activateConstraints:@[[spinner.centerXAnchor constraintEqualToAnchor:button.centerXAnchor],[spinner.centerYAnchor constraintEqualToAnchor:button.centerYAnchor]]];}
        if(busy)[spinner startAnimating];else [spinner removeFromSuperview];
    }
    button.enabled=!busy;button.accessibilityValue=busy?@"正在处理":nil;
}

@implementation FRChatCard
- (instancetype)initWithTitle:(NSString *)title symbol:(NSString *)symbol {
    if((self=[super initWithFrame:CGRectZero])){
        self.backgroundColor=UIColor.secondarySystemBackgroundColor;self.layer.cornerRadius=14;FRSetBorder(self,UIColor.separatorColor,.5);
        _content=[UIStackView new];_content.axis=UILayoutConstraintAxisVertical;_content.spacing=8;_content.translatesAutoresizingMaskIntoConstraints=NO;[self addSubview:_content];
        [NSLayoutConstraint activateConstraints:@[[_content.leadingAnchor constraintEqualToAnchor:self.leadingAnchor constant:14],[_content.trailingAnchor constraintEqualToAnchor:self.trailingAnchor constant:-14],[_content.topAnchor constraintEqualToAnchor:self.topAnchor constant:14],[_content.bottomAnchor constraintEqualToAnchor:self.bottomAnchor constant:-14]]];
        UIImageView *icon=[[UIImageView alloc] initWithImage:[UIImage systemImageNamed:symbol]];icon.contentMode=UIViewContentModeScaleAspectFit;icon.tintColor=UIColor.secondaryLabelColor;[NSLayoutConstraint activateConstraints:@[[icon.widthAnchor constraintEqualToConstant:16],[icon.heightAnchor constraintEqualToConstant:16]]];
        _titleLabel=FRComponentLabel(title,13,UIFontWeightMedium);_heading=[[UIStackView alloc] initWithArrangedSubviews:@[icon,_titleLabel]];_heading.spacing=8;_heading.alignment=UIStackViewAlignmentCenter;[_content addArrangedSubview:_heading];
    }return self;
}
- (UILabel *)addDetail:(NSString *)text {UILabel *label=FRComponentLabel(text,13,UIFontWeightRegular);label.textColor=UIColor.secondaryLabelColor;[self.content addArrangedSubview:label];return label;}
@end
@implementation FRPermissionCard
- (instancetype)initWithTitle:(NSString *)title detail:(NSString *)detail buttonTitle:(NSString *)buttonTitle review:(void(^)(UIButton *))review {
    if((self=[super initWithTitle:title symbol:@"lock.shield"])) {
        UILabel *description=[self addDetail:detail];description.numberOfLines=2;description.textColor=UIColor.secondaryLabelColor;
        UIButton *button=FRQuietButton(@"查看",@"chevron.right",buttonTitle,review);[button setContentHuggingPriority:UILayoutPriorityRequired forAxis:UILayoutConstraintAxisHorizontal];[self.heading addArrangedSubview:button];self.accessibilityIdentifier=@"component.permission";
    }return self;
}
@end
@implementation FROperationCard
- (instancetype)initWithTitle:(NSString *)title detail:(NSString *)detail {if((self=[super initWithTitle:title symbol:@"slider.horizontal.3"])){self.backgroundColor=UIColor.clearColor;FRSetBorder(self,UIColor.clearColor,0);if(detail.length)[self addDetail:detail];self.accessibilityIdentifier=@"component.operation";}return self;}
@end
@implementation FRStatusCard
- (instancetype)initWithTitle:(NSString *)title detail:(NSString *)detail error:(BOOL)error {if((self=[super initWithTitle:title symbol:error?@"exclamationmark.circle":@"circle.dotted"])){self.backgroundColor=UIColor.clearColor;FRSetBorder(self,UIColor.clearColor,0);[self addDetail:detail];self.accessibilityIdentifier=error?@"component.error":@"component.status";}return self;}
@end
@implementation FRMediaCard
- (instancetype)initWithFilename:(NSString *)filename kind:(NSString *)kind symbol:(NSString *)symbol ready:(BOOL)ready error:(NSString *)error action:(void(^)(UIButton *))action {
    if((self=[super initWithTitle:filename.length?filename:kind symbol:symbol])){
        self.titleLabel.numberOfLines=2;self.titleLabel.lineBreakMode=NSLineBreakByTruncatingMiddle;
        if(error.length)[self addDetail:error];
        _openButton=FRQuietButton(nil,ready?@"arrow.down.to.line":(error.length?@"arrow.clockwise":@"ellipsis"),ready?[@"打开原文件：" stringByAppendingString:filename?:kind]:(error.length?@"重新加载附件":@"正在加载附件"),action);[_openButton setContentHuggingPriority:UILayoutPriorityRequired forAxis:UILayoutConstraintAxisHorizontal];[self.heading addArrangedSubview:_openButton];
        FRComponentSetBusy(_openButton,!ready&&!error.length);self.accessibilityIdentifier=@"component.media";
    }return self;
}
- (void)installPreview:(UIView *)preview height:(CGFloat)height {
    [preview removeFromSuperview];preview.layer.cornerRadius=10;preview.clipsToBounds=YES;[preview.heightAnchor constraintEqualToConstant:height].active=YES;[self.content insertArrangedSubview:preview atIndex:0];
}
@end
@implementation FRImageCard
- (void)setPreviewImage:(UIImage *)image action:(void(^)(UIButton *))action {
    UIButton *preview=[UIButton buttonWithType:UIButtonTypeCustom];UIImageView *imageView=[[UIImageView alloc] initWithImage:image];imageView.contentMode=UIViewContentModeScaleAspectFit;imageView.translatesAutoresizingMaskIntoConstraints=NO;[preview addSubview:imageView];
    [NSLayoutConstraint activateConstraints:@[[imageView.leadingAnchor constraintEqualToAnchor:preview.leadingAnchor],[imageView.trailingAnchor constraintEqualToAnchor:preview.trailingAnchor],[imageView.topAnchor constraintEqualToAnchor:preview.topAnchor],[imageView.bottomAnchor constraintEqualToAnchor:preview.bottomAnchor]]];
    preview.accessibilityLabel=@"查看原图";[preview addAction:[UIAction actionWithHandler:^(UIAction *a){action((UIButton *)a.sender);}] forControlEvents:UIControlEventTouchUpInside];[self installPreview:preview height:210];self.accessibilityIdentifier=@"component.image";
}
@end
@implementation FRVideoCard @end
@implementation FRAudioCard @end
@implementation FRFileCard @end

@interface FRPermissionController ()
@property(nonatomic,copy) NSString *detailText,*contextText,*allowTitle;
@property(nonatomic) BOOL canDeny,submitting;
@property(nonatomic) UIStackView *actions;
@property(nonatomic) UILabel *errorLabel;
@end
// The sheet's buttons, including the side-by-side choice row.
static NSArray<UIButton *> *FRSheetButtons(UIStackView *stack){NSMutableArray *buttons=[NSMutableArray array];for(UIView *v in stack.arrangedSubviews){if([v isKindOfClass:UIButton.class])[buttons addObject:v];else if([v isKindOfClass:UIStackView.class])[buttons addObjectsFromArray:FRSheetButtons((UIStackView *)v)];}return buttons;}
@implementation FRPermissionController
- (instancetype)initWithTitle:(NSString *)title detail:(NSString *)detail context:(NSString *)context allowTitle:(NSString *)allowTitle canDeny:(BOOL)canDeny {
    if((self=[super init])){self.title=title;self.detailText=detail;self.contextText=context;self.allowTitle=allowTitle;self.canDeny=canDeny;self.modalPresentationStyle=UIModalPresentationPageSheet;}return self;
}
- (void)viewDidLoad {
    [super viewDidLoad];self.view.backgroundColor=UIColor.systemBackgroundColor;self.view.accessibilityIdentifier=@"permission.sheet";
    self.sheetPresentationController.detents=@[UISheetPresentationControllerDetent.mediumDetent,UISheetPresentationControllerDetent.largeDetent];self.sheetPresentationController.selectedDetentIdentifier=UIContentSizeCategoryIsAccessibilityCategory(self.traitCollection.preferredContentSizeCategory)?UISheetPresentationControllerDetentIdentifierLarge:UISheetPresentationControllerDetentIdentifierMedium;self.sheetPresentationController.prefersGrabberVisible=YES;self.presentationController.delegate=self;
    UIScrollView *scroll=[UIScrollView new];scroll.translatesAutoresizingMaskIntoConstraints=NO;[self.view addSubview:scroll];
    UIStackView *details=[UIStackView new];details.axis=UILayoutConstraintAxisVertical;details.spacing=12;details.translatesAutoresizingMaskIntoConstraints=NO;[scroll addSubview:details];
    // UI 2.0: an amber tile names what is asked; the request reads in full before the two choices.
    UIImageView *tile=[[UIImageView alloc] initWithImage:[UIImage systemImageNamed:@"exclamationmark.shield" withConfiguration:[UIImageSymbolConfiguration configurationWithPointSize:20 weight:UIImageSymbolWeightMedium]]];tile.tintColor=FRAmber();tile.backgroundColor=FRAmberSoft();tile.contentMode=UIViewContentModeCenter;tile.layer.cornerRadius=12;tile.layer.cornerCurve=kCACornerCurveContinuous;tile.translatesAutoresizingMaskIntoConstraints=NO;[tile.widthAnchor constraintEqualToConstant:44].active=YES;[tile.heightAnchor constraintEqualToConstant:44].active=YES;
    UILabel *heading=FRComponentLabel(self.title,19,UIFontWeightBold);heading.accessibilityTraits=UIAccessibilityTraitHeader;UILabel *context=FRComponentLabel(self.contextText,13,UIFontWeightRegular);context.textColor=FRInkSecondary();
    UIStackView *titles=[[UIStackView alloc] initWithArrangedSubviews:@[heading,context]];titles.axis=UILayoutConstraintAxisVertical;titles.spacing=2;
    UIStackView *top=[[UIStackView alloc] initWithArrangedSubviews:@[tile,titles]];top.spacing=12;top.alignment=UIStackViewAlignmentCenter;[details addArrangedSubview:top];[details setCustomSpacing:16 afterView:top];
    UILabel *description=FRComponentLabel(self.detailText,15,UIFontWeightRegular);description.lineBreakMode=NSLineBreakByCharWrapping;description.accessibilityIdentifier=@"permission.description";[details addArrangedSubview:description];
    _errorLabel=FRComponentLabel(@"",13,UIFontWeightRegular);_errorLabel.textColor=FRAmber();_errorLabel.hidden=YES;[details addArrangedSubview:_errorLabel];
    _actions=[UIStackView new];_actions.axis=UILayoutConstraintAxisVertical;_actions.spacing=8;_actions.translatesAutoresizingMaskIntoConstraints=NO;[self.view addSubview:_actions];__weak typeof(self) weak=self;
    UIButton *allow=FRActionButton(self.allowTitle,nil,FRActionStylePrimary,^(UIButton *b){[weak submit:@"approved" button:b];});allow.accessibilityIdentifier=@"permission.allow";
    UIStackView *choices=[[UIStackView alloc] initWithArrangedSubviews:@[allow]];choices.spacing=10;choices.distribution=UIStackViewDistributionFillEqually;
    // Side by side, unless large text needs the full width for each choice.
    choices.axis=UIContentSizeCategoryIsAccessibilityCategory(self.traitCollection.preferredContentSizeCategory)?UILayoutConstraintAxisVertical:UILayoutConstraintAxisHorizontal;
    if(self.canDeny){UIButton *deny=FRActionButton(@"拒绝",nil,FRActionStyleSecondary,^(UIButton *b){[weak submit:@"denied" button:b];});deny.accessibilityIdentifier=@"permission.deny";deny.accessibilityLabel=@"拒绝此次操作";
        UIButtonConfiguration *c=[UIButtonConfiguration filledButtonConfiguration];UIButtonConfiguration *old=deny.configuration;c.title=old.title;c.titleTextAttributesTransformer=old.titleTextAttributesTransformer;c.cornerStyle=old.cornerStyle;c.contentInsets=old.contentInsets;c.baseBackgroundColor=FRSurface();c.baseForegroundColor=FRInk();deny.configuration=c;[choices insertArrangedSubview:deny atIndex:0];}
    for(UIButton *b in choices.arrangedSubviews)[b.heightAnchor constraintGreaterThanOrEqualToConstant:52].active=YES;
    [_actions addArrangedSubview:choices];
    UIButton *later=FRActionButton(self.canDeny?@"稍后再说 · 任务会等你":@"取消",nil,FRActionStyleSecondary,^(UIButton *b){[weak dismissViewControllerAnimated:YES completion:^{if(weak.closeHandler)weak.closeHandler();}];});later.accessibilityIdentifier=@"permission.later";[_actions addArrangedSubview:later];
    [NSLayoutConstraint activateConstraints:@[[scroll.topAnchor constraintEqualToAnchor:self.view.safeAreaLayoutGuide.topAnchor constant:24],[scroll.leadingAnchor constraintEqualToAnchor:self.view.safeAreaLayoutGuide.leadingAnchor],[scroll.trailingAnchor constraintEqualToAnchor:self.view.safeAreaLayoutGuide.trailingAnchor],[scroll.bottomAnchor constraintEqualToAnchor:_actions.topAnchor constant:-16],[details.leadingAnchor constraintEqualToAnchor:scroll.contentLayoutGuide.leadingAnchor constant:20],[details.trailingAnchor constraintEqualToAnchor:scroll.contentLayoutGuide.trailingAnchor constant:-20],[details.topAnchor constraintEqualToAnchor:scroll.contentLayoutGuide.topAnchor],[details.bottomAnchor constraintEqualToAnchor:scroll.contentLayoutGuide.bottomAnchor],[details.widthAnchor constraintEqualToAnchor:scroll.frameLayoutGuide.widthAnchor constant:-40],[_actions.leadingAnchor constraintEqualToAnchor:self.view.safeAreaLayoutGuide.leadingAnchor constant:20],[_actions.trailingAnchor constraintEqualToAnchor:self.view.safeAreaLayoutGuide.trailingAnchor constant:-20],[_actions.bottomAnchor constraintEqualToAnchor:self.view.safeAreaLayoutGuide.bottomAnchor constant:-12]]];
}
- (void)submit:(NSString *)decision button:(UIButton *)button {if(self.submitting||!self.decisionHandler)return;self.submitting=YES;self.modalInPresentation=YES;for(UIButton *b in FRSheetButtons(self.actions))b.enabled=NO;FRComponentSetBusy(button,YES);self.errorLabel.hidden=YES;self.decisionHandler(decision);}
- (void)finishWithError:(NSError *)error {
    self.submitting=NO;self.modalInPresentation=NO;for(UIButton *b in FRSheetButtons(self.actions))FRComponentSetBusy(b,NO);
    if(error){self.errorLabel.text=error.localizedDescription;self.errorLabel.hidden=NO;UIAccessibilityPostNotification(UIAccessibilityAnnouncementNotification,error.localizedDescription);}
    else [self dismissViewControllerAnimated:YES completion:^{if(self.closeHandler)self.closeHandler();}];
}
- (void)presentationControllerDidDismiss:(UIPresentationController *)presentationController {if(self.closeHandler)self.closeHandler();}
@end

// Shared, layout-preserving loading surfaces. Animation never implies progress.
@interface FRSkeletonView ()
@property(nonatomic,copy) NSString *kind;
@property(nonatomic) CAShapeLayer *bars;
@end
@implementation FRSkeletonView
- (instancetype)initWithKind:(NSString *)kind {
    if((self=[super initWithFrame:CGRectZero])){
        _kind=kind;_bars=[CAShapeLayer layer];_bars.fillColor=UIColor.tertiarySystemFillColor.CGColor;[self.layer addSublayer:_bars];
        self.isAccessibilityElement=YES;self.accessibilityLabel=@"正在加载";self.userInteractionEnabled=NO;
        self.accessibilityIdentifier=[@"skeleton." stringByAppendingString:kind];
        for(NSString *name in @[UIAccessibilityReduceMotionStatusDidChangeNotification,UIApplicationDidBecomeActiveNotification,UIApplicationDidEnterBackgroundNotification])[[NSNotificationCenter defaultCenter] addObserver:self selector:@selector(updateAnimation) name:name object:nil];
    }return self;
}
- (CGSize)intrinsicContentSize {return CGSizeMake(UIViewNoIntrinsicMetric,[[@{@"row":@56,@"compactRow":@44,@"chat":@112,@"media":@210,@"reply":@48} objectForKey:self.kind] doubleValue]);}
- (void)layoutSubviews {
    [super layoutSubviews];CGFloat w=self.bounds.size.width,h=self.bounds.size.height;UIBezierPath *p=[UIBezierPath bezierPath];
    void(^bar)(CGFloat,CGFloat,CGFloat,CGFloat,CGFloat)=^(CGFloat x,CGFloat y,CGFloat width,CGFloat height,CGFloat r){[p appendPath:[UIBezierPath bezierPathWithRoundedRect:CGRectMake(x,y,MAX(0,width),height) cornerRadius:r]];};
    if([self.kind isEqual:@"compactRow"]){bar(0,14,16,16,4);bar(26,17,w*.55,10,5);}
    else if([self.kind isEqual:@"row"]){bar(0,19,16,16,4);bar(26,12,w*.61,11,5.5);bar(26,33,w*.79-26,9,4.5);}
    else if([self.kind isEqual:@"chat"]){bar(w*.34,4,w*.66,32,12);bar(0,54,w*.96,10,5);bar(0,74,w*.90,10,5);bar(0,94,w*.68,10,5);}
    else if([self.kind isEqual:@"media"]){bar(0,0,w,h,12);}
    else {bar(0,6,w*.96,9,4.5);bar(0,24,w*.82,9,4.5);}
    [CATransaction begin];[CATransaction setDisableActions:YES];self.bars.fillColor=[UIColor.tertiarySystemFillColor resolvedColorWithTraitCollection:self.traitCollection].CGColor;self.bars.frame=self.bounds;self.bars.path=p.CGPath;[CATransaction commit];
}
- (void)didMoveToWindow {[super didMoveToWindow];[self updateAnimation];}
- (void)updateAnimation {
    [self.bars removeAnimationForKey:@"loading"];
    if(!self.window||UIAccessibilityIsReduceMotionEnabled()||UIApplication.sharedApplication.applicationState==UIApplicationStateBackground)return;
    CABasicAnimation *a=[CABasicAnimation animationWithKeyPath:@"opacity"];a.fromValue=@.45;a.toValue=@1;a.duration=1.05;a.autoreverses=YES;a.repeatCount=HUGE_VALF;a.timingFunction=[CAMediaTimingFunction functionWithName:kCAMediaTimingFunctionEaseInEaseOut];[self.bars addAnimation:a forKey:@"loading"];
}
- (void)dealloc {[[NSNotificationCenter defaultCenter] removeObserver:self];}
@end


@interface FRDisclosureRow ()
@property(nonatomic) UIStackView *content;
@property(nonatomic) UILabel *detailLabel;
@property(nonatomic) UIButton *toggle;
@property(nonatomic) UIImageView *chevron;
@end
@implementation FRDisclosureRow
- (instancetype)initWithTitle:(NSString *)title detail:(NSString *)detail symbol:(NSString *)symbol action:(UIButton *)action {
    if((self=[super initWithFrame:CGRectZero])){
        self.accessibilityIdentifier=@"component.disclosure";_content=[UIStackView new];_content.axis=UILayoutConstraintAxisVertical;_content.spacing=4;_content.translatesAutoresizingMaskIntoConstraints=NO;[self addSubview:_content];
        [NSLayoutConstraint activateConstraints:@[[_content.leadingAnchor constraintEqualToAnchor:self.leadingAnchor],[_content.trailingAnchor constraintEqualToAnchor:self.trailingAnchor],[_content.topAnchor constraintEqualToAnchor:self.topAnchor],[_content.bottomAnchor constraintEqualToAnchor:self.bottomAnchor]]];
        _toggle=[UIButton buttonWithType:UIButtonTypeCustom];_toggle.accessibilityLabel=title;_toggle.accessibilityHint=@"展开或收起详情";_toggle.accessibilityIdentifier=@"disclosure.toggle";[_toggle addTarget:self action:@selector(toggleDetails) forControlEvents:UIControlEventTouchUpInside];[_toggle.heightAnchor constraintGreaterThanOrEqualToConstant:44].active=YES;
        UIImageView *icon=[[UIImageView alloc] initWithImage:[UIImage systemImageNamed:symbol withConfiguration:[UIImageSymbolConfiguration configurationWithPointSize:14]]];icon.tintColor=UIColor.secondaryLabelColor;icon.contentMode=UIViewContentModeScaleAspectFit;[icon.widthAnchor constraintEqualToConstant:16].active=YES;
        _summaryLabel=FRComponentLabel(title,13,UIFontWeightRegular);_summaryLabel.textColor=UIColor.secondaryLabelColor;
        _chevron=[[UIImageView alloc] initWithImage:[UIImage systemImageNamed:@"chevron.right" withConfiguration:[UIImageSymbolConfiguration configurationWithPointSize:10 weight:UIImageSymbolWeightMedium]]];_chevron.tintColor=UIColor.tertiaryLabelColor;_chevron.contentMode=UIViewContentModeScaleAspectFit;[_chevron.widthAnchor constraintEqualToConstant:12].active=YES;
        UIStackView *heading=[[UIStackView alloc] initWithArrangedSubviews:@[icon,_summaryLabel,_chevron]];heading.alignment=UIStackViewAlignmentCenter;heading.spacing=8;heading.translatesAutoresizingMaskIntoConstraints=NO;heading.userInteractionEnabled=NO;[_toggle addSubview:heading];
        [NSLayoutConstraint activateConstraints:@[[heading.leadingAnchor constraintEqualToAnchor:_toggle.leadingAnchor],[heading.trailingAnchor constraintEqualToAnchor:_toggle.trailingAnchor],[heading.topAnchor constraintEqualToAnchor:_toggle.topAnchor constant:8],[heading.bottomAnchor constraintEqualToAnchor:_toggle.bottomAnchor constant:-8]]];
        UIStackView *row=[[UIStackView alloc] initWithArrangedSubviews:action?@[_toggle,action]:@[_toggle]];row.spacing=8;row.alignment=UIStackViewAlignmentCenter;[_content addArrangedSubview:row];
        _detailLabel=FRComponentLabel(detail,13,UIFontWeightRegular);_detailLabel.textColor=UIColor.secondaryLabelColor;_detailLabel.hidden=YES;[_content addArrangedSubview:_detailLabel];
        if(!detail.length){_toggle.enabled=NO;_toggle.accessibilityHint=nil;_chevron.hidden=YES;}
    }return self;
}
- (void)setExpanded:(BOOL)expanded {_expanded=expanded;self.detailLabel.hidden=!expanded;self.chevron.transform=expanded?CGAffineTransformMakeRotation(M_PI_2):CGAffineTransformIdentity;self.toggle.accessibilityValue=expanded?@"已展开":@"已收起";}
- (void)toggleDetails {self.expanded=!self.expanded;if(self.expansionChanged)self.expansionChanged(self.expanded);}
@end

#pragma mark - Streaming reply and composer controls

static UIImage *FRSmallSymbol(NSString *name, CGFloat size){return [UIImage systemImageNamed:name withConfiguration:[UIImageSymbolConfiguration configurationWithPointSize:size weight:UIImageSymbolWeightMedium]];}

@interface FRThinkingView ()
@property(nonatomic) UILabel *headerLabel;
@property(nonatomic) UIImageView *chevron;
@property(nonatomic) UIButton *toggle;
@property(nonatomic,copy) NSString *fullText;
@property(nonatomic) BOOL active;
@end
@implementation FRThinkingView
- (instancetype)initWithText:(NSString *)text active:(BOOL)active {
    if((self=[super initWithFrame:CGRectZero])){
        self.accessibilityIdentifier=@"component.thinking";
        UIView *rule=[UIView new];rule.backgroundColor=UIColor.separatorColor;rule.translatesAutoresizingMaskIntoConstraints=NO;[self addSubview:rule];
        UIStackView *content=[UIStackView new];content.axis=UILayoutConstraintAxisVertical;content.spacing=2;content.translatesAutoresizingMaskIntoConstraints=NO;[self addSubview:content];
        _toggle=[UIButton buttonWithType:UIButtonTypeCustom];_toggle.accessibilityIdentifier=@"thinking.toggle";_toggle.accessibilityHint=@"展开或收起思考过程";[_toggle addTarget:self action:@selector(toggleExpanded) forControlEvents:UIControlEventTouchUpInside];
        UIImageView *icon=[[UIImageView alloc] initWithImage:FRSmallSymbol(@"lightbulb",12)];icon.tintColor=UIColor.secondaryLabelColor;icon.contentMode=UIViewContentModeScaleAspectFit;
        _headerLabel=FRComponentLabel(@"",12,UIFontWeightMedium);_headerLabel.textColor=UIColor.secondaryLabelColor;_headerLabel.numberOfLines=1;
        _chevron=[[UIImageView alloc] initWithImage:FRSmallSymbol(@"chevron.right",9)];_chevron.tintColor=UIColor.tertiaryLabelColor;_chevron.contentMode=UIViewContentModeScaleAspectFit;
        UIView *spacer=[UIView new];[spacer setContentHuggingPriority:UILayoutPriorityDefaultLow forAxis:UILayoutConstraintAxisHorizontal];
        UIStackView *header=[[UIStackView alloc] initWithArrangedSubviews:@[icon,_headerLabel,_chevron,spacer]];header.spacing=6;header.alignment=UIStackViewAlignmentCenter;header.userInteractionEnabled=NO;header.translatesAutoresizingMaskIntoConstraints=NO;[_toggle addSubview:header];
        [NSLayoutConstraint activateConstraints:@[[header.leadingAnchor constraintEqualToAnchor:_toggle.leadingAnchor],[header.trailingAnchor constraintEqualToAnchor:_toggle.trailingAnchor],[header.topAnchor constraintEqualToAnchor:_toggle.topAnchor constant:4],[header.bottomAnchor constraintEqualToAnchor:_toggle.bottomAnchor constant:-4],[_toggle.heightAnchor constraintGreaterThanOrEqualToConstant:32]]];
        _bodyLabel=FRComponentLabel(@"",12,UIFontWeightRegular);_bodyLabel.textColor=UIColor.tertiaryLabelColor;_bodyLabel.accessibilityIdentifier=@"thinking.body";
        [content addArrangedSubview:_toggle];[content addArrangedSubview:_bodyLabel];
        [NSLayoutConstraint activateConstraints:@[[rule.leadingAnchor constraintEqualToAnchor:self.leadingAnchor],[rule.widthAnchor constraintEqualToConstant:2],[rule.topAnchor constraintEqualToAnchor:self.topAnchor constant:6],[rule.bottomAnchor constraintEqualToAnchor:self.bottomAnchor constant:-2],
            [content.leadingAnchor constraintEqualToAnchor:rule.trailingAnchor constant:10],[content.trailingAnchor constraintEqualToAnchor:self.trailingAnchor],[content.topAnchor constraintEqualToAnchor:self.topAnchor],[content.bottomAnchor constraintEqualToAnchor:self.bottomAnchor]]];
        [self setText:text active:active];
    }return self;
}
- (void)setText:(NSString *)text active:(BOOL)active {
    self.fullText=text?:@"";self.active=active;
    self.headerLabel.text=active?@"正在思考…":@"思考过程";
    [self render];
}
- (void)render {
    NSString *text=[self.fullText stringByTrimmingCharactersInSet:NSCharacterSet.whitespaceAndNewlineCharacterSet];
    // Collapsed: the latest few lines, so streaming thought stays visible without taking over.
    if(!self.expanded&&text.length>160)text=[@"…" stringByAppendingString:[text substringFromIndex:text.length-160]];
    self.bodyLabel.text=text;self.bodyLabel.numberOfLines=self.expanded?0:3;
    self.chevron.transform=self.expanded?CGAffineTransformMakeRotation(M_PI_2):CGAffineTransformIdentity;
    self.toggle.accessibilityLabel=self.headerLabel.text;self.toggle.accessibilityValue=self.expanded?@"已展开":@"已收起";
}
- (void)setExpanded:(BOOL)expanded {_expanded=expanded;[self render];}
- (void)toggleExpanded {self.expanded=!self.expanded;if(self.expansionChanged)self.expansionChanged(self.expanded);}
@end

static UIView *FRToolStepLine(NSDictionary *tool){
    NSString *status=[tool[@"status"] isKindOfClass:NSString.class]?tool[@"status"]:@"";
    UIView *mark;
    if([status isEqual:@"running"]){UIActivityIndicatorView *spinner=[[UIActivityIndicatorView alloc] initWithActivityIndicatorStyle:UIActivityIndicatorViewStyleMedium];spinner.transform=CGAffineTransformMakeScale(.7,.7);[spinner startAnimating];mark=spinner;}
    else{NSString *symbol=[status isEqual:@"failed"]?@"exclamationmark.circle":([status isEqual:@"waiting"]?@"hand.raised":@"checkmark.circle");UIImageView *icon=[[UIImageView alloc] initWithImage:FRSmallSymbol(symbol,12)];icon.contentMode=UIViewContentModeScaleAspectFit;icon.tintColor=[status isEqual:@"failed"]?FRAmber():UIColor.tertiaryLabelColor;mark=icon;}
    [mark.widthAnchor constraintEqualToConstant:16].active=YES;[mark.heightAnchor constraintEqualToConstant:16].active=YES;
    NSString *label=[tool[@"label"] isKindOfClass:NSString.class]&&[tool[@"label"] length]?tool[@"label"]:([tool[@"name"] isKindOfClass:NSString.class]?tool[@"name"]:@"工具");
    NSString *note=[tool[@"note"] isKindOfClass:NSString.class]?tool[@"note"]:@"";
    UILabel *text=FRComponentLabel(note.length?[NSString stringWithFormat:@"%@ · %@",label,note]:label,12,UIFontWeightRegular);text.numberOfLines=1;text.lineBreakMode=NSLineBreakByTruncatingTail;
    text.textColor=[status isEqual:@"running"]?UIColor.secondaryLabelColor:UIColor.tertiaryLabelColor;
    UIStackView *line=[[UIStackView alloc] initWithArrangedSubviews:@[mark,text]];line.spacing=8;line.alignment=UIStackViewAlignmentCenter;
    line.isAccessibilityElement=YES;line.accessibilityLabel=[NSString stringWithFormat:@"%@，%@",text.text,@{@"running":@"进行中",@"failed":@"未成功",@"waiting":@"等待确认"}[status]?:@"完成"];line.accessibilityIdentifier=@"tool.step";
    return line;
}
UIStackView *FRToolStepsView(NSArray<NSDictionary *> *tools){
    UIStackView *view=[UIStackView new];view.axis=UILayoutConstraintAxisVertical;view.spacing=4;view.accessibilityIdentifier=@"component.toolSteps";FRUpdateToolSteps(view,tools);return view;
}
void FRUpdateToolSteps(UIStackView *view, NSArray<NSDictionary *> *tools){
    for(UIView *line in view.arrangedSubviews){[view removeArrangedSubview:line];[line removeFromSuperview];}
    // Long runs keep the latest steps; earlier ones are summarised in one line.
    NSUInteger start=tools.count>6?tools.count-6:0;
    if(start){UILabel *more=FRComponentLabel([NSString stringWithFormat:@"已完成 %lu 个步骤",(unsigned long)start],12,UIFontWeightRegular);more.textColor=UIColor.tertiaryLabelColor;[view addArrangedSubview:more];}
    for(NSUInteger i=start;i<tools.count;i++)if([tools[i] isKindOfClass:NSDictionary.class])[view addArrangedSubview:FRToolStepLine(tools[i])];
}

UIButton *FRAgentChip(NSString *name, NSString *symbol, void(^remove)(UIButton *)){
    UIButton *chip=[UIButton buttonWithType:UIButtonTypeSystem];UIButtonConfiguration *config=UIButtonConfiguration.filledButtonConfiguration;
    config.image=FRSmallSymbol(symbol.length?symbol:@"person.crop.circle",11);config.imagePadding=5;config.cornerStyle=UIButtonConfigurationCornerStyleCapsule;
    config.baseBackgroundColor=[FRAccent() colorWithAlphaComponent:.18];config.baseForegroundColor=FRAccent();config.contentInsets=NSDirectionalEdgeInsetsMake(5,10,5,8);
    config.attributedTitle=[[NSAttributedString alloc] initWithString:[NSString stringWithFormat:@"@%@  ✕",name] attributes:@{NSFontAttributeName:FRComponentFont(12,UIFontWeightMedium)}];
    chip.configuration=config;chip.accessibilityLabel=[NSString stringWithFormat:@"%@已加入，点按移除",name];chip.accessibilityIdentifier=@"composer.agentChip";
    [chip addAction:[UIAction actionWithHandler:^(UIAction *a){remove(chip);}] forControlEvents:UIControlEventTouchUpInside];return chip;
}

UIButton *FRPillButton(NSString *title, NSString *symbol, void(^action)(UIButton *)){
    UIButton *button=[UIButton buttonWithType:UIButtonTypeSystem];UIButtonConfiguration *config=UIButtonConfiguration.plainButtonConfiguration;
    config.cornerStyle=UIButtonConfigurationCornerStyleCapsule;config.imagePadding=4;config.contentInsets=NSDirectionalEdgeInsetsMake(6,10,6,10);
    config.attributedTitle=[[NSAttributedString alloc] initWithString:title attributes:@{NSFontAttributeName:FRComponentFont(12,UIFontWeightMedium)}];
    button.configuration=config;[button addAction:[UIAction actionWithHandler:^(UIAction *a){action(button);}] forControlEvents:UIControlEventTouchUpInside];
    [button.heightAnchor constraintGreaterThanOrEqualToConstant:36].active=YES;FRSetPillOn(button,symbol,NO);return button;
}
void FRSetPillOn(UIButton *button, NSString *symbol, BOOL on){
    UIButtonConfiguration *config=button.configuration;config.image=FRSmallSymbol(symbol,12);
    config.baseForegroundColor=on?FRAccent():UIColor.secondaryLabelColor;
    config.background.backgroundColor=on?[FRAccent() colorWithAlphaComponent:.16]:UIColor.clearColor;
    config.background.strokeColor=on?[FRAccent() colorWithAlphaComponent:.45]:UIColor.separatorColor;config.background.strokeWidth=.5;
    button.configuration=config;button.selected=NO;button.accessibilityValue=on?@"已开启":@"已关闭";
    button.accessibilityTraits=UIAccessibilityTraitButton|(on?UIAccessibilityTraitSelected:0);
}

@implementation FRSuggestionBar {UIImageView *_icon;}
- (instancetype)initWithFrame:(CGRect)frame {
    if((self=[super initWithFrame:frame])){
        self.backgroundColor=UIColor.secondarySystemBackgroundColor;self.layer.cornerRadius=16;FRSetBorder(self,[FRAccent() colorWithAlphaComponent:.35],.5);self.accessibilityIdentifier=@"composer.suggestion";
        _icon=[[UIImageView alloc] initWithImage:FRSmallSymbol(@"sparkles",14)];_icon.tintColor=FRAccent();_icon.contentMode=UIViewContentModeScaleAspectFit;[_icon.widthAnchor constraintEqualToConstant:20].active=YES;
        _titleLabel=FRComponentLabel(@"",13,UIFontWeightMedium);_detailLabel=FRComponentLabel(@"",11,UIFontWeightRegular);_detailLabel.textColor=UIColor.secondaryLabelColor;
        UIStackView *labels=[[UIStackView alloc] initWithArrangedSubviews:@[_titleLabel,_detailLabel]];labels.axis=UILayoutConstraintAxisVertical;labels.spacing=1;
        _acceptButton=[UIButton buttonWithType:UIButtonTypeSystem];UIButtonConfiguration *config=UIButtonConfiguration.filledButtonConfiguration;config.cornerStyle=UIButtonConfigurationCornerStyleCapsule;config.baseBackgroundColor=FRAccent();config.baseForegroundColor=UIColor.whiteColor;config.contentInsets=NSDirectionalEdgeInsetsMake(6,12,6,12);_acceptButton.configuration=config;_acceptButton.accessibilityIdentifier=@"suggestion.accept";
        [_acceptButton setContentCompressionResistancePriority:UILayoutPriorityRequired forAxis:UILayoutConstraintAxisHorizontal];[_acceptButton setContentHuggingPriority:UILayoutPriorityRequired forAxis:UILayoutConstraintAxisHorizontal];
        _dismissButton=[UIButton buttonWithType:UIButtonTypeSystem];UIButtonConfiguration *close=UIButtonConfiguration.plainButtonConfiguration;close.image=FRSmallSymbol(@"xmark",11);close.baseForegroundColor=UIColor.secondaryLabelColor;_dismissButton.configuration=close;_dismissButton.accessibilityLabel=@"不用";_dismissButton.accessibilityIdentifier=@"suggestion.dismiss";[_dismissButton.widthAnchor constraintEqualToConstant:36].active=YES;[_dismissButton.heightAnchor constraintEqualToConstant:36].active=YES;
        __weak typeof(self) weak=self;[_acceptButton addAction:[UIAction actionWithHandler:^(UIAction *a){if(weak.accept)weak.accept();}] forControlEvents:UIControlEventTouchUpInside];[_dismissButton addAction:[UIAction actionWithHandler:^(UIAction *a){if(weak.dismiss)weak.dismiss();}] forControlEvents:UIControlEventTouchUpInside];
        UIStackView *row=[[UIStackView alloc] initWithArrangedSubviews:@[_icon,labels,_acceptButton,_dismissButton]];row.spacing=8;row.alignment=UIStackViewAlignmentCenter;row.translatesAutoresizingMaskIntoConstraints=NO;[self addSubview:row];
        [NSLayoutConstraint activateConstraints:@[[row.leadingAnchor constraintEqualToAnchor:self.leadingAnchor constant:12],[row.trailingAnchor constraintEqualToAnchor:self.trailingAnchor constant:-4],[row.topAnchor constraintEqualToAnchor:self.topAnchor constant:6],[row.bottomAnchor constraintEqualToAnchor:self.bottomAnchor constant:-6]]];
    }return self;
}
- (void)showSymbol:(NSString *)symbol title:(NSString *)title detail:(NSString *)detail accept:(NSString *)acceptTitle {
    _icon.image=FRSmallSymbol(symbol.length?symbol:@"sparkles",14);self.titleLabel.text=title;self.detailLabel.text=detail;self.detailLabel.hidden=!detail.length;
    UIButtonConfiguration *config=self.acceptButton.configuration;config.attributedTitle=[[NSAttributedString alloc] initWithString:acceptTitle attributes:@{NSFontAttributeName:FRComponentFont(12,UIFontWeightSemibold)}];self.acceptButton.configuration=config;
    self.accessibilityLabel=title;
}
@end
