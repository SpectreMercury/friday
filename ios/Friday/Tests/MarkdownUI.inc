static NSArray<UIView *> *FRMDFind(UIView *view,NSString *identifier) {
    NSMutableArray *found=[NSMutableArray array];if([view.accessibilityIdentifier isEqual:identifier])[found addObject:view];for(UIView *child in view.subviews)[found addObjectsFromArray:FRMDFind(child,identifier)];return found;
}
static void FRRunMarkdownChecks(FRCompactTabsController *tabs) {
    [UIView setAnimationsEnabled:NO];FROpenSession(@"demo-work");
    dispatch_after(dispatch_time(DISPATCH_TIME_NOW,400*NSEC_PER_MSEC),dispatch_get_main_queue(),^{
        FRChatController *chat=(id)tabs.visibleController.topViewController;[chat.draftTimer invalidate];
        NSString *code=@"defaults read /Library/Preferences/com.apple.SoftwareUpdate AutomaticallyInstallMacOSUpdates\n  echo '**literal**'\n";
        NSString *source=[NSString stringWithFormat:@"# 标题\n\n**中文粗体**与 `行内代码`\n\n| 设置项 | 当前值 | 含义 |\n|---|:---:|---|\n| `AutomaticallyInstallMacOSUpdates` | `true` | 自动安装更新 |\n\n```bash\n%@```\n\n1. **第一项**\n2. 第二项\n\n> 引用内容\n\n---\n\n[链接](https://example.com)",code];
        NSDictionary *job=@{@"id":@"md-test",@"session_id":chat.sid,@"kind":@"chat",@"status":@"running",@"draft":@{@"text":source,@"phase":@"generating"}};
        chat.jobs=@[job];chat.approvals=@[];chat.stamp=[NSData data];[chat.table reloadData];[chat.table layoutIfNeeded];
        UITableViewCell *cell=[chat.table cellForRowAtIndexPath:[NSIndexPath indexPathForRow:0 inSection:0]];FRMarkdownView *view=(id)[cell.contentView viewWithTag:6101];FRComponentCheck([view isKindOfClass:FRMarkdownView.class],@"chat renders Markdown with the shared native component");
        UIViewController *host=[UIViewController new];[tabs.visibleController pushViewController:host animated:NO];FRMarkdownView *message=[[FRMarkdownView alloc] initWithText:source];message.translatesAutoresizingMaskIntoConstraints=NO;[host.view addSubview:message];NSLayoutConstraint *width=[message.widthAnchor constraintEqualToConstant:288];[NSLayoutConstraint activateConstraints:@[width,[message.leadingAnchor constraintEqualToAnchor:host.view.leadingAnchor],[message.topAnchor constraintEqualToAnchor:host.view.topAnchor]]];
        for(NSString *category in @[UIContentSizeCategoryLarge,UIContentSizeCategoryAccessibilityExtraExtraExtraLarge]){
            message.traitOverrides.preferredContentSizeCategory=category;
            for(NSNumber *size in @[@288,@358,@370]){width.constant=size.doubleValue;[host.view layoutIfNeeded];
                FRComponentCheck(fabs(message.bounds.size.width-size.doubleValue)<1&&message.bounds.size.height>200,@"Markdown has a bounded width and self-sized height");
                for(NSString *identifier in @[@"markdown.codeScroll",@"markdown.tableScroll"]){UIScrollView *scroll=(id)FRMDFind(message,identifier).firstObject;[scroll layoutIfNeeded];FRComponentCheck(scroll.bounds.size.width<=size.doubleValue&&scroll.bounds.size.height>20&&scroll.contentSize.width>scroll.bounds.size.width,@"long code and tables scroll horizontally inside the message");}
            }
        }
        UITextView *codeView=(id)FRMDFind(message,@"markdown.codeText").firstObject;FRComponentCheck([codeView.text isEqual:code]&&codeView.selectable&&!codeView.editable,@"code stays literal and selectable");
        UIPasteboard *pasteboard=UIPasteboard.generalPasteboard;NSArray *saved=pasteboard.items;
        [(UIButton *)FRMDFind(message,@"markdown.copyCode").firstObject sendActionsForControlEvents:UIControlEventTouchUpInside];FRComponentCheck([pasteboard.string isEqual:code],@"copy code preserves exact whitespace and newlines");
        [(UIButton *)FRMDFind(message,@"markdown.copyReply").firstObject sendActionsForControlEvents:UIControlEventTouchUpInside];FRComponentCheck([pasteboard.string isEqual:source],@"copy reply preserves original Markdown across blocks");pasteboard.items=saved;
        for(UITextView *text in FRMDFind(message,@"markdown.text"))if([text.text containsString:@"中文粗体"]){UIFont *font=[text.attributedText attribute:NSFontAttributeName atIndex:0 effectiveRange:nil];FRComponentCheck((font.fontDescriptor.symbolicTraits&UIFontDescriptorTraitBold)!=0&&! [text.text containsString:@"**"],@"bold is rendered rather than exposed as punctuation");}
        message.text=@"前文\n\n```bash\necho 'streaming'";[host.view layoutIfNeeded];FRComponentCheck(FRMDFind(message,@"markdown.codeText").count==1,@"partial streaming fence renders without dropping content");
        message.text=@"前文\n\n```bash\necho 'streaming'\n```\n\n**完成**";[host.view layoutIfNeeded];FRComponentCheck(FRMDFind(message,@"markdown.codeText").count==1,@"completed stream reconciles blocks without duplicates");
        fprintf(stderr,"FRIDAY_MARKDOWN_UI COMPLETE\n");fflush(stderr);exit(0);
    });
}
