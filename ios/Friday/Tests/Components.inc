// Debug simulator only. Exercises production components at narrow widths and large type.
static void FRComponentCheck(BOOL passed,NSString *name) {
    fprintf(stderr,"FRIDAY_COMPONENT %s %s\n",passed?"PASS":"FAIL",name.UTF8String);fflush(stderr);if(!passed)exit(1);
}
static UIButton *FRFindButton(UIView *view,NSString *identifier) {
    if([view isKindOfClass:UIButton.class]&&[view.accessibilityIdentifier isEqual:identifier])return (id)view;
    for(UIView *child in view.subviews){UIButton *found=FRFindButton(child,identifier);if(found)return found;}return nil;
}
static UIViewController *FRComponentGallery(void) {
    UIViewController *vc=[UIViewController new];vc.title=@"组件库";vc.view.backgroundColor=UIColor.blackColor;
    UIScrollView *scroll=[UIScrollView new];scroll.translatesAutoresizingMaskIntoConstraints=NO;[vc.view addSubview:scroll];
    UIStackView *stack=[UIStackView new];stack.axis=UILayoutConstraintAxisVertical;stack.spacing=16;stack.translatesAutoresizingMaskIntoConstraints=NO;[scroll addSubview:stack];
    [NSLayoutConstraint activateConstraints:@[[scroll.topAnchor constraintEqualToAnchor:vc.view.safeAreaLayoutGuide.topAnchor],[scroll.bottomAnchor constraintEqualToAnchor:vc.view.safeAreaLayoutGuide.bottomAnchor],[scroll.leadingAnchor constraintEqualToAnchor:vc.view.leadingAnchor],[scroll.trailingAnchor constraintEqualToAnchor:vc.view.trailingAnchor],[stack.leadingAnchor constraintEqualToAnchor:scroll.contentLayoutGuide.leadingAnchor constant:16],[stack.trailingAnchor constraintEqualToAnchor:scroll.contentLayoutGuide.trailingAnchor constant:-16],[stack.topAnchor constraintEqualToAnchor:scroll.contentLayoutGuide.topAnchor constant:16],[stack.bottomAnchor constraintEqualToAnchor:scroll.contentLayoutGuide.bottomAnchor constant:-16],[stack.widthAnchor constraintEqualToAnchor:scroll.frameLayoutGuide.widthAnchor constant:-32]]];
    __weak UIViewController *weak=vc;
    [stack addArrangedSubview:[[FRPermissionCard alloc] initWithTitle:@"操作需要授权" detail:@"更新工作空间中的行动清单，需要你确认本次写入。" buttonTitle:@"查看并决定" review:^(UIButton *b){FRPermissionController *sheet=[[FRPermissionController alloc] initWithTitle:@"操作需要你的授权" detail:@"将本周的想法整理为行动清单，并写入你选择的文件夹。\n\n目标文件：工作空间 / 行动清单.md\n操作：创建文档" context:@"仅批准此次操作，不授予长期权限。" allowTitle:@"允许此次操作" canDeny:YES];__weak FRPermissionController *dialog=sheet;sheet.decisionHandler=^(NSString *d){[dialog finishWithError:nil];};[weak presentViewController:sheet animated:YES completion:nil];}]];
    FROperationCard *operation=[[FROperationCard alloc] initWithTitle:@"正在整理资料" detail:@"已完成的内容会保留。"];
    [operation.content addArrangedSubview:FRActionButton(@"停止任务",@"stop.circle",FRActionStyleDestructive,^(UIButton *b){})];[stack addArrangedSubview:operation];
    FRImageCard *image=[[FRImageCard alloc] initWithFilename:@"周末的海边.png" kind:@"图片" symbol:@"photo" ready:YES error:nil action:^(UIButton *b){}];
    UIGraphicsImageRenderer *renderer=[[UIGraphicsImageRenderer alloc] initWithSize:CGSizeMake(600,360)];
    UIImage *preview=[renderer imageWithActions:^(UIGraphicsImageRendererContext *context){[[UIColor colorWithRed:.15 green:.29 blue:.34 alpha:1] setFill];UIRectFill(CGRectMake(0,0,600,360));[[UIColor colorWithRed:.89 green:.73 blue:.48 alpha:1] setFill];[[UIBezierPath bezierPathWithOvalInRect:CGRectMake(410,45,80,80)] fill];[[UIColor colorWithRed:.08 green:.19 blue:.25 alpha:1] setFill];UIRectFill(CGRectMake(0,205,600,155));}];
    [image setPreviewImage:preview action:^(UIButton *b){}];[stack addArrangedSubview:image];
    for(NSArray *kind in @[@[FRVideoCard.class,@"视频",@"play.rectangle",@"片段.mp4"],@[FRAudioCard.class,@"音频",@"waveform",@"语音留言.m4a"],@[FRFileCard.class,@"文件",@"doc",@"本周行动清单.pdf"]]){
        FRMediaCard *card=[[kind[0] alloc] initWithFilename:kind[3] kind:kind[1] symbol:kind[2] ready:YES error:nil action:^(UIButton *b){}];
        if(kind[0]!=FRFileCard.class){UIImageView *preview=[[UIImageView alloc] initWithImage:[UIImage systemImageNamed:kind[2]]];preview.tintColor=UIColor.secondaryLabelColor;preview.contentMode=UIViewContentModeCenter;[card installPreview:preview height:kind[0]==FRAudioCard.class?80:180];}[stack addArrangedSubview:card];
    }
    [stack addArrangedSubview:[[FRImageCard alloc] initWithFilename:@"预览加载中.png" kind:@"图片" symbol:@"photo" ready:NO error:nil action:^(UIButton *b){}]];
    [stack addArrangedSubview:[[FRVideoCard alloc] initWithFilename:@"片段.mp4" kind:@"视频" symbol:@"play.rectangle" ready:NO error:@"连接暂时中断，点按重新加载。" action:^(UIButton *b){}]];
    [stack addArrangedSubview:[[FRStatusCard alloc] initWithTitle:@"视频项目" detail:@"已完成 3 / 5 个镜头" error:NO]];
    [stack addArrangedSubview:[[FRStatusCard alloc] initWithTitle:@"操作未完成" detail:@"与 Mac 的连接暂时中断，请重试。" error:YES]];return vc;
}
static void FRRunComponentChecks(FRCompactTabsController *tabs) {
    [UIView setAnimationsEnabled:NO];FROpenSession(@"demo-work");
    dispatch_after(dispatch_time(DISPATCH_TIME_NOW,400*NSEC_PER_MSEC),dispatch_get_main_queue(),^{
        FRChatController *chat=(id)tabs.visibleController.topViewController;[chat.draftTimer invalidate];[tabs.view layoutIfNeeded];
        UIFont *font=chat.managementButton.titleLabel.font;CGSize size=chat.managementButton.intrinsicContentSize;
        for(int i=0;i<3;i++){FRSetButtonBusy(chat.managementButton,YES);[chat.view layoutIfNeeded];FRComponentCheck([chat.managementButton.titleLabel.font isEqual:font]&&CGSizeEqualToSize(size,chat.managementButton.intrinsicContentSize),@"first and repeated loading preserve navigation typography and size");FRSetButtonBusy(chat.managementButton,NO);}
        FRComponentCheck(chat.navigationItem.titleView.bounds.size.width<=chat.view.bounds.size.width-180,@"navigation title is bounded by available width");
        [chat management];
        dispatch_after(dispatch_time(DISPATCH_TIME_NOW,200*NSEC_PER_MSEC),dispatch_get_main_queue(),^{
            FRPermissionController *sheet=(id)chat.presentedViewController;FRComponentCheck([sheet isKindOfClass:FRPermissionController.class],@"Mac management opens a dedicated permission sheet");
            UIButton *allow=FRFindButton(sheet.view,@"permission.allow");[allow sendActionsForControlEvents:UIControlEventTouchUpInside];
            FRComponentCheck(sheet.submitting&&!allow.enabled,@"submission disables controls immediately");
            dispatch_after(dispatch_time(DISPATCH_TIME_NOW,300*NSEC_PER_MSEC),dispatch_get_main_queue(),^{
                FRComponentCheck([[FRStore.shared session:chat.sid][@"management"] boolValue]&&chat.presentedViewController==nil,@"first enable updates the session and dismisses the sheet");
                FRComponentCheck([chat.managementButton.titleLabel.font isEqual:font],@"successful enable keeps the original navigation font");
                NSDictionary *approval=@{@"token":@"component-test",@"description":@"将行动清单写入工作空间。",@"job_id":@"demo-job",@"expires":@(NSDate.date.timeIntervalSince1970+600)};
                chat.approvals=@[approval];[chat presentNextApproval];
                dispatch_after(dispatch_time(DISPATCH_TIME_NOW,200*NSEC_PER_MSEC),dispatch_get_main_queue(),^{
                    FRPermissionController *review=(id)chat.presentedViewController;FRComponentCheck([review isKindOfClass:FRPermissionController.class],@"new approval automatically presents its decision sheet");
                    [FRFindButton(review.view,@"permission.later") sendActionsForControlEvents:UIControlEventTouchUpInside];
                    dispatch_after(dispatch_time(DISPATCH_TIME_NOW,200*NSEC_PER_MSEC),dispatch_get_main_queue(),^{
                        [chat presentNextApproval];FRComponentCheck(chat.presentedViewController==nil,@"later does not repeatedly interrupt the conversation");
                        [chat reviewApproval:approval];
                        dispatch_after(dispatch_time(DISPATCH_TIME_NOW,200*NSEC_PER_MSEC),dispatch_get_main_queue(),^{
                            FRPermissionController *retry=(id)chat.presentedViewController;__block NSUInteger decisions=0;retry.decisionHandler=^(NSString *d){decisions++;};UIButton *button=FRFindButton(retry.view,@"permission.allow");
                            [button sendActionsForControlEvents:UIControlEventTouchUpInside];[button sendActionsForControlEvents:UIControlEventTouchUpInside];FRComponentCheck(decisions==1,@"duplicate decisions are blocked while in flight");
                            [retry finishWithError:[NSError errorWithDomain:@"test" code:1 userInfo:@{NSLocalizedDescriptionKey:@"网络中断，请重试。"}]];FRComponentCheck(button.enabled&&!retry.submitting&&chat.presentedViewController==retry,@"failure preserves the sheet and enables retry");
                            [button sendActionsForControlEvents:UIControlEventTouchUpInside];FRComponentCheck(decisions==2,@"retry submits one new decision");
                            [retry dismissViewControllerAnimated:NO completion:^{
                                UIViewController *gallery=FRComponentGallery();[tabs.visibleController pushViewController:gallery animated:NO];
                                for(NSString *category in @[UIContentSizeCategoryLarge,UIContentSizeCategoryAccessibilityExtraExtraExtraLarge]){
                                    gallery.traitOverrides.preferredContentSizeCategory=category;
                                    for(NSNumber *width in @[@320,@390,@402]){gallery.view.frame=CGRectMake(0,0,width.doubleValue,844);[gallery.view layoutIfNeeded];UIScrollView *scroll=(id)gallery.view.subviews.firstObject;UIStackView *cards=(id)scroll.subviews.firstObject;
                                        for(FRChatCard *card in cards.arrangedSubviews){[card layoutIfNeeded];FRComponentCheck(card.bounds.size.width<=width.doubleValue-32&&card.bounds.size.height>44,@"cards fit narrow and accessible layouts");for(UIView *part in card.content.arrangedSubviews)FRComponentCheck(CGRectGetMaxX(part.frame)<=card.bounds.size.width-13&&CGRectGetMaxY(part.frame)<=card.bounds.size.height-13,@"card content remains inside its padding");}
                                    }
                                }
                                fprintf(stderr,"FRIDAY_COMPONENT COMPLETE\n");fflush(stderr);exit(0);
                            }];
                        });
                    });
                });
            });
        });
    });
}
