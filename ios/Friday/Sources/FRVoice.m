#import "FRVoice.h"
#import <AVFoundation/AVFoundation.h>
#import <UIKit/UIKit.h>
NSString * const FRVoiceChanged=@"FRVoiceChanged";
@interface FRVoice () <AVAudioRecorderDelegate>
@property(nonatomic,readwrite) NSString *sessionID,*sessionName,*status,*transcript,*failure;
@property(nonatomic) NSString *leaseID;
@property(nonatomic) NSURLSessionWebSocketTask *socket;
@property(nonatomic) AVAudioEngine *engine;
@property(nonatomic) AVAudioPlayerNode *player;
@property(nonatomic) AVAudioConverter *micConverter;
@property(nonatomic) AVAudioFormat *outputFormat;
@property(nonatomic) NSUInteger generation, playbackGeneration;
@property(nonatomic) NSInteger epoch,packetEpoch,minimumEpoch;
@property(nonatomic) double sampleRate;
@property(nonatomic) NSMutableArray<NSData *> *uploads;
@property(nonatomic) BOOL sending,tapInstalled;
@property(nonatomic) NSUInteger pendingFrames;
@property(nonatomic) AVAudioRecorder *recorder;
@property(nonatomic) NSString *recordSession,*recordID;
@property(nonatomic) NSURL *recordFile;
@property(nonatomic) BOOL requestingRecord,uploadingRecord;
@property(nonatomic) NSUInteger recordGeneration;
@end
@implementation FRVoice
+ (instancetype)shared {static FRVoice *v;static dispatch_once_t once;dispatch_once(&once,^{v=[FRVoice new];});return v;}
- (instancetype)init {if((self=[super init])){
    _status=@"未开始";_uploads=[NSMutableArray array];_sampleRate=24000;
    NSNotificationCenter *n=NSNotificationCenter.defaultCenter;[n addObserver:self selector:@selector(accountChanged) name:FRAccountChanged object:nil];[n addObserver:self selector:@selector(interruption:) name:AVAudioSessionInterruptionNotification object:nil];[n addObserver:self selector:@selector(routeChanged:) name:AVAudioSessionRouteChangeNotification object:nil];[n addObserver:self selector:@selector(background) name:UIApplicationDidEnterBackgroundNotification object:nil];
    NSString *dir=[NSTemporaryDirectory() stringByAppendingPathComponent:@"FridayRecordings"];[[NSFileManager defaultManager] removeItemAtPath:dir error:nil];
}return self;}
- (void)changed {[[NSNotificationCenter defaultCenter] postNotificationName:FRVoiceChanged object:nil];}
- (void)accountChanged {[self stop];[self discardRecording];}
- (void)background {if(self.recorder.isRecording||self.requestingRecord)[self discardRecording];}
- (void)interruption:(NSNotification *)n {if([n.userInfo[AVAudioSessionInterruptionTypeKey] unsignedIntegerValue]==AVAudioSessionInterruptionTypeBegan){self.failure=@"音频被系统打断，任务会继续执行。";[self stop];[self discardRecording];}}
- (void)routeChanged:(NSNotification *)n {if([n.userInfo[AVAudioSessionRouteChangeReasonKey] unsignedIntegerValue]==AVAudioSessionRouteChangeReasonOldDeviceUnavailable){self.failure=@"音频设备已断开。";[self stop];[self discardRecording];}}
- (void)fail:(NSError *)e {self.failure=e.localizedDescription?:@"语音连接已结束。";[self stop];}
- (void)start:(NSDictionary *)workspace {
    if(self.sessionID||self.recordSession||self.requestingRecord)return;
    NSUInteger gen=++self.generation;self.sessionID=workspace[@"id"];self.sessionName=workspace[@"name"];self.failure=nil;self.transcript=@"";self.status=@"准备语音…";[self changed];
    [AVAudioApplication requestRecordPermissionWithCompletionHandler:^(BOOL granted){dispatch_async(dispatch_get_main_queue(),^{
        if(gen!=self.generation)return;if(!granted){self.failure=@"请在 iPhone 设置中允许 Friday 使用麦克风。";[self stop];return;}
        [FRAPI.shared call:[FRAPI.shared pathForSession:self.sessionID suffix:@"/voice"] method:@"POST" body:@{} completion:^(NSDictionary *lease,NSError *error){
            if(gen!=self.generation){if(lease[@"id"])[FRAPI.shared call:[@"/v1/voice/" stringByAppendingString:FRSegment(lease[@"id"])] method:@"DELETE" body:nil completion:^(id v,NSError *e){}];return;}
            if(error){[self fail:error];return;}self.leaseID=lease[@"id"];self.epoch=[lease[@"epoch"] integerValue];self.minimumEpoch=self.epoch;self.packetEpoch=-1;
            NSString *path=[NSString stringWithFormat:@"/v1/voice/%@/stream",FRSegment(self.leaseID)];NSError *e;NSMutableURLRequest *r=[FRAPI.shared request:path method:@"GET" body:nil error:&e];NSURLComponents *url=[NSURLComponents componentsWithURL:r.URL resolvingAgainstBaseURL:NO];url.scheme=[url.scheme isEqual:@"https"]?@"wss":@"ws";r.URL=url.URL;
            self.socket=[FRAPI.shared.session webSocketTaskWithRequest:r];[self.socket resume];
            if(![self startAudio:gen error:&e]){[self fail:e];return;}[self receive:gen];
        }];
    });}];
}
- (BOOL)startAudio:(NSUInteger)gen error:(NSError **)error {
    AVAudioSession *audio=AVAudioSession.sharedInstance;
    if(![audio setCategory:AVAudioSessionCategoryPlayAndRecord mode:AVAudioSessionModeVoiceChat options:AVAudioSessionCategoryOptionDefaultToSpeaker|AVAudioSessionCategoryOptionAllowBluetoothHFP error:error]||![audio setActive:YES error:error])return NO;
    self.engine=[AVAudioEngine new];self.player=[AVAudioPlayerNode new];[self.engine attachNode:self.player];self.outputFormat=[[AVAudioFormat alloc] initStandardFormatWithSampleRate:48000 channels:1];[self.engine connect:self.player to:self.engine.mainMixerNode format:self.outputFormat];
    if(![self.engine.inputNode setVoiceProcessingEnabled:YES error:error])return NO;
    AVAudioFormat *input=[self.engine.inputNode outputFormatForBus:0];if(input.sampleRate<=0||input.channelCount==0)return NO;
    AVAudioFormat *format=[[AVAudioFormat alloc] initWithCommonFormat:AVAudioPCMFormatInt16 sampleRate:16000 channels:1 interleaved:NO];
    AVAudioConverter *converter=[[AVAudioConverter alloc] initFromFormat:input toFormat:format];self.micConverter=converter;__weak typeof(self) weak=self;
    [self.engine.inputNode installTapOnBus:0 bufferSize:2048 format:input block:^(AVAudioPCMBuffer *buffer,AVAudioTime *when){
        AVAudioPCMBuffer *out=[[AVAudioPCMBuffer alloc] initWithPCMFormat:format frameCapacity:(AVAudioFrameCount)(buffer.frameLength*16000/input.sampleRate+64)];__block BOOL supplied=NO;NSError *e;
        [converter convertToBuffer:out error:&e withInputFromBlock:^AVAudioBuffer *(AVAudioPacketCount count,AVAudioConverterInputStatus *status){if(supplied){*status=AVAudioConverterInputStatus_NoDataNow;return nil;}supplied=YES;*status=AVAudioConverterInputStatus_HaveData;return buffer;}];
        if(e||!out.frameLength)return;NSData *bytes=[NSData dataWithBytes:out.int16ChannelData[0] length:out.frameLength*2];
        dispatch_async(dispatch_get_main_queue(),^{if(weak.generation!=gen||!weak.socket)return;if(weak.uploads.count>=8){[weak fail:[NSError errorWithDomain:@"Friday" code:0 userInfo:@{NSLocalizedDescriptionKey:@"网络上传拥堵，通话已结束。"}]];return;}[weak.uploads addObject:bytes];[weak pump:gen];});
    }];self.tapInstalled=YES;[self.engine prepare];if(![self.engine startAndReturnError:error])return NO;[self.player play];return YES;
}
- (void)pump:(NSUInteger)gen {if(self.sending||!self.uploads.count||gen!=self.generation)return;self.sending=YES;NSData *data=self.uploads.firstObject;[self.uploads removeObjectAtIndex:0];[self.socket sendMessage:[[NSURLSessionWebSocketMessage alloc] initWithData:data] completionHandler:^(NSError *e){dispatch_async(dispatch_get_main_queue(),^{if(gen!=self.generation)return;self.sending=NO;if(e)[self fail:e];else[self pump:gen];});}];}
- (void)receive:(NSUInteger)gen {
    [self.socket receiveMessageWithCompletionHandler:^(NSURLSessionWebSocketMessage *message,NSError *error){dispatch_async(dispatch_get_main_queue(),^{
        if(gen!=self.generation)return;if(error){[self fail:error];return;}
        if(message.type==NSURLSessionWebSocketMessageTypeData){if(self.packetEpoch==self.epoch&&self.packetEpoch>=self.minimumEpoch)[self play:message.data];}
        else {
            NSDictionary *e=[NSJSONSerialization JSONObjectWithData:[message.string dataUsingEncoding:NSUTF8StringEncoding] options:0 error:nil];if(![e isKindOfClass:NSDictionary.class]){[self stop];return;}
            NSString *type=e[@"type"];BOOL foreign=e[@"session_id"]&&![e[@"session_id"] isEqual:self.sessionID];
            if([type isEqual:@"audio_format"])self.packetEpoch=(foreign||!e[@"epoch"])?-1:[e[@"epoch"] integerValue];
            if(!foreign && (!e[@"epoch"]||[e[@"epoch"] integerValue]>=self.epoch)){
                if(e[@"epoch"])self.epoch=[e[@"epoch"] integerValue];
                if([type isEqual:@"ready"])self.status=@"正在听";
                if([type isEqual:@"recognized"])self.transcript=FRString(e[@"text"]);
                if([type isEqual:@"state"])self.status=@{@"listening":@"正在听",@"thinking":@"正在思考",@"speaking":@"正在回答"}[e[@"state"]]?:@"连接中";
                if([type isEqual:@"clear_playback"]){self.minimumEpoch=MAX(self.minimumEpoch,self.epoch);[self clearPlayback];}
                if([type isEqual:@"audio_format"]){double rate=[e[@"sample_rate"] doubleValue];if(rate<8000||rate>96000||(e[@"channels"]&&[e[@"channels"] integerValue]!=1)||(e[@"encoding"]&&![e[@"encoding"] isEqual:@"pcm_s16le"])) {self.failure=@"音频格式不支持。";[self stop];return;}self.sampleRate=rate;}
                if([type isEqual:@"error"]){self.failure=FRString(e[@"message"]);[self stop];return;}[self changed];
            }
        }
        if(gen==self.generation)[self receive:gen];
    });}];
}
- (void)play:(NSData *)data {
    if(!data.length||data.length%2||data.length>192000)return;
    AVAudioFormat *format=[[AVAudioFormat alloc] initWithCommonFormat:AVAudioPCMFormatInt16 sampleRate:self.sampleRate channels:1 interleaved:NO];AVAudioPCMBuffer *input=[[AVAudioPCMBuffer alloc] initWithPCMFormat:format frameCapacity:(AVAudioFrameCount)data.length/2];input.frameLength=input.frameCapacity;memcpy(input.int16ChannelData[0],data.bytes,data.length);
    AVAudioPCMBuffer *output=[[AVAudioPCMBuffer alloc] initWithPCMFormat:self.outputFormat frameCapacity:(AVAudioFrameCount)(input.frameLength*48000/self.sampleRate+64)];AVAudioConverter *converter=[[AVAudioConverter alloc] initFromFormat:format toFormat:self.outputFormat];__block BOOL supplied=NO;NSError *error;
    [converter convertToBuffer:output error:&error withInputFromBlock:^AVAudioBuffer *(AVAudioPacketCount n,AVAudioConverterInputStatus *s){if(supplied){*s=AVAudioConverterInputStatus_NoDataNow;return nil;}supplied=YES;*s=AVAudioConverterInputStatus_HaveData;return input;}];if(error){[self fail:error];return;}if(!output.frameLength)return;
    if(self.pendingFrames+output.frameLength>48000*8){self.failure=@"播放缓冲已满，请重新连接。";[self stop];return;}
    self.pendingFrames+=output.frameLength;NSUInteger generation=self.playbackGeneration;__weak typeof(self) weak=self;
    [self.player scheduleBuffer:output completionCallbackType:AVAudioPlayerNodeCompletionDataPlayedBack completionHandler:^(AVAudioPlayerNodeCompletionCallbackType type){dispatch_async(dispatch_get_main_queue(),^{if(weak.playbackGeneration==generation)weak.pendingFrames=weak.pendingFrames>=output.frameLength?weak.pendingFrames-output.frameLength:0;});}];if(!self.player.playing)[self.player play];
}
- (void)clearPlayback {self.playbackGeneration++;[self.player stop];self.pendingFrames=0;if(self.engine.running)[self.player play];}
- (void)interrupt {if(!self.socket)return;[self clearPlayback];self.minimumEpoch=self.epoch+1;NSString *text=[NSString stringWithFormat:@"{\"type\":\"interrupt\",\"epoch\":%ld}",(long)self.epoch];NSUInteger gen=self.generation;[self.socket sendMessage:[[NSURLSessionWebSocketMessage alloc] initWithString:text] completionHandler:^(NSError *e){if(e)dispatch_async(dispatch_get_main_queue(),^{if(gen==self.generation)[self fail:e];});}];}
- (void)stop {[self stopWithCompletion:nil];}
- (void)switchTo:(NSDictionary *)workspace {
    if(self.sessionID&&!self.leaseID){self.failure=@"语音还在连接，请结束后再切换会话。";[self changed];return;}
    NSUInteger expected=self.generation+1;[self stopWithCompletion:^{if(self.generation==expected)[self start:workspace];}];
}
- (void)stopWithCompletion:(void (^)(void))completion {
    self.generation++;[self clearPlayback];[self.player stop];if(self.tapInstalled){[self.engine.inputNode removeTapOnBus:0];self.tapInstalled=NO;}[self.engine stop];self.engine=nil;self.player=nil;self.micConverter=nil;
    [self.socket cancelWithCloseCode:NSURLSessionWebSocketCloseCodeNormalClosure reason:nil];self.socket=nil;[self.uploads removeAllObjects];self.sending=NO;
    NSString *lease=self.leaseID;self.leaseID=nil;self.sessionID=nil;self.sessionName=nil;self.status=@"未开始";
    if(!self.recordSession)[AVAudioSession.sharedInstance setActive:NO withOptions:AVAudioSessionSetActiveOptionNotifyOthersOnDeactivation error:nil];
    if(lease)[FRAPI.shared call:[@"/v1/voice/" stringByAppendingString:FRSegment(lease)] method:@"DELETE" body:nil completion:^(id v,NSError *e){if(e){self.failure=@"尚未确认旧通话已结束，请稍后重试。";[self changed];}else if(completion)completion();}];else if(completion)completion();[self changed];
}
- (BOOL)recording {return self.recorder.isRecording;}
- (void)record:(NSString *)sid completion:(FRReply)completion {
    if(self.sessionID||self.recordSession||self.requestingRecord){completion(nil,[NSError errorWithDomain:@"Friday" code:0 userInfo:@{NSLocalizedDescriptionKey:@"请先结束当前通话或处理语音草稿。"}]);return;}
    self.requestingRecord=YES;NSUInteger generation=++self.recordGeneration;
    [AVAudioApplication requestRecordPermissionWithCompletionHandler:^(BOOL granted){dispatch_async(dispatch_get_main_queue(),^{
        if(generation!=self.recordGeneration)return;self.requestingRecord=NO;if(!granted){completion(nil,[NSError errorWithDomain:@"Friday" code:0 userInfo:@{NSLocalizedDescriptionKey:@"麦克风权限未开启。"}]);return;}
        self.recordSession=sid;self.recordID=NSUUID.UUID.UUIDString;NSString *folder=[NSTemporaryDirectory() stringByAppendingPathComponent:@"FridayRecordings"];[[NSFileManager defaultManager] createDirectoryAtPath:folder withIntermediateDirectories:YES attributes:@{NSFileProtectionKey:NSFileProtectionComplete} error:nil];self.recordFile=[NSURL fileURLWithPath:[folder stringByAppendingPathComponent:[self.recordID stringByAppendingString:@".m4a"]]];
        NSError *e;AVAudioSession *audio=AVAudioSession.sharedInstance;
        BOOL configured=[audio setCategory:AVAudioSessionCategoryPlayAndRecord mode:AVAudioSessionModeDefault options:AVAudioSessionCategoryOptionDefaultToSpeaker|AVAudioSessionCategoryOptionAllowBluetoothHFP error:&e]&&[audio setActive:YES error:&e];
        if(configured)self.recorder=[[AVAudioRecorder alloc] initWithURL:self.recordFile settings:@{AVFormatIDKey:@(kAudioFormatMPEG4AAC),AVSampleRateKey:@16000,AVNumberOfChannelsKey:@1,AVEncoderBitRateKey:@64000} error:&e];self.recorder.delegate=self;
        if(!self.recorder||![self.recorder recordForDuration:180]){[self discardRecording];completion(nil,e?:[NSError errorWithDomain:@"Friday" code:0 userInfo:@{NSLocalizedDescriptionKey:@"无法开始录音。"}]);return;}[self changed];completion(@YES,nil);
    });}];
}
- (void)finishRecording {[self.recorder stop];[AVAudioSession.sharedInstance setActive:NO withOptions:AVAudioSessionSetActiveOptionNotifyOthersOnDeactivation error:nil];[self changed];}
- (void)audioRecorderDidFinishRecording:(AVAudioRecorder *)recorder successfully:(BOOL)flag {if(!flag)[self discardRecording];else[self changed];}
- (void)audioRecorderEncodeErrorDidOccur:(AVAudioRecorder *)recorder error:(NSError *)error {self.failure=error.localizedDescription;[self discardRecording];}
- (void)sendRecording:(FRReply)completion {
    if(self.recorder.isRecording||!self.recordFile||self.uploadingRecord)return;
    NSData *data=[NSData dataWithContentsOfURL:self.recordFile];if(!data.length||data.length>20*1024*1024){completion(nil,[NSError errorWithDomain:@"Friday" code:413 userInfo:@{NSLocalizedDescriptionKey:@"语音为空或超过 20 MB。"}]);return;}
    NSError *e;NSMutableURLRequest *r=[FRAPI.shared request:[FRAPI.shared pathForSession:self.recordSession suffix:@"/voice-messages"] method:@"POST" body:nil error:&e];r.HTTPBody=data;[r setValue:@"audio/mp4" forHTTPHeaderField:@"Content-Type"];[r setValue:self.recordID forHTTPHeaderField:@"Idempotency-Key"];self.uploadingRecord=YES;NSUInteger generation=self.recordGeneration;
    [FRAPI.shared sendRequest:r raw:NO completion:^(id v,NSError *error){if(generation!=self.recordGeneration)return;self.uploadingRecord=NO;if(!error)[self discardRecording];completion(v,error);}];
}
- (void)discardRecording {self.recordGeneration++;self.requestingRecord=NO;[self.recorder stop];self.recorder=nil;if(self.recordFile)[[NSFileManager defaultManager] removeItemAtURL:self.recordFile error:nil];self.recordFile=nil;self.recordID=nil;self.recordSession=nil;self.uploadingRecord=NO;if(!self.sessionID)[AVAudioSession.sharedInstance setActive:NO withOptions:AVAudioSessionSetActiveOptionNotifyOthersOnDeactivation error:nil];[self changed];}
@end
