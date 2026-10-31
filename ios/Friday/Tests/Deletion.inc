// Compiled only into Debug simulators; deletion paths against the demo service.
static void FRDeletionCheck(BOOL passed,NSString *name) {
    fprintf(stderr,"FRIDAY_DELETION %s %s\n",passed?"PASS":"FAIL",name.UTF8String);fflush(stderr);
    if(!passed)exit(1);
}
static NSIndexPath *FRDeletionIndex(FRListController *list,NSString *rowID){
    for(NSInteger section=0;section<[list numberOfSectionsInTableView:list.tableView];section++)for(NSInteger row=0;row<[list tableView:list.tableView numberOfRowsInSection:section];row++){
        NSIndexPath *index=[NSIndexPath indexPathForRow:row inSection:section];if([[list rowAt:index][@"id"] isEqual:rowID])return index;}
    return nil;
}
static void FRAfter(double seconds,dispatch_block_t block){dispatch_after(dispatch_time(DISPATCH_TIME_NOW,(int64_t)(seconds*NSEC_PER_SEC)),dispatch_get_main_queue(),block);}
static void FRRunDeletionChecks(UITabBarController *tabs) {
    [UIView setAnimationsEnabled:NO];
    FRListController *plain=[FRListController new];[plain loadViewIfNeeded];plain.rows=@[@{@"title":@"a",@"key":@"file-a"},@{@"title":@"b",@"id":@"b"}];[plain hideRows:@[@"file-a"]];
    FRDeletionCheck([plain tableView:plain.tableView numberOfRowsInSection:0]==1,@"a row being deleted leaves its list at once");
    plain.rows=@[@{@"title":@"a",@"key":@"file-a"},@{@"title":@"b",@"id":@"b"}];FRDeletionCheck([plain tableView:plain.tableView numberOfRowsInSection:0]==1,@"and stays out when the list reloads");
    [plain unhideRows:@[@"file-a"]];FRDeletionCheck([plain tableView:plain.tableView numberOfRowsInSection:0]==2,@"a failed delete brings the row back");
    FRSessionsController *list=(id)((UINavigationController *)tabs.viewControllers[0]).viewControllers.firstObject;[list reloadData];
    NSIndexPath *index=FRDeletionIndex(list,@"demo-mac");
    FRDeletionCheck(index!=nil,@"conversation is listed with a selectable id");
    UISwipeActionsConfiguration *swipe=[list tableView:list.tableView trailingSwipeActionsConfigurationForRowAtIndexPath:index];
    FRDeletionCheck(swipe.actions.count==2&&swipe.actions.firstObject.style==UIContextualActionStyleDestructive&&[swipe.actions[1].title isEqual:@"归档"],@"swipe offers delete and archive");
    UIContextMenuConfiguration *menu=[list tableView:list.tableView contextMenuConfigurationForRowAtIndexPath:index point:CGPointZero];
    FRDeletionCheck(menu!=nil,@"long press opens the conversation menu");
    [list toggleSelecting];FRDeletionCheck(list.editing&&list.toolbarItems.count>=2&&!list.toolbarItems.lastObject.enabled,@"choosing several shows disabled batch actions");
    [list.tableView selectRowAtIndexPath:FRDeletionIndex(list,@"demo-mac") animated:NO scrollPosition:UITableViewScrollPositionNone];[list tableView:list.tableView didSelectRowAtIndexPath:FRDeletionIndex(list,@"demo-mac")];
    FRDeletionCheck(list.toolbarItems.lastObject.enabled&&[list.toolbarItems.lastObject.title containsString:@"（1）"],@"batch delete counts the chosen rows");
    [list endSelecting];
    FRDeleteSessions(list,@[@"demo-mac"],^{
        FRDeletionCheck(![FRStore.shared session:@"demo-mac"]&&!FRDeletionIndex(list,@"demo-mac"),@"deleted conversation leaves the list at once");
        FRDeletionCheck([list.view.window viewWithTag:6190]!=nil,@"an undo toast appears");
        [FRAPI.shared get:@"/v1/trash" completion:^(NSDictionary *trash,NSError *e){
            FRDeletionCheck([[trash[@"items"] valueForKey:@"id"] containsObject:@"demo-mac"],@"it waits in 最近删除");
            FRRestoreSessions(list,@[@"demo-mac"],^{FRAfter(1.5,^{
                FRDeletionCheck([FRStore.shared session:@"demo-mac"]!=nil,@"restore brings it back");
                FROpenSession(@"demo-work");
                FRAfter(1,^{
                    FRChatController *chat=(id)((UINavigationController *)tabs.viewControllers[0]).topViewController;
                    FRDeletionCheck([chat isKindOfClass:FRChatController.class]&&chat.jobs.count>0,@"conversation opens");
                    NSArray *items=[chat turnMenu:chat.jobs.firstObject];UIAction *last=(id)items.lastObject;
                    FRDeletionCheck([last isKindOfClass:UIAction.class]&&last.attributes&UIMenuElementAttributesDestructive,@"each turn offers delete");
                    FRDeletionCheck([chat tableView:chat.table contextMenuConfigurationForRowAtIndexPath:[NSIndexPath indexPathForRow:0 inSection:0] point:CGPointZero]!=nil,@"long press on a turn opens its menu");
                    NSArray *conversation=[chat conversationMenu];UIMenu *danger=conversation.lastObject;
                    FRDeletionCheck([[danger.children valueForKey:@"title"] containsObject:@"删除对话"],@"conversation menu offers delete");
                    fprintf(stderr,"FRIDAY_DELETION COMPLETE\n");fflush(stderr);exit(0);
                });
            });});
        }];
    });
    FRDeletionCheck(![FRStore.shared session:@"demo-mac"]&&!FRDeletionIndex(list,@"demo-mac"),@"deleted conversation leaves before the Mac answers");
    FRStore.shared.sessions=[FRStore.shared.sessions arrayByAddingObject:@{@"id":@"demo-mac",@"name":@"stale"}];
    FRDeletionCheck(![FRStore.shared session:@"demo-mac"],@"a refresh that still lists it does not bring it back");
}
