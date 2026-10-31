#import "FRDemo.h"
#import "FRAPI.h"
#import <TargetConditionals.h>
BOOL FRDemoEnabled(void) {
#if DEBUG && TARGET_OS_SIMULATOR
    return [NSProcessInfo.processInfo.arguments containsObject:@"--friday-demo"];
#else
    return NO;
#endif
}
NSDictionary *FRDemoPairing(void){return @{@"token":@"isolated-simulator-demo-no-server-access",@"device_id":@"demo-device",@"account":@{@"id":@"demo-account",@"is_admin":@YES}};}
id FRDemoRealistic(NSString *path,NSString *method,id body);
static id FRDemoStatic(NSString *path,NSString *method,id body);

#pragma mark - Live demo: new conversations, streamed replies, agent suggestions

static NSMutableArray<NSMutableDictionary *> *FRDemoNewSessions(void){static NSMutableArray *v;if(!v)v=[NSMutableArray array];return v;}
static NSMutableDictionary<NSString *,NSMutableArray *> *FRDemoJobs(void){static NSMutableDictionary *v;if(!v)v=[NSMutableDictionary dictionary];return v;}
static NSMutableDictionary<NSString *,id> *FRDemoHandlers(void){static NSMutableDictionary *v;if(!v)v=[NSMutableDictionary dictionary];return v;}
void FRDemoSubscribe(NSString *sid,void(^handler)(NSDictionary *event)){FRDemoHandlers()[sid]=[handler copy];}
void FRDemoUnsubscribe(NSString *sid){[FRDemoHandlers() removeObjectForKey:sid];}
static BOOL FRDemoLooksLikeDrama(NSString *text){for(NSString *word in @[@"短剧",@"剧本",@"分镜",@"内景",@"外景",@"第1场",@"第一场",@"成片"])if([text containsString:word])return YES;return NO;}
static NSMutableDictionary *FRDemoSession(NSString *sid){for(NSMutableDictionary *s in FRDemoNewSessions())if([s[@"id"] isEqual:sid])return s;return nil;}
static void FRDemoEmit(NSMutableDictionary *job,NSDictionary *event){
    NSMutableDictionary *live=job[@"live"];live[@"seq"]=@([live[@"seq"] integerValue]+1);
    NSString *type=event[@"type"];
    if([type isEqual:@"thinking"])live[@"thinking"]=[live[@"thinking"] stringByAppendingString:event[@"delta"]];
    if([type isEqual:@"text"])live[@"text"]=[live[@"text"] stringByAppendingString:event[@"delta"]];
    if([type isEqual:@"step"])live[@"text"]=@"";
    if([type isEqual:@"tool"]){NSMutableArray *tools=[live[@"tools"] mutableCopy];NSUInteger i=[[tools valueForKey:@"id"] indexOfObject:event[@"tool"][@"id"]];if(i==NSNotFound)[tools addObject:event[@"tool"]];else tools[i]=event[@"tool"];live[@"tools"]=tools;}
    if([type isEqual:@"suggest"])live[@"suggested"]=event[@"agent"];
    if([type isEqual:@"status"])live[@"status"]=event[@"text"];
    NSMutableDictionary *payload=[event mutableCopy];payload[@"job_id"]=job[@"id"];payload[@"seq"]=live[@"seq"];
    void(^handler)(NSDictionary *)=FRDemoHandlers()[job[@"session_id"]];if(handler)handler(payload);
}
// Plays a scripted reply as the Mac would stream it: thinking, tool steps, then text.
static void FRDemoPlay(NSMutableDictionary *job){
    NSDictionary *options=job[@"options"];BOOL thinking=[options[@"thinking"] boolValue],drama=[options[@"agents"] containsObject:@"drama"],suggest=!drama&&FRDemoLooksLikeDrama(job[@"prompt"]);
    NSMutableArray *steps=[NSMutableArray array];
    void(^chunks)(NSString *,NSString *,NSUInteger)=^(NSString *type,NSString *text,NSUInteger size){for(NSUInteger i=0;i<text.length;i+=size)[steps addObject:@{@"type":type,@"delta":[text substringWithRange:NSMakeRange(i,MIN(size,text.length-i))]}];};
    if(thinking)chunks(@"thinking",drama?@"用户请短剧助手参与：先导入剧本，确认人物与场次，再按场景拆成 4–15 秒的镜头，给出分镜后请用户确认再渲染草稿。":(suggest?@"这段内容像剧本。短剧工具需要用户邀请短剧助手加入，先说明可以怎么做。":@"用户想要一份清晰的安排。先查一下相关信息，再按优先级整理成三条。"),3);
    NSArray *tools=drama?@[@[@"t1",@"drama_import",@"短剧 · 导入剧本",@"雨夜咖啡馆"],@[@"t2",@"drama_plan",@"短剧 · 设定与分镜",@"storyboard"]]:(suggest?@[]:@[@[@"t1",@"web_search",@"搜索网页",@"周末 安排 建议"]]);
    for(NSArray *t in tools){[steps addObject:@{@"type":@"tool",@"tool":@{@"id":t[0],@"name":t[1],@"label":t[2],@"note":t[3],@"status":@"running"}}];[steps addObject:@{@"type":@"pause"}];[steps addObject:@{@"type":@"tool",@"tool":@{@"id":t[0],@"name":t[1],@"label":t[2],@"note":t[3],@"status":@"done"}}];}
    if(tools.count)[steps addObject:@{@"type":@"step"}];
    if(suggest)[steps addObject:@{@"type":@"suggest",@"agent":@"drama"}];
    NSString *answer=drama?@"剧本已导入为短剧项目「雨夜咖啡馆」，拆成 **2 场 · 4 个镜头**，约 25 秒：\n\n1. 林夏推门进店，环顾四周（6 秒）\n2. 店员抬头提醒「快打烊了」（5 秒）\n3. 林夏坐下，取出旧照片（8 秒）\n4. 林夏撑伞走进雨里（6 秒）\n\n确认分镜后，我先渲染低成本草稿，预计 15 分钟。":(suggest?@"这看起来是一段剧本。让**短剧助手**加入后，我可以把它导入成短剧项目：整理人物与场次、拆分镜头、渲染草稿，再合成成片。\n\n点下方「让短剧助手加入」即可继续。":@"可以这样安排这个周末：\n\n- **周六上午**：把本周的资料归档，留出 30 分钟回顾进展。\n- **周六下午**：出门走走，顺路处理需要线下办的事。\n- **周日**：确定下周最重要的一件事，写进日程。\n\n需要我把它加进定时提醒吗？");
    chunks(@"text",answer,3);
    __block NSUInteger index=0;__block void(^next)(void);__weak __block void(^weakNext)(void);
    next=^{
        if(index>=steps.count){
            job[@"status"]=@"succeeded";job[@"status_text"]=@"已完成";job[@"outcome"]=@"completed";job[@"result"]=answer;job[@"timing"]=@{@"finished_at":@(NSDate.date.timeIntervalSince1970)};
            if(drama)job[@"drama"]=@{@"id":@"p1",@"title":@"雨夜咖啡馆 · 第 1 集",@"stage":@"storyboard",@"shots":@4,@"done":@0,@"seconds":@25};
            if(suggest){NSMutableDictionary *o=[job[@"options"] mutableCopy];o[@"suggested"]=@"drama";job[@"options"]=o;}
            NSMutableDictionary *session=FRDemoSession(job[@"session_id"]);if([session[@"auto_title"] boolValue]){session[@"name"]=drama||suggest?@"雨夜咖啡馆短剧":@"周末安排";session[@"auto_title"]=@0;}
            FRDemoEmit(job,@{@"type":@"done"});[job removeObjectForKey:@"live"];return;
        }
        NSDictionary *step=steps[index++];BOOL pause=[step[@"type"] isEqual:@"pause"];
        if(!pause){if(!job[@"live"])return;FRDemoEmit(job,step);}
        NSTimeInterval delay=pause?0.9:([step[@"type"] isEqual:@"thinking"]?0.05:([step[@"type"] isEqual:@"text"]?0.06:0.2));
        void(^again)(void)=weakNext;dispatch_after(dispatch_time(DISPATCH_TIME_NOW,(int64_t)(delay*NSEC_PER_SEC)),dispatch_get_main_queue(),^{if(again)again();});
    };
    weakNext=next;
    // Keep the block alive for the scripted run; it releases itself when finished.
    static NSMutableArray *running;if(!running)running=[NSMutableArray array];[running addObject:next];
    NSTimeInterval wait=[NSProcessInfo.processInfo.arguments containsObject:@"--friday-slow-stream"]?4:0.6;
    // Slow mode stands in for a cold Mac: the model has to load before the first token.
    if(wait>1)dispatch_after(dispatch_time(DISPATCH_TIME_NOW,(int64_t)(0.8*NSEC_PER_SEC)),dispatch_get_main_queue(),^{if(job[@"live"])FRDemoEmit(job,@{@"type":@"status",@"text":@"正在唤醒本地模型，大约需要 20 秒…"});});
    dispatch_after(dispatch_time(DISPATCH_TIME_NOW,(int64_t)(wait*NSEC_PER_SEC)),dispatch_get_main_queue(),^{FRDemoEmit(job,@{@"type":@"start"});next();});
}
static id FRDemoLive(NSString *path,NSString *method,id body){
    if([path isEqual:@"/v1/agents"])return @{@"items":@[@{@"id":@"drama",@"name":@"短剧助手",@"symbol":@"film",@"description":@"把剧本做成多镜头短剧：设定、分镜、草稿与正式渲染、合成成片"}]};
    if([path isEqual:@"/v1/agents/suggest"]){NSString *text=body[@"text"];BOOL invited=[body[@"agents"] containsObject:@"drama"];return @{@"suggestion":(!invited&&[text isKindOfClass:NSString.class]&&FRDemoLooksLikeDrama(text))?@{@"id":@"drama",@"name":@"短剧助手",@"symbol":@"film",@"reason":@"这看起来是剧本或短剧制作"}:NSNull.null};}
    if([path isEqual:@"/v1/sessions"]&&[method isEqual:@"POST"]){
        NSMutableDictionary *session=[@{@"id":[NSString stringWithFormat:@"demo-new-%lu",(unsigned long)FRDemoNewSessions().count+1],@"name":@"新对话",@"auto_title":@1,@"revision":@1,@"skill_names":@[],@"management":@NO,@"archived":@NO} mutableCopy];
        [FRDemoNewSessions() insertObject:session atIndex:0];return session;
    }
    NSArray *parts=[path componentsSeparatedByString:@"/"];
    if(parts.count==5&&[parts[3] length]&&[parts[4] isEqual:@"messages"]&&[method isEqual:@"POST"]){
        NSString *sid=parts[3];NSMutableArray *jobs=FRDemoJobs()[sid]?:[NSMutableArray array];FRDemoJobs()[sid]=jobs;
        NSMutableDictionary *job=[@{@"id":[NSString stringWithFormat:@"demo-live-%@-%lu",sid,(unsigned long)jobs.count],@"session_id":sid,@"kind":@"chat",@"prompt":body[@"text"]?:@"",@"request_id":body[@"request_id"]?:@"",@"status":@"running",@"status_text":@"进行中",@"created":@(NSDate.date.timeIntervalSince1970),@"timing":@{@"running_since":@(NSDate.date.timeIntervalSince1970)},
            @"options":@{@"thinking":@([body[@"thinking"] boolValue]),@"agents":body[@"agents"]?:@[],@"suggested":NSNull.null},@"live":[@{@"thinking":@"",@"text":@"",@"tools":@[],@"seq":@0} mutableCopy]} mutableCopy];
        [jobs addObject:job];NSMutableDictionary *session=FRDemoSession(sid);
        if([session[@"auto_title"] boolValue]&&[session[@"name"] isEqual:@"新对话"]){NSString *line=[[body[@"text"] componentsSeparatedByString:@"\n"] firstObject];session[@"name"]=line.length>18?[[line substringToIndex:18] stringByAppendingString:@"…"]:line;}
        FRDemoPlay(job);NSMutableDictionary *reply=[job mutableCopy];[reply removeObjectForKey:@"live"];return reply;
    }
    return nil;
}
static NSArray *FRDemoExtraJobs(NSString *sid){NSMutableArray *items=[NSMutableArray array];for(NSDictionary *job in FRDemoJobs()[sid]){NSMutableDictionary *copy=[job mutableCopy];if(copy[@"live"])copy[@"live"]=[copy[@"live"] copy];[items addObject:copy];}return items;}
static NSDictionary *FRDemoWithExtras(NSDictionary *response,NSString *path){
    if([path isEqual:@"/v1/sessions"]&&[response[@"items"] isKindOfClass:NSArray.class]){NSMutableArray *items=[NSMutableArray array];for(NSDictionary *s in FRDemoNewSessions())[items addObject:[s copy]];[items addObjectsFromArray:response[@"items"]];return @{@"items":items};}
    NSArray *parts=[path componentsSeparatedByString:@"/"];
    if(parts.count==5&&[parts[4] isEqual:@"messages"]&&FRDemoJobs()[parts[3]].count){NSMutableArray *items=[response[@"items"]?:@[] mutableCopy];[items addObjectsFromArray:FRDemoExtraJobs(parts[3])];return @{@"items":items,@"revision":[NSUUID UUID].UUIDString};}
    return response;
}

#pragma mark - Demo deletion: 最近删除, deleted turns, archive and rename

static NSMutableDictionary *FRDemoTrash(void){static NSMutableDictionary *v;if(!v)v=[NSMutableDictionary dictionary];return v;}
static NSMutableSet *FRDemoGone(void){static NSMutableSet *v;if(!v)v=[NSMutableSet set];return v;}
static NSMutableDictionary *FRDemoOverrides(void){static NSMutableDictionary *v;if(!v)v=[NSMutableDictionary dictionary];return v;}
static id FRDemoRespond(NSString *path,NSString *method,id body);
static NSDictionary *FRDemoApply(NSDictionary *session){NSDictionary *o=FRDemoOverrides()[session[@"id"]];if(!o)return session;NSMutableDictionary *m=[session mutableCopy];[m addEntriesFromDictionary:o];return m;}
static BOOL FRDemoHidden(NSString *sid){return FRDemoTrash()[sid]||[FRDemoGone() containsObject:sid];}
static id FRDemoFilter(NSString *path,id response){
    if(![response isKindOfClass:NSDictionary.class]||![response[@"items"] isKindOfClass:NSArray.class])return [response isKindOfClass:NSDictionary.class]&&response[@"id"]&&[path hasPrefix:@"/v1/sessions/"]?FRDemoApply(response):response;
    NSMutableArray *items=[NSMutableArray array];
    for(NSDictionary *item in response[@"items"]){
        if([path isEqual:@"/v1/sessions"]){if(!FRDemoHidden(item[@"id"]))[items addObject:FRDemoApply(item)];continue;}
        if([path isEqual:@"/v1/dramas"]){if(FRDemoHidden([@"drama-" stringByAppendingString:item[@"id"]]))continue;NSMutableDictionary *m=[item mutableCopy];m[@"session_id"]=[@"drama-" stringByAppendingString:item[@"id"]];[items addObject:m];continue;}
        if(item[@"session_id"]&&(FRDemoHidden(item[@"session_id"])||[FRDemoGone() containsObject:FRString(item[@"id"])]))continue;
        [items addObject:item];
    }
    NSMutableDictionary *copy=[response mutableCopy];copy[@"items"]=items;return copy;
}
id FRDemoResponse(NSString *path,NSString *method,id body) {
    path=[[path componentsSeparatedByString:@"?"] firstObject];NSArray *parts=[path componentsSeparatedByString:@"/"];NSTimeInterval now=NSDate.date.timeIntervalSince1970;
    if([method isEqual:@"DELETE"]&&parts.count==4&&[parts[2] isEqual:@"sessions"]){
        NSString *sid=parts[3],*name=@"对话";for(NSDictionary *s in FRDemoRespond(@"/v1/sessions",@"GET",nil)[@"items"])if([s[@"id"] isEqual:sid])name=FRDemoApply(s)[@"name"];
        FRDemoTrash()[sid]=@{@"id":sid,@"name":name,@"kind":@"session",@"deleted":@(now),@"expires":@(now+30*86400)};return @{@"deleted":@YES,@"id":sid};
    }
    if([method isEqual:@"DELETE"]&&parts.count==4&&[parts[2] isEqual:@"dramas"]){
        NSString *sid=[@"drama-" stringByAppendingString:parts[3]],*name=@"短剧";for(NSDictionary *d in FRDemoRespond(@"/v1/dramas",@"GET",nil)[@"items"])if([d[@"id"] isEqual:parts[3]])name=d[@"title"];
        FRDemoTrash()[sid]=@{@"id":sid,@"name":name,@"kind":@"drama",@"deleted":@(now),@"expires":@(now+30*86400)};return @{@"deleted":@YES};
    }
    if([path isEqual:@"/v1/trash"]){if([method isEqual:@"DELETE"]){[FRDemoGone() addObjectsFromArray:FRDemoTrash().allKeys];[FRDemoTrash() removeAllObjects];return @{@"items":@[]};}
        return @{@"items":[FRDemoTrash().allValues sortedArrayUsingDescriptors:@[[NSSortDescriptor sortDescriptorWithKey:@"deleted" ascending:NO]]],@"days":@30};}
    if(parts.count==5&&[parts[2] isEqual:@"trash"]&&[parts[4] isEqual:@"restore"]){NSDictionary *item=FRDemoTrash()[parts[3]];[FRDemoTrash() removeObjectForKey:parts[3]];return @{@"id":parts[3],@"name":item[@"name"]?:@""};}
    if(parts.count==4&&[parts[2] isEqual:@"trash"]&&[method isEqual:@"DELETE"]){[FRDemoTrash() removeObjectForKey:parts[3]];[FRDemoGone() addObject:parts[3]];return @{@"purged":@YES};}
    if([method isEqual:@"DELETE"]&&parts.count==6&&[parts[4] isEqual:@"tasks"]){[FRDemoGone() addObject:parts[5]];return @{@"deleted":@[parts[5]]};}
    if([method isEqual:@"DELETE"]&&parts.count==6&&[parts[4] isEqual:@"files"])return @{@"deleted":@YES};
    if([method isEqual:@"PATCH"]&&parts.count==4&&[parts[2] isEqual:@"sessions"]&&([body[@"name"] isKindOfClass:NSString.class]||body[@"archived"])){
        NSMutableDictionary *o=[FRDemoOverrides()[parts[3]] mutableCopy]?:[NSMutableDictionary dictionary];if([body[@"name"] isKindOfClass:NSString.class])o[@"name"]=body[@"name"];if(body[@"archived"])o[@"archived"]=body[@"archived"];FRDemoOverrides()[parts[3]]=o;
        NSMutableDictionary *s=[FRDemoFilter(path,FRDemoRespond(path,@"GET",nil)) mutableCopy]?:[NSMutableDictionary dictionary];s[@"id"]=parts[3];[s addEntriesFromDictionary:o];return s;
    }
    return FRDemoFilter(path,FRDemoRespond(path,method,body));
}
static id FRDemoRespond(NSString *path,NSString *method,id body) {
    id dynamic=FRDemoLive(path,method,body);if(dynamic)return dynamic;
    for(NSDictionary *s in FRDemoNewSessions())if([path isEqual:[@"/v1/sessions/" stringByAppendingString:s[@"id"]]])return [s copy];
    NSArray *parts=[path componentsSeparatedByString:@"/"];
    if(parts.count==5&&[parts[3] hasPrefix:@"demo-new-"]&&[parts[4] isEqual:@"messages"])return FRDemoWithExtras(@{@"items":@[],@"revision":@"0"},path);
    if([path isEqual:@"/v1/sessions"]||(parts.count==5&&[parts[4] isEqual:@"messages"]))return FRDemoWithExtras(FRDemoStatic(path,method,body),path);
    return FRDemoStatic(path,method,body);
}
static id FRDemoStatic(NSString *path,NSString *method,id body) {
    if([NSProcessInfo.processInfo.arguments containsObject:@"--friday-real"]){id real=FRDemoRealistic(path,method,body);if(real)return real;}
    static BOOL managementEnabled=NO;
    if([method isEqual:@"PATCH"]&&[path isEqual:@"/v1/sessions/demo-work"]&&body[@"management"])managementEnabled=[body[@"management"] boolValue];
    NSArray *sessions=@[@{@"id":@"demo-work",@"name":@"想法与日常",@"revision":@1,@"skill_names":@[@"研究",@"文档"],@"management":@(managementEnabled),@"archived":@NO,@"created":@(NSDate.date.timeIntervalSince1970-3*3600)},@{@"id":@"demo-mac",@"name":@"Mac 管理",@"revision":@1,@"skill_names":@[@"设备诊断"],@"management":@YES,@"archived":@NO,@"created":@(NSDate.date.timeIntervalSince1970-3*86400)}];
    NSDictionary *job=@{@"id":@"demo-job",@"session_id":@"demo-work",@"prompt":@"帮我整理一下这周的想法，做成一份清晰的行动清单。",@"result":@"我把这些想法整理成了三件值得先做的事。\n\n今天 · 留出 30 分钟，把最想完成的一件事写下来。\n\n这周 · 把手头的资料归到同一个工作空间，保留真正有用的部分。\n\n周末 · 回顾一次进展，再决定下一步。\n\n你想从哪一件开始？",@"status":@"succeeded",@"kind":@"chat",@"created":@1790500000,@"timing":@{@"finished_at":@1790500038}};
    if([path hasSuffix:@"/evolution"])return @{@"updater_online":@YES,@"mcp":@{@"names":@[],@"revision":@0},@"catalog":@[@{@"kind":@"mcp",@"name":@"workspace-summary"}],@"items":@[@{@"id":@"demo-revision",@"kind":@"skill",@"name":@"meeting-notes",@"summary":@"把会议记录整理为决定与待办，保留原始依据。",@"state":@"staged"},@{@"id":@"demo-core",@"kind":@"core",@"name":@"friday",@"summary":@"优化语音响应，原版本已保留。",@"state":@"succeeded"}]};
    if([path containsString:@"/evolution/"])return @{@"id":@"demo-revision",@"kind":@"skill",@"name":@"meeting-notes",@"summary":@"把会议记录整理为决定与待办，保留原始依据。",@"state":@"staged",@"files":@{@"SKILL.md":@"# 会议整理\n\n保留每项决定的依据，列出负责人与下一步。"},@"report":@{@"static_validation":@"passed"}};
    if([path isEqual:@"/v1/sessions"])return @{@"items":sessions};
    if([path hasSuffix:@"/messages"]){
        if([NSProcessInfo.processInfo.arguments containsObject:@"--friday-markdown-review"]){
            NSString *table=@"## 当前设置\n\n已读取 `com.apple.SoftwareUpdate.plist`，以下为排版演示数据。\n\n| 设置项 | 当前值 | 含义 |\n|---|:---:|---|\n| `AutomaticDownload` | `true` | 自动下载更新 |\n| `AutomaticallyInstallMacOSUpdates` | `true` | 自动安装 macOS 更新 |\n| `ConfigDataInstall` | `true` | 自动安装配置描述文件更新 |\n| `CriticalUpdateInstall` | `true` | 自动安装关键更新 |\n\n**结论：这里是加粗文字。** 普通说明、`行内代码`与表格保持各自的排版。\n\n---\n\n> 表格可以横向滚动，查看完整设置项及其他列。";
            NSString *code=@"**你需要做的（二选一）： **\n\n1. **在 Mac 终端查看以下设置**（代码只作为排版示例）：\n\n```bash\ndefaults read /Library/Preferences/com.apple.SoftwareUpdate AutomaticDownload\ndefaults read /Library/Preferences/com.apple.SoftwareUpdate AutomaticallyInstallMacOSUpdates\nprintf '%s\\n' '保留原始换行和缩进'\n```\n\n2. **或者在系统设置中查看**，再决定下一步。\n\n代码可横向查看，点右上角**复制代码**保留完整命令。\n\n[Apple 支持](https://support.apple.com/) · ~~旧说明~~";
            NSMutableDictionary *sample=[job mutableCopy];sample[@"prompt"]=@"检查一下 Markdown 的表格、粗体和代码块排版。";sample[@"result"]=[NSProcessInfo.processInfo.arguments containsObject:@"--friday-markdown-code"]?code:table;return @{@"items":@[sample]};
        }

        if([NSProcessInfo.processInfo.arguments containsObject:@"--friday-ui-review"]){
            NSDictionary *first=@{@"id":@"review-first",@"session_id":@"demo-work",@"kind":@"chat",@"prompt":@"帮我看看 Mac 的网络。",@"result":@"网络连接正常，HTTPS 连接验证通过。\n\n当前代理可用，暂时不需要切换节点。",@"status":@"succeeded",@"outcome":@"completed",@"created":@1790500000,@"timing":@{@"finished_at":@1790500038}};
            NSDictionary *second=@{@"id":@"review-second",@"session_id":@"demo-work",@"kind":@"chat",@"prompt":@"打开一下 Mac 的计算器，并截图给我。",@"result":@"当前会话未开启「Mac 管理」，本次电脑操作未执行。开启后请重试原请求。",@"status":@"succeeded",@"outcome":@"needs_input",@"required_action":@"enable_mac_management",@"created":@1790500100,@"timing":@{@"finished_at":@1790500112}};
            return @{@"items":@[first,second]};
        }
        return @{@"items":@[job]};
    }
    if([path isEqual:@"/v1/home"])return @{@"mac":@{@"online":@YES,@"busy":@YES,@"loaded":@"video",@"voice":@NO,@"queued":@3,@"done_today":@11},
        @"now":@{@"id":@"demo-render",@"kind":@"video",@"title":@"雨夜来电 · 镜头 6 正式渲染",@"session_id":@"demo-work",@"session_name":@"雨夜来电",@"mine":@YES,@"elapsed":@400,@"eta_seconds":@240,@"progress":@0.62},
        @"queue":@[@{@"id":@"demo-q1",@"kind":@"image",@"title":@"生成图片 · 浅草夜景封面",@"session_id":@"demo-work",@"session_name":@"东京三日行程",@"position":@1,@"eta_seconds":@240},
                   @{@"id":@"demo-q2",@"kind":@"video",@"title":@"雨夜来电 · 镜头 7 正式渲染",@"session_id":@"demo-work",@"session_name":@"雨夜来电",@"position":@2,@"eta_seconds":@720}],
        @"active":@[],
        @"attention":@[@{@"id":@"demo-2",@"kind":@"chat",@"title":@"更新工作空间的技能",@"session_id":@"demo-work",@"session_name":@"想法与日常",@"reason":@"approval"},
                       @{@"id":@"demo-p",@"kind":@"chat",@"title":@"整理论文笔记",@"session_id":@"demo-work",@"session_name":@"论文",@"reason":@"paused"}],
        @"next_schedule":@{@"id":@"s1",@"title":@"提醒交周报",@"when":@"每周一 09:00",@"next_run":@"2026-11-02 09:00"}};
    if([path isEqual:@"/v1/tasks"]){NSMutableArray *jobs=[NSMutableArray array];NSArray *states=@[@"running",@"queued",@"waiting_approval",@"succeeded",@"failed",@"cancelled"],*prompts=@[@"整理本周的资料",@"为周末做一份安排",@"更新工作空间的技能",@"整理行动清单",@"生成一段视频",@"检查 Mac 的网络"];for(NSUInteger i=0;i<states.count;i++){NSMutableDictionary *item=[job mutableCopy];item[@"id"]=[NSString stringWithFormat:@"demo-%lu",(unsigned long)i];item[@"status"]=states[i];item[@"prompt"]=prompts[i];item[@"created"]=@(NSDate.date.timeIntervalSince1970-72);item[@"timing"]=@{@"finished_at":@(NSDate.date.timeIntervalSince1970-34)};[jobs addObject:item];}return @{@"items":jobs};}
    if([path isEqual:@"/v1/skills"])return @{@"items":@[@{@"name":@"研究",@"description":@"整理资料与形成判断",@"version":@"1"},@{@"name":@"文档",@"description":@"让内容清晰成形",@"version":@"1"}]};
    for(NSDictionary *s in sessions)if([path isEqual:[@"/v1/sessions/" stringByAppendingString:s[@"id"]]])return s;
    return @{@"items":@[],@"revision":@1};
}

// Realistic fixtures (--friday-real): the shapes real use produces — long raw
// prompts, paused and merged turns, task progress, drama, memory, schedules.
static NSDictionary *FRRealShot(NSString *sid,NSInteger index,NSString *scene,NSString *action,NSString *camera,NSInteger seconds,NSString *status,NSString *error,NSArray *takes){
    return @{@"id":sid,@"index":@(index),@"scene_id":scene,@"action":action,@"visual":@"",@"camera":camera,@"duration":@(seconds),@"character_ids":@[@"c1"],@"dialogue":index==2?@[@{@"name":@"店员",@"line":@"快打烊了。"}]:@[],@"takes":takes,@"status":status,@"last_error":error?:NSNull.null,@"approved":@([status isEqual:@"approved"]||[status isEqual:@"done"]),@"draft_take_id":takes.count?takes[0][@"id"]:NSNull.null,@"final_take_id":[status isEqual:@"done"]?takes.lastObject[@"id"]:NSNull.null,@"prompt_override":NSNull.null};
}
id FRDemoRealistic(NSString *path,NSString *method,id body){
    NSTimeInterval now=NSDate.date.timeIntervalSince1970;
    if([path isEqual:@"/v1/sessions"])return @{@"items":@[
        @{@"id":@"demo-work",@"name":@"想法与日常",@"revision":@1,@"skill_names":@[@"研究",@"文档"],@"management":@NO,@"archived":@NO,@"active":@1,@"waiting":@0,@"last":@{@"title":@"查一下 Python 3.13 有哪些主要新特性，整理成 5 条要点保存到 py313.md",@"status":@"running",@"status_text":@"进行中",@"created":@(now-90)}},
        @{@"id":@"demo-video",@"name":@"视频创作",@"revision":@1,@"skill_names":@[@"视频创作"],@"management":@NO,@"archived":@NO,@"active":@0,@"waiting":@1,@"last":@{@"title":@"给我做一段海边日落的视频，按我的默认设置",@"status":@"waiting_approval",@"status_text":@"等待确认",@"created":@(now-600)}},
        @{@"id":@"demo-mac",@"name":@"Mac 管理",@"revision":@1,@"skill_names":@[@"设备诊断"],@"management":@YES,@"archived":@NO,@"active":@0,@"waiting":@0,@"last":@{@"title":@"定时 · BTC 价格监控",@"status":@"succeeded",@"status_text":@"无需提醒",@"created":@(now-1800)}}]};
    if([path isEqual:@"/v1/tasks"]){
        NSArray *rows=@[@[@"running",@"",@"进行中",@"查一下 Python 3.13 有哪些主要新特性，整理成 5 条要点保存到 py313.md",@"demo-work"],
                        @[@"waiting_approval",@"",@"等待确认",@"生成视频 · 给我做一段海边日落的视频，按我的默认设置",@"demo-video"],
                        @[@"succeeded",@"paused",@"已暂停",@"帮我在大众点评找到深圳综合评分4分以上，环境分低于口味分0.3的店铺",@"demo-work"],
                        @[@"succeeded",@"continued",@"已并入上一条",@"顺便注明来源链接",@"demo-work"],
                        @[@"succeeded",@"silent",@"无需提醒",@"定时 · BTC 价格监控",@"demo-mac"],
                        @[@"failed",@"",@"失败",@"生成图片 · 一只坐在窗边的橘猫",@"demo-video"]];
        NSMutableArray *jobs=[NSMutableArray array];NSUInteger i=0;
        for(NSArray *r in rows){[jobs addObject:@{@"id":[NSString stringWithFormat:@"real-%lu",(unsigned long)i],@"session_id":r[4],@"kind":@"chat",@"status":r[0],@"outcome":[r[1] length]?r[1]:NSNull.null,@"status_text":r[2],@"title":r[3],
            @"prompt":i==1?@"integrated_multimodal_description: [Shot 1] A cinematic vertical shot of a calm beach at sunset, the sun sinking toward the horizon, warm gold and violet gradient sky, gentle waves…":r[3],@"created":@(now-120-60*i),@"timing":@{@"finished_at":@(now-30*i)}}];i++;}
        return @{@"items":jobs};
    }
    if([path hasSuffix:@"/demo-work/messages"])return @{@"items":@[
        @{@"id":@"m1",@"session_id":@"demo-work",@"kind":@"chat",@"prompt":@"帮我在大众点评找到深圳综合评分4分以上，环境分低于口味分0.3的店铺",@"status":@"succeeded",@"outcome":@"paused",@"status_text":@"已暂停",@"result":@"目前核对了 3 个公开数据源，其中只有 CnOpenData 提供逐店的口味分和环境分，需要下载数据集才能筛选。\n\n还差：下载并按条件筛选。说「继续」我会从这里接着做。",@"created":@(now-3600),@"timing":@{@"finished_at":@(now-2400)}},
        @{@"id":@"m2",@"session_id":@"demo-work",@"kind":@"chat",@"prompt":@"查一下 Python 3.13 有哪些主要新特性，整理成 5 条要点保存到 py313.md",@"status":@"running",@"status_text":@"进行中",@"result":NSNull.null,@"created":@(now-90),@"timing":@{@"running_since":@(now-85)},
          @"todo":@[@{@"content":@"搜索官方更新说明",@"status":@"completed"},@{@"content":@"读取 What's New 页面",@"status":@"in_progress"},@{@"content":@"整理 5 条要点并保存",@"status":@"pending"}]},
        @{@"id":@"m3",@"session_id":@"demo-work",@"kind":@"chat",@"prompt":@"顺便注明来源链接",@"status":@"succeeded",@"outcome":@"continued",@"status_text":@"已并入上一条",@"result":@"（已并入正在进行的任务）",@"created":@(now-40),@"timing":@{@"finished_at":@(now-38)}}]};
    if([path isEqual:@"/v1/memory"])return @{@"user":@[@"称呼：阿杰",@"时区：伦敦，回复用中文"],@"memory":@[@"做视频默认竖屏，时长 10 秒",@"文件默认存到 Google Drive 的「Friday」文件夹"],@"limits":@{@"user":@600,@"memory":@1400}};
    if([path isEqual:@"/v1/schedules"])return @{@"items":@[
        @{@"id":@"s1",@"title":@"喝水提醒",@"prompt":@"提醒我喝水",@"when":@"每天 08:00",@"next_run":@"2026-10-21 08:00",@"notify":@"always",@"enabled":@YES},
        @{@"id":@"s2",@"title":@"BTC 价格监控",@"prompt":@"查 BTC 美元价格，低于 50000 就提醒我",@"when":@"每小时第 39 分",@"next_run":@"2026-10-20 11:39",@"notify":@"when_relevant",@"enabled":@YES},
        @{@"id":@"s3",@"title":@"周报草稿",@"prompt":@"根据本周会话整理周报草稿",@"when":@"每周五 17:00",@"next_run":NSNull.null,@"notify":@"always",@"enabled":@NO}]};
    if([path isEqual:@"/v1/dramas"])return @{@"items":@[@{@"id":@"p1",@"title":@"雨夜咖啡馆 · 第 1 集",@"stage":@"producing",@"shots":@4,@"done":@1,@"seconds":@25,@"updated":@(now-300)},@{@"id":@"p2",@"title":@"旧城来信",@"stage":@"storyboard",@"shots":@14,@"done":@0,@"seconds":@96,@"updated":@(now-86400)}]};
    if([path hasPrefix:@"/v1/dramas/p"]){
        NSDictionary *done=@{@"id":@"t1",@"kind":@"draft",@"status":@"succeeded",@"has_output":@YES,@"params":@{},@"created":@(now-900)},*final=@{@"id":@"t2",@"kind":@"final",@"status":@"succeeded",@"has_output":@YES,@"params":@{},@"created":@(now-600)};
        NSDictionary *review=@{@"id":@"t3",@"kind":@"draft",@"status":@"succeeded",@"has_output":@YES,@"params":@{},@"created":@(now-500)},*running=@{@"id":@"t4",@"kind":@"draft",@"status":@"running",@"has_output":@NO,@"params":@{},@"created":@(now-100)},*failed=@{@"id":@"t5",@"kind":@"draft",@"status":@"failed",@"has_output":@NO,@"error":@"显存不足",@"params":@{},@"created":@(now-80)};
        return @{@"project":@{@"id":@"p1",@"title":@"雨夜咖啡馆 · 第 1 集",@"script":@"第1场 内景·咖啡馆·夜",@"style":@"Rainy neo-noir, teal and amber, 35mm film grain",@"resolution":@"sd",@"revision":@3},@"stage":@"producing",
            @"characters":@[@{@"id":@"c1",@"name":@"林夏",@"description":@"主角，二十多岁",@"appearance":@"a woman in her late 20s, short black hair, beige trench coat"},@{@"id":@"c2",@"name":@"店员",@"description":@"",@"appearance":@""}],
            @"scenes":@[@{@"id":@"s1",@"heading":@"第1场 内景·咖啡馆·夜",@"setting":@"A small cafe at night, warm lamps, rain on the window"},@{@"id":@"s2",@"heading":@"第2场 外景·街道·夜",@"setting":@""}],
            @"shots":@[FRRealShot(@"a",1,@"s1",@"林夏推门进店，环顾四周",@"wide, static",6,@"done",nil,@[done,final]),FRRealShot(@"b",2,@"s1",@"店员抬头提醒",@"medium, slow push-in",5,@"draft_review",nil,@[review]),
                       FRRealShot(@"c",3,@"s1",@"林夏坐下，取出旧照片",@"close-up",8,@"drafting",nil,@[running]),FRRealShot(@"d",4,@"s2",@"林夏撑伞走进雨里",@"wide, tracking",6,@"planned",@"显存不足",@[failed])],
            @"exports":@[],@"planning":@[@{@"job_id":@"j",@"op":@"storyboard",@"status":@"succeeded",@"error":NSNull.null}],@"estimate":@{@"final_seconds_remaining":@3900,@"draft_seconds_remaining":@900,@"jobs_ahead":@1}};
    }
    return nil;
}
