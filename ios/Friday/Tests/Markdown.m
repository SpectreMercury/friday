#import <Foundation/Foundation.h>
#import "FRMarkdown.h"
static int checks;
static void check(BOOL ok,NSString *name){checks++;if(!ok){fprintf(stderr,"FAIL %s\n",name.UTF8String);exit(1);}}
static NSString *visible(NSArray *runs){NSMutableString *text=[NSMutableString string];for(NSDictionary *run in runs)[text appendString:run[@"text"]];return text;}
int main(void){@autoreleasepool{
    NSArray *runs=FRMarkdownRuns(@"**你需要做的（二选一）： ** 与 `true`，~~旧值~~，*重点*。");
    check([visible(runs) isEqual:@"你需要做的（二选一）：  与 true，旧值，重点。"],@"Chinese emphasis and code markers disappear");
    check([runs[0][@"bold"] boolValue],@"bold has a semantic style");
    runs=FRMarkdownRuns(@"**粗体 `a_b`**，***组合***");check([visible(runs) isEqual:@"粗体 a_b，组合"],@"nested spans preserve text");check([runs[1][@"code"] boolValue]&&[runs[1][@"bold"] boolValue],@"nested code retains styles");
    check([visible(FRMarkdownRuns(@"\\*普通\\* my_variable_name `**字面值**`")) isEqual:@"*普通* my_variable_name **字面值**"],@"escapes identifiers and code remain literal");
    check([visible(FRMarkdownRuns(@"`` echo `date` ``")) isEqual:@"echo `date`"],@"multi-backtick code spans");
    check([visible(FRMarkdownRuns(@"未完成 **加粗和 `代码")) isEqual:@"未完成 **加粗和 `代码"],@"incomplete inline stream preserves all content");
    runs=FRMarkdownRuns(@"[文档](https://example.com/a_(b))");check([runs[0][@"link"] isEqual:@"https://example.com/a_(b)"],@"balanced parentheses in links");
    for(NSString *url in @[@"javascript:alert(1)",@"file:///etc/passwd",@"friday-private://token",@"https://user:pass@example.com",@"https://example.com/a\nb"]){check(FRMarkdownLink(url)==nil,@"unsafe schemes and credential links remain inert");}
    check([visible(FRMarkdownRuns(@"![photo](https://example.com/p.png) <script>alert(1)</script>")) isEqual:@"![photo](https://example.com/p.png) <script>alert(1)</script>"],@"images and HTML never load or execute");
    NSString *command=@"sudo defaults write /Library/Preferences/com.apple.SoftwareUpdate AutomaticDownload -bool false\nsudo killall cfprefsd\n";
    NSArray *blocks=FRMarkdownBlocks([NSString stringWithFormat:@"**你需要做的：**\n\n1. **在 Mac 终端执行**\n```bash\n%@```\n\n2. 打开设置",command]);
    check(blocks.count==4&&[blocks[2][@"type"] isEqual:@"code"],@"screenshot list and fenced code are distinct blocks");check([blocks[2][@"text"] isEqual:command]&&[blocks[2][@"language"] isEqual:@"bash"],@"command copies exactly including line breaks");
    blocks=FRMarkdownBlocks(@"```python\nprint('**not bold**')\n");check([blocks[0][@"type"] isEqual:@"code"]&&[blocks[0][@"text"] isEqual:@"print('**not bold**')\n"],@"unclosed streamed fence remains a code block");
    blocks=FRMarkdownBlocks(@"~~~~txt\n```literal\n~~~\n~~~~");check([blocks[0][@"text"] isEqual:@"```literal\n~~~\n"],@"closing fence must match marker and length");
    blocks=FRMarkdownBlocks(@"| 设置项 | 当前值 | 含义 |\n|---|:---:|---:|\n| `AutomaticDownload` | `true` | 自动下载更新 |\n| A\\|B | `x|y` | 尾部 |");
    check(blocks.count==1&&[blocks[0][@"type"] isEqual:@"table"],@"screenshot table parses as a table");check([blocks[0][@"rows"] count]==3&&[blocks[0][@"rows"][2] count]==3,@"escaped and inline-code pipes do not split columns");check([blocks[0][@"alignment"] isEqual:@[@"left",@"center",@"right"]],@"table alignments retained");
    blocks=FRMarkdownBlocks(@"| a | b |\n| --- | --- |\n| only |");check([blocks[0][@"rows"][1][1] isEqual:@""],@"short table rows padded without losing cells");
    blocks=FRMarkdownBlocks(@"# 标题\n\n> 引用\n> 第二行\n\n---\n\n- [x] 已完成\n  - 子项");check(blocks.count==5,@"heading quote separator task and nested list");check([blocks[3][@"marker"] isEqual:@"☑"]&&[blocks[4][@"indent"] integerValue]==1,@"task and nested list metadata");
    blocks=FRMarkdownBlocks(@"标题\n===\n正文");check([blocks[0][@"type"] isEqual:@"heading"],@"setext headings");
    NSString *huge=[@"a" stringByPaddingToLength:100001 withString:@"a" startingAtIndex:0];blocks=FRMarkdownBlocks(huge);check([blocks[0][@"type"] isEqual:@"plain"]&&[blocks[0][@"text"] isEqual:huge],@"large messages fall back without data loss");
    NSLog(@"Markdown parser passed %d checks",checks);
}return 0;}
