// Compiled only into Debug simulators; exercises real tab actions and containment.
static void FRNavigationCheck(BOOL passed,NSString *name) {
    fprintf(stderr,"FRIDAY_NAVIGATION %s %s\n",passed?"PASS":"FAIL",name.UTF8String);fflush(stderr);
    if(!passed)exit(1);
}
static void FRRunNavigationChecks(FRCompactTabsController *tabs) {
    [UIView setAnimationsEnabled:NO];
    FRNavigationCheck(tabs.viewControllers.count==3,@"three tabs: conversations, in progress, works");
    for(NSUInteger index=0;index<tabs.viewControllers.count;index++){
        tabs.selectedIndex=index;[tabs.view layoutIfNeeded];
        FRNavigationCheck(tabs.visibleController==tabs.viewControllers[index]&&tabs.visibleController.view.window!=nil,@"tab selection shows that tab's navigation");
        FRNavigationCheck(!tabs.tabBar.hidden&&tabs.visibleController.topViewController.view.safeAreaInsets.bottom>=tabs.tabBar.frame.size.height-1,@"list content clears the system tab bar");
    }
    tabs.selectedIndex=0;
    FROpenSession(@"demo-work");
    dispatch_after(dispatch_time(DISPATCH_TIME_NOW,300*NSEC_PER_MSEC),dispatch_get_main_queue(),^{
        FRChatController *chat=(id)tabs.visibleController.topViewController;[tabs.view layoutIfNeeded];
        FRNavigationCheck([chat isKindOfClass:FRChatController.class]&&chat.hidesBottomBarWhenPushed&&chat.view.safeAreaInsets.bottom<=tabs.view.safeAreaInsets.bottom+1,@"opening a conversation removes the tab and its inset");
        NSDictionary *draft=@{@"id":@"draft-check",@"session_id":@"demo-work",@"kind":@"chat",@"status":@"running",@"prompt":@"为什么天空是蓝色的？",@"draft":@{@"text":@"阳光中的蓝光更容易被空气散射。",@"phase":@"reviewing"}};
        chat.rawMessages=@[draft];chat.jobs=FRConversationRows(chat.rawMessages,chat.sid);
        chat.stamp=[NSData data];[chat.table reloadData];
        UITableViewCell *draftCell=[chat tableView:chat.table cellForRowAtIndexPath:[NSIndexPath indexPathForRow:0 inSection:0]];
        BOOL hasDraft=NO,hasLabel=NO;
        for(UIView *child in draftCell.contentView.subviews)if([child isKindOfClass:UIStackView.class])for(UIView *row in [(UIStackView *)child arrangedSubviews]){
            if([row isKindOfClass:FRMarkdownView.class])hasDraft=[[(FRMarkdownView *)row text] isEqual:draft[@"draft"][@"text"]];
            if([row isKindOfClass:UILabel.class]&&[[(UILabel *)row text] isEqual:@"草稿 · 正在核对"])hasLabel=YES;
        }
        FRNavigationCheck(hasDraft&&hasLabel,@"live text is explicitly labelled as an unreviewed draft");
        NSDictionary *ask=@{@"id":@"ask",@"session_id":@"s",@"kind":@"chat"},*cat=@{@"id":@"cat",@"session_id":@"s",@"kind":@"image",@"parent_job_id":@"ask",@"artifact_url":@"/a"},
            *resend=@{@"id":@"resend",@"session_id":@"s",@"kind":@"chat",@"sent_media":@[@"cat",@"missing"]};
        NSArray *media=FRConversationRows(@[ask,cat,resend],@"s");
        FRNavigationCheck([[media valueForKey:@"id"] isEqual:@[@"ask",@"cat",@"resend",@"cat"]],@"media a reply sent shows under that reply as well");
        [chat saveConversation];NSDictionary *saved=[FRAPI.shared.cache snapshot:[@"chat:" stringByAppendingString:chat.sid]];
        FRNavigationCheck(!saved[@"items"][0][@"draft"],@"unreviewed draft is excluded from the disk snapshot");
        [chat.draftTimer invalidate];
        NSMutableArray *history=[NSMutableArray array];
        for(NSUInteger i=0;i<12;i++){NSMutableDictionary *entry=[draft mutableCopy];entry[@"id"]=[NSString stringWithFormat:@"history-%lu",(unsigned long)i];entry[@"draft"]=@{@"text":@"已有内容。\n继续阅读这一条消息，更新不应打断阅读位置。",@"phase":@"generating"};[history addObject:entry];}
        chat.jobs=history;chat.approvals=@[];[chat.table reloadData];[chat.table layoutIfNeeded];
        NSIndexPath *reading=[NSIndexPath indexPathForRow:2 inSection:0];chat.table.contentOffset=CGPointMake(0,[chat.table rectForRowAtIndexPath:reading].origin.y+5);[chat.table layoutIfNeeded];
        CGFloat prior=[chat.table rectForRowAtIndexPath:reading].origin.y-chat.table.contentOffset.y;
        UITableViewCell *preserved=[chat.table cellForRowAtIndexPath:reading];
        NSMutableArray *newHistory=[history mutableCopy];NSMutableDictionary *changed=[history[11] mutableCopy];changed[@"draft"]=@{@"text":@"后台的新回复变长了。\n第二段。\n第三段。\n第四段。",@"phase":@"generating"};newHistory[11]=changed;
        [chat displayJobs:newHistory approvals:@[] stamp:[@"updated" dataUsingEncoding:NSUTF8StringEncoding]];
        FRNavigationCheck([chat.table cellForRowAtIndexPath:reading]==preserved&&fabs([chat.table rectForRowAtIndexPath:reading].origin.y-chat.table.contentOffset.y-prior)<1,@"background draft updates preserve visible cells and reading position");
        NSMutableArray *live=[newHistory mutableCopy];NSMutableDictionary *visible=[live[2] mutableCopy];visible[@"draft"]=@{@"text":@"正在阅读的草稿继续输出新的内容。",@"phase":@"reviewing"};live[2]=visible;
        [chat displayJobs:live approvals:@[] stamp:[@"live" dataUsingEncoding:NSUTF8StringEncoding]];
        FRNavigationCheck([chat.table cellForRowAtIndexPath:reading]==preserved&&[[(FRMarkdownView *)[preserved.contentView viewWithTag:6101] text] containsString:@"继续输出"],@"visible draft updates text in place without rebuilding its cell");
        [chat.input becomeFirstResponder];
        dispatch_after(dispatch_time(DISPATCH_TIME_NOW,600*NSEC_PER_MSEC),dispatch_get_main_queue(),^{
            [tabs.view layoutIfNeeded];UIView *composer=chat.input.superview.superview;CGRect frame=[composer convertRect:composer.bounds toView:chat.view];CGFloat keyboardTop=chat.view.keyboardLayoutGuide.layoutFrame.origin.y;
            FRNavigationCheck(CGRectGetMaxY(frame)<=keyboardTop&&fabs(keyboardTop-CGRectGetMaxY(frame)-2)<1,@"composer remains above the keyboard guide");
            [chat.input resignFirstResponder];
            UIViewController *details=[UIViewController new];[tabs.visibleController pushViewController:details animated:NO];[tabs.view layoutIfNeeded];
            FRNavigationCheck(details.view.safeAreaInsets.bottom<=tabs.view.safeAreaInsets.bottom+1,@"conversation subpages keep the tab hidden");
            [tabs.visibleController popToRootViewControllerAnimated:NO];[tabs.view layoutIfNeeded];
            FRNavigationCheck(!tabs.tabBar.hidden&&tabs.visibleController.topViewController.view.safeAreaInsets.bottom>=tabs.tabBar.frame.size.height-1,@"returning to the list restores the tab bar");
            tabs.selectedIndex=2;
            FRNavigationCheck(tabs.visibleController==tabs.viewControllers[2],@"tab selection still works after conversation navigation");
            fprintf(stderr,"FRIDAY_NAVIGATION COMPLETE\n");fflush(stderr);exit(0);
        });
    });
}
