#import "FRViews.h"
#import "FRAPI.h"
#import <AuthenticationServices/AuthenticationServices.h>
#import <CommonCrypto/CommonDigest.h>
#import <Security/Security.h>
static NSString *FRBase64(NSData *data){return [[[[data base64EncodedStringWithOptions:0] stringByReplacingOccurrencesOfString:@"+" withString:@"-"] stringByReplacingOccurrencesOfString:@"/" withString:@"_"] stringByReplacingOccurrencesOfString:@"=" withString:@""];}
static NSString *FRRandom(void){NSMutableData *data=[NSMutableData dataWithLength:32];if(SecRandomCopyBytes(kSecRandomDefault,32,data.mutableBytes)!=errSecSuccess)return nil;return FRBase64(data);}
@interface FRLoginController : UIViewController <ASWebAuthenticationPresentationContextProviding>
@property(nonatomic) UITextField *address;
@property(nonatomic) UIButton *login;
@property(nonatomic) ASWebAuthenticationSession *auth;
@property(nonatomic) NSString *verifier;
@property(nonatomic) NSString *state;
@end
@implementation FRLoginController
- (void)viewDidLoad {
    [super viewDidLoad];self.view.backgroundColor=UIColor.systemBackgroundColor;self.title=@"";
    UILabel *title=[UILabel new];title.text=@"Friday";title.font=[UIFont systemFontOfSize:34 weight:UIFontWeightBold];
    UILabel *subtitle=[UILabel new];subtitle.text=@"你的想法，从这里继续。";subtitle.textColor=UIColor.secondaryLabelColor;subtitle.font=[UIFont systemFontOfSize:15];
    self.address=[UITextField new];self.address.text=FRAPI.shared.origin.absoluteString;self.address.font=[UIFont systemFontOfSize:14];self.address.keyboardType=UIKeyboardTypeURL;self.address.autocapitalizationType=UITextAutocapitalizationTypeNone;self.address.autocorrectionType=UITextAutocorrectionTypeNo;self.address.borderStyle=UITextBorderStyleNone;self.address.backgroundColor=UIColor.secondarySystemBackgroundColor;self.address.layer.cornerRadius=12;self.address.leftView=[[UIView alloc] initWithFrame:CGRectMake(0,0,14,1)];self.address.leftViewMode=UITextFieldViewModeAlways;self.address.accessibilityLabel=@"Mac 服务地址";self.address.placeholder=@"https://你的服务地址";
    self.login=[UIButton buttonWithType:UIButtonTypeSystem];UIButtonConfiguration *config=UIButtonConfiguration.filledButtonConfiguration;config.title=@"使用通行密钥登录";config.image=[UIImage systemImageNamed:@"person.badge.key"];config.imagePadding=8;config.baseBackgroundColor=UIColor.labelColor;config.baseForegroundColor=UIColor.systemBackgroundColor;config.cornerStyle=UIButtonConfigurationCornerStyleCapsule;self.login.configuration=config;[self.login addTarget:self action:@selector(signIn) forControlEvents:UIControlEventTouchUpInside];
    UILabel *note=[UILabel new];note.text=@"连接你自己的 Mac。登录凭据由 iPhone 钥匙串保管。";note.numberOfLines=0;note.font=[UIFont systemFontOfSize:12];note.textColor=UIColor.secondaryLabelColor;
    UIStackView *stack=[[UIStackView alloc] initWithArrangedSubviews:@[title,subtitle,self.address,self.login,note]];stack.axis=UILayoutConstraintAxisVertical;stack.spacing=18;stack.translatesAutoresizingMaskIntoConstraints=NO;[self.view addSubview:stack];
    [NSLayoutConstraint activateConstraints:@[[stack.leadingAnchor constraintEqualToAnchor:self.view.safeAreaLayoutGuide.leadingAnchor constant:28],[stack.trailingAnchor constraintEqualToAnchor:self.view.safeAreaLayoutGuide.trailingAnchor constant:-28],[stack.centerYAnchor constraintEqualToAnchor:self.view.safeAreaLayoutGuide.centerYAnchor constant:-25],[self.address.heightAnchor constraintEqualToConstant:50],[self.login.heightAnchor constraintEqualToConstant:50]]];
}
- (ASPresentationAnchor)presentationAnchorForWebAuthenticationSession:(ASWebAuthenticationSession *)session {return self.view.window;}
- (void)signIn {
    NSError *error;if(![FRAPI.shared setOriginString:self.address.text error:&error]){FRShowError(self,error);return;}
    self.verifier=FRRandom();self.state=FRRandom();if(!self.verifier||!self.state)return;
    NSData *bytes=[self.verifier dataUsingEncoding:NSUTF8StringEncoding];unsigned char sha[CC_SHA256_DIGEST_LENGTH];CC_SHA256(bytes.bytes,(CC_LONG)bytes.length,sha);
    FRSetButtonBusy(self.login,YES);
    [FRAPI.shared call:@"/v1/native/auth/start" method:@"POST" body:@{@"challenge":FRBase64([NSData dataWithBytes:sha length:sizeof(sha)]),@"state":self.state} completion:^(NSDictionary *v,NSError *e){
        if(e){FRSetButtonBusy(self.login,NO);FRShowError(self,e);return;}
        NSURL *url=[NSURL URLWithString:FRString(v[@"url"])];NSURL *origin=FRAPI.shared.origin;
        if(![url.scheme isEqual:origin.scheme]||![url.host isEqual:origin.host]||![url.port?:@0 isEqual:origin.port?:@0]||![url.path isEqual:@"/login"]){FRSetButtonBusy(self.login,NO);return;}
        __weak typeof(self) weak=self;
        self.auth=[[ASWebAuthenticationSession alloc] initWithURL:url callbackURLScheme:@"friday-private" completionHandler:^(NSURL *callback,NSError *error){dispatch_async(dispatch_get_main_queue(),^{[weak complete:callback error:error];});}];
        self.auth.presentationContextProvider=self;self.auth.prefersEphemeralWebBrowserSession=YES;
        if(![self.auth start]){FRSetButtonBusy(self.login,NO);FRShowError(self,[NSError errorWithDomain:@"Friday" code:0 userInfo:@{NSLocalizedDescriptionKey:@"无法打开系统登录窗口。"}]);}
    }];
}
- (void)complete:(NSURL *)url error:(NSError *)error {
    self.auth=nil;FRSetButtonBusy(self.login,NO);
    if(error){if(error.code!=ASWebAuthenticationSessionErrorCodeCanceledLogin)FRShowError(self,error);return;}
    if(![url.scheme isEqual:@"friday-private"]||![url.host isEqual:@"auth"]||![url.path isEqual:@"/callback"])return;
    NSMutableDictionary *q=[NSMutableDictionary dictionary];for(NSURLQueryItem *i in [NSURLComponents componentsWithURL:url resolvingAgainstBaseURL:NO].queryItems)if(i.value)q[i.name]=i.value;
    if(![q[@"state"] isEqual:self.state]||!q[@"code"])return;
    FRSetButtonBusy(self.login,YES);
    [FRAPI.shared call:@"/v1/native/auth/exchange" method:@"POST" body:@{@"code":q[@"code"],@"verifier":self.verifier} completion:^(NSDictionary *v,NSError *e){FRSetButtonBusy(self.login,NO);self.verifier=nil;self.state=nil;if(e){FRShowError(self,e);return;}NSError *saveError;if(![FRAPI.shared savePairing:v error:&saveError])FRShowError(self,saveError);}];
}
@end
