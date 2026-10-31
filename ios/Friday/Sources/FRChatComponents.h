#import <UIKit/UIKit.h>

// Presentation-only components. Controllers own authorization, downloads and playback.

// Appearance: a gray designed for dark mode, mirrored for light mode.
FOUNDATION_EXPORT UIColor *FRShade(CGFloat dark);
// UI 2.0 palette (light, dark): ink on paper, one blue for work in progress, amber for "waits on you".
FOUNDATION_EXPORT UIColor *FRInk(void);        // primary text, primary buttons
FOUNDATION_EXPORT UIColor *FRInkSecondary(void);
FOUNDATION_EXPORT UIColor *FRInkTertiary(void);
FOUNDATION_EXPORT UIColor *FRPaper(void);      // screen background
FOUNDATION_EXPORT UIColor *FRSurface(void);    // user bubbles, quiet buttons, chips
FOUNDATION_EXPORT UIColor *FRHairline(void);
FOUNDATION_EXPORT UIColor *FRAccent(void);     // running work
FOUNDATION_EXPORT UIColor *FRAccentSoft(void);
FOUNDATION_EXPORT UIColor *FRAmber(void);      // needs the user
FOUNDATION_EXPORT UIColor *FRAmberSoft(void);
FOUNDATION_EXPORT UIColor *FROnline(void);
FOUNDATION_EXPORT UIColor *FRInverse(void);    // text on FRInk
// Scaled with Dynamic Type from the given base size; mono for times and percentages.
FOUNDATION_EXPORT UIFont *FRFont(CGFloat size, UIFontWeight weight);
FOUNDATION_EXPORT UIFont *FRMono(CGFloat size);
// "约 4 分", "约 1 小时 20 分": an estimate, never a promise.
FOUNDATION_EXPORT NSString *FREstimate(NSTimeInterval seconds);
// Layer borders keep following light/dark changes (CGColor does not).
FOUNDATION_EXPORT void FRSetBorder(UIView *view, UIColor *color, CGFloat width);
// A short message above the bottom edge, optionally with an undo action. Replaces the previous one.
FOUNDATION_EXPORT void FRToast(UIViewController *controller, NSString *text, NSString *actionTitle, void(^action)(void));
typedef NS_ENUM(NSInteger, FRActionStyle) { FRActionStylePrimary, FRActionStyleSecondary, FRActionStyleDestructive };
FOUNDATION_EXPORT UIButton *FRActionButton(NSString *title, NSString *symbol, FRActionStyle style, void(^action)(UIButton *));
FOUNDATION_EXPORT UIButton *FRQuietButton(NSString *title, NSString *symbol, NSString *accessibilityLabel, void(^action)(UIButton *));
FOUNDATION_EXPORT UIButton *FRCopyButton(NSString *label, NSString *(^textProvider)(void));
FOUNDATION_EXPORT UIButton *FRLatestButton(void(^action)(UIButton *));
FOUNDATION_EXPORT void FRComponentSetBusy(UIButton *button, BOOL busy);

@interface FRChatCard : UIView
@property(nonatomic,readonly) UIStackView *content;
@property(nonatomic,readonly) UIStackView *heading;
@property(nonatomic,readonly) UILabel *titleLabel;
- (instancetype)initWithTitle:(NSString *)title symbol:(NSString *)symbol;
- (UILabel *)addDetail:(NSString *)text;
@end

@interface FRPermissionCard : FRChatCard
- (instancetype)initWithTitle:(NSString *)title detail:(NSString *)detail buttonTitle:(NSString *)buttonTitle review:(void(^)(UIButton *))review;
@end

@interface FROperationCard : FRChatCard
- (instancetype)initWithTitle:(NSString *)title detail:(NSString *)detail;
@end

@interface FRStatusCard : FRChatCard
- (instancetype)initWithTitle:(NSString *)title detail:(NSString *)detail error:(BOOL)error;
@end

@interface FRMediaCard : FRChatCard
@property(nonatomic,readonly) UIButton *openButton;
- (instancetype)initWithFilename:(NSString *)filename kind:(NSString *)kind symbol:(NSString *)symbol ready:(BOOL)ready error:(NSString *)error action:(void(^)(UIButton *))action;
- (void)installPreview:(UIView *)preview height:(CGFloat)height;
@end
@interface FRImageCard : FRMediaCard
- (void)setPreviewImage:(UIImage *)image action:(void(^)(UIButton *))action;
@end
@interface FRVideoCard : FRMediaCard @end
@interface FRAudioCard : FRMediaCard @end
@interface FRFileCard : FRMediaCard @end

// Scrollable details, fixed action area, single in-flight decision, inline retry errors.
@interface FRPermissionController : UIViewController <UIAdaptivePresentationControllerDelegate>
@property(nonatomic,copy) void(^decisionHandler)(NSString *decision);
@property(nonatomic,copy) void(^closeHandler)(void);
@property(nonatomic,readonly) BOOL submitting;
- (instancetype)initWithTitle:(NSString *)title detail:(NSString *)detail context:(NSString *)context allowTitle:(NSString *)allowTitle canDeny:(BOOL)canDeny;
- (void)finishWithError:(NSError *)error;
@end

@interface FRSkeletonView : UIView
- (instancetype)initWithKind:(NSString *)kind;
@end

// Quiet, expandable task information. Actions remain explicit buttons.
@interface FRDisclosureRow : UIView
@property(nonatomic,readonly) UILabel *summaryLabel;
@property(nonatomic) BOOL expanded;
@property(nonatomic,copy) void(^expansionChanged)(BOOL expanded);
- (instancetype)initWithTitle:(NSString *)title detail:(NSString *)detail symbol:(NSString *)symbol action:(UIButton *)action;
@end

// Streaming reply pieces and the conversation composer's agent controls.
// Collapsed thinking shows its latest lines; tapping expands the whole text.
@interface FRThinkingView : UIView
@property(nonatomic,readonly) UILabel *bodyLabel;
@property(nonatomic) BOOL expanded;
@property(nonatomic,copy) void(^expansionChanged)(BOOL expanded);
- (instancetype)initWithText:(NSString *)text active:(BOOL)active;
- (void)setText:(NSString *)text active:(BOOL)active;
@end
// One compact line per tool step: running, done, failed or waiting for approval.
FOUNDATION_EXPORT UIStackView *FRToolStepsView(NSArray<NSDictionary *> *tools);
FOUNDATION_EXPORT void FRUpdateToolSteps(UIStackView *view, NSArray<NSDictionary *> *tools);
// "@短剧助手 ×": tapping removes the invited agent from this conversation.
FOUNDATION_EXPORT UIButton *FRAgentChip(NSString *name, NSString *symbol, void(^remove)(UIButton *));
// Small capsule toggle used in the composer (thinking).
FOUNDATION_EXPORT UIButton *FRPillButton(NSString *title, NSString *symbol, void(^action)(UIButton *));
FOUNDATION_EXPORT void FRSetPillOn(UIButton *button, NSString *symbol, BOOL on);

// A prompt above the composer: "让短剧助手加入？" with an explicit accept and dismiss.
@interface FRSuggestionBar : UIView
@property(nonatomic,readonly) UIButton *acceptButton,*dismissButton;
@property(nonatomic,readonly) UILabel *titleLabel,*detailLabel;
- (void)showSymbol:(NSString *)symbol title:(NSString *)title detail:(NSString *)detail accept:(NSString *)acceptTitle;
@property(nonatomic,copy) void(^accept)(void);
@property(nonatomic,copy) void(^dismiss)(void);
@end
