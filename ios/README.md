# Harness for iPhone

Native SwiftUI client for the session-scoped Harness service. The Xcode project is `Harness/Harness.xcodeproj`, with shared schemes `Harness Personal` (free account, no push) and `Harness` (APNs), iPhone deployment target iOS 17.0. It has no third-party packages.

## Run

1. Open the project in **full Xcode 15 or later**. Command Line Tools alone have no iPhone SDK and cannot build this app.
2. Sign in under **Xcode → Settings → Apple Accounts**. With a free Apple Account, select scheme **Harness Personal**, which uses the `Personal` build configuration and empty `Personal.entitlements`. In the Harness target's Signing & Capabilities, keep automatic signing enabled, set a unique bundle identifier and select your **Personal Team** for that configuration. With a paid membership, select scheme **Harness**, configure your team/bundle identifier, and enable Push Notifications in the provisioning profile; Debug uses development APNs and Release uses production APNs.
3. Connect and trust your iPhone, enable Developer Mode on the phone, select it as the run destination, and run. A simulator can exercise navigation/API contracts, but it does not establish audio quality, lock-screen operation, or microphone echo cancellation performance.
4. In the app, enter the HTTPS origin of the gateway (without a path) and a one-use invite from the Harness administration command. The gateway must forward `/v1`, WebSocket upgrades, and Google OAuth callback traffic to the Mac. Use the same origin configured for the backend's OAuth redirect.
5. Connect Google in Settings. Complete browser authorization, return, refresh, and explicitly bind document IDs under a session's **技能与可访问资源**. Use the session’s **从 Google Drive 选择文件** action to open the backend’s Google Picker page in Safari. Returning to the app reloads the binding revision and selected IDs. Resource IDs remain editable, and permission to create new documents is a separate toggle. Empty bindings do not mean whole-Drive access.

For local simulator development, `http://localhost:<port>` is supported as a narrowly scoped ATS exception. Production and device use require HTTPS/WSS. The app never embeds service tokens or connector secrets.

Free Personal Team profiles expire after seven days, after which Xcode must rebuild/reinstall the app ([Apple account limits](https://developer.apple.com/help/account/basics/about-your-developer-account)). `Harness Personal` does not request notification permission, register with APNs, or upload push tokens. Device pairing, foreground event sync, sessions, tasks, and voice remain available. Settings explains that results and pending approvals sync when the app opens. Keep using this scheme for free-account builds: overriding it with `-configuration Debug` or `Release` re-enables the paid APNs entitlements. Do not add Push Notifications to the target globally when testing with a Personal Team.

## Implemented behavior

- Three native tabs: sessions, tasks, settings. Session creation, rename, archive/restore, individually enabled skills, revision-checked connector resource bindings, and owner-only Mac management configuration.
- Fork selected original messages and files into a new session; imported context rows are viewable but are not offered as original tasks to fork again. No approvals, connector bindings, management privileges, or audio connection are copied.
- Session-specific chat drafts, stable request IDs for retrying identical message submissions, file import, authenticated Quick Look downloads, task results/cancellation, and request-scoped approval/denial.
- Keychain device credentials, device credential renewal, revoke/local sign-out, Google browser authorization and file Picker, and APNs registration/notification routing to the original session. APNs sending additionally requires the server's Apple push key/team/bundle configuration and a signed app/profile; the repository does not contain these secrets.
- An explicit live call is pinned to the session that opened it. Browsing another session leaves it attached to the original. Switching voice ends the old connection before creating another; end/interrupt never cancel an existing task.
- Voice messages are available under the conversation’s **＋ → 录制语音留言** even when another user holds the live-call slot. A dedicated sheet requires explicit start, stop, then send. AVAudioRecorder produces mono AAC in an M4A container, capped at 180 seconds and 20 MB. Capture is mutually exclusive with this phone’s live call. A stopped draft keeps its original session and UUID `Idempotency-Key` across retries; the app sends raw `audio/mp4` to `/v1/sessions/{sid}/voice-messages`. The Mac transcribes and runs the resulting ordinary task. Permission failure, encoding/interruption/device-removal failure, cancellation, sheet dismissal, and leaving the app stop capture and delete temporary drafts. A normal stop retains the draft for sending; application startup removes leftovers from a forced termination. Closing during upload cannot undo a task the server has already received.
- AVAudioEngine `.playAndRecord` / `.voiceChat`, system voice processing, 16 kHz mono signed PCM16 microphone conversion, negotiated downlink sample rates, serial bounded upload, and bounded playback. The WebSocket generation and epoch gate reject old-session/old-turn audio. A manual interruption suppresses old packets immediately while awaiting the server's new epoch.
- Microphone capture ends on explicit hangup, permission failure, call interruption, old audio device removal, stream failure, or sustained backpressure. `UIBackgroundModes=audio` permits an already active call to continue with the screen locked. This does not start microphone capture in the background.
- Foreground event polling resumes and catches up after reconnecting. Existing jobs remain on the Mac when the app closes. Cold voice creation allows 135 seconds for local model warmup; already-warm response latency still needs device measurement.

## Checks

Run from the repository root:

```sh
./ios/check.sh
```

This parses all Swift sources, validates project/plist syntax, then **compiles and executes Foundation contract checks** for session/job decoding, connector bindings, same-origin URL safety, encoded file paths, voice timeout, session/epoch audio filtering, and invalidation of late preview downloads after sign-out, raw voice-message request destination/headers/retry identity and size limits, and the shared microphone lease. These checks do not build UIKit/SwiftUI/AVFoundation code.

The development host now has Xcode 27.0 (27A266a), with first-launch components and the iOS 27 SDK installed. Full simulator and unsigned iPhone device builds succeeded without compiler warnings on 2026-09-27. Reproduce the simulator build from the repository root:

```sh
DEVELOPER_DIR=/Applications/Xcode.app/Contents/Developer \
xcodebuild -project ios/Harness/Harness.xcodeproj -scheme Harness \
  -configuration Debug -sdk iphonesimulator \
  -destination 'generic/platform=iOS Simulator' \
  -derivedDataPath /tmp/harness-ios-build CODE_SIGNING_ALLOWED=NO build
```

For an unsigned device build, replace `-sdk iphonesimulator` with `-sdk iphoneos`, the destination with `generic/platform=iOS`, and the derived-data path with `/tmp/harness-iphone-build`. This verifies device-target compilation but cannot be installed on an iPhone until it is signed. Build logs from this verification are `/tmp/harness-ios-build.log` and `/tmp/harness-iphone-build.log`.

The free-account configuration also passed a full unsigned device build on 2026-09-27, with `HarnessPushEnvironment=disabled` in the built app. Actual Personal Team provisioning and installation still require the user's Apple login and connected iPhone:

```sh
xcodebuild -project ios/Harness/Harness.xcodeproj -scheme 'Harness Personal' \
  -sdk iphoneos -destination 'generic/platform=iOS' \
  -derivedDataPath /tmp/harness-personal-build CODE_SIGNING_ALLOWED=NO build
```

The iOS 27 runtime was also installed. The app installed and launched successfully on a dedicated **Harness Validation** simulator (iPhone 17 Pro), and its process remained running during the follow-up check. Device Hub accessibility inspection timed out, so this is process-level startup verification; the pairing screen has not yet received visual review. The project is open in Xcode for continued local development.

For distribution, use Xcode Archive with your signing team and registered-device profile. App icon artwork can be added before distribution; the project currently uses the system's default app icon.

## Required device acceptance

Not yet verified on this development host: device signing/distribution and testing on a connected iPhone. The successful simulator build compiles the SwiftUI, UIKit, and AVFoundation code; it does not establish real-device behavior. Validate on an actual registered device:

- Two sessions with different skills and equal file names; run and approve a task in A while browsing B. Old results must remain in A.
- Two clients editing the same settings: the stale editor must get a revision conflict and explicitly reload before trying again.
- Wi-Fi, cellular, and the existing VPN; lock/unlock during a call; speaker and Bluetooth headset; phone calls and headset disconnection.
- Record/stop/send a voice message while the Mac’s live-call slot is occupied by another user; the phone must still enqueue it in the original session. Check 180-second auto-stop, permission denial, headset removal, phone-call interruption, backgrounding/dismissal cleanup, and retry after a lost upload response without creating a duplicate task. Starting live capture and message capture on the same phone must be mutually exclusive.
- Speak over playback and manually stop playback; no old packets should be heard afterward, and the original durable job should still finish in its original session.
- Kill/reopen the app and restart the service; results and pending approvals must reload without submitting duplicate writes.
- Google authorization expiry, limited resource access, local and remote device revoke, APNs receipt with development and production signing.
- Measure **actual speaker output**, targeting warm simple-answer median <=3 seconds / P95 <=5 seconds and interruption stop <=300 ms. Parser/contract checks cannot establish these metrics.

Audio implementation references: [Apple voice processing](https://developer.apple.com/documentation/avfaudio/avaudioionode/setvoiceprocessingenabled(_:)), [playAndRecord](https://developer.apple.com/documentation/avfaudio/avaudiosession/category-swift.struct/playandrecord), and [remote notification registration](https://developer.apple.com/documentation/usernotifications/registering-your-app-with-apns).
