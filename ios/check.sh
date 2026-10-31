#!/bin/sh
set -eu
cd "$(dirname "$0")/.."
swiftc -frontend -parse ios/Harness/Sources/*.swift
plutil -lint ios/Harness/Harness.xcodeproj/project.pbxproj ios/Harness/Resources/Info.plist ios/Harness/Resources/*.entitlements
check_dir=$(mktemp -d "${TMPDIR:-/tmp}/harness-ios-check.XXXXXX")
trap 'rm -rf "$check_dir"' EXIT INT TERM
swiftc ios/Harness/Sources/Models.swift ios/Harness/Sources/APIClient.swift ios/ContractTests.swift -o "$check_dir/contracts"
"$check_dir/contracts"
