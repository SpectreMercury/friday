#!/bin/zsh
set -euo pipefail
cd "$(dirname "$0")/../.."
xcrun --sdk macosx clang -fobjc-arc -fmodules -framework Foundation -Iios/Friday/Sources \
  ios/Friday/Sources/FRMarkdown.m ios/Friday/Tests/Markdown.m -o /tmp/friday-markdown-tests
/tmp/friday-markdown-tests
xcrun --sdk macosx clang -fobjc-arc -fmodules -framework Foundation -framework Security \
  -Iios/Friday/Sources ios/Friday/Sources/FRAPI.m ios/Friday/Sources/FRDemo.m ios/Friday/Sources/FRStore.m ios/Friday/Sources/FRCache.m \
  ios/Friday/Tests/Contracts.m -o /tmp/friday-contracts
/tmp/friday-contracts
xcodebuild -project ios/Friday/Friday.xcodeproj -scheme Friday -configuration Debug \
  -sdk iphonesimulator -destination 'generic/platform=iOS Simulator' \
  -derivedDataPath /tmp/friday-native-build CODE_SIGNING_ALLOWED=NO build
xcodebuild -project ios/Friday/Friday.xcodeproj -scheme Friday -configuration Release \
  -sdk iphoneos -destination 'generic/platform=iOS' \
  -derivedDataPath /tmp/friday-native-device CODE_SIGNING_ALLOWED=NO build
