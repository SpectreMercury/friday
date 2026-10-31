"""Build a stable, locally signed app identity for macOS privacy settings."""
import hashlib
import plistlib
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = Path.home() / 'Applications/Friday Desktop.app'


def main():
    source = ROOT / 'scripts/friday_desktop.swift'
    contents = APP / 'Contents'
    executable = contents / 'MacOS/FridayDesktop'
    executable.parent.mkdir(parents=True, exist_ok=True)
    fingerprint = hashlib.sha256(source.read_bytes()).hexdigest()
    marker = contents / 'Resources/source.sha256'
    marker.parent.mkdir(exist_ok=True)
    (contents / 'source.sha256').unlink(missing_ok=True)  # Older development build location.
    if (executable.exists() and marker.exists() and marker.read_text() == fingerprint
            and subprocess.run(['codesign', '--verify', str(APP)], capture_output=True).returncode == 0):
        print(APP)
        return
    subprocess.run(['xcrun', 'swiftc', '-parse-as-library', '-O', str(source), '-o', str(executable)], check=True)
    with (contents / 'Info.plist').open('wb') as stream:
        plistlib.dump({'CFBundleIdentifier': 'com.personal.Friday.desktop', 'CFBundleName': 'Friday Desktop',
                      'CFBundleDisplayName': 'Friday Desktop', 'CFBundleExecutable': 'FridayDesktop',
                      'CFBundlePackageType': 'APPL', 'CFBundleVersion': '1', 'LSUIElement': True,
                      'NSHighResolutionCapable': True, 'LSMinimumSystemVersion': '14.0'}, stream)
    marker.write_text(fingerprint)
    subprocess.run(['codesign', '--force', '--sign', '-', '--identifier', 'com.personal.Friday.desktop', str(APP)], check=True)
    print(APP)


if __name__ == '__main__':
    main()
