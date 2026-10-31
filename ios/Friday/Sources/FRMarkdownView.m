#import "FRMarkdownView.h"
#import "FRMarkdown.h"
#import "FRChatComponents.h"

static UIFont *FRMDFont(CGFloat size,BOOL mono,BOOL bold,BOOL italic,UITraitCollection *traits) {
    UIFont *base=mono?[UIFont monospacedSystemFontOfSize:size weight:bold?UIFontWeightSemibold:UIFontWeightRegular]:[UIFont systemFontOfSize:size weight:bold?UIFontWeightSemibold:UIFontWeightRegular];
    if(italic){UIFontDescriptor *descriptor=[base.fontDescriptor fontDescriptorWithSymbolicTraits:base.fontDescriptor.symbolicTraits|UIFontDescriptorTraitItalic];if(descriptor)base=[UIFont fontWithDescriptor:descriptor size:size];}
    return [[UIFontMetrics metricsForTextStyle:UIFontTextStyleBody] scaledFontForFont:base compatibleWithTraitCollection:traits];
}
static NSAttributedString *FRMDAttributed(NSString *source,CGFloat size,BOOL bold,BOOL literal,UITraitCollection *traits) {
    NSMutableAttributedString *result=[NSMutableAttributedString new];NSMutableParagraphStyle *paragraph=[NSMutableParagraphStyle new];paragraph.lineSpacing=5;paragraph.lineBreakMode=NSLineBreakByWordWrapping;
    NSArray *runs=literal?@[@{@"text":source?:@""}]:FRMarkdownRuns(source);
    for(NSDictionary *run in runs){BOOL code=[run[@"code"] boolValue];NSMutableDictionary *attributes=[@{NSFontAttributeName:FRMDFont(code?size-1:size,code,bold||[run[@"bold"] boolValue],[run[@"italic"] boolValue],traits),NSForegroundColorAttributeName:UIColor.labelColor,NSParagraphStyleAttributeName:paragraph} mutableCopy];
        if(code)attributes[NSBackgroundColorAttributeName]=UIColor.tertiarySystemFillColor;if([run[@"strike"] boolValue])attributes[NSStrikethroughStyleAttributeName]=@(NSUnderlineStyleSingle);if(run[@"link"])attributes[NSLinkAttributeName]=[NSURL URLWithString:run[@"link"]];
        [result appendAttributedString:[[NSAttributedString alloc] initWithString:run[@"text"] attributes:attributes]];
    }return result;
}
static UITextView *FRMDText(NSAttributedString *text) {
    UITextView *view=[UITextView new];view.attributedText=text;view.editable=NO;view.selectable=YES;view.scrollEnabled=NO;view.backgroundColor=UIColor.clearColor;view.textContainerInset=UIEdgeInsetsZero;view.textContainer.lineFragmentPadding=0;view.dataDetectorTypes=UIDataDetectorTypeNone;view.linkTextAttributes=@{NSForegroundColorAttributeName:UIColor.linkColor};return view;
}
static UIScrollView *FRMDScroll(UIView *content) {
    UIScrollView *scroll=[UIScrollView new];scroll.alwaysBounceVertical=NO;scroll.alwaysBounceHorizontal=NO;scroll.directionalLockEnabled=YES;scroll.showsHorizontalScrollIndicator=YES;content.translatesAutoresizingMaskIntoConstraints=NO;[scroll addSubview:content];
    [NSLayoutConstraint activateConstraints:@[[content.topAnchor constraintEqualToAnchor:scroll.contentLayoutGuide.topAnchor],[content.bottomAnchor constraintEqualToAnchor:scroll.contentLayoutGuide.bottomAnchor],[content.leadingAnchor constraintEqualToAnchor:scroll.contentLayoutGuide.leadingAnchor],[content.trailingAnchor constraintEqualToAnchor:scroll.contentLayoutGuide.trailingAnchor],[scroll.heightAnchor constraintEqualToAnchor:content.heightAnchor constant:8]]];return scroll;
}
@interface FRMarkdownView ()
@property(nonatomic) UIStackView *blocks;
@property(nonatomic) NSArray *models;
@property(nonatomic,copy) NSString *renderedCategory;
@end
@implementation FRMarkdownView
- (instancetype)initWithText:(NSString *)text {
    if((self=[super initWithFrame:CGRectZero])){_blocks=[UIStackView new];_blocks.axis=UILayoutConstraintAxisVertical;_showsActions=YES;_blocks.spacing=12;_blocks.translatesAutoresizingMaskIntoConstraints=NO;[self addSubview:_blocks];[NSLayoutConstraint activateConstraints:@[[_blocks.leadingAnchor constraintEqualToAnchor:self.leadingAnchor],[_blocks.trailingAnchor constraintEqualToAnchor:self.trailingAnchor],[_blocks.topAnchor constraintEqualToAnchor:self.topAnchor],[_blocks.bottomAnchor constraintEqualToAnchor:self.bottomAnchor]]];self.accessibilityIdentifier=@"component.markdown";[self registerForTraitChanges:@[UITraitPreferredContentSizeCategory.class] withHandler:^(id<UITraitEnvironment> environment,UITraitCollection *previous){FRMarkdownView *view=(id)environment;view.models=nil;[view render];}];self.text=text;}return self;
}
- (void)setText:(NSString *)text {if([_text isEqual:text])return;_text=[text copy]?:@"";[self render];}
- (void)setMetadata:(NSString *)metadata {_metadata=[metadata copy];UILabel *label=(id)[self viewWithTag:6103];if([label isKindOfClass:UILabel.class])label.text=_metadata;else [self render];}
- (void)setMoreMenu:(UIMenu *)moreMenu {_moreMenu=moreMenu;UIButton *more=(id)[self viewWithTag:6107];if([more isKindOfClass:UIButton.class])more.menu=moreMenu;else if(self.showsActions)[self render];}
- (void)setShowsActions:(BOOL)showsActions {if(_showsActions==showsActions)return;_showsActions=showsActions;[self render];}
- (UIView *)codeBlock:(NSDictionary *)block {
    UIView *card=[UIView new];card.backgroundColor=UIColor.secondarySystemBackgroundColor;card.layer.cornerRadius=12;card.clipsToBounds=YES;card.accessibilityIdentifier=@"markdown.code";
    UIStackView *stack=[UIStackView new];stack.axis=UILayoutConstraintAxisVertical;stack.spacing=4;stack.translatesAutoresizingMaskIntoConstraints=NO;[card addSubview:stack];
    UILabel *language=[UILabel new];language.text=[block[@"language"] length]?block[@"language"]:@"代码";language.font=FRMDFont(12,NO,YES,NO,self.traitCollection);language.textColor=UIColor.secondaryLabelColor;language.lineBreakMode=NSLineBreakByTruncatingTail;
    NSString *code=block[@"text"];
    UIButton *copy=FRCopyButton(@"复制代码",^NSString *{return code;});copy.accessibilityIdentifier=@"markdown.copyCode";
    UIStackView *header=[[UIStackView alloc] initWithArrangedSubviews:@[language,copy]];header.alignment=UIStackViewAlignmentCenter;header.spacing=8;[copy setContentHuggingPriority:UILayoutPriorityRequired forAxis:UILayoutConstraintAxisHorizontal];[stack addArrangedSubview:header];
    UIFont *font=FRMDFont(13,YES,NO,NO,self.traitCollection);NSMutableParagraphStyle *paragraph=[NSMutableParagraphStyle new];paragraph.lineSpacing=4;paragraph.lineBreakMode=NSLineBreakByCharWrapping;
    UITextView *text=FRMDText([[NSAttributedString alloc] initWithString:code attributes:@{NSFontAttributeName:font,NSForegroundColorAttributeName:UIColor.labelColor,NSParagraphStyleAttributeName:paragraph}]);text.accessibilityIdentifier=@"markdown.codeText";
    CGFloat width=120;for(NSString *line in [code componentsSeparatedByString:@"\n"])width=MAX(width,[line sizeWithAttributes:@{NSFontAttributeName:font}].width+4);[text.widthAnchor constraintEqualToConstant:MIN(4000,ceil(width))].active=YES;
    UIScrollView *scroll=FRMDScroll(text);scroll.accessibilityIdentifier=@"markdown.codeScroll";scroll.accessibilityLabel=@"代码，可横向滚动";[stack addArrangedSubview:scroll];
    [NSLayoutConstraint activateConstraints:@[[stack.leadingAnchor constraintEqualToAnchor:card.leadingAnchor constant:12],[stack.trailingAnchor constraintEqualToAnchor:card.trailingAnchor constant:-12],[stack.topAnchor constraintEqualToAnchor:card.topAnchor constant:0],[stack.bottomAnchor constraintEqualToAnchor:card.bottomAnchor constant:-10]]];return card;
}
- (UIView *)tableBlock:(NSDictionary *)block {
    NSArray *rows=block[@"rows"],*align=block[@"alignment"];NSMutableArray *widths=[NSMutableArray array];
    for(NSUInteger column=0;column<align.count;column++){CGFloat width=80;for(NSUInteger r=0;r<rows.count;r++){NSAttributedString *text=FRMDAttributed(rows[r][column],14,r==0,NO,self.traitCollection);width=MAX(width,text.size.width+24);}[widths addObject:@(MIN(320,ceil(width)))];}
    UIStackView *grid=[UIStackView new];grid.axis=UILayoutConstraintAxisVertical;grid.spacing=.5;grid.backgroundColor=UIColor.separatorColor;grid.layer.cornerRadius=6;grid.clipsToBounds=YES;
    for(NSUInteger r=0;r<rows.count;r++){NSArray *row=rows[r];UIStackView *line=[UIStackView new];line.axis=UILayoutConstraintAxisHorizontal;line.alignment=UIStackViewAlignmentFill;line.spacing=.5;
        for(NSUInteger c=0;c<align.count;c++){UITextView *text=FRMDText(FRMDAttributed(row[c],14,r==0,NO,self.traitCollection));NSMutableAttributedString *cellText=[text.attributedText mutableCopy];[cellText removeAttribute:NSBackgroundColorAttributeName range:NSMakeRange(0,cellText.length)];text.attributedText=cellText;text.backgroundColor=r==0?UIColor.tertiarySystemBackgroundColor:(r%2?UIColor.secondarySystemBackgroundColor:FRShade(.045));text.textContainerInset=UIEdgeInsetsMake(9,10,9,10);text.textAlignment=[align[c] isEqual:@"center"]?NSTextAlignmentCenter:([align[c] isEqual:@"right"]?NSTextAlignmentRight:NSTextAlignmentLeft);text.accessibilityLabel=[NSString stringWithFormat:@"%@：%@",rows[0][c],text.text];if(r==0)text.accessibilityTraits|=UIAccessibilityTraitHeader;[text.widthAnchor constraintEqualToConstant:[widths[c] doubleValue]].active=YES;[line addArrangedSubview:text];}
        [grid addArrangedSubview:line];
    }
    UIScrollView *scroll=FRMDScroll(grid);scroll.accessibilityIdentifier=@"markdown.tableScroll";scroll.accessibilityLabel=@"表格，可横向滚动查看所有列";return scroll;
}
- (UIView *)viewForBlock:(NSDictionary *)block {
    NSString *type=block[@"type"],*source=block[@"text"]?:@"";
    if([type isEqual:@"code"])return [self codeBlock:block];if([type isEqual:@"table"])return [self tableBlock:block];
    if([type isEqual:@"rule"]){UIView *rule=[UIView new];rule.backgroundColor=UIColor.separatorColor;[rule.heightAnchor constraintEqualToConstant:1].active=YES;return rule;}
    BOOL heading=[type isEqual:@"heading"],quote=[type isEqual:@"quote"],list=[type isEqual:@"list"];
    CGFloat size=heading?MAX(16,24-[block[@"level"] integerValue]*2):16;
    UITextView *text=FRMDText(FRMDAttributed(source,size,heading,[type isEqual:@"plain"],self.traitCollection));text.accessibilityIdentifier=@"markdown.text";if(heading)text.accessibilityTraits|=UIAccessibilityTraitHeader;
    if(list||quote){UIView *container=[UIView new];text.translatesAutoresizingMaskIntoConstraints=NO;[container addSubview:text];CGFloat indent=list?MIN(6,[block[@"indent"] integerValue])*14:0;UIView *marker;
        if(list){UILabel *label=[UILabel new];label.text=block[@"marker"];label.font=FRMDFont(16,NO,NO,NO,self.traitCollection);label.textColor=UIColor.secondaryLabelColor;marker=label;}else{marker=[UIView new];marker.backgroundColor=UIColor.separatorColor;}
        marker.translatesAutoresizingMaskIntoConstraints=NO;[container addSubview:marker];
        [NSLayoutConstraint activateConstraints:@[[marker.leadingAnchor constraintEqualToAnchor:container.leadingAnchor constant:indent],[marker.topAnchor constraintEqualToAnchor:container.topAnchor constant:list?0:3],[text.leadingAnchor constraintEqualToAnchor:marker.trailingAnchor constant:10],[text.trailingAnchor constraintEqualToAnchor:container.trailingAnchor],[text.topAnchor constraintEqualToAnchor:container.topAnchor],[text.bottomAnchor constraintEqualToAnchor:container.bottomAnchor]]];
        if(quote)[NSLayoutConstraint activateConstraints:@[[marker.widthAnchor constraintEqualToConstant:3],[marker.bottomAnchor constraintEqualToAnchor:container.bottomAnchor constant:-3]]];else [marker setContentHuggingPriority:UILayoutPriorityRequired forAxis:UILayoutConstraintAxisHorizontal];return container;
    }return text;
}
- (void)render {
    if(!self.blocks)return;self.renderedCategory=self.traitCollection.preferredContentSizeCategory;
    NSArray *models=FRMarkdownBlocks(self.text);NSArray *oldViews=[self.blocks.arrangedSubviews copy];
    for(NSUInteger i=0;i<models.count;i++){
        if(i<self.models.count&&[models[i] isEqual:self.models[i]])continue;
        UIView *old=i<oldViews.count?oldViews[i]:nil;
        // Preserve the existing selectable text view while a paragraph streams.
        if([old isKindOfClass:UITextView.class]&&[models[i][@"type"] isEqual:@"paragraph"]&&[self.models[i][@"type"] isEqual:@"paragraph"]){[(UITextView *)old setAttributedText:FRMDAttributed(models[i][@"text"],16,NO,NO,self.traitCollection)];continue;}
        if(old){[self.blocks removeArrangedSubview:old];[old removeFromSuperview];}
        [self.blocks insertArrangedSubview:[self viewForBlock:models[i]] atIndex:i];
    }
    for(NSUInteger i=models.count;i<oldViews.count;i++){[self.blocks removeArrangedSubview:oldViews[i]];[oldViews[i] removeFromSuperview];}
    // Keep a copy-all action even across code/table boundaries; preserve the original Markdown.
    for(NSUInteger i=models.count;i<self.blocks.arrangedSubviews.count;){UIView *v=self.blocks.arrangedSubviews[i];[self.blocks removeArrangedSubview:v];[v removeFromSuperview];}
    if(self.showsActions){
        __weak typeof(self) weak=self;UIButton *copy=FRCopyButton(@"复制回复",^NSString *{return weak.text;});copy.accessibilityIdentifier=@"markdown.copyReply";
        UILabel *metadata=[UILabel new];metadata.tag=6103;metadata.text=self.metadata;metadata.font=FRMDFont(12,NO,NO,NO,self.traitCollection);metadata.textColor=UIColor.secondaryLabelColor;metadata.numberOfLines=0;metadata.accessibilityIdentifier=@"task.elapsed";
        NSMutableArray *items=[NSMutableArray arrayWithObject:copy];
        if(self.moreMenu){UIButton *more=FRQuietButton(nil,@"ellipsis",@"更多操作",nil);more.menu=self.moreMenu;more.showsMenuAsPrimaryAction=YES;more.accessibilityIdentifier=@"markdown.more";more.tag=6107;[items addObject:more];}
        [items addObjectsFromArray:@[metadata,[UIView new]]];
        UIStackView *footer=[[UIStackView alloc] initWithArrangedSubviews:items];footer.axis=UILayoutConstraintAxisHorizontal;footer.alignment=UIStackViewAlignmentCenter;footer.spacing=8;[metadata setContentHuggingPriority:UILayoutPriorityDefaultHigh forAxis:UILayoutConstraintAxisHorizontal];[self.blocks addArrangedSubview:footer];
    }
    for(NSUInteger i=0;i<models.count;i++){CGFloat gap=12;if(i+1<models.count&&[models[i][@"type"] isEqual:@"list"]&&[models[i+1][@"type"] isEqual:@"list"])gap=5;[self.blocks setCustomSpacing:gap afterView:self.blocks.arrangedSubviews[i]];}
    self.models=models;[self invalidateIntrinsicContentSize];
}
@end
