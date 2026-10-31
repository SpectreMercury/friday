// Regression checks for the quiet transcript, not a screenshot-only style check.
static void FRRunInteractionChecks(FRCompactTabsController *tabs) {
    [UIView setAnimationsEnabled:NO];FROpenSession(@"demo-work");
    dispatch_after(dispatch_time(DISPATCH_TIME_NOW,400*NSEC_PER_MSEC),dispatch_get_main_queue(),^{
        FRChatController *chat=(id)tabs.visibleController.topViewController;[chat.draftTimer invalidate];[FRStore.shared stop];
        NSDictionary *job=@{@"id":@"interaction",@"session_id":chat.sid,@"kind":@"chat",@"status":@"running",@"created":@(NSDate.date.timeIntervalSince1970-12),@"draft":@{@"text":@"正在整理资料。",@"phase":@"generating"}};
        chat.jobs=@[job];chat.approvals=@[];chat.stamp=[NSData data];[chat.table reloadData];[chat.table layoutIfNeeded];NSIndexPath *index=[NSIndexPath indexPathForRow:0 inSection:0];UITableViewCell *cell=[chat.table cellForRowAtIndexPath:index];
        FRDisclosureRow *row=(id)FRMDFind(cell,@"component.disclosure").firstObject;CGFloat compact=row.bounds.size.height;
        FRComponentCheck(row&&!row.expanded&&compact<=52,@"running task occupies one quiet line");
        FRComponentCheck(FRMDFind(cell,@"markdown.copyReply").count==0&&FRMDFind(cell,@"task.elapsed").count==1,@"streaming does not duplicate copy or task status controls");
        [FRFindButton(row,@"disclosure.toggle") sendActionsForControlEvents:UIControlEventTouchUpInside];[chat.table layoutIfNeeded];
        FRComponentCheck(row.expanded&&row.bounds.size.height>compact,@"task details expand in the conversation");
        [chat.table reloadData];[chat.table layoutIfNeeded];cell=[chat.table cellForRowAtIndexPath:index];row=(id)FRMDFind(cell,@"component.disclosure").firstObject;
        FRComponentCheck(row.expanded,@"a row refresh preserves the chosen disclosure state");
        [FRFindButton(row,@"disclosure.toggle") sendActionsForControlEvents:UIControlEventTouchUpInside];[chat.table layoutIfNeeded];FRComponentCheck(!row.expanded&&fabs(row.bounds.size.height-compact)<1,@"collapsing restores the compact task row");
        chat.rawMessages=@[job];FRStore.shared.drafts[chat.sid]=[@{@"text":@"立即显示的消息",@"submitted":@"立即显示的消息",@"request_id":@"optimistic-check",@"sending":@YES} mutableCopy];
        NSArray *optimistic=[chat presentedJobs];FRComponentCheck(optimistic.count==2&&[optimistic.lastObject[@"prompt"] isEqual:@"立即显示的消息"],@"send projects a message before the server responds");
        chat.jobs=optimistic;[chat.table reloadData];[chat.table layoutIfNeeded];UITableViewCell *pendingCell=[chat.table cellForRowAtIndexPath:[NSIndexPath indexPathForRow:1 inSection:0]];
        FRComponentCheck(FRMDFind(pendingCell,@"message.sending").count==1,@"optimistic message owns the sending spinner");
        FRStore.shared.drafts[chat.sid][@"sending"]=@NO;FRStore.shared.drafts[chat.sid][@"uncertain"]=@YES;chat.jobs=[chat presentedJobs];[chat.table reloadData];[chat.table layoutIfNeeded];pendingCell=[chat.table cellForRowAtIndexPath:[NSIndexPath indexPathForRow:1 inSection:0]];
        FRComponentCheck(FRMDFind(pendingCell,@"message.sending").count==0&&FRMDFind(pendingCell,@"message.retry").count==1,@"uncertain delivery shows a retry without a spinner");
        [FRStore.shared.drafts removeObjectForKey:chat.sid];chat.acceptedMessage=@{@"id":@"accepted-check",@"session_id":chat.sid,@"prompt":@"立即显示的消息",@"status":@"queued",@"kind":@"chat"};chat.jobs=[chat presentedJobs];[chat.table reloadData];[chat.table layoutIfNeeded];pendingCell=[chat.table cellForRowAtIndexPath:[NSIndexPath indexPathForRow:1 inSection:0]];
        FRComponentCheck(FRMDFind(pendingCell,@"message.sending").count==0&&FRMDFind(pendingCell,@"message.retry").count==0,@"accepted send keeps its bubble and removes delivery status");
        FRStore.shared.drafts[chat.sid]=[@{@"text":@"立即显示的消息",@"submitted":@"立即显示的消息",@"request_id":@"accepted-request",@"uncertain":@YES} mutableCopy];
        chat.rawMessages=@[job,@{@"id":@"server-check",@"session_id":chat.sid,@"prompt":@"立即显示的消息",@"request_id":@"accepted-request"}];
        [chat reconcileAcceptedMessage];
        FRComponentCheck(FRStore.shared.drafts[chat.sid]==nil&&[chat.acceptedMessage[@"id"] isEqual:@"server-check"],@"history confirms a timed-out send by request ID");
        NSDictionary *question=@{@"outcome":@"needs_input",@"result":@"请补充你希望采用的方案。"};UIAlertController *dialog=[chat supplementDialogForJob:question];
        FRComponentCheck([dialog.title isEqual:@"补充信息"]&&[dialog.message isEqual:question[@"result"]]&&dialog.textFields.count==1,@"missing input is presented in a question dialog");
        if([NSProcessInfo.processInfo.arguments containsObject:@"--friday-send-ux-check"]){fprintf(stderr,"FRIDAY_SEND_UX COMPLETE\n");fflush(stderr);exit(0);}
        chat.acceptedMessage=nil;chat.jobs=@[job];[chat.table reloadData];[chat.table layoutIfNeeded];
        NSMutableDictionary *done=[job mutableCopy];[done removeObjectForKey:@"draft"];done[@"status"]=@"succeeded";done[@"result"]=@"**整理完成。**\n\n资料已归档。";done[@"timing"]=@{@"finished_at":@(NSDate.date.timeIntervalSince1970)};chat.jobs=@[done];[chat.table reloadData];[chat.table layoutIfNeeded];cell=[chat.table cellForRowAtIndexPath:index];
        UIButton *copy=(id)FRMDFind(cell,@"markdown.copyReply").firstObject;
        FRComponentCheck(copy&&copy.configuration.title.length==0&&copy.bounds.size.width>=44&&copy.bounds.size.height>=44,@"small copy icon keeps a full mobile touch target");
        FRComponentCheck(FRMDFind(cell,@"task.elapsed").count==1&&FRMDFind(cell,@"component.disclosure").count==0,@"completed reply has one footer and no leftover running panel");
        done[@"required_action"]=@"enable_mac_management";done[@"outcome"]=@"needs_input";chat.jobs=@[done];[chat.table reloadData];[chat.table layoutIfNeeded];cell=[chat.table cellForRowAtIndexPath:index];
        FRComponentCheck(FRMDFind(cell,@"component.permission").count==1&&FRMDFind(cell,@"component.disclosure").count==0,@"permission gap shows one entry without a redundant retry panel");
        FRFileCard *file=[[FRFileCard alloc] initWithFilename:@"行动清单.pdf" kind:@"文件" symbol:@"doc" ready:YES error:nil action:^(UIButton *b){}];
        CGSize size=[file systemLayoutSizeFittingSize:CGSizeMake(358,UILayoutFittingCompressedSize.height) withHorizontalFittingPriority:UILayoutPriorityRequired verticalFittingPriority:UILayoutPriorityFittingSizeLevel];
        FRComponentCheck(size.height<=88&&size.height>=44,@"ready file is one compact attachment row");
        NSMutableString *longReply=[NSMutableString string];for(NSUInteger i=0;i<18;i++)[longReply appendFormat:@"### 第 %lu 项\n\n阅读历史消息时，新回复应留在下方，不打断正在阅读的位置。\n\n",(unsigned long)i+1];
        NSDictionary *longJob=@{@"id":@"reading",@"session_id":chat.sid,@"kind":@"chat",@"status":@"succeeded",@"result":longReply};
        [chat displayJobs:@[longJob] approvals:@[] stamp:[NSData data]];[chat.table layoutIfNeeded];
        chat.table.contentOffset=CGPointMake(0,180);[chat updateLatestButton];CGFloat readingOffset=chat.table.contentOffset.y;
        FRComponentCheck(!chat.latestButton.hidden&&chat.latestButton.bounds.size.width>=44,@"long transcript exposes an accessible return-to-latest control");
        NSMutableDictionary *arrival=[longJob mutableCopy];arrival[@"id"]=@"arrival";arrival[@"result"]=@"新的回复已经完成。";
        [chat displayJobs:@[longJob,arrival] approvals:@[] stamp:[NSData data]];
        FRComponentCheck(fabs(chat.table.contentOffset.y-readingOffset)<1&&!chat.latestButton.hidden,@"new replies preserve the reading position above the bottom");
        [chat.latestButton sendActionsForControlEvents:UIControlEventTouchUpInside];[chat.table layoutIfNeeded];[chat updateLatestButton];
        FRComponentCheck([chat distanceFromLatest]<1&&chat.latestButton.hidden,@"return-to-latest reaches the bottom and then disappears");
        arrival[@"result"]=@"新的回复已补充。";[chat displayJobs:@[longJob,arrival] approvals:@[] stamp:[NSData data]];
        FRComponentCheck([chat distanceFromLatest]<1,@"readers at the bottom continue following updates");
        CGFloat originalFont=chat.input.font.pointSize;chat.input.text=@"一段用于检查大字体输入框的文字，输入框现在占满整行宽度，所以这里需要更长的内容才会超过最大高度并开始滚动。";[chat updateInput];chat.traitOverrides.preferredContentSizeCategory=UIContentSizeCategoryAccessibilityExtraExtraExtraLarge;[chat.view layoutIfNeeded];
        FRComponentCheck(chat.input.font.pointSize>originalFont&&chat.inputHeight.constant<=132&&chat.input.scrollEnabled,@"composer adapts to accessibility text and keeps long input scrollable");
        chat.traitOverrides.preferredContentSizeCategory=UIContentSizeCategoryLarge;chat.input.text=@"";[chat updateInput];[chat.view layoutIfNeeded];
        UIButton *feedback=FRCopyButton(@"复制测试",^NSString *{return @"exact copy";});NSArray *savedPasteboard=UIPasteboard.generalPasteboard.items;[feedback sendActionsForControlEvents:UIControlEventTouchUpInside];
        FRComponentCheck([feedback.accessibilityValue isEqual:@"已复制"]&&[UIPasteboard.generalPasteboard.string isEqual:@"exact copy"],@"copy provides visible and accessible success feedback");UIPasteboard.generalPasteboard.items=savedPasteboard;
        dispatch_after(dispatch_time(DISPATCH_TIME_NOW,2*NSEC_PER_SEC),dispatch_get_main_queue(),^{
            FRComponentCheck(chat.inputHeight.constant<=46,@"empty composer returns to one line after resizing text");
            chat.input.text=@"短消息";[chat updateInput];[chat.view layoutIfNeeded];FRComponentCheck(chat.inputHeight.constant<=46,@"short input also returns to one line after resizing text");chat.input.text=@"";[chat updateInput];
            FRComponentCheck(feedback.accessibilityValue==nil&&[feedback.accessibilityLabel isEqual:@"复制测试"],@"copy feedback resets while preserving its action label");
            fprintf(stderr,"FRIDAY_INTERACTION COMPLETE\n");fflush(stderr);
            if([NSProcessInfo.processInfo.arguments containsObject:@"--friday-reading-preview"]){chat.table.contentOffset=CGPointMake(0,180);[chat updateLatestButton];return;}exit(0);
        });
    });
}
