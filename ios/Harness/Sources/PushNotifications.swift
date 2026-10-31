import Foundation
import UIKit
import UserNotifications

extension Notification.Name {
    static let harnessPushToken = Notification.Name("HarnessPushToken")
    static let harnessNotificationRoute = Notification.Name("HarnessNotificationRoute")
}
final class AppDelegate: NSObject, UIApplicationDelegate, UNUserNotificationCenterDelegate {
    func application(_ application: UIApplication, didFinishLaunchingWithOptions launchOptions: [UIApplication.LaunchOptionsKey: Any]? = nil) -> Bool {
        VoiceMessageDraft.clearAbandonedFiles()
        UNUserNotificationCenter.current().delegate = self; return true
    }
    func application(_ application: UIApplication, didRegisterForRemoteNotificationsWithDeviceToken deviceToken: Data) {
        let token = deviceToken.map { String(format: "%02x", $0) }.joined()
        NotificationCenter.default.post(name: .harnessPushToken, object: token)
    }
    func userNotificationCenter(_ center: UNUserNotificationCenter, willPresent notification: UNNotification) async -> UNNotificationPresentationOptions { [.banner, .sound, .badge] }
    func userNotificationCenter(_ center: UNUserNotificationCenter, didReceive response: UNNotificationResponse) async {
        guard let sessionID = response.notification.request.content.userInfo["session_id"] as? String else { return }
        await MainActor.run {
            UserDefaults.standard.set(sessionID, forKey: "HarnessNotificationSession")
            NotificationCenter.default.post(name: .harnessNotificationRoute, object: nil)
        }
    }
    // Navigation never moves an active voice connection.
}
@MainActor
enum PushNotifications {
    private static var environment: String? {
        guard let value = Bundle.main.object(forInfoDictionaryKey: "HarnessPushEnvironment") as? String,
              ["sandbox", "production"].contains(value) else { return nil }
        return value
    }
    static var isAvailable: Bool { environment != nil }
    static let unavailableMessage = "此测试版不支持远程通知。打开 App 后会同步任务结果与待审批事项。"

    static func requestAuthorization() async -> String {
        guard isAvailable else { return unavailableMessage }
        do {
            let allowed = try await UNUserNotificationCenter.current().requestAuthorization(options: [.alert, .sound, .badge])
            if allowed { UIApplication.shared.registerForRemoteNotifications(); return "已允许通知；设备令牌注册成功后将同步到 Mac。" }
            return "通知未开启，可在 iPhone 设置中修改。"
        } catch { return error.localizedDescription }
    }
    static func registerIfAuthorized() async {
        guard isAvailable else { return }
        let settings = await UNUserNotificationCenter.current().notificationSettings()
        if settings.authorizationStatus == .authorized || settings.authorizationStatus == .provisional { UIApplication.shared.registerForRemoteNotifications() }
    }
    static func sync(token: String, store: AppStore) async {
        guard let environment, let api = store.api, let deviceID = store.credentials?.pairing.deviceId else { return }
        do { try await api.perform("/v1/devices/\(APIClient.segment(deviceID))/push", body: ["token": .string(token), "environment": .string(environment)]) }
        catch { store.error = "通知设备注册失败：\(error.localizedDescription)" }
    }
}
