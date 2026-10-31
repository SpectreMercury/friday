#import <Foundation/Foundation.h>
BOOL FRDemoEnabled(void);
NSDictionary *FRDemoPairing(void);
id FRDemoResponse(NSString *path,NSString *method,id body);
void FRDemoSubscribe(NSString *sid,void(^handler)(NSDictionary *event));
void FRDemoUnsubscribe(NSString *sid);
