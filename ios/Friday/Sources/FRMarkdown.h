#import <Foundation/Foundation.h>
// Small, deterministic Markdown model. Never evaluates HTML or loads remote resources.
FOUNDATION_EXPORT NSArray<NSDictionary *> *FRMarkdownBlocks(NSString *source);
FOUNDATION_EXPORT NSArray<NSDictionary *> *FRMarkdownRuns(NSString *source);
FOUNDATION_EXPORT NSURL *FRMarkdownLink(NSString *source);
