#import "FRMarkdown.h"

static NSString *FRMDTrim(NSString *s) {return [s stringByTrimmingCharactersInSet:NSCharacterSet.whitespaceAndNewlineCharacterSet];}
static NSTextCheckingResult *FRMDMatch(NSString *pattern,NSString *text) {
    static NSMutableDictionary *cache;static dispatch_once_t once;dispatch_once(&once,^{cache=[NSMutableDictionary dictionary];});
    NSRegularExpression *regex;@synchronized(cache){regex=cache[pattern];if(!regex){regex=[NSRegularExpression regularExpressionWithPattern:pattern options:0 error:nil];cache[pattern]=regex;}}
    return [regex firstMatchInString:text options:0 range:NSMakeRange(0,text.length)];
}
static NSString *FRMDGroup(NSString *s,NSTextCheckingResult *match,NSUInteger group) {NSRange range=[match rangeAtIndex:group];return range.location==NSNotFound?@"":[s substringWithRange:range];}
NSURL *FRMarkdownLink(NSString *source) {
    if([source rangeOfCharacterFromSet:NSCharacterSet.whitespaceAndNewlineCharacterSet].location!=NSNotFound||[source containsString:@"\\"]||[source rangeOfCharacterFromSet:NSCharacterSet.controlCharacterSet].location!=NSNotFound)return nil;
    NSURLComponents *url=[NSURLComponents componentsWithString:source];if(![@[@"https",@"http"] containsObject:url.scheme.lowercaseString]||!url.host.length||url.user||url.password)return nil;return url.URL;
}
static NSArray *FRMDCells(NSString *line) {
    NSString *s=FRMDTrim(line);NSMutableArray *cells=[NSMutableArray array];NSMutableString *cell=[NSMutableString string];NSUInteger ticks=0;
    for(NSUInteger i=0;i<s.length;i++){
        unichar c=[s characterAtIndex:i];
        if(c=='\\'&&i+1<s.length){[cell appendString:[s substringWithRange:NSMakeRange(i,2)]];i++;continue;}
        if(c=='`'){NSUInteger count=1;while(i+count<s.length&&[s characterAtIndex:i+count]=='`')count++;if(!ticks)ticks=count;else if(ticks==count)ticks=0;[cell appendString:[s substringWithRange:NSMakeRange(i,count)]];i+=count-1;continue;}
        if(c=='|'&&!ticks){[cells addObject:FRMDTrim(cell)];[cell setString:@""];}else [cell appendFormat:@"%C",c];
    }[cells addObject:FRMDTrim(cell)];if([s hasPrefix:@"|"]&&cells.count)[cells removeObjectAtIndex:0];if([s hasSuffix:@"|"]&&![s hasSuffix:@"\\|"]&&cells.count)[cells removeLastObject];return cells;
}
static NSArray *FRMDAlignments(NSString *line) {
    NSArray *cells=FRMDCells(line);if(!cells.count)return nil;NSMutableArray *align=[NSMutableArray array];for(NSString *cell in cells){if(!FRMDMatch(@"^:?-{3,}:?$",cell))return nil;[align addObject:[cell hasSuffix:@":"]?([cell hasPrefix:@":"]?@"center":@"right"):@"left"];}return align;
}
NSArray<NSDictionary *> *FRMarkdownBlocks(NSString *source) {
    source=source?:@"";NSArray *plain=@[@{@"type":@"plain",@"text":source}];if(source.length>100000)return plain;
    NSString *normalized=[[source stringByReplacingOccurrencesOfString:@"\r\n" withString:@"\n"] stringByReplacingOccurrencesOfString:@"\r" withString:@"\n"];
    NSArray *lines=[normalized componentsSeparatedByString:@"\n"];if(lines.count>2000)return plain;
    NSMutableArray *blocks=[NSMutableArray array],*paragraph=[NSMutableArray array];
    void(^flush)(void)=^{if(paragraph.count){[blocks addObject:@{@"type":@"paragraph",@"text":[paragraph componentsJoinedByString:@"\n"]}];[paragraph removeAllObjects];}};
    for(NSUInteger i=0;i<lines.count;i++){
        NSString *line=lines[i],*trim=FRMDTrim(line);if(!trim.length){flush();continue;}
        NSTextCheckingResult *fence=FRMDMatch(@"^ {0,8}(`{3,}|~{3,})([^\\n]*)$",line);
        if(fence){flush();NSString *marker=FRMDGroup(line,fence,1),*language=FRMDTrim(FRMDGroup(line,fence,2));NSMutableArray *code=[NSMutableArray array];BOOL closed=NO;
            while(++i<lines.count){NSString *candidate=FRMDTrim(lines[i]);BOOL end=candidate.length>=marker.length;for(NSUInteger j=0;end&&j<candidate.length;j++)end=[candidate characterAtIndex:j]==[marker characterAtIndex:0];if(end){closed=YES;break;}[code addObject:lines[i]];}
            NSString *text=[code componentsJoinedByString:@"\n"];if(closed&&code.count)text=[text stringByAppendingString:@"\n"];
            [blocks addObject:@{@"type":@"code",@"text":text,@"language":language}];continue;
        }
        NSArray *alignment=i+1<lines.count?FRMDAlignments(lines[i+1]):nil;
        if([line containsString:@"|"]&&alignment&&FRMDCells(line).count==alignment.count){flush();NSMutableArray *rows=[NSMutableArray arrayWithObject:FRMDCells(line)];i++;
            while(i+1<lines.count&&[FRMDTrim(lines[i+1]) length]&&[lines[i+1] containsString:@"|"]){NSArray *cells=FRMDCells(lines[++i]);NSMutableArray *row=[cells mutableCopy];while(row.count<alignment.count)[row addObject:@""];if(row.count>alignment.count)return plain;[rows addObject:row];if(rows.count*alignment.count>512)return plain;}
            if(alignment.count>12)return plain;[blocks addObject:@{@"type":@"table",@"rows":rows,@"alignment":alignment}];continue;
        }
        if(FRMDMatch(@"^ {0,3}((\\* *){3,}|(- *){3,}|(_ *){3,})$",line)){flush();[blocks addObject:@{@"type":@"rule"}];continue;}
        NSTextCheckingResult *heading=FRMDMatch(@"^ {0,3}(#{1,6})[ \\t]+(.+?) *#* *$",line);
        if(heading){flush();[blocks addObject:@{@"type":@"heading",@"level":@(FRMDGroup(line,heading,1).length),@"text":FRMDGroup(line,heading,2)}];continue;}
        if(i+1<lines.count&&FRMDMatch(@"^ {0,3}(=+|-+) *$",lines[i+1])){flush();[blocks addObject:@{@"type":@"heading",@"level":@([FRMDTrim(lines[i+1]) hasPrefix:@"="]?1:2),@"text":line}];i++;continue;}
        NSTextCheckingResult *quote=FRMDMatch(@"^ {0,3}> ?(.*)$",line);
        if(quote){flush();NSMutableArray *quoted=[NSMutableArray arrayWithObject:FRMDGroup(line,quote,1)];while(i+1<lines.count&&(quote=FRMDMatch(@"^ {0,3}> ?(.*)$",lines[i+1])))[quoted addObject:FRMDGroup(lines[++i],quote,1)];[blocks addObject:@{@"type":@"quote",@"text":[quoted componentsJoinedByString:@"\n"]}];continue;}
        NSTextCheckingResult *list=FRMDMatch(@"^( *)([-+*]|[0-9]{1,9}[.)])[ \\t]+(.*)$",line);
        if(list){flush();NSString *marker=FRMDGroup(line,list,2),*text=FRMDGroup(line,list,3);if(marker.length==1)marker=@"•";
            if(FRMDMatch(@"^\\[[ xX]\\] +",text)){marker=[[text substringWithRange:NSMakeRange(1,1)] isEqual:@" "]?@"☐":@"☑";text=[text substringFromIndex:4];}
            [blocks addObject:@{@"type":@"list",@"marker":marker,@"indent":@(MIN(6,FRMDGroup(line,list,1).length/2)),@"text":text}];continue;}
        if([line hasPrefix:@"  "]&&!paragraph.count&&[blocks.lastObject[@"type"] isEqual:@"list"]){NSMutableDictionary *item=[blocks.lastObject mutableCopy];item[@"text"]=[item[@"text"] stringByAppendingFormat:@"\n%@",trim];blocks[blocks.count-1]=item;continue;}
        [paragraph addObject:line];if(blocks.count>256)return plain;
    }flush();return blocks.count>256?plain:blocks;
}

static void FRMDAppend(NSMutableArray *runs,NSString *text,NSDictionary *style) {if(!text.length)return;NSMutableDictionary *run=[style mutableCopy];run[@"text"]=text;[runs addObject:run];}
static void FRMDInline(NSString *source,NSDictionary *style,NSMutableArray *runs,NSUInteger depth) {
    if(depth>8||source.length>100000){FRMDAppend(runs,source,style);return;}
    BOOL noLinkDelimiter=NO;NSMutableSet *missingDelimiters=[NSMutableSet set];NSMutableString *plain=[NSMutableString string];void(^flush)(void)=^{FRMDAppend(runs,[plain copy],style);[plain setString:@""];};
    for(NSUInteger i=0;i<source.length;){unichar c=[source characterAtIndex:i];
        if(c=='\\'&&i+1<source.length&&[[NSCharacterSet characterSetWithCharactersInString:@"\\`*{}_[]()#+-.!|>~"] characterIsMember:[source characterAtIndex:i+1]]){[plain appendString:[source substringWithRange:NSMakeRange(i+1,1)]];i+=2;continue;}
        if(c=='`'){NSUInteger n=1;while(i+n<source.length&&[source characterAtIndex:i+n]=='`')n++;NSString *delimiter=[source substringWithRange:NSMakeRange(i,n)];NSUInteger pos=i+n;NSRange end=NSMakeRange(NSNotFound,0);
            while(pos<source.length){end=[source rangeOfString:delimiter options:0 range:NSMakeRange(pos,source.length-pos)];if(end.location==NSNotFound)break;NSUInteger count=n;while(end.location+count<source.length&&[source characterAtIndex:end.location+count]=='`')count++;if(count==n)break;pos=end.location+count;end=NSMakeRange(NSNotFound,0);}
            if(end.location!=NSNotFound){flush();NSString *code=[[source substringWithRange:NSMakeRange(i+n,end.location-i-n)] stringByReplacingOccurrencesOfString:@"\n" withString:@" "];if([code hasPrefix:@" "]&&[code hasSuffix:@" "]&&FRMDTrim(code).length)code=[code substringWithRange:NSMakeRange(1,code.length-2)];NSMutableDictionary *s=[style mutableCopy];s[@"code"]=@YES;FRMDAppend(runs,code,s);i=NSMaxRange(end);continue;}
            [plain appendString:delimiter];i+=n;continue;
        }
        if(!noLinkDelimiter&&(c=='['||(c=='!'&&i+1<source.length&&[source characterAtIndex:i+1]=='['))){
            BOOL image=c=='!';NSUInteger start=i+(image?2:1);NSRange middle=[source rangeOfString:@"](" options:0 range:NSMakeRange(start,source.length-start)];
            if(middle.location==NSNotFound)noLinkDelimiter=YES;
            if(middle.location!=NSNotFound){NSUInteger end=NSMaxRange(middle),balance=1;for(;end<source.length;end++){unichar ch=[source characterAtIndex:end];if(ch=='\\'&&end+1<source.length){end++;continue;}if(ch=='(')balance++;if(ch==')'&&!--balance)break;}
                if(!balance){NSString *url=[source substringWithRange:NSMakeRange(NSMaxRange(middle),end-NSMaxRange(middle))];NSURL *safe=FRMarkdownLink(url);if(safe&&!image){flush();NSMutableDictionary *s=[style mutableCopy];s[@"link"]=safe.absoluteString;FRMDInline([source substringWithRange:NSMakeRange(start,middle.location-start)],s,runs,depth+1);i=end+1;continue;}
                    // Unsupported images/unsafe links remain literal; never create an attachment or execute a URL.
                    [plain appendString:[source substringWithRange:NSMakeRange(i,end+1-i)]];i=end+1;continue;
                }
            }
        }
        NSString *delimiter=nil,*key=nil;NSUInteger remaining=source.length-i;
        if(remaining>=3&&([[source substringWithRange:NSMakeRange(i,3)] isEqual:@"***"]||[[source substringWithRange:NSMakeRange(i,3)] isEqual:@"___"])){delimiter=[source substringWithRange:NSMakeRange(i,3)];key=@"both";}
        else if(remaining>=2){NSString *pair=[source substringWithRange:NSMakeRange(i,2)];if([@[@"**",@"__",@"~~"] containsObject:pair]){delimiter=pair;key=[pair isEqual:@"~~"]?@"strike":@"bold";}}
        if(!delimiter&&(c=='*'||c=='_')){delimiter=[NSString stringWithFormat:@"%C",c];key=@"italic";}
        // Underscores inside identifiers and filenames are literal.
        if(c=='_'&&i>0&&[[NSCharacterSet alphanumericCharacterSet] characterIsMember:[source characterAtIndex:i-1]])delimiter=nil;
        if(delimiter){NSUInteger start=i+delimiter.length;NSRange end=[missingDelimiters containsObject:delimiter]?NSMakeRange(NSNotFound,0):[source rangeOfString:delimiter options:0 range:NSMakeRange(start,source.length-start)];
            while(end.location!=NSNotFound&&end.location>0&&[source characterAtIndex:end.location-1]=='\\'){NSUInteger next=NSMaxRange(end);end=[source rangeOfString:delimiter options:0 range:NSMakeRange(next,source.length-next)];}
            if(end.location!=NSNotFound&&end.location>start){flush();NSMutableDictionary *s=[style mutableCopy];if([key isEqual:@"both"]){s[@"bold"]=@YES;s[@"italic"]=@YES;}else s[key]=@YES;FRMDInline([source substringWithRange:NSMakeRange(start,end.location-start)],s,runs,depth+1);i=NSMaxRange(end);continue;}
            [missingDelimiters addObject:delimiter];[plain appendString:delimiter];i+=delimiter.length;continue;
        }
        [plain appendFormat:@"%C",c];i++;
    }flush();
}
NSArray<NSDictionary *> *FRMarkdownRuns(NSString *source) {NSMutableArray *runs=[NSMutableArray array];FRMDInline(source?:@"",@{},runs,0);return runs;}
