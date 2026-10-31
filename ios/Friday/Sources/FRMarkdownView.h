#import <UIKit/UIKit.h>
@interface FRMarkdownView : UIView
@property(nonatomic,copy) NSString *text;
@property(nonatomic,copy) NSString *metadata;
@property(nonatomic) BOOL showsActions;
// Shown as a "…" button next to copy when set (delete, edit and resend).
@property(nonatomic) UIMenu *moreMenu;
- (instancetype)initWithText:(NSString *)text;
@end
